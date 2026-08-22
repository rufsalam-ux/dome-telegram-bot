from datetime import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, Float, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Parent(Base):
    __tablename__ = "parents"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True, nullable=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    email_verification_code_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_verification_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    email_reports_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    active_child_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    children: Mapped[list["Child"]] = relationship(back_populates="parent")


class Child(Base):
    __tablename__ = "children"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("parents.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    age_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    birth_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    birth_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    birth_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(16), nullable=True)
    birthday_greeted_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    native_language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    target_language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    can_read_target: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    country: Mapped[str | None] = mapped_column(String(120), nullable=True)
    language_level: Mapped[str] = mapped_column(String(20), default="PRE_A1")
    working_difficulty: Mapped[float] = mapped_column(Float, default=0.15)
    comprehension_score: Mapped[float] = mapped_column(Float, default=0.0)
    grammar_score: Mapped[float] = mapped_column(Float, default=0.0)
    vocabulary_score: Mapped[float] = mapped_column(Float, default=0.0)
    pronunciation_score: Mapped[float] = mapped_column(Float, default=0.0)
    fluency_score: Mapped[float] = mapped_column(Float, default=0.0)
    independence_score: Mapped[float] = mapped_column(Float, default=0.0)
    answers_count: Mapped[int] = mapped_column(Integer, default=0)
    active_character_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    parent: Mapped[Parent] = relationship(back_populates="children")


class Character(Base):
    __tablename__ = "characters"
    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id"), index=True)
    original_path: Mapped[str] = mapped_column(Text)
    processed_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="UPLOADED")
    source: Mapped[str] = mapped_column(String(40), default="CHILD_DRAWING")
    catalog_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LessonSession(Base):
    __tablename__ = "lesson_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id"), index=True)
    lesson_id: Mapped[str] = mapped_column(String(80))
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(40), default="IN_PROGRESS")
    level_at_start: Mapped[str] = mapped_column(String(20), default="PRE_A1")
    level_at_end: Mapped[str | None] = mapped_column(String(20), nullable=True)
    lesson_revision: Mapped[int] = mapped_column(Integer, default=21)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    runtime_state_json: Mapped[str] = mapped_column(Text, default="{}")


class VoiceAttempt(Base):
    __tablename__ = "voice_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_session_id: Mapped[int] = mapped_column(ForeignKey("lesson_sessions.id"), index=True)
    phrase_id: Mapped[str] = mapped_column(String(100))
    attempt_number: Mapped[int] = mapped_column(Integer)
    audio_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="RECEIVED")
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_language: Mapped[str | None] = mapped_column(String(30), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    grammar_errors: Mapped[str | None] = mapped_column(Text, nullable=True)
    pronunciation_errors: Mapped[str | None] = mapped_column(Text, nullable=True)
    semantic_match: Mapped[float | None] = mapped_column(Float, nullable=True)
    comprehension_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    grammar_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    vocabulary_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    pronunciation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    fluency_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    independence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    recommended_difficulty: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class InteractiveResult(Base):
    __tablename__ = "interactive_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_session_id: Mapped[int] = mapped_column(ForeignKey("lesson_sessions.id"), index=True)
    slide_id: Mapped[str] = mapped_column(String(80))
    task_type: Mapped[str] = mapped_column(String(40))
    result_json: Mapped[str] = mapped_column(Text)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ConsentRecord(Base):
    __tablename__ = "consent_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("parents.id"), index=True)
    child_id: Mapped[int | None] = mapped_column(ForeignKey("children.id"), nullable=True, index=True)
    consent_type: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[str] = mapped_column(String(80))
    phone: Mapped[str] = mapped_column(String(40))
    verified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger)
    details_json: Mapped[str] = mapped_column(Text, default="{}")


class CourseEnrollment(Base):
    __tablename__ = "course_enrollments"
    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id"), index=True)
    course_id: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE")
    access_source: Mapped[str] = mapped_column(String(40), default="MANUAL")
    payment_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    access_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SkillMastery(Base):
    __tablename__ = "skill_mastery"
    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id"), index=True)
    skill_id: Mapped[str] = mapped_column(String(120), index=True)
    stage: Mapped[str] = mapped_column(String(30), default="NEW")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ActivityAttempt(Base):
    __tablename__ = "activity_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_session_id: Mapped[int] = mapped_column(ForeignKey("lesson_sessions.id"), index=True)
    activity_id: Mapped[str] = mapped_column(String(120), index=True)
    activity_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(30), default="STARTED")
    input_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    correction_given: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class HomeworkAssignment(Base):
    __tablename__ = "homework_assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id"), index=True)
    lesson_session_id: Mapped[int | None] = mapped_column(ForeignKey("lesson_sessions.id"), nullable=True, index=True)
    lesson_id: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(255), default="Р”РѕРјР°С€РЅРµРµ Р·Р°РґР°РЅРёРµ")
    body: Mapped[str] = mapped_column(Text)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=5)
    status: Mapped[str] = mapped_column(String(30), default="NEW")
    optional: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    current_step: Mapped[int] = mapped_column(Integer, default=0)


class PaymentApproval(Base):
    __tablename__ = "payment_approvals"
    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("parents.id"), index=True)
    child_id: Mapped[int | None] = mapped_column(ForeignKey("children.id"), nullable=True, index=True)
    package_code: Mapped[str] = mapped_column(String(120), index=True)
    amount_minor: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    status: Mapped[str] = mapped_column(String(30), default="PENDING_SMS")
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    provider_customer_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_payment_method_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    charged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id"), index=True)
    course_id: Mapped[str] = mapped_column(String(100), index=True)
    plan_id: Mapped[str] = mapped_column(String(40), default="weekly1")
    lessons_per_week: Mapped[int] = mapped_column(Integer, default=1)
    monthly_price: Mapped[float] = mapped_column(Float, default=39.0)
    currency: Mapped[str] = mapped_column(String(10), default="EUR")
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE")
    test_mode: Mapped[bool] = mapped_column(Boolean, default=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    payment_provider: Mapped[str] = mapped_column(String(30), default="manual", index=True)
    provider_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # Number of SUBSCRIPTION entitlements that already existed when this
    # release schedule segment started. Prevents plan changes/re-subscribe from
    # retroactively granting or withholding weeks from an older segment.
    release_baseline_count: Mapped[int] = mapped_column(Integer, default=0)


class LessonEntitlement(Base):
    __tablename__ = "lesson_entitlements"
    __table_args__ = (UniqueConstraint("child_id", "lesson_id", "course_id", name="uq_lesson_entitlement_child_lesson_course"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    child_id: Mapped[int] = mapped_column(ForeignKey("children.id"), index=True)
    lesson_id: Mapped[str] = mapped_column(String(100), index=True)
    course_id: Mapped[str] = mapped_column(String(100), index=True)
    unlocked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    max_completed_runs: Mapped[int] = mapped_column(Integer, default=2)
    completed_runs: Mapped[int] = mapped_column(Integer, default=0)
    cartoon_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(40), default="SUBSCRIPTION")
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE")

class PaymentWebhookEvent(Base):
    __tablename__ = "payment_webhook_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(30), default="stripe")
    event_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
