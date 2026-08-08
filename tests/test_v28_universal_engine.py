from app.engine.activity_registry import REGISTRY, validate_activity_ids
from app.engine.schema import ActivitySpec, CameraPolicy, LessonManifest


def test_registry_contains_future_activity_families():
    required = {
        "speak", "read_aloud", "memory", "drag_drop", "matching", "coloring",
        "handwriting_paper_live", "camera_action", "pose_action", "spatial_orientation",
        "video_pause_question", "describe_ai_guess", "proof_transfer", "branching_story",
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
