"""Server-side account access policy for standalone DOME clients.

This module intentionally does not make entitlement or subscription decisions.
It answers only whether a signed-in parent is allowed to use protected DOME
endpoints.  That keeps administrative blocks reversible and preserves a
parent's children, lesson progress, recordings and purchase history.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.qa_access import is_owner_parent
from app.services.platform_settings import load_settings, save_settings


AUTOMATIC_ACCESS = "AUTOMATIC"
MANUAL_APPROVAL = "MANUAL"
ACTIVE = "ACTIVE"
BLOCKED = "BLOCKED"
PENDING_APPROVAL = "PENDING_APPROVAL"
VALID_ACCOUNT_STATUSES = frozenset({ACTIVE, BLOCKED, PENDING_APPROVAL})


def account_access_settings() -> dict[str, Any]:
    """Return the persisted access policy, repairing invalid legacy values."""
    data = load_settings("account_access")
    mode = str(data.get("new_user_access_mode") or AUTOMATIC_ACCESS).upper()
    if mode not in {AUTOMATIC_ACCESS, MANUAL_APPROVAL}:
        mode = AUTOMATIC_ACCESS
    return {"schema_version": "1.0", "new_user_access_mode": mode}


def set_new_user_access_mode(mode: str) -> dict[str, Any]:
    value = str(mode or "").upper()
    if value not in {AUTOMATIC_ACCESS, MANUAL_APPROVAL}:
        raise ValueError("new_user_access_mode must be AUTOMATIC or MANUAL")
    return save_settings("account_access", {"schema_version": "1.0", "new_user_access_mode": value})


def initial_account_status() -> str:
    return PENDING_APPROVAL if account_access_settings()["new_user_access_mode"] == MANUAL_APPROVAL else ACTIVE


def account_status(parent: Any) -> str:
    """Owners are always active; legacy null statuses remain accessible."""
    if is_owner_parent(parent):
        return ACTIVE
    value = str(getattr(parent, "account_status", "") or "").upper()
    return value if value in VALID_ACCOUNT_STATUSES else ACTIVE


def account_access_error(parent: Any) -> tuple[str, str] | None:
    status = account_status(parent)
    if status == BLOCKED:
        return "ACCOUNT_BLOCKED", "Доступ к приложению временно приостановлен."
    if status == PENDING_APPROVAL:
        return "ACCOUNT_PENDING_APPROVAL", "Заявка ожидает подтверждения администратора."
    return None


def set_account_status(parent: Any, status: str, *, actor: str = "admin") -> str:
    """Apply an administrative status without ever allowing an owner block."""
    value = str(status or "").upper()
    if value not in VALID_ACCOUNT_STATUSES:
        raise ValueError("status must be ACTIVE, BLOCKED or PENDING_APPROVAL")
    if is_owner_parent(parent):
        if value != ACTIVE:
            raise PermissionError("OWNER_UNBLOCKABLE")
        value = ACTIVE
    parent.account_status = value
    parent.access_status_changed_at = datetime.utcnow()
    parent.access_status_changed_by = str(actor or "admin")[:120]
    return value
