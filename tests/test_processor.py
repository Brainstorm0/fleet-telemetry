"""Tests the processor Lambda end-to-end with small in-memory fakes
instead of real AWS (no internet / credentials needed)."""
import base64
import importlib
import json
import os
import sys

from botocore.exceptions import ClientError

from signing import sign_frame

SECRET = "test-secret"
PROCESSOR_DIR = os.path.join(os.path.dirname(__file__), "..", "lambdas", "processor")


class FakeTable:
    def __init__(self):
        self.items = []

    def put_item(self, Item):
        self.items.append(Item)

    def query(self, **kw):
        return {"Items": []}

    def scan(self, **kw):
        return {"Items": [{"connectionId": "abc"}, {"connectionId": "gone"}]}

    def delete_item(self, Key):
        self.items.append(("deleted", Key))

    def update_item(self, **kw):
        return {}


class FakeDynamo:
    def __init__(self):
        self.tables = {}

    def Table(self, name):
        return self.tables.setdefault(name, FakeTable())


class FakeSns:
    def __init__(self):
        self.sent = []

    def publish(self, **kw):
        self.sent.append(kw)


class FakeWs:
    def __init__(self):
        self.posts = []

    def post_to_connection(self, ConnectionId, Data):
        if ConnectionId == "gone":
            raise ClientError({"Error": {"Code": "GoneException"}}, "PostToConnection")
        self.posts.append((ConnectionId, json.loads(Data)))


def load(monkeypatch):
    monkeypatch.setenv("HMAC_SECRET", SECRET)
    monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123:topic")
    monkeypatch.setenv("WS_ENDPOINT", "https://example.execute-api.us-east-1.amazonaws.com/prod")
    sys.modules.pop("handler", None)
    sys.path.insert(0, PROCESSOR_DIR)
    try:
        handler = importlib.import_module("handler")
    finally:
        sys.path.pop(0)
        sys.modules.pop("handler", None)
    fakes = {"dynamodb": FakeDynamo(), "sns": FakeSns(), "ws": FakeWs()}
    handler._clients.update(fakes)
    return handler, fakes


def kinesis_event(frames):
    return {"Records": [
        {"kinesis": {"sequenceNumber": str(i), "data": base64.b64encode(json.dumps(f).encode()).decode()}}
        for i, f in enumerate(frames)
    ]}


def good_frame(**kw):
    f = {"vehicleId": "V3", "ts": 1_700_000_000_000, "lat": 22.3, "lon": 70.8, "speedKmh": 62.0,
         "cargoTempC": 4.2, "engineTempC": 86.0, "fuelPct": 55.0, "doorOpen": False}
    f.update(kw)
    return sign_frame(f, SECRET)


def test_good_frame_is_stored_and_broadcast(monkeypatch):
    h, fakes = load(monkeypatch)
    out = h.lambda_handler(kinesis_event([good_frame()]), None)
    assert out == {"batchItemFailures": []}
    tel = fakes["dynamodb"].tables["FleetTelemetry"].items
    assert len(tel) == 1 and tel[0]["VehicleId"] == "V3"
    state = fakes["dynamodb"].tables["FleetState"].items
    assert state[0]["Pk"] == "LATEST" and state[0]["ts"] == 1_700_000_000_000
    posts = fakes["ws"].posts
    assert posts[0][1]["type"] == "batch" and posts[0][1]["items"][0]["data"]["vehicleId"] == "V3"
    # stale "gone" connection was cleaned up
    assert ("deleted", {"connectionId": "gone"}) in fakes["dynamodb"].tables["FleetConnections"].items
    assert fakes["sns"].sent == []


def test_tampered_frame_is_rejected(monkeypatch):
    h, fakes = load(monkeypatch)
    bad = good_frame()
    bad["cargoTempC"] = 1.0
    h.lambda_handler(kinesis_event([bad]), None)
    assert "FleetTelemetry" not in fakes["dynamodb"].tables


def test_critical_alert_sends_email(monkeypatch):
    h, fakes = load(monkeypatch)
    h.lambda_handler(kinesis_event([good_frame(cargoTempC=9.8)]), None)
    assert len(fakes["sns"].sent) == 1
    assert "CARGO_TEMP_HIGH" in fakes["sns"].sent[0]["Subject"]
    alerts = [i for i in fakes["dynamodb"].tables["FleetState"].items if i.get("Pk") == "ALERT"]
    assert alerts and alerts[0]["code"] == "CARGO_TEMP_HIGH"


def test_garbage_record_does_not_fail_batch(monkeypatch):
    h, fakes = load(monkeypatch)
    event = {"Records": [{"kinesis": {"sequenceNumber": "1", "data": base64.b64encode(b"not json").decode()}}]}
    assert h.lambda_handler(event, None) == {"batchItemFailures": []}


def test_repeated_anomaly_only_alerts_once(monkeypatch):
    h, fakes = load(monkeypatch)
    # previous reading already had CARGO_TEMP_HIGH
    monkeypatch.setattr(h, "load_history", lambda vid: [
        {"ts": 1_699_999_998_000, "cargoTempC": 9.5, "anomalies": ["CARGO_TEMP_HIGH"]}])
    h.lambda_handler(kinesis_event([good_frame(cargoTempC=9.8)]), None)
    alerts = [i for i in fakes["dynamodb"].tables["FleetState"].items if i.get("Pk") == "ALERT"]
    assert alerts == [] and fakes["sns"].sent == []
    msg = fakes["ws"].posts[0][1]["items"][0]
    assert msg["anomalies"] and msg["newAlerts"] == []
