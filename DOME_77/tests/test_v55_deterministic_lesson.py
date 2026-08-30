from pathlib import Path
import json

def test_animal_pairs_are_deterministic():
    from app.services.animal_compare import SAFE_TASKS
    assert SAFE_TASKS["penguin_parrot"] == [("Кто из них умеет летать?", "parrot"), ("Кто из них живёт там, где очень холодно?", "penguin")]
    assert SAFE_TASKS["lion_turtle"] == [("Кто из них быстрее бегает?", "lion"), ("У кого есть панцирь?", "turtle")]

def test_lesson_bounds_authored_ai_followups_and_has_current_animal_phrases():
    j=json.loads(Path("content/lessons/demo_001/lesson.json").read_text(encoding="utf-8"))
    followup_slides=[s for s in j["slides"] if s.get("allow_ai_followup")]
    assert [s["slide_id"] for s in followup_slides] == ["slide_01", "slide_24", "slide_44"]
    for slide in followup_slides:
        assert 1 <= slide["max_ai_followups"] <= 3
        assert slide["conversation_goal"]
    parrot=next(s for s in followup_slides if s["slide_id"]=="slide_44")
    assert parrot["required_phrase_id"] == "parrot"
    assert parrot["suppress_ai_followup"] is False
    suitcase=next(s for s in followup_slides if s["slide_id"]=="slide_24")
    assert suitcase["selection_policy"] == "child_choice"
    assert suitcase["follow_up_policy"] == "optional"
    assert suitcase["max_ai_followups"] == 1
    ids={x["phrase_id"] for x in j["required_phrases"]}
    assert {"penguin","parrot","lion","giraffe","zebra"} <= ids
    for s in j["slides"]:
        if s.get("interactive_task")=="animal_compare":
            assert s.get("required_phrase_source")=="selected_animal"
            assert s.get("max_attempts")==3
            assert s.get("allow_skip") is False

def test_penguin_asset_is_single_file_present():
    p=Path("app/webapp/static/assets/animals/penguin.png")
    assert p.exists() and p.stat().st_size > 1000

def test_cartoon_overlay_config_exists():
    p=Path("content/lessons/demo_001/cartoon_text_overlays.json")
    j=json.loads(p.read_text(encoding="utf-8"))
    assert j["enabled"] is True
    assert isinstance(j["overlays"], list)
