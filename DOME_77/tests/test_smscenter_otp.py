import pytest
from app.services.sms_consent import create_otp_challenge, verify_otp_code, normalize_phone


def test_normalize_georgian_phone():
    assert normalize_phone('+995 599-123-456') == '995599123456'


def test_otp_hash_and_expiry(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, 'consent_hash_secret', 'x' * 40)
    monkeypatch.setattr(settings, 'sms_otp_ttl_seconds', 600)
    code, challenge = create_otp_challenge('+995599123456', now=1000)
    assert len(code) == 6 and code.isdigit()
    assert verify_otp_code(phone=challenge.phone, code=code, code_hash=challenge.code_hash,
                           salt=challenge.salt, expires_at=challenge.expires_at, now=1200)
    assert not verify_otp_code(phone=challenge.phone, code='000000', code_hash=challenge.code_hash,
                               salt=challenge.salt, expires_at=challenge.expires_at, now=1200)
    assert not verify_otp_code(phone=challenge.phone, code=code, code_hash=challenge.code_hash,
                               salt=challenge.salt, expires_at=challenge.expires_at, now=1700)
