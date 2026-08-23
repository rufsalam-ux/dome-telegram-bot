from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from sqlalchemy import select

from app.db.models import LessonMovie
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
