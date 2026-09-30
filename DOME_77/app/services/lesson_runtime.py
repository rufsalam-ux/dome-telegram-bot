from __future__ import annotations

from dataclasses import dataclass

import json

from app.services.adaptive_learning import (
    AdaptiveDecision,
    AdaptiveScores,
    LEVELS,
    LEVEL_CENTERS,
    derived_level,
    evidence_history,
    evidence_record,
    level_from_score,
    score_answer,
    skill_profile_payload,
    update_running_average,
)

VOICE_FEEDBACK_STATES={"NO_AUDIO","NO_SPEECH","ASR_FAILED","ANSWER_UNCLEAR","INCORRECT","PARTIALLY_CORRECT","CORRECT"}


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


def classify_voice_feedback(*,audio_received:bool,has_speech:bool,transcript:str,confidence:float,status:str,semantic_match:float=0.0)->str:
    """One mutually-exclusive child feedback state for the mobile runtime."""
    if not audio_received:return "NO_AUDIO"
    if not has_speech:return "NO_SPEECH"
    if not str(transcript or "").strip():return "ASR_FAILED"
    if float(confidence or 0.0)<0.35:return "ANSWER_UNCLEAR"
    value=str(status or "").upper()
    if value.startswith("ACCEPTED"):
        return "CORRECT" if value=="ACCEPTED_CORRECT" and float(semantic_match or 0.0)>=0.8 else "PARTIALLY_CORRECT"
    if value in {"TECHNICAL_UNCERTAINTY","ANSWER_UNCLEAR"}:return "ANSWER_UNCLEAR"
    return "INCORRECT"


def no_speech_feedback(attempt_number: int, max_attempts: int, example: str = "") -> tuple[str, str]:
    """Return native feedback and target-language correction source text."""
    if attempt_number <= 1:
        return "Я пока не услышала ответ. Нажми на микрофон и скажи чуть громче.", ""
    if attempt_number < max_attempts:
        hint = example or "Можно ответить одним словом или короткой фразой."
        return "Я всё ещё не слышу речь. Послушай пример и попробуй ещё раз.", hint
    return "Я не смогла услышать ответ. Мы продолжим, но эта попытка не засчитана как правильная.", ""


def apply_adaptive_assessment(child, voice_attempt, assessment, signals: dict | None = None) -> tuple[float, str]:
    """Update the live child profile after each meaningful mobile answer."""
    if str(assessment.status or "") in {"NO_SPEECH", "TECHNICAL_UNCERTAINTY", "ASR_FAILED", "ANSWER_UNCLEAR"} or not assessment.transcript:
        return float(child.working_difficulty or 0.15), str(child.language_level or "PRE_A1")
    signals = signals or {}
    model_evidence = assessment.adaptive_assessment if isinstance(getattr(assessment, "adaptive_assessment", None), dict) else {}
    adaptive = score_answer(
        semantic_match=assessment.semantic_match,
        grammar_errors=assessment.grammar_errors,
        pronunciation_errors=assessment.pronunciation_errors,
        transcript=assessment.transcript,
        attempt_number=voice_attempt.attempt_number,
        status=assessment.status,
        response_latency_ms=max(0, min(120_000, int(signals.get("response_latency_ms") or 0))),
        hints_used=max(0, min(5, int(signals.get("hints_used") or 0))),
        used_native_language=bool(signals.get("used_native_language")),
        open_question=bool(signals.get("open_question")),
        understood_question=model_evidence.get("understood_question"),
        on_topic=model_evidence.get("on_topic"),
        target_word_count=model_evidence.get("target_word_count"),
        lexical_diversity=model_evidence.get("lexical_diversity_0_to_1"),
        grammar_complexity=model_evidence.get("grammar_complexity_0_to_1"),
        confidence_evidence=model_evidence.get("confidence_0_to_1"),
        hesitation=bool(model_evidence.get("hesitation")),
        choice_only=bool(signals.get("choice_only")),
    )
    # Adapt inside a bounded recent window.  Legacy answers_count can contain
    # years of turns and must not dilute a newly observed ability change.
    count = min(9, int(getattr(child, "adaptive_meaningful_turns", 0) or 0))
    for field in ("comprehension", "grammar", "vocabulary", "pronunciation", "fluency", "independence", "speaking", "confidence"):
        column = f"{field}_score"
        value = float(getattr(adaptive, field))
        setattr(voice_attempt, column, value)
        setattr(child, column, update_running_average(float(getattr(child, column, 0) or 0), count, value))
    voice_attempt.recommended_difficulty = adaptive.recommended_difficulty
    child.answers_count = int(child.answers_count or 0) + 1
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
    child.adaptive_level = derived_level(
        comprehension=float(child.comprehension_score or 0),
        vocabulary=float(child.vocabulary_score or 0),
        grammar=float(child.grammar_score or 0),
        confidence=float(child.confidence_score or 0),
    )
    return float(child.working_difficulty), str(child.language_level)


def persist_adaptive_decision(child, voice_attempt, assessment, signals: dict, decision: AdaptiveDecision) -> dict:
    """Persist bounded rolling evidence without changing the dialogue critical path."""

    scores = AdaptiveScores(
        comprehension=float(voice_attempt.comprehension_score or 0),
        grammar=float(voice_attempt.grammar_score or 0),
        vocabulary=float(voice_attempt.vocabulary_score or 0),
        pronunciation=float(voice_attempt.pronunciation_score or 0),
        fluency=float(voice_attempt.fluency_score or 0),
        independence=float(voice_attempt.independence_score or 0),
        speaking=float(voice_attempt.speaking_score or 0),
        confidence=float(voice_attempt.confidence_score or 0),
        recommended_difficulty=float(voice_attempt.recommended_difficulty or child.working_difficulty or .15),
    )
    full_signals = {**(signals or {}), "attempt_number": int(voice_attempt.attempt_number or 1)}
    record = evidence_record(assessment=assessment, scores=scores, signals=full_signals, decision=decision)
    history = [*evidence_history(child), record][-10:]
    child.adaptive_evidence_json = json.dumps(history, ensure_ascii=False)
    child.adaptive_reason = decision.reason
    child.adaptive_level = decision.derived_level
    # Keep the runtime scalar aligned with the one-rung decision so the next
    # slide reads exactly the level just chosen instead of a stale average.
    child.working_difficulty = LEVEL_CENTERS[LEVELS[decision.derived_level]]
    child.adaptive_meaningful_turns = int(getattr(child, "adaptive_meaningful_turns", 0) or 0) + 1
    if decision.support_needed:
        child.adaptive_support_uses = int(getattr(child, "adaptive_support_uses", 0) or 0) + 1
    if decision.visual_action:
        child.adaptive_visual_uses = int(getattr(child, "adaptive_visual_uses", 0) or 0) + 1
    voice_attempt.adaptive_level = decision.derived_level
    voice_attempt.adaptive_reason = decision.reason
    voice_attempt.support_language_used = decision.support_needed
    voice_attempt.visual_hint_used = bool(decision.visual_action)
    voice_attempt.repair_step = decision.repair_step
    return skill_profile_payload(child, reason=decision.reason)


def complexity_support(difficulty: float) -> str:
    value = float(difficulty or 0.15)
    if value < 0.25:
        return "Можно ответить одним словом или короткой фразой."
    if value < 0.50:
        return "Ответь короткой фразой или одним предложением."
    if value < 0.72:
        return "Ответь предложением и добавь одну деталь."
    return "Расскажи подробнее и добавь пример или объяснение."


def correction_for_assessment(*, accepted: bool, semantic_match: float, attempt_number: int, ai_correction: str, authored_example: str, goal: str) -> str:
    """Prefer the short authored model when an answer is weak or repeated."""

    weak = not accepted and (float(semantic_match or 0.0) < 0.65 or int(attempt_number) > 1)
    if weak and str(authored_example or "").strip():
        return str(authored_example).strip()
    return str(ai_correction or authored_example or goal or "").strip()
