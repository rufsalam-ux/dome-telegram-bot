import json
from pathlib import Path

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Base, Child, LessonEntitlement, Parent
from app.services import lesson_access
from app.services.content_authoring_assistant import deterministic_proposal, sanitize_proposal
from app.services.lesson_loader import LessonConfigurationError, _runtime_slides, load_lesson
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
        assert "missing media media/picture.png" in " ".join((await response.json())["validation_errors"])
        assert not (storage / "authored-content/lessons/studio_001/lesson.json").exists()

        first = FormData()
        first.add_field("file", b"one", filename="picture.png", content_type="image/png")
        result1 = await (await client.post("/api/studio/lessons/studio_001/media", headers=headers, data=first)).json()
        second = FormData()
        second.add_field("file", b"two", filename="picture.png", content_type="image/png")
        result2 = await (await client.post("/api/studio/lessons/studio_001/media", headers=headers, data=second)).json()
        assert result1["path"] != result2["path"]
        assert (storage / "authored-content/lessons/studio_001" / result1["path"]).read_bytes() == b"one"

        lesson["slides"][0]["media_sequence"][0]["src"] = result1["path"]
        response = await client.put("/api/studio/lessons/studio_001", headers=headers, json={"lesson": lesson})
        assert (await response.json())["validation_errors"] == []

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
async def test_content_studio_reorder_archive_and_feature_gate(monkeypatch, tmp_path):
    storage = tmp_path / "storage"
    content = tmp_path / "content"
    (content / "lessons").mkdir(parents=True)
    (content / "courses").mkdir(parents=True)
    monkeypatch.setattr(settings, "storage_root", storage)
    monkeypatch.setattr(settings, "content_root", content)
    monkeypatch.setattr(settings, "content_studio_token", "owner-secret")
    monkeypatch.setattr(settings, "content_studio_enabled", True)
    app = web.Application()
    content_studio.register_content_studio_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    headers = {"Authorization": "Bearer owner-secret"}
    try:
        for lesson_id in ("order_a", "order_b"):
            created = await client.post("/api/studio/lessons", headers=headers, json={"lesson_id": lesson_id, "title": lesson_id})
            lesson = (await created.json())["lesson"]
            lesson["slides"] = [_slide()]
            lesson["slides"][0].pop("media_sequence")
            await client.put(f"/api/studio/lessons/{lesson_id}", headers=headers, json={"lesson": lesson})
            assert (await client.post(f"/api/studio/lessons/{lesson_id}/publish", headers=headers)).status == 200
        reordered = await client.post("/api/studio/lessons/reorder", headers=headers, json={"orders": {"order_b": 1, "order_a": 2}})
        assert reordered.status == 200
        assert (storage / "authored-content/lessons/order_b/draft.json").exists()
        await client.post("/api/studio/lessons/order_b/publish", headers=headers)
        archived = await client.post("/api/studio/lessons/order_a/archive", headers=headers)
        assert archived.status == 200
        assert (await archived.json())["summary"]["publication_status"] == "ARCHIVED"
        with pytest.raises(LessonConfigurationError):
            load_lesson("order_a")
        assert load_lesson("order_b")["order"] == 1
        monkeypatch.setattr(settings, "content_studio_enabled", False)
        assert (await client.get("/api/studio/status", headers=headers)).status == 503
        assert load_lesson("order_b")["lesson_id"] == "order_b"
    finally:
        await client.close()


def test_demo_001_studio_migration_preserves_runtime_step_sequence():
    source = json.loads((Path(__file__).resolve().parents[1] / "content/lessons/demo_001/lesson.json").read_text("utf-8"))
    before = _runtime_slides(source["slides"], "demo_001", content_engine="")
    migrated = json.loads(json.dumps(source))
    migrated.update({
        "engine": "content_v1", "schema_version": "2.1", "status": "published", "active": True,
        "max_completed_runs": 2, "expires_after_months": 10,
    })
    for order, slide in enumerate(migrated["slides"], 1):
        slide["order"] = order
    assert content_studio.validate_content_lesson(migrated) == []
    after = _runtime_slides(migrated["slides"], "demo_001", content_engine="content_v1")
    assert [slide["slide_id"] for slide in after] == [slide["slide_id"] for slide in before]
    assert [slide["type"] for slide in after] == [slide["type"] for slide in before]


def test_content_studio_ui_has_native_authoring_lifecycle_and_drag_reorder():
    html = (Path(__file__).resolve().parents[1] / "app/webapp/static/content_studio.html").read_text("utf-8")
    for marker in ("data-step-handle", "text/dome-lesson", "archiveLesson", "preSlideVideo", "required_movie_phrase", "puzzle"):
        assert marker in html


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
    assert "listLessons(child.id)" in app
    assert "require('./LessonPlayer')" in app and "<LessonPlayer lessonId={activeLessonId}" in app
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


def test_stable_task_templates_pre_video_and_movie_phrase_validate():
    lesson = {
        "schema_version": "2.1", "engine": "content_v1", "lesson_id": "templates_001",
        "course_id": "conversation", "title": "Templates", "order": 1,
        "max_completed_runs": 2, "expires_after_months": 10,
        "slides": [
            {"slide_id": "drag", "order": 1, "type": "drag_drop", "prompt": "Pack", "items": [{"id": "coat"}, {"id": "hat"}], "targets": [{"id": "bag"}, {"id": "box"}]},
            {"slide_id": "memory", "order": 2, "type": "memory", "prompt": "Pairs", "pairs": [["cat", "кот"], ["dog", "собака"]]},
            {"slide_id": "puzzle", "order": 3, "type": "puzzle", "prompt": "Puzzle", "pieces": 6, "image_file": "media/puzzle.png"},
            {"slide_id": "movie", "order": 4, "type": "required_movie_phrase", "prompt": "Say it", "requiredForMovie": True, "moviePhraseId": "movie_line", "allow_skip": False},
        ],
    }
    assert content_studio.validate_content_lesson(lesson) == []
    lesson["slides"][2]["preSlideVideo"] = {"enabled": True, "uri": "media/intro.mp4", "skippable": True, "showPolicy": "once_per_attempt"}
    assert content_studio.validate_content_lesson(lesson) == []
    lesson["slides"][2]["preSlideVideo"] = {}
    assert content_studio.validate_content_lesson(lesson) == []
    lesson["slides"][3].pop("moviePhraseId")
    assert "requiredForMovie needs moviePhraseId" in " ".join(content_studio.validate_content_lesson(lesson))


def test_founder_friendly_steps_extract_to_existing_runtime_without_code_changes():
    from app.services.authored_content import authored_steps, validate_content_lesson

    lesson = {
        "engine": "content_v1", "schema_version": "3.0", "status": "published", "active": True,
        "lesson_id": "founder_001", "course_id": "conversation", "title": "Editable", "order": 7,
        "max_completed_runs": 2, "expires_after_months": 10,
        "languages": {"target": "en", "native": "ru"},
        "steps": [
            {"id": "hello", "type": "ai_dialogue", "target_phrase": "Hello!", "native_explanation": "Поздоровайся.", "ai_instruction": "Ask one short question."},
            {"id": "clip", "type": "video", "src": "videos/hello.mp4", "autoplay": True, "autoContinue": True},
            {"id": "answer", "type": "voice_answer", "target_phrase": "What did you see?", "controls": {"answer": {"enabled": True, "required": True}, "hint": {"enabled": True}}},
        ],
    }
    assert validate_content_lesson(lesson) == []
    steps = authored_steps(lesson)
    assert [step["slide_id"] for step in steps] == ["hello", "clip", "answer"]
    assert [step["order"] for step in steps] == [1, 2, 3]
    assert steps[0]["type"] == "dialogue"
    assert steps[1]["video_file"] == "videos/hello.mp4"
    assert steps[1]["auto_continue"] is True
    assert steps[2]["answer_mode"] == "required_voice"


def test_founder_friendly_validation_reports_language_prompt_and_next_step_errors():
    from app.services.authored_content import validate_content_lesson

    lesson = {
        "engine": "content_v1", "schema_version": "3.0", "status": "published", "active": True,
        "lesson_id": "broken_001", "course_id": "conversation", "title": "Broken", "order": 8,
        "max_completed_runs": 2, "expires_after_months": 10,
        "languages": {"target": "english", "native": ""},
        "steps": [{"id": "answer", "type": "voice_answer", "next_step_id": "missing"}],
    }
    errors = validate_content_lesson(lesson)
    assert any("needs ai_instruction or target_phrase" in error for error in errors)
    assert any("next step missing does not exist" in error for error in errors)
    assert any("languages.target" in error for error in errors)
    assert any("languages.native" in error for error in errors)


def test_demo_001_is_a_published_editable_content_lesson_without_runtime_loss():
    from app.services.authored_content import validate_content_lesson
    from app.services.lesson_loader import load_lesson

    source_path = settings.content_root / "lessons" / "demo_001" / "lesson.json"
    source = json.loads(source_path.read_text("utf-8"))
    assert source["engine"] == "content_v1"
    assert source["schema_version"] == "3.0"
    assert source["status"] == "published"
    assert validate_content_lesson(source) == []
    runtime = load_lesson("demo_001")
    assert [step["slide_id"] for step in runtime["slides"]] == [step["slide_id"] for step in source["slides"]]
    assert [step["type"] for step in runtime["slides"]] == [step["type"] for step in source["slides"]]


def test_authoring_assistant_only_returns_editable_declarative_templates():
    proposal = deterministic_proposal("Make a memory game from these pictures", ["media/cat.png", "media/dog.png"])
    assert proposal["type"] == "memory" and len(proposal["pairs"]) >= 2
    clean = sanitize_proposal({**proposal, "javascript": "location.reload()", "type": "memory"}, proposal)
    assert "javascript" not in clean
    assert not any(word in json.dumps(clean) for word in ("<script", "function()"))


@pytest.mark.asyncio
async def test_media_library_deduplicates_renames_replaces_and_deletes_only_unused(monkeypatch, tmp_path):
    storage = tmp_path / "storage"
    content = tmp_path / "content"
    (content / "lessons").mkdir(parents=True)
    (content / "courses").mkdir(parents=True)
    monkeypatch.setattr(settings, "storage_root", storage)
    monkeypatch.setattr(settings, "content_root", content)
    monkeypatch.setattr(settings, "content_studio_token", "owner-secret")
    monkeypatch.setattr(settings, "openai_api_key", "")
    app = web.Application(client_max_size=10 * 1024 * 1024)
    content_studio.register_content_studio_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    headers = {"Authorization": "Bearer owner-secret"}
    try:
        created = await client.post("/api/studio/lessons", headers=headers, json={"lesson_id": "media_001", "title": "Media"})
        lesson = (await created.json())["lesson"]
        lesson["slides"] = [{"slide_id": "one", "order": 1, "type": "info", "prompt": "Hello"}]
        await client.put("/api/studio/lessons/media_001", headers=headers, json={"lesson": lesson})

        first = FormData(); first.add_field("file", b"same-image", filename="cat.png", content_type="image/png")
        first_response = await client.post("/api/studio/lessons/media_001/media", headers=headers, data=first)
        first_asset = await first_response.json()
        duplicate = FormData(); duplicate.add_field("file", b"same-image", filename="renamed-cat.png", content_type="image/png")
        duplicate_response = await client.post("/api/studio/lessons/media_001/media", headers=headers, data=duplicate)
        duplicate_asset = await duplicate_response.json()
        assert first_response.status == 201 and duplicate_response.status == 200
        assert duplicate_asset["reused"] is True and duplicate_asset["path"] == first_asset["path"]
        assert len(list((storage / "authored-content/lessons/media_001/media").iterdir())) == 1

        renamed = await client.patch(f'/api/studio/lessons/media_001/media/{first_asset["name"]}', headers=headers, json={"display_name": "Кот"})
        assert (await renamed.json())["display_name"] == "Кот"
        replacement = FormData(); replacement.add_field("file", b"new-image", filename="cat-v2.png", content_type="image/png")
        replacement_response = await client.post(f'/api/studio/lessons/media_001/media/{first_asset["name"]}/replace', headers=headers, data=replacement)
        replacement_asset = await replacement_response.json()
        assert replacement_asset["path"] != first_asset["path"] and replacement_asset["replaces"] == first_asset["path"]

        lesson["slides"][0]["media_sequence"] = [{"id": "visual", "type": "image", "src": first_asset["path"]}]
        await client.put("/api/studio/lessons/media_001", headers=headers, json={"lesson": lesson})
        used_delete = await client.delete(f'/api/studio/lessons/media_001/media/{first_asset["name"]}', headers=headers)
        assert used_delete.status == 409
        unused_delete = await client.delete(f'/api/studio/lessons/media_001/media/{replacement_asset["name"]}', headers=headers)
        assert unused_delete.status == 200

        assisted = await client.post("/api/studio/assist/task", headers=headers, json={"lesson_id": "media_001", "instruction": "Make a 6-piece puzzle", "assets": [first_asset["path"]]})
        proposal = (await assisted.json())["proposal"]
        assert proposal["type"] == "puzzle" and proposal["pieces"] == 6
    finally:
        await client.close()
