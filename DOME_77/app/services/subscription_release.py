from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import LessonEntitlement, Subscription
from app.db.session import SessionLocal
from app.services.authored_content import augment_course
from app.services.course_catalog import list_courses
from app.services.platform_settings import load_settings

_RELEASE_LOCKS: defaultdict[tuple[int, str], asyncio.Lock] = defaultdict(asyncio.Lock)


def _course_order(course_id: str) -> list[str]:
    course = next((c for c in list_courses() if c.course_id == course_id), None)
    if course is None:
        return []
    raw = {"course_id": course.course_id, "lesson_ids": list(course.lesson_ids)}
    return [str(x) for x in augment_course(raw).get("lesson_ids") or []]


def _access_rules() -> tuple[int, int]:
    cfg = load_settings("pricing").get("regular_course") or {}
    months = int(cfg.get("lesson_access_months", 10) or 10)
    max_runs = int(cfg.get("max_completed_runs", 2) or 2)
    return months, max_runs


async def active_subscription(child_id: int, course_id: str) -> Subscription | None:
    async with SessionLocal() as db:
        return await db.scalar(
            select(Subscription).where(
                Subscription.child_id == child_id,
                Subscription.course_id == course_id,
                Subscription.status == "ACTIVE",
            ).order_by(Subscription.id.desc())
        )


async def release_due_lessons(child_id: int, course_id: str, *, now: datetime | None = None) -> list[LessonEntitlement]:
    """Unlock the quota due from this child's own 1/2/3/4-per-week plan.

    Quota is based on how many SUBSCRIPTION slots have already been consumed,
    not on the current prefix of the course list. Therefore reordering/inserting
    lessons in the admin catalog cannot accidentally grant extra lessons.
    """
    async with _RELEASE_LOCKS[(int(child_id), str(course_id))]:
        now = now or datetime.utcnow()
        sub = await active_subscription(child_id, course_id)
        if sub is None:
            return []
        started = sub.started_at or now
        if started > now:
            return []
        freq = max(1, min(4, int(sub.lessons_per_week or 1)))
        elapsed_days = max(0, (now - started).days)
        weeks_open = elapsed_days // 7 + 1
        baseline = max(0, int(getattr(sub, "release_baseline_count", 0) or 0))
        due_count = baseline + weeks_open * freq
        order = _course_order(course_id)
        if not order:
            return []
        months, max_runs = _access_rules()

        async with SessionLocal() as db:
            existing = (await db.scalars(select(LessonEntitlement).where(
                LessonEntitlement.child_id == child_id,
                LessonEntitlement.course_id == course_id,
            ))).all()
            by_lesson = {e.lesson_id: e for e in existing}
            subscription_lesson_ids = {e.lesson_id for e in existing if str(e.source or "") == "SUBSCRIPTION"}
            slots_left = max(0, due_count - len(subscription_lesson_ids))
            if slots_left <= 0:
                return []

            candidates = [lesson_id for lesson_id in order if lesson_id not in by_lesson]
            created: list[LessonEntitlement] = []
            # Schedule positions are relative to THIS plan segment, not to the
            # lifetime number of lessons. Without subtracting the baseline, a child
            # who changed/restarted a plan after many prior lessons could wait weeks
            # before the first lesson of the new segment.
            segment_slot_index = max(0, len(subscription_lesson_ids) - baseline)
            for lesson_id in candidates:
                if len(created) >= slots_left:
                    break
                week_index = segment_slot_index // freq
                unlock_at = started + timedelta(days=7 * week_index)
                if unlock_at > now:
                    break
                row = LessonEntitlement(
                    child_id=child_id,
                    lesson_id=lesson_id,
                    course_id=course_id,
                    unlocked_at=unlock_at,
                    expires_at=unlock_at + relativedelta(months=months),
                    max_completed_runs=max_runs,
                    completed_runs=0,
                    source="SUBSCRIPTION",
                    status="ACTIVE",
                )
                db.add(row)
                created.append(row)
                segment_slot_index += 1
            if not created:
                return []
            try:
                await db.commit()
            except IntegrityError:
                # Another process may have released the same lessons concurrently.
                # Roll back and report only rows that now exist instead of duplicating.
                await db.rollback()
                return []
            for row in created:
                await db.refresh(row)
            return created


async def ensure_test_entitlement(child_id: int, lesson_id: str, course_id: str) -> LessonEntitlement:
    """Free-QA helper used only when no explicit personal test plan is enforcing quota."""
    months, max_runs = _access_rules()
    async with SessionLocal() as db:
        row = await db.scalar(select(LessonEntitlement).where(
            LessonEntitlement.child_id == child_id,
            LessonEntitlement.lesson_id == lesson_id,
            LessonEntitlement.course_id == course_id,
        ).order_by(LessonEntitlement.id.desc()))
        if row is not None:
            return row
        now = datetime.utcnow()
        row = LessonEntitlement(
            child_id=child_id,
            lesson_id=lesson_id,
            course_id=course_id,
            unlocked_at=now,
            expires_at=now + relativedelta(months=months),
            max_completed_runs=max_runs,
            completed_runs=0,
            source="FREE_TEST",
            status="ACTIVE",
        )
        db.add(row)
        try:
            await db.commit()
            await db.refresh(row)
            return row
        except IntegrityError:
            await db.rollback()
            existing = await db.scalar(select(LessonEntitlement).where(
                LessonEntitlement.child_id == child_id,
                LessonEntitlement.lesson_id == lesson_id,
                LessonEntitlement.course_id == course_id,
            ).order_by(LessonEntitlement.id.desc()))
            if existing is None:
                raise
            return existing
