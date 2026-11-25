"""Tests for the dashboard site Lambda against a moto (fake) S3 bucket."""
import base64
import importlib
import os
import sys

import boto3
import pytest

moto = pytest.importorskip("moto")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BUCKET = "test-dashboard-bucket"


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setenv("SITE_BUCKET", BUCKET)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    with moto.mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        s3.put_object(Bucket=BUCKET, Key="index.html", Body=b"<html>fleet</html>")
        s3.put_object(Bucket=BUCKET, Key="app.js", Body=b"console.log(1);")
        s3.put_object(Bucket=BUCKET, Key="secret.txt", Body=b"do not serve")

        sys.path.insert(0, os.path.join(ROOT, "lambdas", "site"))
        try:
            sys.modules.pop("handler", None)
            mod = importlib.import_module("handler")
        finally:
            sys.path.pop(0)
            sys.modules.pop("handler", None)
        yield mod


def call(mod, path):
    return mod.lambda_handler({"rawPath": path}, None)


def test_root_serves_index_with_security_headers(site):
    r = call(site, "/")
    assert r["statusCode"] == 200
    assert base64.b64decode(r["body"]) == b"<html>fleet</html>"
    assert r["headers"]["Content-Type"].startswith("text/html")
    assert "Strict-Transport-Security" in r["headers"]


def test_serves_allow_listed_file(site):
    r = call(site, "/app.js")
    assert r["statusCode"] == 200
    assert r["headers"]["Content-Type"].startswith("application/javascript")


def test_rejects_files_not_on_allow_list(site):
    assert call(site, "/secret.txt")["statusCode"] == 404
    assert call(site, "/../secret.txt")["statusCode"] == 404


def test_missing_allow_listed_file_is_404(site):
    assert call(site, "/styles.css")["statusCode"] == 404
