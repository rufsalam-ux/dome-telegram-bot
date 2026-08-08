from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from app.services.platform_settings import load_settings


@dataclass(frozen=True)
class ConversationDecision:
    accept_without_retry: bool = False
    should_correct: bool = False
    should_simplify: bool = False
    max_followups: int = 1
    difficulty: float = 0.15


def _cfg() -> dict[str, Any]:
    return load_settings("conversation")


def _stable_probability(key: str) -> float:
    digest = hashlib.sha256(key.encode("utf-8", errors="ignore")).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64 - 1)


def should_use_name(*, child_id: int | str, session_id: int | str, turn_key: str) -> bool:
    cfg = _cfg()
    if not cfg.get("enabled", True):
        return False
    p = max(0.0, min(1.0, float(cfg.get("name_usage_probability", 0.22))))
    return _stable_probability(f"{child_id}:{session_id}:{turn_key}:name") < p


def human_prefix(*, child_name: str, child_id: int | str, session_id: int | str, turn_key: str) -> str:
    cfg = _cfg()
    if not cfg.get("human_dialogue_phrases", True):
        return ""
    variants = list(cfg.get("help_phrases_ru") or [])
    if not variants:
        return ""
    use_name = bool(child_name) and should_use_name(child_id=child_id, session_id=session_id, turn_key=turn_key)
    idx = int(_stable_probability(f"{child_id}:{session_id}:{turn_key}:phrase") * len(variants)) % len(variants)
    phrase = str(variants[idx])
    if "{name}" in phrase and not use_name:
        nameless = [v for v in variants if "{name}" not in str(v)]
        if nameless:
            phrase = str(nameless[idx % len(nameless)])
        else:
            phrase = phrase.replace("{name}", "").replace("  ", " ").strip(" ,")
    return phrase.replace("{name}", child_name).strip()


def good_enough(*, semantic_match: float, grammar_errors: list[str], pronunciation_errors: list[str]) -> bool:
    cfg = _cfg()
    return (
        float(semantic_match or 0.0) >= float(cfg.get("good_enough_semantic_threshold", 0.62))
        and len(grammar_errors or []) <= int(cfg.get("good_enough_max_grammar_errors", 2))
        and len(pronunciation_errors or []) <= int(cfg.get("good_enough_max_pronunciation_errors", 2))
    )


def decide_retry(*, status: str, semantic_match: float, grammar_errors: list[str], pronunciation_errors: list[str], correction_count: int) -> ConversationDecision:
    cfg = _cfg()
    max_corrections = max(0, int(cfg.get("max_corrections", 1)))
    simplify_after = max(0, int(cfg.get("simplify_after_corrections", 1)))
    acceptable = good_enough(
        semantic_match=semantic_match,
        grammar_errors=grammar_errors,
        pronunciation_errors=pronunciation_errors,
    )
    if status == "RETRY_REQUIRED" and cfg.get("avoid_echo_if_good", True) and acceptable:
        return ConversationDecision(accept_without_retry=True)
    if status not in {"RETRY_REQUIRED", "WRONG_LANGUAGE"}:
        return ConversationDecision()
    if correction_count >= simplify_after:
        return ConversationDecision(should_simplify=True)
    if correction_count < max_corrections:
        return ConversationDecision(should_correct=True)
    return ConversationDecision(should_simplify=True)


def adapted_followup_limit(*, configured_max: int, working_difficulty: float, answer_score: float) -> int:
    cfg = _cfg()
    base = max(0, int(configured_max))
    if not cfg.get("live_adaptation", True):
        return base
    threshold = float(cfg.get("extra_followup_threshold", 0.68))
    if float(working_difficulty or 0) >= threshold and float(answer_score or 0) >= threshold:
        return max(base, int(cfg.get("extra_followups_if_strong", 2)))
    if float(working_difficulty or 0) <= float(cfg.get("minimal_task_threshold", 0.26)):
        return min(base, 1)
    return base


def clamp_difficulty(value: float) -> float:
    cfg = _cfg()
    lo = float(cfg.get("difficulty_floor", 0.05))
    hi = float(cfg.get("difficulty_ceiling", 0.95))
    return max(lo, min(hi, float(value)))
