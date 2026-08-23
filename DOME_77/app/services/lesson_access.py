from __future__ import annotations

from datetime import datetime
from sqlalchemy import select, update

from app.db.session import SessionLocal
from app.db.models import LessonEntitlement, LessonSession, Subscription


async def _consume_subscription_allocation(db, entitlement: LessonEntitlement) -> None:
    if str(entitlement.source or "") != "SUBSCRIPTION" or int(entitlement.completed_runs or 0) != 1:
        return
    sub=await db.scalar(select(Subscription).where(
        Subscription.child_id==entitlement.child_id,
        Subscription.course_id==entitlement.course_id,
        Subscription.status=='ACTIVE',
    ).order_by(Subscription.id.desc()))
    if sub is not None:
        sub.lessons_used=min(max(0,int(sub.lessons_allocated or 0)),max(0,int(sub.lessons_used or 0))+1)


async def get_entitlement(child_id: int, lesson_id: str, course_id: str) -> LessonEntitlement | None:
    async with SessionLocal() as db:
        return await db.scalar(select(LessonEntitlement).where(
            LessonEntitlement.child_id == child_id,
            LessonEntitlement.lesson_id == lesson_id,
            LessonEntitlement.course_id == course_id,
        ).order_by(LessonEntitlement.id.desc()))


async def can_start(child_id: int, lesson_id: str, course_id: str) -> tuple[bool, str, LessonEntitlement | None]:
    row = await get_entitlement(child_id, lesson_id, course_id)
    if row is None:
        return False, "LOCKED", None
    now = datetime.utcnow()
    if row.expires_at and row.expires_at < now:
        return False, "EXPIRED", row
    if row.completed_runs >= row.max_completed_runs:
        return False, "RUN_LIMIT", row
    return True, "OK", row


async def mark_completed(child_id: int, lesson_id: str, course_id: str) -> LessonEntitlement:
    """Compatibility helper for old callers that do not have a session id."""
    async with SessionLocal() as db:
        row = await db.scalar(select(LessonEntitlement).where(
            LessonEntitlement.child_id == child_id,
            LessonEntitlement.lesson_id == lesson_id,
            LessonEntitlement.course_id == course_id,
        ).order_by(LessonEntitlement.id.desc()))
        if row is None:
            raise RuntimeError("Lesson entitlement is missing; lesson must be unlocked before completion")
        row.completed_runs = min(row.max_completed_runs, row.completed_runs + 1)
        await _consume_subscription_allocation(db,row)
        if row.completed_runs >= row.max_completed_runs:
            row.status = "COMPLETED"
        await db.commit()
        await db.refresh(row)
        return row


async def complete_session_once(
    *, session_id: int, child_id: int, lesson_id: str, course_id: str, final_step: int
) -> tuple[LessonEntitlement, bool]:
    """Atomically finish one authored lesson session and consume exactly one run.

    Duplicate/stale completion callbacks are harmless: only the first transition
    of this LessonSession to COMPLETED increments LessonEntitlement.completed_runs.
    """
    async with SessionLocal() as db:
        entitlement = await db.scalar(select(LessonEntitlement).where(
            LessonEntitlement.child_id == child_id,
            LessonEntitlement.lesson_id == lesson_id,
            LessonEntitlement.course_id == course_id,
        ).order_by(LessonEntitlement.id.desc()))
        if entitlement is None:
            raise RuntimeError("Lesson entitlement is missing; lesson must be unlocked before completion")

        result = await db.execute(
            update(LessonSession)
            .where(LessonSession.id == int(session_id), LessonSession.status != "COMPLETED")
            .values(status="COMPLETED", completed_at=datetime.utcnow(), current_step=int(final_step))
        )
        newly_completed = bool(result.rowcount)
        if newly_completed:
            entitlement.completed_runs = min(entitlement.max_completed_runs, entitlement.completed_runs + 1)
            await _consume_subscription_allocation(db,entitlement)
            if entitlement.completed_runs >= entitlement.max_completed_runs:
                entitlement.status = "COMPLETED"
        await db.commit()
        await db.refresh(entitlement)
        return entitlement, newly_completed


async def mark_cartoon_generated(child_id: int, lesson_id: str, course_id: str | None = None) -> None:
    async with SessionLocal() as db:
        conditions = [
            LessonEntitlement.child_id == child_id,
            LessonEntitlement.lesson_id == lesson_id,
        ]
        if course_id:
            conditions.append(LessonEntitlement.course_id == course_id)
        row = await db.scalar(select(LessonEntitlement).where(*conditions).order_by(LessonEntitlement.id.desc()))
        if row and not row.cartoon_generated:
            row.cartoon_generated = True
            await db.commit()
