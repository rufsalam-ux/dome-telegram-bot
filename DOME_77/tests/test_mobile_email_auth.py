from datetime import UTC, datetime, timedelta
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings, settings
from app.db.models import Base, Parent
from app.db.session import _add_columns
from app.services import email_reports
from app.services.email_reports import _deliver, _message
from app.services.password_auth import (
    hash_verification_code,
    verify_password,
    verify_verification_code,
)
from app.webapp import mobile_api


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def test_railway_postgres_url_uses_async_driver():
    configured = Settings(database_url="postgresql://user:pass@db.example.test/dome")
    assert configured.database_url == "postgresql+asyncpg://user:pass@db.example.test/dome"


def test_settings_reads_exact_railway_smtp_variable_names(monkeypatch):
    railway_values = {
        "SMTP_HOST": "smtp.gmail.com",
        "SMTP_PORT": "587",
        "SMTP_USERNAME": "bilinguadom@gmail.com",
        "SMTP_PASSWORD": "test-app-password-not-a-real-secret",
        "SMTP_FROM_EMAIL": "bilinguadom@gmail.com",
        "SMTP_FROM_NAME": "DOME",
        "SMTP_STARTTLS": "true",
    }
    for name, value in railway_values.items():
        monkeypatch.setenv(name, value)

    configured = Settings(_env_file=None)

    assert configured.smtp_host == "smtp.gmail.com"
    assert configured.smtp_port == 587
    assert configured.smtp_username == "bilinguadom@gmail.com"
    assert configured.smtp_password == railway_values["SMTP_PASSWORD"]
    assert configured.smtp_from_email == "bilinguadom@gmail.com"
    assert configured.smtp_from_name == "DOME"
    assert configured.smtp_starttls is True
    assert configured.smtp_missing_variables == ()


def test_smtp_error_lists_missing_names_without_secret_values(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "smtp_username", "bilinguadom@gmail.com")
    monkeypatch.setattr(settings, "smtp_password", "")
    monkeypatch.setattr(settings, "smtp_from_email", "")

    with pytest.raises(RuntimeError) as error:
        _message("parent@example.com", "Subject", "Body")

    message = str(error.value)
    assert "SMTP_HOST" in message
    assert "SMTP_PASSWORD" in message
    assert "SMTP_FROM_EMAIL" in message
    assert "bilinguadom@gmail.com" not in message


def test_smtp_message_uses_configured_sender_name(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "smtp_from_email", "sender@example.test")
    monkeypatch.setattr(settings, "smtp_from_name", "DOME")
    monkeypatch.setattr(settings, "smtp_username", "sender@example.test")
    monkeypatch.setattr(settings, "smtp_password", "test-password")

    message = _message("parent@example.com", "Subject", "Body")

    assert message["From"] == "DOME <sender@example.test>"
    assert message["To"] == "parent@example.com"


def test_settings_reads_exact_railway_resend_variable_names(monkeypatch):
    monkeypatch.setenv("EMAIL_DELIVERY_PROVIDER", "resend")
    monkeypatch.setenv("RESEND_API_KEY", "test-resend-key-not-a-real-secret")
    monkeypatch.setenv("MAIL_FROM", "Bilingvadom <no-reply@bilinguadom.com>")

    configured = Settings(_env_file=None)

    assert configured.email_delivery_provider == "resend"
    assert configured.resend_api_key == "test-resend-key-not-a-real-secret"
    assert configured.mail_from == "Bilingvadom <no-reply@bilinguadom.com>"
    assert configured.email_delivery_missing_variables == ()


def test_resend_https_delivery_uses_existing_message_and_sender(monkeypatch):
    captured: dict[str, object] = {}

    class Response:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(settings, "email_delivery_provider", "resend")
    monkeypatch.setattr(settings, "resend_api_key", "test-resend-key-not-a-real-secret")
    monkeypatch.setattr(settings, "resend_api_url", "https://api.resend.com/emails")
    monkeypatch.setattr(settings, "mail_from", "Bilingvadom <no-reply@bilinguadom.com>")
    monkeypatch.setattr(settings, "email_api_timeout_seconds", 30)
    monkeypatch.setattr(email_reports, "urlopen", fake_urlopen)

    message = _message("parent@example.com", "Verification", "Code: 123456")
    _deliver(message)

    request = captured["request"]
    payload = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "https://api.resend.com/emails"
    assert captured["timeout"] == 30
    assert payload == {
        "from": "Bilingvadom <no-reply@bilinguadom.com>",
        "to": ["parent@example.com"],
        "subject": "Verification",
        "text": "Code: 123456\n",
    }
    assert request.get_header("Authorization") == "Bearer test-resend-key-not-a-real-secret"


def test_resend_configuration_reports_only_missing_variable_names(monkeypatch):
    monkeypatch.setattr(settings, "email_delivery_provider", "resend")
    monkeypatch.setattr(settings, "resend_api_key", "")
    monkeypatch.setattr(settings, "mail_from", "")

    with pytest.raises(RuntimeError) as error:
        _message("parent@example.com", "Subject", "Body")

    assert str(error.value).endswith("RESEND_API_KEY, MAIL_FROM")


@pytest.mark.asyncio
async def test_add_columns_is_sqlite_compatible_without_pragma():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE parents (id INTEGER PRIMARY KEY)"))
        await _add_columns(connection, "parents", {
            "email_verified": "BOOLEAN NOT NULL DEFAULT TRUE",
            "email_verification_code_hash": "VARCHAR(255)",
            "email_verification_expires_at": "TIMESTAMP",
        })
        columns = (await connection.execute(text("PRAGMA table_info(parents)"))).mappings().all()
    await engine.dispose()

    assert {column["name"] for column in columns} >= {
        "email_verified",
        "email_verification_code_hash",
        "email_verification_expires_at",
    }


def test_verification_code_hash_is_salted_and_bound_to_email_and_purpose():
    first = hash_verification_code("Parent@Example.COM", "123456")
    second = hash_verification_code("parent@example.com", "123456")

    assert first != second
    assert "123456" not in first
    assert verify_verification_code("parent@example.com", "123456", first)
    assert not verify_verification_code("other@example.com", "123456", first)
    assert not verify_verification_code("parent@example.com", "123456", first, "reset")


@pytest.mark.asyncio
async def test_mobile_register_resend_verify_and_login_flow(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    sent_codes: list[str] = []

    async def capture_verification_email(to_email: str, code: str, ttl_minutes: int = 10):
        assert to_email == "parent@example.com"
        assert ttl_minutes == 10
        sent_codes.append(code)

    monkeypatch.setattr(mobile_api, "SessionLocal", sessions)
    monkeypatch.setattr(mobile_api, "send_verification_email", capture_verification_email)
    monkeypatch.setattr(settings, "mobile_auth_secret", "test-secret-that-is-long-enough-for-mobile-auth")

    app = web.Application()
    mobile_api.register_mobile_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post("/api/mobile/register", json={
            "name": "Parent",
            "email": "  Parent@Example.COM ",
            "password": "correct horse battery staple",
        })
        assert response.status == 200
        assert (await response.json())["verification_required"] is True
        assert len(sent_codes) == 1 and len(sent_codes[0]) == 6

        async with sessions() as db:
            parent = await db.scalar(select(Parent))
            assert parent is not None
            assert parent.email == "parent@example.com"
            assert parent.email_verified is False
            assert parent.password_hash != "correct horse battery staple"
            assert verify_password("correct horse battery staple", parent.password_hash)
            assert parent.email_verification_code_hash != sent_codes[0]
            assert parent.email_verification_expires_at > utcnow()

        response = await client.post("/api/mobile/login", json={
            "email": "parent@example.com",
            "password": "correct horse battery staple",
        })
        assert response.status == 403
        assert (await response.json())["code"] == "EMAIL_NOT_VERIFIED"

        first_code = sent_codes[-1]
        response = await client.post("/api/mobile/resend-verification", json={"email": "PARENT@example.com"})
        assert response.status == 200
        assert len(sent_codes) == 2
        second_code = sent_codes[-1]

        response = await client.post("/api/mobile/verify-email", json={"email": "parent@example.com", "code": first_code})
        assert response.status == 400
        assert (await response.json())["error"] == "Неверный код подтверждения"

        async with sessions() as db:
            parent = await db.scalar(select(Parent))
            parent.email_verification_expires_at = utcnow() - timedelta(seconds=1)
            await db.commit()
        response = await client.post("/api/mobile/verify-email", json={"email": "parent@example.com", "code": second_code})
        assert response.status == 400
        assert "истёк" in (await response.json())["error"]

        response = await client.post("/api/mobile/resend-verification", json={"email": "parent@example.com"})
        assert response.status == 200
        response = await client.post("/api/mobile/verify-email", json={"email": "parent@example.com", "code": sent_codes[-1]})
        assert response.status == 200
        verified = await response.json()
        assert verified["token"]
        assert verified["parent"]["email_verified"] is True

        async with sessions() as db:
            parent = await db.scalar(select(Parent))
            assert parent.email_verified is True
            assert parent.email_verification_code_hash is None
            assert parent.email_verification_expires_at is None

        response = await client.post("/api/mobile/login", json={
            "email": "PARENT@EXAMPLE.COM",
            "password": "correct horse battery staple",
        })
        assert response.status == 200
        assert (await response.json())["token"]
    finally:
        await client.close()
        await engine.dispose()
