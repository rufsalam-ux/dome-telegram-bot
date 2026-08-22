from datetime import date
from app.services import course_scheduler


def test_seasonal_alternates_when_more_than_one_lesson_per_week(monkeypatch):
    course = {
        "lesson_ids": ["l1", "winter1", "l2", "winter2", "l3"],
        "lessons_per_week": 2,
        "seasonal": {"periods": [{
            "id": "winter", "start": "12-01", "end": "02-28",
            "enabled": True, "lesson_ids": ["winter1", "winter2"]
        }]}
    }
    monkeypatch.setattr(course_scheduler, "_load_course", lambda _: course)
    day = date(2026, 12, 10)
    assert course_scheduler.choose_next_lesson("c", [], day) == "winter1"
    assert course_scheduler.choose_next_lesson("c", ["winter1"], day) == "l1"
    assert course_scheduler.choose_next_lesson("c", ["winter1", "l1"], day) == "winter2"
    assert course_scheduler.choose_next_lesson("c", ["winter1", "l1", "winter2"], day) == "l2"


def test_one_lesson_per_week_keeps_seasonal_priority(monkeypatch):
    course = {
        "lesson_ids": ["l1", "winter1", "l2", "winter2"],
        "lessons_per_week": 1,
        "seasonal": {"periods": [{
            "id": "winter", "start": "12-01", "end": "02-28",
            "enabled": True, "lesson_ids": ["winter1", "winter2"]
        }]}
    }
    monkeypatch.setattr(course_scheduler, "_load_course", lambda _: course)
    day = date(2026, 12, 10)
    assert course_scheduler.choose_next_lesson("c", [], day) == "winter1"
    assert course_scheduler.choose_next_lesson("c", ["winter1"], day) == "winter2"


def test_payment_gate_is_present_in_runtime_files():
    from pathlib import Path
    root = Path(__file__).parents[1]
    handlers = (root / "app/bot/handlers.py").read_text("utf-8")
    payments = (root / "config/payments.json").read_text("utf-8")
    assert "_show_parent_course_payment_gate" in handlers
    assert "course_payment:test_bypass" in handlers
    assert '"require_course_payment_before_lesson": true' in payments.lower()
