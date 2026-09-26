import pytest
from app.services import speech_pipeline
from app.services.conversational_tutor import TutorTurn, adaptive_follow_up_policy, build_assessed_turn, conversation_policy_for_task
from app.services.speech_pipeline import SpeechAssessment
from app.webapp.mobile_api import _append_dialogue_turn


async def _async(value):
    return value

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


def test_explicit_dialogue_policy_and_single_answer_are_bounded():
    dialogue = conversation_policy_for_task({
        "conversation_mode": "multi_turn",
        "max_turns": 3,
        "completion_condition": "max_turns_or_natural_close",
    })
    assert dialogue.enabled is True
    assert dialogue.max_turns == 3
    assert dialogue.max_follow_ups == 2

    single = conversation_policy_for_task({
        "conversation_mode": "single_answer",
        "allow_ai_followup": True,
        "max_ai_followups": 7,
    })
    assert single.enabled is False
    assert single.max_turns == 1
    assert single.max_follow_ups == 0


def test_configured_dialogue_continues_after_understood_short_answer():
    allowed, maximum, reason = adaptive_follow_up_policy(
        authored_enabled=True,
        authored_max=2,
        language_level="PRE_A1",
        attempt_number=1,
        transcript="Да",
        confidence=0.61,
        semantic_match=0.7,
        required_dialogue=True,
    )
    assert (allowed, maximum, reason) == (True, 2, "configured_dialogue")


def test_session_history_contains_exact_spoken_question_and_metadata_across_turns():
    history = _append_dialogue_turn(
        [], slide_id="slide_19", task_id="lesha_clothes", target_language="ru", native_language="en",
        conversation_turn=0, initial_prompt="Спроси Лёшу, почему он тепло одет.",
        user_text="Лёша, почему ты так тепло одет?",
        assistant_reaction="Потому что я собирался в Исландию! Там холодно.",
        assistant_follow_up="А что ты надела бы в Исландию?", timestamp="2026-09-25T10:00:00+00:00",
    )
    history = _append_dialogue_turn(
        history, slide_id="slide_19", task_id="lesha_clothes", target_language="ru", native_language="en",
        conversation_turn=1, initial_prompt="unused", user_text="Я надела бы тёплую куртку.",
        assistant_reaction="Отличный выбор — в Исландии бывает холодно.", assistant_follow_up="",
        timestamp="2026-09-25T10:00:05+00:00",
    )
    assert [item["role"] for item in history] == ["assistant", "user", "assistant", "user", "assistant"]
    assert history[2]["text"].endswith("А что ты надела бы в Исландию?")
    assert history[-1]["turn"] == 1
    assert history[-1]["slide_id"] == "slide_19"
    assert history[-1]["task_id"] == "lesha_clothes"
    assert history[-1]["target_language"] == "ru"
    assert [item["order"] for item in history] == list(range(len(history)))


def test_dialogue_stops_at_authored_turn_limit_and_single_answer_never_follows_up():
    capped = build_assessed_turn(
        {"reaction_target": "Я тебя поняла.", "follow_up_target": "Ещё один вопрос?"},
        accepted=True, allow_follow_up=True, follow_up_count=1, max_follow_ups=1,
        answer_text="Тёплую куртку",
    )
    assert capped.follow_up_target == ""
    assert capped.complete is True

    single = build_assessed_turn(
        {"reaction_target": "Я тебя поняла.", "follow_up_target": "Ещё один вопрос?"},
        accepted=True, allow_follow_up=False, follow_up_count=0, max_follow_ups=0,
        answer_text="Тёплую куртку",
    )
    assert single.follow_up_target == ""
    assert single.complete is True


@pytest.mark.asyncio
async def test_real_transcript_task_languages_and_history_reach_llm_and_return_next_question(monkeypatch, tmp_path):
    captured = {}
    history = [{
        "role": "assistant", "text": "Спроси Лёшу, почему он тепло одет.",
        "slide_id": "slide_19", "task_id": "lesha_clothes",
        "target_language": "ru", "native_language": "en", "turn": 0, "order": 0,
    }]
    monkeypatch.setattr(speech_pipeline.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(
        speech_pipeline,
        "transcribe_audio",
        lambda *_args, **_kwargs: _async(("Лёша, почему ты так тепло одет?", "ru", .96)),
    )

    async def evaluate(prompt):
        captured.update(prompt)
        return {
            "decision": "CORRECT", "semantic_match": .98,
            "reaction_target": "Потому что я собирался в Исландию! Там холодно.",
            "reaction_native": "Because I was going to Iceland! It is cold there.",
            "follow_up_target": "А что ты надела бы в Исландию?",
            "follow_up_native": "What would you wear in Iceland?",
            "emotion": "curious",
        }

    monkeypatch.setattr(speech_pipeline, "_evaluate_with_chat", evaluate)
    result = await speech_pipeline.assess_speech(
        tmp_path / "voice.wav", "ru", "en", "Спроси Лёшу, почему он тепло одет.",
        ["ask Lyosha why he is dressed warmly"], 1,
        child_name="Алиса", child_gender="girl",
        allow_follow_up=True, max_follow_ups=1, follow_up_count=0,
        conversation_goal="Answer as Lyosha, then ask one connected question.",
        pedagogical_intent="ask_person_question",
        dialogue_history=history,
        conversation_mode="multi_turn",
        completion_condition="max_turns_or_natural_close",
    )
    assert captured["transcript"] == "Лёша, почему ты так тепло одет?"
    assert captured["goal"] == "Спроси Лёшу, почему он тепло одет."
    assert captured["target_language_code"] == "ru"
    assert captured["native_language_code"] == "en"
    assert captured["dialogue_history"] == history
    assert captured["conversation_mode"] == "multi_turn"
    assert result.response_target.startswith("Потому что")
    assert result.follow_up_target == "А что ты надела бы в Исландию?"
    assert result.tutor_turn and result.tutor_turn.complete is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transcript", "reaction", "correction"),
    [
        ("Я бы взяла купальник, потому что люблю плавать.", "Неожиданный выбор! В Исландии вода очень холодная.", ""),
        ("Я надеть тёплая куртка.", "Я тебя поняла: в Исландии пригодится тёплая куртка.", "Я надела бы тёплую куртку."),
    ],
)
async def test_unexpected_or_imperfect_answer_gets_specific_help_and_dialogue_continues(
    monkeypatch, tmp_path, transcript, reaction, correction,
):
    monkeypatch.setattr(speech_pipeline.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(speech_pipeline, "transcribe_audio", lambda *_args, **_kwargs: _async((transcript, "ru", .91)))
    monkeypatch.setattr(speech_pipeline, "_evaluate_with_chat", lambda prompt: _async({
        "decision": "CORRECT", "semantic_match": .9,
        "reaction_target": reaction,
        "corrected_target": correction,
        "follow_up_target": "А что ещё ты возьмёшь с собой?",
        "emotion": "encouraging",
    }))
    result = await speech_pipeline.assess_speech(
        tmp_path / "voice.wav", "ru", "en", "Что ты надела бы в Исландию?", [], 1,
        allow_follow_up=True, max_follow_ups=2, follow_up_count=0,
        dialogue_history=[{"role": "assistant", "text": "Что ты надела бы в Исландию?"}],
        conversation_mode="multi_turn",
    )
    assert result.transcript == transcript
    assert result.response_target == reaction
    assert result.follow_up_target == "А что ещё ты возьмёшь с собой?"
    assert result.tutor_turn and result.tutor_turn.complete is False
    if correction:
        assert result.corrected_target == correction


@pytest.mark.asyncio
async def test_unclear_asr_never_completes_or_calls_llm(monkeypatch, tmp_path):
    monkeypatch.setattr(speech_pipeline.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(speech_pipeline, "transcribe_audio", lambda *_args, **_kwargs: _async(("", "ru", .12)))

    async def forbidden(_prompt):
        raise AssertionError("LLM must not receive an unintelligible transcript")

    monkeypatch.setattr(speech_pipeline, "_evaluate_with_chat", forbidden)
    result = await speech_pipeline.assess_speech(
        tmp_path / "voice.wav", "ru", "en", "Ответь Лёше.", [], 1,
        allow_follow_up=True, max_follow_ups=1, conversation_mode="multi_turn",
    )
    assert result.status == "TECHNICAL_UNCERTAINTY"
    assert result.tutor_turn is None


@pytest.mark.asyncio
async def test_five_turn_dialogue_keeps_history_and_generates_each_ai_turn(monkeypatch, tmp_path):
    transcripts = [
        "Лёша, почему ты так тепло одет?",
        "Я бы надела тёплую куртку.",
        "Ещё я взяла бы шапку.",
        "Мне нравится путешествовать зимой.",
        "Больше всего я люблю снег.",
    ]
    reactions = [
        "Я собирался в холодную Исландию.",
        "Тёплая куртка отлично подойдёт.",
        "Шапка тоже пригодится.",
        "Зимние путешествия бывают волшебными.",
        "Снег и правда очень красивый.",
    ]
    questions = [
        "А что ты надела бы в Исландию?",
        "Что ещё ты взяла бы с собой?",
        "Ты любишь путешествовать зимой?",
        "Что тебе больше всего нравится зимой?",
        "",
    ]
    history = []
    prompts = []
    index = 0
    monkeypatch.setattr(speech_pipeline.settings, "openai_api_key", "test-key")

    async def transcribe(*_args, **_kwargs):
        return transcripts[index], "ru", .97

    async def evaluate(prompt):
        prompts.append(prompt)
        return {
            "decision": "CORRECT",
            "semantic_match": .98,
            "reaction_target": reactions[index],
            "follow_up_target": questions[index],
            "emotion": "curious",
        }

    monkeypatch.setattr(speech_pipeline, "transcribe_audio", transcribe)
    monkeypatch.setattr(speech_pipeline, "_evaluate_with_chat", evaluate)

    for turn in range(5):
        index = turn
        result = await speech_pipeline.assess_speech(
            tmp_path / f"turn-{turn + 1}.wav", "ru", "en",
            "Поговори с Лёшей о путешествии.", [], 1,
            child_name="Алиса", child_gender="girl",
            allow_follow_up=True, max_follow_ups=4, follow_up_count=turn,
            conversation_goal="Keep one connected child-safe conversation.",
            dialogue_history=history,
            conversation_mode="multi_turn",
            completion_condition="max_turns_or_natural_close",
            trace_id=f"turn-{turn + 1}",
        )
        assert result.transcript == transcripts[turn]
        assert result.response_target == reactions[turn]
        assert result.tutor_turn is not None
        assert result.follow_up_target == questions[turn]
        assert prompts[turn]["dialogue_history"] == history[-12:]
        history = _append_dialogue_turn(
            history,
            slide_id="slide_19",
            task_id="lesha_clothes",
            target_language="ru",
            native_language="en",
            conversation_turn=turn,
            initial_prompt="Спроси Лёшу, почему он тепло одет.",
            user_text=result.transcript,
            assistant_reaction=result.response_target,
            assistant_follow_up=result.follow_up_target,
        )

    assert len(prompts) == 5
    assert prompts[-1]["dialogue_policy"]["follow_up_count"] == 4
    assert prompts[-1]["dialogue_policy"]["remaining_follow_ups"] == 0
    assert history[-2]["text"] == transcripts[-1]
    assert history[-1]["text"] == reactions[-1]
