import json
from pathlib import Path

from app.services.authored_content import augment_course, discover_course_lessons, validate_content_lesson
from app.services.pilot_access import find_pilot
from app.services.course_scheduler import choose_next_lesson


def test_three_pilot_courses_exist():
    root=Path('content/courses')
    expected={'conversation':True,'learn_to_read':False,'reading':False}
    for name, active in expected.items():
        data=json.loads((root/f'{name}.json').read_text('utf-8'))
        assert data['course_id']==name
        assert data['active'] is active


def test_conversation_starts_with_existing_production_lesson():
    course=json.loads(Path('content/courses/conversation.json').read_text('utf-8'))
    augmented=augment_course(course)
    assert augmented['lesson_ids'][0]=='demo_001'
    assert choose_next_lesson('conversation',[])=='demo_001'


def test_content_template_is_valid_but_inactive():
    data=json.loads(Path('content/templates/pilot_lesson_template/lesson.json').read_text('utf-8'))
    assert data['engine']=='content_v1'
    assert data['active'] is False
    assert validate_content_lesson(data)==[]


def test_pilot_template_disabled_by_default():
    assert find_pilot('DEMO30') is None


def test_menu_is_child_simple():
    text=Path('app/bot/keyboards.py').read_text('utf-8')
    block=text[text.index('def child_menu_keyboard'):text.index('def parent_menu_keyboard')]
    assert '▶ Продолжить' in block
    assert '📚 Мои курсы' in block
    assert '⭐ Мои успехи' in block
    assert 'menu:character' not in block
