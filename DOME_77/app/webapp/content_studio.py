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
    if filename not in {"content_studio.css", "content_studio_extensions.css", "content_studio.js"}:
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


def register_content_studio_routes(app: web.Application) -> None:
    app.router.add_get("/content-studio", studio_page)
    app.router.add_get("/content-studio/{filename}", studio_static)
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
