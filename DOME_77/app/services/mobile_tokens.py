from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from app.core.config import settings


def _secret() -> bytes:
    raw = settings.mobile_auth_secret.strip()
    if len(raw) < 32:
        raise RuntimeError("MOBILE_AUTH_SECRET must contain at least 32 characters")
    return hashlib.sha256(raw.encode("utf-8") + b"|mobile-v1").digest()


def issue_session_token(parent_id: int, ttl: int = 60 * 60 * 24 * 90) -> str:
    payload = {"parent_id": int(parent_id), "exp": int(time.time()) + ttl, "v": 2}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def verify_session_token(token: str) -> int | None:
    try:
        body, signature = token.split(".", 1)
        expected = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        padded = body + "=" * ((4 - len(body) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return int(payload["parent_id"])
    except Exception:
        return None


def signed_media_token(value: str, ttl: int = 86400 * 7) -> str:
    expires_at = int(time.time()) + ttl
    body = f"{value}|{expires_at}"
    signature = hmac.new(_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
    return f"{expires_at}.{signature}"


def verify_media_token(value: str, token: str) -> bool:
    try:
        expires_raw, signature = token.split(".", 1)
        expires_at = int(expires_raw)
        if expires_at < int(time.time()):
            return False
        body = f"{value}|{expires_at}"
        expected = hmac.new(_secret(), body.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
        return hmac.compare_digest(signature, expected)
    except Exception:
        return False
