"""Integration test against moto (a local fake of DynamoDB + SNS that
behaves like the real services, e.g. rejects floats and reserved words)."""
import base64
import importlib
import json
import os
import sys

import boto3
import pytest

moto = pytest.importorskip("moto")
from signing import sign_frame  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def make_tables(ddb):
    ddb.create_table(TableName="FleetTelemetry", BillingMode="PAY_PER_REQUEST",
                     KeySchema=[{"AttributeName": "VehicleId", "KeyType": "HASH"},
                                {"AttributeName": "Timestamp", "KeyType": "RANGE"}],
                     AttributeDefinitions=[{"AttributeName": "VehicleId", "AttributeType": "S"},
                                           {"AttributeName": "Timestamp", "AttributeType": "N"}])
    ddb.create_table(TableName="FleetState", BillingMode="PAY_PER_REQUEST",
                     KeySchema=[{"AttributeName": "Pk", "KeyType": "HASH"},
                                {"AttributeName": "Sk", "KeyType": "RANGE"}],
                     AttributeDefinitions=[{"AttributeName": "Pk", "AttributeType": "S"},
                                           {"AttributeName": "Sk", "AttributeType": "S"}])
    ddb.create_table(TableName="FleetConnections", BillingMode="PAY_PER_REQUEST",
                     KeySchema=[{"AttributeName": "connectionId", "KeyType": "HASH"}],
                     AttributeDefinitions=[{"AttributeName": "connectionId", "AttributeType": "S"}])


def load_module(name, folder):
    sys.path.insert(0, os.path.join(ROOT, "lambdas", folder))
    try:
        sys.modules.pop("handler", None)
        return importlib.import_module("handler")
    finally:
        sys.path.pop(0)
        sys.modules.pop("handler", None)  # don't leak into other test files


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with moto.mock_aws():
        ddb = boto3.client("dynamodb")
        make_tables(ddb)
        topic = boto3.client("sns").create_topic(Name="alerts")["TopicArn"]
        monkeypatch.setenv("SNS_TOPIC_ARN", topic)
        monkeypatch.setenv("HMAC_SECRET", "s3cret")
        monkeypatch.setenv("WS_ENDPOINT", "")
        yield


def event(frames):
    return {"Records": [{"kinesis": {"sequenceNumber": str(i),
                                     "data": base64.b64encode(json.dumps(f).encode()).decode()}}
                        for i, f in enumerate(frames)]}


def test_full_flow_processor_then_api(aws):
    proc = load_module("handler", "processor")
    proc._clients.clear()
    t0 = 1_700_000_000_000
    frames = []
    for i in range(12):  # V3 warming up 0.2 C every 2 s, ends above 8 C
        f = {"vehicleId": "V3", "ts": t0 + i * 2000, "lat": 22.3, "lon": 70.8, "speedKmh": 60.5,
             "cargoTempC": round(6.0 + 0.2 * i, 2), "engineTempC": 86.0, "fuelPct": 50.0, "doorOpen": False}
        frames.append(sign_frame(f, "s3cret"))
    for f in frames:  # one record per invocation so history builds up
        assert proc.lambda_handler(event([f]), None) == {"batchItemFailures": []}

    api = load_module("handler", "api")
    api._ddb = None
    latest = json.loads(api.lambda_handler({"rawPath": "/latest"}, None)["body"])["vehicles"]
    assert latest[0]["vehicleId"] == "V3" and latest[0]["ts"] == frames[-1]["ts"]
    hist = json.loads(api.lambda_handler(
        {"rawPath": "/history", "queryStringParameters": {"vehicleId": "V3", "limit": "5"}}, None)["body"])
    assert len(hist["readings"]) == 5 and hist["readings"][-1]["ts"] == frames[-1]["ts"]
    alerts = json.loads(api.lambda_handler({"rawPath": "/alerts"}, None)["body"])["alerts"]
    codes = [a["code"] for a in alerts]
    assert "CARGO_TEMP_HIGH" in codes
    assert codes.count("CARGO_TEMP_HIGH") == 1  # de-duplicated across readings
    bad = api.lambda_handler({"rawPath": "/history", "queryStringParameters": {"vehicleId": "x;drop"}}, None)
    assert bad["statusCode"] == 400


def test_connections_lambda(aws):
    conn = load_module("handler", "connections")
    conn._ddb = None
    ctx = {"routeKey": "$connect", "connectionId": "c1", "domainName": "x", "stage": "prod"}
    assert conn.lambda_handler({"requestContext": ctx}, None)["statusCode"] == 200
    table = boto3.resource("dynamodb").Table("FleetConnections")
    assert table.get_item(Key={"connectionId": "c1"}).get("Item")
    ctx["routeKey"] = "$disconnect"
    conn.lambda_handler({"requestContext": ctx}, None)
    assert "Item" not in table.get_item(Key={"connectionId": "c1"})
