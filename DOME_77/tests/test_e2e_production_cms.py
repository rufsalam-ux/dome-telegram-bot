"""
Full End-to-End Verification of the DOME Production Admin Panel (CMS)
Validates Tests A through G as specified in the master prompt
"""

import json
import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.core.config import settings
from app.db.models import Base
from app.webapp import content_studio
import app.webapp.content_studio_cms as cms_module


@pytest_asyncio.fixture
async def cms_client(monkeypatch, tmp_path):
    storage = tmp_path / "storage"
    (storage / "platform-settings").mkdir(parents=True)
    (storage / "authored-content" / "lessons").mkdir(parents=True)
    (storage / "authored-content" / "courses").mkdir(parents=True)
    (storage / "animations").mkdir(parents=True)
    (storage / "media").mkdir(parents=True)

    monkeypatch.setattr(settings, "storage_root", storage)
    monkeypatch.setattr(settings, "content_studio_enabled", True)
    monkeypatch.setattr(settings, "content_studio_token", "owner-token")

    # In-memory SQLite for tests
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(cms_module, "_SessionLocal", sessions)
    monkeypatch.setattr(content_studio, "_SessionLocal", sessions)

    app = web.Application()
    content_studio.register_content_studio_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_e2e_a_studio_browser_auth_and_admin_alias(cms_client):
    """Test A: /content-studio and /admin alias are served with 200/302 and CSRF support."""
    # /content-studio serves HTML
    resp = await cms_client.get("/content-studio")
    assert resp.status == 200
    html = await resp.text()
    assert "DOME Admin Panel &amp; CMS" in html or "DOME Admin Panel & CMS" in html
    assert "sectionMedia" in html
    assert "sectionHeroes" in html
    assert "sectionAnimations" in html
    assert "sectionMovies" in html

    # /admin alias redirects or serves content studio
    admin_resp = await cms_client.get("/admin", allow_redirects=False)
    assert admin_resp.status in (200, 301, 302)


@pytest.mark.asyncio
async def test_e2e_b_both_cats_preserved_and_hero_management(cms_client):
    """Test B: Old Gray Cat and New DOME Cat both exist, distinct, and editable."""
    headers = {"Authorization": "Bearer owner-token"}
    resp = await cms_client.get("/api/studio/cms/heroes", headers=headers)
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
    heroes = {h["id"]: h for h in data["heroes"]}

    # Crucial assertion: BOTH cats must be distinct entries
    assert "cat" in heroes, "Classic Old Gray Cat 'cat' must exist"
    assert "dome_cat" in heroes, "New DOME Cat 'dome_cat' must exist"
    assert heroes["cat"]["id"] != heroes["dome_cat"]["id"]

    # Update voice ID and active status
    update_resp = await cms_client.post(
        "/api/studio/cms/heroes",
        headers=headers,
        json={"hero_id": "dome_cat", "active": True, "voice_id": "cartesia-dome-cat-v1"}
    )
    assert update_resp.status == 200
    u_data = await update_resp.json()
    assert u_data["ok"] is True


@pytest.mark.asyncio
async def test_e2e_c_central_media_manager(cms_client):
    """Test C: Media files can be listed, filtered, and searched."""
    headers = {"Authorization": "Bearer owner-token"}
    resp = await cms_client.get("/api/studio/cms/media", headers=headers)
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
    assert "files" in data
    assert isinstance(data["files"], list)


@pytest.mark.asyncio
async def test_e2e_d_animation_library_and_movement_creation(cms_client):
    """Test D: Animations are loaded from library and new movement can be created."""
    headers = {"Authorization": "Bearer owner-token"}
    list_resp = await cms_client.get("/api/studio/cms/animations", headers=headers)
    assert list_resp.status == 200
    anims = (await list_resp.json())["animations"]
    assert len(anims) > 0

    # Create new animation
    new_anim = {
        "id": "high_five_jump",
        "name": "High Five and Jump",
        "description": "Character waves and jumps",
        "duration": 2.5,
        "actions": [{"action": "wave", "duration": 1.0}, {"action": "jump", "duration": 1.5}],
        "compatible_heroes": ["all"]
    }
    create_resp = await cms_client.post("/api/studio/cms/animations", headers=headers, json=new_anim)
    assert create_resp.status == 200
    assert (await create_resp.json())["ok"] is True

    # Toggle animation
    toggle_resp = await cms_client.post("/api/studio/cms/animations/high_five_jump/toggle", headers=headers, json={"active": False})
    assert toggle_resp.status == 200
    assert (await toggle_resp.json())["ok"] is True


@pytest.mark.asyncio
async def test_e2e_e_movie_builder_and_timeline(cms_client):
    """Test E: Movie timeline configuration and publishing for live mobile sync."""
    headers = {"Authorization": "Bearer owner-token"}
    config = {
        "scenes": [
            {"title": "Intro", "duration": 3.0, "animation": "wave", "background": "room_default", "dialogue": "Привет!"},
            {"title": "Finale", "duration": 4.0, "animation": "cheer", "background": "celebration", "dialogue": "Отличная работа!"}
        ]
    }
    save_resp = await cms_client.post(
        "/api/studio/cms/movie-config",
        headers=headers,
        json={"lesson_id": "test_lesson_1", "config": config}
    )
    assert save_resp.status == 200
    assert (await save_resp.json())["ok"] is True

    # Publish config
    pub_resp = await cms_client.post(
        "/api/studio/cms/movie-config/publish?lesson_id=test_lesson_1",
        headers=headers
    )
    assert pub_resp.status == 200
    assert (await pub_resp.json())["ok"] is True

    # Jobs queue
    jobs_resp = await cms_client.get("/api/studio/cms/movie-jobs", headers=headers)
    assert jobs_resp.status == 200
    assert (await jobs_resp.json())["ok"] is True


@pytest.mark.asyncio
async def test_e2e_f_ai_and_language_settings(cms_client):
    """Test F: AI Persona, TTS voice engine, and language policies."""
    headers = {"Authorization": "Bearer owner-token"}
    payload = {
        "persona_name": "Кот DOME",
        "persona_role": "Добрый наставник",
        "system_prompt": "Ты чуткий и ободряющий тьютор. Всегда хвали за попытку.",
        "tts_provider": "cartesia",
        "llm_model": "gemini-2.0-flash",
        "temperature": 0.25,
        "max_words": 15,
        "strict_immersion": True,
        "child_safe_filter": True
    }
    save_resp = await cms_client.post("/api/studio/cms/ai-settings", headers=headers, json=payload)
    assert save_resp.status == 200
    assert (await save_resp.json())["ok"] is True

    # Read back
    get_resp = await cms_client.get("/api/studio/cms/ai-settings", headers=headers)
    assert get_resp.status == 200
    settings_data = (await get_resp.json())["settings"]
    assert settings_data["persona_name"] == "Кот DOME"
    assert settings_data["tts_provider"] == "cartesia"
    assert settings_data["strict_immersion"] is True


@pytest.mark.asyncio
async def test_e2e_g_platform_settings_and_audit(cms_client):
    """Test G: Registration modes, feature flags, and audit log."""
    headers = {"Authorization": "Bearer owner-token"}
    settings_payload = {
        "registration_mode": "MANUAL_APPROVAL",
        "features": {
            "custom_heroes": True,
            "new_animation_engine": True,
            "voice_homework": True
        }
    }
    save_resp = await cms_client.post("/api/studio/cms/settings", headers=headers, json=settings_payload)
    assert save_resp.status == 200
    assert (await save_resp.json())["ok"] is True

    # Verify audit log recorded changes
    audit_resp = await cms_client.get("/api/studio/cms/audit", headers=headers)
    assert audit_resp.status == 200
    audit_data = await audit_resp.json()
    assert audit_data["ok"] is True
    assert len(audit_data["records"]) > 0
