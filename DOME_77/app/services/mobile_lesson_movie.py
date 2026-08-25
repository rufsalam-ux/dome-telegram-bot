from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from sqlalchemy import select

from app.db.models import LessonMovie, MovieVoiceSlot
from app.db.session import SessionLocal
from app.services.ai_speech import synthesize_speech, translate_text
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
        if phrase_id in wanted and status.startswith("ACCEPTED") and path.exists() and path.stat().st_size > 0:
            selected[phrase_id] = path
    return selected, [phrase_id for phrase_id in required if phrase_id not in selected]


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

    if phrase_id not in set(required_movie_phrase_ids(lesson)) or not str(getattr(attempt, "status", "")).startswith("ACCEPTED"):
        return False
    await ensure_movie_voice_slots(db, session_id, lesson)
    slot = await db.scalar(select(MovieVoiceSlot).where(
        MovieVoiceSlot.lesson_session_id == session_id,
        MovieVoiceSlot.required_voice_id == phrase_id,
    ))
    if not slot:
        return False
    slot.status="RECORDED";slot.source_attempt_id=getattr(attempt,"id",None);slot.audio_path=str(getattr(attempt,"audio_path","") or "") or None
    slot.diagnostics_json=json.dumps({"expected":True,"recorded":True,"strategy":"exact_child_recording"})
    return True


def _tokens(value: object) -> set[str]:
    return {word for word in re.findall(r"[\w'-]+", str(value or "").casefold(), flags=re.UNICODE) if len(word) > 1}


def _compatible_attempt(required: dict, attempts: list[object], used_attempt_ids: set[int]) -> tuple[object | None, float]:
    target = _tokens(required.get("target_text"))
    for meaning in required.get("accepted_meaning") or []:target |= _tokens(meaning)
    best: object | None=None;best_score=0.0
    for attempt in attempts:
        attempt_id=int(getattr(attempt,"id",0) or 0)
        if attempt_id in used_attempt_ids or not str(getattr(attempt,"status","")).startswith("ACCEPTED"):continue
        path=Path(str(getattr(attempt,"audio_path","") or ""));transcript=_tokens(getattr(attempt,"transcript",""))
        if not target or not transcript or not path.exists() or path.stat().st_size<=0:continue
        score=len(target&transcript)/max(1,min(len(target),len(transcript)))
        if score>best_score:best,best_score=attempt,score
    return (best,best_score) if best_score>=.45 else (None,best_score)


async def resolve_movie_voice_slots(db, session_id: int, voice_attempts: Iterable[object], lesson: dict, target_language: str, cache_root: Path) -> tuple[dict[str, Path], list[dict]]:
    """Resolve exact → compatible → neutral TTS → silence without blocking movie creation."""

    attempts=list(voice_attempts);required_rows={str(row.get("phrase_id")):row for row in lesson.get("required_phrases") or []}
    slots=await ensure_movie_voice_slots(db,session_id,lesson);audio_by_phrase:dict[str,Path]={};used:set[int]=set()
    exact:dict[str,object]={}
    for attempt in attempts:
        phrase_id=str(getattr(attempt,"phrase_id","") or "");path=Path(str(getattr(attempt,"audio_path","") or ""))
        if phrase_id in required_rows and str(getattr(attempt,"status","")).startswith("ACCEPTED") and path.exists() and path.stat().st_size>0:exact[phrase_id]=attempt
    pending_tts:list[tuple[MovieVoiceSlot,str,str]]=[]
    for slot in slots:
        phrase_id=slot.required_voice_id;attempt=exact.get(phrase_id)
        if attempt:
            path=Path(str(getattr(attempt,"audio_path")));attempt_id=int(getattr(attempt,"id",0) or 0);used.add(attempt_id);audio_by_phrase[phrase_id]=path
            slot.status="RECORDED";slot.source_attempt_id=attempt_id or None;slot.audio_path=str(path);slot.diagnostics_json=json.dumps({"expected":True,"recorded":True,"strategy":"exact_child_recording"})
            continue
        compatible,score=_compatible_attempt(required_rows.get(phrase_id,{}),attempts,used)
        if compatible:
            path=Path(str(getattr(compatible,"audio_path")));attempt_id=int(getattr(compatible,"id",0) or 0);used.add(attempt_id);audio_by_phrase[phrase_id]=path
            slot.status="FALLBACK_COMPATIBLE";slot.source_attempt_id=attempt_id or None;slot.audio_path=str(path);slot.diagnostics_json=json.dumps({"expected":True,"recorded":False,"strategy":"compatible_child_recording","compatibility":round(score,3)})
            continue
        source_text=str(required_rows.get(phrase_id,{}).get("target_text") or phrase_id);source_language=str(lesson.get("target_language") or "ru")
        pending_tts.append((slot,source_text,source_language))

    async def make_tts(slot:MovieVoiceSlot,text:str,source_language:str):
        try:
            localized=await translate_text(text,source_language,target_language) if source_language!=target_language else text
            return slot,await synthesize_speech(localized,target_language,cache_root/"movie-voice-fallback",f"session{session_id}_{slot.required_voice_id}","warm")
        except Exception as exc:
            log.warning("Movie voice TTS fallback failed session=%s phrase=%s: %s",session_id,slot.required_voice_id,exc);return slot,None

    if pending_tts:
        for slot,path in await asyncio.gather(*(make_tts(*item) for item in pending_tts)):
            if path and path.exists() and path.stat().st_size>0:
                audio_by_phrase[slot.required_voice_id]=path;slot.status="FALLBACK_TTS";slot.audio_path=str(path);slot.source_attempt_id=None;strategy="neutral_tts"
            else:
                slot.status="FALLBACK_SILENCE";slot.audio_path=None;slot.source_attempt_id=None;strategy="silence"
            slot.diagnostics_json=json.dumps({"expected":True,"recorded":False,"strategy":strategy})
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
        raise FileNotFoundError(f"Missing authored movie base: {inputs.base_video}")
    if not inputs.character.exists():
        raise FileNotFoundError(f"Missing selected child hero: {inputs.character}")

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
