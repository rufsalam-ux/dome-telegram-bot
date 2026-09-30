from __future__ import annotations
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

LEVELS = ["PRE_A1", "A1", "A2", "B1", "B2"]
LEVEL_CENTERS = {"PRE_A1": 0.12, "A1": 0.30, "A2": 0.50, "B1": 0.70, "B2": 0.88}

@dataclass
class AdaptiveScores:
    comprehension: float
    grammar: float
    vocabulary: float
    pronunciation: float
    fluency: float
    independence: float
    speaking: float
    confidence: float
    recommended_difficulty: float


@dataclass(frozen=True)
class AdaptiveDecision:
    """One deterministic, child-safe adaptive decision for the next turn.

    The dialogue model supplies evidence in the same request that produces the
    semantic response.  This policy is deliberately local/deterministic so an
    adaptive-provider failure can never block a lesson or add another LLM call.
    """

    derived_level: int
    difficulty_used: int
    repair_step: int
    support_needed: bool
    expected_response_type: str
    visual_action: dict[str, Any] | None
    reason: str


def clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def score_answer(*, semantic_match: float, grammar_errors: list[str], pronunciation_errors: list[str],
                 transcript: str, attempt_number: int, status: str, response_latency_ms: int = 0,
                 hints_used: int = 0, used_native_language: bool = False,
                 open_question: bool = False, understood_question: bool | None = None,
                 on_topic: bool | None = None, target_word_count: int | None = None,
                 lexical_diversity: float | None = None, grammar_complexity: float | None = None,
                 confidence_evidence: float | None = None, hesitation: bool = False,
                 choice_only: bool = False) -> AdaptiveScores:
    tokens = [word for word in (transcript or "").split() if word]
    words = len(tokens)
    comprehension = clamp(
        .55 * float(semantic_match or 0.0)
        + .30 * (1.0 if understood_question is True else .35 if understood_question is None else 0.0)
        + .15 * (1.0 if on_topic is True else .35 if on_topic is None else 0.0)
    )
    grammar = clamp(1.0 - min(len(grammar_errors), 4) * 0.18)
    pronunciation = clamp(1.0 - min(len(pronunciation_errors), 4) * 0.18)
    diversity = clamp(lexical_diversity if lexical_diversity is not None else (len({value.casefold() for value in tokens}) / max(1, words)))
    vocabulary = clamp(0.10 + words / 18.0 + diversity * .25)
    fluency = clamp(0.25 + words / 18.0)
    latency_penalty = .0 if response_latency_ms <= 0 else min(.18, max(0, response_latency_ms - 3500) / 45_000)
    independence = clamp(1.0 - max(attempt_number - 1, 0) * 0.22 - min(3, max(0, hints_used)) * .10 - latency_penalty)
    if open_question and attempt_number == 1 and words >= 3 and semantic_match >= .7:
        independence = clamp(independence + .08)
    if status == "WRONG_LANGUAGE" or used_native_language:
        # Speaking in the support language can still prove comprehension.  Do
        # not collapse understanding merely because productive speech lags.
        independence *= 0.55
    if status == "TECHNICAL_UNCERTAINTY":
        return AdaptiveScores(0, 0, 0, 0, 0, 0, 0, 0, 0.0)
    target_words = max(0, int(target_word_count if target_word_count is not None else (0 if used_native_language else words)))
    productive_ratio = target_words / max(1, words)
    speaking = clamp(productive_ratio * .45 + min(words, 10) / 10.0 * .35 + (grammar_complexity if grammar_complexity is not None else grammar) * .20)
    confidence = clamp(
        (confidence_evidence if confidence_evidence is not None else independence) * .55
        + independence * .30
        + (0.0 if hesitation else .15)
        - (.12 if choice_only else 0.0)
    )
    overall = (
        comprehension * .35 + vocabulary * .20 + grammar * .15 + confidence * .15 +
        speaking * .10 + independence * .05
    )
    return AdaptiveScores(comprehension, grammar, vocabulary, pronunciation, fluency, independence, speaking, confidence, clamp(overall))


def update_running_average(old: float, count: int, new: float) -> float:
    return clamp((old * count + new) / (count + 1))


def level_from_score(score: float, previous: str = "PRE_A1", min_answers: int = 6) -> str:
    if min_answers < 6:
        return previous
    if score >= .80: return "B2"
    if score >= .64: return "B1"
    if score >= .46: return "A2"
    if score >= .25: return "A1"
    return "PRE_A1"


def proficiency_band(score: float) -> str:
    value = clamp(score)
    if value < .25: return "beginner"
    if value < .50: return "emerging"
    if value < .72: return "intermediate"
    return "strong"


def derived_level(*, comprehension: float, vocabulary: float, grammar: float, confidence: float) -> int:
    """Return language-input difficulty without conflating it with speaking.

    A child who understands but answers with one word therefore keeps normal
    target-language input while receiving a lighter response requirement.
    """

    value = clamp(comprehension * .50 + vocabulary * .25 + grammar * .10 + confidence * .15)
    if value < .20:
        return 0
    if value < .38:
        return 1
    if value < .58:
        return 2
    if value < .78:
        return 3
    return 4


def _profile_value(child: Any, name: str, fallback: float = 0.0) -> float:
    return clamp(float(getattr(child, name, fallback) or fallback))


def initial_level_score(language_level: str) -> float:
    return clamp(LEVEL_CENTERS.get(str(language_level or "PRE_A1").upper(), .12))


def evidence_history(child: Any) -> list[dict[str, Any]]:
    try:
        value = json.loads(str(getattr(child, "adaptive_evidence_json", "[]") or "[]"))
        return [item for item in value if isinstance(item, dict)][-10:] if isinstance(value, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def skill_profile_payload(child: Any, *, reason: str | None = None) -> dict[str, Any]:
    count = int(getattr(child, "adaptive_meaningful_turns", 0) or getattr(child, "answers_count", 0) or 0)
    hypothesis = initial_level_score(str(getattr(child, "language_level", "PRE_A1") or "PRE_A1"))
    comprehension = _profile_value(child, "comprehension_score", hypothesis if count == 0 else 0)
    vocabulary = _profile_value(child, "vocabulary_score", hypothesis if count == 0 else 0)
    grammar = _profile_value(child, "grammar_score", hypothesis if count == 0 else 0)
    speaking = _profile_value(child, "speaking_score", hypothesis * .75 if count == 0 else 0)
    confidence = _profile_value(child, "confidence_score", hypothesis * .8 if count == 0 else 0)
    observed_level = derived_level(comprehension=comprehension, vocabulary=vocabulary, grammar=grammar, confidence=confidence)
    persisted_level = getattr(child, "adaptive_level", None)
    level = max(0, min(4, int(persisted_level))) if count > 0 and persisted_level is not None else observed_level
    support_uses = int(getattr(child, "adaptive_support_uses", 0) or 0)
    visual_uses = int(getattr(child, "adaptive_visual_uses", 0) or 0)
    return {
        "comprehension": round(comprehension * 100, 1),
        "vocabulary": round(vocabulary * 100, 1),
        "speaking": round(speaking * 100, 1),
        "grammar": round(grammar * 100, 1),
        "confidence": round(confidence * 100, 1),
        "derived_level": level,
        "observed_level": observed_level,
        "level_name": ("ZERO", "WORDS", "SIMPLE", "CONVERSATIONAL", "CONFIDENT")[level],
        "meaningful_turns": count,
        "support_language_usage_rate": round(support_uses / max(1, count), 3),
        "visual_hint_usage_rate": round(visual_uses / max(1, count), 3),
        "reason": str(reason if reason is not None else getattr(child, "adaptive_reason", "initial hypothesis") or "initial hypothesis"),
        "evidence": evidence_history(child),
    }


def adaptive_decision(
    *,
    profile: dict[str, Any],
    accepted: bool,
    attempt_number: int,
    hints_used: int,
    target_language: str,
    support_language: str,
    visible_items: list[dict[str, Any]] | None = None,
    model_visual_action: dict[str, Any] | None = None,
    model_support_needed: bool = False,
    previous_level: int | None = None,
) -> AdaptiveDecision:
    observed_level = max(0, min(4, int(profile.get("observed_level", profile.get("derived_level", 0)) or 0)))
    prior = max(0, min(4, int(previous_level if previous_level is not None else profile.get("derived_level", 0) or 0)))
    # One meaningful turn moves exactly one rung. Evidence may recommend a
    # higher/lower destination, but never produces a startling multi-level jump.
    level = min(4, prior + 1) if accepted else max(0, prior - 1)
    if accepted and observed_level < prior:
        level = prior
    repair_step = 0 if accepted else max(1, min(5, int(attempt_number or 1) + max(0, int(hints_used or 0))))
    visible = [item for item in (visible_items or []) if isinstance(item, dict) and str(item.get("id") or "")]
    visible_ids = [str(item["id"]) for item in visible]
    action: dict[str, Any] | None = None
    requested = model_visual_action if isinstance(model_visual_action, dict) else {}
    action_type = str(requested.get("type") or "")
    requested_ids = [str(value) for value in (requested.get("objectIds") or requested.get("object_ids") or []) if str(value)]
    requested_id = str(requested.get("objectId") or requested.get("object_id") or "")
    if requested_id:
        requested_ids = [requested_id]
    if action_type in {"highlight", "animate", "point", "showChoice"} and requested_ids and all(value in visible_ids for value in requested_ids):
        action = {"type": action_type, "objectIds": requested_ids[:2]}
    elif visible_ids and not accepted:
        if repair_step == 3 and len(visible_ids) >= 2:
            action = {"type": "showChoice", "objectIds": visible_ids[:2]}
        else:
            action = {"type": "point" if repair_step >= 4 else "highlight", "objectIds": visible_ids[:1]}
    support_needed = bool(
        not accepted
        and (prior == 0 or repair_step >= 2 or model_support_needed)
        and str(target_language or "").lower() != str(support_language or "").lower()
    )
    expected = "choice_or_tap" if level == 0 and repair_step >= 2 else "word_or_voice" if level <= 1 else "voice"
    reason = (
        "accepted without extra help"
        if accepted
        else f"repair step {repair_step}: " + ("support language required" if support_needed else "simpler target-language scaffolding")
    )
    return AdaptiveDecision(level, level, repair_step, support_needed, expected, action, reason)


def evidence_record(*, assessment: Any, scores: AdaptiveScores, signals: dict[str, Any], decision: AdaptiveDecision) -> dict[str, Any]:
    transcript = str(getattr(assessment, "transcript", "") or "")
    evidence = getattr(assessment, "adaptive_assessment", {}) or {}
    return {
        "at": datetime.now(UTC).isoformat(),
        "status": str(getattr(assessment, "status", "") or ""),
        "understood_question": bool(evidence.get("understood_question", scores.comprehension >= .55)),
        "on_topic": bool(evidence.get("on_topic", float(getattr(assessment, "semantic_match", 0) or 0) >= .55)),
        "target_word_count": int(evidence.get("target_word_count", len(transcript.split())) or 0),
        "phrase_word_count": len(transcript.split()),
        "attempts": int(signals.get("attempt_number") or 1),
        "response_latency_ms": int(signals.get("response_latency_ms") or 0),
        "repetition_required": int(signals.get("attempt_number") or 1) > 1,
        "simplification_required": decision.repair_step >= 1,
        "visual_hint_used": bool(decision.visual_action),
        "support_language_used": bool(decision.support_needed),
        "choice_only": bool(signals.get("choice_only")),
        "hesitation": bool(evidence.get("hesitation", False)),
        "confidence": round(scores.confidence, 4),
        "derived_level": decision.derived_level,
        "reason": decision.reason,
    }


def adapt_prompt(base: str, difficulty: float) -> str:
    # The same lesson task can shrink or expand during the lesson.
    if difficulty < .25:
        return base + " Можно ответить одним словом или просто назвать то, что видишь."
    if difficulty < .50:
        return base + " Ответь короткой фразой или одним предложением."
    if difficulty < .72:
        return base + " Ответь предложением и добавь одну деталь."
    return base + " Расскажи подробнее: объясни почему или добавь пример."
