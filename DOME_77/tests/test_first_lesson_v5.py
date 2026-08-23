import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_pdf_lesson_has_current_authored_slide_count():
    lesson = json.loads((ROOT / "content/lessons/demo_001/lesson.json").read_text(encoding="utf-8"))
    assert len(lesson["slides"]) == 34
    orders = [slide["order"] for slide in lesson["slides"]]
    assert orders == [
        1, 3, 9, 10, 11, 12, 13, 14, 15, 4, 6, 7, 8, 17, 18, 19, 20,
        21, 22, 23, 24, 40, 41, 47, 50, 46, 43, 51, 45, 42, 44, 48, 16, 49,
    ]
    assert len(set(orders)) == len(orders)
    for slide in lesson["slides"]:
        assert (ROOT / "content/lessons/demo_001" / slide["image"]).exists()


def test_required_phrases_match_timeline():
    lesson = json.loads((ROOT / "content/lessons/demo_001/lesson.json").read_text(encoding="utf-8"))
    required = {x["phrase_id"] for x in lesson["required_phrases"]}
    timeline = {x["phrase_id"] for x in lesson["timeline"]}
    assert required == timeline
