from __future__ import annotations

from datetime import UTC, datetime

from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Child, LessonEntitlement, Parent
from app.db.session import SessionLocal
from app.services.lesson_loader import load_lesson


FREE_DEMO_LESSON_ID = "demo_001"
FREE_DEMO_SOURCE = "FREE_DEMO"


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _demo_rules() -> tuple[str, int, int]:
    lesson = load_lesson(FREE_DEMO_LESSON_ID)
    if not lesson or not bool(lesson.get("active", True)):
        raise RuntimeError(f"Free demo lesson {FREE_DEMO_LESSON_ID} is missing or inactive")
    course_id = str(lesson.get("course_id") or "conversation")
    access_months = max(1, int(lesson.get("expires_after_months") or 10))
    max_completed_runs = max(1, int(lesson.get("max_completed_runs") or 2))
    return course_id, access_months, max_completed_runs


async def ensure_free_demo_entitlement(
    db: AsyncSession,
    *,
    parent_id: int,
    child_id: int | None = None,
    now: datetime | None = None,
) -> tuple[LessonEntitlement | None, bool]:
    """Grant the standalone free demo to a verified parent's first child.

    The caller owns the transaction. Existing entitlements are returned exactly
    as they are so expiration and completed-run limits can never be reset by
    registration, login, retries, or a later deployment.
    """
    parent = await db.get(Parent, int(parent_id))
    if (
        parent is None
        or not bool(parent.email_verified)
        or not str(parent.email or "").strip()
        or not str(parent.password_hash or "").strip()
    ):
        return None, False

    first_child = await db.scalar(
        select(Child)
        .where(Child.parent_id == parent.id)
        .order_by(Child.id.asc())
        .limit(1)
    )
    if first_child is None or (child_id is not None and first_child.id != int(child_id)):
        return None, False

    course_id, access_months, max_completed_runs = _demo_rules()
    existing = await db.scalar(
        select(LessonEntitlement).where(
            LessonEntitlement.child_id == first_child.id,
            LessonEntitlement.lesson_id == FREE_DEMO_LESSON_ID,
            LessonEntitlement.course_id == course_id,
        )
    )
    if existing is not None:
        return existing, False

    unlocked_at = now or _utcnow()
    entitlement = LessonEntitlement(
        child_id=first_child.id,
        lesson_id=FREE_DEMO_LESSON_ID,
        course_id=course_id,
        unlocked_at=unlocked_at,
        expires_at=unlocked_at + relativedelta(months=access_months),
        max_completed_runs=max_completed_runs,
        completed_runs=0,
        source=FREE_DEMO_SOURCE,
        status="ACTIVE",
    )
    db.add(entitlement)
    await db.flush()
    return entitlement, True


async def backfill_free_demo_entitlements(*, now: datetime | None = None) -> int:
    """Apply the same standalone rule to eligible accounts created before it."""
    async with SessionLocal() as db:
        parent_ids = (
            await db.scalars(
                select(Parent.id).where(
                    Parent.email_verified.is_(True),
                    Parent.email.is_not(None),
                    Parent.email != "",
                    Parent.password_hash.is_not(None),
                    Parent.password_hash != "",
                )
            )
        ).all()
        created = 0
        for parent_id in parent_ids:
            _, was_created = await ensure_free_demo_entitlement(
                db, parent_id=int(parent_id), now=now
            )
            created += int(was_created)
        await db.commit()
        return created
