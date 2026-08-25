import json
from pathlib import Path

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Base, Child, LessonEntitlement, Parent
from app.services import lesson_access
from app.services.lesson_loader import load_lesson
from app.services.mobile_tokens import issue_session_token
from app.webapp import content_studio, mobile_api


def _slide(slide_id="step_01"):
    return {
        "slide_id": slide_id,
        "order": 1,
        "type": "passive",
        "prompt": "Hello!",
        "requiredForMovie": False,
        "max_attempts": 3,
        "media_sequence": [{"id": "visual", "type": "image", "src": "media/picture.png"}],
    }


async def _memory_database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, sessions


@pytest.mark.asyncio
async def test_content_studio_draft_publish_versioned_media_and_rollback(monkeypatch, tmp_path):
    storage = tmp_path / "storage"
    content = tmp_path / "content"
    (content / "lessons").mkdir(parents=True)
    (content / "courses").mkdir(parents=True)
    monkeypatch.setattr(settings, "storage_root", storage)
    monkeypatch.setattr(settings, "content_root", content)
    monkeypatch.setattr(settings, "content_studio_token", "owner-secret")

    app = web.Application(client_max_size=10 * 1024 * 1024)
    content_studio.register_content_studio_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    headers = {"Authorization": "Bearer owner-secret"}
    try:
        response = await client.post("/api/studio/lessons", headers=headers, json={
            "lesson_id": "studio_001", "title": "Studio lesson", "course_id": "conversation",
        })
        assert response.status == 201

        lesson = (await response.json())["lesson"]
        lesson["slides"] = [_slide()]
        response = await client.put("/api/studio/lessons/studio_001", headers=headers, json={"lesson": lesson})
        assert response.status == 200
        assert (await response.json())["validation_errors"] == []
        assert not (storage / "authored-content/lessons/studio_001/lesson.json").exists()

        first = FormData()
        first.add_field("file", b"one", filename="picture.png", content_type="image/png")
        result1 = await (await client.post("/api/studio/lessons/studio_001/media", headers=headers, data=first)).json()
        second = FormData()
        second.add_field("file", b"two", filename="picture.png", content_type="image/png")
        result2 = await (await client.post("/api/studio/lessons/studio_001/media", headers=headers, data=second)).json()
        assert result1["path"] != result2["path"]
        assert (storage / "authored-content/lessons/studio_001" / result1["path"]).read_bytes() == b"one"

        response = await client.post("/api/studio/lessons/studio_001/publish", headers=headers)
        assert response.status == 200
        published = (await response.json())["lesson"]
        assert published["status"] == "published" and published["active"] is True
        assert not (storage / "authored-content/lessons/studio_001/draft.json").exists()

        published["title"] = "Second title"
        await client.put("/api/studio/lessons/studio_001", headers=headers, json={"lesson": published})
        await client.post("/api/studio/lessons/studio_001/publish", headers=headers)
        detail = await (await client.get("/api/studio/lessons/studio_001", headers=headers)).json()
        assert detail["versions"]
        version = detail["versions"][0]
        response = await client.post("/api/studio/lessons/studio_001/rollback", headers=headers, json={"version": version})
        assert response.status == 200
        assert (await response.json())["lesson"]["title"] == "Studio lesson"

        unauthorized = await client.get("/api/studio/lessons")
        assert unauthorized.status == 401
        assert (storage / "authored-content/studio-audit.jsonl").exists()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_mobile_catalog_discovers_new_published_lesson_without_apk_rebuild(monkeypatch, tmp_path):
    storage = tmp_path / "storage"
    content = tmp_path / "content"
    (content / "lessons").mkdir(parents=True)
    (content / "courses").mkdir(parents=True)
    (content / "courses/conversation.json").write_text(json.dumps({
        "schema_version": "1.0", "course_id": "conversation", "title": "Conversation",
        "active": True, "lesson_ids": [],
    }), "utf-8")
    root = storage / "authored-content/lessons/studio_002"
    root.mkdir(parents=True)
    (root / "lesson.json").write_text(json.dumps({
        "schema_version": "2.1", "engine": "content_v1", "lesson_id": "studio_002",
        "course_id": "conversation", "title": "Published from Studio", "description": "No rebuild",
        "order": 20, "active": True, "status": "published", "max_completed_runs": 2,
        "expires_after_months": 10, "slides": [_slide()],
    }), "utf-8")
    monkeypatch.setattr(settings, "storage_root", storage)
    monkeypatch.setattr(settings, "content_root", content)
    monkeypatch.setattr(settings, "mobile_auth_secret", "catalog-test-secret-that-is-long-enough")

    engine, sessions = await _memory_database()
    try:
        async with sessions() as db:
            parent = Parent(display_name="Owner", email="owner@example.com", email_verified=True)
            db.add(parent)
            await db.flush()
            child = Child(parent_id=parent.id, display_name="Child", target_language="en", native_language="ru")
            db.add(child)
            await db.flush()
            db.add(LessonEntitlement(
                child_id=child.id, lesson_id="studio_002", course_id="conversation", source="PURCHASE",
                status="ACTIVE", max_completed_runs=2, completed_runs=0,
            ))
            await db.commit()
            parent_id, child_id = parent.id, child.id
        monkeypatch.setattr(mobile_api, "SessionLocal", sessions)
        monkeypatch.setattr(lesson_access, "SessionLocal", sessions)
        app = web.Application()
        mobile_api.register_mobile_routes(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            token = issue_session_token(parent_id)
            response = await client.get(f"/api/mobile/child/{child_id}/lessons", headers={"Authorization": f"Bearer {token}"})
            assert response.status == 200
            lessons = (await response.json())["lessons"]
            assert [(item["lesson_id"], item["available"]) for item in lessons] == [("studio_002", True)]
            assert lessons[0]["title"] == "Published from Studio"
        finally:
            await client.close()
    finally:
        await engine.dispose()


def test_release_mobile_runtime_uses_server_selected_lesson_and_production_api():
    root = Path(__file__).resolve().parents[2] / "DOME_MOBILE_77"
    player = (root / "src/screens/LessonPlayer.tsx").read_text("utf-8")
    app = (root / "src/screens/RootApp.tsx").read_text("utf-8")
    env = (root / ".env.example").read_text("utf-8")
    assert "getLesson(lessonId)" in player and "startSession(child.id,lessonId)" in player
    assert "listLessons(child.id)" in app and "<LazyLessonPlayer lessonId={activeLessonId}" in app
    assert "dome-telegram-bot-production.up.railway.app" in env


def test_studio_lesson_orders_are_not_filtered_by_demo_001_legacy_cut(monkeypatch, tmp_path):
    storage = tmp_path / "storage"
    content = tmp_path / "content"
    lesson_root = storage / "authored-content/lessons/studio_long"
    lesson_root.mkdir(parents=True)
    (content / "lessons").mkdir(parents=True)
    slides = [{**_slide(f"step_{index:02d}"), "order": index} for index in range(1, 41)]
    (lesson_root / "lesson.json").write_text(json.dumps({
        "schema_version": "2.1", "engine": "content_v1", "lesson_id": "studio_long",
        "course_id": "conversation", "title": "Long", "order": 1, "active": True,
        "status": "published", "max_completed_runs": 2, "expires_after_months": 10, "slides": slides,
    }), "utf-8")
    monkeypatch.setattr(settings, "storage_root", storage)
    monkeypatch.setattr(settings, "content_root", content)
    loaded = load_lesson("studio_long")
    assert len(loaded["slides"]) == 40
    assert {slide["order"] for slide in loaded["slides"]} >= {2, 25, 39}
