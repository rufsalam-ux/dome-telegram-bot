from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from sqlalchemy import select
from app.db.models import CourseEnrollment, Subscription, LessonEntitlement

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
    lessons_per_week: int = 1
    monthly_price: float = 0.0
    currency: str = 'EUR'
    provider_subscription_id: str = ''
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
        return await db.scalar(select(Subscription).where(Subscription.child_id==ev.child_id,Subscription.course_id==ev.course_id).order_by(Subscription.id.desc()))
    return None

async def apply_normalized_event(db, ev:NormalizedPaymentEvent) -> Subscription|None:
    status=normalized_status(ev.status)
    sub=await _matching_subscription(db,ev)
    creating = ev.event_type in {'SUBSCRIPTION_CREATED','CHECKOUT_COMPLETED','PAYMENT_SUCCEEDED','SUBSCRIPTION_ACTIVE'}
    if creating and ev.child_id and ev.course_id and sub is None:
        sub=Subscription(
            child_id=ev.child_id, course_id=ev.course_id,
            started_at=datetime.utcnow(), provider_subscription_id=ev.provider_subscription_id or None,
            release_baseline_count=await _baseline(db,ev.child_id,ev.course_id), test_mode=False, payment_provider=ev.provider, status='PENDING')
        db.add(sub)
        await db.flush()
    if sub is None:
        return None

    old_status=str(sub.status or '')
    old_freq=int(sub.lessons_per_week or 1)
    new_freq=max(1,min(4,int(ev.lessons_per_week or old_freq)))
    if ev.event_type in {'PLAN_CHANGED','SUBSCRIPTION_UPDATED'} and (new_freq!=old_freq or (status=='ACTIVE' and old_status!='ACTIVE')):
        sub.release_baseline_count=await _baseline(db,sub.child_id,sub.course_id)
        sub.started_at=datetime.utcnow()

    if ev.plan_id: sub.plan_id=ev.plan_id
    # A confirmed provider update may also represent a course switch on the same
    # recurring subscription. Preserve the subscription id, but move the release
    # baseline to the new course only when the normalized event explicitly names it.
    if ev.course_id and ev.course_id != sub.course_id and ev.event_type in {'PLAN_CHANGED','SUBSCRIPTION_UPDATED','PAYMENT_SUCCEEDED','SUBSCRIPTION_ACTIVE'}:
        sub.course_id=ev.course_id
        sub.release_baseline_count=await _baseline(db,sub.child_id,sub.course_id)
        sub.started_at=datetime.utcnow()
    sub.lessons_per_week=new_freq
    if ev.monthly_price > 0: sub.monthly_price=float(ev.monthly_price)
    if ev.currency: sub.currency=ev.currency.upper()
    if ev.provider_subscription_id: sub.provider_subscription_id=ev.provider_subscription_id
    sub.payment_provider=ev.provider
    sub.test_mode=False

    if ev.event_type in {'PAYMENT_FAILED'} or status=='PAST_DUE':
        sub.status='PAST_DUE'
    elif ev.event_type in {'SUBSCRIPTION_CANCELLED','SUBSCRIPTION_PAUSED'} or status=='CANCELLED':
        sub.status='CANCELLED'; sub.cancelled_at=datetime.utcnow()
    elif ev.event_type in {'PAYMENT_SUCCEEDED','SUBSCRIPTION_ACTIVE'} or status=='ACTIVE':
        if old_status!='ACTIVE':
            sub.release_baseline_count=await _baseline(db,sub.child_id,sub.course_id)
            sub.started_at=datetime.utcnow()
        sub.status='ACTIVE'; sub.cancelled_at=None
    elif ev.event_type in {'SUBSCRIPTION_CREATED','CHECKOUT_COMPLETED','SUBSCRIPTION_UPDATED','PLAN_CHANGED'}:
        # Creation/checkout/update alone must never unlock paid content unless the provider
        # explicitly reports ACTIVE/paid. This prevents premature access on pending approval.
        if sub.status not in {'ACTIVE','PAST_DUE','CANCELLED'}:
            sub.status='PENDING'

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
