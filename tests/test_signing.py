import json

from signing import sign_frame, verify_frame

FRAME = {"vehicleId": "V1", "ts": 1700000000000, "cargoTempC": 4.25, "lat": 21.17, "lon": 72.83}


def test_sign_and_verify():
    signed = sign_frame(FRAME, "secret")
    assert "sig" in signed and len(signed["sig"]) == 64
    assert verify_frame(signed, "secret")


def test_tampered_value_is_rejected():
    signed = sign_frame(FRAME, "secret")
    signed["cargoTempC"] = 1.0
    assert not verify_frame(signed, "secret")


def test_wrong_secret_is_rejected():
    assert not verify_frame(sign_frame(FRAME, "secret"), "other")


def test_missing_signature_is_rejected():
    assert not verify_frame(dict(FRAME), "secret")


def test_signature_survives_json_round_trip():
    # This is exactly what happens on the way through Kinesis
    signed = sign_frame(FRAME, "secret")
    assert verify_frame(json.loads(json.dumps(signed)), "secret")
