from app.services.lesson_loader import load_lesson, validate_lesson_revision

def test_v17_hard_removal_and_routes():
    lesson=load_lesson('demo_001')
    orders=[int(s['order']) for s in lesson['slides']]
    ids=[s['slide_id'] for s in lesson['slides']]
    assert 2 not in orders
    assert not (set(range(32,40)) & set(orders))
    assert 'slide_02' not in ids
    assert ids[ids.index('slide_31')+1]=='slide_40'
    assert validate_lesson_revision('demo_001')==orders

def test_v17_skip_policy():
    lesson=load_lesson('demo_001')
    by={s['slide_id']:s for s in lesson['slides']}
    assert by['slide_19']['allow_skip'] is True
    assert by['slide_19']['required_phrase_id'] is None
    assert by['slide_19']['post_required_phrase_id']=='lesha_clothes'
    assert by['slide_24']['allow_skip'] is False
    assert by['slide_24']['required_phrase_id']=='take_trip'
