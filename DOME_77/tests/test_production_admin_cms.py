import json
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from app.core.config import settings
from app.webapp import content_studio


@pytest.mark.asyncio
async def test_cms_dashboard_metrics(monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.db.models import Base
    import app.webapp.content_studio_cms as cms_module

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(cms_module, "_SessionLocal", sessions)

    monkeypatch.setattr(settings, "content_studio_enabled", True)
    monkeypatch.setattr(settings, "content_studio_token", "owner-token")
    app = web.Application()
    content_studio.register_content_studio_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        headers = {"Authorization": "Bearer owner-token"}
        resp = await client.get("/api/studio/cms/dashboard", headers=headers)
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert "total_users" in data
        assert "active_children" in data
        assert "courses_count" in data
        assert "published_lessons" in data
        assert "movie_jobs_total" in data
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cms_heroes_preserves_both_cats(monkeypatch):
    monkeypatch.setattr(settings, "content_studio_enabled", True)
    monkeypatch.setattr(settings, "content_studio_token", "owner-token")
    app = web.Application()
    content_studio.register_content_studio_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        headers = {"Authorization": "Bearer owner-token"}
        resp = await client.get("/api/studio/cms/heroes", headers=headers)
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        heroes = data["heroes"]
        hero_ids = {h["id"] for h in heroes}
        # Both cats MUST be preserved
        assert "cat" in hero_ids, "Classic Old Gray Cat must exist"
        assert "dome_cat" in hero_ids, "New DOME Cat in blue hoodie must exist"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cms_animations_library_and_create_movement(monkeypatch, tmp_path):
    storage = tmp_path / "storage"
    storage.mkdir(parents=True)
    monkeypatch.setattr(settings, "storage_root", storage)
    monkeypatch.setattr(settings, "content_studio_enabled", True)
    monkeypatch.setattr(settings, "content_studio_token", "owner-token")
    app = web.Application()
    content_studio.register_content_studio_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        headers = {"Authorization": "Bearer owner-token"}

        # 1. List animations
        resp = await client.get("/api/studio/cms/animations", headers=headers)
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert len(data["animations"]) > 0

        # 2. Add new movement
        new_movement = {
            "id": "spin_jump",
            "name": "Spin and Jump",
            "description": "Character rotates and jumps",
            "duration": 3.0,
            "actions": [
                {"action": "turn", "duration": 1.0},
                {"action": "jump", "duration": 2.0}
            ],
            "compatible_heroes": ["all"]
        }
        create_resp = await client.post("/api/studio/cms/animations", headers=headers, json=new_movement)
        assert create_resp.status == 200
        c_data = await create_resp.json()
        assert c_data["ok"] is True
        assert c_data["animation"]["id"] == "spin_jump"

        # 3. Verify it appears in library
        list_again = await client.get("/api/studio/cms/animations", headers=headers)
        l_data = await list_again.json()
        ids = [a["id"] for a in l_data["animations"]]
        assert "spin_jump" in ids
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cms_media_manager_and_used_in(monkeypatch):
    monkeypatch.setattr(settings, "content_studio_enabled", True)
    monkeypatch.setattr(settings, "content_studio_token", "owner-token")
    app = web.Application()
    content_studio.register_content_studio_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        headers = {"Authorization": "Bearer owner-token"}
        resp = await client.get("/api/studio/cms/media", headers=headers)
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert "files" in data
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cms_ai_settings_and_platform_settings(monkeypatch, tmp_path):
    storage = tmp_path / "storage"
    (storage / "platform-settings").mkdir(parents=True)
    monkeypatch.setattr(settings, "storage_root", storage)
    monkeypatch.setattr(settings, "content_studio_enabled", True)
    monkeypatch.setattr(settings, "content_studio_token", "owner-token")
    app = web.Application()
    content_studio.register_content_studio_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        headers = {"Authorization": "Bearer owner-token"}

        # AI settings
        ai_resp = await client.get("/api/studio/cms/ai-settings", headers=headers)
        assert ai_resp.status == 200
        ai_data = await ai_resp.json()
        assert ai_data["ok"] is True
        assert "persona" in ai_data["settings"]

        # Platform settings & registration mode
        settings_resp = await client.get("/api/studio/cms/settings", headers=headers)
        assert settings_resp.status == 200
        s_data = await settings_resp.json()
        assert s_data["ok"] is True
        assert "features" in s_data
        assert "registration_mode" in s_data
    finally:
        await client.close()
