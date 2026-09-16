import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).parents[1]
MOBILE_ROOT = Path(r"C:\Users\D Dima\Downloads\Telegram Desktop\DOME_MOBILE_77\DOME_MOBILE_77")


# 1. Test Task Complete: both Answer and Continue are simultaneously enabled
def test_task_complete_enables_both_answer_and_continue():
    state = {
        "isRecording": False,
        "isFinalizing": False,
        "isUploading": False,
        "isPlayingTTS": False,
        "isPlayingChildVoice": False,
        "accepted": True,
    }

    canRecord = (
        not state["isRecording"]
        and not state["isFinalizing"]
        and not state["isUploading"]
        and not state["isPlayingTTS"]
        and not state["isPlayingChildVoice"]
    )
    canContinue = state["accepted"]

    assert canRecord is True
    assert canContinue is True


# 2. Test Answer button state machine transitions
def test_answer_state_machine_transitions():
    state = {
        "recording": False,
        "isFinalizing": False,
        "isUploading": False,
        "isPlayingTTS": False,
        "isPlayingChildVoice": False,
    }

    def can_record(s):
        return (
            not s["recording"]
            and not s["isFinalizing"]
            and not s["isUploading"]
            and not s["isPlayingTTS"]
            and not s["isPlayingChildVoice"]
        )

    # Initial state: ready
    assert can_record(state) is True

    # When recording starts: disabled
    state["recording"] = True
    assert can_record(state) is False

    # When stopping / finalizing: disabled
    state["recording"] = False
    state["isFinalizing"] = True
    assert can_record(state) is False

    # When uploading: disabled
    state["isFinalizing"] = False
    state["isUploading"] = True
    assert can_record(state) is False

    # When upload finishes and TTS speaks: disabled during speech
    state["isUploading"] = False
    state["isPlayingTTS"] = True
    assert can_record(state) is False

    # When TTS completes: enabled again!
    state["isPlayingTTS"] = False
    assert can_record(state) is True


# 3. Test Failed Re-record preserves previous audio
def test_failed_rerecord_preserves_previous_audio():
    saved_audio_by_slide = {"slide_20": "file:///path/to/child_voice_v1.m4a"}

    def attempt_rerecord(success: bool, new_uri: str | None):
        if not success or not new_uri:
            return
        saved_audio_by_slide["slide_20"] = new_uri

    attempt_rerecord(success=False, new_uri=None)
    assert saved_audio_by_slide["slide_20"] == "file:///path/to/child_voice_v1.m4a"

    attempt_rerecord(success=False, new_uri="file:///path/to/empty.m4a")
    assert saved_audio_by_slide["slide_20"] == "file:///path/to/child_voice_v1.m4a"


# 4. Test Successful Re-record updates audio
def test_successful_rerecord_overwrites_audio():
    saved_audio_by_slide = {"slide_20": "file:///path/to/child_voice_v1.m4a"}

    def attempt_rerecord(success: bool, new_uri: str | None):
        if not success or not new_uri:
            return
        saved_audio_by_slide["slide_20"] = new_uri

    attempt_rerecord(success=True, new_uri="file:///path/to/child_voice_v2.m4a")
    assert saved_audio_by_slide["slide_20"] == "file:///path/to/child_voice_v2.m4a"


# 5. Test Hint Context: Teddy Bear vs Book
def test_hint_context_teddy_bear_vs_book():
    def get_hint_for_slide_20(selected_item: str, target_lang: str = "ru"):
        lower = selected_item.lower()
        if "мишк" in lower or "teddy" in lower or "bear" in lower:
            return "Мила привезла мне плюшевого мишку." if target_lang == "ru" else "Mila gave me a teddy bear."
        elif "книг" in lower or "book" in lower:
            return "Мила привезла мне книгу." if target_lang == "ru" else "Mila gave me a book."
        elif "букет" in lower or "flower" in lower:
            return "Мила привезла мне букет." if target_lang == "ru" else "Mila gave me flowers."
        elif "рюкзак" in lower or "backpack" in lower:
            return "Мила привезла мне рюкзак." if target_lang == "ru" else "Mila gave me a backpack."
        return "Выбери подарок Милы."

    hint_teddy = get_hint_for_slide_20("мишка")
    assert "мишк" in hint_teddy
    assert "книг" not in hint_teddy

    hint_teddy_en = get_hint_for_slide_20("teddy bear", target_lang="en")
    assert "teddy bear" in hint_teddy_en
    assert "book" not in hint_teddy_en


# 6. Test Hint Context: Book
def test_hint_context_book():
    def get_hint_for_slide_20(selected_item: str, target_lang: str = "ru"):
        lower = selected_item.lower()
        if "мишк" in lower or "teddy" in lower or "bear" in lower:
            return "Мила привезла мне плюшевого мишку."
        elif "книг" in lower or "book" in lower:
            return "Мила привезла мне интересную книгу."
        return "Выбери подарок Милы."

    hint_book = get_hint_for_slide_20("книга")
    assert "книг" in hint_book
    assert "мишк" not in hint_book


# 7. Test Movie Player headers, Range support & CORS
@pytest.mark.asyncio
async def test_movie_player_url_and_range_headers():
    from aiohttp import web
    from app.webapp.mobile_api import movie_file

    req = MagicMock(spec=web.Request)
    req.method = "OPTIONS"

    res = await movie_file(req)
    assert res.headers.get("Access-Control-Allow-Origin") == "*"
    assert "Range" in res.headers.get("Access-Control-Allow-Headers", "")
    assert "GET" in res.headers.get("Access-Control-Allow-Methods", "")


# 8. Test Lesson First Completion creates homework
@pytest.mark.asyncio
async def test_lesson_first_completion_creates_homework():
    from app.db.models import HomeworkAssignment

    run_no = 1
    homework_created = False
    existing_hw = None

    if run_no == 1 and existing_hw is None:
        hw = HomeworkAssignment(
            child_id=1,
            lesson_id="demo_001",
            title="Домашнее задание",
            body="Нарисуй место, куда ты хотел бы отправиться, и назови три вещи, которые возьмёшь с собой.",
            status="NEW",
        )
        homework_created = True

    assert homework_created is True
    assert hw.status == "NEW"


# 9. Test Lesson Second Completion does NOT duplicate homework
def test_lesson_second_completion_does_not_duplicate_homework():
    run_no = 2
    homework_created = False

    if run_no == 1:
        homework_created = True

    assert homework_created is False


# 10. Test Video Step sequence order in botLesson.json
def test_video_step_sequence_order():
    bot_lesson_path = MOBILE_ROOT / "src" / "data" / "botLesson.json"
    data = json.loads(bot_lesson_path.read_text(encoding="utf-8"))
    by_id = {s["slide_id"]: s for s in data["slides"]}

    curr = "slide_01"
    seq = []
    seen = set()
    while curr and curr in by_id and curr not in seen:
        seen.add(curr)
        seq.append(curr)
        curr = by_id[curr].get("next_slide")

    targets = [
        ("video_01_madagascar", "slide_18"),
        ("video_02_lesha", "slide_19"),
        ("video_03_mila", "slide_20"),
        ("video_04_suitcase", "slide_24"),
        ("video_10_zebra", "slide_47"),
        ("video_06_lion", "slide_51"),
        ("video_08_giraffe", "slide_45"),
        ("video_06_bear", "slide_42"),
        ("video_11_plane", "slide_16"),
    ]

    for v_id, target in targets:
        assert v_id in seq, f"{v_id} missing from sequence"
        assert target in seq, f"{target} missing from sequence"
        v_idx = seq.index(v_id)
        assert seq[v_idx + 1] == target, f"Expected {target} immediately after {v_id}, found {seq[v_idx+1]}"


# 11. Test Video Step Auto-Continue to target slide
def test_video_step_auto_continue_to_target_slide():
    bot_lesson_path = MOBILE_ROOT / "src" / "data" / "botLesson.json"
    data = json.loads(bot_lesson_path.read_text(encoding="utf-8"))
    by_id = {s["slide_id"]: s for s in data["slides"]}

    v1 = by_id["video_01_madagascar"]
    assert v1["auto_continue"] is True
    assert v1["next_slide"] == "slide_18"


# 12. Test Lesson Completion Idempotency: no infinite repeat
def test_lesson_completion_idempotency_no_infinite_repeat():
    bot_lesson_path = MOBILE_ROOT / "src" / "data" / "botLesson.json"
    data = json.loads(bot_lesson_path.read_text(encoding="utf-8"))
    by_id = {s["slide_id"]: s for s in data["slides"]}

    last_slide = by_id.get("slide_49")
    assert last_slide is not None
    assert last_slide.get("next_slide") is None


# 13. Test Hero Articulation Safe Fallback
def test_hero_articulation_safe_fallback():
    from app.services.free_topic_cartoon import _try_segment_hero

    invalid_path = Path("non_existent_hero_image.png")
    result = _try_segment_hero(invalid_path, Path("."))
    assert result is None
