"""token แบบ stateless: base64(json).hmac — ใช้กับ client_id, code, access/refresh, device, session, csrf"""
import base64
import hashlib
import hmac
import json
import os
import time

SECRET_KEY = os.environ["SECRET_KEY"].encode()


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(kind: str, data: dict, ttl: int | None) -> str:
    body = {"k": kind, **data}
    if ttl:
        body["exp"] = int(time.time()) + ttl
    raw = b64(json.dumps(body, separators=(",", ":")).encode())
    mac = b64(hmac.new(SECRET_KEY, raw.encode(), hashlib.sha256).digest())
    return f"{raw}.{mac}"


def verify(token: str, kind: str) -> dict | None:
    try:
        raw, mac = token.split(".")
        good = b64(hmac.new(SECRET_KEY, raw.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(mac, good):
            return None
        body = json.loads(_unb64(raw))
    except Exception:
        return None
    if body.get("k") != kind or ("exp" in body and body["exp"] < time.time()):
        return None
    return body
