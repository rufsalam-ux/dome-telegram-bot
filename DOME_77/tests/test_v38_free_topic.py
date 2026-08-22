from app.services.free_topic_builder import _fallback

def test_fallback_has_real_structure_and_required_cartoon_lines():
    lesson=_fallback('Minecraft','en',12,'A1',21)
    slides=lesson['slides']
    assert len(slides)==21
    assert sum(bool(s.get('required_cartoon_line')) for s in slides)==5
    assert all(not s.get('can_skip') for s in slides if s.get('required_cartoon_line'))
    kinds={s['type'] for s in slides}
    assert {'choice','drag_drop','memory','video','drawing','voice_answer'} <= kinds
    assert all(s.get('audio_text') for s in slides)
    assert all(s.get('image_prompt') for s in slides)
