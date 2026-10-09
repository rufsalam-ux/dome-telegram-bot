from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from sqlalchemy import select
from app.db.models import Child, CourseEnrollment, Subscription, LessonEntitlement
from app.services.subscription_plan_changes import (
    activate_pending_after_successful_payment,
    cancel_plan_change,
    current_plan_snapshot,
    mark_pending_provider_scheduled,
    plan_identity_matches,
    record_successful_billing_period,
)
from app.services.pricing_versions import MONTH, normalize_billing_period

ACTIVE={'ACTIVE','TRIALING','PAID','SUCCEEDED'}
PAST_DUE={'PAST_DUE','FAILED','UNPAID'}
CANCELLED={'CANCELLED','CANCELED','PAUSED','DELETED','EXPIRED'}

@dataclass
class NormalizedPaymentEvent:
    provider: str
    event_id: str
    event_type: str
    status: str = ''
    child_id: int = 0
    course_id: str = ''
    plan_id: str = ''
    plan_version_id: str = ''
    billing_period: str = MONTH
    provider_plan_id: str = ''
    lessons_per_week: int = 1
    monthly_price: float = 0.0
    currency: str = 'EUR'
    provider_subscription_id: str = ''
    occurred_at: datetime | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    charged_amount: float = 0.0
    special_first_year: bool = False
    standard_renewal_price: float = 0.0
    intro_week_price: float = 0.0
    plan_from_subscription_snapshot: bool = False
    raw: dict[str,Any] = field(default_factory=dict)


def normalized_status(value:str) -> str:
    s=(value or '').upper().strip()
    if s in ACTIVE: return 'ACTIVE'
    if s in PAST_DUE: return 'PAST_DUE'
    if s in CANCELLED: return 'CANCELLED'
    return s or 'UNKNOWN'

async def _baseline(db, child_id:int, course_id:str) -> int:
    rows=(await db.scalars(select(LessonEntitlement.id).where(
        LessonEntitlement.child_id==child_id,
        LessonEntitlement.course_id==course_id,
        LessonEntitlement.source=='SUBSCRIPTION'))).all()
    return len(rows)

async def _matching_subscription(db, ev:NormalizedPaymentEvent):
    if ev.provider_subscription_id:
        row=await db.scalar(select(Subscription).where(Subscription.provider_subscription_id==ev.provider_subscription_id, Subscription.payment_provider==ev.provider).order_by(Subscription.id.desc()))
        if row: return row
    if ev.child_id and ev.course_id:
        row=await db.scalar(select(Subscription).where(Subscription.child_id==ev.child_id,Subscription.course_id==ev.course_id).order_by(Subscription.id.desc()))
        # A new provider subscription after cancellation is a new commercial
        # agreement. Never revive the cancelled row: doing so could accidentally
        # restore its grandfathered price/version and overwrite billing history.
        # A provider id may still be attached to a legacy or pending row that did
        # not have one when checkout was first recorded.
        if ev.provider_subscription_id and row is not None:
            existing_provider_id=str(row.provider_subscription_id or '')
            if str(row.status or '').upper() == 'CANCELLED':
                return None
            if existing_provider_id and existing_provider_id != str(ev.provider_subscription_id):
                return None
        return row
    return None

async def apply_normalized_event(db, ev:NormalizedPaymentEvent) -> Subscription|None:
    now=ev.occurred_at or datetime.utcnow()
    status=normalized_status(ev.status)
    sub=await _matching_subscription(db,ev)
    creating = ev.event_type in {'SUBSCRIPTION_CREATED','CHECKOUT_COMPLETED','PAYMENT_SUCCEEDED','SUBSCRIPTION_ACTIVE'}
    if creating and ev.child_id and ev.course_id and sub is None:
        period=normalize_billing_period(ev.billing_period or MONTH)
        price=max(0.0,float(ev.monthly_price or 0.0))
        version_id=str(ev.plan_version_id or f"legacy-{ev.plan_id or 'weekly1'}-{period.lower()}-{str(ev.currency or 'EUR').lower()}-{price:.2f}")
        sub=Subscription(
            child_id=ev.child_id, course_id=ev.course_id,
            plan_id=ev.plan_id or 'weekly1', current_plan_id=ev.plan_id or 'weekly1',
            current_plan_version_id=version_id,current_plan_price=price,billing_period=period,
            provider_plan_id=ev.provider_plan_id or None,
            lessons_per_week=max(1,min(4,int(ev.lessons_per_week or 1))),
            monthly_price=price, currency=str(ev.currency or 'EUR').upper(),
            started_at=now, current_period_start=ev.period_start or now,
            current_period_end=ev.period_end, next_charge_at=ev.period_end,
            provider_subscription_id=ev.provider_subscription_id or None,
            release_baseline_count=await _baseline(db,ev.child_id,ev.course_id), test_mode=False, payment_provider=ev.provider, status='PENDING',
            special_first_year=bool(ev.special_first_year),
            standard_renewal_price=float(ev.standard_renewal_price) if ev.standard_renewal_price else None)
        db.add(sub)
        await db.flush()
    if sub is None:
        return None

    # A locally confirmed cancellation cannot be undone by delayed approval or
    # status events. A genuinely collected payment is reconciled independently.
    if getattr(sub, 'cancel_at_period_end', False) and ev.event_type in {'SUBSCRIPTION_ACTIVE', 'SUBSCRIPTION_CREATED', 'CHECKOUT_COMPLETED', 'SUBSCRIPTION_UPDATED', 'PLAN_CHANGED'}:
        return sub
    if ev.provider == 'paypal' and ev.event_type == 'SUBSCRIPTION_ACTIVE' and ev.charged_amount <= 0:
        return sub

    # Payment snapshots can arrive after a newer payment or after verification.
    # Never move a paid period backwards or reset its already consumed lessons.
    paid_at = ev.period_start or now
    if ev.event_type in {'PAYMENT_SUCCEEDED', 'SUBSCRIPTION_ACTIVE'} and sub.current_period_start and int(sub.lessons_allocated or 0) > 0 and paid_at <= sub.current_period_start:
        return sub
    if ev.event_type in {'PAYMENT_SUCCEEDED', 'SUBSCRIPTION_ACTIVE'} and sub.current_period_end and int(sub.lessons_allocated or 0) > 0 and paid_at < sub.current_period_end:
        return sub
    if ev.intro_week_price > 0 and ev.event_type == 'SUBSCRIPTION_ACTIVE' and ev.charged_amount <= 0:
        return sub
    paid_intro = ev.intro_week_price > 0 and abs(ev.charged_amount - ev.intro_week_price) < 0.01
    if paid_intro and (sub.pending_plan_id or int(sub.lessons_allocated or 0) > 0):
        return sub  # The introductory week belongs only to the initial purchase.
    if paid_intro:
        from app.services.intro_offer import consume
        if not await consume(db, sub, paid_at=paid_at):
            return sub
        ev.period_end = paid_at + timedelta(days=7)
    prior_intro = bool(
        sub.current_period_start and sub.current_period_end
        and 0 < (sub.current_period_end - sub.current_period_start).total_seconds() <= 7 * 86400
    )
    completed_intro = bool(prior_intro and ev.event_type == 'PAYMENT_SUCCEEDED' and not paid_intro and paid_at >= sub.current_period_end)

    old_status=str(sub.status or '')
    new_freq=max(1,min(4,int(ev.lessons_per_week or sub.lessons_per_week or 1)))
    provider_update_event = ev.event_type in {'PLAN_CHANGED','SUBSCRIPTION_UPDATED'}
    had_pending_plan = bool(sub.pending_plan_id)
    provider_update_is_pending = bool(sub.pending_plan_id and provider_update_event)
    cancelling_pending = str(sub.pending_provider_status or '').upper() == 'CANCEL_PENDING_APPROVAL'
    provider_confirmed_cancel = bool(
        provider_update_is_pending and cancelling_pending
        and plan_identity_matches(current_plan_snapshot(sub), plan_id=ev.plan_id,
                                  version_id=ev.plan_version_id, provider_plan_id=ev.provider_plan_id,
                                  billing_period=ev.billing_period)
    )
    if provider_confirmed_cancel:
        owner=await db.get(Child,sub.child_id)
        if owner is not None:cancel_plan_change(db,sub,parent_id=owner.parent_id,now=now)
    elif (
        provider_update_is_pending and not cancelling_pending
        and ev.plan_id == str(sub.pending_plan_id)
        and normalize_billing_period(ev.billing_period) == normalize_billing_period(sub.pending_plan_billing_period)
        and (not ev.plan_version_id or not sub.pending_plan_version_id or ev.plan_version_id == str(sub.pending_plan_version_id))
        and (not ev.provider_plan_id or not sub.pending_provider_plan_id or ev.provider_plan_id == str(sub.pending_provider_plan_id))
    ):
        mark_pending_provider_scheduled(sub, provider_reference=ev.provider_subscription_id or ev.event_id)
        if ev.provider_plan_id:sub.pending_provider_plan_id=ev.provider_plan_id
    payment_activates_pending = bool(had_pending_plan and ev.event_type == 'PAYMENT_SUCCEEDED')
    activated = False
    if payment_activates_pending:
        child = await db.get(Child, sub.child_id)
        if child is None:
            return sub
        previous_period_start = sub.current_period_start
        resource = ev.raw.get('resource') if isinstance(ev.raw.get('resource'), dict) else {}
        sale_amount = resource.get('amount') if isinstance(resource.get('amount'), dict) else {}
        known_pending_snapshot = bool(
            ev.plan_from_subscription_snapshot and ev.provider == sub.payment_provider == 'paypal'
            and ev.provider_subscription_id and ev.provider_subscription_id == sub.provider_subscription_id
            and sub.pending_provider_plan_id and ev.provider_plan_id == sub.pending_provider_plan_id
            and (sale_amount.get('currency') or sale_amount.get('currency_code'))
        )
        activated = activate_pending_after_successful_payment(
            db, sub, parent_id=child.parent_id, paid_at=paid_at,
            charged_plan_id=ev.plan_id, charged_plan_version_id=ev.plan_version_id,
            charged_provider_plan_id=ev.provider_plan_id,
            charged_billing_period=ev.billing_period, charged_currency=ev.currency,
            charged_amount=float(ev.charged_amount or 0.0), period_end=ev.period_end,
            allow_pending_snapshot_for_current_payment=known_pending_snapshot,
        )
        if not activated and sub.current_period_start == previous_period_start:
            return sub
    preserve_tariff = provider_update_event or had_pending_plan or ev.event_type not in {
        'SUBSCRIPTION_CREATED', 'CHECKOUT_COMPLETED', 'PAYMENT_SUCCEEDED', 'SUBSCRIPTION_ACTIVE',
    }
    if ev.plan_id and not preserve_tariff:
        sub.plan_id=ev.plan_id
        sub.current_plan_id=ev.plan_id
    # A confirmed provider update may also represent a course switch on the same
    # recurring subscription. Preserve the subscription id, but move the release
    # baseline to the new course only when the normalized event explicitly names it.
    if not had_pending_plan and ev.course_id and ev.course_id != sub.course_id and ev.event_type in {'PLAN_CHANGED','SUBSCRIPTION_UPDATED','PAYMENT_SUCCEEDED','SUBSCRIPTION_ACTIVE'}:
        sub.course_id=ev.course_id
        sub.release_baseline_count=await _baseline(db,sub.child_id,sub.course_id)
        sub.started_at=datetime.utcnow()
    if not preserve_tariff:
        sub.lessons_per_week=new_freq
        if ev.monthly_price > 0:
            sub.monthly_price=float(ev.monthly_price);sub.current_plan_price=float(ev.monthly_price)
        if ev.currency: sub.currency=ev.currency.upper()
        if ev.plan_version_id:sub.current_plan_version_id=ev.plan_version_id
        if ev.billing_period:sub.billing_period=normalize_billing_period(ev.billing_period)
        if ev.provider_plan_id:sub.provider_plan_id=ev.provider_plan_id
    if not sub.current_plan_version_id:
        price=float(sub.current_plan_price if sub.current_plan_price is not None else sub.monthly_price or 0.0)
        sub.current_plan_version_id=f"legacy-{sub.current_plan_id or sub.plan_id or 'weekly1'}-{str(sub.billing_period or MONTH).lower()}-{str(sub.currency or 'EUR').lower()}-{price:.2f}"
    if sub.current_plan_price is None:sub.current_plan_price=float(sub.monthly_price or 0.0)
    if ev.provider_subscription_id: sub.provider_subscription_id=ev.provider_subscription_id
    sub.payment_provider=ev.provider
    sub.test_mode=False
    if ev.special_first_year and not preserve_tariff:
        sub.special_first_year = True
        if ev.standard_renewal_price:
            sub.standard_renewal_price = float(ev.standard_renewal_price)
    elif not preserve_tariff and ev.event_type == 'PAYMENT_SUCCEEDED' and not paid_intro and normalize_billing_period(sub.billing_period or MONTH) == 'YEAR' and sub.current_period_start and ev.period_start and ev.period_start > sub.current_period_start:
        sub.special_first_year = False

    if ev.event_type in {'PAYMENT_FAILED'} or status=='PAST_DUE':
        if not (had_pending_plan and old_status == 'ACTIVE' and sub.current_period_end and now < sub.current_period_end):
            sub.status='PAST_DUE'
    elif ev.event_type in {'SUBSCRIPTION_CANCELLED','SUBSCRIPTION_PAUSED'} or status=='CANCELLED':
        sub.cancel_at_period_end = True
        sub.renewal_cancelled_at = sub.renewal_cancelled_at or datetime.utcnow()
        sub.next_charge_at = None
        paid_access = bool(sub.current_period_end and sub.current_period_end > now and int(sub.lessons_allocated or 0) > 0)
        sub.status = 'ACTIVE' if paid_access else 'CANCELLED'
        sub.cancelled_at = sub.cancelled_at or datetime.utcnow()
    elif ev.event_type in {'PAYMENT_SUCCEEDED','SUBSCRIPTION_ACTIVE'} or status=='ACTIVE':
        if not had_pending_plan or ev.event_type == 'PAYMENT_SUCCEEDED':
            if old_status!='ACTIVE':
                sub.release_baseline_count=await _baseline(db,sub.child_id,sub.course_id)
                sub.started_at=now
            sub.status='ACTIVE'; sub.cancelled_at=None
    elif ev.event_type in {'SUBSCRIPTION_CREATED','CHECKOUT_COMPLETED','SUBSCRIPTION_UPDATED','PLAN_CHANGED'}:
        # Creation/checkout/update alone must never unlock paid content unless the provider
        # explicitly reports ACTIVE/paid. This prevents premature access on pending approval.
        if sub.status not in {'ACTIVE','PAST_DUE','CANCELLED'}:
            sub.status='PENDING'

    if ev.event_type == 'PAYMENT_SUCCEEDED':
        paid_at=ev.period_start or now
        if not payment_activates_pending:
            record_successful_billing_period(sub,period_start=paid_at,period_end=ev.period_end)
        if activated or completed_intro:
            sub.release_baseline_count=await _baseline(db,sub.child_id,sub.course_id)
            sub.started_at=paid_at
    elif ev.event_type == 'SUBSCRIPTION_ACTIVE' and paid_intro and old_status != 'ACTIVE':
        record_successful_billing_period(sub, period_start=paid_at, period_end=ev.period_end)

    if getattr(sub, 'cancel_at_period_end', False):
        sub.next_charge_at = None

    access_source=ev.provider.upper()
    ref=ev.provider_subscription_id or ev.event_id
    enroll=await db.scalar(select(CourseEnrollment).where(
        CourseEnrollment.child_id==sub.child_id,
        CourseEnrollment.course_id==sub.course_id,
        CourseEnrollment.access_source==access_source,
        CourseEnrollment.payment_reference==ref).order_by(CourseEnrollment.id.desc()))
    if enroll is None and sub.status=='ACTIVE':
        enroll=CourseEnrollment(child_id=sub.child_id,course_id=sub.course_id,status='ACTIVE',access_source=access_source,payment_reference=ref)
        db.add(enroll)
    elif enroll:
        enroll.status='ACTIVE' if sub.status=='ACTIVE' else ('CANCELLED' if sub.status=='CANCELLED' else enroll.status)
    return sub
