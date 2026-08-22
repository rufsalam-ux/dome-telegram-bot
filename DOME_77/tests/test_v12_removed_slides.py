import json
from pathlib import Path
from app.services.lesson_revision import normalize_lesson_step, CURRENT_REVISION
ROOT=Path(__file__).parents[1]
def load(): return json.loads((ROOT/"content/lessons/demo_001/lesson.json").read_text(encoding="utf-8"))
def test_removed_source_slides_absent():
 d=load(); orders=[s["order"] for s in d["slides"]]
 assert 2 not in orders
 assert all(n not in orders for n in range(25,40))
 assert len(d["slides"])==34
def test_current_revision_resume_is_exact():
 for i in (0,1,10,22,33): assert normalize_lesson_step(i,CURRENT_REVISION)==i
