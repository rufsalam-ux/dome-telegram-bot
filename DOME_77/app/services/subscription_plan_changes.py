from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from dateutil.relativedelta import relativedelta
from sqlalchemy import select

from app.db.models import Child, Subscription, SubscriptionAuditEvent
from app.services.family_pricing import BILLING_WEEKS_PER_MONTH, calculate_family_price
from app.services.platform_settings import load_settings
from app.services.pricing_engine import subscription_plans_for_course


PLAN_CHANGE_REQUESTED = "PLAN_CHANGE_REQUESTED"
PLAN_CHANGE_UPDATED = "PLAN_CHANGE_UPDATED"
PLAN_CHANGE_CANCELLED = "PLAN_CHANGE_CANCELLED"
PLAN_CHANGE_ACTIVATED = "PLAN_CHANGE_ACTIVATED"
BILLING_PERIOD = "MONTH"


class PlanChangeError(ValueError):
    pass


@dataclass(frozen=True)
class PlanSnapshot:
    plan_id: str
    lessons_per_week: int
    price: float
    currency: str
    billing_period: str = BILLING_PERIOD

    @property
    def title(self) -> str:
        return f"{self.lessons_per_week} урок(а) в неделю"


@dataclass(frozen=True)
class PlanChangePreview:
    subscription_id: int
    current: PlanSnapshot
    requested: PlanSnapshot
    effective_at: datetime
    replaces_pending_plan_id: str | None = None


@dataclass(frozen=True)
class RenewalCharge:
    plan_id: str
    lessons_per_week: int
    amount: float
    currency: str
    billing_period: str
    activates_pending: bool


def _now(value: datetime | None = None) -> datetime:
    return value or datetime.utcnow()


def _billing_weeks() -> int:
    family = load_settings("pricing").get("family") or {}
    return max(1, int(family.get("billing_weeks_per_month", BILLING_WEEKS_PER_MONTH) or BILLING_WEEKS_PER_MONTH))


async def _family_position(db, parent_id: int, child_id: int) -> int:
    ids = list((await db.scalars(
        select(Child.id).where(Child.parent_id == int(parent_id)).order_by(Child.id.asc())
    )).all())
    try:
        return ids.index(int(child_id)) + 1
    except ValueError as exc:
        raise PlanChangeError("Ребёнок не принадлежит этому аккаунту") from exc


async def plan_snapshot_for_child(
    db,
    *,
    parent_id: int,
    child_id: int,
    course_id: str,
    plan_id: str,
) -> PlanSnapshot:
    plans = {str(item.get("id") or ""): item for item in subscription_plans_for_course(course_id)}
    raw = plans.get(str(plan_id))
    if not raw:
        raise PlanChangeError("Тариф не найден в backend pricing configuration")
    try:
        lessons_per_week = max(1, min(4, int(raw.get("lessons_per_week") or 0)))
        base_price = float(raw.get("monthly_price"))
    except (TypeError, ValueError) as exc:
        raise PlanChangeError("Цена тарифа не настроена на backend") from exc
    if base_price <= 0:
        raise PlanChangeError("Цена тарифа не настроена на backend")
    pricing = load_settings("pricing")
    family = pricing.get("family") or {}
    position = await _family_position(db, parent_id, child_id)
    price = calculate_family_price(
        base_price,
        position,
        lessons_per_week,
        float(family.get("additional_child_discount_per_lesson_eur", 0.50) or 0.0),
        _billing_weeks(),
    ).effective_price
    return PlanSnapshot(
        plan_id=str(plan_id),
        lessons_per_week=lessons_per_week,
        price=round(float(price), 2),
        currency=str(pricing.get("currency") or "EUR").upper(),
    )


async def plan_catalog_for_child(db, *, parent_id: int, child_id: int, course_id: str) -> list[PlanSnapshot]:
    result: list[PlanSnapshot] = []
    for raw in subscription_plans_for_course(course_id):
        plan_id = str(raw.get("id") or "")
        if plan_id:
            result.append(await plan_snapshot_for_child(
                db, parent_id=parent_id, child_id=child_id, course_id=course_id, plan_id=plan_id
            ))
    return result


def current_plan_snapshot(sub: Subscription) -> PlanSnapshot:
    return PlanSnapshot(
        plan_id=str(sub.current_plan_id or sub.plan_id or "weekly1"),
        lessons_per_week=max(1, int(sub.lessons_per_week or 1)),
        price=round(float(sub.monthly_price or 0.0), 2),
        currency=str(sub.currency or "EUR").upper(),
    )


def next_billing_period_start(sub: Subscription, *, now: datetime | None = None) -> datetime:
    point = _now(now)
    explicit = sub.current_period_end or sub.next_charge_at
    if explicit and explicit > point:
        return explicit
    anchor = sub.current_period_start or sub.started_at or point
    candidate = anchor + relativedelta(months=1)
    while candidate <= point:
        candidate += relativedelta(months=1)
    return candidate


async def preview_plan_change(
    db,
    sub: Subscription,
    *,
    parent_id: int,
    requested_plan_id: str,
    now: datetime | None = None,
) -> PlanChangePreview:
    if str(sub.status or "").upper() not in {"ACTIVE", "TRIALING"}:
        raise PlanChangeError("Изменить тариф можно только у активной подписки")
    current = current_plan_snapshot(sub)
    requested = await plan_snapshot_for_child(
        db,
        parent_id=parent_id,
        child_id=sub.child_id,
        course_id=sub.course_id,
        plan_id=requested_plan_id,
    )
    if requested.plan_id == current.plan_id and not sub.pending_plan_id:
        raise PlanChangeError("Этот тариф уже действует")
    point=_now(now)
    effective_at = sub.pending_plan_effective_at if sub.pending_plan_effective_at and sub.pending_plan_effective_at > point else next_billing_period_start(sub, now=point)
    return PlanChangePreview(
        subscription_id=sub.id,
        current=current,
        requested=requested,
        effective_at=effective_at,
        replaces_pending_plan_id=str(sub.pending_plan_id) if sub.pending_plan_id else None,
    )


def _audit(
    db,
    sub: Subscription,
    *,
    parent_id: int,
    event_type: str,
    old_plan: str | None,
    new_plan: str | None,
    requested_at: datetime,
    effective_at: datetime,
    old_price: float,
    new_price: float,
    currency: str,
    metadata: dict | None = None,
) -> SubscriptionAuditEvent:
    row = SubscriptionAuditEvent(
        subscription_id=sub.id,
        parent_id=int(parent_id),
        child_id=sub.child_id,
        event_type=event_type,
        old_plan_id=old_plan,
        new_plan_id=new_plan,
        requested_at=requested_at,
        effective_at=effective_at,
        old_price=round(float(old_price or 0.0), 2),
        new_price=round(float(new_price or 0.0), 2),
        currency=str(currency or "EUR").upper(),
        billing_period=BILLING_PERIOD,
        metadata_json=json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
    )
    db.add(row)
    return row


def schedule_plan_change(
    db,
    sub: Subscription,
    *,
    parent_id: int,
    preview: PlanChangePreview,
    provider_status: str,
    provider_reference: str = "",
    now: datetime | None = None,
) -> str:
    requested_at = _now(now)
    previous_pending = str(sub.pending_plan_id) if sub.pending_plan_id else None
    previous_price = float(sub.pending_plan_price or sub.monthly_price or 0.0)
    event_type = PLAN_CHANGE_UPDATED if previous_pending else PLAN_CHANGE_REQUESTED
    sub.current_plan_id = str(sub.current_plan_id or sub.plan_id or preview.current.plan_id)
    sub.plan_id = sub.current_plan_id
    sub.pending_plan_id = preview.requested.plan_id
    sub.pending_plan_created_at = requested_at
    sub.pending_plan_effective_at = preview.effective_at
    sub.pending_plan_price = preview.requested.price
    sub.pending_lessons_per_week = preview.requested.lessons_per_week
    sub.pending_plan_currency = preview.requested.currency
    sub.pending_provider_status = str(provider_status or "SCHEDULED").upper()
    sub.pending_provider_reference = str(provider_reference or "") or None
    _audit(
        db,
        sub,
        parent_id=parent_id,
        event_type=event_type,
        old_plan=previous_pending or preview.current.plan_id,
        new_plan=preview.requested.plan_id,
        requested_at=requested_at,
        effective_at=preview.effective_at,
        old_price=previous_price if previous_pending else preview.current.price,
        new_price=preview.requested.price,
        currency=preview.requested.currency,
        metadata={"previous_pending_plan_id": previous_pending, "provider_status": sub.pending_provider_status},
    )
    return event_type


def mark_pending_provider_scheduled(sub: Subscription, *, provider_reference: str = "") -> None:
    if sub.pending_plan_id:
        sub.pending_provider_status = "SCHEDULED"
        if provider_reference:
            sub.pending_provider_reference = provider_reference


def _clear_pending(sub: Subscription) -> None:
    sub.pending_plan_id = None
    sub.pending_plan_created_at = None
    sub.pending_plan_effective_at = None
    sub.pending_plan_price = None
    sub.pending_lessons_per_week = None
    sub.pending_plan_currency = None
    sub.pending_provider_status = None
    sub.pending_provider_reference = None


def cancel_plan_change(db, sub: Subscription, *, parent_id: int, now: datetime | None = None) -> None:
    if not sub.pending_plan_id:
        raise PlanChangeError("Запланированного изменения тарифа нет")
    cancelled_at = _now(now)
    current = current_plan_snapshot(sub)
    pending_id = str(sub.pending_plan_id)
    effective_at = sub.pending_plan_effective_at or next_billing_period_start(sub, now=cancelled_at)
    pending_price = float(sub.pending_plan_price or 0.0)
    requested_at = sub.pending_plan_created_at or cancelled_at
    _audit(
        db,
        sub,
        parent_id=parent_id,
        event_type=PLAN_CHANGE_CANCELLED,
        old_plan=pending_id,
        new_plan=current.plan_id,
        requested_at=requested_at,
        effective_at=effective_at,
        old_price=pending_price,
        new_price=current.price,
        currency=str(sub.pending_plan_currency or current.currency),
        metadata={"cancelled_at": cancelled_at.isoformat()},
    )
    _clear_pending(sub)


def renewal_charge_for(sub: Subscription, *, now: datetime | None = None) -> RenewalCharge:
    point = _now(now)
    due = bool(sub.pending_plan_id and sub.pending_plan_effective_at and sub.pending_plan_effective_at <= point)
    if due:
        return RenewalCharge(
            plan_id=str(sub.pending_plan_id),
            lessons_per_week=max(1, int(sub.pending_lessons_per_week or sub.lessons_per_week or 1)),
            amount=round(float(sub.pending_plan_price or 0.0), 2),
            currency=str(sub.pending_plan_currency or sub.currency or "EUR").upper(),
            billing_period=BILLING_PERIOD,
            activates_pending=True,
        )
    current = current_plan_snapshot(sub)
    return RenewalCharge(
        plan_id=current.plan_id,
        lessons_per_week=current.lessons_per_week,
        amount=current.price,
        currency=current.currency,
        billing_period=BILLING_PERIOD,
        activates_pending=False,
    )


def record_successful_billing_period(
    sub: Subscription,
    *,
    period_start: datetime,
    period_end: datetime | None = None,
) -> None:
    end = period_end or (period_start + relativedelta(months=1))
    sub.current_period_start = period_start
    sub.current_period_end = end
    sub.next_charge_at = end
    sub.lessons_allocated = max(1, int(sub.lessons_per_week or 1)) * _billing_weeks()
    sub.lessons_used = 0


def activate_pending_after_successful_payment(
    db,
    sub: Subscription,
    *,
    parent_id: int,
    paid_at: datetime,
    charged_plan_id: str = "",
    charged_amount: float = 0.0,
    period_end: datetime | None = None,
) -> bool:
    effective_at = sub.pending_plan_effective_at
    if not sub.pending_plan_id or not effective_at or paid_at < effective_at:
        record_successful_billing_period(sub, period_start=paid_at, period_end=period_end)
        return False
    if charged_plan_id and charged_plan_id != str(sub.pending_plan_id):
        record_successful_billing_period(sub, period_start=paid_at, period_end=period_end)
        sub.pending_plan_effective_at=sub.current_period_end
        return False
    expected_price = round(float(sub.pending_plan_price or 0.0), 2)
    if charged_amount > 0 and expected_price > 0 and abs(round(charged_amount, 2) - expected_price) > 0.01:
        record_successful_billing_period(sub, period_start=paid_at, period_end=period_end)
        sub.pending_plan_effective_at=sub.current_period_end
        return False
    current = current_plan_snapshot(sub)
    requested_at = sub.pending_plan_created_at or paid_at
    new_plan = str(sub.pending_plan_id)
    new_lessons = max(1, int(sub.pending_lessons_per_week or sub.lessons_per_week or 1))
    new_currency = str(sub.pending_plan_currency or sub.currency or "EUR").upper()
    _audit(
        db,
        sub,
        parent_id=parent_id,
        event_type=PLAN_CHANGE_ACTIVATED,
        old_plan=current.plan_id,
        new_plan=new_plan,
        requested_at=requested_at,
        effective_at=effective_at,
        old_price=current.price,
        new_price=expected_price,
        currency=new_currency,
        metadata={"paid_at": paid_at.isoformat(), "charged_amount": charged_amount},
    )
    sub.current_plan_id = new_plan
    sub.plan_id = new_plan
    sub.lessons_per_week = new_lessons
    sub.monthly_price = expected_price
    sub.currency = new_currency
    sub.started_at = paid_at
    _clear_pending(sub)
    record_successful_billing_period(sub, period_start=paid_at, period_end=period_end)
    return True
