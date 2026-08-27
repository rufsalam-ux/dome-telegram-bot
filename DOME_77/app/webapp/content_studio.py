from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiohttp import web

from app.core.config import settings
from app.services.authored_content import (
    backup_lesson_version,
    bundled_lessons_root,
    ensure_persistent_lesson,
    persistent_lessons_root,
    publication_status,
    restore_lesson_version,
    validate_content_lesson,
)
from app.services.lesson_loader import LessonConfigurationError, load_lesson


LESSON_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,79}$")
MEDIA_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".mp4", ".m4v", ".webm", ".mov",
    ".mp3", ".m4a", ".ogg", ".wav",
}


def _require_enabled() -> None:
    if not settings.content_studio_enabled:
        raise web.HTTPServiceUnavailable(
            text=json.dumps({"error": "Content Studio is disabled"}),
            content_type="application/json",
        )


def _lesson_id(value: Any) -> str:
    lesson_id = str(value or "").strip().lower()
    if not LESSON_ID_RE.fullmatch(lesson_id):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid lesson id"}), content_type="application/json")
    return lesson_id


def _authorized(request: web.Request) -> None:
    _require_enabled()
    expected = settings.content_studio_token.strip()
    if not expected:
        raise web.HTTPServiceUnavailable(
            text=json.dumps({"error": "Content Studio is not configured"}),
            content_type="application/json",
        )
    auth = request.headers.get("Authorization", "")
    supplied = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("X-DOME-Studio-Token", "").strip()
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise web.HTTPUnauthorized(
            text=json.dumps({"error": "Owner token is invalid"}),
            content_type="application/json",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _draft_path(lesson_id: str) -> Path:
    return persistent_lessons_root() / lesson_id / "draft.json"


def _live_path(lesson_id: str) -> Path:
    persistent = persistent_lessons_root() / lesson_id / "lesson.json"
    return persistent if persistent.exists() else bundled_lessons_root() / lesson_id / "lesson.json"


def _editable_lesson(lesson_id: str) -> dict[str, Any] | None:
    return _read_json(_draft_path(lesson_id)) or _read_json(_live_path(lesson_id))


def _all_lesson_ids() -> list[str]:
    found: set[str] = set()
    for root in (persistent_lessons_root(), bundled_lessons_root()):
        if root.exists():
            for folder in root.iterdir():
                if folder.is_dir() and ((folder / "lesson.json").exists() or (folder / "draft.json").exists()):
                    found.add(folder.name)
    return sorted(found)


def _audit(event: str, lesson_id: str, **details: Any) -> None:
    path = settings.storage_root / "authored-content" / "studio-audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "event": event,
        "lesson_id": lesson_id,
        "at": datetime.now(UTC).isoformat(),
        **details,
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def _summary(lesson_id: str) -> dict[str, Any]:
    draft = _read_json(_draft_path(lesson_id))
    live = _read_json(_live_path(lesson_id))
    current = draft or live or {}
    persistent_live = persistent_lessons_root() / lesson_id / "lesson.json"
    lifecycle = publication_status(live or current) if (live or current) else "DRAFT"
    return {
        "lesson_id": lesson_id,
        "title": str(current.get("title") or lesson_id),
        "course_id": str(current.get("course_id") or "conversation"),
        "order": int(current.get("order") or 9999),
        "draft": draft is not None,
        "published": live is not None and lifecycle == "PUBLISHED" and bool(live.get("active", True)),
        "publication_status": lifecycle,
        "source": "persistent" if persistent_live.exists() else "bundled",
        "slide_count": len(current.get("slides") or []),
        "revision": int((live or current).get("revision") or 1),
    }


async def studio_page(_: web.Request) -> web.FileResponse:
    _require_enabled()
    response = web.FileResponse(Path(__file__).parent / "static" / "content_studio.html")
    response.headers["Cache-Control"] = "no-store"
    return response


async def studio_status(request: web.Request) -> web.Response:
    _authorized(request)
    return web.json_response({"ok": True, "storage": "persistent", "max_upload_mb": settings.content_studio_max_upload_mb})


async def list_lessons(request: web.Request) -> web.Response:
    _authorized(request)
    lessons = sorted((_summary(lesson_id) for lesson_id in _all_lesson_ids()), key=lambda item: (item["course_id"], item["order"], item["lesson_id"]))
    courses = []
    for path in sorted((settings.content_root / "courses").glob("*.json")):
        raw = _read_json(path)
        if raw:
            courses.append({"course_id": raw.get("course_id", path.stem), "title": raw.get("title", path.stem), "active": bool(raw.get("active", True))})
    return web.json_response({"lessons": lessons, "courses": courses})


async def create_lesson(request: web.Request) -> web.Response:
    _authorized(request)
    data = await request.json()
    lesson_id = _lesson_id(data.get("lesson_id"))
    root = persistent_lessons_root() / lesson_id
    if root.exists() or (bundled_lessons_root() / lesson_id).exists():
        raise web.HTTPConflict(text=json.dumps({"error": "Lesson already exists"}), content_type="application/json")
    lesson = {
        "schema_version": "2.1",
        "engine": "content_v1",
        "lesson_id": lesson_id,
        "course_id": str(data.get("course_id") or "conversation"),
        "title": str(data.get("title") or lesson_id),
        "description": str(data.get("description") or ""),
        "order": int(data.get("order") or 9999),
        "active": False,
        "status": "draft",
        "import_status": "DRAFT",
        "max_completed_runs": 2,
        "expires_after_months": 10,
        "target_language": str(data.get("target_language") or "en"),
        "age_min": max(2, int(data.get("age_min") or 4)),
        "age_max": max(2, int(data.get("age_max") or 10)),
        "difficulty": str(data.get("difficulty") or "PRE_A1"),
        "native_language_mode": "child_profile",
        "slides": [],
        "revision": 1,
    }
    _atomic_json(_draft_path(lesson_id), lesson)
    _audit("LESSON_CREATED", lesson_id)
    return web.json_response({"lesson": lesson, "summary": _summary(lesson_id)}, status=201)


async def get_lesson(request: web.Request) -> web.Response:
    _authorized(request)
    lesson_id = _lesson_id(request.match_info["lesson_id"])
    lesson = _editable_lesson(lesson_id)
    if lesson is None:
        raise web.HTTPNotFound()
    versions_root = persistent_lessons_root() / lesson_id / "_versions"
    versions = [path.name for path in sorted(versions_root.iterdir(), reverse=True) if path.is_dir()] if versions_root.exists() else []
    media_root = persistent_lessons_root() / lesson_id / "media"
    media = []
    if media_root.exists():
        for path in sorted((item for item in media_root.iterdir() if item.is_file()), key=lambda item: item.stat().st_mtime, reverse=True):
            media.append({"name": path.name, "path": f"media/{path.name}", "size": path.stat().st_size})
    return web.json_response({"lesson": lesson, "summary": _summary(lesson_id), "versions": versions, "media": media})


async def save_lesson(request: web.Request) -> web.Response:
    _authorized(request)
    lesson_id = _lesson_id(request.match_info["lesson_id"])
    incoming = await request.json()
    lesson = incoming.get("lesson") if isinstance(incoming.get("lesson"), dict) else incoming
    if not isinstance(lesson, dict):
        raise web.HTTPBadRequest(text=json.dumps({"error": "lesson must be an object"}), content_type="application/json")
    lesson = dict(lesson)
    lesson["lesson_id"] = lesson_id
    lesson.setdefault("schema_version", "2.1")
    lesson["engine"] = "content_v1"
    lesson["active"] = False
    lesson["status"] = "draft"
    lesson["import_status"] = "DRAFT"
    lesson["max_completed_runs"] = 2
    lesson["expires_after_months"] = 10
    lesson["revision"] = max(1, int(lesson.get("revision") or 1))
    for index, slide in enumerate(lesson.get("slides") or [], 1):
        if isinstance(slide, dict):
            slide["order"] = index
    _atomic_json(_draft_path(lesson_id), lesson)
    _audit("LESSON_DRAFT_SAVED", lesson_id, slide_count=len(lesson.get("slides") or []))
    return web.json_response({"lesson": lesson, "summary": _summary(lesson_id), "validation_errors": validate_content_lesson(lesson)})


async def reorder_lessons(request: web.Request) -> web.Response:
    """Save author-selected catalog order as drafts; children stay on live revisions."""

    _authorized(request)
    data = await request.json()
    orders = data.get("orders")
    if not isinstance(orders, dict) or not orders:
        raise web.HTTPBadRequest(text=json.dumps({"error": "orders object is required"}), content_type="application/json")
    changed: list[str] = []
    for raw_id, raw_order in orders.items():
        lesson_id = _lesson_id(raw_id)
        lesson = _editable_lesson(lesson_id)
        if lesson is None:
            raise web.HTTPNotFound(text=json.dumps({"error": f"Lesson {lesson_id} not found"}), content_type="application/json")
        try:
            order = max(1, int(raw_order))
        except (TypeError, ValueError) as exc:
            raise web.HTTPBadRequest(text=json.dumps({"error": f"Invalid order for {lesson_id}"}), content_type="application/json") from exc
        draft = json.loads(json.dumps(lesson))
        draft.update({"lesson_id": lesson_id, "engine": "content_v1", "schema_version": str(draft.get("schema_version") or "2.1"), "order": order, "active": False, "status": "draft", "import_status": "DRAFT"})
        _atomic_json(_draft_path(lesson_id), draft)
        _audit("LESSON_ORDER_DRAFTED", lesson_id, order=order)
        changed.append(lesson_id)
    return web.json_response({"ok": True, "changed": changed, "lessons": sorted((_summary(item) for item in _all_lesson_ids()), key=lambda item: (item["course_id"], item["order"], item["lesson_id"]))})


async def duplicate_lesson(request: web.Request) -> web.Response:
    _authorized(request)
    source_id = _lesson_id(request.match_info["lesson_id"])
    data = await request.json()
    target_id = _lesson_id(data.get("lesson_id"))
    if (persistent_lessons_root() / target_id).exists() or (bundled_lessons_root() / target_id).exists():
        raise web.HTTPConflict(text=json.dumps({"error": "Target lesson already exists"}), content_type="application/json")
    source = _editable_lesson(source_id)
    if source is None:
        raise web.HTTPNotFound()
    target_root = persistent_lessons_root() / target_id
    source_root = persistent_lessons_root() / source_id
    if (source_root / "media").exists():
        shutil.copytree(source_root / "media", target_root / "media", dirs_exist_ok=True)
    duplicate = json.loads(json.dumps(source))
    duplicate.update({"lesson_id": target_id, "title": str(data.get("title") or f'{source.get("title", source_id)} — копия'), "active": False, "status": "draft", "import_status": "DRAFT", "revision": 1})
    _atomic_json(_draft_path(target_id), duplicate)
    _audit("LESSON_DUPLICATED", target_id, source_lesson_id=source_id)
    return web.json_response({"lesson": duplicate, "summary": _summary(target_id)}, status=201)


async def delete_lesson(request: web.Request) -> web.Response:
    _authorized(request)
    lesson_id = _lesson_id(request.match_info["lesson_id"])
    root = (persistent_lessons_root() / lesson_id).resolve()
    expected = persistent_lessons_root().resolve()
    if expected not in root.parents or not root.exists():
        raise web.HTTPNotFound()
    shutil.rmtree(root)
    _audit("LESSON_PERSISTENT_COPY_DELETED", lesson_id)
    return web.json_response({"ok": True, "bundled_fallback": (bundled_lessons_root() / lesson_id / "lesson.json").exists()})


async def validate_lesson(request: web.Request) -> web.Response:
    _authorized(request)
    lesson_id = _lesson_id(request.match_info["lesson_id"])
    lesson = _editable_lesson(lesson_id)
    if lesson is None:
        raise web.HTTPNotFound()
    errors = validate_content_lesson(lesson)
    return web.json_response({"ok": not errors, "errors": errors})


async def preview_lesson(request: web.Request) -> web.Response:
    _authorized(request)
    lesson_id = _lesson_id(request.match_info["lesson_id"])
    draft = _read_json(_draft_path(lesson_id))
    if draft is not None:
        errors = validate_content_lesson(draft)
        return web.json_response({"lesson": draft, "validation_errors": errors})
    try:
        return web.json_response({"lesson": load_lesson(lesson_id, preview=True), "validation_errors": []})
    except (LessonConfigurationError, FileNotFoundError) as exc:
        raise web.HTTPNotFound(text=json.dumps({"error": str(exc)}), content_type="application/json") from exc


async def publish_lesson(request: web.Request) -> web.Response:
    _authorized(request)
    lesson_id = _lesson_id(request.match_info["lesson_id"])
    draft = _read_json(_draft_path(lesson_id))
    if draft is None:
        draft = _read_json(_live_path(lesson_id))
    if draft is None:
        raise web.HTTPNotFound()
    candidate = json.loads(json.dumps(draft))
    candidate.update({"lesson_id": lesson_id, "engine": "content_v1", "active": True, "status": "published", "import_status": "PUBLISHED"})
    candidate["revision"] = max(1, int(candidate.get("revision") or 1)) + 1
    errors = validate_content_lesson(candidate)
    if errors:
        raise web.HTTPUnprocessableEntity(text=json.dumps({"error": "Lesson validation failed", "errors": errors}, ensure_ascii=False), content_type="application/json")
    root = ensure_persistent_lesson(lesson_id)
    if (root / "lesson.json").exists():
        backup_lesson_version(lesson_id, "before_studio_publish")
    _atomic_json(root / "lesson.json", candidate)
    _draft_path(lesson_id).unlink(missing_ok=True)
    _audit("LESSON_PUBLISHED", lesson_id, revision=candidate["revision"])
    return web.json_response({"lesson": candidate, "summary": _summary(lesson_id)})


async def archive_lesson(request: web.Request) -> web.Response:
    """Explicitly hide a live lesson without deleting its versions or media."""

    _authorized(request)
    lesson_id = _lesson_id(request.match_info["lesson_id"])
    source = _editable_lesson(lesson_id)
    if source is None:
        raise web.HTTPNotFound()
    root = ensure_persistent_lesson(lesson_id)
    if (root / "lesson.json").exists():
        backup_lesson_version(lesson_id, "before_archive")
    archived = json.loads(json.dumps(source))
    archived.update({"lesson_id": lesson_id, "engine": "content_v1", "schema_version": str(archived.get("schema_version") or "2.1"), "active": False, "status": "archived", "import_status": "ARCHIVED"})
    archived["revision"] = max(1, int(archived.get("revision") or 1)) + 1
    _atomic_json(root / "lesson.json", archived)
    _draft_path(lesson_id).unlink(missing_ok=True)
    _audit("LESSON_ARCHIVED", lesson_id, revision=archived["revision"])
    return web.json_response({"lesson": archived, "summary": _summary(lesson_id)})


async def rollback_lesson(request: web.Request) -> web.Response:
    _authorized(request)
    lesson_id = _lesson_id(request.match_info["lesson_id"])
    data = await request.json()
    version = Path(str(data.get("version") or "")).name
    if not version or version != str(data.get("version") or ""):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid version"}), content_type="application/json")
    if not restore_lesson_version(lesson_id, version):
        raise web.HTTPNotFound(text=json.dumps({"error": "Version not found"}), content_type="application/json")
    _draft_path(lesson_id).unlink(missing_ok=True)
    _audit("LESSON_ROLLED_BACK", lesson_id, version=version)
    return web.json_response({"lesson": _editable_lesson(lesson_id), "summary": _summary(lesson_id)})


async def upload_media(request: web.Request) -> web.Response:
    _authorized(request)
    lesson_id = _lesson_id(request.match_info["lesson_id"])
    if _editable_lesson(lesson_id) is None:
        raise web.HTTPNotFound()
    reader = await request.multipart()
    part = await reader.next()
    if part is None or part.name != "file" or not part.filename:
        raise web.HTTPBadRequest(text=json.dumps({"error": "file is required"}), content_type="application/json")
    original = Path(part.filename).name
    extension = Path(original).suffix.lower()
    if extension not in MEDIA_EXTENSIONS:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Unsupported media type"}), content_type="application/json")
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", Path(original).stem).strip("-")[:48] or "media"
    media_root = persistent_lessons_root() / lesson_id / "media"
    media_root.mkdir(parents=True, exist_ok=True)
    temporary = media_root / f".{secrets.token_hex(8)}.upload"
    digest = hashlib.sha256()
    size = 0
    limit = max(1, settings.content_studio_max_upload_mb) * 1024 * 1024
    try:
        with temporary.open("wb") as stream:
            while True:
                chunk = await part.read_chunk(size=64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=size)
                digest.update(chunk)
                stream.write(chunk)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        filename = f"{stem}-{stamp}-{digest.hexdigest()[:10]}{extension}"
        target = media_root / filename
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    _audit("LESSON_MEDIA_UPLOADED", lesson_id, filename=filename, size=size, sha256=digest.hexdigest())
    return web.json_response({"path": f"media/{filename}", "name": filename, "size": size, "sha256": digest.hexdigest()}, status=201)


async def studio_media(request: web.Request) -> web.StreamResponse:
    _authorized(request)
    lesson_id = _lesson_id(request.match_info["lesson_id"])
    filename = Path(request.match_info["filename"]).name
    if filename != request.match_info["filename"] or Path(filename).suffix.lower() not in MEDIA_EXTENSIONS:
        raise web.HTTPNotFound()
    base = (persistent_lessons_root() / lesson_id / "media").resolve()
    path = (base / filename).resolve()
    if base not in path.parents or not path.exists():
        raise web.HTTPNotFound()
    response = web.FileResponse(path)
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return response


def register_content_studio_routes(app: web.Application) -> None:
    app.router.add_get("/content-studio", studio_page)
    app.router.add_get("/api/studio/status", studio_status)
    app.router.add_get("/api/studio/lessons", list_lessons)
    app.router.add_post("/api/studio/lessons", create_lesson)
    app.router.add_post("/api/studio/lessons/reorder", reorder_lessons)
    app.router.add_get("/api/studio/lessons/{lesson_id}", get_lesson)
    app.router.add_put("/api/studio/lessons/{lesson_id}", save_lesson)
    app.router.add_delete("/api/studio/lessons/{lesson_id}", delete_lesson)
    app.router.add_post("/api/studio/lessons/{lesson_id}/duplicate", duplicate_lesson)
    app.router.add_post("/api/studio/lessons/{lesson_id}/validate", validate_lesson)
    app.router.add_get("/api/studio/lessons/{lesson_id}/preview", preview_lesson)
    app.router.add_post("/api/studio/lessons/{lesson_id}/publish", publish_lesson)
    app.router.add_post("/api/studio/lessons/{lesson_id}/archive", archive_lesson)
    app.router.add_post("/api/studio/lessons/{lesson_id}/rollback", rollback_lesson)
    app.router.add_post("/api/studio/lessons/{lesson_id}/media", upload_media)
    app.router.add_get("/api/studio/lessons/{lesson_id}/media/{filename}", studio_media)
