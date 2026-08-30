from app.services.lesson_loader import load_lesson, validate_lesson_revision
def test_hard_removal_and_route():
 lesson=load_lesson("demo_001"); orders=[int(s["order"]) for s in lesson["slides"]]; ids=[s["slide_id"] for s in lesson["slides"]]
 assert 2 not in orders and not (set(range(25,40)) & set(orders))
 assert ids[ids.index("slide_24")+1]=="slide_40"
 assert validate_lesson_revision("demo_001")==orders
def test_suitcase_action_is_mandatory_but_followup_voice_is_optional():
 by={s["slide_id"]:s for s in load_lesson("demo_001")["slides"]}
 assert by["slide_24"]["requires_interactive_completion"] is True
 assert by["slide_24"]["allow_skip"] is True
 assert by["slide_24"]["voice_after_action_optional"] is True
 assert by["slide_24"]["requiredForMovie"] is False
 assert by["slide_24"]["required_phrase_id"]=="take_trip"
