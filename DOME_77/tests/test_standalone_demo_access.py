from datetime import datetime

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Base, Child, LessonEntitlement, LessonSession, Parent
from app.services import lesson_access, standalone_demo_access
from app.services.mobile_tokens import issue_session_token
from app.services.standalone_demo_access import (
    backfill_free_demo_entitlements,
    ensure_free_demo_entitlement,
)
from app.webapp import mobile_api


async def _memory_database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, sessions


@pytest.mark.asyncio
async def test_legacy_free_demo_helper_never_grants_access():
    engine, sessions = await _memory_database()
    try:
        async with sessions() as db:
            parent = Parent(
                email="standalone@example.com",
                password_hash="password-hash",
                email_verified=False,
            )
            db.add(parent)
            await db.flush()
            first = Child(parent_id=parent.id, display_name="First")
            second = Child(parent_id=parent.id, display_name="Second")
            db.add_all([first, second])
            await db.flush()

            row, created = await ensure_free_demo_entitlement(
                db, parent_id=parent.id, child_id=first.id
            )
            assert row is None and created is False

            parent.email_verified = True
            row, created = await ensure_free_demo_entitlement(
                db, parent_id=parent.id, child_id=first.id
            )
            assert row is None and created is False

            other, created = await ensure_free_demo_entitlement(
                db, parent_id=parent.id, child_id=second.id
            )
            assert other is None and created is False
            await db.commit()

        async with sessions() as db:
            assert await db.scalar(select(func.count(LessonEntitlement.id))) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_startup_backfill_is_noop_and_preserves_existing_records(monkeypatch):
    engine, sessions = await _memory_database()
    try:
        async with sessions() as db:
            parent = Parent(
                email="existing@example.com",
                password_hash="password-hash",
                email_verified=True,
            )
            db.add(parent)
            await db.flush()
            child = Child(parent_id=parent.id, display_name="Existing child")
            db.add(child)
            await db.flush()
            legacy = LessonEntitlement(child_id=child.id, lesson_id="demo_001", course_id="conversation", source="FREE_DEMO", status="ACTIVE", max_completed_runs=2, completed_runs=1)
            db.add(legacy)
            await db.commit()

        assert await backfill_free_demo_entitlements() == 0
        assert await backfill_free_demo_entitlements() == 0

        async with sessions() as db:
            entitlement = await db.scalar(select(LessonEntitlement))
            assert entitlement is not None
            assert entitlement.source == "FREE_DEMO"
            assert entitlement.completed_runs == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mobile_start_has_no_telegram_admin_bypass(monkeypatch):
    engine, sessions = await _memory_database()
    try:
        async with sessions() as db:
            parent = Parent(
                telegram_user_id=777,
                email="admin-standalone@example.com",
                password_hash="password-hash",
                email_verified=True,
            )
            db.add(parent)
            await db.flush()
            first = Child(parent_id=parent.id, display_name="First", language_level="PRE_A1")
            second = Child(parent_id=parent.id, display_name="Second", language_level="PRE_A1")
            db.add_all([first, second])
            await db.flush()
            await db.commit()
            parent_id, first_id, second_id = parent.id, first.id, second.id

        monkeypatch.setattr(mobile_api, "SessionLocal", sessions)
        monkeypatch.setattr(lesson_access, "SessionLocal", sessions)
        monkeypatch.setattr(settings, "mobile_auth_secret", "test-secret-that-is-long-enough-for-mobile-auth")
        monkeypatch.setattr(settings, "admin_telegram_ids", "777")
        token = issue_session_token(parent_id)

        app = web.Application()
        mobile_api.register_mobile_routes(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            response = await client.post(
                "/api/mobile/session/start",
                headers={"Authorization": f"Bearer {token}"},
                json={"child_id": first_id, "lesson_id": "demo_001"},
            )
            assert response.status == 403
            assert "PAYMENT_REQUIRED" in (await response.json())["error"]

            response = await client.post(
                "/api/mobile/session/start",
                headers={"Authorization": f"Bearer {token}"},
                json={"child_id": second_id, "lesson_id": "demo_001"},
            )
            assert response.status == 403
            assert "PAYMENT_REQUIRED" in (await response.json())["error"]
        finally:
            await client.close()

        async with sessions() as db:
            assert await db.scalar(select(func.count(LessonEntitlement.id))) == 0
            assert await db.scalar(select(func.count(LessonSession.id))) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mobile_session_auth_resume_progress_translate_and_tts(monkeypatch, tmp_path):
    engine, sessions = await _memory_database()
    try:
        async with sessions() as db:
            parent = Parent(
                email="mobile-session@example.com",
                password_hash="password-hash",
                email_verified=True,
            )
            db.add(parent)
            await db.flush()
            child = Child(parent_id=parent.id, display_name="Test", language_level="PRE_A1")
            db.add(child)
            await db.flush()
            db.add(LessonEntitlement(child_id=child.id, lesson_id="demo_001", course_id="conversation", source="SUBSCRIPTION", status="ACTIVE", max_completed_runs=2, completed_runs=0))
            await db.commit()
            parent_id, child_id = parent.id, child.id

        async def fake_translate(text, _source, _target):
            return f"translated:{text}"

        observed_tts = {}
        async def fake_synthesize_bilingual(target_text, target_language, native_text, native_language, _root, _prefix, _delivery_style="warm"):
            observed_tts.update(target_text=target_text,target_language=target_language,native_text=native_text,native_language=native_language)
            audio = tmp_path / "tts.ogg"
            audio.write_bytes((target_text+native_text).encode("utf-8"))
            return audio

        monkeypatch.setattr(mobile_api, "SessionLocal", sessions)
        monkeypatch.setattr(lesson_access, "SessionLocal", sessions)
        monkeypatch.setattr(mobile_api, "translate_text", fake_translate)
        monkeypatch.setattr(mobile_api, "synthesize_bilingual_speech", fake_synthesize_bilingual)
        monkeypatch.setattr(settings, "mobile_auth_secret", "test-secret-that-is-long-enough-for-mobile-auth")
        token = issue_session_token(parent_id)
        headers = {"Authorization": f"Bearer {token}"}

        app = web.Application()
        mobile_api.register_mobile_routes(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            response = await client.post(
                "/api/mobile/session/start",
                json={"child_id": child_id, "lesson_id": "demo_001"},
            )
            assert response.status == 401
            assert (await response.json())["code"] == "MOBILE_SESSION_INVALID"

            response = await client.post(
                "/api/mobile/session/start",
                headers=headers,
                json={"child_id": child_id, "lesson_id": "demo_001"},
            )
            assert response.status == 200
            started = await response.json()
            assert started["resumed"] is False
            session_id = started["session_id"]

            response = await client.post(
                f"/api/mobile/session/{session_id}/progress",
                headers=headers,
                json={"current_step": 1},
            )
            assert response.status == 200
            assert (await response.json())["current_step"] == 1
            for step in (2, 3):
                response = await client.post(
                    f"/api/mobile/session/{session_id}/progress",
                    headers=headers,
                    json={"current_step": step},
                )
                assert response.status == 200

            response = await client.post(
                "/api/mobile/session/start",
                headers=headers,
                json={"child_id": child_id, "lesson_id": "demo_001"},
            )
            assert response.status == 200
            resumed = await response.json()
            assert resumed["session_id"] == session_id
            assert resumed["resumed"] is True
            assert resumed["current_step"] == 3

            response = await client.post(
                "/api/mobile/translate",
                headers=headers,
                json={"text": "hello", "source_language": "ru", "target_language": "en"},
            )
            assert response.status == 200
            assert (await response.json())["text"] == "translated:hello"

            response = await client.get(
                f"/api/mobile/tts?text=hello&token={token}"
            )
            assert response.status == 401
            response = await client.get(
                "/api/mobile/tts.ogg?text=hello",
                headers=headers,
            )
            assert response.status == 200
            await response.read()
            assert response.content_type == "audio/ogg"
            assert response.headers["Content-Disposition"] == 'inline; filename="dome-tutor.ogg"'
            assert observed_tts == {"target_text":"translated:hello","target_language":"ru","native_text":"","native_language":"ru"}
        finally:
            await client.close()

        async with sessions() as db:
            assert await db.scalar(select(func.count(LessonSession.id))) == 1
            session = await db.get(LessonSession, session_id)
            assert session.current_step == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_persistent_mobile_account_survives_database_engine_restart(monkeypatch, tmp_path):
    database_path = tmp_path / "persistent-app.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    first_engine = create_async_engine(database_url)
    first_sessions = async_sessionmaker(first_engine, expire_on_commit=False)
    async with first_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    monkeypatch.setattr(settings, "mobile_auth_secret", "test-secret-that-is-long-enough-for-mobile-auth")
    async with first_sessions() as db:
        parent = Parent(
            email="persistent@example.com",
            password_hash="password-hash",
            email_verified=True,
        )
        db.add(parent)
        await db.flush()
        child = Child(parent_id=parent.id, display_name="Persistent Test", language_level="PRE_A1")
        db.add(child)
        await db.flush()
        entitlement = LessonEntitlement(child_id=child.id, lesson_id="demo_001", course_id="conversation", source="SUBSCRIPTION", status="ACTIVE", max_completed_runs=2, completed_runs=0)
        db.add(entitlement)
        await db.commit()
        parent_id, child_id, entitlement_id = parent.id, child.id, entitlement.id
    token = issue_session_token(parent_id)
    await first_engine.dispose()

    second_engine = create_async_engine(database_url)
    second_sessions = async_sessionmaker(second_engine, expire_on_commit=False)
    monkeypatch.setattr(mobile_api, "SessionLocal", second_sessions)
    monkeypatch.setattr(lesson_access, "SessionLocal", second_sessions)

    app = web.Application()
    mobile_api.register_mobile_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        headers = {"Authorization": f"Bearer {token}"}
        response = await client.get("/api/mobile/bootstrap", headers=headers)
        assert response.status == 200
        assert [row["name"] for row in (await response.json())["children"]] == ["Persistent Test"]

        response = await client.post(
            "/api/mobile/session/start",
            headers=headers,
            json={"child_id": child_id, "lesson_id": "demo_001"},
        )
        assert response.status == 200

        async with second_sessions() as db:
            entitlement = await db.get(LessonEntitlement, entitlement_id)
            assert entitlement is not None
            assert entitlement.child_id == child_id
            assert entitlement.lesson_id == "demo_001"
            assert entitlement.source == "SUBSCRIPTION"
    finally:
        await client.close()
        await second_engine.dispose()
