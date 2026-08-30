from __future__ import annotations

import pytest

from app.services import speech_pipeline
from app.services.lesson_voice_context import (
    authoritative_voice_context,
    contextual_assessment_goal,
    referenced_items_are_visible,
    selected_item_turn,
)


SUITCASE = {
    "type": "drag_and_drop",
    "interactive_task": "suitcase",
    "selection_policy": "child_choice",
    "drag_items": [
        {"id": "camera", "label_en": "Camera", "label_ru": "фотоаппарат"},
        {"id": "fish", "label_en": "Fish", "label_ru": "рыба"},
    ],
}


def test_server_context_rejects_client_items_that_are_not_visible():
    context = authoritative_voice_context(
        SUITCASE,
        {"visible_items": ["camera", "passport"], "selected_items": ["fish", "passport"], "removed_items": ["camera", "boots"]},
        "en",
        "ru",
    )
    assert [item["id"] for item in context["visible_items"]] == ["camera"]
    assert context["selected_items"] == []
    assert [item["id"] for item in context["removed_items"]] == ["camera"]
    assert context["selection_policy"] == "child_choice"


def test_suitcase_choice_has_no_hidden_correct_set_and_any_visible_item_is_valid_context():
    context = authoritative_voice_context(
        SUITCASE,
        {"visible_items": ["camera", "fish"], "selected_items": ["fish"]},
        "en",
        "ru",
    )
    goal = contextual_assessment_goal("What will you take?", context)
    assert "fish" in goal.casefold() and "hidden correct set" in goal.casefold()
    assert "camera" not in goal
    assert referenced_items_are_visible({"referenced_item_ids": ["fish"]}, context) is True
    assert referenced_items_are_visible({"referenced_item_ids": ["passport"]}, context) is False


@pytest.mark.asyncio
async def test_free_child_phrase_is_accepted_by_meaning_not_sample_string(monkeypatch, tmp_path):
    monkeypatch.setattr(speech_pipeline.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(speech_pipeline, "transcribe_audio", lambda *_args, **_kwargs: _async(("The parrot has big wings.", "en", .96)))
    monkeypatch.setattr(speech_pipeline, "_evaluate_with_chat", lambda prompt: _async({
        "decision": "CORRECT",
        "semantic_match": .93,
        "reaction_target": "Big wings help it fly!",
        "referenced_item_ids": ["parrot"],
        "emotion": "happy",
    }))
    context = {
        "task_type": "animal_compare",
        "visible_items": [{"id": "parrot", "label_target": "parrot", "label_native": "попугай"}],
        "selected_items": [{"id": "parrot", "label_target": "parrot", "label_native": "попугай"}],
        "allow_unlisted_items": False,
    }
    result = await speech_pipeline.assess_speech(
        tmp_path / "voice.wav", "en", "ru", "Say something about the parrot.",
        ["a relevant idea about the parrot"], 1, runtime_context=context,
    )
    assert result.status == "ACCEPTED_CORRECT"
    assert result.semantic_match == .93
    assert result.transcript != "The parrot is red and beautiful."


@pytest.mark.asyncio
async def test_native_language_idea_is_preserved_in_target_language_help(monkeypatch, tmp_path):
    monkeypatch.setattr(speech_pipeline.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(speech_pipeline, "transcribe_audio", lambda *_args, **_kwargs: _async(("Я возьму камеру, чтобы фотографировать.", "ru", .95)))
    monkeypatch.setattr(speech_pipeline, "_evaluate_with_chat", lambda prompt: _async({
        "decision": "WRONG_LANGUAGE",
        "semantic_match": .9,
        "corrected_target": "I will take the camera to take photos.",
        "model_answer_target": "I will take the camera to take photos.",
        "referenced_item_ids": ["camera"],
        "emotion": "encouraging",
    }))
    context = authoritative_voice_context(SUITCASE, {"visible_items": ["camera", "fish"], "selected_items": ["camera"]}, "en", "ru")
    result = await speech_pipeline.assess_speech(
        tmp_path / "voice.wav", "en", "ru", "Tell me about your choice.", [], 1, runtime_context=context,
    )
    assert result.status == "WRONG_LANGUAGE"
    assert result.corrected_target == "I will take the camera to take photos."


@pytest.mark.asyncio
async def test_ai_reference_to_invisible_item_is_rejected_before_child_response(monkeypatch, tmp_path):
    monkeypatch.setattr(speech_pipeline.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(speech_pipeline, "transcribe_audio", lambda *_args, **_kwargs: _async(("I will take the camera.", "en", .95)))
    monkeypatch.setattr(speech_pipeline, "_evaluate_with_chat", lambda prompt: _async({
        "decision": "CORRECT",
        "semantic_match": 1,
        "reaction_target": "Pack your passport too.",
        "follow_up_target": "Where are your boots?",
        "referenced_item_ids": ["passport", "boots"],
    }))
    context = authoritative_voice_context(SUITCASE, {"visible_items": ["camera", "fish"], "selected_items": ["camera"]}, "en", "ru")
    result = await speech_pipeline.assess_speech(
        tmp_path / "voice.wav", "en", "ru", "Tell me about your choice.", [], 1, runtime_context=context,
    )
    assert result.status == "RETRY_REQUIRED"
    assert not result.tutor_turn or (not result.tutor_turn.reaction_target and not result.tutor_turn.follow_up_target)


def test_target_and_native_text_share_one_semantic_turn():
    turn = selected_item_turn(
        "You chose the camera!", "Ты выбрал фотоаппарат!",
        follow_up_target="Why did you choose the camera?",
        follow_up_native="Почему ты выбрал фотоаппарат?",
    )
    assert "camera" in turn.reaction_target and "фотоаппарат" in turn.reaction_native
    assert "camera" in turn.follow_up_target and "фотоаппарат" in turn.native_hint


async def _async(value):
    return value
