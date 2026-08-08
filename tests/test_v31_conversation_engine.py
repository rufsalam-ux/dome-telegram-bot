from app.services.conversation_engine import good_enough, decide_retry, adapted_followup_limit, clamp_difficulty
from app.services.platform_settings import load_settings


def test_conversation_config_exists():
    cfg = load_settings("conversation")
    assert cfg["enabled"] is True
    assert 0 <= cfg["name_usage_probability"] <= 1
    assert cfg["max_corrections"] == 1


def test_good_answer_does_not_force_repeat():
    assert good_enough(semantic_match=.82, grammar_errors=["small"], pronunciation_errors=[])
    d = decide_retry(status="RETRY_REQUIRED", semantic_match=.82, grammar_errors=["small"], pronunciation_errors=[], correction_count=0)
    assert d.accept_without_retry is True


def test_one_correction_then_simplification():
    first = decide_retry(status="RETRY_REQUIRED", semantic_match=.2, grammar_errors=["a","b","c"], pronunciation_errors=[], correction_count=0)
    assert first.should_correct is True
    second = decide_retry(status="RETRY_REQUIRED", semantic_match=.2, grammar_errors=["a","b","c"], pronunciation_errors=[], correction_count=1)
    assert second.should_simplify is True


def test_strong_child_can_get_more_dialogue():
    assert adapted_followup_limit(configured_max=1, working_difficulty=.8, answer_score=.85) >= 2


def test_difficulty_clamped():
    assert 0.0 < clamp_difficulty(-4) < 1.0
    assert 0.0 < clamp_difficulty(4) < 1.0
