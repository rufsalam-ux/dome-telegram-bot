from pathlib import Path
import json

from app.services.lesson_importer import instruction_map, parse_extra_links
from app.services.authored_content import validate_content_lesson
from app.engine.activity_registry import REGISTRY


def test_instruction_parser_accepts_common_variants():
    text='''3 слайд.\nСмотрим мультфильм.\nСлайд 6: перетащи буквы.\n7. Читаем слово.\n'''
    m=instruction_map(text)
    assert 3 in m and 'мультфильм' in m[3]
    assert 6 in m and 'перетащи' in m[6]
    assert 7 in m and 'Читаем' in m[7]


def test_extra_links_are_attached_by_slide():
    m=parse_extra_links(['слайд 3 https://youtu.be/abc1234','11: https://youtube.com/watch?v=xyz9876'])
    assert m[3][0].startswith('https://')
    assert m[11][0].startswith('https://')


def test_critical_activity_types_are_implemented():
    for key in ['video_pause_question','interactive_scene','real_world_find','photo_task']:
        assert REGISTRY[key].implemented_now is True


def test_strict_validation_blocks_broken_interactive():
    d={'lesson_id':'x','course_id':'reading','title':'x','order':1,'slides':[{'type':'drag_drop','items':['a']} ]}
    assert any('drag_drop needs items and targets' in e for e in validate_content_lesson(d))


def test_v69_admin_workflow_present():
    h=Path('app/bot/handlers.py').read_text('utf-8')
    for command in ['previewlesson','validate_lesson','lessonversions','lessonrestore','done_extras','dome_testplan']:
        assert command in h
    assert 'release_due_lessons' in h


def test_no_implicit_data_mount_for_dome():
    c=Path('app/core/config.py').read_text('utf-8')
    assert 'Path("/data").exists()' not in c


def test_all_bundled_lessons_share_access_rules():
    for p in Path('content/lessons').glob('*/lesson.json'):
        d=json.loads(p.read_text('utf-8'))
        assert d['max_completed_runs']==2
        assert d['expires_after_months']==10
