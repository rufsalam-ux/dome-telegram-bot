"""Regression coverage for the active (admin_panel.js) Content Studio CMS."""

import json

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Base, TariffPlan
from app.services.tariff_plans import seed_default_tariffs
from app.webapp import content_studio


async def _memory_sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, sessions


@pytest.mark.asyncio
async def test_content_studio_page_and_antigravity_bridge_are_served(monkeypatch):
    """The CMS must be opened from its route, with both active visual assets."""
    monkeypatch.setattr(settings, "content_studio_enabled", True)
    app = web.Application()
    content_studio.register_content_studio_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        page = await client.get("/content-studio")
        bridge = await client.get("/content-studio/content_studio_antigravity_bridge.css")
        active = await client.get("/content-studio/admin_panel.css")
        assert page.status == bridge.status == active.status == 200
        assert "content_studio_antigravity_bridge.css" in await page.text()
        assert "Antigravity" in await bridge.text()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_studio_course_lesson_move_cover_and_safe_archive(monkeypatch, tmp_path):
    storage = tmp_path / "storage"
    content = tmp_path / "content"
    (content / "lessons").mkdir(parents=True)
    (content / "courses").mkdir(parents=True)
    monkeypatch.setattr(settings, "storage_root", storage)
    monkeypatch.setattr(settings, "content_root", content)
    monkeypatch.setattr(settings, "content_studio_token", "owner-secret")
    monkeypatch.setattr(settings, "content_studio_enabled", True)

    app = web.Application(client_max_size=10 * 1024 * 1024)
    content_studio.register_content_studio_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    headers = {"Authorization": "Bearer owner-secret"}
    try:
        for course_id in ("cms_alpha", "cms_beta"):
            response = await client.post(
                "/api/studio/courses", headers=headers,
                json={"course_id": course_id, "title": course_id, "status": "draft"},
            )
            assert response.status == 200

        created = await client.post(
            "/api/studio/lessons", headers=headers,
            json={"lesson_id": "cms_lesson", "title": "CMS lesson", "course_id": "cms_alpha"},
        )
        assert created.status == 201
        lesson = (await created.json())["lesson"]
        assert lesson["target_language"] == "ru"
        lesson["slides"] = [{"slide_id": "one", "order": 1, "type": "passive", "prompt": "Привет", "requiredForMovie": False}]
        assert (await client.put("/api/studio/lessons/cms_lesson", headers=headers, json={"lesson": lesson})).status == 200
        assert (await client.post("/api/studio/lessons/cms_lesson/publish", headers=headers)).status == 200

        # Move creates a draft; the live revision remains readable for an
        # already-started child session until the owner publishes the draft.
        moved = await client.post(
            "/api/studio/lessons/cms_lesson/move", headers=headers,
            json={"target_course_id": "cms_beta"},
        )
        moved_payload = await moved.json()
        assert moved.status == 200 and moved_payload["pending_publish"] is True
        root = storage / "authored-content" / "lessons" / "cms_lesson"
        assert json.loads((root / "lesson.json").read_text("utf-8"))["course_id"] == "cms_alpha"
        assert json.loads((root / "draft.json").read_text("utf-8"))["course_id"] == "cms_beta"
        assert (await client.post("/api/studio/lessons/cms_lesson/publish", headers=headers)).status == 200

        alpha = await client.get("/api/studio/courses/cms_alpha", headers=headers)
        beta = await client.get("/api/studio/courses/cms_beta", headers=headers)
        assert "cms_lesson" not in (await alpha.json())["course"]["lesson_ids"]
        assert "cms_lesson" in (await beta.json())["course"]["lesson_ids"]

        image = FormData()
        image.add_field("file", b"png", filename="cover.png", content_type="image/png")
        uploaded = await client.post("/api/studio/courses/cms_beta/cover", headers=headers, data=image)
        cover = (await uploaded.json())["cover_image"]
        assert uploaded.status == 200
        served = await client.get(cover, headers=headers)
        assert served.status == 200 and await served.read() == b"png"

        archived = await client.delete("/api/studio/lessons/cms_lesson", headers=headers)
        result = await archived.json()
        assert archived.status == 200 and result["action"] == "archived"
        assert (root / "lesson.json").is_file()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_tariff_seed_never_overwrites_owner_managed_values():
    engine, sessions = await _memory_sessions()
    try:
        async with sessions() as db:
            await seed_default_tariffs(db)
            plan = await db.get(TariffPlan, 1)
            plan.name = "Owner configured"
            plan.monthly_price = 47.0
            plan.annual_price = 470.0
            await db.commit()
            await seed_default_tariffs(db)
            reloaded = await db.get(TariffPlan, 1)
            assert reloaded.name == "Owner configured"
            assert reloaded.monthly_price == 47.0
            assert reloaded.annual_price == 470.0
    finally:
        await engine.dispose()
