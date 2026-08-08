import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_pdf_lesson_has_41_active_slides_after_removal():
    lesson = json.loads((ROOT / "content/lessons/demo_001/lesson.json").read_text(encoding="utf-8"))
    assert len(lesson["slides"]) == 41
    orders = [slide["order"] for slide in lesson["slides"]]
    assert orders == list(range(1, 32)) + list(range(40, 50))
    for slide in lesson["slides"]:
        assert (ROOT / "content/lessons/demo_001" / slide["image"]).exists()


def test_required_phrases_match_timeline():
    lesson = json.loads((ROOT / "content/lessons/demo_001/lesson.json").read_text(encoding="utf-8"))
    required = {x["phrase_id"] for x in lesson["required_phrases"]}
    timeline = {x["phrase_id"] for x in lesson["timeline"]}
    assert required == timeline
