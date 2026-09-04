from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator
from app.engine.activity_registry import REGISTRY

class FeedbackPolicy(BaseModel):
    mode: Literal["silent", "gentle", "normal", "strict"] = "gentle"
    max_corrections_per_block: int = 1
    ignore_minor_errors: bool = True
    low_confidence_is_not_error: bool = True
    encouragement_after_correction: bool = True

class CameraPolicy(BaseModel):
    enabled: bool = False
    coordinate_frame: Literal["child", "screen", "page", "object"] = "child"
    auto_detect_mirror: bool = True
    auto_detect_rotation: bool = True
    require_calibration_if_uncertain: bool = True
    confidence_threshold: float = 0.72
    low_confidence_action: Literal["retry", "skip", "ask_reposition"] = "ask_reposition"

class ActivitySpec(BaseModel):
    id: str
    type: str
    instruction: str = ""
    prompt: str = ""
    required: bool = False
    allow_skip: bool = True
    max_attempts: int = 3
    target_language_required: bool = False
    waits_for_answer: bool = True
    cartoon_phrase_id: str | None = None
    feedback: FeedbackPolicy = Field(default_factory=FeedbackPolicy)
    camera: CameraPolicy = Field(default_factory=CameraPolicy)
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_type(self):
        if self.type not in REGISTRY:
            raise ValueError(f"Unknown activity type: {self.type}")
        if self.required:
            self.allow_skip = False
        return self

class LessonManifest(BaseModel):
    schema_version: str = "2.1"
    lesson_id: str
    course_id: str
    title: str
    target_language: str = "en"
    native_language_mode: str = "child_profile"
    activities: list[ActivitySpec] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

class CourseManifest(BaseModel):
    schema_version: str = "1.0"
    course_id: str
    title: str
    description: str = ""
    cover_image: str = ""
    order: int = 1
    active: bool = True
    locked: bool = False
    status: str = "published"
    price: float | None = None
    currency: str = "EUR"
    lesson_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

