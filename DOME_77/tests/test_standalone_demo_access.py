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
async def test_free_demo_is_idempotent_and_only_for_verified_first_child():
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
            fixed_now = datetime(2026, 8, 22, 12, 0, 0)
            row, created = await ensure_free_demo_entitlement(
                db, parent_id=parent.id, child_id=first.id, now=fixed_now
            )
            assert created is True
            assert row is not None
            assert row.source == "FREE_DEMO"
            assert row.max_completed_runs == 2
            assert row.completed_runs == 0
            assert row.unlocked_at == fixed_now
            assert row.expires_at == datetime(2027, 6, 22, 12, 0, 0)

            same, created = await ensure_free_demo_entitlement(
                db, parent_id=parent.id, child_id=first.id
            )
            assert created is False and same.id == row.id
            assert same.expires_at == row.expires_at

            other, created = await ensure_free_demo_entitlement(
                db, parent_id=parent.id, child_id=second.id
            )
            assert other is None and created is False
            await db.commit()

        async with sessions() as db:
            assert await db.scalar(select(func.count(LessonEntitlement.id))) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_startup_backfill_uses_same_rule_for_existing_verified_account(monkeypatch):
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
            db.add(Child(parent_id=parent.id, display_name="Existing child"))
            await db.commit()

        monkeypatch.setattr(standalone_demo_access, "SessionLocal", sessions)
        assert await backfill_free_demo_entitlements() == 1
        assert await backfill_free_demo_entitlements() == 0

        async with sessions() as db:
            entitlement = await db.scalar(select(LessonEntitlement))
            assert entitlement is not None
            assert entitlement.lesson_id == "demo_001"
            assert entitlement.source == "FREE_DEMO"
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
            await ensure_free_demo_entitlement(db, parent_id=parent.id, child_id=first.id)
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
            assert response.status == 200

            response = await client.post(
                "/api/mobile/session/start",
                headers={"Authorization": f"Bearer {token}"},
                json={"child_id": second_id, "lesson_id": "demo_001"},
            )
            assert response.status == 403
            assert (await response.json())["error"] == "Урок недоступен: LOCKED"
        finally:
            await client.close()

        async with sessions() as db:
            assert await db.scalar(select(func.count(LessonEntitlement.id))) == 1
            assert await db.scalar(select(func.count(LessonSession.id))) == 1
    finally:
        await engine.dispose()
