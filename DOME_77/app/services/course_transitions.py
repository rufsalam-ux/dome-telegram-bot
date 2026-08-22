from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from app.core.config import settings
from app.db.models import Child, Parent, Subscription, LessonEntitlement, CourseEnrollment
from app.db.session import SessionLocal
from app.services.course_catalog import list_courses
from app.services.runtime_mode import CONVERSATION_ONLY, client_course_allowed

DEFAULT_TRANSITIONS: dict[str, Any] = {
    "schema_version": "1.0",
    "notify_remaining_lessons": [4, 1],
    "pause_subscription_if_no_choice": True,
    "courses": {
        "conversation": {
            "repeat_allowed": True,
            "recommended": "learn_to_read",
            "next": ["learn_to_read", "reading"],
        },
        "learn_to_read": {
            "repeat_allowed": True,
            "recommended": "reading",
            "next": ["conversation", "reading"],
        },
        "reading": {
            "repeat_allowed": True,
            "recommended": "conversation",
            "next": ["conversation"],
        },
    },
}


def _settings_path() -> Path:
    p = settings.storage_root / "platform-settings" / "course_transitions.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_transition_settings() -> dict[str, Any]:
    p = _settings_path()
    if not p.exists():
        save_transition_settings(deepcopy(DEFAULT_TRANSITIONS))
    try:
        data = json.loads(p.read_text("utf-8"))
        return data if isinstance(data, dict) else deepcopy(DEFAULT_TRANSITIONS)
    except Exception:
        return deepcopy(DEFAULT_TRANSITIONS)


def save_transition_settings(data: dict[str, Any]) -> dict[str, Any]:
    p = _settings_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(p)
    return data


def get_route(course_id: str) -> dict[str, Any]:
    if CONVERSATION_ONLY:
        return {"repeat_allowed": False, "recommended": None, "next": []}
    cfg = load_transition_settings()
    return deepcopy(((cfg.get("courses") or {}).get(course_id) or {}))


def set_route(course_id: str, next_course_ids: list[str], *, recommended: str | None = None, repeat_allowed: bool = True) -> dict[str, Any]:
    if CONVERSATION_ONLY:
        raise RuntimeError("Course transitions are temporarily disabled in conversation-only mode")
    valid = {c.course_id for c in list_courses()}
    clean = []
    for cid in next_course_ids:
        cid = str(cid).strip()
        if cid and cid in valid and cid != course_id and cid not in clean:
            clean.append(cid)
    if recommended and recommended not in clean:
        recommended = clean[0] if clean else None
    cfg = load_transition_settings()
    courses = cfg.setdefault("courses", {})
    courses[course_id] = {"repeat_allowed": bool(repeat_allowed), "recommended": recommended or (clean[0] if clean else None), "next": clean}
    return save_transition_settings(cfg)


def course_titles() -> dict[str, str]:
    return {c.course_id: c.title for c in list_courses()}


def _choice_path(child_id: int, course_id: str) -> Path:
    p = settings.storage_root / "course-transition-choices" / str(child_id)
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{course_id}.json"


def save_choice(child_id: int, course_id: str, target: str) -> dict[str, Any]:
    data = {"child_id": int(child_id), "course_id": str(course_id), "target": str(target), "chosen_at": datetime.utcnow().isoformat()}
    _choice_path(child_id, course_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    return data


def load_choice(child_id: int, course_id: str) -> dict[str, Any] | None:
    try:
        return json.loads(_choice_path(child_id, course_id).read_text("utf-8"))
    except Exception:
        return None


def clear_choice(child_id: int, course_id: str) -> None:
    try:
        _choice_path(child_id, course_id).unlink()
    except FileNotFoundError:
        pass


def _notice_path(child_id: int, course_id: str) -> Path:
    p = settings.storage_root / "course-transition-notices" / str(child_id)
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{course_id}.json"


def load_notice_state(child_id: int, course_id: str) -> dict[str, Any]:
    try:
        data = json.loads(_notice_path(child_id, course_id).read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def mark_notice(child_id: int, course_id: str, key: str) -> None:
    state = load_notice_state(child_id, course_id)
    state[str(key)] = datetime.utcnow().isoformat()
    _notice_path(child_id, course_id).write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")


async def course_progress(child_id: int, course_id: str) -> tuple[int, int, int]:
    course = next((c for c in list_courses() if c.course_id == course_id), None)
    if course is None:
        return 0, 0, 0
    lesson_ids = [str(x) for x in course.lesson_ids]
    total = len(lesson_ids)
    if not lesson_ids:
        return 0, 0, 0
    async with SessionLocal() as db:
        rows = (await db.scalars(select(LessonEntitlement).where(
            LessonEntitlement.child_id == child_id,
            LessonEntitlement.course_id == course_id,
            LessonEntitlement.lesson_id.in_(lesson_ids),
        ))).all()
    completed = {r.lesson_id for r in rows if int(r.completed_runs or 0) >= 1}
    count = len(completed)
    return count, total, max(0, total - count)


async def active_course_rows() -> list[tuple[Child, Parent, Subscription]]:
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(Child, Parent, Subscription)
            .join(Parent, Child.parent_id == Parent.id)
            .join(Subscription, Subscription.child_id == Child.id)
            .where(Subscription.status == "ACTIVE")
        )).all()
        return list(rows)


async def apply_transition(child_id: int, from_course_id: str, target: str) -> str:
    """Apply a previously chosen transition after course completion.

    Keeps the same active subscription/plan. For a new course, switches its course_id
    and starts a new release segment. For repeat, resets entitlement counters while
    retaining historical lesson sessions/attempts for progress reports.
    """
    if CONVERSATION_ONLY and target != "repeat":
        return "TRANSITIONS_DISABLED"
    now = datetime.utcnow()
    async with SessionLocal() as db:
        sub = await db.scalar(select(Subscription).where(
            Subscription.child_id == child_id,
            Subscription.course_id == from_course_id,
            Subscription.status == "ACTIVE",
        ).order_by(Subscription.id.desc()))
        if sub is None:
            return "NO_ACTIVE_SUBSCRIPTION"

        if target == "repeat":
            ents = (await db.scalars(select(LessonEntitlement).where(
                LessonEntitlement.child_id == child_id,
                LessonEntitlement.course_id == from_course_id,
            ))).all()
            for e in ents:
                e.completed_runs = 0
                e.cartoon_generated = False
                e.status = "ACTIVE"
                e.unlocked_at = now
                e.expires_at = None
                e.source = "REPEAT_CYCLE"
            sub.started_at = now
            sub.release_baseline_count = 0
            await db.commit()
            clear_choice(child_id, from_course_id)
            return "REPEATED"

        valid = {c.course_id for c in list_courses() if c.active}
        if target not in valid:
            return "INVALID_TARGET"

        existing_count = len((await db.scalars(select(LessonEntitlement.id).where(
            LessonEntitlement.child_id == child_id,
            LessonEntitlement.course_id == target,
            LessonEntitlement.source == "SUBSCRIPTION",
        ))).all())
        sub.course_id = target
        sub.started_at = now
        sub.release_baseline_count = existing_count
        enr = await db.scalar(select(CourseEnrollment).where(
            CourseEnrollment.child_id == child_id,
            CourseEnrollment.course_id == target,
            CourseEnrollment.status == "ACTIVE",
        ).order_by(CourseEnrollment.id.desc()))
        if enr is None:
            db.add(CourseEnrollment(child_id=child_id, course_id=target, status="ACTIVE", access_source="COURSE_TRANSITION", payment_reference=str(sub.provider_subscription_id or "TRANSITION")))
        await db.commit()
    clear_choice(child_id, from_course_id)
    return "MOVED"


def _switch_path(child_id: int) -> Path:
    p = settings.storage_root / "course-switches"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{int(child_id)}.json"

def save_course_switch(child_id: int, from_course_id: str, target_course_id: str, mode: str) -> dict[str, Any]:
    if mode not in {"after_current", "immediate"}:
        raise ValueError("invalid switch mode")
    data={"child_id":int(child_id),"from_course_id":str(from_course_id),"target_course_id":str(target_course_id),"mode":mode,"created_at":datetime.utcnow().isoformat()}
    _switch_path(child_id).write_text(json.dumps(data,ensure_ascii=False,indent=2),"utf-8")
    return data

def load_course_switch(child_id: int) -> dict[str, Any] | None:
    try:
        data=json.loads(_switch_path(child_id).read_text("utf-8"))
        return data if isinstance(data,dict) else None
    except Exception:
        return None

def clear_course_switch(child_id: int) -> None:
    try: _switch_path(child_id).unlink()
    except FileNotFoundError: pass

async def apply_course_switch(child_id: int, from_course_id: str, target_course_id: str) -> str:
    """Move an active child subscription to another course without deleting old progress.
    Existing entitlements/history remain intact. The same frequency is preserved.
    """
    if CONVERSATION_ONLY:
        return "TRANSITIONS_DISABLED"
    valid={c.course_id for c in list_courses() if c.active}
    if target_course_id not in valid or target_course_id==from_course_id:
        return "INVALID_TARGET"
    now=datetime.utcnow()
    async with SessionLocal() as db:
        sub=await db.scalar(select(Subscription).where(Subscription.child_id==child_id,Subscription.course_id==from_course_id,Subscription.status=="ACTIVE").order_by(Subscription.id.desc()))
        if sub is None:
            return "NO_ACTIVE_SUBSCRIPTION"
        existing_count=len((await db.scalars(select(LessonEntitlement.id).where(LessonEntitlement.child_id==child_id,LessonEntitlement.course_id==target_course_id,LessonEntitlement.source=="SUBSCRIPTION"))).all())
        sub.course_id=target_course_id
        sub.started_at=now
        sub.release_baseline_count=existing_count
        enr=await db.scalar(select(CourseEnrollment).where(CourseEnrollment.child_id==child_id,CourseEnrollment.course_id==target_course_id,CourseEnrollment.status=="ACTIVE").order_by(CourseEnrollment.id.desc()))
        if enr is None:
            db.add(CourseEnrollment(child_id=child_id,course_id=target_course_id,status="ACTIVE",access_source="COURSE_SWITCH",payment_reference=str(sub.provider_subscription_id or "COURSE_SWITCH")))
        await db.commit()
    clear_course_switch(child_id)
    return "MOVED"
