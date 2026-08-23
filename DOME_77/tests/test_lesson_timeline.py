from app.services.lesson_loader import load_lesson


def test_first_lesson_has_ten_phrases_and_timeline_slots():
    lesson = load_lesson("demo_001")
    assert len(lesson["required_phrases"]) == 10
    assert len(lesson["timeline"]) == 10
    assert all((slot["end"] - slot["talk_start"]) <= 7.1 for slot in lesson["timeline"])
