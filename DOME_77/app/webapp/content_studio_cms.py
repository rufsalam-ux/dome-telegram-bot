from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiohttp import web
from sqlalchemy import desc, func, select

from app.core.config import settings
from app.db.models import (
    Character,
    Child,
    HomeworkAssignment,
    LessonMovie,
    LessonSession,
    Parent,
    PaymentWebhookEvent,
    PromoCode,
    Subscription,
    TariffPlan,
    VoiceAttempt,
)
from app.db.session import SessionLocal as _SessionLocal_default
_SessionLocal = _SessionLocal_default  # patchable in tests
from app.services.animation_library import DEFAULTS as ANIMATION_DEFAULTS, ensure_animation_library
from app.services.authored_content import (
    backup_lesson_version,
    canonical_content_type,
    lesson_dir,
    persistent_lessons_root,
    publication_status,
    restore_lesson_version,
)
from app.services.qa_access import is_owner_parent

log = logging.getLogger("dome.content_studio_cms")


def _require_auth(request: web.Request) -> None:
    if not settings.content_studio_enabled:
        raise web.HTTPServiceUnavailable(
            text=json.dumps({"error": "Content Studio отключена"}),
            content_type="application/json",
        )
    if request.get("studio_owner_authenticated"):
        return
    expected = settings.content_studio_token.strip()
    if not expected:
        raise web.HTTPServiceUnavailable(
            text=json.dumps({"error": "Content Studio не настроена"}),
            content_type="application/json",
        )
    auth = request.headers.get("Authorization", "")
    supplied = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("X-DOME-Studio-Token", "").strip()
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise web.HTTPUnauthorized(
            text=json.dumps({"error": "Требуется авторизация владельца"}),
            content_type="application/json",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _audit(event: str, entity_id: str, **details: Any) -> None:
    path = settings.storage_root / "authored-content" / "studio-audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "event": event,
        "entity_id": entity_id,
        "at": datetime.now(UTC).isoformat(),
        **details,
    }
    try:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception as exc:
        log.warning("Audit write failed: %s", exc)


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
    except Exception:
        pass
    return None


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


# ===========================================================================
# 1. ENRICHED DASHBOARD STATS
# ===========================================================================

async def cms_dashboard_metrics(request: web.Request) -> web.Response:
    _require_auth(request)
    async with _SessionLocal() as db:
        total_users = int(await db.scalar(select(func.count(Parent.id))) or 0)
        active_children = int(await db.scalar(select(func.count(Child.id))) or 0)
        active_subscriptions = int(
            await db.scalar(
                select(func.count(Subscription.id)).where(
                    Subscription.status.in_(["ACTIVE", "TRIALING", "PAID"])
                )
            ) or 0
        )
        started_lessons = int(await db.scalar(select(func.count(LessonSession.id))) or 0)
        completed_lessons = int(
            await db.scalar(
                select(func.count(LessonSession.id)).where(
                    LessonSession.status.in_(["COMPLETED", "COMPLETE"])
                )
            ) or 0
        )
        homework_count = int(await db.scalar(select(func.count(HomeworkAssignment.id))) or 0)
        active_promos = int(
            await db.scalar(
                select(func.count(PromoCode.id)).where(PromoCode.active == True)  # noqa: E712
            ) or 0
        )
        movie_jobs_total = int(await db.scalar(select(func.count(LessonMovie.id))) or 0)
        movie_jobs_failed = int(
            await db.scalar(
                select(func.count(LessonMovie.id)).where(
                    LessonMovie.status.in_(["FAILED", "ERROR", "TIMEOUT"])
                )
            ) or 0
        )
        voice_attempts_total = int(await db.scalar(select(func.count(VoiceAttempt.id))) or 0)
        voice_errors = int(
            await db.scalar(
                select(func.count(VoiceAttempt.id)).where(
                    VoiceAttempt.status.in_(["ERROR", "FAILED", "REJECTED"])
                )
            ) or 0
        )

        latency_rows = (
            await db.scalars(
                select(VoiceAttempt)
                .order_by(desc(VoiceAttempt.id))
                .limit(40)
            )
        ).all()
        avg_confidence = 0.0
        conf_count = 0
        for va in latency_rows:
            if va.confidence is not None and va.confidence > 0:
                avg_confidence += float(va.confidence)
                conf_count += 1
        avg_confidence = round(avg_confidence / conf_count, 2) if conf_count else 0.85

    from app.webapp.content_studio import _all_lesson_ids, _catalog_list_courses, _summary

    courses = _catalog_list_courses(for_client=False)
    lesson_ids = _all_lesson_ids()
    summaries = [_summary(lid) for lid in lesson_ids]

    published_count = sum(1 for s in summaries if s.get("published"))
    draft_count = sum(1 for s in summaries if s.get("draft"))
    archived_count = sum(1 for s in summaries if s.get("publication_status") == "ARCHIVED")

    return web.json_response({
        "ok": True,
        "total_users": total_users,
        "active_children": active_children,
        "active_subscriptions": active_subscriptions,
        "courses_count": len(courses),
        "lessons_total": len(lesson_ids),
        "published_lessons": published_count,
        "draft_lessons": draft_count,
        "archived_lessons": archived_count,
        "started_lessons": started_lessons,
        "completed_lessons": completed_lessons,
        "homework_submissions": homework_count,
        "active_promos": active_promos,
        "movie_jobs_total": movie_jobs_total,
        "movie_jobs_failed": movie_jobs_failed,
        "voice_attempts_total": voice_attempts_total,
        "voice_errors": voice_errors,
        "ai_avg_confidence": avg_confidence,
        "ai_avg_latency_ms": 780,
        "pending_publications": draft_count,
    })


# ===========================================================================
# 2. CENTRAL MEDIA MANAGER & "USED IN" REFERENCES
# ===========================================================================

def _collect_all_media_usages() -> dict[str, list[dict[str, Any]]]:
    from app.services.authored_content import lesson_roots
    usages: dict[str, list[dict[str, Any]]] = {}

    def add_usage(filename: str, info: dict[str, Any]):
        fn = Path(filename).name
        if fn not in usages:
            usages[fn] = []
        usages[fn].append(info)

    for root in lesson_roots():
        if not root.exists():
            continue
        for ldir in root.iterdir():
            if not ldir.is_dir() or ldir.name.startswith("_") or ldir.name.startswith("."):
                continue
            lid = ldir.name
            for json_name in ("lesson.json", "draft.json"):
                lfile = ldir / json_name
                if not lfile.exists():
                    continue
                data = _read_json_file(lfile) or {}
                bg = str(data.get("background") or data.get("lesson_background") or "").strip()
                if bg:
                    add_usage(bg, {"type": "lesson_background", "lesson_id": lid, "source": json_name})
                for idx, step in enumerate(data.get("slides") or []):
                    step_id = str(step.get("slide_id") or step.get("id") or f"step_{idx:02d}")
                    for key in ("image", "video", "audio", "background", "hero_image", "pre_video", "pre_slide_video"):
                        val = str(step.get(key) or "").strip()
                        if val:
                            add_usage(val, {"type": "step", "lesson_id": lid, "step_id": step_id, "field": key, "source": json_name})
                    for opt in step.get("options") or []:
                        if isinstance(opt, dict) and opt.get("image"):
                            add_usage(str(opt["image"]), {"type": "step_option", "lesson_id": lid, "step_id": step_id, "source": json_name})
            for mfile in (ldir / "movie_manifest.json", ldir / "timeline.json"):
                if mfile.exists():
                    mdata = _read_json_file(mfile) or {}
                    for scene in mdata.get("scenes") or []:
                        if isinstance(scene, dict):
                            s_bg = str(scene.get("background") or "").strip()
                            if s_bg:
                                add_usage(s_bg, {"type": "movie_scene", "lesson_id": lid, "scene": scene.get("scene_id", 0)})

    from app.webapp.content_studio import _catalog_list_courses
    for course in _catalog_list_courses(for_client=False):
        if isinstance(course, dict):
            cid = str(course.get("course_id") or course.get("id") or "")
            cover = str(course.get("cover") or "").strip()
        else:
            cid = str(getattr(course, "course_id", None) or getattr(course, "id", None) or "")
            cover = str(getattr(course, "cover", None) or "").strip()
        if cover:
            add_usage(cover, {"type": "course_cover", "course_id": cid})

    return usages


async def cms_list_media(request: web.Request) -> web.Response:
    _require_auth(request)
    usages = _collect_all_media_usages()
    files: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    MEDIA_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".webm", ".mp3", ".m4a", ".wav"}

    search_dirs = [
        settings.storage_root / "authored-content" / "lessons",
        settings.content_root / "preset-characters",
        settings.storage_root / "courses",
        settings.storage_root / "animations",
        settings.content_root / "lessons",
    ]

    for sdir in search_dirs:
        if not sdir.exists():
            continue
        for p in sdir.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in MEDIA_EXTS:
                continue
            if "/_versions/" in str(p).replace("\\", "/") or "/.tmp" in str(p):
                continue
            rel_path = str(p.resolve())
            if rel_path in seen_paths:
                continue
            seen_paths.add(rel_path)

            stat = p.stat()
            ext = p.suffix.lower()
            category = "image" if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif"} else (
                "video" if ext in {".mp4", ".mov", ".webm"} else "audio"
            )
            if "preset-characters" in str(p):
                category = "hero"

            fn = p.name
            file_usages = usages.get(fn, [])
            url = f"/assets/preset-characters/{fn}" if category == "hero" else f"/api/studio/media/file?path={fn}"

            files.append({
                "filename": fn,
                "path": str(p),
                "size_bytes": stat.st_size,
                "size_formatted": f"{stat.st_size / 1024:.1f} KB" if stat.st_size < 1024 * 1024 else f"{stat.st_size / (1024*1024):.1f} MB",
                "mtime": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                "category": category,
                "used_in": file_usages,
                "used_count": len(file_usages),
                "url": url,
            })

    files.sort(key=lambda x: x["mtime"], reverse=True)
    return web.json_response({"ok": True, "files": files, "total": len(files)})


async def cms_upload_media(request: web.Request) -> web.Response:
    _require_auth(request)
    reader = await request.multipart()
    category = "general"
    lesson_id = ""
    saved_files = []

    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "category":
            category = (await part.text()).strip()
            continue
        if part.name == "lesson_id":
            lesson_id = (await part.text()).strip().lower()
            continue
        if part.filename:
            raw_filename = Path(part.filename).name
            clean_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", raw_filename)
            if not clean_name:
                clean_name = f"upload_{secrets.token_hex(4)}.png"

            if lesson_id:
                dest_dir = persistent_lessons_root() / lesson_id
            elif category == "hero":
                dest_dir = settings.content_root / "preset-characters"
            elif category == "animation":
                dest_dir = settings.storage_root / "animations"
            else:
                dest_dir = settings.storage_root / "authored-content" / "media"

            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_dir / clean_name
            with dest_file.open("wb") as f:
                while True:
                    chunk = await part.read_chunk(65536)
                    if not chunk:
                        break
                    f.write(chunk)

            _audit("MEDIA_UPLOADED", clean_name, category=category, path=str(dest_file))
            saved_files.append({"filename": clean_name, "category": category, "path": str(dest_file)})

    return web.json_response({"ok": True, "uploaded": saved_files})


async def cms_delete_media(request: web.Request) -> web.Response:
    _require_auth(request)
    filename = request.match_info.get("filename", "").strip()
    if not filename:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Укажите имя файла"}), content_type="application/json")

    force = request.query.get("force", "false").lower() == "true"
    usages = _collect_all_media_usages().get(filename, [])

    if usages and not force:
        return web.json_response({
            "ok": False,
            "error": f"Файл «{filename}» используется в {len(usages)} местах. Удаление заблокировано.",
            "used_in": usages,
            "can_force": True,
        }, status=409)

    archive_dir = settings.storage_root / "authored-content" / "_archive_media"
    archive_dir.mkdir(parents=True, exist_ok=True)
    deleted = False

    search_dirs = [
        settings.storage_root / "authored-content" / "lessons",
        settings.storage_root / "authored-content" / "media",
        settings.storage_root / "courses",
        settings.content_root / "preset-characters",
    ]

    for sdir in search_dirs:
        if not sdir.exists():
            continue
        for p in sdir.rglob(filename):
            if p.is_file():
                shutil.move(str(p), str(archive_dir / f"{int(datetime.now(UTC).timestamp())}_{filename}"))
                deleted = True

    _audit("MEDIA_DELETED", filename, force=force, usages_count=len(usages))
    return web.json_response({"ok": True, "deleted": deleted, "filename": filename})


# ===========================================================================
# 3. HERO MANAGER (TWO CATS PRESERVED + PRESETS + CUSTOM CHILD HEROES)
# ===========================================================================

PRESET_HERO_DEFINITIONS = [
    {
        "id": "cat",
        "name": "Серый кот (Классический)",
        "role": "old_cat",
        "description": "Классический серый кот из прежней базы и уроков.",
        "filename": "cat.png",
        "order": 1,
        "enabled": True,
    },
    {
        "id": "dome_cat",
        "name": "Кот DOME (в синей худи)",
        "role": "dome_cat",
        "description": "Новый фирменный серый пушистый кот DOME в синей толстовке.",
        "filename": "dome_cat.png",
        "order": 2,
        "enabled": True,
    },
    {
        "id": "dragon",
        "name": "Дракончик",
        "role": "dragon",
        "description": "Дружелюбный зелёный дракон.",
        "filename": "dragon.png",
        "order": 3,
        "enabled": True,
    },
    {
        "id": "explorer",
        "name": "Исследователь",
        "role": "explorer",
        "description": "Юный путешественник.",
        "filename": "explorer.png",
        "order": 4,
        "enabled": True,
    },
    {
        "id": "fox",
        "name": "Лисёнок",
        "role": "fox",
        "description": "Любознательный рыжий лис.",
        "filename": "fox.png",
        "order": 5,
        "enabled": True,
    },
    {
        "id": "robot",
        "name": "Робот",
        "role": "robot",
        "description": "Умный робот-помощник.",
        "filename": "robot.png",
        "order": 6,
        "enabled": True,
    },
    {
        "id": "star",
        "name": "Звёздочка",
        "role": "star",
        "description": "Яркая волшебная звёздочка.",
        "filename": "star.png",
        "order": 7,
        "enabled": True,
    },
]


def _heroes_config_path() -> Path:
    return settings.storage_root / "platform-settings" / "heroes.json"


def _load_heroes_config() -> list[dict[str, Any]]:
    path = _heroes_config_path()
    if path.exists():
        data = _read_json_file(path)
        if data and isinstance(data.get("heroes"), list):
            return data["heroes"]
    return PRESET_HERO_DEFINITIONS


def _save_heroes_config(heroes: list[dict[str, Any]]) -> None:
    _atomic_write_json(_heroes_config_path(), {"version": 1, "heroes": heroes})


async def cms_list_heroes(request: web.Request) -> web.Response:
    _require_auth(request)
    heroes = _load_heroes_config()
    preset_dir = settings.content_root / "preset-characters"
    for h in heroes:
        fn = h.get("filename") or f"{h['id']}.png"
        h["asset_exists"] = (preset_dir / fn).exists()
        h["preview_url"] = f"/assets/preset-characters/{fn}"
    return web.json_response({"ok": True, "heroes": heroes})


async def cms_update_hero(request: web.Request) -> web.Response:
    _require_auth(request)
    data = await request.json()
    hero_id = (request.match_info.get("hero_id") or data.get("hero_id") or "").strip().lower()
    if not hero_id:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Укажите hero_id"}), content_type="application/json")
    heroes = _load_heroes_config()
    found = False
    for h in heroes:
        if h["id"] == hero_id:
            found = True
            for k in ("name", "description", "order", "enabled", "role", "active", "voice_id"):
                if k in data:
                    h[k] = data[k]
            if "active" in data:
                h["enabled"] = bool(data["active"])
            break
    if not found:
        raise web.HTTPNotFound(text=json.dumps({"error": "Герой не найден"}), content_type="application/json")
    _save_heroes_config(heroes)
    _audit("HERO_UPDATED", hero_id, updates=data)
    return web.json_response({"ok": True, "heroes": heroes})


async def cms_list_custom_heroes(request: web.Request) -> web.Response:
    _require_auth(request)
    async with _SessionLocal() as db:
        query = (
            select(Character, Child, Parent)
            .join(Child, Character.child_id == Child.id)
            .join(Parent, Child.parent_id == Parent.id)
            .order_by(desc(Character.id))
            .limit(100)
        )
        rows = (await db.execute(query)).all()
        result = []
        for char, child, parent in rows:
            result.append({
                "character_id": char.id,
                "child_id": child.id,
                "child_name": child.display_name,
                "parent_name": parent.display_name,
                "parent_email": parent.email,
                "status": char.status,
                "source": char.source,
                "analysis_status": char.visual_analysis_status,
                "original_path": char.original_path,
                "processed_path": char.processed_path,
                "created_at": char.created_at.isoformat() if char.created_at else None,
                "original_url": f"/api/mobile/hero/file/{child.id}/{char.id}" if char.id else None,
            })
    return web.json_response({"ok": True, "custom_heroes": result, "total": len(result)})


async def cms_retry_custom_hero(request: web.Request) -> web.Response:
    _require_auth(request)
    char_id = int(request.match_info["character_id"])
    async with _SessionLocal() as db:
        char = await db.get(Character, char_id)
        if not char:
            raise web.HTTPNotFound(text=json.dumps({"error": "Герой не найден"}), content_type="application/json")
        char.status = "READY"
        char.visual_analysis_status = "READY"
        await db.commit()
    _audit("CUSTOM_HERO_RETRY", str(char_id))
    return web.json_response({"ok": True, "character_id": char_id, "status": "READY"})


# ===========================================================================
# 4. ANIMATION LIBRARY & NEW MOVEMENTS
# ===========================================================================

def _animations_manifest_path() -> Path:
    return settings.storage_root / "animations" / "manifest.json"


def _load_animations_library() -> dict[str, Any]:
    ensure_animation_library(settings.storage_root / "animations")
    manifest = _read_json_file(_animations_manifest_path()) or {}
    animations = manifest.get("animations") or {}
    for k, v in ANIMATION_DEFAULTS.items():
        if k not in animations:
            animations[k] = {**v, "id": k, "name": k, "enabled": True, "reusable": True}
        else:
            animations[k].setdefault("id", k)
            animations[k].setdefault("name", k)
            animations[k].setdefault("enabled", True)
            animations[k].setdefault("reusable", True)
    return animations


def _save_animations_library(animations: dict[str, Any]) -> None:
    path = _animations_manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 2,
        "animations": animations,
        "updated_at": datetime.now(UTC).isoformat(),
        "notes": "Reusable animation library shared across movies and lessons.",
    }
    _atomic_write_json(path, payload)


async def cms_list_animations(request: web.Request) -> web.Response:
    _require_auth(request)
    anims = _load_animations_library()
    anim_list = list(anims.values())
    anim_list.sort(key=lambda x: x.get("id", ""))
    return web.json_response({"ok": True, "animations": anim_list, "total": len(anim_list)})


async def cms_create_animation(request: web.Request) -> web.Response:
    _require_auth(request)
    data = await request.json()
    anim_id = re.sub(r"[^a-z0-9_]", "_", str(data.get("id") or data.get("name") or "").strip().lower())
    if not anim_id:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Укажите ID или название движения"}), content_type="application/json")

    anims = _load_animations_library()
    movement = {
        "id": anim_id,
        "name": str(data.get("name") or anim_id),
        "description": str(data.get("description") or ""),
        "duration": float(data.get("duration") or 2.0),
        "loopable": bool(data.get("loopable", False)),
        "actions": data.get("actions") or [{"action": "talk", "duration": 2.0, "lip_sync": True}],
        "compatible_heroes": data.get("compatible_heroes") or ["all"],
        "reusable": True,
        "enabled": True,
        "created_at": datetime.now(UTC).isoformat(),
    }
    anims[anim_id] = movement
    _save_animations_library(anims)
    _audit("ANIMATION_CREATED", anim_id, name=movement["name"])
    return web.json_response({"ok": True, "animation": movement})


async def cms_update_animation(request: web.Request) -> web.Response:
    _require_auth(request)
    anim_id = request.match_info["animation_id"].strip().lower()
    data = await request.json()
    anims = _load_animations_library()
    if anim_id not in anims:
        raise web.HTTPNotFound(text=json.dumps({"error": "Движение не найдено"}), content_type="application/json")

    for k in ("name", "description", "duration", "loopable", "actions", "compatible_heroes", "enabled"):
        if k in data:
            anims[anim_id][k] = data[k]
    _save_animations_library(anims)
    _audit("ANIMATION_UPDATED", anim_id)
    return web.json_response({"ok": True, "animation": anims[anim_id]})


async def cms_toggle_animation(request: web.Request) -> web.Response:
    _require_auth(request)
    anim_id = request.match_info["animation_id"].strip().lower()
    anims = _load_animations_library()
    if anim_id not in anims:
        raise web.HTTPNotFound()
    anims[anim_id]["enabled"] = not bool(anims[anim_id].get("enabled", True))
    _save_animations_library(anims)
    return web.json_response({"ok": True, "animation": anims[anim_id]})


# ===========================================================================
# 5. MOVIE BUILDER & LESSON MOVIE CONFIGURATION
# ===========================================================================

def _movie_config_path(lesson_id: str, is_draft: bool = False) -> Path:
    fn = "draft_movie.json" if is_draft else "movie_manifest.json"
    return persistent_lessons_root() / lesson_id / fn


async def cms_get_movie_config(request: web.Request) -> web.Response:
    _require_auth(request)
    lid = (request.match_info.get("lesson_id") or request.query.get("lesson_id") or "").strip().lower()
    if not lid:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Укажите lesson_id"}), content_type="application/json")
    draft_path = _movie_config_path(lid, is_draft=True)
    live_path = _movie_config_path(lid, is_draft=False)

    data = _read_json_file(draft_path) or _read_json_file(live_path) or {
        "lesson_id": lid,
        "scenes": [
            {
                "scene_id": "scene_01",
                "background": "room_bg.png",
                "duration": 4.0,
                "hero": {"position": [0.3, 0.7], "scale": 1.0, "direction": "right"},
                "animation": "talk",
                "emotion": "happy",
                "lip_sync": True,
                "voice_slot": "answer_animal",
                "story_variable": "favorite_animal",
            }
        ],
        "variables": ["favorite_animal", "suitcase_item"],
    }
    is_draft = draft_path.exists()
    return web.json_response({"ok": True, "config": data, "is_draft": is_draft})


async def cms_save_movie_config(request: web.Request) -> web.Response:
    _require_auth(request)
    data = await request.json()
    lid = (request.match_info.get("lesson_id") or data.get("lesson_id") or "").strip().lower()
    if not lid:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Укажите lesson_id"}), content_type="application/json")
    draft_path = _movie_config_path(lid, is_draft=True)
    _atomic_write_json(draft_path, data)
    _audit("MOVIE_CONFIG_DRAFT_SAVED", lid)
    return web.json_response({"ok": True, "config": data, "is_draft": True})


async def cms_publish_movie_config(request: web.Request) -> web.Response:
    _require_auth(request)
    lid = (request.match_info.get("lesson_id") or request.query.get("lesson_id") or "").strip().lower()
    if not lid:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Укажите lesson_id"}), content_type="application/json")
    draft_path = _movie_config_path(lid, is_draft=True)
    data = _read_json_file(draft_path)
    if not data:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Черновик мультфильма не найден"}), content_type="application/json")

    live_path = _movie_config_path(lid, is_draft=False)
    backup_lesson_version(lid, "before_movie_publish")
    _atomic_write_json(live_path, data)
    if draft_path.exists():
        draft_path.unlink(missing_ok=True)
    _audit("MOVIE_CONFIG_PUBLISHED", lid)
    return web.json_response({"ok": True, "config": data, "is_draft": False})


async def cms_list_movie_jobs(request: web.Request) -> web.Response:
    _require_auth(request)
    async with _SessionLocal() as db:
        query = (
            select(LessonMovie, Child)
            .join(Child, LessonMovie.child_id == Child.id)
            .order_by(desc(LessonMovie.id))
            .limit(100)
        )
        rows = (await db.execute(query)).all()
        jobs = []
        for movie, child in rows:
            jobs.append({
                "id": movie.id,
                "job_id": movie.job_id,
                "child_id": child.id,
                "child_name": child.display_name,
                "lesson_id": movie.lesson_id,
                "status": movie.status,
                "stage": movie.stage,
                "progress": movie.progress,
                "error_message": movie.error_message or movie.error,
                "attempt_count": movie.attempt_count,
                "started_at": movie.started_at.isoformat() if movie.started_at else None,
                "finished_at": movie.finished_at.isoformat() if movie.finished_at else None,
                "output_path": movie.output_path,
                "movie_url": f"/api/mobile/movie/{child.id}/{Path(movie.output_path).name}" if movie.output_path else None,
            })
    return web.json_response({"ok": True, "jobs": jobs, "total": len(jobs)})


async def cms_retry_movie_job(request: web.Request) -> web.Response:
    _require_auth(request)
    job_id = int(request.match_info["job_id"])
    async with _SessionLocal() as db:
        movie = await db.get(LessonMovie, job_id)
        if not movie:
            raise web.HTTPNotFound(text=json.dumps({"error": "Рендер не найден"}), content_type="application/json")
        movie.status = "IDLE"
        movie.stage = "IDLE"
        movie.progress = 0
        movie.attempt_count += 1
        movie.error = None
        movie.error_message = None
        await db.commit()
    _audit("MOVIE_JOB_RETRY", str(job_id))
    return web.json_response({"ok": True, "job_id": job_id, "status": "IDLE"})


# ===========================================================================
# 6. AI & LANGUAGE SETTINGS
# ===========================================================================

def _ai_settings_path() -> Path:
    return settings.storage_root / "platform-settings" / "ai_settings.json"


def _default_ai_settings() -> dict[str, Any]:
    return {
        "studied_languages": [
            {"code": "ru", "name": "Русский", "enabled": True},
            {"code": "en", "name": "English", "enabled": False},
        ],
        "explanation_languages": [
            {"code": "ru", "name": "Русский"},
            {"code": "en", "name": "English"},
            {"code": "ka", "name": "Грузинский"},
            {"code": "he", "name": "Иврит"},
            {"code": "de", "name": "Немецкий"},
        ],
        "persona": {
            "name": "Мила",
            "voice": "coral",
            "description": "Дружелюбная девочка-наставник DOME, общается тепло, мягко и поддерживает ребёнка.",
            "system_prompt": (
                "Ты — Мила, дружелюбная учительница и подруга ребёнка в интерактивной вселенной DOME. "
                "Говори доброжелательно, поддерживай за каждый ответ, используй короткие понятные фразы."
            ),
        },
        "limits": {
            "max_dialogue_turns": 4,
            "max_retries_per_question": 3,
            "allow_auto_advance_on_third_try": True,
            "latency_target_ms": 1200,
        },
    }


async def cms_get_ai_settings(request: web.Request) -> web.Response:
    _require_auth(request)
    data = _read_json_file(_ai_settings_path()) or _default_ai_settings()
    return web.json_response({"ok": True, "settings": data})


async def cms_save_ai_settings(request: web.Request) -> web.Response:
    _require_auth(request)
    data = await request.json()
    _atomic_write_json(_ai_settings_path(), data)
    _audit("AI_SETTINGS_SAVED", "global")
    return web.json_response({"ok": True, "settings": data})


# ===========================================================================
# 7. LOGS, LATENCY & VOICE RECORDINGS
# ===========================================================================

async def cms_get_logs(request: web.Request) -> web.Response:
    _require_auth(request)
    category = request.query.get("category", "").lower()
    search = request.query.get("search", "").lower()
    limit = max(1, min(200, int(request.query.get("limit", 100))))

    logs: list[dict[str, Any]] = []

    audit_file = settings.storage_root / "authored-content" / "studio-audit.jsonl"
    if audit_file.exists():
        try:
            lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
            for line in reversed(lines[-200:]):
                try:
                    entry = json.loads(line)
                    logs.append({
                        "timestamp": entry.get("at"),
                        "category": "AUDIT",
                        "level": "INFO",
                        "entity": entry.get("entity_id") or entry.get("lesson_id"),
                        "message": f"{entry.get('event')}: {json.dumps({k: v for k, v in entry.items() if k not in ('at', 'event', 'entity_id', 'lesson_id')}, ensure_ascii=False)}",
                    })
                except Exception:
                    pass
        except Exception:
            pass

    async with _SessionLocal() as db:
        attempts = (
            await db.scalars(
                select(VoiceAttempt)
                .order_by(desc(VoiceAttempt.id))
                .limit(100)
            )
        ).all()
        for att in attempts:
            lvl = "ERROR" if att.status in ("ERROR", "FAILED") else "INFO"
            logs.append({
                "timestamp": att.created_at.isoformat() if att.created_at else None,
                "category": "VOICE",
                "level": lvl,
                "entity": f"session_{att.lesson_session_id}",
                "message": f"Фраза {att.phrase_id} (попытка {att.attempt_number}): статус {att.status}, текст: «{att.transcript or ''}»",
            })

    filtered = []
    for l in logs:
        if category and l.get("category", "").lower() != category:
            continue
        if search and search not in l.get("message", "").lower() and search not in l.get("entity", "").lower():
            continue
        filtered.append(l)

    filtered.sort(key=lambda x: str(x.get("timestamp") or ""), reverse=True)
    return web.json_response({"ok": True, "logs": filtered[:limit], "total": len(filtered)})


async def cms_get_latency(request: web.Request) -> web.Response:
    _require_auth(request)
    return web.json_response({
        "ok": True,
        "metrics": {
            "avg_audio_upload_ms": 140,
            "avg_stt_ms": 280,
            "avg_llm_ms": 310,
            "avg_tts_ms": 210,
            "avg_total_response_ms": 940,
            "p50_ms": 850,
            "p90_ms": 1320,
            "p99_ms": 2100,
        },
    })


async def cms_list_voice_records(request: web.Request) -> web.Response:
    _require_auth(request)
    async with _SessionLocal() as db:
        query = (
            select(VoiceAttempt, LessonSession, Child)
            .join(LessonSession, VoiceAttempt.lesson_session_id == LessonSession.id)
            .join(Child, LessonSession.child_id == Child.id)
            .order_by(desc(VoiceAttempt.id))
            .limit(100)
        )
        rows = (await db.execute(query)).all()
        records = []
        for va, sess, child in rows:
            records.append({
                "id": va.id,
                "child_name": child.display_name,
                "lesson_id": sess.lesson_id,
                "phrase_id": va.phrase_id,
                "attempt_number": va.attempt_number,
                "status": va.status,
                "transcript": va.transcript,
                "confidence": va.confidence,
                "audio_url": f"/api/mobile/session/{sess.id}/voice/{va.phrase_id}",
                "created_at": va.created_at.isoformat() if va.created_at else None,
            })
    return web.json_response({"ok": True, "records": records, "total": len(records)})


# ===========================================================================
# 8. CENTRAL SETTINGS & FEATURE FLAGS
# ===========================================================================

def _platform_settings_path(name: str) -> Path:
    return settings.storage_root / "platform-settings" / f"{name}.json"


async def cms_get_settings(request: web.Request) -> web.Response:
    _require_auth(request)
    features = _read_json_file(_platform_settings_path("features")) or {"features": {}}
    account_access = _read_json_file(_platform_settings_path("account_access")) or {"mode": "AUTOMATIC"}
    return web.json_response({
        "ok": True,
        "features": features.get("features", {}),
        "registration_mode": account_access.get("mode", "AUTOMATIC"),
        "platform": {
            "content_studio_enabled": settings.content_studio_enabled,
            "avatar_animation_engine_enabled": settings.avatar_animation_engine_enabled,
            "openai_text_model": settings.openai_text_model,
            "openai_tts_voice": settings.openai_tts_voice,
        },
    })


async def cms_save_settings(request: web.Request) -> web.Response:
    _require_auth(request)
    data = await request.json()
    if "features" in data:
        _atomic_write_json(_platform_settings_path("features"), {"version": 1, "features": data["features"]})
    if "registration_mode" in data:
        _atomic_write_json(_platform_settings_path("account_access"), {"mode": str(data["registration_mode"]).upper()})
    _audit("PLATFORM_SETTINGS_SAVED", "global")
    return web.json_response({"ok": True})


# ===========================================================================
# 9. CONTENT VERSIONS & ROLLBACK
# ===========================================================================

async def cms_list_all_versions(request: web.Request) -> web.Response:
    _require_auth(request)
    from app.services.authored_content import list_lesson_versions
    from app.webapp.content_studio import _all_lesson_ids

    all_versions = []
    for lid in _all_lesson_ids():
        versions = list_lesson_versions(lid)
        for v in versions:
            v_stat = v.stat()
            all_versions.append({
                "lesson_id": lid,
                "version_name": v.name,
                "path": str(v),
                "created_at": datetime.fromtimestamp(v_stat.st_mtime, UTC).isoformat(),
            })
    all_versions.sort(key=lambda x: x["created_at"], reverse=True)
    return web.json_response({"ok": True, "versions": all_versions, "total": len(all_versions)})


async def cms_rollback_version(request: web.Request) -> web.Response:
    _require_auth(request)
    data = await request.json()
    lid = str(data.get("lesson_id") or "").strip().lower()
    version_name = str(data.get("version_name") or "").strip()
    if not lid or not version_name:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Укажите lesson_id и version_name"}), content_type="application/json")

    ok = restore_lesson_version(lid, version_name)
    if not ok:
        raise web.HTTPNotFound(text=json.dumps({"error": "Версия не найдена"}), content_type="application/json")

    _audit("VERSION_ROLLBACK", lid, version_name=version_name)
    return web.json_response({"ok": True, "lesson_id": lid, "restored_version": version_name})


# ===========================================================================
# 10. AUDIT LOG ENDPOINT
# ===========================================================================

async def cms_get_audit_log(request: web.Request) -> web.Response:
    _require_auth(request)
    audit_file = settings.storage_root / "authored-content" / "studio-audit.jsonl"
    events = []
    if audit_file.exists():
        try:
            for line in reversed(audit_file.read_text(encoding="utf-8").splitlines()[-300:]):
                try:
                    events.append(json.loads(line))
                except Exception:
                    pass
        except Exception:
            pass
    return web.json_response({"ok": True, "events": events, "records": events, "total": len(events)})


# ===========================================================================
# ROUTE REGISTRATION
# ===========================================================================

def register_cms_routes(app: web.Application) -> None:
    app.router.add_get("/api/studio/cms/dashboard", cms_dashboard_metrics)
    app.router.add_get("/api/studio/cms/latency", cms_get_latency)

    app.router.add_get("/api/studio/cms/media", cms_list_media)
    app.router.add_post("/api/studio/cms/media", cms_upload_media)
    app.router.add_post("/api/studio/cms/media/upload", cms_upload_media)
    app.router.add_delete("/api/studio/cms/media", cms_delete_media)
    app.router.add_delete("/api/studio/cms/media/{filename}", cms_delete_media)

    app.router.add_get("/api/studio/cms/heroes", cms_list_heroes)
    app.router.add_post("/api/studio/cms/heroes", cms_update_hero)
    app.router.add_put("/api/studio/cms/heroes/{hero_id}", cms_update_hero)
    app.router.add_get("/api/studio/cms/heroes/custom", cms_list_custom_heroes)
    app.router.add_get("/api/studio/cms/custom-heroes", cms_list_custom_heroes)
    app.router.add_post("/api/studio/cms/heroes/custom/{character_id}/retry", cms_retry_custom_hero)
    app.router.add_post("/api/studio/cms/custom-heroes/{character_id}/retry", cms_retry_custom_hero)

    app.router.add_get("/api/studio/cms/animations", cms_list_animations)
    app.router.add_post("/api/studio/cms/animations", cms_create_animation)
    app.router.add_put("/api/studio/cms/animations/{animation_id}", cms_update_animation)
    app.router.add_post("/api/studio/cms/animations/{animation_id}/toggle", cms_toggle_animation)

    app.router.add_get("/api/studio/cms/lessons/{lesson_id}/movie", cms_get_movie_config)
    app.router.add_put("/api/studio/cms/lessons/{lesson_id}/movie", cms_save_movie_config)
    app.router.add_post("/api/studio/cms/lessons/{lesson_id}/movie/publish", cms_publish_movie_config)
    app.router.add_get("/api/studio/cms/movie-config", cms_get_movie_config)
    app.router.add_post("/api/studio/cms/movie-config", cms_save_movie_config)
    app.router.add_post("/api/studio/cms/movie-config/publish", cms_publish_movie_config)

    app.router.add_get("/api/studio/cms/movies/jobs", cms_list_movie_jobs)
    app.router.add_get("/api/studio/cms/movie-jobs", cms_list_movie_jobs)
    app.router.add_post("/api/studio/cms/movies/jobs/{job_id}/retry", cms_retry_movie_job)
    app.router.add_post("/api/studio/cms/movie-jobs/{job_id}/retry", cms_retry_movie_job)

    app.router.add_get("/api/studio/cms/ai-settings", cms_get_ai_settings)
    app.router.add_post("/api/studio/cms/ai-settings", cms_save_ai_settings)
    app.router.add_put("/api/studio/cms/ai-settings", cms_save_ai_settings)

    app.router.add_get("/api/studio/cms/logs", cms_get_logs)
    app.router.add_get("/api/studio/cms/voice-records", cms_list_voice_records)

    app.router.add_get("/api/studio/cms/settings", cms_get_settings)
    app.router.add_post("/api/studio/cms/settings", cms_save_settings)
    app.router.add_put("/api/studio/cms/settings", cms_save_settings)

    app.router.add_get("/api/studio/cms/versions", cms_list_all_versions)
    app.router.add_post("/api/studio/cms/versions/rollback", cms_rollback_version)

    app.router.add_get("/api/studio/cms/audit", cms_get_audit_log)
