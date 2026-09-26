from __future__ import annotations

import re
from dataclasses import asdict, dataclass


_ALLOWED_EMOTIONS = {
    "warm",
    "happy",
    "curious",
    "surprised",
    "encouraging",
    "gentle_correction",
}


@dataclass(frozen=True)
class TutorTurn:
    """One bounded, child-safe conversational turn returned to every client."""

    reaction_target: str = ""
    reaction_native: str = ""
    correction_target: str = ""
    follow_up_target: str = ""
    follow_up_native: str = ""
    model_answer_target: str = ""
    model_answer_native: str = ""
    native_hint: str = ""
    emotion: str = "warm"
    complete: bool = False
    skipped: bool = False
    reason: str = ""

    def payload(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ConversationPolicy:
    """Normalized authored policy for one voice task.

    ``max_turns`` counts child answers. A two-turn task therefore permits one
    follow-up question. Legacy lesson fields remain supported, but an explicit
    single-answer mode always wins so an ordinary voice task cannot become an
    open-ended conversation merely because a client sends a non-zero turn.
    """

    mode: str = "single_answer"
    enabled: bool = False
    max_turns: int = 1
    completion_condition: str = "after_first_accepted_answer"

    @property
    def max_follow_ups(self) -> int:
        return max(0, self.max_turns - 1)


def conversation_policy_for_task(task: dict | None) -> ConversationPolicy:
    authored = task if isinstance(task, dict) else {}
    raw_mode = str(
        authored.get("conversation_mode")
        or authored.get("conversationMode")
        or ""
    ).strip().lower()
    explicit_single = raw_mode in {"single", "single_answer", "one_shot", "none", "off"}
    legacy_enabled = bool(authored.get("allow_ai_followup")) or str(authored.get("follow_up_policy") or "").lower() == "optional"
    enabled = not bool(authored.get("suppress_ai_followup")) and not explicit_single and (
        raw_mode in {"dialogue", "conversation", "multi_turn", "roleplay"} or legacy_enabled
    )
    legacy_follow_ups = max(0, int(authored.get("max_ai_followups") or 0))
    raw_max_turns = authored.get("max_turns", authored.get("maxTurns"))
    try:
        max_turns = int(raw_max_turns) if raw_max_turns is not None else (legacy_follow_ups + 1 if legacy_follow_ups else (2 if enabled else 1))
    except (TypeError, ValueError):
        max_turns = 2 if enabled else 1
    max_turns = max(2, min(8, max_turns)) if enabled else 1
    completion = str(
        authored.get("completion_condition")
        or authored.get("completionCondition")
        or ("max_turns_or_natural_close" if enabled else "after_first_accepted_answer")
    ).strip()
    return ConversationPolicy(
        mode=raw_mode or ("multi_turn" if enabled else "single_answer"),
        enabled=enabled,
        max_turns=max_turns,
        completion_condition=completion,
    )


def _compact(text: object, *, max_chars: int = 280) -> str:
    value = " ".join(str(text or "").strip().split())
    return value[:max_chars].strip()


def _one_question(text: object) -> str:
    """Keep a PRE_A1 turn short and enforce at most one question."""

    value = _compact(text)
    if not value:
        return ""
    question_seen = False
    output: list[str] = []
    for part in re.split(r"(?<=[.!?])\s+", value):
        if "?" in part:
            if question_seen:
                continue
            question_seen = True
            part = part.split("?", 1)[0].strip() + "?"
        output.append(part)
        if len(output) >= 2:
            break
    return " ".join(output).strip()


_GENERIC_PRAISE = {
    "nice", "nice job", "great", "great job", "good", "good job", "well done",
    "amazing", "awesome", "excellent", "super", "perfect", "bravo",
    "молодец", "отлично", "здорово", "хорошо", "супер", "прекрасно",
}


def _is_generic_praise(text: str) -> bool:
    normalized = re.sub(r"[^\w\s]", "", str(text or "").casefold()).strip()
    normalized = " ".join(normalized.split())
    return normalized in _GENERIC_PRAISE


def adaptive_follow_up_policy(
    *,
    authored_enabled: bool,
    authored_max: int,
    language_level: str,
    attempt_number: int,
    transcript: str,
    confidence: float,
    semantic_match: float | None = None,
    required_dialogue: bool = False,
) -> tuple[bool, int, str]:
    """Permit harder follow-ups only after an independent, confident answer."""

    level = str(language_level or "PRE_A1").upper()
    level_cap = 2 if level == "PRE_A1" else 3
    authored_limit = max(0, min(7, int(authored_max)))
    # An explicit authored dialogue controls its own bounded number of turns.
    # The language-level cap is for opportunistic follow-ups, not for cutting
    # short a task that intentionally asks the child to keep talking.
    maximum = authored_limit if required_dialogue else min(level_cap, authored_limit)
    words = re.findall(r"\w+", str(transcript or ""), flags=re.UNICODE)
    is_strong_attempt = int(attempt_number) == 1 or (semantic_match is not None and float(semantic_match) >= 0.85)
    if required_dialogue:
        continue_dialogue = (
            authored_enabled
            and maximum > 0
            and float(confidence or 0.0) >= 0.35
            and bool(words)
        )
        return continue_dialogue, maximum, "configured_dialogue" if continue_dialogue else "dialogue_needs_retry"
    strong = (
        authored_enabled
        and maximum > 0
        and is_strong_attempt
        and float(confidence or 0.0) >= 0.75
        and len(words) >= (1 if level == "PRE_A1" else 2)
        and (semantic_match is None or float(semantic_match) >= 0.82)
    )
    return strong, maximum, "strong_independent_answer" if strong else "support_or_completion"


def build_assessed_turn(
    result: dict,
    *,
    accepted: bool,
    allow_follow_up: bool,
    follow_up_count: int,
    max_follow_ups: int,
    answer_text: str = "",
) -> TutorTurn:
    """Normalize an AI assessment into the shared runtime contract.

    The model can suggest a follow-up, but authored lesson policy is the final
    authority. This prevents accidental extra questions or endless dialogue.
    """

    reaction = _compact(result.get("reaction_target") or result.get("response_target")).replace("?", ".")
    if _is_generic_praise(reaction):
        # A bare "Great!" is not evidence that the tutor listened. Ground a
        # successful reaction in the actual answer; never praise a failed take.
        reaction = f"{_compact(answer_text, max_chars=60).rstrip('.!?')}!" if accepted and _compact(answer_text) else ""
    correction = _compact(result.get("corrected_target"))
    follow_up = _one_question(result.get("follow_up_target"))
    can_follow = accepted and allow_follow_up and follow_up_count < max(0, int(max_follow_ups))
    if not can_follow:
        follow_up = ""
    emotion = str(result.get("emotion") or "warm").strip().lower()
    if emotion not in _ALLOWED_EMOTIONS:
        emotion = "warm"
    return TutorTurn(
        reaction_target=reaction,
        reaction_native=_compact(result.get("reaction_native") or result.get("response_native")),
        correction_target=correction,
        follow_up_target=follow_up,
        follow_up_native=_compact(result.get("follow_up_native")) if follow_up else "",
        model_answer_target=_compact(result.get("model_answer_target") or correction),
        model_answer_native=_compact(result.get("model_answer_native")),
        native_hint=_compact(result.get("native_hint")),
        emotion=emotion,
        complete=accepted and not follow_up,
        reason="accepted" if accepted else "retry",
    )


def no_speech_turn(
    attempt_number: int,
    max_attempts: int,
    *,
    target_retry: str,
    native_hint: str = "",
    model_answer: str = "",
) -> TutorTurn:
    """Progressive assistance for silence; never represents success."""

    attempt = max(1, int(attempt_number))
    maximum = max(1, int(max_attempts))
    if attempt == 1:
        return TutorTurn(
            reaction_target=_compact(target_retry),
            native_hint=_compact(native_hint),
            emotion="encouraging",
            complete=False,
            reason="no_speech_retry",
        )
    if attempt < maximum:
        return TutorTurn(
            reaction_target=_compact(target_retry),
            model_answer_target=_compact(model_answer),
            native_hint=_compact(native_hint),
            emotion="encouraging",
            complete=False,
            reason="no_speech_model",
        )
    return TutorTurn(
        reaction_target=_compact(target_retry),
        native_hint=_compact(native_hint),
        emotion="warm",
        complete=True,
        skipped=True,
        reason="no_speech_skipped",
    )
