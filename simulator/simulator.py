"""Fleet telemetry simulator - pretends to be IoT trackers on 5 trucks.

Every few seconds each truck sends a signed JSON frame:
  vehicleId, ts, lat, lon, speedKmh, cargoTempC, engineTempC, fuelPct, doorOpen, sig

Send modes
  dry-run  : just print frames (no network)            -> quick check
  local    : HTTP POST to the local demo server         -> run on your laptop
  kinesis  : boto3 put_records into Kinesis             -> AWS (simplest)
  iot      : MQTT over TLS with X.509 cert to IoT Core  -> AWS (full design)

Demo scenario (on by default): truck V3's fridge "fails" after ~40 s, so its
cargo temperature climbs slowly. The cloud first raises a PREDICTED breach
warning, then a CRITICAL alert + email once it passes 8 C.

Examples
  python simulator.py --mode dry-run --count 3
  python simulator.py --mode local
  python simulator.py --mode kinesis
  python simulator.py --mode iot
"""
import argparse
import json
import math
import os
import random
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lambdas", "processor"))
from signing import sign_frame  # noqa: E402

GENERATED_CONFIG = os.path.join(HERE, "generated", "config.json")
LOCAL_SECRET = "local-dev-secret"

# Simple routes in Gujarat / Maharashtra (lat, lon)
ROUTES = {
    "V1": [(21.1702, 72.8311), (21.6264, 73.0152), (21.7051, 72.9959), (22.3072, 73.1812)],
    "V2": [(21.1702, 72.8311), (20.9467, 72.9520), (20.5992, 72.9342), (20.3893, 72.9106), (19.0760, 72.8777)],
    "V3": [(23.0225, 72.5714), (22.5686, 71.8106), (22.3039, 70.8022)],
    "V4": [(22.3072, 73.1812), (22.6916, 72.8634), (23.0225, 72.5714)],
    "V5": [(21.1702, 72.8311), (21.1255, 73.1122), (21.1100, 73.3936), (19.9975, 73.7898)],
}


def haversine_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


class Vehicle:
    def __init__(self, vid, route, rng):
        self.vid = vid
        self.route = route
        self.rng = rng
        self.seg = 0
        self.pos_km = 0.0  # distance travelled inside current segment
        self.forward = True
        self.speed = rng.uniform(55, 70)
        self.cargo_temp = rng.uniform(3.8, 4.2)
        self.engine_temp = rng.uniform(82, 88)
        self.fuel = rng.uniform(60, 95)
        self.door_open = False
        self.fridge_failed = False
        self.event_ticks = 0
        self.event = None

    def _segment(self):
        pts = self.route if self.forward else list(reversed(self.route))
        return pts[self.seg], pts[self.seg + 1]

    def position(self):
        a, b = self._segment()
        length = max(haversine_km(a, b), 0.001)
        f = min(self.pos_km / length, 1.0)
        return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f

    def step(self, dt_sec, speedup, anomaly_rate):
        r = self.rng
        # random one-off events (only when nothing else is happening)
        if self.event is None and r.random() < anomaly_rate:
            self.event = r.choice(["overspeed", "door", "engine", "spike"])
            self.event_ticks = r.randint(3, 6)

        target = 65.0
        if self.event == "overspeed":
            target = 105.0
        self.speed += (target - self.speed) * 0.3 + r.uniform(-3, 3)
        self.speed = max(0.0, self.speed)

        # move along the route
        self.pos_km += self.speed * dt_sec * speedup / 3600.0
        a, b = self._segment()
        while self.pos_km >= haversine_km(a, b):
            self.pos_km -= haversine_km(a, b)
            self.seg += 1
            if self.seg >= len(self.route) - 1:  # end of route: turn around
                self.seg = 0
                self.forward = not self.forward
                self.fuel = r.uniform(85, 100)  # refuelled at depot
            a, b = self._segment()

        # cargo temperature: fridge holds ~4 C; sensor adds small reading noise
        if self.fridge_failed:
            self.cargo_temp += r.uniform(0.10, 0.22)
        else:
            self.cargo_temp += (4.0 - self.cargo_temp) * 0.25 + r.uniform(-0.05, 0.05)
        sensor_noise = r.gauss(0, 0.08)
        spike = r.uniform(5, 8) if self.event == "spike" else 0.0

        # engine temperature
        eng_target = 118.0 if self.event == "engine" else 86.0
        self.engine_temp += (eng_target - self.engine_temp) * 0.35 + r.uniform(-0.8, 0.8)

        self.fuel = max(0.0, self.fuel - self.speed * dt_sec * speedup / 3600.0 * 0.05)
        self.door_open = self.event == "door"

        if self.event:
            self.event_ticks -= 1
            if self.event_ticks <= 0:
                self.event = None

        lat, lon = self.position()
        return {
            "vehicleId": self.vid,
            "ts": int(time.time() * 1000),
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "speedKmh": round(self.speed, 1),
            "cargoTempC": round(self.cargo_temp + sensor_noise + spike, 2),
            "engineTempC": round(self.engine_temp, 1),
            "fuelPct": round(self.fuel, 1),
            "doorOpen": self.door_open,
        }


# ---------------------------------------------------------------- senders
class DrySender:
    def send(self, frames):
        for f in frames:
            print(json.dumps(f))

    def close(self):
        pass


class LocalSender:
    def __init__(self, url):
        self.url = url

    def send(self, frames):
        data = json.dumps(frames).encode()
        req = urllib.request.Request(self.url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()

    def close(self):
        pass


class KinesisSender:
    def __init__(self, stream, region):
        import boto3
        self.stream = stream
        self.client = boto3.client("kinesis", region_name=region)

    def send(self, frames):
        resp = self.client.put_records(
            StreamName=self.stream,
            Records=[{"Data": json.dumps(f).encode(), "PartitionKey": f["vehicleId"]} for f in frames],
        )
        if resp.get("FailedRecordCount"):
            print(f"  ! {resp['FailedRecordCount']} records failed (throttled?)")

    def close(self):
        pass


class IotSender:
    def __init__(self, cfg):
        import ssl
        import threading
        import paho.mqtt.client as mqtt

        certs = os.path.join(HERE, "generated", "certs")
        for name in ("AmazonRootCA1.pem", "device.pem.crt", "private.pem.key"):
            if not os.path.exists(os.path.join(certs, name)):
                sys.exit(f"Missing {name} in simulator/generated/certs - run terraform apply "
                         "with enable_iot = true, or use --mode kinesis.")
        if not cfg.get("iot_endpoint"):
            sys.exit("IoT is disabled in Terraform (enable_iot = false). Use --mode kinesis.")

        self.topic_prefix = cfg.get("topic_prefix", "fleet/telemetry")
        self.connected = threading.Event()
        self.closing = False
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=cfg.get("client_id", "fleet-simulator"))
        self.client.tls_set(
            ca_certs=os.path.join(certs, "AmazonRootCA1.pem"),
            certfile=os.path.join(certs, "device.pem.crt"),
            keyfile=os.path.join(certs, "private.pem.key"),
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )

        def on_connect(client, userdata, flags, reason_code, properties):
            if reason_code.is_failure:
                print(f"  ! IoT Core refused the connection: {reason_code}")
            else:
                print("  Connected to AWS IoT Core (MQTT over TLS, X.509 certificate)")
                self.connected.set()

        def on_disconnect(client, userdata, flags, reason_code, properties):
            self.connected.clear()
            if not self.closing:
                print(f"  ! Disconnected from IoT Core ({reason_code}) - reconnecting...")

        self.client.on_connect = on_connect
        self.client.on_disconnect = on_disconnect
        print(f"Connecting to AWS IoT Core at {cfg['iot_endpoint']}:8883 ...")
        self.client.connect(cfg["iot_endpoint"], int(os.environ.get("IOT_PORT", "8883")), keepalive=60)
        self.client.loop_start()
        if not self.connected.wait(timeout=20):
            self.close()
            sys.exit("Could not connect to IoT Core within 20 s. Check that terraform apply finished, "
                     "port 8883 is not blocked by your network (try a mobile hotspot), or use --mode kinesis.")

    def send(self, frames):
        if not self.connected.wait(timeout=10):
            raise RuntimeError("not connected to IoT Core")
        for f in frames:
            info = self.client.publish(f"{self.topic_prefix}/{f['vehicleId']}", json.dumps(f), qos=1)
            info.wait_for_publish(timeout=10)

    def close(self):
        self.closing = True
        self.client.disconnect()
        self.client.loop_stop()


# ---------------------------------------------------------------- main
def load_cloud_config():
    if not os.path.exists(GENERATED_CONFIG):
        sys.exit(
            "simulator/generated/config.json not found.\n"
            "Run 'terraform apply' in the infra folder first - it creates this file.")
    with open(GENERATED_CONFIG) as fh:
        return json.load(fh)


def main(argv=None):
    p = argparse.ArgumentParser(description="Fleet IoT telemetry simulator")
    p.add_argument("--mode", choices=["dry-run", "local", "kinesis", "iot"], default="dry-run")
    p.add_argument("--vehicles", type=int, default=5, help="number of trucks (1-5)")
    p.add_argument("--interval", type=float, default=2.0, help="seconds between sends")
    p.add_argument("--count", type=int, default=0, help="stop after N rounds (0 = run forever)")
    p.add_argument("--speedup", type=float, default=30.0, help="move trucks faster on the map")
    p.add_argument("--anomaly-rate", type=float, default=0.02, help="chance of a random event per tick")
    p.add_argument("--scenario", choices=["demo", "none"], default="demo",
                   help="'demo' makes V3's fridge fail after --fail-after seconds")
    p.add_argument("--fail-after", type=float, default=40.0)
    p.add_argument("--tamper-rate", type=float, default=0.0,
                   help="chance of sending a frame with a broken signature (security demo)")
    p.add_argument("--local-url", default="http://localhost:8000/ingest")
    p.add_argument("--seed", type=int, default=None)
    args = p.parse_args(argv)

    rng = random.Random(args.seed)
    if args.mode in ("kinesis", "iot"):
        cfg = load_cloud_config()
        secret = cfg["hmac_secret"]
    else:
        cfg, secret = {}, os.environ.get("HMAC_SECRET", LOCAL_SECRET)

    if args.mode == "dry-run":
        sender = DrySender()
    elif args.mode == "local":
        sender = LocalSender(args.local_url)
    elif args.mode == "kinesis":
        sender = KinesisSender(cfg["stream_name"], cfg["region"])
    else:
        sender = IotSender(cfg)

    ids = list(ROUTES)[: max(1, min(args.vehicles, len(ROUTES)))]
    fleet = [Vehicle(v, ROUTES[v], rng) for v in ids]
    start = time.time()
    rounds = 0
    print(f"Simulating {len(fleet)} vehicles, mode={args.mode}, every {args.interval}s. Ctrl+C to stop.")
    try:
        while True:
            elapsed = time.time() - start
            frames = []
            for v in fleet:
                if args.scenario == "demo" and v.vid == "V3" and elapsed >= args.fail_after and not v.fridge_failed:
                    v.fridge_failed = True
                    print(f"  >> Scenario: {v.vid} refrigeration unit failed - temperature will rise")
                frame = sign_frame(v.step(args.interval, args.speedup, args.anomaly_rate), secret)
                if rng.random() < args.tamper_rate:
                    frame["cargoTempC"] = round(frame["cargoTempC"] - 3, 2)  # attacker edits value
                    print(f"  >> Tampered frame sent for {v.vid} (should be rejected)")
                frames.append(frame)
            try:
                sender.send(frames)
            except Exception as e:  # keep running if the network blips
                print(f"  ! send failed: {e}")
            if args.mode != "dry-run":
                summary = "  ".join(f"{f['vehicleId']}:{f['cargoTempC']:.1f}C/{f['speedKmh']:.0f}kmh" for f in frames)
                print(f"[{time.strftime('%H:%M:%S')}] sent {len(frames)}  {summary}")
            rounds += 1
            if args.count and rounds >= args.count:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        sender.close()


if __name__ == "__main__":
    main()
