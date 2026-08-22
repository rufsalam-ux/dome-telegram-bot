from app.engine.runtime import migrate_manifest_dict, validate_manifest_dict, decide
from app.engine.schema import ActivitySpec


def test_required_activity_disables_skip():
    a = ActivitySpec(id='a1', type='speak', required=True, allow_skip=True)
    assert a.allow_skip is False


def test_runtime_knows_implemented_and_future_types():
    assert decide(ActivitySpec(id='a1', type='speak')).supported is True
    assert decide(ActivitySpec(id='a2', type='pose_action')).supported is False


def test_manifest_migration_adds_defaults():
    d = migrate_manifest_dict({'lesson_id':'l1','course_id':'c1','title':'T','activities':[{'type':'memory'}]})
    assert d['schema_version'] == '2.1'
    assert d['activities'][0]['id'] == 'activity_001'
    assert d['activities'][0]['max_attempts'] == 3


def test_camera_child_frame_requires_mirror_detection():
    data = {
        'lesson_id':'l1','course_id':'c1','title':'T',
        'activities':[{'id':'a1','type':'camera_action','camera':{'enabled':True,'coordinate_frame':'child','auto_detect_mirror':False}}]
    }
    errors = validate_manifest_dict(data)
    assert any('auto-detect mirror' in e for e in errors)
