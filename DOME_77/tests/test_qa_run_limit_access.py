from datetime import datetime, timedelta

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import (
    Base,
    Child,
    LessonEntitlement,
    LessonSession,
    Parent,
    QaAccessAuditEvent,
    QaAccessGrant,
)
from app.services import lesson_access, qa_access
from app.db import session as db_session
from app.core.config import settings
from app.services.qa_access import (
    ADMIN_ROLE,
    QA_TEST_ROLE,
    bootstrap_qa_access_grants,
    grant_run_limit_access,
    revoke_run_limit_access,
)
from app.services.mobile_tokens import issue_session_token
from app.webapp import mobile_api


async def _memory_database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, sessions


async def _account_with_entitlement(sessions, *, role="STANDARD", completed_runs=2, expired=False):
    async with sessions() as db:
        parent = Parent(
            email=f"{role.lower()}-{completed_runs}-{int(expired)}@example.com",
            password_hash="password-hash",
            email_verified=True,
            account_role=role,
        )
        db.add(parent)
        await db.flush()
        child = Child(parent_id=parent.id, display_name=f"{role} child")
        db.add(child)
        await db.flush()
        entitlement = LessonEntitlement(
            child_id=child.id,
            lesson_id="demo_001",
            course_id="conversation",
            max_completed_runs=2,
            completed_runs=completed_runs,
            expires_at=datetime.utcnow() - timedelta(days=1) if expired else datetime.utcnow() + timedelta(days=30),
            source="FREE_DEMO",
            status="COMPLETED" if completed_runs >= 2 else "ACTIVE",
        )
        db.add(entitlement)
        await db.commit()
        return parent.id, child.id, entitlement.id


@pytest.mark.asyncio
async def test_existing_database_migrates_qa_role_and_audit_tables(monkeypatch, tmp_path):
    database_path = tmp_path / "legacy.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path.as_posix()}")
    try:
        async with engine.begin() as connection:
            await connection.execute(text(
                "CREATE TABLE parents (id INTEGER PRIMARY KEY, telegram_user_id BIGINT, "
                "display_name VARCHAR(120), created_at DATETIME)"
            ))
        monkeypatch.setattr(db_session, "engine", engine)
        monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")
        await db_session.init_db()
        async with engine.begin() as connection:
            columns = await connection.run_sync(
                lambda sync_connection: {
                    column["name"] for column in inspect(sync_connection).get_columns("parents")
                }
            )
            tables = await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )
        assert "account_role" in columns
        assert {"qa_access_grants", "qa_access_audit_events"}.issubset(tables)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_standard_accounts_keep_two_run_limit(monkeypatch):
    engine, sessions = await _memory_database()
    try:
        _, child_id, entitlement_id = await _account_with_entitlement(sessions)
        monkeypatch.setattr(lesson_access, "SessionLocal", sessions)
        monkeypatch.setattr(qa_access, "SessionLocal", sessions)

        ok, reason, row = await lesson_access.can_start(child_id, "demo_001", "conversation")
        assert (ok, reason, row.id) == (False, "RUN_LIMIT", entitlement_id)

        row = await lesson_access.mark_completed(child_id, "demo_001", "conversation")
        assert row.completed_runs == 2
        assert row.max_completed_runs == 2
        async with sessions() as db:
            assert await db.scalar(select(func.count(QaAccessAuditEvent.id))) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_role_and_scoped_grant_are_both_required(monkeypatch):
    engine, sessions = await _memory_database()
    try:
        parent_id, child_id, _ = await _account_with_entitlement(sessions, role=QA_TEST_ROLE)
        monkeypatch.setattr(lesson_access, "SessionLocal", sessions)
        monkeypatch.setattr(qa_access, "SessionLocal", sessions)

        ok, reason, _ = await lesson_access.can_start(child_id, "demo_001", "conversation")
        assert (ok, reason) == (False, "RUN_LIMIT")

        async with sessions() as db:
            parent = await db.get(Parent, parent_id)
            parent.account_role = "STANDARD"
            db.add(QaAccessGrant(
                parent_id=parent_id,
                child_id=child_id,
                lesson_id="demo_001",
                course_id="conversation",
                permission="UNLIMITED_LESSON_RUNS",
                status="ACTIVE",
                granted_by="test",
                reason="test",
            ))
            await db.commit()
        ok, reason, _ = await lesson_access.can_start(child_id, "demo_001", "conversation")
        assert (ok, reason) == (False, "RUN_LIMIT")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_explicit_qa_grant_allows_unlimited_runs_and_audits(monkeypatch):
    engine, sessions = await _memory_database()
    try:
        parent_id, child_id, entitlement_id = await _account_with_entitlement(sessions)
        monkeypatch.setattr(lesson_access, "SessionLocal", sessions)
        monkeypatch.setattr(qa_access, "SessionLocal", sessions)

        async with sessions() as db:
            grant, changed = await grant_run_limit_access(
                db,
                child_id=child_id,
                lesson_id="demo_001",
                course_id="conversation",
                actor="telegram-admin:1",
                reason="QA regression testing",
                account_role=ADMIN_ROLE,
            )
            assert changed is True
            await db.commit()
            grant_id = grant.id

        ok, reason, _ = await lesson_access.can_start(child_id, "demo_001", "conversation")
        assert (ok, reason) == (True, "QA_RUN_LIMIT_BYPASS")
        ok, reason, _ = await lesson_access.can_start(child_id, "other_lesson", "conversation")
        assert (ok, reason) == (False, "LOCKED")

        async with sessions() as db:
            session = LessonSession(child_id=child_id, lesson_id="demo_001", status="IN_PROGRESS")
            db.add(session)
            await db.commit()
            session_id = session.id
        row, newly_completed = await lesson_access.complete_session_once(
            session_id=session_id,
            child_id=child_id,
            lesson_id="demo_001",
            course_id="conversation",
            final_step=34,
        )
        assert newly_completed is True
        assert row.completed_runs == 3
        assert row.max_completed_runs == 2

        async with sessions() as db:
            events = list((await db.scalars(select(QaAccessAuditEvent).order_by(QaAccessAuditEvent.id))).all())
            assert [event.event_type for event in events] == [
                "QA_ACCESS_GRANTED",
                "RUN_LIMIT_BYPASS_AUTHORIZED",
                "QA_LIMIT_BYPASS_COMPLETED",
            ]
            assert all(event.parent_id == parent_id for event in events)
            assert all(event.child_id == child_id for event in events)
            assert all(event.grant_id == grant_id for event in events)
            assert events[-1].lesson_session_id == session_id
            assert events[-1].completed_runs == 3
            assert (await db.get(LessonEntitlement, entitlement_id)) is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_qa_mobile_session_starts_with_production_like_free_space(monkeypatch):
    engine, sessions = await _memory_database()
    client = None
    try:
        parent_id, child_id, _ = await _account_with_entitlement(
            sessions, role=QA_TEST_ROLE, completed_runs=16
        )
        async with sessions() as db:
            await grant_run_limit_access(
                db,
                child_id=child_id,
                lesson_id="demo_001",
                course_id="conversation",
                actor="owner-approved-test",
                reason="QA regression testing",
                account_role=QA_TEST_ROLE,
            )
            await db.commit()

        monkeypatch.setattr(lesson_access, "SessionLocal", sessions)
        monkeypatch.setattr(qa_access, "SessionLocal", sessions)
        monkeypatch.setattr(mobile_api, "SessionLocal", sessions)
        monkeypatch.setattr(settings, "mobile_auth_secret", "qa-session-start-secret-that-is-long-enough")
        monkeypatch.setattr(mobile_api, "ensure_runtime_storage_capacity", lambda: {
            "before": 9_207_808,
            "after": 9_207_808,
            "target": 64 * 1024 * 1024,
            "minimum": 4 * 1024 * 1024,
            "target_met": False,
            "files": 0,
            "bytes": 0,
            "ready": True,
        })

        app = web.Application()
        mobile_api.register_mobile_routes(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        response = await client.post(
            "/api/mobile/session/start",
            headers={"Authorization": f"Bearer {issue_session_token(parent_id)}"},
            json={"child_id": child_id, "lesson_id": "demo_001"},
        )
        payload = await response.json()

        assert response.status == 200
        assert payload["lesson_id"] == "demo_001"
        assert payload["run_number"] == 17
        assert payload["lesson_version"].startswith("demo_001:r")
        async with sessions() as db:
            assert await db.scalar(select(func.count(LessonSession.id))) == 1
            events = list((await db.scalars(select(QaAccessAuditEvent).order_by(QaAccessAuditEvent.id))).all())
            assert events[-1].event_type == "RUN_LIMIT_BYPASS_AUTHORIZED"
            assert events[-1].completed_runs == 16
    finally:
        if client is not None:
            await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_qa_grant_never_bypasses_expiration(monkeypatch):
    engine, sessions = await _memory_database()
    try:
        _, child_id, _ = await _account_with_entitlement(sessions, expired=True)
        monkeypatch.setattr(lesson_access, "SessionLocal", sessions)
        monkeypatch.setattr(qa_access, "SessionLocal", sessions)
        async with sessions() as db:
            await grant_run_limit_access(
                db,
                child_id=child_id,
                lesson_id="demo_001",
                course_id="conversation",
                actor="telegram-admin:1",
                reason="QA regression testing",
            )
            await db.commit()
        ok, reason, _ = await lesson_access.can_start(child_id, "demo_001", "conversation")
        assert (ok, reason) == (False, "EXPIRED")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_revocation_restores_normal_limit_and_is_not_rebootstrapped(monkeypatch):
    engine, sessions = await _memory_database()
    try:
        async with sessions() as db:
            parent = Parent(
                email="current-test@example.com",
                password_hash="password-hash",
                email_verified=True,
            )
            db.add(parent)
            await db.flush()
            child = Child(parent_id=parent.id, display_name="Test")
            db.add(child)
            await db.flush()
            db.add(LessonEntitlement(
                child_id=child.id,
                lesson_id="demo_001",
                course_id="conversation",
                max_completed_runs=2,
                completed_runs=2,
                expires_at=datetime.utcnow() + timedelta(days=30),
                source="FREE_DEMO",
                status="COMPLETED",
            ))
            await db.commit()
            parent_id, child_id = parent.id, child.id

        config = {
            "schema_version": 1,
            "bootstrap_grants": [{
                "external_key": "owner-approved-test",
                "child_display_name": "Test",
                "require_unique_match": True,
                "require_verified_parent": True,
                "lesson_id": "demo_001",
                "course_id": "conversation",
                "account_role": "QA_TEST",
                "reason": "owner approved",
            }],
        }
        monkeypatch.setattr(qa_access, "SessionLocal", sessions)
        monkeypatch.setattr(qa_access, "_bootstrap_config", lambda: config)
        monkeypatch.setattr(lesson_access, "SessionLocal", sessions)

        assert await bootstrap_qa_access_grants() == {"created": 1, "existing": 0, "skipped": 0}
        assert await bootstrap_qa_access_grants() == {"created": 0, "existing": 1, "skipped": 0}
        ok, reason, _ = await lesson_access.can_start(child_id, "demo_001", "conversation")
        assert (ok, reason) == (True, "QA_RUN_LIMIT_BYPASS")

        async with sessions() as db:
            changed = await revoke_run_limit_access(
                db,
                child_id=child_id,
                lesson_id="demo_001",
                course_id="conversation",
                actor="telegram-admin:1",
                reason="QA finished",
            )
            assert changed is True
            await db.commit()
        assert await bootstrap_qa_access_grants() == {"created": 0, "existing": 1, "skipped": 0}
        ok, reason, _ = await lesson_access.can_start(child_id, "demo_001", "conversation")
        assert (ok, reason) == (False, "RUN_LIMIT")
        async with sessions() as db:
            parent = await db.get(Parent, parent_id)
            assert parent.account_role == "STANDARD"
            assert await db.scalar(select(func.count(QaAccessAuditEvent.id))) == 3
    finally:
        await engine.dispose()
