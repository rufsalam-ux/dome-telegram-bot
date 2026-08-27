from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import settings

# Universal DOME activity vocabulary. Several pedagogical types may share a
# low-level widget, but every published type must have executable config.
CONTENT_TYPE_ALIASES = {
    # Human/admin-friendly names. Runtime keeps one canonical implementation per mechanic.
    "true_false": "choice",
    "pronunciation": "repeat",
    "tap_to_hear": "tap_sound",
    "sequencing": "sequence",
    "letter_builder": "word_builder",
    "mini_dictation": "dictation",
    "highlight_text": "find_in_text",
    "sound_to_letter_image": "listen_choose",
    "role_reading": "read_roles",
    "reading_aloud": "read_aloud",
    "echo_read": "echo_reading",
    "joint_reading": "shared_reading",
    # Content Studio task-template vocabulary. These aliases configure stable
    # runtime mechanics; they never inject executable lesson code.
    "info": "passive",
    "listen": "passive",
    "multiple_choice": "choice",
    "ordering": "sequence",
    "image_hotspots": "interactive_scene",
    "repeat_phrase": "repeat",
    "open_dialogue": "dialogue",
    "required_movie_phrase": "voice_answer",
}

SUPPORTED_CONTENT_TYPES = {
    "passive", "voice_answer", "choice", "drag_drop", "visual_pack", "memory", "drawing", "video", "roleplay", "mini_game",
    "letter_path", "trace", "tap_sound", "match_visible", "tap_select", "multi_select", "matching", "sorting", "sequence",
    "word_builder", "syllable_builder", "sentence_builder", "fill_gap", "odd_one_out", "sound_position", "syllable_split",
    "find_in_text", "connect_lines", "handwriting_screen", "draw", "coloring", "maze", "dictation", "listen_choose",
    "read_aloud", "read_roles", "echo_reading", "shared_reading", "comprehension", "retell", "continue_story", "dialogue", "repeat", "speak",
    "video_pause_question", "interactive_scene", "real_world_find", "photo_task", "physical_action", "mood_choice", "puzzle",
    # Existing DOME 77 Lesson 1 mechanics remain first-class data during its
    # lossless Studio migration. The mobile runtime already executes them.
    "guided_speaking", "presentation", "card_selector", "choice_card", "guided_scene", "transition",
    "drag_and_drop", "animal_compare", "animal_riddle", "personal_travel_story",
} | set(CONTENT_TYPE_ALIASES)

SUPPORTED_MEDIA_TYPES = {"image", "video", "animation", "youtube", "audio"}
PUBLICATION_STATUSES = {"DRAFT", "PUBLISHED", "ARCHIVED"}


def canonical_content_type(kind: str | None) -> str:
    raw=str(kind or "passive").strip().lower()
    return CONTENT_TYPE_ALIASES.get(raw,raw)


def publication_status(data: dict[str, Any]) -> str:
    """Return one lifecycle value while preserving older authored lessons."""

    explicit = str(data.get("status") or data.get("import_status") or "").strip().upper()
    if explicit in PUBLICATION_STATUSES:
        return explicit
    return "PUBLISHED" if bool(data.get("active", True)) else "DRAFT"


def normalized_media_sequence(slide: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize legacy single-media fields into the universal media contract.

    Content remains data-only. Mobile, Telegram and future renderers consume the
    same ordered descriptors instead of teaching each new lesson to the client.
    """

    configured = slide.get("media_sequence")
    if isinstance(configured, list) and configured:
        return [dict(item) for item in configured if isinstance(item, dict)]
    if slide.get("image") or slide.get("image_file"):
        return [{
            "id": "visual",
            "type": "image",
            "src": str(slide.get("image") or slide.get("image_file")),
        }]
    if slide.get("video_file") or slide.get("video_url"):
        value = str(slide.get("video_file") or slide.get("video_url"))
        return [{
            "id": "video",
            "type": "youtube" if "youtu" in value.lower() else "video",
            "src": value,
        }]
    if slide.get("audio_file") or slide.get("audio_url"):
        return [{
            "id": "audio",
            "type": "audio",
            "src": str(slide.get("audio_file") or slide.get("audio_url")),
        }]
    return []


def _validate_media_sequence(slide: dict[str, Any], label: str) -> list[str]:
    raw = slide.get("media_sequence")
    if raw is None:
        return []
    if not isinstance(raw, list) or not raw:
        return [f"{label}: media_sequence must be a non-empty list"]
    errors: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, 1):
        media_label = f"{label} media {index}"
        if not isinstance(item, dict):
            errors.append(f"{media_label}: descriptor must be an object")
            continue
        media_id = str(item.get("id") or "").strip()
        kind = str(item.get("type") or "").strip().lower()
        source = str(item.get("src") or item.get("url") or "").strip()
        if not media_id:
            errors.append(f"{media_label}: missing id")
        elif media_id in seen:
            errors.append(f"{media_label}: duplicate id {media_id}")
        seen.add(media_id)
        if kind not in SUPPORTED_MEDIA_TYPES:
            errors.append(f"{media_label}: unsupported media type {kind or '<empty>'}")
        if kind in {"image", "video", "youtube", "audio"} and not source:
            errors.append(f"{media_label}: {kind} needs src/url")
        if kind == "animation" and not (source or str(item.get("animation_id") or "").strip()):
            errors.append(f"{media_label}: animation needs src or animation_id")
        if kind == "youtube" and source and not any(host in source.lower() for host in ("youtube.com", "youtu.be")):
            errors.append(f"{media_label}: youtube src must be a YouTube URL")
    return errors


def _validate_pre_slide_video(slide: dict[str, Any], label: str) -> list[str]:
    raw=slide.get("preSlideVideo",slide.get("pre_slide_video"))
    if raw is None:return []
    if not isinstance(raw,dict):return [f"{label}: preSlideVideo must be an object"]
    errors=[];uri=str(raw.get("uri") or raw.get("src") or raw.get("url") or "").strip()
    if raw.get("enabled") is not False and not uri:errors.append(f"{label}: enabled preSlideVideo needs uri/src/url")
    policy=str(raw.get("showPolicy") or raw.get("show_policy") or "once_per_attempt")
    if policy not in {"every_attempt","once_per_attempt","once_ever"}:errors.append(f"{label}: unsupported preSlideVideo showPolicy {policy}")
    for key in ("enabled","skippable","autoplay"):
        if key in raw and not isinstance(raw[key],bool):errors.append(f"{label}: preSlideVideo {key} must be boolean")
    return errors


def bundled_lessons_root() -> Path:
    return settings.content_root / "lessons"


def persistent_lessons_root() -> Path:
    p = settings.storage_root / "authored-content" / "lessons"
    p.mkdir(parents=True, exist_ok=True)
    return p


def lesson_roots() -> list[Path]:
    return [persistent_lessons_root(), bundled_lessons_root()]


def lesson_dir(lesson_id: str) -> Path:
    for root in lesson_roots():
        p = root / lesson_id
        if (p / "lesson.json").exists():
            return p
    return bundled_lessons_root() / lesson_id


def load_authored_lesson(lesson_id: str) -> dict[str, Any] | None:
    path = lesson_dir(lesson_id) / "lesson.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception:
        return None
    if str(data.get("engine") or "").lower() != "content_v1":
        return None
    data = dict(data)
    data.setdefault("lesson_id", lesson_id)
    data.setdefault("active", True)
    data.setdefault("status", publication_status(data).lower())
    data.setdefault("order", 9999)
    data.setdefault("slides", [])
    return data


def load_homework(lesson_id: str) -> dict[str, Any] | None:
    path = lesson_dir(lesson_id) / "homework.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception:
        return None
    data = dict(data)
    data.setdefault("title", "Домашнее задание")
    data.setdefault("slides", [])
    return data


def discover_course_lessons(course_id: str) -> list[str]:
    found: dict[str, tuple[int, int]] = {}
    for priority, root in enumerate(lesson_roots()):
        if not root.exists():
            continue
        for folder in root.iterdir():
            if not folder.is_dir() or folder.name in found:
                continue
            path = folder / "lesson.json"
            try:
                raw = json.loads(path.read_text("utf-8"))
            except Exception:
                continue
            if raw.get("course_id") == course_id and raw.get("active", True) and publication_status(raw) == "PUBLISHED":
                found[str(raw.get("lesson_id") or folder.name)] = (int(raw.get("order", 9999) or 9999), priority)
    return [lid for lid, _ in sorted(found.items(), key=lambda kv: (kv[1][0], kv[1][1], kv[0]))]


def augment_course(course: dict[str, Any]) -> dict[str, Any]:
    out = dict(course)
    configured = [str(x) for x in (out.get("lesson_ids") or [])]
    discovered = discover_course_lessons(str(out.get("course_id") or ""))
    merged: list[str] = []
    for lid in configured + discovered:
        if lid not in merged:
            merged.append(lid)
    authored_order = {lid: i for i, lid in enumerate(discovered)}
    if discovered:
        merged.sort(key=lambda lid: (0, authored_order[lid]) if lid in authored_order else (1, configured.index(lid) if lid in configured else 9999))
    out["lesson_ids"] = merged
    return out


def _indices(slide: dict[str, Any]) -> list[int]:
    raw = slide.get("correct_indices")
    if raw is None and slide.get("correct_option_index") is not None:
        raw = [slide.get("correct_option_index")]
    if raw is None and slide.get("correct_index") is not None:
        raw = [slide.get("correct_index")]
    if not isinstance(raw, list):
        raw = [raw]
    out = []
    for x in raw:
        try:
            out.append(int(x))
        except Exception:
            pass
    return out


def _valid_point(p: Any) -> bool:
    if not isinstance(p, dict):
        return False
    try:
        x, y = float(p.get("x")), float(p.get("y"))
    except Exception:
        return False
    return 0 <= x <= 1 and 0 <= y <= 1


def _validate_slide(slide: dict[str, Any], i: int, prefix: str = "slide") -> list[str]:
    errors: list[str] = []
    raw_kind = str(slide.get("type") or "passive").strip().lower()
    kind = canonical_content_type(raw_kind)
    label = f"{prefix} {i}"
    if kind not in SUPPORTED_CONTENT_TYPES:
        return [f"{label}: unsupported type {kind}"]
    errors.extend(_validate_media_sequence(slide, label))
    errors.extend(_validate_pre_slide_video(slide,label))
    required_for_movie = raw_kind == "required_movie_phrase" or slide.get("requiredForMovie") is True or slide.get("required_for_movie") is True
    if required_for_movie and not str(slide.get("moviePhraseId") or slide.get("required_phrase_id") or "").strip():
        errors.append(f"{label}: requiredForMovie needs moviePhraseId/required_phrase_id")
    if required_for_movie and slide.get("allow_skip") is True:
        errors.append(f"{label}: requiredForMovie cannot allow skip")

    options = list(slide.get("options") or slide.get("items") or [])
    correct = _indices(slide)
    one_answer = {"choice", "tap_select", "listen_choose", "odd_one_out", "fill_gap", "sound_position", "find_in_text"}
    if kind in one_answer:
        if len(options) < 2:
            errors.append(f"{label}: {kind} needs at least 2 options/items")
        if not correct:
            errors.append(f"{label}: {kind} needs correct_option_index/correct_indices")
        elif options and any(x < 0 or x >= len(options) for x in correct):
            errors.append(f"{label}: {kind} has out-of-range correct index")
    if kind == "multi_select":
        if len(options) < 2:
            errors.append(f"{label}: multi_select needs at least 2 options/items")
        if not correct:
            errors.append(f"{label}: multi_select needs correct_indices")
        elif any(x < 0 or x >= len(options) for x in correct):
            errors.append(f"{label}: multi_select has out-of-range correct index")

    if kind in {"drag_drop", "sorting"}:
        items, targets = list(slide.get("items") or []), list(slide.get("targets") or [])
        if not items or not targets or len(items) != len(targets):
            errors.append(f"{label}: {kind} needs items and targets (equal, non-empty)")
        if kind == "drag_drop" and slide.get("drop_zones"):
            zones=list(slide.get("drop_zones") or [])
            if len(zones) != len(items):
                errors.append(f"{label}: drag_drop drop_zones must match items")
            elif any(not _valid_point(z) for z in zones):
                errors.append(f"{label}: drag_drop drop_zones need normalized x/y coordinates")
            if not slide.get("image_file"):
                errors.append(f"{label}: drag_drop with drop_zones needs image_file")

    if kind in {"memory", "matching", "match_visible"}:
        pairs = list(slide.get("pairs") or [])
        if len(pairs) < 2:
            errors.append(f"{label}: {kind} needs at least 2 pairs")

    if kind == "puzzle":
        try:
            pieces = int(slide.get("pieces") or slide.get("piece_count") or 0)
        except (TypeError, ValueError):
            pieces = 0
        has_image = bool(slide.get("image_file") or any(
            isinstance(item, dict) and item.get("type") == "image" and (item.get("src") or item.get("url"))
            for item in (slide.get("media_sequence") or [])
        ))
        if pieces < 2 or pieces > 24:
            errors.append(f"{label}: puzzle needs 2..24 pieces")
        if not has_image:
            errors.append(f"{label}: puzzle needs an image")

    if kind in {"tap_sound", "interactive_scene"}:
        hotspots = list(slide.get("hotspots") or [])
        if not hotspots:
            errors.append(f"{label}: {kind} needs hotspots")
        elif any(not _valid_point(h) for h in hotspots):
            errors.append(f"{label}: {kind} hotspots need normalized x/y coordinates")

    if kind == "connect_lines":
        left, right = list(slide.get("left_points") or []), list(slide.get("right_points") or [])
        if not left or not right or len(left) != len(right):
            errors.append(f"{label}: connect_lines needs equal left_points/right_points")
        elif any(not _valid_point(p) for p in left + right):
            errors.append(f"{label}: connect_lines points need normalized x/y coordinates")
        if not slide.get("image_file"):
            errors.append(f"{label}: connect_lines needs image_file")

    if kind in {"trace", "handwriting_screen", "draw", "drawing", "coloring", "maze", "dictation"} and not slide.get("image_file"):
        errors.append(f"{label}: {kind} needs image_file for the drawing canvas")

    if kind == "maze":
        if not _valid_point(slide.get("start_point")) or not _valid_point(slide.get("end_point")):
            errors.append(f"{label}: maze needs normalized start_point and end_point")

    if kind == "dictation" and not str(slide.get("dictation_text") or slide.get("audio_text") or "").strip():
        errors.append(f"{label}: dictation needs dictation_text/audio_text")

    if kind in {"sequence", "word_builder", "syllable_builder", "syllable_split", "sentence_builder"} and len(slide.get("items") or []) < 2:
        errors.append(f"{label}: {kind} needs at least 2 ordered items")

    if kind == "letter_path":
        if not str(slide.get("letter") or "").strip():
            errors.append(f"{label}: letter_path needs letter")
        if int(slide.get("count") or 0) < 3:
            errors.append(f"{label}: letter_path needs count >= 3")

    if kind in {"read_aloud", "echo_reading", "shared_reading"} and not str(slide.get("reading_text") or "").strip():
        errors.append(f"{label}: {kind} needs reading_text")

    if kind == "read_roles":
        turns = slide.get("role_turns") or []
        roles = [str(x).strip() for x in (slide.get("available_roles") or []) if str(x).strip()]
        if not isinstance(turns, list) or len(turns) < 2:
            errors.append(f"{label}: read_roles needs ordered role_turns")
        else:
            speakers = []
            turn_roles = []
            for t in turns:
                if not isinstance(t, dict) or not str(t.get("text") or "").strip():
                    errors.append(f"{label}: every role_turn needs text")
                    continue
                role = str(t.get("role") or "").strip()
                if role:
                    turn_roles.append(role)
                speaker = str(t.get("speaker") or "").lower()
                if speaker in {"child", "bot"}:
                    speakers.append(speaker)
                elif not roles or not role:
                    errors.append(f"{label}: role_turn needs speaker child/bot or a role from available_roles")
                elif role not in roles:
                    errors.append(f"{label}: role_turn role '{role}' is not in available_roles")
            if roles:
                if len(set(roles)) < 2:
                    errors.append(f"{label}: read_roles available_roles needs at least two roles")
                if len(set(turn_roles)) < 2:
                    errors.append(f"{label}: read_roles needs turns for at least two roles")
            elif "child" not in speakers or "bot" not in speakers:
                errors.append(f"{label}: read_roles needs both child and bot turns")
        if not str(slide.get("reading_text") or "").strip():
            # Keep one complete text version for speech assessment/reporting.
            errors.append(f"{label}: read_roles needs reading_text")

    if kind == "video" and not (slide.get("video_url") or slide.get("video_file")):
        errors.append(f"{label}: video needs video_url/video_file")

    if kind == "video_pause_question":
        if not (slide.get("video_url") or slide.get("video_file")):
            errors.append(f"{label}: video_pause_question needs video_url/video_file")
        try:
            pause = float(slide.get("pause_at_seconds") or 0)
        except Exception:
            pause = 0
        if pause <= 0:
            errors.append(f"{label}: video_pause_question needs pause_at_seconds > 0")
        if not str(slide.get("question") or slide.get("prompt") or "").strip():
            errors.append(f"{label}: video_pause_question needs question")
        video_options = list(slide.get("options") or [])
        if video_options:
            if len(video_options) < 2:
                errors.append(f"{label}: video_pause_question needs at least 2 options")
            if not correct:
                errors.append(f"{label}: video_pause_question with options needs correct_indices")
            elif any(x < 0 or x >= len(video_options) for x in correct):
                errors.append(f"{label}: video_pause_question has out-of-range correct index")

    return errors


def validate_content_lesson(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    explicit_status = str(data.get("status") or "").strip().upper()
    if explicit_status and explicit_status not in PUBLICATION_STATUSES:
        errors.append("status must be draft, published or archived")
    if str(data.get("engine") or "").lower() == "content_v1" and not str(data.get("schema_version") or "").strip():
        errors.append("missing schema_version")
    for key in ["lesson_id", "course_id", "title", "order"]:
        if data.get(key) in (None, ""):
            errors.append(f"missing {key}")
    if int(data.get("max_completed_runs", 0) or 0) != 2:
        errors.append("lesson max_completed_runs must be 2")
    if int(data.get("expires_after_months", 0) or 0) != 10:
        errors.append("lesson expires_after_months must be 10")
    slides = data.get("slides") or []
    if not isinstance(slides, list):
        return errors + ["slides must be a list"]
    if not slides:
        errors.append("lesson needs at least one slide")
        return errors
    seen_ids: set[str] = set()
    seen_orders: set[int] = set()
    for i, slide in enumerate(slides, 1):
        sid = str(slide.get("slide_id") or "")
        if not sid:
            errors.append(f"slide {i}: missing slide_id")
        elif sid in seen_ids:
            errors.append(f"slide {i}: duplicate slide_id {sid}")
        seen_ids.add(sid)
        try:
            order = int(slide.get("order") or 0)
        except (TypeError, ValueError):
            order = 0
        if order < 1:
            errors.append(f"slide {i}: order must be positive")
        elif order in seen_orders:
            errors.append(f"slide {i}: duplicate order {order}")
        seen_orders.add(order)
        errors.extend(_validate_slide(slide, i, "slide"))
    return errors


def ensure_persistent_lesson(lesson_id: str) -> Path:
    import shutil
    dst = persistent_lessons_root() / lesson_id
    if (dst / "lesson.json").exists():
        return dst
    src = bundled_lessons_root() / lesson_id
    if not (src / "lesson.json").exists():
        return dst
    shutil.copytree(src, dst, dirs_exist_ok=True)
    return dst


def backup_lesson_version(lesson_id: str, label: str = "edit") -> Path | None:
    import shutil
    from datetime import datetime
    root = ensure_persistent_lesson(lesson_id)
    if not (root / "lesson.json").exists():
        return None
    versions = root / "_versions"
    versions.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")
    dst = versions / f"{stamp}_{label}"
    shutil.copytree(root, dst, dirs_exist_ok=False, ignore=shutil.ignore_patterns("_versions"))
    return dst


def list_lesson_versions(lesson_id: str) -> list[Path]:
    root = ensure_persistent_lesson(lesson_id) / "_versions"
    if not root.exists():
        return []
    return sorted([p for p in root.iterdir() if p.is_dir()], reverse=True)


def restore_lesson_version(lesson_id: str, version_name: str) -> bool:
    import shutil
    root = ensure_persistent_lesson(lesson_id)
    src = root / "_versions" / version_name
    if not (src / "lesson.json").exists():
        return False
    backup_lesson_version(lesson_id, "before_restore")
    for child in list(root.iterdir()):
        if child.name == "_versions":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    for child in src.iterdir():
        dst = root / child.name
        if child.is_dir():
            shutil.copytree(child, dst)
        else:
            shutil.copy2(child, dst)
    return True


def validate_homework(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    slides = data.get("slides") or []
    if not isinstance(slides, list):
        return ["homework slides must be a list"]
    seen_ids: set[str] = set()
    for i, slide in enumerate(slides, 1):
        sid = str(slide.get("slide_id") or "")
        if not sid:
            errors.append(f"homework {i}: missing slide_id")
        elif sid in seen_ids:
            errors.append(f"homework {i}: duplicate slide_id {sid}")
        seen_ids.add(sid)
        errors.extend(_validate_slide(slide, i, "homework"))
    return errors
