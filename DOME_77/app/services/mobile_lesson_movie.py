from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from sqlalchemy import select

from app.core.config import settings
from app.db.models import LessonMovie, MovieVoiceSlot
from app.db.session import SessionLocal
from app.services.cartoon_builder import _probe_video, build_timeline_cartoon
from app.services.cartoon_text_overlay import cartoon_text_filters
from app.services.lesson_loader import load_lesson


log = logging.getLogger("dome.mobile_movie")


@dataclass(frozen=True)
class MovieRenderInputs:
    base_video: Path
    character: Path
    audio_by_phrase: dict[str, Path]
    timeline: list[dict]
    output: Path
    lesson_dir: Path
    target_language: str
    approved_phrase_ids: tuple[str, ...] = ()
    expected_base_sha256: str = ""
    require_all_phrase_audio: bool = True


@dataclass(frozen=True)
class MovieContract:
    lesson_id: str
    lesson_dir: Path
    base_video: Path
    timeline: list[dict]
    approved_phrase_ids: tuple[str, ...]
    expected_base_sha256: str
    audio_policy: dict


class MovieContractError(RuntimeError):
    """A release/content contract failure safe to translate at the API edge."""


SAFE_MOVIE_CONTRACT_ERROR = "Мультфильм пока не удалось подготовить. Все записи сохранены — попробуйте ещё раз позже."


@lru_cache(maxsize=32)
def _sha256_for_version(path_value: str, size: int, mtime_ns: int) -> str:
    del size, mtime_ns
    digest = hashlib.sha256()
    with Path(path_value).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checked_relative(root: Path, value: object) -> Path:
    candidate = (root / str(value or "")).resolve()
    if root.resolve() not in candidate.parents:
        raise MovieContractError(SAFE_MOVIE_CONTRACT_ERROR)
    return candidate


def load_movie_contract(lesson_id: str, lesson: dict | None = None) -> MovieContract:
    """Load the immutable per-lesson movie source and audio whitelist.

    A bundled movie_manifest.json is release-authoritative. This deliberately
    prevents an older persistent lesson draft from silently selecting an old
    base movie after a deploy.
    """

    lesson = lesson or load_lesson(lesson_id)
    bundled_dir = settings.content_root / "lessons" / lesson_id
    source_dir = (
        settings.storage_root / "authored-content" / "lessons" / lesson_id
        if lesson.get("content_source") == "persistent"
        else bundled_dir
    )
    manifest_name = str(lesson.get("cartoon_base_manifest") or "movie_manifest.json")
    bundled_manifest = bundled_dir / manifest_name
    manifest_path = bundled_manifest if bundled_manifest.exists() else source_dir / manifest_name
    if not manifest_path.exists():
        raise MovieContractError(SAFE_MOVIE_CONTRACT_ERROR)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MovieContractError(SAFE_MOVIE_CONTRACT_ERROR) from exc
    if str(manifest.get("lesson_id") or "") != str(lesson_id):
        raise MovieContractError(SAFE_MOVIE_CONTRACT_ERROR)

    contract_dir = manifest_path.parent
    base_video = _checked_relative(contract_dir, manifest.get("canonical_source"))
    timeline_path = _checked_relative(contract_dir, manifest.get("timeline_file") or "timeline.json")
    if not base_video.is_file() or not timeline_path.is_file():
        raise MovieContractError(SAFE_MOVIE_CONTRACT_ERROR)
    stat = base_video.stat()
    expected_size = int(manifest.get("canonical_source_bytes") or 0)
    expected_hash = str(manifest.get("canonical_source_sha256") or "").lower()
    if expected_size <= 0 or stat.st_size != expected_size or len(expected_hash) != 64:
        raise MovieContractError(SAFE_MOVIE_CONTRACT_ERROR)
    if _sha256_for_version(str(base_video), stat.st_size, stat.st_mtime_ns) != expected_hash:
        raise MovieContractError(SAFE_MOVIE_CONTRACT_ERROR)
    try:
        timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MovieContractError(SAFE_MOVIE_CONTRACT_ERROR) from exc
    if not isinstance(timeline, list) or not timeline:
        raise MovieContractError(SAFE_MOVIE_CONTRACT_ERROR)
    approved = tuple(str(row.get("phrase_id") or "") for row in timeline if str(row.get("phrase_id") or ""))
    if not approved or len(set(approved)) != len(approved):
        raise MovieContractError(SAFE_MOVIE_CONTRACT_ERROR)
    audio_policy = dict(manifest.get("audio_policy") or lesson.get("movie_audio_policy") or {})
    if audio_policy.get("allow_tutor_tts") is not False or audio_policy.get("allow_silence_fallback") is not False:
        raise MovieContractError(SAFE_MOVIE_CONTRACT_ERROR)
    return MovieContract(
        lesson_id=str(lesson_id),
        lesson_dir=contract_dir,
        base_video=base_video,
        timeline=[dict(row) for row in timeline],
        approved_phrase_ids=approved,
        expected_base_sha256=expected_hash,
        audio_policy=audio_policy,
    )


async def recover_interrupted_mobile_movie_jobs() -> int:
    """Make renders interrupted by a process restart safely retryable."""

    recovered = 0
    async with SessionLocal() as db:
        movies = (await db.scalars(select(LessonMovie).where(LessonMovie.status == "PROCESSING"))).all()
        for movie in movies:
            output = Path(movie.output_path) if movie.output_path else None
            complete = False
            if output and output.exists() and output.stat().st_size > 100_000:
                try:
                    _width, _height, duration = _probe_video(output)
                    timeline = load_lesson(movie.lesson_id).get("timeline") or []
                    expected = max((float(row.get("end") or 0) for row in timeline), default=1.0)
                    complete = duration >= expected - 0.5
                except Exception:
                    complete = False
            if complete:
                movie.status = "READY"
                movie.error = None
            else:
                movie.status = "FAILED"
                movie.error = "Render interrupted by application restart; queued for idempotent retry"
            recovered += 1
        if recovered:
            await db.commit()
    if recovered:
        log.warning("Recovered %s interrupted mobile movie job(s)", recovered)
    return recovered


def required_movie_phrase_ids(lesson: dict) -> list[str]:
    """Return the authored movie phrases in timeline order."""

    return [
        str(item["phrase_id"])
        for item in lesson.get("timeline", [])
        if str(item.get("phrase_id") or "").strip()
    ]


def select_movie_voice_takes(voice_attempts: Iterable[object], lesson: dict) -> tuple[dict[str, Path], list[str]]:
    """Select the latest accepted real child recording for every movie phrase.

    Warm-up answers and other accepted speech never enter the movie. A retry
    replaces an older accepted take for the same authored phrase.
    """

    required = required_movie_phrase_ids(lesson)
    wanted = set(required)
    selected: dict[str, Path] = {}
    for attempt in voice_attempts:
        phrase_id = str(getattr(attempt, "phrase_id", "") or "")
        status = str(getattr(attempt, "status", "") or "")
        path = Path(str(getattr(attempt, "audio_path", "") or ""))
        if phrase_id in wanted and movie_take_status(status) and path.exists() and path.stat().st_size > 0:
            selected[phrase_id] = path
    return selected, [phrase_id for phrase_id in required if phrase_id not in selected]


def movie_take_status(status: object) -> bool:
    value = str(status or "").upper()
    return value.startswith("ACCEPTED") or value == "MOVIE_USABLE_WITH_SUPPORT"


async def ensure_movie_voice_slots(db, session_id: int, lesson: dict) -> list[MovieVoiceSlot]:
    """Create EXPECTED rows as soon as a lesson session exists."""

    existing = {
        row.required_voice_id: row
        for row in (await db.scalars(select(MovieVoiceSlot).where(MovieVoiceSlot.lesson_session_id == session_id))).all()
    }
    for phrase_id in required_movie_phrase_ids(lesson):
        if phrase_id not in existing:
            row = MovieVoiceSlot(
                lesson_session_id=session_id,
                required_voice_id=phrase_id,
                status="EXPECTED",
                diagnostics_json=json.dumps({"expected": True, "strategy": "pending"}),
            )
            db.add(row);existing[phrase_id]=row
    await db.flush()
    return [existing[value] for value in required_movie_phrase_ids(lesson)]


async def record_movie_voice_slot(db, session_id: int, phrase_id: str, attempt: object, lesson: dict) -> bool:
    """Save an accepted exact take immediately, never only at completion."""

    if phrase_id not in set(required_movie_phrase_ids(lesson)) or not movie_take_status(getattr(attempt, "status", "")):
        return False
    await ensure_movie_voice_slots(db, session_id, lesson)
    slot = await db.scalar(select(MovieVoiceSlot).where(
        MovieVoiceSlot.lesson_session_id == session_id,
        MovieVoiceSlot.required_voice_id == phrase_id,
    ))
    if not slot:
        return False
    slot.status="RECORDED";slot.source_attempt_id=getattr(attempt,"id",None);slot.audio_path=str(getattr(attempt,"audio_path","") or "") or None
    slot.diagnostics_json=json.dumps({"expected":True,"recorded":True,"strategy":"exact_child_recording","track_role":"child_recording"})
    return True


async def resolve_movie_voice_slots(db, session_id: int, voice_attempts: Iterable[object], lesson: dict, target_language: str, cache_root: Path) -> tuple[dict[str, Path], list[dict]]:
    """Resolve only explicitly whitelisted child takes for movie phrase IDs.

    target_language/cache_root remain in the signature for backward-compatible
    callers; neither tutor TTS nor unrelated conversation audio is permitted.
    """

    del target_language, cache_root
    attempts=list(voice_attempts);required=set(required_movie_phrase_ids(lesson))
    slots=await ensure_movie_voice_slots(db,session_id,lesson);audio_by_phrase:dict[str,Path]={}
    exact:dict[str,object]={}
    for attempt in attempts:
        phrase_id=str(getattr(attempt,"phrase_id","") or "");path=Path(str(getattr(attempt,"audio_path","") or ""))
        if phrase_id in required and movie_take_status(getattr(attempt,"status","")) and path.exists() and path.stat().st_size>0:exact[phrase_id]=attempt
    for slot in slots:
        phrase_id=slot.required_voice_id;attempt=exact.get(phrase_id)
        if attempt:
            path=Path(str(getattr(attempt,"audio_path")));attempt_id=int(getattr(attempt,"id",0) or 0);audio_by_phrase[phrase_id]=path
            slot.status="RECORDED";slot.source_attempt_id=attempt_id or None;slot.audio_path=str(path);slot.diagnostics_json=json.dumps({"expected":True,"recorded":True,"strategy":"exact_child_recording","track_role":"child_recording"})
            continue
        slot.status="MISSING_REQUIRED";slot.audio_path=None;slot.source_attempt_id=None
        slot.diagnostics_json=json.dumps({"expected":True,"recorded":False,"strategy":"missing_required_child_recording","track_role":None})
    await db.flush()
    diagnostics=[]
    for slot in slots:
        try:detail=json.loads(slot.diagnostics_json or '{}')
        except (TypeError,ValueError,json.JSONDecodeError):detail={}
        diagnostics.append({"required_voice_id":slot.required_voice_id,"status":slot.status,**detail})
    return audio_by_phrase,diagnostics


def build_mobile_lesson_movie(inputs: MovieRenderInputs) -> Path:
    """Render the authored lesson movie from its mandatory base video."""

    if not inputs.base_video.exists():
        log.error("Canonical movie base is missing: %s", inputs.base_video)
        raise MovieContractError(SAFE_MOVIE_CONTRACT_ERROR)
    if not inputs.character.exists():
        log.error("Selected child hero is missing: %s", inputs.character)
        raise MovieContractError(SAFE_MOVIE_CONTRACT_ERROR)
    if inputs.expected_base_sha256:
        stat = inputs.base_video.stat()
        actual_hash = _sha256_for_version(str(inputs.base_video), stat.st_size, stat.st_mtime_ns)
        if actual_hash != inputs.expected_base_sha256.lower():
            log.error("Canonical movie base checksum mismatch: %s", inputs.base_video)
            raise MovieContractError(SAFE_MOVIE_CONTRACT_ERROR)
    approved = tuple(inputs.approved_phrase_ids) or tuple(str(row.get("phrase_id") or "") for row in inputs.timeline)
    approved_set = set(approved)
    injected = set(inputs.audio_by_phrase)
    if not injected <= approved_set:
        log.error("Rejected non-whitelisted movie audio roles: %s", sorted(injected - approved_set))
        raise MovieContractError(SAFE_MOVIE_CONTRACT_ERROR)
    missing = [phrase_id for phrase_id in approved if phrase_id not in injected]
    if inputs.require_all_phrase_audio and missing:
        log.error("Required child movie recordings are missing: %s", missing)
        raise MovieContractError(SAFE_MOVIE_CONTRACT_ERROR)

    log.info(
        "MOBILE_MOVIE_RENDER base=%s hero=%s phrases=%s language=%s output=%s",
        inputs.base_video.name,
        inputs.character.name,
        sorted(inputs.audio_by_phrase),
        inputs.target_language,
        inputs.output,
    )
    return build_timeline_cartoon(
        inputs.base_video,
        inputs.character,
        inputs.audio_by_phrase,
        inputs.timeline,
        inputs.output,
        cartoon_text_filters(inputs.lesson_dir,inputs.target_language),
    )
