import pytest
from app.services import speech_pipeline
from app.services.conversational_tutor import (
    adaptive_follow_up_policy,
    build_assessed_turn,
    conversation_policy_for_task,
)
from app.webapp.mobile_api import _append_dialogue_turn


async def _async(val):
    return val


def test_conversation_policy_across_all_four_scenarios():
    # 1. Ordinary voice slide (no explicit conversation_mode)
    ordinary_slide = {
        "slide_id": "slide_03",
        "answer_mode": "required_voice",
        "question": "Скажи привет!",
    }
    policy_ordinary = conversation_policy_for_task(ordinary_slide)
    assert policy_ordinary.enabled is True
    assert policy_ordinary.mode == "multi_turn"
    assert policy_ordinary.max_turns == 5

    # 2. Lyosha scene (slide_19)
    lyosha_slide = {
        "slide_id": "slide_19",
        "answer_mode": "required_voice",
        "conversation_mode": "multi_turn",
        "allow_ai_followup": True,
        "max_turns": 5,
    }
    policy_lyosha = conversation_policy_for_task(lyosha_slide)
    assert policy_lyosha.enabled is True
    assert policy_lyosha.mode == "multi_turn"
    assert policy_lyosha.max_turns == 5

    # 3. Suitcase / selection slide
    suitcase_slide = {
        "slide_id": "slide_07",
        "type": "card_selector",
        "answer_mode": "optional_voice",
        "task_goal": "Собери чемодан в поездку.",
    }
    policy_suitcase = conversation_policy_for_task(suitcase_slide)
    assert policy_suitcase.enabled is True
    assert policy_suitcase.mode == "multi_turn"
    assert policy_suitcase.max_turns == 5

    # 4. Final mood slide
    mood_slide = {
        "slide_id": "slide_49",
        "type": "mood_choice",
        "answer_mode": "optional_voice",
        "task_goal": "Какое у тебя настроение?",
    }
    policy_mood = conversation_policy_for_task(mood_slide)
    assert policy_mood.enabled is True
    assert policy_mood.mode == "multi_turn"
    assert policy_mood.max_turns == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario,slide_meta,transcripts,reactions,questions", [
    (
        "ordinary_voice",
        {"slide_id": "slide_03", "answer_mode": "required_voice", "task_goal": "Расскажи о себе."},
        [
            "Меня зовут Саша и мне пять лет.",
            "Я люблю играть в машинки.",
            "Красная пожарная машинка.",
            "Да, мы играем вместе с папой.",
            "Мы строим большой гараж.",
        ],
        [
            "Приятно познакомиться, Саша!",
            "Машинки — это очень весело.",
            "Пожарные машинки самые смелые!",
            "Здорово играть вместе с папой.",
            "Отличный гараж для всех машинок!",
        ],
        [
            "А какая у тебя любимая игра?",
            "Какая машинка самая любимая?",
            "Ты играешь сам или с друзьями?",
            "А во что вы ещё любите играть?",
            "",  # Turn 5 completes naturally
        ],
    ),
    (
        "lyosha_scene",
        {"slide_id": "slide_19", "conversation_mode": "multi_turn", "max_turns": 5, "allow_ai_followup": True},
        [
            "Лёша, почему ты в тёплой куртке?",
            "Я тоже поеду в Исландию!",
            "Я возьму термос с чаем.",
            "С мятой и лимоном.",
            "Да, я очень люблю снежные горы.",
        ],
        [
            "Потому что в Исландии сейчас зима и дует ветер.",
            "Ура! Вместе путешествовать гораздо веселее.",
            "Горячий чай на морозе — отличная идея.",
            "Мята и лимон согревают лучше всего.",
            "Тогда мы обязательно поднимемся на вершину!",
        ],
        [
            "А ты хочешь поехать со мной?",
            "Что вкусного возьмём с собой на прогулку?",
            "С каким вкусом чай ты любишь?",
            "А ты любишь смотреть на снежные горы?",
            "",
        ],
    ),
    (
        "suitcase_conversation",
        {"slide_id": "slide_07", "type": "card_selector", "answer_mode": "optional_voice"},
        [
            "Я положила в чемодан тёплую куртку.",
            "Ещё я взяла пушистую шапку.",
            "Потому что там будет очень холодно.",
            "И любимую книгу со сказками.",
            "Сказку про храброго кота.",
        ],
        [
            "Тёплая куртка обязательно защитит от ветра.",
            "Пушистая шапка согреет ушки!",
            "Ты отлично подготовилась к холоду.",
            "Сказки очень приятно читать перед сном.",
            "Коты — самые храбрые спутники!",
        ],
        [
            "А что защитит голову от мороза?",
            "Почему ты выбрала такие тёплые вещи?",
            "А какую книгу мы почитаем вечером?",
            "Про кого эта сказка?",
            "",
        ],
    ),
    (
        "mood_final",
        {"slide_id": "slide_49", "type": "mood_choice", "answer_mode": "optional_voice"},
        [
            "У меня весёлое настроение!",
            "Потому что мы отлично позанимались.",
            "Мне больше всего понравился Лёша.",
            "Мы говорили про холодную Исландию.",
            "Я уже жду следующий урок!",
        ],
        [
            "Я так рада, что у тебя отличное настроение!",
            "Мы и правда сегодня здорово потрудились.",
            "Лёша передаёт тебе огромный привет!",
            "Исландия оказалась удивительной страной.",
            "До скорой встречи на следующем уроке!",
        ],
        [
            "Расскажи, почему тебе так весело?",
            "Что в нашем уроке тебе понравилось больше всего?",
            "А о чём вы говорили с Лёшей?",
            "Будешь ждать наше следующее путешествие?",
            "",
        ],
    ),
])
async def test_five_turn_conversation_pipeline_e2e(monkeypatch, tmp_path, scenario, slide_meta, transcripts, reactions, questions):
    policy = conversation_policy_for_task(slide_meta)
    assert policy.enabled is True
    assert policy.max_turns == 5

    history = []
    prompts = []
    index = 0
    monkeypatch.setattr(speech_pipeline.settings, "openai_api_key", "test-key")

    async def transcribe(*_args, **_kwargs):
        return transcripts[index], "ru", 0.95

    async def evaluate(prompt):
        prompts.append(prompt)
        return {
            "decision": "CORRECT",
            "semantic_match": 0.96,
            "reaction_target": reactions[index],
            "follow_up_target": questions[index],
            "emotion": "happy",
        }

    monkeypatch.setattr(speech_pipeline, "transcribe_audio", transcribe)
    monkeypatch.setattr(speech_pipeline, "_evaluate_with_chat", evaluate)

    for turn in range(5):
        index = turn
        result = await speech_pipeline.assess_speech(
            tmp_path / f"{scenario}-turn-{turn + 1}.wav", "ru", "ru",
            str(slide_meta.get("task_goal") or "Поговори с ведущим."), [], 1,
            child_name="Даша", child_gender="girl",
            allow_follow_up=True, max_follow_ups=policy.max_turns - 1, follow_up_count=turn,
            conversation_goal=f"5-turn conversation test for {scenario}",
            dialogue_history=history,
            conversation_mode=policy.mode,
            completion_condition=policy.completion_condition,
            trace_id=f"{scenario}-{turn + 1}",
        )
        assert result.transcript == transcripts[turn]
        assert result.response_target == reactions[turn]
        assert result.tutor_turn is not None
        assert result.tutor_turn.reaction_target == reactions[turn]
        assert result.follow_up_target == questions[turn]
        if turn < 4:
            assert result.tutor_turn.complete is False
            assert result.follow_up_target != ""
        else:
            assert result.tutor_turn.complete is True
            assert result.follow_up_target == ""

        history = _append_dialogue_turn(
            history,
            slide_id=slide_meta.get("slide_id", "test_slide"),
            task_id=scenario,
            target_language="ru",
            native_language="ru",
            conversation_turn=turn,
            initial_prompt="Поговори с ведущим.",
            user_text=result.transcript,
            assistant_reaction=result.response_target,
            assistant_follow_up=result.follow_up_target,
        )

    assert len(prompts) == 5
    assert len(history) == 11  # 1 initial prompt + 5 user turns + 5 assistant turns
    assert history[-2]["text"] == transcripts[-1]
    assert history[-1]["text"] == reactions[-1]
