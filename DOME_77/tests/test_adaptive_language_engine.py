from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

from app.services.adaptive_learning import adaptive_decision, derived_level, score_answer, skill_profile_payload
from app.services.ai_speech import synthesize_speech
from app.services.lesson_runtime import persist_adaptive_decision
from app.services.speech_pipeline import SpeechAssessment, assess_speech


VISIBLE = [
    {"id": "jacket", "label_target": "куртка", "asset_reference": "jacket.png"},
    {"id": "bear", "label_target": "мишка", "asset_reference": "bear.png"},
]


def profile(level: int) -> dict:
    return {"derived_level": level, "level_name": ("ZERO", "WORDS", "SIMPLE", "CONVERSATIONAL", "CONFIDENT")[level]}


def test_zero_level_uses_progressive_target_first_ladder() -> None:
    first = adaptive_decision(
        profile=profile(0), accepted=False, attempt_number=1, hints_used=0,
        target_language="ru", support_language="en", visible_items=VISIBLE,
    )
    second = adaptive_decision(
        profile=profile(0), accepted=False, attempt_number=2, hints_used=0,
        target_language="ru", support_language="en", visible_items=VISIBLE,
    )
    fifth = adaptive_decision(
        profile=profile(0), accepted=False, attempt_number=5, hints_used=0,
        target_language="ru", support_language="en", visible_items=VISIBLE,
    )
    assert first.repair_step == 1 and first.support_needed is True
    assert first.visual_action == {"type": "highlight", "objectIds": ["jacket"]}
    assert second.expected_response_type == "choice_or_tap"
    assert second.visual_action == {"type": "highlight", "objectIds": ["jacket"]}
    assert fifth.support_needed is True
    assert fifth.difficulty_used == 0


def test_simple_and_confident_profiles_keep_distinct_response_difficulty() -> None:
    simple = adaptive_decision(
        profile=profile(2), accepted=True, attempt_number=1, hints_used=0,
        target_language="ru", support_language="en", visible_items=VISIBLE,
    )
    confident = adaptive_decision(
        profile=profile(4), accepted=True, attempt_number=1, hints_used=0,
        target_language="ru", support_language="en", visible_items=VISIBLE,
    )
    assert simple.difficulty_used == 3
    assert confident.difficulty_used == 4
    assert confident.support_needed is False and confident.visual_action is None


def test_five_successes_rise_one_rung_and_failure_rolls_back_one() -> None:
    level = 0
    seen = []
    for _ in range(5):
        decision = adaptive_decision(
            profile={**profile(level), "observed_level": 4}, previous_level=level,
            accepted=True, attempt_number=1, hints_used=0,
            target_language="ru", support_language="en", visible_items=VISIBLE,
        )
        seen.append(decision.difficulty_used)
        level = decision.derived_level
    assert seen == [1, 2, 3, 4, 4]
    rollback = adaptive_decision(
        profile={**profile(level), "observed_level": 2}, previous_level=level,
        accepted=False, attempt_number=1, hints_used=0,
        target_language="ru", support_language="en", visible_items=VISIBLE,
    )
    assert rollback.difficulty_used == 3
    assert rollback.visual_action == {"type": "highlight", "objectIds": ["jacket"]}


def test_understands_but_speaks_little_does_not_collapse_comprehension() -> None:
    scores = score_answer(
        semantic_match=.92, grammar_errors=[], pronunciation_errors=[], transcript="да",
        attempt_number=1, status="ACCEPTED_CORRECT", used_native_language=True,
        understood_question=True, on_topic=True, target_word_count=0,
        lexical_diversity=.2, grammar_complexity=.1, confidence_evidence=.75,
    )
    level = derived_level(comprehension=scores.comprehension, vocabulary=.62, grammar=.72, confidence=.74)
    assert scores.comprehension >= .90
    assert scores.speaking < scores.comprehension
    assert level >= 2


def _child() -> SimpleNamespace:
    return SimpleNamespace(
        language_level="A1", answers_count=1, working_difficulty=.35,
        comprehension_score=.7, vocabulary_score=.55, grammar_score=.6,
        pronunciation_score=.7, fluency_score=.45, independence_score=.5,
        speaking_score=.4, confidence_score=.55, adaptive_level=2,
        adaptive_evidence_json="[]", adaptive_reason="", adaptive_support_uses=0,
        adaptive_visual_uses=0, adaptive_meaningful_turns=0,
    )


def _attempt() -> SimpleNamespace:
    return SimpleNamespace(
        attempt_number=1, comprehension_score=.7, vocabulary_score=.55,
        grammar_score=.6, pronunciation_score=.7, fluency_score=.45,
        independence_score=.5, speaking_score=.4, confidence_score=.55,
        recommended_difficulty=.5,
    )


def test_profile_persists_only_ten_recent_meaningful_turns() -> None:
    child = _child()
    assessment = SpeechAssessment(
        transcript="я вижу куртку", status="ACCEPTED_CORRECT", semantic_match=.9,
        adaptive_assessment={"understood_question": True, "on_topic": True, "target_word_count": 3},
    )
    for index in range(12):
        decision = adaptive_decision(
            profile=profile(2), accepted=index % 3 != 0, attempt_number=1,
            hints_used=0, target_language="ru", support_language="en", visible_items=VISIBLE,
        )
        persist_adaptive_decision(child, _attempt(), assessment, {"response_latency_ms": 900}, decision)
    evidence = json.loads(child.adaptive_evidence_json)
    saved = skill_profile_payload(child)
    assert len(evidence) == 10
    assert saved["meaningful_turns"] == 12
    assert set(("comprehension", "vocabulary", "speaking", "grammar", "confidence")) <= set(saved)


def test_visual_action_never_targets_an_absent_object() -> None:
    decision = adaptive_decision(
        profile=profile(1), accepted=False, attempt_number=2, hints_used=0,
        target_language="ru", support_language="en", visible_items=VISIBLE,
        model_visual_action={"type": "highlight", "objectIds": ["imaginary_dragon"]},
    )
    assert decision.visual_action == {"type": "highlight", "objectIds": ["jacket"]}


def test_adaptation_reuses_the_single_dialogue_ai_call() -> None:
    source = inspect.getsource(assess_speech)
    assert source.count("_evaluate_with_chat(") == 1
    assert "adaptive_profile" in source
    assert "adaptive_policy" in source


def test_tts_restores_warm_emotional_style_without_an_extra_ai_call() -> None:
    source = inspect.getsource(synthesize_speech)
    assert 'payload["instructions"]' in source
    assert "soft, friendly, youthful feminine voice" in source
    assert "style_instruction" in source
    assert source.count("/v1/audio/speech") == 1
