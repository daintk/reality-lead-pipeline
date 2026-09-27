"""Ověření podpisu webhooku (HMAC-SHA256) – nikdo cizí nám nepodstrčí data."""
from __future__ import annotations

import hashlib
import hmac
import time

MAX_SKEW_SECONDS = 300


def sign(secret: str, timestamp: str, body: bytes) -> str:
    msg = timestamp.encode() + b"." + body
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify(secret: str, timestamp: str | None, signature: str | None, body: bytes) -> bool:
    if not secret or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts) > MAX_SKEW_SECONDS:  # ochrana proti replay útoku
        return False
    expected = sign(secret, timestamp, body)
    return hmac.compare_digest(expected, signature)
