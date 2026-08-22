import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LESSON = ROOT / "content" / "lessons" / "demo_001" / "lesson.json"


def test_cartoon_base_is_content_configured_and_exists():
    lesson = json.loads(LESSON.read_text(encoding="utf-8"))
    assert lesson.get("cartoon_base_replaceable") is True
    base = LESSON.parent / lesson["cartoon_base"]
    assert base.exists()
    assert base.suffix.lower() == ".mp4"
    assert base.stat().st_size > 1_000_000


def test_timeline_is_separate_editable_file():
    timeline = LESSON.parent / "timeline.json"
    assert timeline.exists()
    data = json.loads(timeline.read_text(encoding="utf-8"))
    assert isinstance(data, list) and data
    assert "visible_start" in data[0]
    assert "character_animation" in data[0]
