"""Telemetry Ingestion Worker (Kinesis -> Lambda).

For every record in the Kinesis batch:
  1. decode JSON and verify the HMAC-SHA256 signature
  2. validate the schema
  3. load the vehicle's recent history from DynamoDB
  4. run anomaly detection (threshold + z-score + forecast)
  5. store the reading in DynamoDB (FleetTelemetry)
  6. update the "latest state" item and store alerts (FleetState)
  7. email critical alerts through SNS (with a cooldown so we don't spam)
  8. push the reading to every open dashboard via the WebSocket API

Uses the partial-batch-response feature, so one bad record does not force
the whole batch to be retried.
"""
import base64
import json
import logging
import os
import time
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from anomaly import Thresholds, detect, validate
from signing import verify_frame

log = logging.getLogger()
log.setLevel(logging.INFO)

TELEMETRY_TABLE = os.environ.get("TELEMETRY_TABLE", "FleetTelemetry")
STATE_TABLE = os.environ.get("STATE_TABLE", "FleetState")
CONNECTIONS_TABLE = os.environ.get("CONNECTIONS_TABLE", "FleetConnections")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")
WS_ENDPOINT = os.environ.get("WS_ENDPOINT", "")  # https://id.execute-api.region.amazonaws.com/stage
HMAC_SECRET = os.environ.get("HMAC_SECRET", "")
HISTORY_SIZE = int(os.environ.get("HISTORY_SIZE", "20"))
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "7"))
ALERT_COOLDOWN_SEC = int(os.environ.get("ALERT_COOLDOWN_SEC", "300"))

THRESHOLDS = Thresholds(
    cargo_temp_max=float(os.environ.get("CARGO_TEMP_MAX", "8")),
    speed_max=float(os.environ.get("SPEED_MAX", "90")),
)

_clients = {}


def _res(name):
    """Lazily create boto3 resources/clients (keeps cold start small and
    lets unit tests inject fakes through _clients)."""
    if name not in _clients:
        if name == "dynamodb":
            _clients[name] = boto3.resource("dynamodb")
        elif name == "sns":
            _clients[name] = boto3.client("sns")
        elif name == "ws":
            _clients[name] = boto3.client("apigatewaymanagementapi", endpoint_url=WS_ENDPOINT)
    return _clients[name]


def _table(name):
    return _res("dynamodb").Table(name)


def to_decimal(obj):
    """DynamoDB does not accept float - convert recursively to Decimal."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_decimal(v) for v in obj]
    return obj


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return int(o) if o == o.to_integral_value() else float(o)
        return super().default(o)


def emf_metric(name, value, unit="Count"):
    """Write a custom CloudWatch metric using Embedded Metric Format.
    (Just a log line - no extra IAM permission needed.)"""
    print(json.dumps({
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": "FleetTelemetry",
                "Dimensions": [[]],
                "Metrics": [{"Name": name, "Unit": unit}],
            }],
        },
        name: value,
    }))


def load_history(vehicle_id):
    resp = _table(TELEMETRY_TABLE).query(
        KeyConditionExpression=Key("VehicleId").eq(vehicle_id),
        ScanIndexForward=False,
        Limit=HISTORY_SIZE,
        ProjectionExpression="#t, cargoTempC, anomalies",
        ExpressionAttributeNames={"#t": "Timestamp"},
    )
    items = resp.get("Items", [])
    items.reverse()  # oldest first
    return [{"ts": int(i["Timestamp"]), "cargoTempC": float(i["cargoTempC"]),
             "anomalies": list(i.get("anomalies", []))} for i in items]


def store_reading(frame, result):
    now = int(time.time())
    item = {
        "VehicleId": frame["vehicleId"],
        "Timestamp": frame["ts"],
        "lat": frame["lat"],
        "lon": frame["lon"],
        "speedKmh": frame["speedKmh"],
        "cargoTempC": frame["cargoTempC"],
        "engineTempC": frame["engineTempC"],
        "fuelPct": frame["fuelPct"],
        "doorOpen": bool(frame.get("doorOpen", False)),
        "anomalies": [a.code for a in result.anomalies],
        "ExpiresAt": now + RETENTION_DAYS * 86400,
    }
    if result.forecast_cargo_temp is not None:
        item["forecastCargoTempC"] = result.forecast_cargo_temp
    _table(TELEMETRY_TABLE).put_item(Item=to_decimal(item))

    latest = dict(item)
    latest.pop("VehicleId")
    latest.update({"Pk": "LATEST", "Sk": frame["vehicleId"], "vehicleId": frame["vehicleId"],
                   "ts": frame["ts"]})
    latest.pop("Timestamp")
    latest.pop("ExpiresAt")
    _table(STATE_TABLE).put_item(Item=to_decimal(latest))


def store_alerts(frame, alerts):
    state = _table(STATE_TABLE)
    expires = int(time.time()) + RETENTION_DAYS * 86400
    for a in alerts:
        state.put_item(Item=to_decimal({
            "Pk": "ALERT",
            "Sk": f"{frame['ts']:015d}#{frame['vehicleId']}#{a.code}",
            "vehicleId": frame["vehicleId"],
            "ts": frame["ts"],
            "lat": frame["lat"],
            "lon": frame["lon"],
            "ExpiresAt": expires,
            **a.to_dict(),
        }))


def cooldown_ok(vehicle_id):
    """Atomic check-and-set so a vehicle sends at most one email per cooldown."""
    now = int(time.time())
    try:
        _table(STATE_TABLE).update_item(
            Key={"Pk": "COOLDOWN", "Sk": vehicle_id},
            UpdateExpression="SET LastSent = :now",
            ConditionExpression="attribute_not_exists(LastSent) OR LastSent < :cut",
            ExpressionAttributeValues={":now": now, ":cut": now - ALERT_COOLDOWN_SEC},
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def new_alerts(history, result):
    """Only anomalies that were NOT already active on the previous reading
    become alerts - a truck that stays too warm gives one alert, not one
    alert every 2 seconds."""
    prev = set(history[-1].get("anomalies", [])) if history else set()
    return [a for a in result.anomalies if a.code not in prev]


def notify(frame, result, fresh):
    if not SNS_TOPIC_ARN or not any(a.severity == "critical" for a in fresh):
        return False
    if not cooldown_ok(frame["vehicleId"]):
        return False
    lines = [f"- [{a.severity.upper()}] {a.message}" for a in result.anomalies]
    body = (
        f"Fleet alert for vehicle {frame['vehicleId']}\n\n" + "\n".join(lines) +
        f"\n\nLocation: {frame['lat']:.5f}, {frame['lon']:.5f}"
        f"\nTime (UTC): {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(frame['ts'] / 1000))}"
    )
    _res("sns").publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject=f"[Fleet Alert] {frame['vehicleId']} - {result.anomalies[0].code}",
        Message=body,
    )
    return True


def broadcast(messages):
    """Send the whole batch as ONE WebSocket message to every connected
    dashboard (one API call per client instead of one per reading).
    Removes connections that have gone away."""
    if not WS_ENDPOINT or not messages:
        return 0
    payload = json.dumps({"type": "batch", "items": messages}, cls=DecimalEncoder).encode()
    conns = _table(CONNECTIONS_TABLE).scan(ProjectionExpression="connectionId").get("Items", [])
    sent = 0
    for c in conns:
        cid = c["connectionId"]
        try:
            _res("ws").post_to_connection(ConnectionId=cid, Data=payload)
            sent += 1
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code in ("GoneException", "410"):
                _table(CONNECTIONS_TABLE).delete_item(Key={"connectionId": cid})
            else:
                log.warning("post_to_connection failed for %s: %s", cid, code)
    return sent


def process_frame(frame):
    """Process one decoded frame. Returns the dashboard message (or None if rejected)."""
    if HMAC_SECRET and not verify_frame(frame, HMAC_SECRET):
        emf_metric("InvalidSignature", 1)
        log.warning("Rejected frame with bad signature: %s", frame.get("vehicleId"))
        return None
    problems = validate(frame)
    if problems:
        emf_metric("InvalidFrame", 1)
        log.warning("Rejected invalid frame %s: %s", frame.get("vehicleId"), problems)
        return None

    history = load_history(frame["vehicleId"])
    result = detect(frame, history, THRESHOLDS)
    store_reading(frame, result)

    fresh = new_alerts(history, result)
    if fresh:
        emf_metric("AnomaliesDetected", len(fresh))
        store_alerts(frame, fresh)
        notify(frame, result, fresh)

    clean = {k: v for k, v in frame.items() if k != "sig"}
    return {
        "type": "telemetry",
        "data": clean,
        "forecastCargoTempC": result.forecast_cargo_temp,
        "anomalies": [a.to_dict() for a in result.anomalies],
        "newAlerts": [a.to_dict() for a in fresh],
    }


def lambda_handler(event, context):
    failures = []
    outgoing = []
    records = event.get("Records", [])
    for rec in records:
        seq = rec.get("kinesis", {}).get("sequenceNumber")
        try:
            raw = base64.b64decode(rec["kinesis"]["data"])
            frame = json.loads(raw)
        except (KeyError, ValueError) as e:
            log.warning("Skipping undecodable record %s: %s", seq, e)
            emf_metric("InvalidFrame", 1)
            continue  # poison message: drop, do not retry forever
        try:
            msg = process_frame(frame)
            if msg:
                outgoing.append(msg)
        except Exception:  # noqa: BLE001 - report so Kinesis retries just this one
            log.exception("Failed to process record %s", seq)
            failures.append({"itemIdentifier": seq})

    try:
        broadcast(outgoing)
    except Exception:  # noqa: BLE001 - live push is best effort; data is already stored
        log.exception("Broadcast failed")

    emf_metric("RecordsProcessed", len(records) - len(failures))
    return {"batchItemFailures": failures}
