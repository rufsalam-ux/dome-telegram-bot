"""
DOME — Admin → Live App Content Sync — End-to-End Tests

Verifies all 6 acceptance criteria:

  TEST 1  Edit text  → Draft → old text visible → Publish → mobile sees new text WITHOUT APK
  TEST 2  Replace image → Publish → mobile gets new image WITHOUT APK
  TEST 3  Add new lesson → Publish → appears in mobile catalog WITHOUT APK
  TEST 4  Archive/Disable → Publish → lesson disappears from mobile WITHOUT APK
  TEST 5  Reorder → Publish → mobile sees new order WITHOUT APK
  TEST 6  Rollback → Publish → mobile gets restored version WITHOUT APK
"""
from __future__ import annotations

import json
import pytest
import pytest_asyncio
from contextlib import asynccontextmanager
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Base, Parent
from app.services.mobile_tokens import issue_session_token
from app.webapp import content_studio, mobile_api


def _minimal_lesson(lesson_id: str, title: str, order: int = 1, course_id: str = "conversation") -> dict:
    return {
        "lesson_id": lesson_id,
        "engine": "content_v1",
        "course_id": course_id,
        "title": title,
        "revision": 1,
        "active": True,
        "status": "PUBLISHED",
        "import_status": "PUBLISHED",
        "publication_status": "PUBLISHED",
        "order": order,
        "slides": [{"slide_id": "start", "order": 1, "type": "passive", "prompt": title}],
        "required_phrases": [],
    }


@asynccontextmanager
async def _clients(monkeypatch, tmp_path):
    storage = tmp_path / "storage"
    (storage / "platform-settings").mkdir(parents=True)
    (storage / "authored-content" / "lessons").mkdir(parents=True)
    (storage / "authored-content" / "courses").mkdir(parents=True)
    (storage / "animations").mkdir(parents=True)
    (storage / "media").mkdir(parents=True)

    monkeypatch.setattr(settings, "storage_root", storage)
    monkeypatch.setattr(settings, "content_studio_enabled", True)
    monkeypatch.setattr(settings, "content_studio_token", "admin-token")
    monkeypatch.setattr(settings, "mobile_auth_secret", "sync-test-secret-at-least-32-chars-long")
    monkeypatch.setattr(settings, "content_root", tmp_path / "content")
    (tmp_path / "content" / "lessons").mkdir(parents=True)
    (tmp_path / "content" / "courses").mkdir(parents=True)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    # Seed test parent
    async with sessions() as db:
        parent = Parent(id=1, email="test_parent@example.com", display_name="Test Parent", email_verified=True)
        db.add(parent)
        await db.commit()

    import app.webapp.content_studio_cms as cms_module
    monkeypatch.setattr(cms_module, "_SessionLocal", sessions)
    monkeypatch.setattr(content_studio, "_SessionLocal", sessions)
    monkeypatch.setattr(mobile_api, "SessionLocal", sessions)

    token = issue_session_token(1)
    mobile_headers = {"Authorization": f"Bearer {token}"}

    admin_app = web.Application()
    content_studio.register_content_studio_routes(admin_app)

    mobile_app = web.Application()
    mobile_api.register_mobile_routes(mobile_app)

    admin_client = TestClient(TestServer(admin_app))
    mobile_client = TestClient(TestServer(mobile_app))
    await admin_client.start_server()
    await mobile_client.start_server()
    try:
        yield admin_client, mobile_client, mobile_headers
    finally:
        await admin_client.close()
        await mobile_client.close()
        await engine.dispose()


def _admin_headers() -> dict:
    return {"Authorization": "Bearer admin-token"}


async def _save_and_publish(admin: TestClient, lesson_id: str, lesson_data: dict) -> None:
    save = await admin.post(
        f"/api/studio/lessons/{lesson_id}",
        headers=_admin_headers(),
        json=lesson_data,
    )
    assert save.status in (200, 201), f"save failed {save.status}: {await save.text()}"

    pub = await admin.post(
        f"/api/studio/lessons/{lesson_id}/publish",
        headers=_admin_headers(),
    )
    assert pub.status == 200, f"publish failed {pub.status}: {await pub.text()}"


# ---------------------------------------------------------------------------
# TEST 1 — Edit text → Draft → old text visible → Publish → mobile sees new text
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_1_edit_text_publish_mobile_sees_new(monkeypatch, tmp_path):
    async with _clients(monkeypatch, tmp_path) as (admin, mobile, headers):
        lesson_id = "sync_test_01"
        original = _minimal_lesson(lesson_id, "Original Title v1")
        await _save_and_publish(admin, lesson_id, original)

        # Mobile sees original title
        r1 = await mobile.get(f"/api/mobile/lesson/{lesson_id}", headers=headers)
        assert r1.status == 200
        assert (await r1.json())["title"] == "Original Title v1"

        # Admin saves DRAFT with new title (NOT published yet)
        draft = _minimal_lesson(lesson_id, "Draft Unpublished Title")
        save_r = await admin.post(f"/api/studio/lessons/{lesson_id}", headers=_admin_headers(), json=draft)
        assert save_r.status in (200, 201)

        # Mobile still sees the old text before publish!
        r_draft_check = await mobile.get(f"/api/mobile/lesson/{lesson_id}", headers=headers)
        assert (await r_draft_check.json())["title"] == "Original Title v1", "Draft change leaked before publish!"

        # Admin clicks PUBLISH
        pub_r = await admin.post(f"/api/studio/lessons/{lesson_id}/publish", headers=_admin_headers())
        assert pub_r.status == 200

        # Now mobile immediately sees new text WITHOUT APK
        r2 = await mobile.get(f"/api/mobile/lesson/{lesson_id}", headers=headers)
        assert r2.status == 200
        assert (await r2.json())["title"] == "Draft Unpublished Title"


# ---------------------------------------------------------------------------
# TEST 2 — Replace image → Publish → mobile gets new image path WITHOUT APK
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_2_replace_image_publish_mobile_sees_new(monkeypatch, tmp_path):
    async with _clients(monkeypatch, tmp_path) as (admin, mobile, headers):
        lesson_id = "sync_test_02"
        original = _minimal_lesson(lesson_id, "Image Lesson")
        original["cover_image"] = "media/old_cat.png"
        await _save_and_publish(admin, lesson_id, original)

        r1 = await mobile.get(f"/api/mobile/lesson/{lesson_id}", headers=headers)
        assert (await r1.json()).get("cover_image") == "media/old_cat.png"

        # Admin replaces image ref and publishes
        updated = _minimal_lesson(lesson_id, "Image Lesson")
        updated["cover_image"] = "media/new_cat.png"
        await _save_and_publish(admin, lesson_id, updated)

        r2 = await mobile.get(f"/api/mobile/lesson/{lesson_id}", headers=headers)
        assert (await r2.json()).get("cover_image") == "media/new_cat.png"


# ---------------------------------------------------------------------------
# TEST 3 — Add new lesson → Publish → appears in mobile manifest WITHOUT APK
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_3_new_lesson_appears_in_manifest(monkeypatch, tmp_path):
    async with _clients(monkeypatch, tmp_path) as (admin, mobile, headers):
        m1 = await (await mobile.get("/api/mobile/content/manifest")).json()
        assert "new_sync_03" not in m1["lessons"]

        # Admin creates and publishes new lesson
        new_lesson = _minimal_lesson("new_sync_03", "Brand New Animal Adventure")
        await _save_and_publish(admin, "new_sync_03", new_lesson)

        # App manifest immediately includes it
        m2 = await (await mobile.get("/api/mobile/content/manifest")).json()
        assert "new_sync_03" in m2["lessons"]
        assert m2["content_version"] != m1.get("content_version")

        # Mobile loads new lesson
        r = await mobile.get("/api/mobile/lesson/new_sync_03", headers=headers)
        assert r.status == 200
        assert (await r.json())["title"] == "Brand New Animal Adventure"


# ---------------------------------------------------------------------------
# TEST 4 — Archive/Disable → Publish → lesson disappears from manifest
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_4_archive_lesson_disappears_from_manifest(monkeypatch, tmp_path):
    async with _clients(monkeypatch, tmp_path) as (admin, mobile, headers):
        lesson_id = "sync_test_04"
        active = _minimal_lesson(lesson_id, "Lesson to Archive")
        await _save_and_publish(admin, lesson_id, active)

        m1 = await (await mobile.get("/api/mobile/content/manifest")).json()
        assert lesson_id in m1["lessons"]

        # Admin archives lesson via safe-archive or status update
        archive_r = await admin.post(f"/api/studio/lessons/{lesson_id}/safe-archive", headers=_admin_headers())
        assert archive_r.status in (200, 201)

        # Manifest excludes archived lessons
        m2 = await (await mobile.get("/api/mobile/content/manifest")).json()
        assert lesson_id not in m2["lessons"]


# ---------------------------------------------------------------------------
# TEST 5 — Reorder → Publish → mobile sees new order WITHOUT APK
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_5_reorder_lessons_reflected_in_order(monkeypatch, tmp_path):
    async with _clients(monkeypatch, tmp_path) as (admin, mobile, headers):
        l1 = _minimal_lesson("sync_l1", "Lesson 1", order=10)
        l2 = _minimal_lesson("sync_l2", "Lesson 2", order=20)
        await _save_and_publish(admin, "sync_l1", l1)
        await _save_and_publish(admin, "sync_l2", l2)

        # Reorder them (swap orders)
        l1_reordered = _minimal_lesson("sync_l1", "Lesson 1", order=25)
        l2_reordered = _minimal_lesson("sync_l2", "Lesson 2", order=5)
        await _save_and_publish(admin, "sync_l1", l1_reordered)
        await _save_and_publish(admin, "sync_l2", l2_reordered)

        d1 = await (await mobile.get("/api/mobile/lesson/sync_l1", headers=headers)).json()
        d2 = await (await mobile.get("/api/mobile/lesson/sync_l2", headers=headers)).json()
        assert d1["order"] == 25
        assert d2["order"] == 5


# ---------------------------------------------------------------------------
# TEST 6 — Rollback → Publish → mobile gets restored version WITHOUT APK
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_6_rollback_publish_mobile_gets_restored(monkeypatch, tmp_path):
    async with _clients(monkeypatch, tmp_path) as (admin, mobile, headers):
        lesson_id = "sync_test_06"

        # v1 published
        v1 = _minimal_lesson(lesson_id, "Version 1 Working Title")
        await _save_and_publish(admin, lesson_id, v1)

        # v2 published (bad edit)
        v2 = _minimal_lesson(lesson_id, "Version 2 Mistake Title")
        await _save_and_publish(admin, lesson_id, v2)

        r2 = await mobile.get(f"/api/mobile/lesson/{lesson_id}", headers=headers)
        assert (await r2.json())["title"] == "Version 2 Mistake Title"

        # Get version history from admin
        studio_r = await admin.get(f"/api/studio/lessons/{lesson_id}", headers=_admin_headers())
        assert studio_r.status == 200
        versions = (await studio_r.json()).get("versions") or []
        assert len(versions) > 0, "No version backup created during publish"

        # Rollback to earliest version (the one created before v2 publish)
        earliest = sorted(versions)[0]
        rb_r = await admin.post(
            f"/api/studio/lessons/{lesson_id}/rollback",
            headers=_admin_headers(),
            json={"version": earliest},
        )
        assert rb_r.status == 200

        # Mobile immediately receives restored content
        restored = await (await mobile.get(f"/api/mobile/lesson/{lesson_id}", headers=headers)).json()
        assert restored["title"] == "Version 1 Working Title"


# ---------------------------------------------------------------------------
# Bonus: ETag 304 and Session Pinning
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_manifest_etag_304_and_pinned_version(monkeypatch, tmp_path):
    async with _clients(monkeypatch, tmp_path) as (admin, mobile, headers):
        lesson_id = "sync_pinned_07"
        v1 = _minimal_lesson(lesson_id, "Pinned Lesson v1")
        await _save_and_publish(admin, lesson_id, v1)

        # Test ETag support
        m_resp = await mobile.get("/api/mobile/content/manifest")
        assert m_resp.status == 200
        etag = m_resp.headers.get("ETag")
        assert etag

        m_304 = await mobile.get("/api/mobile/content/manifest", headers={"If-None-Match": etag})
        assert m_304.status == 304

        # Publish v2
        r1_live = await (await mobile.get(f"/api/mobile/lesson/{lesson_id}", headers=headers)).json()
        rev1 = r1_live.get("revision")

        v2 = _minimal_lesson(lesson_id, "Pinned Lesson v2")
        await _save_and_publish(admin, lesson_id, v2)

        # Querying with pinned revision gets v1 from _versions
        pinned_resp = await mobile.get(f"/api/mobile/lesson/{lesson_id}?revision={rev1}", headers=headers)
        assert pinned_resp.status == 200
        assert (await pinned_resp.json())["title"] == "Pinned Lesson v1"

        # Normal query gets v2
        live_resp = await mobile.get(f"/api/mobile/lesson/{lesson_id}", headers=headers)
        assert live_resp.status == 200
        assert (await live_resp.json())["title"] == "Pinned Lesson v2"
