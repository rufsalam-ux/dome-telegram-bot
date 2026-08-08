import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_slide9_is_blocking_and_not_skippable():
    lesson = json.loads((ROOT / "content/lessons/demo_001/lesson.json").read_text(encoding="utf-8"))
    slide = next(s for s in lesson["slides"] if s["slide_id"] == "slide_09")
    assert slide["type"] == "card_selector"
    assert slide["allow_skip"] is False
    assert slide["blocking_interaction"] is True
    assert slide["must_wait_for_card_answer"] is True
    assert len(slide["card_options"]) == 6

def test_handlers_block_stale_next_and_wait_for_card():
    code = (ROOT / "app/bot/handlers.py").read_text(encoding="utf-8")
    assert "Сначала выбери одну карточку" in code
    assert "await state.set_state(LessonFlow.waiting_card)" in code
    assert "Я дождусь ответа и только потом задам следующий вопрос" in code
    assert "allow_skip=False" in code
