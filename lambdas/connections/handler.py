"""WebSocket connection manager for API Gateway.

Routes:
  $connect    -> save the connectionId in DynamoDB
  $disconnect -> delete it
  $default    -> reply to {"action": "ping"} with a pong (keeps the socket alive)
"""
import json
import os
import time

import boto3

CONNECTIONS_TABLE = os.environ.get("CONNECTIONS_TABLE", "FleetConnections")
CONNECTION_TTL_SEC = 2 * 60 * 60  # API Gateway closes sockets after 2 h anyway

_ddb = None


def _table():
    global _ddb
    if _ddb is None:
        _ddb = boto3.resource("dynamodb")
    return _ddb.Table(CONNECTIONS_TABLE)


def _ws_client(ctx):
    endpoint = f"https://{ctx['domainName']}/{ctx['stage']}"
    return boto3.client("apigatewaymanagementapi", endpoint_url=endpoint)


def lambda_handler(event, context):
    ctx = event.get("requestContext", {})
    route = ctx.get("routeKey")
    cid = ctx.get("connectionId")

    if route == "$connect":
        _table().put_item(Item={
            "connectionId": cid,
            "connectedAt": int(time.time()),
            "ExpiresAt": int(time.time()) + CONNECTION_TTL_SEC,
        })
        return {"statusCode": 200, "body": "connected"}

    if route == "$disconnect":
        _table().delete_item(Key={"connectionId": cid})
        return {"statusCode": 200, "body": "disconnected"}

    # $default
    try:
        body = json.loads(event.get("body") or "{}")
    except ValueError:
        body = {}
    if body.get("action") == "ping":
        _ws_client(ctx).post_to_connection(
            ConnectionId=cid,
            Data=json.dumps({"type": "pong", "ts": int(time.time() * 1000)}).encode(),
        )
    return {"statusCode": 200, "body": "ok"}
