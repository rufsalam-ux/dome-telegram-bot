from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass

import aiohttp

from app.core.config import settings


class SMSConsentError(RuntimeError):
    """A readable SMS provider/configuration error."""


@dataclass(frozen=True)
class OTPChallenge:
    phone: str
    code_hash: str
    salt: str
    expires_at: int
    sent_at: int


def _sms_bypass_enabled() -> bool:
    """Return True when SMS verification is temporarily bypassed."""
    return os.getenv("SMS_BYPASS", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


def normalize_phone(phone: str) -> str:
    """Return an international number without +, spaces or punctuation."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if not 9 <= len(digits) <= 15:
        raise SMSConsentError(
            "Введите номер в международном формате, например +995599123456."
        )
    return digits


def _secret() -> bytes:
    value = settings.consent_hash_secret.strip()
    if len(value) < 24:
        raise SMSConsentError(
            "Добавьте в .env CONSENT_HASH_SECRET — длинную случайную строку "
            "не короче 24 символов."
        )
    return value.encode("utf-8")


def _make_hash(phone: str, code: str, salt: str) -> str:
    message = f"{phone}:{code}:{salt}".encode("utf-8")
    return hmac.new(_secret(), message, hashlib.sha256).hexdigest()


def create_otp_challenge(
    phone: str,
    *,
    now: int | None = None,
) -> tuple[str, OTPChallenge]:
    normalized = normalize_phone(phone)
    issued_at = int(now if now is not None else time.time())
    code = f"{secrets.randbelow(1_000_000):06d}"
    salt = secrets.token_hex(16)
    challenge = OTPChallenge(
        phone=normalized,
        code_hash=_make_hash(normalized, code, salt),
        salt=salt,
        expires_at=issued_at + settings.sms_otp_ttl_seconds,
        sent_at=issued_at,
    )
    return code, challenge


def verify_otp_code(
    *,
    phone: str,
    code: str,
    code_hash: str,
    salt: str,
    expires_at: int,
    now: int | None = None,
) -> bool:
    if _sms_bypass_enabled():
        return True
    checked_at = int(now if now is not None else time.time())
    if checked_at > int(expires_at):
        return False
    stripped = code.strip()
    if not (stripped.isdigit() and len(stripped) == 6):
        return False
    normalized = normalize_phone(phone)
    expected = _make_hash(normalized, stripped, salt)
    return hmac.compare_digest(expected, code_hash)


def _provider_config() -> tuple[str, str, str]:
    api_key = settings.smscenter_api_key.strip()
    sender = settings.smscenter_sender_id.strip()
    api_url = settings.smscenter_api_url.strip()
    if not api_key:
        raise SMSConsentError("SMS не настроено. Добавьте SMSCENTER_API_KEY в .env.")
    if not sender:
        raise SMSConsentError(
            "SMS не настроено. Добавьте одобренное имя отправителя "
            "в SMSCENTER_SENDER_ID."
        )
    if not api_url.startswith("https://"):
        raise SMSConsentError("SMSCENTER_API_URL должен начинаться с https://")
    return api_key, sender, api_url


async def _send_sms(phone: str, text: str) -> str:
    api_key, sender, api_url = _provider_config()
    payload = {
        "from": sender,
        "to": normalize_phone(phone),
        "content": text,
        "api-key": api_key,
    }
    headers = {
        "api-key": api_key,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                api_url,
                json=payload,
                headers=headers,
            ) as response:
                body = await response.text()
                try:
                    data = json.loads(body) if body else {}
                except json.JSONDecodeError:
                    data = {}
                success = bool(data.get("success"))
                if response.status >= 300 or not success:
                    message = data.get("message") or data.get("error") or body[:300]
                    raise SMSConsentError(
                        f"SMSCenter не отправил код (HTTP {response.status}). "
                        "Проверьте активацию аккаунта, API-ключ, баланс "
                        f"и имя отправителя. {message}"
                    )
                client_id = data.get("data", {}).get("client_id")
                return str(client_id or "")
    except aiohttp.ClientError as exc:
        raise SMSConsentError(
            f"Не удалось подключиться к SMSCenter: {exc}"
        ) from exc


async def send_verification(phone: str) -> OTPChallenge:
    """Create an OTP and send it unless temporary bypass mode is enabled."""
    code, challenge = create_otp_challenge(phone)
    if _sms_bypass_enabled():
        print(f"BYPASS: SMS skipped for {challenge.phone}")
        return challenge
    minutes = max(1, settings.sms_otp_ttl_seconds // 60)
    await _send_sms(
        challenge.phone,
        f"DOME: код подтверждения {code}. Код действует {minutes} мин.",
    )
    return challenge


async def check_verification(
    phone: str,
    code: str,
    *,
    code_hash: str,
    salt: str,
    expires_at: int,
) -> bool:
    """Verify the OTP locally or accept it in temporary bypass mode."""
    return verify_otp_code(
        phone=phone,
        code=code,
        code_hash=code_hash,
        salt=salt,
        expires_at=expires_at,
    )
