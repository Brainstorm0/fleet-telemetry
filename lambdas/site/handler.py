"""Serves the dashboard website over HTTPS from the private S3 bucket.

Why this exists: AWS Academy Learner Lab blocks both ways CloudFront can read
a private bucket (Origin Access Control and Origin Access Identity). Instead
this Lambda, behind the existing HTTP API (TLS + throttling), reads the files
with LabRole and returns them. The bucket itself stays fully private.

HTTP API (payload format 2.0) routes:
  GET /        -> index.html
  GET /{file}  -> one of the allow-listed dashboard files
"""
import base64
import os

import boto3
from botocore.exceptions import ClientError

BUCKET = os.environ.get("SITE_BUCKET", "")

# Only these keys can ever be read: no path traversal, no listing the bucket.
FILES = {
    "index.html": ("text/html; charset=utf-8", "no-cache"),
    "config.js": ("application/javascript; charset=utf-8", "no-cache"),
    "app.js": ("application/javascript; charset=utf-8", "max-age=60"),
    "styles.css": ("text/css; charset=utf-8", "max-age=60"),
}

SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}

_s3 = None


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _text(status, message):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store", **SECURITY_HEADERS},
        "body": message,
    }


def lambda_handler(event, context):
    path = event.get("rawPath", "/")
    key = path.lstrip("/") or "index.html"
    if key not in FILES:
        return _text(404, "Not found")

    try:
        obj = _client().get_object(Bucket=BUCKET, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return _text(404, "Not found")
        print(f"S3 read failed for {key}: {exc}")
        return _text(502, "Dashboard temporarily unavailable")

    content_type, cache_control = FILES[key]
    body = obj["Body"].read()
    return {
        "statusCode": 200,
        "headers": {"Content-Type": content_type, "Cache-Control": cache_control, **SECURITY_HEADERS},
        "body": base64.b64encode(body).decode("ascii"),
        "isBase64Encoded": True,
    }
