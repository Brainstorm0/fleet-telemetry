"""Local demo server - runs the WHOLE pipeline on your laptop, no AWS needed.

It plays the role of every cloud piece so you can test before deploying:

  simulator --HTTP POST /ingest-->  [this server]
                                     |- verify HMAC signature   (same code as Lambda)
                                     |- anomaly detection       (same code as Lambda)
                                     |- keep data in memory     (instead of DynamoDB)
                                     |- print "EMAIL" alerts    (instead of SNS)
                                     |- push over /ws           (instead of API Gateway WebSocket)
  dashboard  <--WebSocket /ws-------'
  dashboard  --GET /api/latest, /api/history, /api/alerts  (instead of HTTP API)

Run:   python local_demo/server.py      then open http://localhost:8000
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict, deque

from aiohttp import WSMsgType, web

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "lambdas", "processor"))
from anomaly import Thresholds, detect, validate  # noqa: E402
from signing import verify_frame  # noqa: E402

DASHBOARD_DIR = os.path.join(ROOT, "dashboard")
HISTORY_SIZE = 20
COOLDOWN_SEC = 300


class State:
    def __init__(self, secret):
        self.secret = secret
        self.thresholds = Thresholds()
        self.history = defaultdict(lambda: deque(maxlen=500))
        self.latest = {}
        self.alerts = deque(maxlen=200)
        self.last_email = {}
        self.sockets = set()
        self.stats = {"accepted": 0, "rejected_sig": 0, "rejected_invalid": 0}


def process(state, frame):
    if not verify_frame(frame, state.secret):
        state.stats["rejected_sig"] += 1
        print(f"  x REJECTED (bad signature) from {frame.get('vehicleId')}")
        return None
    problems = validate(frame)
    if problems:
        state.stats["rejected_invalid"] += 1
        print(f"  x REJECTED (invalid) {frame.get('vehicleId')}: {problems}")
        return None

    vid = frame["vehicleId"]
    hist = list(state.history[vid])[-HISTORY_SIZE:]
    result = detect(frame, [{"ts": h["ts"], "cargoTempC": h["cargoTempC"]} for h in hist], state.thresholds)
    prev = set(hist[-1]["anomalies"]) if hist else set()
    fresh = [a for a in result.anomalies if a.code not in prev]  # same rule as the Lambda

    clean = {k: v for k, v in frame.items() if k != "sig"}
    stored = dict(clean, anomalies=[a.code for a in result.anomalies],
                  forecastCargoTempC=result.forecast_cargo_temp)
    state.history[vid].append(stored)
    state.latest[vid] = stored
    state.stats["accepted"] += 1

    for a in fresh:
        state.alerts.appendleft(dict(a.to_dict(), vehicleId=vid, ts=frame["ts"],
                                     lat=frame["lat"], lon=frame["lon"]))
    critical_new = any(a.severity == "critical" for a in fresh)
    if critical_new and time.time() - state.last_email.get(vid, 0) > COOLDOWN_SEC:
        state.last_email[vid] = time.time()
        print(f"\n  *** [SIMULATED SNS EMAIL] Fleet alert for {vid}: "
              + "; ".join(a.message for a in result.anomalies) + "\n")
    elif fresh:
        print(f"  ! NEW ALERT {vid}: " + "; ".join(a.message for a in fresh))

    return {"type": "telemetry", "data": clean,
            "forecastCargoTempC": result.forecast_cargo_temp,
            "anomalies": [a.to_dict() for a in result.anomalies],
            "newAlerts": [a.to_dict() for a in fresh]}


async def broadcast(state, items):
    if not items:
        return
    payload = json.dumps({"type": "batch", "items": items})
    for ws in list(state.sockets):
        try:
            await ws.send_str(payload)
        except (ConnectionResetError, RuntimeError):
            state.sockets.discard(ws)


# ---------------------------------------------------------------- handlers
async def ingest(request):
    state = request.app["state"]
    try:
        body = await request.json()
    except ValueError:
        return web.json_response({"error": "invalid JSON"}, status=400)
    frames = body if isinstance(body, list) else [body]
    out = [m for m in (process(state, f) for f in frames if isinstance(f, dict)) if m]
    await broadcast(state, out)
    return web.json_response({"accepted": len(out), "received": len(frames)})


async def ws_handler(request):
    state = request.app["state"]
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    state.sockets.add(ws)
    print(f"  + dashboard connected ({len(state.sockets)} open)")
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    if json.loads(msg.data).get("action") == "ping":
                        await ws.send_str(json.dumps({"type": "pong", "ts": int(time.time() * 1000)}))
                except ValueError:
                    pass
    finally:
        state.sockets.discard(ws)
        print(f"  - dashboard disconnected ({len(state.sockets)} open)")
    return ws


async def api_latest(request):
    return web.json_response({"vehicles": list(request.app["state"].latest.values())})


async def api_history(request):
    vid = request.query.get("vehicleId", "")
    limit = min(int(request.query.get("limit", 60)), 500)
    readings = list(request.app["state"].history.get(vid, []))[-limit:]
    return web.json_response({"vehicleId": vid, "readings": readings})


async def api_alerts(request):
    limit = min(int(request.query.get("limit", 50)), 200)
    return web.json_response({"alerts": list(request.app["state"].alerts)[:limit]})


async def api_health(request):
    return web.json_response({"status": "ok", "stats": request.app["state"].stats})


async def config_js(request):
    host = request.host
    cfg = {"apiUrl": f"http://{host}/api", "wsUrl": f"ws://{host}/ws", "mode": "local"}
    return web.Response(text=f"window.FLEET_CONFIG = {json.dumps(cfg)};\n",
                        content_type="application/javascript")


async def index(request):
    return web.FileResponse(os.path.join(DASHBOARD_DIR, "index.html"))


def make_app(secret):
    app = web.Application()
    app["state"] = State(secret)
    app.router.add_post("/ingest", ingest)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/latest", api_latest)
    app.router.add_get("/api/history", api_history)
    app.router.add_get("/api/alerts", api_alerts)
    app.router.add_get("/api/health", api_health)
    app.router.add_get("/config.js", config_js)  # overrides dashboard/config.js
    app.router.add_get("/", index)
    app.router.add_static("/", DASHBOARD_DIR)
    return app


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--secret", default=os.environ.get("HMAC_SECRET", "local-dev-secret"))
    args = p.parse_args()
    print(f"Local fleet demo on http://localhost:{args.port}  (Ctrl+C to stop)")
    web.run_app(make_app(args.secret), port=args.port, print=None)


if __name__ == "__main__":
    main()
