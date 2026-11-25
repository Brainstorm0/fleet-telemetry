"""HMAC-SHA256 signing helpers for telemetry frames.

The simulator signs every frame and the processor Lambda verifies the
signature before the data is trusted. This protects against tampering
while the message travels to the cloud.

The signature is computed over a *canonical* JSON string: all fields
except "sig", keys sorted, no extra spaces. Both sides must build the
string the same way, so this single file is shared by the simulator,
the local demo server and the Lambda.
"""
import hashlib
import hmac
import json

SIG_FIELD = "sig"


def canonical_string(frame: dict) -> str:
    body = {k: v for k, v in frame.items() if k != SIG_FIELD}
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def sign_frame(frame: dict, secret: str) -> dict:
    """Return a copy of the frame with a "sig" field added."""
    digest = hmac.new(
        secret.encode("utf-8"),
        canonical_string(frame).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    signed = dict(frame)
    signed[SIG_FIELD] = digest
    return signed


def verify_frame(frame: dict, secret: str) -> bool:
    """True if the frame carries a valid signature for this secret."""
    received = frame.get(SIG_FIELD)
    if not isinstance(received, str):
        return False
    expected = sign_frame(frame, secret)[SIG_FIELD]
    return hmac.compare_digest(received, expected)
