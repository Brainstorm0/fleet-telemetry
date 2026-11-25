"""Read-only REST API used by the dashboard when it first loads.

HTTP API (payload format 2.0) routes:
  GET /latest                      -> latest reading of every vehicle
  GET /history?vehicleId=V1&limit= -> recent readings of one vehicle
  GET /alerts?limit=               -> most recent alerts
  GET /health                      -> simple health check (used for SLA probes)
"""
import json
import os
import re
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

TELEMETRY_TABLE = os.environ.get("TELEMETRY_TABLE", "FleetTelemetry")
STATE_TABLE = os.environ.get("STATE_TABLE", "FleetState")
VEHICLE_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

_ddb = None


def _table(name):
    global _ddb
    if _ddb is None:
        _ddb = boto3.resource("dynamodb")
    return _ddb.Table(name)


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return int(o) if o == o.to_integral_value() else float(o)
        return super().default(o)


def respond(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body, cls=DecimalEncoder),
    }


def _limit(params, default, maximum):
    try:
        return max(1, min(int(params.get("limit", default)), maximum))
    except (TypeError, ValueError):
        return default


def get_latest():
    items = _table(STATE_TABLE).query(KeyConditionExpression=Key("Pk").eq("LATEST")).get("Items", [])
    for i in items:
        i.pop("Pk", None)
        i.pop("Sk", None)
    return items


def get_history(vehicle_id, limit):
    items = _table(TELEMETRY_TABLE).query(
        KeyConditionExpression=Key("VehicleId").eq(vehicle_id),
        ScanIndexForward=False,
        Limit=limit,
    ).get("Items", [])
    items.reverse()
    for i in items:  # same field names the dashboard uses everywhere
        i["vehicleId"] = i.pop("VehicleId")
        i["ts"] = i.pop("Timestamp")
        i.pop("ExpiresAt", None)
    return items


def get_alerts(limit):
    items = _table(STATE_TABLE).query(
        KeyConditionExpression=Key("Pk").eq("ALERT"),
        ScanIndexForward=False,
        Limit=limit,
    ).get("Items", [])
    for i in items:
        i.pop("Pk", None)
        i.pop("Sk", None)
        i.pop("ExpiresAt", None)
    return items


def lambda_handler(event, context):
    path = event.get("rawPath", "/")
    params = event.get("queryStringParameters") or {}

    if path.endswith("/health"):
        return respond(200, {"status": "ok"})
    if path.endswith("/latest"):
        return respond(200, {"vehicles": get_latest()})
    if path.endswith("/alerts"):
        return respond(200, {"alerts": get_alerts(_limit(params, 50, 200))})
    if path.endswith("/history"):
        vid = params.get("vehicleId", "")
        if not VEHICLE_RE.match(vid):
            return respond(400, {"error": "vehicleId is required (letters, digits, - or _)"})
        return respond(200, {"vehicleId": vid, "readings": get_history(vid, _limit(params, 60, 500))})
    return respond(404, {"error": "not found"})
