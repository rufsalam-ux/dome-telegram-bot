"""Integration coverage for standalone tester access administration.

The database is in-memory: these tests create only disposable accounts and
never inspect or mutate a real Railway profile, child, recording or progress.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Base, Character, Child, LessonSession, Parent
from app.services import account_access, lesson_access, platform_settings
from app.services.account_access import ACTIVE, BLOCKED, PENDING_APPROVAL, account_status, set_account_status
from app.services.mobile_tokens import issue_session_token, signed_media_token
from app.webapp import content_studio, mobile_api


def _lesson() -> dict:
    return {
        "lesson_id": "demo_001", "course_id": "conversation", "title": "Tester lesson",
        "revision": 1, "publication_status": "PUBLISHED", "slides": [{"slide_id": "start", "order": 1, "type": "passive"}],
        "required_phrases": [],
    }


@asynccontextmanager
async def _api(monkeypatch, tmp_path, mode: str = "AUTOMATIC"):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    client: TestClient | None = None
    sent: list[str] = []
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async def capture_email(_email: str, code: str, _ttl: int = 10) -> None:
            sent.append(code)

        monkeypatch.setattr(mobile_api, "SessionLocal", sessions)
        monkeypatch.setattr(lesson_access, "SessionLocal", sessions)
        monkeypatch.setattr(mobile_api, "send_verification_email", capture_email)
        monkeypatch.setattr(mobile_api, "_load_mobile_lesson", lambda _lesson_id: _lesson())
        monkeypatch.setattr(mobile_api, "ensure_runtime_storage_capacity", lambda: {"before": 1024, "after": 1024, "target": 1, "minimum": 1, "target_met": True, "ready": True})
        monkeypatch.setattr(settings, "mobile_auth_secret", "account-access-test-secret-long-enough")
        monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")
        monkeypatch.setattr(account_access, "load_settings", lambda _name: {"schema_version": "1.0", "new_user_access_mode": mode})

        app = web.Application()
        mobile_api.register_mobile_routes(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        yield client, sessions, sent
    finally:
        if client is not None:
            await client.close()
        await engine.dispose()


async def _register_and_verify(client: TestClient, sent: list[str], email: str = "tester@example.com") -> dict:
    response = await client.post("/api/mobile/register", json={"name": "Tester", "email": email, "password": "correct horse battery staple"})
    assert response.status == 200
    response = await client.post("/api/mobile/verify-email", json={"email": email, "code": sent[-1]})
    assert response.status == 200
    return await response.json()


@pytest.mark.asyncio
async def test_new_user_is_active_by_default_and_can_start_lesson(monkeypatch, tmp_path):
    async with _api(monkeypatch, tmp_path) as (client, sessions, sent):
        verified = await _register_and_verify(client, sent)
        assert verified["parent"]["account_status"] == ACTIVE
        headers = {"Authorization": f"Bearer {verified['token']}"}
        child_response = await client.post("/api/mobile/children", headers=headers, json={"name": "Mila", "age_years": 7, "target_language": "en", "native_language": "ru"})
        assert child_response.status == 201
        child = await child_response.json()
        started = await client.post("/api/mobile/session/start", headers=headers, json={"child_id": child["id"], "lesson_id": "demo_001"})
        assert started.status == 200
        async with sessions() as db:
            parent = await db.scalar(select(Parent).where(Parent.email == "tester@example.com"))
        assert parent is not None and account_status(parent) == ACTIVE


@pytest.mark.asyncio
async def test_block_denies_protected_routes_without_deleting_progress_and_unblock_restores(monkeypatch, tmp_path):
    async with _api(monkeypatch, tmp_path) as (client, sessions, sent):
        verified = await _register_and_verify(client, sent)
        headers = {"Authorization": f"Bearer {verified['token']}"}
        child_response = await client.post("/api/mobile/children", headers=headers, json={"name": "Mila", "age_years": 7, "target_language": "en", "native_language": "ru"})
        child = await child_response.json()
        started = await client.post("/api/mobile/session/start", headers=headers, json={"child_id": child["id"], "lesson_id": "demo_001"})
        assert started.status == 200
        started_payload = await started.json()
        session_id = started_payload["session_id"]
        progress = await client.post(
            f"/api/mobile/session/{session_id}/progress",
            headers=headers,
            json={"current_step_id": "start", "lesson_version": started_payload["lesson_version"]},
        )
        assert progress.status == 200

        async with sessions() as db:
            parent = await db.scalar(select(Parent).where(Parent.email == "tester@example.com"))
            assert parent is not None
            character = Character(child_id=child["id"], original_path="unused.png", processed_path="unused.png", status="READY")
            db.add(character)
            await db.flush()
            character_id = character.id
            set_account_status(parent, BLOCKED, actor="test")
            await db.commit()

        for method, path, body in [
            (client.get, "/api/mobile/bootstrap", None),
            (client.get, f"/api/mobile/child/{child['id']}/lessons", None),
            (client.post, "/api/mobile/session/start", {"child_id": child["id"], "lesson_id": "demo_001"}),
            (client.post, f"/api/mobile/session/{session_id}/progress", {"current_step_id": "start", "lesson_version": "1"}),
            (client.post, f"/api/mobile/session/{session_id}/complete", {}),
        ]:
            response = await method(path, headers=headers, **({"json": body} if body is not None else {}))
            assert response.status == 403
            assert (await response.json())["code"] == "ACCOUNT_BLOCKED"

        # Existing signed URLs are private media, not an irrevocable grant.
        # A block must revoke them too even though native image/video elements
        # do not always attach an Authorization header.
        hero_token = signed_media_token(f"hero:{child['id']}:{character_id}")
        hero = await client.get(f"/api/mobile/hero/file/{child['id']}/{character_id}?t={hero_token}")
        assert hero.status == 403
        assert (await hero.json())["code"] == "ACCOUNT_BLOCKED"
        movie_token = signed_media_token(f"movie:{child['id']}:private.mp4")
        movie = await client.get(f"/api/mobile/movie/{child['id']}/private.mp4?t={movie_token}")
        assert movie.status == 403
        assert (await movie.json())["code"] == "ACCOUNT_BLOCKED"

        async with sessions() as db:
            saved = await db.get(LessonSession, session_id)
            parent = await db.scalar(select(Parent).where(Parent.email == "tester@example.com"))
            assert saved is not None and saved.current_step_id == "start"
            assert parent is not None
            set_account_status(parent, ACTIVE, actor="test")
            await db.commit()

        restored = await client.get("/api/mobile/bootstrap", headers=headers)
        assert restored.status == 200
        resumed = await client.post("/api/mobile/session/start", headers=headers, json={"child_id": child["id"], "lesson_id": "demo_001"})
        assert resumed.status == 200
        assert (await resumed.json())["session_id"] == session_id


@pytest.mark.asyncio
async def test_manual_mode_starts_pending_then_admin_approval_restores_login(monkeypatch, tmp_path):
    async with _api(monkeypatch, tmp_path, mode="MANUAL") as (client, sessions, sent):
        response = await client.post("/api/mobile/register", json={"name": "Tester", "email": "pending@example.com", "password": "correct horse battery staple"})
        assert response.status == 200
        response = await client.post("/api/mobile/verify-email", json={"email": "pending@example.com", "code": sent[-1]})
        assert response.status == 403
        assert (await response.json())["code"] == "ACCOUNT_PENDING_APPROVAL"
        async with sessions() as db:
            parent = await db.scalar(select(Parent).where(Parent.email == "pending@example.com"))
            assert parent is not None and account_status(parent) == PENDING_APPROVAL
            set_account_status(parent, ACTIVE, actor="test")
            await db.commit()
        response = await client.post("/api/mobile/login", json={"email": "pending@example.com", "password": "correct horse battery staple"})
        assert response.status == 200


@pytest.mark.asyncio
async def test_owner_is_unblockable_and_never_loses_access(monkeypatch, tmp_path):
    async with _api(monkeypatch, tmp_path) as (client, sessions, _sent):
        async with sessions() as db:
            owner = Parent(email="krisriskrisris@gmail.com", password_hash="hash", email_verified=True, account_status=BLOCKED)
            db.add(owner)
            await db.flush()
            child = Child(parent_id=owner.id, display_name="Owner child", age_years=7, target_language="en", native_language="ru")
            db.add(child)
            await db.commit()
            owner_id = owner.id
            with pytest.raises(PermissionError, match="OWNER_UNBLOCKABLE"):
                set_account_status(owner, BLOCKED, actor="test")

        headers = {"Authorization": f"Bearer {issue_session_token(owner_id)}"}
        response = await client.get("/api/mobile/bootstrap", headers=headers)
        assert response.status == 200
        assert (await response.json())["parent"]["is_owner"] is True


@pytest.mark.asyncio
async def test_content_studio_persists_registration_mode_and_controls_only_non_owner_access(monkeypatch, tmp_path):
    """The existing Clients view is the only access-control surface."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    client: TestClient | None = None
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        # platform_settings resolves its persistent directory on import.  Keep
        # this test's admin toggle entirely inside pytest's temporary storage.
        monkeypatch.setattr(platform_settings, "SETTINGS_DIR", tmp_path / "platform-settings")
        monkeypatch.setattr(settings, "content_studio_enabled", True)
        monkeypatch.setattr(settings, "content_studio_token", "owner-secret")
        monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")
        monkeypatch.setattr(content_studio, "_SessionLocal", sessions)
        async with sessions() as db:
            tester = Parent(email="visible@example.com", display_name="Visible tester", password_hash="hash", email_verified=True)
            owner = Parent(email="krisriskrisris@gmail.com", display_name="Owner", password_hash="hash", email_verified=True)
            db.add_all([tester, owner])
            await db.commit()
            tester_id = tester.id
            owner_id = owner.id

        app = web.Application()
        content_studio.register_content_studio_routes(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        headers = {"Authorization": "Bearer owner-secret"}

        initial = await client.get("/api/studio/admin/access-settings", headers=headers)
        assert initial.status == 200
        assert (await initial.json())["new_user_access_mode"] == "AUTOMATIC"
        manual = await client.put(
            "/api/studio/admin/access-settings", headers=headers,
            json={"new_user_access_mode": "MANUAL"},
        )
        assert manual.status == 200
        assert (await manual.json())["new_user_access_mode"] == "MANUAL"

        clients = await client.get("/api/studio/admin/clients", headers=headers)
        rows = (await clients.json())["clients"]
        visible = next(row for row in rows if row["id"] == tester_id)
        assert visible["account_status"] == ACTIVE
        assert "last_active_at" in visible

        blocked = await client.post(
            f"/api/studio/admin/clients/{tester_id}/access", headers=headers,
            json={"status": "BLOCKED"},
        )
        assert blocked.status == 200
        assert (await blocked.json())["account_status"] == BLOCKED
        unblocked = await client.post(
            f"/api/studio/admin/clients/{tester_id}/access", headers=headers,
            json={"status": "ACTIVE"},
        )
        assert unblocked.status == 200
        assert (await unblocked.json())["account_status"] == ACTIVE

        owner_block = await client.post(
            f"/api/studio/admin/clients/{owner_id}/access", headers=headers,
            json={"status": "BLOCKED"},
        )
        assert owner_block.status == 403
    finally:
        if client is not None:
            await client.close()
        await engine.dispose()
