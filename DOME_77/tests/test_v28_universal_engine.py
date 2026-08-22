from app.engine.activity_registry import REGISTRY, validate_activity_ids
from app.engine.schema import ActivitySpec, CameraPolicy, LessonManifest


def test_registry_contains_future_activity_families():
    required = {
        "speak", "read_aloud", "read_roles", "memory", "drag_drop", "matching", "coloring",
        "trace", "tap_sound", "connect_lines", "sentence_builder", "dictation",
        "video_pause_question", "real_world_find", "photo_task", "physical_action",
    }
    assert required <= set(REGISTRY)


def test_required_activity_cannot_be_skipped():
    a = ActivitySpec(id="x", type="speak", required=True, allow_skip=True)
    assert a.allow_skip is False


def test_camera_policy_defaults_child_relative_and_mirror_safe():
    c = CameraPolicy(enabled=True)
    assert c.coordinate_frame == "child"
    assert c.auto_detect_mirror is True
    assert c.low_confidence_action == "ask_reposition"


def test_lesson_manifest_validates_registry_type():
    lesson = LessonManifest(
        lesson_id="l1", course_id="c1", title="Test",
        activities=[{"id":"a1","type":"memory"}],
    )
    assert lesson.activities[0].type == "memory"
    assert validate_activity_ids(["memory", "no_such_type"]) == ["no_such_type"]
