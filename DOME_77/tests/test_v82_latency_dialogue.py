import pytest
from app.services.conversational_tutor import TutorTurn, adaptive_follow_up_policy, build_assessed_turn
from app.services.speech_pipeline import SpeechAssessment

def test_tutor_turn_bilingual_fields():
    turn = TutorTurn(
        reaction_target="Great job!",
        reaction_native="Отличная работа!",
        follow_up_target="What color is it?",
        follow_up_native="Какого оно цвета?",
        model_answer_target="It is blue.",
        model_answer_native="Оно синее.",
    )
    payload = turn.payload()
    assert payload["reaction_target"] == "Great job!"
    assert payload["reaction_native"] == "Отличная работа!"
    assert payload["follow_up_target"] == "What color is it?"
    assert payload["follow_up_native"] == "Какого оно цвета?"
    assert payload["model_answer_target"] == "It is blue."
    assert payload["model_answer_native"] == "Оно синее."

def test_build_assessed_turn_single_pass_bilingual():
    result = {
        "reaction_target": "I love that elephant!",
        "reaction_native": "Мне очень нравится этот слон!",
        "follow_up_target": "Where does he live?",
        "follow_up_native": "Где он живёт?",
        "model_answer_target": "He lives in Africa.",
        "model_answer_native": "Он живёт в Африке.",
        "corrected_target": "It is an elephant.",
        "decision": "CORRECT",
        "emotion": "happy",
    }
    turn = build_assessed_turn(
        result,
        accepted=True,
        allow_follow_up=True,
        follow_up_count=0,
        max_follow_ups=2,
        answer_text="An elephant",
    )
    assert turn.reaction_target == "I love that elephant!"
    assert turn.reaction_native == "Мне очень нравится этот слон!"
    assert turn.follow_up_target == "Where does he live?"
    assert turn.follow_up_native == "Где он живёт?"
    assert turn.model_answer_target == "He lives in Africa."
    assert turn.model_answer_native == "Он живёт в Африке."
    assert turn.emotion == "happy"
    assert turn.complete is False

def test_adaptive_follow_up_policy_turn_continuation():
    strong, maximum, _ = adaptive_follow_up_policy(
        authored_enabled=True,
        authored_max=2,
        language_level="PRE_A1",
        attempt_number=1,
        transcript="big elephant",
        confidence=0.92,
        semantic_match=0.90,
    )
    assert strong is True
    assert maximum == 2

    strong2, _, _ = adaptive_follow_up_policy(
        authored_enabled=True,
        authored_max=2,
        language_level="PRE_A1",
        attempt_number=2,
        transcript="big elephant",
        confidence=0.88,
        semantic_match=0.90,
    )
    assert strong2 is True

    weak, _, _ = adaptive_follow_up_policy(
        authored_enabled=True,
        authored_max=2,
        language_level="A2",
        attempt_number=2,
        transcript="elephant",
        confidence=0.60,
        semantic_match=0.50,
    )
    assert weak is False

def test_speech_assessment_bilingual_passthrough():
    assessment = SpeechAssessment(
        transcript="I see a cat",
        detected_language="en",
        confidence=0.95,
        status="ACCEPTED_CORRECT",
        response_target="What a lovely cat!",
        response_native="Какой милый кот!",
        follow_up_target="What is his name?",
        follow_up_native="Как его зовут?",
        model_answer_target="His name is Tom.",
        model_answer_native="Его зовут Том.",
        child_phrase_native="Я вижу кота",
    )
    assert assessment.response_target == "What a lovely cat!"
    assert assessment.response_native == "Какой милый кот!"
    assert assessment.follow_up_target == "What is his name?"
    assert assessment.follow_up_native == "Как его зовут?"
    assert assessment.model_answer_native == "Его зовут Том."
    assert assessment.child_phrase_native == "Я вижу кота"
