from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Base, Child, LessonEntitlement, LessonSession, Parent
from app.services import lesson_access
from app.services.mobile_tokens import issue_session_token
from app.webapp import mobile_api


def _runtime_lesson() -> dict:
    """A one-step published lesson keeps the API contract test independent of media."""
    return {
        "lesson_id": "demo_001",
        "course_id": "conversation",
        "title": "Progress contract test",
        "revision": 1,
        "publication_status": "PUBLISHED",
        "slides": [{"slide_id": "step_1", "order": 1, "type": "passive"}],
        "required_phrases": [],
    }


@asynccontextmanager
async def _owner_api(monkeypatch, tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    client: TestClient | None = None
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as db:
            parent = Parent(
                email="krisriskrisris@gmail.com",
                password_hash="owner-test-password-hash",
                email_verified=True,
            )
            db.add(parent)
            await db.flush()
            child = Child(
                parent_id=parent.id,
                display_name="Test",
                target_language="en",
                native_language="ru",
                language_level="PRE_A1",
            )
            db.add(child)
            await db.commit()
            parent_id, child_id = parent.id, child.id

        monkeypatch.setattr(mobile_api, "SessionLocal", sessions)
        monkeypatch.setattr(lesson_access, "SessionLocal", sessions)
        monkeypatch.setattr(mobile_api, "_load_mobile_lesson", lambda _lesson_id: _runtime_lesson())
        monkeypatch.setattr(mobile_api, "ensure_runtime_storage_capacity", lambda: {
            "before": 512 * 1024 * 1024,
            "after": 512 * 1024 * 1024,
            "target": 64 * 1024 * 1024,
            "minimum": 4 * 1024 * 1024,
            "target_met": True,
            "ready": True,
        })
        monkeypatch.setattr(settings, "mobile_auth_secret", "progress-contract-secret-long-enough")
        monkeypatch.setattr(settings, "storage_root", tmp_path / "storage")

        app = web.Application()
        mobile_api.register_mobile_routes(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        yield client, sessions, parent_id, child_id
    finally:
        if client is not None:
            await client.close()
        await engine.dispose()


async def _start_owner_session(client: TestClient, parent_id: int, child_id: int) -> tuple[dict, dict[str, str]]:
    headers = {"Authorization": f"Bearer {issue_session_token(parent_id)}"}
    response = await client.post(
        "/api/mobile/session/start",
        headers=headers,
        json={"child_id": child_id, "lesson_id": "demo_001"},
    )
    assert response.status == 200
    payload = await response.json()
    assert payload["lesson_id"] == "demo_001"
    assert payload["lesson_version"]
    return payload, headers


@pytest.mark.asyncio
async def test_mobile_progress_endpoint_is_registered_and_saves_step(monkeypatch, tmp_path):
    async with _owner_api(monkeypatch, tmp_path) as (client, _sessions, parent_id, child_id):
        started, headers = await _start_owner_session(client, parent_id, child_id)
        response = await client.post(
            f"/api/mobile/session/{started['session_id']}/progress",
            headers=headers,
            json={
                "current_step_id": "step_1",
                "current_step": 0,
                "lesson_version": started["lesson_version"],
            },
        )
        assert response.status == 200
        assert (await response.json()) == {
            "ok": True,
            "session_id": started["session_id"],
            "current_step": 0,
            "current_step_id": "step_1",
            "lesson_version": started["lesson_version"],
        }


@pytest.mark.asyncio
async def test_child_progress_endpoint_returns_real_child_data(monkeypatch, tmp_path):
    async with _owner_api(monkeypatch, tmp_path) as (client, _sessions, parent_id, child_id):
        _started, headers = await _start_owner_session(client, parent_id, child_id)
        response = await client.get(f"/api/mobile/child/{child_id}/progress", headers=headers)
        assert response.status == 200
        payload = await response.json()
        assert payload["ok"] is True
        assert payload["child"]["id"] == child_id
        assert payload["child"]["name"] == "Test"
        assert "summary" in payload and "courses" in payload


@pytest.mark.asyncio
async def test_owner_start_progress_and_completion_use_a_real_persisted_session(monkeypatch, tmp_path):
    async with _owner_api(monkeypatch, tmp_path) as (client, sessions, parent_id, child_id):
        started, headers = await _start_owner_session(client, parent_id, child_id)
        session_id = started["session_id"]
        assert isinstance(session_id, int) and session_id > 0

        progressed = await client.post(
            f"/api/mobile/session/{session_id}/progress",
            headers=headers,
            json={"current_step_id": "step_1", "lesson_version": started["lesson_version"]},
        )
        assert progressed.status == 200
        completed = await client.post(f"/api/mobile/session/{session_id}/complete", headers=headers, json={})
        assert completed.status == 200

        async with sessions() as db:
            session = await db.get(LessonSession, session_id)
            entitlement = await db.scalar(select(LessonEntitlement).where(
                LessonEntitlement.child_id == child_id,
                LessonEntitlement.lesson_id == "demo_001",
            ))
        assert session is not None and session.status == "COMPLETED"
        assert entitlement is not None
        assert entitlement.source == "OWNER_ACCESS"
        assert entitlement.completed_runs == 1
        assert entitlement.max_completed_runs == 999999


@pytest.mark.asyncio
async def test_invalid_session_or_child_is_a_scoped_4xx_not_a_missing_valid_route(monkeypatch, tmp_path):
    async with _owner_api(monkeypatch, tmp_path) as (client, _sessions, parent_id, _child_id):
        headers = {"Authorization": f"Bearer {issue_session_token(parent_id)}"}
        no_session = await client.post(
            "/api/mobile/session/999999/progress",
            headers=headers,
            json={"current_step_id": "step_1", "lesson_version": "unknown"},
        )
        assert no_session.status == 404
        no_child = await client.get("/api/mobile/child/999999/progress", headers=headers)
        assert no_child.status in {403, 404}


def test_owner_client_never_mints_a_fake_session_id():
    source = (Path(__file__).resolve().parents[2] / "DOME_MOBILE_77/src/screens/LessonPlayer.tsx").read_text(encoding="utf-8")
    assert "OWNER_SESSION_START_CLIENT_BYPASS" not in source
    assert "setSession(Date.now())" not in source
