from __future__ import annotations

from dataclasses import dataclass

from app.services.adaptive_learning import level_from_score, score_answer, update_running_average


@dataclass(frozen=True)
class AttemptOutcome:
    status: str
    accepted: bool
    advance_allowed: bool
    needs_retry: bool


def voice_attempt_outcome(status: str, attempt_number: int, max_attempts: int) -> AttemptOutcome:
    """One production policy for voice retries across interactive lesson types.

    Exhausting attempts permits progress so the child cannot be trapped, but it
    never changes an incorrect/no-speech take into a correct one.
    """
    status = str(status or "TECHNICAL_UNCERTAINTY").upper()
    accepted = status.startswith("ACCEPTED")
    if accepted:
        return AttemptOutcome(status, True, True, False)
    if attempt_number >= max(1, int(max_attempts)):
        final = "NO_SPEECH_CONTINUE" if status == "NO_SPEECH" else "COMPLETED_WITH_SUPPORT"
        return AttemptOutcome(final, False, True, False)
    return AttemptOutcome(status, False, False, True)


def no_speech_feedback(attempt_number: int, max_attempts: int, example: str = "") -> tuple[str, str]:
    """Return native feedback and target-language correction source text."""
    if attempt_number <= 1:
        return "Я пока не услышала ответ. Нажми на микрофон и скажи чуть громче.", ""
    if attempt_number < max_attempts:
        hint = example or "Можно ответить одним словом или короткой фразой."
        return "Я всё ещё не слышу речь. Послушай пример и попробуй ещё раз.", hint
    return "Я не смогла услышать ответ. Мы продолжим, но эта попытка не засчитана как правильная.", ""


def apply_adaptive_assessment(child, voice_attempt, assessment) -> tuple[float, str]:
    """Update the live child profile after each meaningful mobile answer."""
    if str(assessment.status or "") in {"NO_SPEECH", "TECHNICAL_UNCERTAINTY"} or not assessment.transcript:
        return float(child.working_difficulty or 0.15), str(child.language_level or "PRE_A1")
    adaptive = score_answer(
        semantic_match=assessment.semantic_match,
        grammar_errors=assessment.grammar_errors,
        pronunciation_errors=assessment.pronunciation_errors,
        transcript=assessment.transcript,
        attempt_number=voice_attempt.attempt_number,
        status=assessment.status,
    )
    count = int(child.answers_count or 0)
    for field in ("comprehension", "grammar", "vocabulary", "pronunciation", "fluency", "independence"):
        column = f"{field}_score"
        value = float(getattr(adaptive, field))
        setattr(voice_attempt, column, value)
        setattr(child, column, update_running_average(float(getattr(child, column) or 0), count, value))
    voice_attempt.recommended_difficulty = adaptive.recommended_difficulty
    child.answers_count = count + 1
    # Respond inside this lesson, while smoothing enough to avoid oscillation.
    child.working_difficulty = max(
        0.05,
        min(0.95, float(child.working_difficulty or 0.15) * 0.65 + adaptive.recommended_difficulty * 0.35),
    )
    child.language_level = level_from_score(
        child.working_difficulty,
        str(child.language_level or "PRE_A1"),
        int(child.answers_count),
    )
    return float(child.working_difficulty), str(child.language_level)


def complexity_support(difficulty: float) -> str:
    value = float(difficulty or 0.15)
    if value < 0.25:
        return "Можно ответить одним словом или короткой фразой."
    if value < 0.50:
        return "Ответь короткой фразой или одним предложением."
    if value < 0.72:
        return "Ответь предложением и добавь одну деталь."
    return "Расскажи подробнее и добавь пример или объяснение."
