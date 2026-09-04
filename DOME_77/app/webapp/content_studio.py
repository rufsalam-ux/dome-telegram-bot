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
    authored_steps,
    backup_lesson_version,
    bundled_lessons_root,
    ensure_persistent_lesson,
    normalized_media_sequence,
    persistent_lessons_root,
    publication_status,
    restore_lesson_version,
    validate_content_lesson,
)
from app.services.lesson_loader import LessonConfigurationError, load_lesson
from app.services.content_authoring_assistant import propose_task


LESSON_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,79}$")
MEDIA_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".mp4", ".m4v", ".webm", ".mov",
    ".mp3", ".m4a", ".ogg", ".wav",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".m4v", ".webm", ".mov"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".ogg", ".wav"}


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


def _media_manifest_path(lesson_id: str) -> Path:
    return persistent_lessons_root() / lesson_id / "media-library.json"


def _media_manifest(lesson_id: str) -> dict[str, Any]:
    data = _read_json(_media_manifest_path(lesson_id)) or {}
    assets = data.get("assets") if isinstance(data.get("assets"), dict) else {}
    return {"version": 1, "assets": assets}


def _save_media_manifest(lesson_id: str, manifest: dict[str, Any]) -> None:
    _atomic_json(_media_manifest_path(lesson_id), manifest)


def _json_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for child in value for item in _json_strings(child)]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _json_strings(child)]
    return []


def _media_references(lesson_id: str, filename: str) -> list[str]:
    needle = f"media/{filename}"
    references: list[str] = []
    for label, data in (("draft", _read_json(_draft_path(lesson_id))), ("published", _read_json(persistent_lessons_root() / lesson_id / "lesson.json"))):
        if data and any(value == needle or value.endswith(f"/{filename}") for value in _json_strings(data)):
            references.append(label)
    return references


def _media_entry(lesson_id: str, path: Path, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = ((manifest or _media_manifest(lesson_id)).get("assets") or {}).get(path.name) or {}
    return {
        "name": path.name,
        "display_name": str(metadata.get("display_name") or path.name),
        "path": f"media/{path.name}",
        "size": path.stat().st_size,
        "sha256": str(metadata.get("sha256") or ""),
        "created_at": str(metadata.get("created_at") or ""),
        "used_by": _media_references(lesson_id, path.name),
    }


def _media_validation_errors(lesson_id: str, lesson: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    roots = [persistent_lessons_root() / lesson_id, bundled_lessons_root() / lesson_id]
    for index, slide in enumerate(authored_steps(lesson), 1):
        descriptors = normalized_media_sequence(slide)
        pre_video = slide.get("preSlideVideo") or slide.get("pre_slide_video")
        if isinstance(pre_video, dict) and pre_video.get("enabled") is not False:
            descriptors.append({"id": "preSlideVideo", "type": "video", "src": pre_video.get("uri") or pre_video.get("src") or pre_video.get("url")})
        for descriptor in descriptors:
            if not isinstance(descriptor, dict):
                continue
            source = str(descriptor.get("src") or descriptor.get("url") or "").strip()
            kind = str(descriptor.get("type") or "").strip().lower()
            if not source or re.match(r"^https?://", source, re.I):
                continue
            safe = Path(source.replace("\\", "/"))
            if safe.is_absolute() or ".." in safe.parts:
                errors.append(f"slide {index}: unsafe media path {source}")
                continue
            if not any((root / safe).is_file() for root in roots):
                errors.append(f"slide {index}: missing media {source}")
                continue
            extension = safe.suffix.lower()
            allowed = IMAGE_EXTENSIONS if kind in {"image", "animation"} else VIDEO_EXTENSIONS if kind == "video" else AUDIO_EXTENSIONS if kind == "audio" else None
            if allowed is not None and extension not in allowed:
                errors.append(f"slide {index}: {kind} cannot use {extension or '<no extension>'}")
    return errors


def _russian_validation_errors(errors: list[str]) -> list[str]:
    """Turn technical schema errors into actionable owner-facing messages."""

    translated: list[str] = []
    replacements = {
        "lesson needs at least one slide": "Добавьте хотя бы один шаг урока.",
        "steps/slides must be a list": "Последовательность шагов повреждена. Откройте резервную версию.",
        "missing lesson_id": "Не указан идентификатор урока.",
        "missing course_id": "Не указан курс.",
        "missing title": "Укажите название урока.",
        "missing order": "Укажите место урока в курсе.",
        "missing schema_version": "Не указана версия формата урока.",
    }
    for error in errors:
        if error in replacements:
            translated.append(replacements[error])
        elif "missing media" in error:
            translated.append(f"Не найден файл для шага: {error.split('missing media', 1)[1].strip()}.")
        elif "unsafe media path" in error:
            translated.append("Небезопасный путь к медиафайлу. Загрузите файл через редактор.")
        elif "duplicate slide_id" in error:
            translated.append("У двух шагов одинаковый ID. Продублируйте шаг заново или измените ID.")
        elif "duplicate order" in error:
            translated.append("Нарушен порядок шагов. Нажмите «Сохранить» ещё раз после перестановки.")
        elif "needs ai_instruction or target_phrase" in error:
            translated.append("Для голосового/диалогового шага заполните инструкцию AI или фразу на изучаемом языке.")
        else:
            translated.append(f"Проверьте шаг урока: {error}")
    return translated


def _validation_errors(lesson_id: str, lesson: dict[str, Any]) -> list[str]:
    return validate_content_lesson(lesson) + _media_validation_errors(lesson_id, lesson)


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
        "source": "persistent" if persistent_live.exists() or draft is not None else "bundled",
        "slide_count": len(current.get("steps") or current.get("slides") or []),
        "revision": int((live or current).get("revision") or 1),
    }


async def studio_page(_: web.Request) -> web.FileResponse:
    _require_enabled()
    response = web.FileResponse(Path(__file__).parent / "static" / "content_studio.html")
    response.headers["Cache-Control"] = "no-store"
    return response


async def studio_static(request: web.Request) -> web.FileResponse:
    _require_enabled()
    filename = request.match_info["filename"]
    _ALLOWED_STATIC = {
        "content_studio.css",
        "content_studio_extensions.css",
        "content_studio.js",
        "admin_panel.js",
        "admin_panel.css",
    }
    if filename not in _ALLOWED_STATIC:
        raise web.HTTPNotFound()
    response = web.FileResponse(Path(__file__).parent / "static" / filename)
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
        raise web.HTTPConflict(text=json.dumps({"error": "Урок с таким ID уже существует."}, ensure_ascii=False), content_type="application/json")
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
        "explanation_language": str(data.get("explanation_language") or "ru"),
        "age_min": max(2, int(data.get("age_min") or 4)),
        "age_max": max(2, int(data.get("age_max") or 10)),
        "difficulty": str(data.get("difficulty") or "PRE_A1"),
        "native_language_mode": "child_profile",
        "slides": [],
        "revision": 1,
    }
    _atomic_json(_draft_path(lesson_id), lesson)
    # Register lesson into course catalog (keeps lesson_ids in sync)
    try:
        from app.services.course_catalog import catalog_add_lesson
        catalog_add_lesson(lesson["course_id"], lesson_id)
    except Exception:
        pass  # Non-fatal; lesson is still discoverable via filesystem scan
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
    manifest = _media_manifest(lesson_id)
    media = []
    if media_root.exists():
        for path in sorted((item for item in media_root.iterdir() if item.is_file()), key=lambda item: item.stat().st_mtime, reverse=True):
            media.append(_media_entry(lesson_id, path, manifest))
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
    # Set before _validation_errors so the DRAFT 0-slide exemption works
    lesson["max_completed_runs"] = 2
    lesson["expires_after_months"] = 10
    lesson["revision"] = max(1, int(lesson.get("revision") or 1))
    steps_key = "steps" if isinstance(lesson.get("steps"), list) else "slides"
    for index, slide in enumerate(lesson.get(steps_key) or [], 1):
        if isinstance(slide, dict):
            slide["order"] = index
    errors = _validation_errors(lesson_id, lesson)
    if errors:
        raise web.HTTPUnprocessableEntity(
            text=json.dumps(
                {
                    "error": "Урок не сохранён: исправьте отмеченные поля.",
                    "errors": _russian_validation_errors(errors),
                    "technical_errors": errors,
                },
                ensure_ascii=False,
            ),
            content_type="application/json",
        )
    backup = backup_lesson_version(lesson_id, "before_studio_save")
    _atomic_json(_draft_path(lesson_id), lesson)
    _audit("LESSON_DRAFT_SAVED", lesson_id, slide_count=len(authored_steps(lesson)), backup_version=backup.name if backup else None)
    return web.json_response({"lesson": lesson, "summary": _summary(lesson_id), "validation_errors": [], "backup_version": backup.name if backup else None})


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
        raise web.HTTPConflict(text=json.dumps({"error": "Урок-копия с таким ID уже существует."}, ensure_ascii=False), content_type="application/json")
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
    lesson = None
    if request.can_read_body:
        try:
            incoming = await request.json()
            lesson = incoming.get("lesson") if isinstance(incoming, dict) and isinstance(incoming.get("lesson"), dict) else incoming
        except (json.JSONDecodeError, web.HTTPBadRequest):
            lesson = None
    lesson = lesson if isinstance(lesson, dict) else _editable_lesson(lesson_id)
    if lesson is None:
        raise web.HTTPNotFound()
    errors = _validation_errors(lesson_id, lesson)
    return web.json_response({"ok": not errors, "errors": _russian_validation_errors(errors), "technical_errors": errors})


async def preview_lesson(request: web.Request) -> web.Response:
    _authorized(request)
    lesson_id = _lesson_id(request.match_info["lesson_id"])
    draft = _read_json(_draft_path(lesson_id))
    if draft is not None:
        errors = _validation_errors(lesson_id, draft)
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
    errors = _validation_errors(lesson_id, candidate)
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
    if bool(data.get("as_draft")):
        root = ensure_persistent_lesson(lesson_id)
        source_root = root / "_versions" / version
        source = source_root / "draft.json"
        if not source.is_file():
            source = source_root / "lesson.json"
        restored = _read_json(source)
        if restored is None:
            raise web.HTTPNotFound(text=json.dumps({"error": "Version not found"}), content_type="application/json")
        backup = backup_lesson_version(lesson_id, "before_draft_restore")
        restored.update({"lesson_id": lesson_id, "active": False, "status": "draft", "import_status": "DRAFT"})
        _atomic_json(_draft_path(lesson_id), restored)
        _audit("LESSON_DRAFT_RESTORED", lesson_id, version=version, backup_version=backup.name if backup else None)
        return web.json_response({"lesson": restored, "summary": _summary(lesson_id)})
    if not restore_lesson_version(lesson_id, version):
        raise web.HTTPNotFound(text=json.dumps({"error": "Version not found"}), content_type="application/json")
    _draft_path(lesson_id).unlink(missing_ok=True)
    _audit("LESSON_ROLLED_BACK", lesson_id, version=version)
    return web.json_response({"lesson": _editable_lesson(lesson_id), "summary": _summary(lesson_id)})


async def _store_media_part(lesson_id: str, part: Any) -> tuple[dict[str, Any], bool]:
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
        sha256 = digest.hexdigest()
        manifest = _media_manifest(lesson_id)
        duplicate = next((name for name, metadata in manifest["assets"].items() if metadata.get("sha256") == sha256 and Path(name).suffix.lower() == extension and (media_root / name).exists()), None)
        if duplicate:
            temporary.unlink(missing_ok=True)
            return _media_entry(lesson_id, media_root / duplicate, manifest), True
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        filename = f"{stem}-{stamp}-{sha256[:10]}{extension}"
        target = media_root / filename
        os.replace(temporary, target)
        manifest["assets"][filename] = {
            "display_name": original,
            "sha256": sha256,
            "created_at": datetime.now(UTC).isoformat(),
        }
        _save_media_manifest(lesson_id, manifest)
    finally:
        temporary.unlink(missing_ok=True)
    return _media_entry(lesson_id, target, manifest), False


async def upload_media(request: web.Request) -> web.Response:
    _authorized(request)
    lesson_id = _lesson_id(request.match_info["lesson_id"])
    if _editable_lesson(lesson_id) is None:
        raise web.HTTPNotFound()
    reader = await request.multipart()
    part = await reader.next()
    if part is None or part.name != "file" or not part.filename:
        raise web.HTTPBadRequest(text=json.dumps({"error": "file is required"}), content_type="application/json")
    result, reused = await _store_media_part(lesson_id, part)
    _audit("LESSON_MEDIA_REUSED" if reused else "LESSON_MEDIA_UPLOADED", lesson_id, filename=result["name"], size=result["size"], sha256=result["sha256"])
    result["reused"] = reused
    return web.json_response(result, status=200 if reused else 201)


async def rename_media(request: web.Request) -> web.Response:
    _authorized(request)
    lesson_id = _lesson_id(request.match_info["lesson_id"])
    filename = Path(request.match_info["filename"]).name
    path = persistent_lessons_root() / lesson_id / "media" / filename
    if filename != request.match_info["filename"] or not path.is_file():
        raise web.HTTPNotFound()
    data = await request.json()
    display_name = str(data.get("display_name") or "").strip()[:160]
    if not display_name:
        raise web.HTTPBadRequest(text=json.dumps({"error": "display_name is required"}), content_type="application/json")
    manifest = _media_manifest(lesson_id)
    metadata = dict(manifest["assets"].get(filename) or {})
    metadata["display_name"] = display_name
    metadata.setdefault("created_at", datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat())
    manifest["assets"][filename] = metadata
    _save_media_manifest(lesson_id, manifest)
    _audit("LESSON_MEDIA_RENAMED", lesson_id, filename=filename, display_name=display_name)
    return web.json_response(_media_entry(lesson_id, path, manifest))


async def replace_media(request: web.Request) -> web.Response:
    _authorized(request)
    lesson_id = _lesson_id(request.match_info["lesson_id"])
    filename = Path(request.match_info["filename"]).name
    old_path = persistent_lessons_root() / lesson_id / "media" / filename
    if filename != request.match_info["filename"] or not old_path.is_file():
        raise web.HTTPNotFound()
    reader = await request.multipart()
    part = await reader.next()
    if part is None or part.name != "file" or not part.filename:
        raise web.HTTPBadRequest(text=json.dumps({"error": "file is required"}), content_type="application/json")
    result, reused = await _store_media_part(lesson_id, part)
    result.update({"reused": reused, "replaces": f"media/{filename}"})
    _audit("LESSON_MEDIA_REPLACEMENT_CREATED", lesson_id, old_filename=filename, new_filename=result["name"], reused=reused)
    return web.json_response(result, status=200 if reused else 201)


async def delete_unused_media(request: web.Request) -> web.Response:
    _authorized(request)
    lesson_id = _lesson_id(request.match_info["lesson_id"])
    filename = Path(request.match_info["filename"]).name
    base = (persistent_lessons_root() / lesson_id / "media").resolve()
    path = (base / filename).resolve()
    if filename != request.match_info["filename"] or base not in path.parents or not path.is_file():
        raise web.HTTPNotFound()
    used_by = _media_references(lesson_id, filename)
    if used_by:
        raise web.HTTPConflict(text=json.dumps({"error": "Asset is still referenced", "used_by": used_by}), content_type="application/json")
    path.unlink()
    manifest = _media_manifest(lesson_id)
    manifest["assets"].pop(filename, None)
    _save_media_manifest(lesson_id, manifest)
    _audit("LESSON_MEDIA_UNUSED_DELETED", lesson_id, filename=filename)
    return web.json_response({"ok": True, "deleted": filename})


async def assist_task(request: web.Request) -> web.Response:
    _authorized(request)
    data = await request.json()
    instruction = str(data.get("instruction") or "").strip()
    if not instruction:
        raise web.HTTPBadRequest(text=json.dumps({"error": "instruction is required"}), content_type="application/json")
    assets = [str(item) for item in (data.get("assets") or []) if isinstance(item, str)]
    result = await propose_task(instruction, assets)
    _audit("LESSON_TASK_PROPOSED", str(data.get("lesson_id") or "assistant"), task_type=result["proposal"].get("type"), source=result["source"])
    return web.json_response(result)


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


async def lesson_asset(request: web.Request) -> web.StreamResponse:
    """Preview only a file that the current lesson actually references."""

    _authorized(request)
    lesson_id = _lesson_id(request.match_info["lesson_id"])
    source = str(request.query.get("path") or "").strip().replace("\\", "/")
    safe = Path(source)
    lesson = _editable_lesson(lesson_id)
    if (
        lesson is None
        or not source
        or safe.is_absolute()
        or ".." in safe.parts
        or safe.suffix.lower() not in MEDIA_EXTENSIONS
        or source not in _json_strings(lesson)
    ):
        raise web.HTTPNotFound()
    candidates = [persistent_lessons_root() / lesson_id / safe, bundled_lessons_root() / lesson_id / safe]
    path = next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)
    if path is None:
        raise web.HTTPNotFound()
    response = web.FileResponse(path)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response




# ── PROMO CODES MANAGEMENT (Stage 7 & 8) ──────────────────────────────────
async def studio_list_promos(request: web.Request) -> web.Response:
    _authorized(request)
    from app.services.promo_codes import list_promo_codes
    async with _SessionLocal() as db:
        promos = await list_promo_codes(db, include_archived=False)
        return web.json_response({"promos": promos})


async def studio_create_promo(request: web.Request) -> web.Response:
    _authorized(request)
    data = await request.json()
    from app.services.promo_codes import create_promo_code
    async with _SessionLocal() as db:
        try:
            promo = await create_promo_code(db, data)
            return web.json_response({"ok": True, "id": promo.id, "code": promo.code}, status=201)
        except ValueError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)


async def studio_update_promo(request: web.Request) -> web.Response:
    _authorized(request)
    promo_id = int(request.match_info["promo_id"])
    data = await request.json()
    from app.services.promo_codes import update_promo_code
    async with _SessionLocal() as db:
        try:
            promo = await update_promo_code(db, promo_id, data)
            return web.json_response({"ok": True, "id": promo.id, "code": promo.code})
        except ValueError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)


async def studio_toggle_promo(request: web.Request) -> web.Response:
    _authorized(request)
    promo_id = int(request.match_info["promo_id"])
    from app.services.promo_codes import toggle_promo_code
    async with _SessionLocal() as db:
        try:
            active = await toggle_promo_code(db, promo_id)
            return web.json_response({"ok": True, "active": active})
        except ValueError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)


async def studio_delete_promo(request: web.Request) -> web.Response:
    _authorized(request)
    promo_id = int(request.match_info["promo_id"])
    from app.services.promo_codes import delete_promo_code
    async with _SessionLocal() as db:
        deleted = await delete_promo_code(db, promo_id, hard=False)
        return web.json_response({"ok": deleted})




# ── CLIENTS / CUSTOMERS CRM & TARIFF CMS (Stage 8, 9, 10) ─────────────────
async def admin_list_clients(request: web.Request) -> web.Response:
    _authorized(request)
    from app.db.models import Parent as _Parent, Child as _Child, Subscription as _Subscription, PromoCodeUsage as _PromoUsage
    from app.db.models import LessonSession as _LessonSession
    import io, csv

    q = str(request.query.get("q", "")).strip().lower()
    plan_filter = str(request.query.get("plan", "")).strip().lower()
    period_filter = str(request.query.get("period", "")).strip().upper()
    status_filter = str(request.query.get("status", "")).strip().upper()
    country_filter = str(request.query.get("country", "")).strip().lower()
    provider_filter = str(request.query.get("provider", "")).strip().lower()
    promo_filter = str(request.query.get("promo", "")).strip().upper()
    export_csv = request.query.get("export") == "csv"

    async with _SessionLocal() as db:
        parents = list((await db.scalars(_select(_Parent).order_by(_Parent.id.desc()))).all())
        clients = []

        for p in parents:
            full_name = f"{p.first_name or ''} {p.last_name or ''} {p.display_name or ''}".strip().lower()
            email = str(p.email or "").lower()
            phone = str(p.phone or "").lower()
            if q and (q not in full_name and q not in email and q not in phone):
                continue

            if country_filter and country_filter != str(p.country or "").lower():
                continue

            children = list((await db.scalars(_select(_Child).where(_Child.parent_id == p.id).order_by(_Child.id.asc()))).all())
            child_ids = [c.id for c in children]

            sub = None
            if child_ids:
                sub = await db.scalar(
                    _select(_Subscription).where(_Subscription.child_id.in_(child_ids)).order_by(_Subscription.id.desc()).limit(1)
                )

            promo_usages = list((await db.scalars(_select(_PromoUsage).where(_PromoUsage.parent_id == p.id))).all())
            latest_promo = promo_usages[0] if promo_usages else None

            is_owner = str(p.account_role or "").upper() == "OWNER"
            if is_owner:
                status = "OWNER"
            elif sub and sub.status in {"ACTIVE", "PAST_DUE", "CANCELLED", "TRIAL", "EXPIRED", "PAYMENT_FAILED"}:
                status = sub.status
            elif not p.email_verified:
                status = "EMAIL_NOT_VERIFIED"
            elif p.email_verified and not sub:
                status = "VERIFIED"
            else:
                status = str(sub.status if sub else p.onboarding_stage or "REGISTERED").upper()

            if status_filter and status != status_filter:
                continue

            current_plan = (sub.current_plan_id or sub.plan_id or "") if sub else ""
            if plan_filter and plan_filter not in current_plan.lower():
                continue

            billing_period = (sub.billing_period or "MONTH").upper() if sub else "MONTH"
            if period_filter and period_filter != billing_period:
                continue

            provider = (sub.payment_provider or "paypal").lower() if sub else ""
            if provider_filter and provider_filter != provider:
                continue

            if promo_filter:
                has_promo = any(promo_filter in str(u.payment_reference or "") for u in promo_usages)
                if not has_promo:
                    continue

            lessons_alloc = int(sub.lessons_allocated or 0) if sub else 0
            lessons_used = int(sub.lessons_used or 0) if sub else 0
            lessons_rem = max(0, lessons_alloc - lessons_used)

            plan_name_map = {
                "weekly1": "DOME Start", "weekly2": "DOME Smart", "weekly3": "DOME Plus", "weekly4": "DOME Max",
                "start": "DOME Start", "smart": "DOME Smart", "plus": "DOME Plus", "max": "DOME Max"
            }
            plan_title = plan_name_map.get(current_plan, current_plan or "Не выбран")

            client_record = {
                "id": p.id,
                "first_name": p.first_name or "",
                "last_name": p.last_name or "",
                "display_name": p.display_name or f"{p.first_name or ''} {p.last_name or ''}".strip() or p.email,
                "email": p.email,
                "email_verified": bool(p.email_verified),
                "phone": p.phone or "",
                "country": p.country or "",
                "preferred_language": p.preferred_language or "ru",
                "account_role": p.account_role or "STANDARD",
                "is_owner": is_owner,
                "status": status,
                "registered_at": p.created_at.isoformat() if p.created_at else None,
                "children": [
                    {
                        "id": c.id,
                        "name": c.display_name,
                        "age": c.age_years,
                        "native_language": c.native_language or "ru",
                        "target_language": c.target_language or "ru",
                    }
                    for c in children
                ],
                "subscription": {
                    "id": sub.id if sub else None,
                    "plan_id": current_plan,
                    "plan_title": plan_title,
                    "lessons_per_week": sub.lessons_per_week if sub else 0,
                    "lessons_per_month": (sub.lessons_per_week * 4) if sub else 0,
                    "billing_period": billing_period,
                    "price": sub.current_plan_price if sub and sub.current_plan_price else (sub.monthly_price if sub else 0.0),
                    "currency": sub.currency if sub else "EUR",
                    "status": sub.status if sub else "NO_SUBSCRIPTION",
                    "start_date": sub.started_at.isoformat() if sub and sub.started_at else None,
                    "next_billing_date": (sub.next_charge_at or sub.current_period_end).isoformat() if sub and (sub.next_charge_at or sub.current_period_end) else None,
                    "cancellation_date": sub.ended_at.isoformat() if sub and sub.ended_at else None,
                    "lessons_used": lessons_used,
                    "lessons_remaining": lessons_rem,
                    "payment_provider": sub.payment_provider if sub else "",
                    "promo_code": latest_promo.payment_reference if latest_promo else "",
                    "discount": latest_promo.discount_amount if latest_promo else 0.0,
                    "grandfathered_price": bool(sub.current_plan_price and sub.current_plan_price < (sub.monthly_price or 999)) if sub else False,
                } if sub else None,
            }
            clients.append(client_record)

        if export_csv:
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([
                "ID", "Имя", "Фамилия", "Email", "Email Verified", "Телефон", "Страна",
                "Статус", "Роль", "Дата регистрации", "Дети", "Тариф", "Период", "Цена",
                "Уроков использовано", "Уроков осталось", "Провайдер", "Дата следующего списания"
            ])
            for c in clients:
                sub_info = c["subscription"] or {}
                kids = "; ".join([f"{k['name']} ({k['age']} лет)" for k in c["children"]])
                writer.writerow([
                    c["id"], c["first_name"], c["last_name"], c["email"], "Да" if c["email_verified"] else "Нет",
                    c["phone"], c["country"], c["status"], c["account_role"], c["registered_at"], kids,
                    sub_info.get("plan_title", "—"), sub_info.get("billing_period", "—"), sub_info.get("price", "—"),
                    sub_info.get("lessons_used", "—"), sub_info.get("lessons_remaining", "—"),
                    sub_info.get("payment_provider", "—"), sub_info.get("next_billing_date", "—")
                ])
            csv_content = output.getvalue()
            return web.Response(
                body=csv_content.encode("utf-8-sig"),
                content_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=dome_clients.csv"}
            )

        return web.json_response({"clients": clients, "count": len(clients)})


async def admin_get_client_card(request: web.Request) -> web.Response:
    _authorized(request)
    from app.db.models import (
        Parent as _Parent, Child as _Child, Subscription as _Subscription,
        PromoCodeUsage as _PromoUsage, LessonSession as _LessonSession,
        PaymentWebhookEvent as _PaymentEvent, UserConsent as _UserConsent,
        SubscriptionAuditEvent as _SubAudit
    )
    parent_id = int(request.match_info["parent_id"])

    async with _SessionLocal() as db:
        parent = await db.get(_Parent, parent_id)
        if not parent:
            raise web.HTTPNotFound(text=json.dumps({"error": "Клиент не найден"}), content_type="application/json")

        children = list((await db.scalars(_select(_Child).where(_Child.parent_id == parent.id).order_by(_Child.id.asc()))).all())
        child_ids = [c.id for c in children]

        # All subscriptions history
        subs = []
        sessions = []
        if child_ids:
            subs = list((await db.scalars(_select(_Subscription).where(_Subscription.child_id.in_(child_ids)).order_by(_Subscription.id.desc()))).all())
            sessions = list((await db.scalars(_select(_LessonSession).where(_LessonSession.child_id.in_(child_ids)).order_by(_LessonSession.id.desc()).limit(100))).all())

        # Payments / webhooks
        payments = list((await db.scalars(_select(_PaymentEvent).order_by(_PaymentEvent.id.desc()).limit(50))).all())

        # Promos
        promos = list((await db.scalars(_select(_PromoUsage).where(_PromoUsage.parent_id == parent.id).order_by(_PromoUsage.used_at.desc()))).all())

        # Consents
        consents = list((await db.scalars(_select(_UserConsent).where(_UserConsent.parent_id == parent.id).order_by(_UserConsent.accepted_at.desc()))).all())

        # Audit
        sub_ids = [s.id for s in subs]
        audits = []
        if sub_ids:
            audits = list((await db.scalars(_select(_SubAudit).where(_SubAudit.subscription_id.in_(sub_ids)).order_by(_SubAudit.id.desc()))).all())

        from app.services.consents import DOCUMENT_METADATA
        return web.json_response({
            "parent": {
                "id": parent.id,
                "first_name": parent.first_name or "",
                "last_name": parent.last_name or "",
                "display_name": parent.display_name or f"{parent.first_name or ''} {parent.last_name or ''}".strip() or parent.email,
                "email": parent.email,
                "email_verified": bool(parent.email_verified),
                "phone": parent.phone or "",
                "country": parent.country or "",
                "preferred_language": parent.preferred_language or "ru",
                "account_role": parent.account_role or "STANDARD",
                "is_owner": str(parent.account_role or "").upper() == "OWNER",
                "onboarding_stage": parent.onboarding_stage or "REGISTERED",
                "verification_status": parent.verification_status or "UNVERIFIED",
                "marketing_opt_in": bool(parent.marketing_opt_in),
                "registered_at": parent.created_at.isoformat() if parent.created_at else None,
            },
            "children": [
                {
                    "id": c.id,
                    "name": c.display_name,
                    "age": c.age_years,
                    "native_language": c.native_language or "ru",
                    "target_language": c.target_language or "ru",
                    "level": c.language_level or "PRE_A1",
                    "active_hero_id": c.active_character_id,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                }
                for c in children
            ],
            "subscriptions": [
                {
                    "id": s.id,
                    "plan_id": s.current_plan_id or s.plan_id,
                    "billing_period": s.billing_period,
                    "monthly_price": s.monthly_price,
                    "current_plan_price": s.current_plan_price,
                    "currency": s.currency,
                    "status": s.status,
                    "started_at": s.started_at.isoformat() if s.started_at else None,
                    "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                    "next_charge_at": s.next_charge_at.isoformat() if s.next_charge_at else None,
                    "lessons_allocated": s.lessons_allocated,
                    "lessons_used": s.lessons_used,
                    "payment_provider": s.payment_provider,
                    "provider_subscription_id": s.provider_subscription_id,
                }
                for s in subs
            ],
            "lessons": [
                {
                    "id": sess.id,
                    "child_id": sess.child_id,
                    "lesson_id": sess.lesson_id,
                    "status": sess.status,
                    "current_step": sess.current_step,
                    "started_at": sess.started_at.isoformat() if sess.started_at else None,
                    "completed_at": sess.completed_at.isoformat() if sess.completed_at else None,
                }
                for sess in sessions
            ],
            "payments": [
                {
                    "id": p.id,
                    "provider": p.provider,
                    "event_id": p.event_id,
                    "event_type": p.event_type,
                    "processed_at": p.processed_at.isoformat() if p.processed_at else None,
                }
                for p in payments
            ],
            "promos": [
                {
                    "id": pr.id,
                    "plan_id": pr.plan_id,
                    "discount_amount": pr.discount_amount,
                    "original_price": pr.original_price,
                    "final_price": pr.final_price,
                    "payment_reference": pr.payment_reference,
                    "used_at": pr.used_at.isoformat() if pr.used_at else None,
                }
                for pr in promos
            ],
            "consents": [
                {
                    "id": con.id,
                    "document_type": con.document_type,
                    "title": DOCUMENT_METADATA.get(con.document_type, {}).get("title", con.document_type),
                    "document_version": con.document_version,
                    "accepted": con.accepted,
                    "accepted_at": con.accepted_at.isoformat() if con.accepted_at else None,
                    "locale": con.locale,
                    "ip_address": con.ip_address,
                }
                for con in consents
            ],
            "audit": [
                {
                    "id": a.id,
                    "action": a.action,
                    "actor": a.actor,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                    "details": json.loads(a.details_json or "{}"),
                }
                for a in audits
            ],
        })


async def admin_list_tariffs(request: web.Request) -> web.Response:
    _authorized(request)
    from app.services.tariff_plans import list_all_tariffs
    async with _SessionLocal() as db:
        tariffs = await list_all_tariffs(db)
        return web.json_response({"tariffs": tariffs})


async def admin_update_tariff(request: web.Request) -> web.Response:
    _authorized(request)
    plan_id = str(request.match_info["plan_id"]).strip()
    data = await request.json()
    from app.services.tariff_plans import update_tariff
    async with _SessionLocal() as db:
        try:
            tariff = await update_tariff(db, plan_id, data)
            from app.services.tariff_plans import _tariff_dict
            return web.json_response({"ok": True, "tariff": _tariff_dict(tariff)})
        except ValueError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)


def register_content_studio_routes(app: web.Application) -> None:
    app.router.add_get("/content-studio", studio_page)
    app.router.add_get("/content-studio/{filename}", studio_static)
    app.router.add_get("/api/studio/status", studio_status)
    app.router.add_get("/api/studio/promos", studio_list_promos)
    app.router.add_post("/api/studio/promos", studio_create_promo)
    app.router.add_put("/api/studio/promos/{promo_id}", studio_update_promo)
    app.router.add_post("/api/studio/promos/{promo_id}/toggle", studio_toggle_promo)
    app.router.add_delete("/api/studio/promos/{promo_id}", studio_delete_promo)
    app.router.add_get("/api/studio/admin/clients", admin_list_clients)
    app.router.add_get("/api/studio/admin/clients/{parent_id}", admin_get_client_card)
    app.router.add_get("/api/studio/admin/tariffs", admin_list_tariffs)
    app.router.add_put("/api/studio/admin/tariffs/{plan_id}", admin_update_tariff)
    app.router.add_get("/api/studio/lessons", list_lessons)
    app.router.add_post("/api/studio/lessons", create_lesson)
    app.router.add_post("/api/studio/lessons/reorder", reorder_lessons)
    app.router.add_get("/api/studio/lessons/{lesson_id}", get_lesson)
    app.router.add_put("/api/studio/lessons/{lesson_id}", save_lesson)
    app.router.add_delete("/api/studio/lessons/{lesson_id}", delete_lesson)
    app.router.add_post("/api/studio/lessons/{lesson_id}/duplicate", duplicate_lesson)
    app.router.add_post("/api/studio/lessons/{lesson_id}/validate", validate_lesson)
    app.router.add_get("/api/studio/lessons/{lesson_id}/preview", preview_lesson)
    app.router.add_get("/api/studio/lessons/{lesson_id}/asset", lesson_asset)
    app.router.add_post("/api/studio/lessons/{lesson_id}/publish", publish_lesson)
    app.router.add_post("/api/studio/lessons/{lesson_id}/archive", archive_lesson)
    app.router.add_post("/api/studio/lessons/{lesson_id}/rollback", rollback_lesson)
    app.router.add_post("/api/studio/assist/task", assist_task)
    app.router.add_post("/api/studio/lessons/{lesson_id}/media", upload_media)
    app.router.add_patch("/api/studio/lessons/{lesson_id}/media/{filename}", rename_media)
    app.router.add_post("/api/studio/lessons/{lesson_id}/media/{filename}/replace", replace_media)
    app.router.add_delete("/api/studio/lessons/{lesson_id}/media/{filename}", delete_unused_media)
    app.router.add_get("/api/studio/lessons/{lesson_id}/media/{filename}", studio_media)
    # Admin / user management
    app.router.add_get("/api/studio/admin/users", admin_list_users)
    app.router.add_get("/api/studio/admin/users/{parent_id}", admin_get_user)
    app.router.add_post("/api/studio/admin/users/{parent_id}/role", admin_set_role)
    app.router.add_post("/api/studio/admin/owner-apply", admin_apply_owner_emails)
    # Course CMS
    app.router.add_get("/api/studio/courses", studio_list_courses)
    app.router.add_post("/api/studio/courses", studio_create_course)
    app.router.add_post("/api/studio/courses/reorder", studio_reorder_courses)
    app.router.add_get("/api/studio/courses/{course_id}", studio_get_course)
    app.router.add_put("/api/studio/courses/{course_id}", studio_update_course)
    app.router.add_post("/api/studio/courses/{course_id}/duplicate", studio_duplicate_course)
    app.router.add_post("/api/studio/courses/{course_id}/publish", studio_publish_course)
    app.router.add_post("/api/studio/courses/{course_id}/archive", studio_archive_course)
    app.router.add_delete("/api/studio/courses/{course_id}", studio_delete_course)
    app.router.add_post("/api/studio/courses/{course_id}/cover", studio_upload_course_cover)

    # Lesson CMS movement
    app.router.add_post("/api/studio/lessons/{lesson_id}/move", studio_move_lesson)

    # Homework CMS
    app.router.add_get("/api/studio/lessons/{lesson_id}/homework", studio_get_homework)
    app.router.add_put("/api/studio/lessons/{lesson_id}/homework", studio_save_homework)
    app.router.add_post("/api/studio/lessons/{lesson_id}/homework/publish", studio_publish_homework)
    app.router.add_post("/api/studio/lessons/{lesson_id}/homework/duplicate", studio_duplicate_homework)
    app.router.add_post("/api/studio/lessons/{lesson_id}/homework/move", studio_move_homework)
    app.router.add_delete("/api/studio/lessons/{lesson_id}/homework", studio_delete_homework)



# ---------------------------------------------------------------------------
# Admin / user-management endpoints (all require Content Studio token)
# ---------------------------------------------------------------------------

from app.db.models import Child as _Child, LessonEntitlement as _LessonEntitlement  # noqa: E402
from app.db.session import SessionLocal as _SessionLocal  # noqa: E402
from app.services.qa_access import (  # noqa: E402
    OWNER_ROLE as _OWNER_ROLE,
    QA_TEST_ROLE as _QA_TEST_ROLE,
    ADMIN_ROLE as _ADMIN_ROLE,
    STANDARD_ROLE as _STANDARD_ROLE,
)
from sqlalchemy import select as _select  # noqa: E402

_ALLOWED_ROLES = {_STANDARD_ROLE, _QA_TEST_ROLE, _ADMIN_ROLE, _OWNER_ROLE}

# Owner emails that always receive OWNER role — single source of truth.
# Edit this list and POST /api/studio/admin/owner-apply to apply changes live.
OWNER_EMAIL_ALLOWLIST: list[str] = [
    "krisriskrisris@gmail.com",
]


def _parent_payload(parent, children) -> dict:
    return {
        "id": parent.id,
        "email": parent.email,
        "display_name": parent.display_name,
        "account_role": str(parent.account_role or _STANDARD_ROLE),
        "email_verified": bool(parent.email_verified),
        "created_at": parent.created_at.isoformat() if parent.created_at else None,
        "children": [
            {
                "id": c.id,
                "display_name": c.display_name,
                "age_years": c.age_years,
                "language_level": c.language_level,
                "target_language": c.target_language,
                "native_language": c.native_language,
            }
            for c in children
        ],
    }


async def admin_list_users(request: web.Request) -> web.Response:
    """GET /api/studio/admin/users?email=...&role=...&limit=50"""
    _authorized(request)
    from app.db.models import Parent as _Parent
    email_q = str(request.query.get("email", "")).strip().lower()
    role_q = str(request.query.get("role", "")).strip().upper() or None
    limit = min(int(request.query.get("limit", 50)), 200)
    async with _SessionLocal() as db:
        stmt = _select(_Parent).order_by(_Parent.id.desc()).limit(limit)
        parents = list(await db.scalars(stmt))
        results = []
        for p in parents:
            if email_q and email_q not in str(p.email or "").lower():
                continue
            if role_q and str(p.account_role or _STANDARD_ROLE).upper() != role_q:
                continue
            children = list(await db.scalars(_select(_Child).where(_Child.parent_id == p.id)))
            results.append(_parent_payload(p, children))
    return web.json_response({"users": results, "count": len(results)})


async def admin_get_user(request: web.Request) -> web.Response:
    """GET /api/studio/admin/users/{parent_id}"""
    _authorized(request)
    from app.db.models import Parent as _Parent
    pid = int(request.match_info["parent_id"])
    async with _SessionLocal() as db:
        p = await db.get(_Parent, pid)
        if p is None:
            raise web.HTTPNotFound(text=json.dumps({"error": "User not found"}), content_type="application/json")
        children = list(await db.scalars(_select(_Child).where(_Child.parent_id == p.id)))
        # Entitlement summary per child
        ents = []
        for c in children:
            rows = list(await db.scalars(_select(_LessonEntitlement).where(_LessonEntitlement.child_id == c.id)))
            for e in rows:
                ents.append({
                    "child_id": c.id,
                    "lesson_id": e.lesson_id,
                    "course_id": e.course_id,
                    "completed_runs": e.completed_runs,
                    "max_completed_runs": e.max_completed_runs,
                    "status": e.status,
                })
        payload = _parent_payload(p, children)
        payload["entitlements"] = ents
    return web.json_response(payload)


async def admin_set_role(request: web.Request) -> web.Response:
    """POST /api/studio/admin/users/{parent_id}/role  body: {"role": "OWNER"|"ADMIN"|"QA_TEST"|"STANDARD"}"""
    _authorized(request)
    from app.db.models import Parent as _Parent
    pid = int(request.match_info["parent_id"])
    data = await request.json()
    new_role = str(data.get("role", "")).strip().upper()
    if new_role not in _ALLOWED_ROLES:
        raise web.HTTPBadRequest(
            text=json.dumps({"error": f"role must be one of {sorted(_ALLOWED_ROLES)}"}),
            content_type="application/json",
        )
    async with _SessionLocal() as db:
        p = await db.get(_Parent, pid)
        if p is None:
            raise web.HTTPNotFound(text=json.dumps({"error": "User not found"}), content_type="application/json")
        old_role = str(p.account_role or _STANDARD_ROLE)
        p.account_role = new_role
        await db.commit()
        log.info("ADMIN_SET_ROLE parent_id=%s email=%s old=%s new=%s", pid, p.email, old_role, new_role)
    return web.json_response({"ok": True, "parent_id": pid, "old_role": old_role, "new_role": new_role})


async def admin_apply_owner_emails(request: web.Request) -> web.Response:
    """POST /api/studio/admin/owner-apply — apply OWNER_EMAIL_ALLOWLIST to the live DB.

    Safe to call repeatedly (idempotent).  Returns a summary of changes.
    """
    _authorized(request)
    from app.db.models import Parent as _Parent
    applied = []
    skipped = []
    async with _SessionLocal() as db:
        for raw in OWNER_EMAIL_ALLOWLIST:
            email = raw.strip().lower()
            p = await db.scalar(_select(_Parent).where(_Parent.email == email))
            if p is None:
                skipped.append({"email": email, "reason": "not_found"})
                continue
            old = str(p.account_role or _STANDARD_ROLE)
            if old == _OWNER_ROLE:
                skipped.append({"email": email, "reason": "already_owner", "parent_id": p.id})
                continue
            p.account_role = _OWNER_ROLE
            applied.append({"email": email, "parent_id": p.id, "old_role": old})
            log.info("OWNER_APPLY email=%s parent_id=%s old_role=%s", email, p.id, old)
        await db.commit()
    return web.json_response({"ok": True, "applied": applied, "skipped": skipped})


# ---------------------------------------------------------------------------
# Course and Homework CMS Endpoints
# ---------------------------------------------------------------------------

from app.services.course_catalog import (
    list_courses as _catalog_list_courses,
    load_course as _catalog_load_course,
    save_course as _catalog_save_course,
    create_course as _catalog_create_course,
    update_course as _catalog_update_course,
    duplicate_course as _catalog_duplicate_course,
    archive_course as _catalog_archive_course,
    delete_course as _catalog_delete_course,
    reorder_courses as _catalog_reorder_courses,
    add_lesson_to_course as _catalog_add_lesson,
    remove_lesson_from_course as _catalog_remove_lesson,
)
from app.services.homework_catalog import (
    load_homework as _catalog_load_homework,
    save_homework as _catalog_save_homework,
    duplicate_homework as _catalog_duplicate_homework,
    move_homework as _catalog_move_homework,
    archive_homework as _catalog_archive_homework,
)


async def studio_list_courses(request: web.Request) -> web.Response:
    _authorized(request)
    courses = _catalog_list_courses(for_client=False)
    payload = []
    for c in courses:
        data = c.model_dump()
        data["lesson_count"] = len(c.lesson_ids)
        payload.append(data)
    return web.json_response({"courses": payload})


async def studio_create_course(request: web.Request) -> web.Response:
    _authorized(request)
    data = await request.json()
    cid = str(data.get("course_id") or data.get("id") or "").strip().lower()
    title = str(data.get("title") or "").strip()
    if not cid or not title:
        raise web.HTTPBadRequest(text=json.dumps({"error": "course_id and title are required"}), content_type="application/json")
    try:
        manifest = _catalog_create_course(
            course_id=cid,
            title=title,
            description=str(data.get("description") or ""),
            cover_image=str(data.get("cover_image") or ""),
            order=int(data.get("order") or 1),
            active=bool(data.get("active", True)),
            status=str(data.get("status") or "draft"),
        )
        return web.json_response({"ok": True, "course": manifest.model_dump()})
    except ValueError as e:
        raise web.HTTPBadRequest(text=json.dumps({"error": str(e)}), content_type="application/json")


async def studio_get_course(request: web.Request) -> web.Response:
    _authorized(request)
    cid = request.match_info["course_id"].strip().lower()
    try:
        manifest = _catalog_load_course(cid)
        return web.json_response({"ok": True, "course": manifest.model_dump()})
    except FileNotFoundError:
        raise web.HTTPNotFound(text=json.dumps({"error": f"Course not found: {cid}"}), content_type="application/json")


async def studio_update_course(request: web.Request) -> web.Response:
    _authorized(request)
    cid = request.match_info["course_id"].strip().lower()
    data = await request.json()
    try:
        manifest = _catalog_update_course(cid, data)
        return web.json_response({"ok": True, "course": manifest.model_dump()})
    except FileNotFoundError:
        raise web.HTTPNotFound(text=json.dumps({"error": f"Course not found: {cid}"}), content_type="application/json")


async def studio_duplicate_course(request: web.Request) -> web.Response:
    _authorized(request)
    cid = request.match_info["course_id"].strip().lower()
    data = await request.json()
    new_id = str(data.get("new_course_id") or f"{cid}_copy").strip().lower()
    new_title = str(data.get("new_title") or "").strip()
    try:
        manifest = _catalog_duplicate_course(cid, new_id, new_title)
        return web.json_response({"ok": True, "course": manifest.model_dump()})
    except Exception as e:
        raise web.HTTPBadRequest(text=json.dumps({"error": str(e)}), content_type="application/json")


async def studio_publish_course(request: web.Request) -> web.Response:
    _authorized(request)
    cid = request.match_info["course_id"].strip().lower()
    manifest = _catalog_update_course(cid, {"status": "published", "active": True, "locked": False})
    return web.json_response({"ok": True, "course": manifest.model_dump()})


async def studio_archive_course(request: web.Request) -> web.Response:
    _authorized(request)
    cid = request.match_info["course_id"].strip().lower()
    manifest = _catalog_archive_course(cid)
    return web.json_response({"ok": True, "course": manifest.model_dump()})


async def studio_delete_course(request: web.Request) -> web.Response:
    _authorized(request)
    cid = request.match_info["course_id"].strip().lower()
    result = _catalog_delete_course(cid)
    return web.json_response({"ok": True, **result})


async def studio_reorder_courses(request: web.Request) -> web.Response:
    _authorized(request)
    data = await request.json()
    ordered_ids = data.get("order") or []
    updated = _catalog_reorder_courses(ordered_ids)
    return web.json_response({"ok": True, "courses": [c.model_dump() for c in updated]})


async def studio_upload_course_cover(request: web.Request) -> web.Response:
    _authorized(request)
    cid = request.match_info["course_id"].strip().lower()
    reader = await request.multipart()
    field = await reader.next()
    if field is None or not field.filename:
        raise web.HTTPBadRequest(text=json.dumps({"error": "No file uploaded"}), content_type="application/json")
    ext = Path(field.filename).suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise web.HTTPBadRequest(text=json.dumps({"error": "Invalid image format"}), content_type="application/json")

    safe_name = f"cover{ext}"
    target_dir = settings.storage_root / "courses" / cid
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / safe_name
    with open(target_path, "wb") as f:
        while True:
            chunk = await field.read_chunk()
            if not chunk:
                break
            f.write(chunk)

    rel_url = f"/api/studio/courses/{cid}/cover/{safe_name}"
    _catalog_update_course(cid, {"cover_image": rel_url})
    return web.json_response({"ok": True, "cover_image": rel_url})


async def studio_move_lesson(request: web.Request) -> web.Response:
    _authorized(request)
    lid = request.match_info["lesson_id"].strip().lower()
    data = await request.json()
    target_course_id = str(data.get("target_course_id") or "").strip().lower()
    if not target_course_id:
        raise web.HTTPBadRequest(text=json.dumps({"error": "target_course_id is required"}), content_type="application/json")

    # 1. Update lesson data
    lesson_data = load_authored_lesson(lid)
    old_course_id = str(lesson_data.get("course_id") or "conversation").strip().lower()
    lesson_data["course_id"] = target_course_id

    # Save lesson
    from app.services.authored_content import ensure_persistent_lesson
    p_path = ensure_persistent_lesson(lid)
    p_path.write_text(json.dumps(lesson_data, ensure_ascii=False, indent=2) + "\n", "utf-8")

    # 2. Update courses
    try:
        _catalog_remove_lesson(old_course_id, lid)
    except Exception:
        pass
    try:
        _catalog_add_lesson(target_course_id, lid)
    except Exception:
        pass

    return web.json_response({"ok": True, "lesson_id": lid, "old_course_id": old_course_id, "new_course_id": target_course_id})


# Homework Endpoints

async def studio_get_homework(request: web.Request) -> web.Response:
    _authorized(request)
    lid = request.match_info["lesson_id"].strip().lower()
    hw = _catalog_load_homework(lid)
    return web.json_response({"ok": True, "homework": hw.model_dump()})


async def studio_save_homework(request: web.Request) -> web.Response:
    _authorized(request)
    lid = request.match_info["lesson_id"].strip().lower()
    data = await request.json()
    hw_data = data.get("homework") or data
    hw_data["lesson_id"] = lid

    saved_path = _catalog_save_homework(hw_data)
    loaded = _catalog_load_homework(lid)

    # Sync back to lesson manifest for backward compatibility
    try:
        from app.services.authored_content import load_authored_lesson, ensure_persistent_lesson
        ld = load_authored_lesson(lid)
        ld["homework"] = {
            "enabled": loaded.enabled,
            "optional": loaded.optional,
            "available_policy": loaded.available_policy,
            "requires_completion_for_next_lesson": loaded.requires_completion_for_next_lesson,
            "title": loaded.title,
            "description": loaded.description,
            "duration_minutes": loaded.duration_minutes,
            "slides": loaded.slides,
        }
        p_path = ensure_persistent_lesson(lid)
        p_path.write_text(json.dumps(ld, ensure_ascii=False, indent=2) + "\n", "utf-8")
    except Exception as e:
        import logging
        logging.getLogger("dome.content_studio").warning("Could not sync homework back to lesson: %s", e)

    return web.json_response({"ok": True, "homework": loaded.model_dump()})


async def studio_publish_homework(request: web.Request) -> web.Response:
    _authorized(request)
    lid = request.match_info["lesson_id"].strip().lower()
    hw = _catalog_load_homework(lid)
    hw.status = "published"
    hw.enabled = True
    _catalog_save_homework(hw)
    return web.json_response({"ok": True, "homework": hw.model_dump()})


async def studio_duplicate_homework(request: web.Request) -> web.Response:
    _authorized(request)
    lid = request.match_info["lesson_id"].strip().lower()
    data = await request.json()
    target_lid = str(data.get("target_lesson_id") or "").strip().lower()
    if not target_lid:
        raise web.HTTPBadRequest(text=json.dumps({"error": "target_lesson_id is required"}), content_type="application/json")
    duplicated = _catalog_duplicate_homework(lid, target_lid)
    return web.json_response({"ok": True, "homework": duplicated.model_dump()})


async def studio_move_homework(request: web.Request) -> web.Response:
    _authorized(request)
    lid = request.match_info["lesson_id"].strip().lower()
    data = await request.json()
    target_lid = str(data.get("target_lesson_id") or "").strip().lower()
    if not target_lid:
        raise web.HTTPBadRequest(text=json.dumps({"error": "target_lesson_id is required"}), content_type="application/json")
    moved = _catalog_move_homework(lid, target_lid)
    return web.json_response({"ok": True, "homework": moved.model_dump()})


async def studio_delete_homework(request: web.Request) -> web.Response:
    _authorized(request)
    lid = request.match_info["lesson_id"].strip().lower()
    hw = _catalog_archive_homework(lid)
    return web.json_response({"ok": True, "homework": hw.model_dump()})
