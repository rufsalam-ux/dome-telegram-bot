from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Child,
    LessonEntitlement,
    Parent,
    QaAccessAuditEvent,
    QaAccessGrant,
)
from app.db.session import SessionLocal


log = logging.getLogger("dome.qa_access")

STANDARD_ROLE = "STANDARD"
QA_TEST_ROLE = "QA_TEST"
ADMIN_ROLE = "ADMIN"
QA_ROLES = frozenset({QA_TEST_ROLE, ADMIN_ROLE})
UNLIMITED_LESSON_RUNS = "UNLIMITED_LESSON_RUNS"
ACTIVE = "ACTIVE"
REVOKED = "REVOKED"


async def active_run_limit_grant(
    db: AsyncSession,
    *,
    child_id: int,
    lesson_id: str,
    course_id: str,
) -> tuple[Parent | None, QaAccessGrant | None]:
    """Return an explicit grant only when both account role and scope match."""
    child = await db.get(Child, int(child_id))
    if child is None:
        return None, None
    parent = await db.get(Parent, child.parent_id)
    if parent is None or str(parent.account_role or STANDARD_ROLE).upper() not in QA_ROLES:
        return parent, None
    grant = await db.scalar(
        select(QaAccessGrant).where(
            QaAccessGrant.parent_id == parent.id,
            QaAccessGrant.child_id == child.id,
            QaAccessGrant.lesson_id == str(lesson_id),
            QaAccessGrant.course_id == str(course_id),
            QaAccessGrant.permission == UNLIMITED_LESSON_RUNS,
            QaAccessGrant.status == ACTIVE,
        )
    )
    return parent, grant


def add_qa_audit_event(
    db: AsyncSession,
    *,
    event_type: str,
    parent_id: int,
    child_id: int,
    lesson_id: str,
    course_id: str,
    actor: str,
    grant: QaAccessGrant | None = None,
    entitlement: LessonEntitlement | None = None,
    lesson_session_id: int | None = None,
    details: dict | None = None,
) -> QaAccessAuditEvent:
    event = QaAccessAuditEvent(
        event_type=event_type,
        grant_id=grant.id if grant is not None else None,
        parent_id=int(parent_id),
        child_id=int(child_id),
        lesson_id=str(lesson_id),
        course_id=str(course_id),
        entitlement_id=entitlement.id if entitlement is not None else None,
        lesson_session_id=lesson_session_id,
        actor=str(actor),
        completed_runs=int(entitlement.completed_runs or 0) if entitlement is not None else 0,
        max_completed_runs=int(entitlement.max_completed_runs or 0) if entitlement is not None else 0,
        details_json=json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
    )
    db.add(event)
    return event


async def grant_run_limit_access(
    db: AsyncSession,
    *,
    child_id: int,
    lesson_id: str,
    course_id: str,
    actor: str,
    reason: str,
    account_role: str = QA_TEST_ROLE,
    external_key: str | None = None,
) -> tuple[QaAccessGrant, bool]:
    """Explicitly classify an account and grant one scoped QA permission."""
    child = await db.get(Child, int(child_id))
    if child is None:
        raise ValueError("Child not found")
    parent = await db.get(Parent, child.parent_id)
    if parent is None:
        raise ValueError("Parent not found")

    requested_role = str(account_role or QA_TEST_ROLE).upper()
    if requested_role not in QA_ROLES:
        raise ValueError("QA account role must be QA_TEST or ADMIN")
    role_changed = False
    if str(parent.account_role or STANDARD_ROLE).upper() not in QA_ROLES:
        parent.account_role = requested_role
        role_changed = True

    grant = await db.scalar(
        select(QaAccessGrant).where(
            QaAccessGrant.child_id == child.id,
            QaAccessGrant.lesson_id == str(lesson_id),
            QaAccessGrant.course_id == str(course_id),
            QaAccessGrant.permission == UNLIMITED_LESSON_RUNS,
        )
    )
    created_or_reactivated = False
    if grant is None:
        grant = QaAccessGrant(
            parent_id=parent.id,
            child_id=child.id,
            lesson_id=str(lesson_id),
            course_id=str(course_id),
            permission=UNLIMITED_LESSON_RUNS,
            status=ACTIVE,
            external_key=external_key,
            granted_by=str(actor),
            reason=str(reason),
        )
        db.add(grant)
        await db.flush()
        created_or_reactivated = True
    else:
        if grant.parent_id != parent.id:
            raise ValueError("QA grant belongs to another parent")
        if grant.status != ACTIVE:
            grant.status = ACTIVE
            grant.revoked_at = None
            created_or_reactivated = True
        if external_key and grant.external_key not in {None, external_key}:
            raise ValueError("QA grant has a different external key")
        if external_key and grant.external_key is None:
            grant.external_key = external_key
            created_or_reactivated = True
        grant.granted_by = str(actor)
        grant.reason = str(reason)
        grant.updated_at = datetime.utcnow()
        await db.flush()

    changed = created_or_reactivated or role_changed
    if changed:
        add_qa_audit_event(
            db,
            event_type="QA_ACCESS_GRANTED",
            grant=grant,
            parent_id=parent.id,
            child_id=child.id,
            lesson_id=lesson_id,
            course_id=course_id,
            actor=actor,
            details={"account_role": parent.account_role, "reason": reason},
        )
    return grant, changed


async def revoke_run_limit_access(
    db: AsyncSession,
    *,
    child_id: int,
    lesson_id: str,
    course_id: str,
    actor: str,
    reason: str,
) -> bool:
    child = await db.get(Child, int(child_id))
    if child is None:
        raise ValueError("Child not found")
    parent = await db.get(Parent, child.parent_id)
    grant = await db.scalar(
        select(QaAccessGrant).where(
            QaAccessGrant.parent_id == child.parent_id,
            QaAccessGrant.child_id == child.id,
            QaAccessGrant.lesson_id == str(lesson_id),
            QaAccessGrant.course_id == str(course_id),
            QaAccessGrant.permission == UNLIMITED_LESSON_RUNS,
            QaAccessGrant.status == ACTIVE,
        )
    )
    if parent is None or grant is None:
        return False
    grant.status = REVOKED
    grant.revoked_at = datetime.utcnow()
    grant.updated_at = grant.revoked_at
    add_qa_audit_event(
        db,
        event_type="QA_ACCESS_REVOKED",
        grant=grant,
        parent_id=parent.id,
        child_id=child.id,
        lesson_id=lesson_id,
        course_id=course_id,
        actor=actor,
        details={"reason": reason},
    )
    await db.flush()
    remaining = await db.scalar(
        select(func.count(QaAccessGrant.id)).where(
            QaAccessGrant.parent_id == parent.id,
            QaAccessGrant.status == ACTIVE,
            QaAccessGrant.permission == UNLIMITED_LESSON_RUNS,
        )
    )
    if str(parent.account_role or STANDARD_ROLE).upper() == QA_TEST_ROLE and int(remaining or 0) == 0:
        parent.account_role = STANDARD_ROLE
    return True


def _bootstrap_config() -> dict:
    path = Path(__file__).resolve().parents[2] / "config" / "qa_access.json"
    if not path.exists():
        return {"schema_version": 1, "bootstrap_grants": []}
    return json.loads(path.read_text(encoding="utf-8"))


async def bootstrap_qa_access_grants() -> dict[str, int]:
    """Apply auditable one-time owner-approved grants from production config.

    Existing external keys, including revoked grants, are never re-applied.
    Display names are only locators during this migration; runtime authorization
    relies exclusively on account_role plus the persisted scoped grant.
    """
    config = _bootstrap_config()
    result = {"created": 0, "existing": 0, "skipped": 0}
    async with SessionLocal() as db:
        for spec in config.get("bootstrap_grants") or []:
            external_key = str(spec.get("external_key") or "").strip()
            lesson_id = str(spec.get("lesson_id") or "").strip()
            course_id = str(spec.get("course_id") or "").strip()
            permission = str(spec.get("permission") or UNLIMITED_LESSON_RUNS).strip()
            if not external_key or not lesson_id or not course_id or permission != UNLIMITED_LESSON_RUNS:
                log.error("QA bootstrap entry skipped: invalid key, scope, or permission")
                result["skipped"] += 1
                continue
            existing = await db.scalar(
                select(QaAccessGrant).where(QaAccessGrant.external_key == external_key)
            )
            if existing is not None:
                result["existing"] += 1
                continue

            expected_name = str(spec.get("child_display_name") or "").strip().casefold()
            rows = (
                await db.execute(select(Child, Parent).join(Parent, Child.parent_id == Parent.id))
            ).all()
            matches = []
            for child, parent in rows:
                if str(child.display_name or "").strip().casefold() != expected_name:
                    continue
                if spec.get("require_verified_parent", True) and not (
                    bool(parent.email_verified)
                    and str(parent.email or "").strip()
                    and str(parent.password_hash or "").strip()
                ):
                    continue
                matches.append((child, parent))
            if spec.get("require_unique_match", True) and len(matches) != 1:
                log.error(
                    "QA bootstrap entry %s skipped: expected one verified child match, found %s",
                    external_key,
                    len(matches),
                )
                result["skipped"] += 1
                continue
            if not matches:
                result["skipped"] += 1
                continue
            child, _ = matches[0]
            _, changed = await grant_run_limit_access(
                db,
                child_id=child.id,
                lesson_id=lesson_id,
                course_id=course_id,
                actor=f"bootstrap:{external_key}",
                reason=str(spec.get("reason") or "Owner-approved QA access"),
                account_role=str(spec.get("account_role") or QA_TEST_ROLE),
                external_key=external_key,
            )
            result["created"] += int(changed)
        await db.commit()
    return result
