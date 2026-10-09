from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta

import pytest
from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, Child, Parent, Subscription, SubscriptionAuditEvent
from app.services import paypal_adapter, platform_settings
from app.services.payment_lifecycle import NormalizedPaymentEvent, apply_normalized_event
from app.services.pricing_versions import ensure_versioned_pricing_config
from app.services.subscription_plan_changes import (
    PLAN_CHANGE_ACTIVATED,
    cancel_plan_change,
    current_plan_snapshot,
    next_billing_period_start,
    preview_plan_change,
    renewal_charge_for,
    schedule_plan_change,
)


@pytest.fixture(autouse=True)
def isolated_pricing(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_settings, "SETTINGS_DIR", tmp_path / "settings")
    platform_settings.save_settings("pricing", deepcopy(platform_settings.DEFAULT_PRICING))
    ensure_versioned_pricing_config()


@asynccontextmanager
async def paid_subscription(period="MONTH", frequency=1, intro=False):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            parent = Parent(email="deferred@example.test")
            db.add(parent)
            await db.flush()
            child = Child(parent_id=parent.id, display_name="Child")
            db.add(child)
            await db.flush()
            start = datetime(2026, 10, 7, 12)
            end = start + (timedelta(days=7) if intro else relativedelta(years=1) if period == "YEAR" else relativedelta(months=1))
            sub = Subscription(
                child_id=child.id, course_id="conversation", status="ACTIVE", test_mode=False,
                plan_id=f"weekly{frequency}", current_plan_id=f"weekly{frequency}",
                current_plan_version_id="locked-original", provider_plan_id="P-ORIGINAL",
                billing_period=period, lessons_per_week=frequency, currency="EUR",
                monthly_price=400 if period == "YEAR" else 40,
                current_plan_price=400 if period == "YEAR" else 40,
                payment_provider="paypal", provider_subscription_id="I-DEFERRED",
                started_at=start, current_period_start=start, current_period_end=end, next_charge_at=end,
                lessons_allocated=frequency * (1 if intro else 52 if period == "YEAR" else 4),
                lessons_used=1, release_baseline_count=3,
            )
            db.add(sub)
            await db.flush()
            yield db, parent, sub, start, end
    finally:
        await engine.dispose()


async def schedule(db, parent, sub, start, frequency=2, period="MONTH"):
    preview = await preview_plan_change(
        db, sub, parent_id=parent.id, requested_plan_id=f"weekly{frequency}",
        requested_billing_period=period, now=start + timedelta(days=1),
    )
    schedule_plan_change(db, sub, parent_id=parent.id, preview=preview,
                         provider_status="APPROVAL_REQUIRED", provider_plan_id="P-REQUESTED", now=start + timedelta(days=1))
    return preview


def event_for(sub, requested, at, *, event_type="PAYMENT_SUCCEEDED", **overrides):
    values = dict(
        provider="paypal", event_id=f"{event_type}-{at.isoformat()}", event_type=event_type,
        status="PAST_DUE" if event_type == "PAYMENT_FAILED" else "ACTIVE",
        child_id=sub.child_id, course_id=sub.course_id, plan_id=requested.plan_id,
        plan_version_id=requested.version_id, provider_plan_id="P-REQUESTED",
        billing_period=requested.billing_period, lessons_per_week=requested.lessons_per_week,
        monthly_price=requested.price, charged_amount=requested.price, currency=requested.currency,
        provider_subscription_id="I-DEFERRED", occurred_at=at, period_start=at,
        period_end=at + (relativedelta(years=1) if requested.billing_period == "YEAR" else relativedelta(months=1)),
    )
    values.update(overrides)
    return NormalizedPaymentEvent(**values)


def paid_state(sub):
    return (current_plan_snapshot(sub), sub.current_period_start, sub.current_period_end,
            sub.next_charge_at, sub.lessons_allocated, sub.lessons_used,
            sub.started_at, sub.release_baseline_count, sub.special_first_year, sub.standard_renewal_price)


@pytest.mark.parametrize("current_period,intro", [("MONTH", True), ("YEAR", True), ("MONTH", False), ("YEAR", False)])
@pytest.mark.parametrize("target_period", ["MONTH", "YEAR"])
@pytest.mark.parametrize("current_frequency,target_frequency", [(1, 4), (4, 1)])
@pytest.mark.asyncio
async def test_change_preserves_paid_week_month_or_year_until_confirmed_next_payment(
    current_period, intro, target_period, current_frequency, target_frequency,
):
    async with paid_subscription(current_period, current_frequency, intro) as (db, parent, sub, start, end):
        before = paid_state(sub)
        preview = await schedule(db, parent, sub, start, target_frequency, target_period)
        assert preview.effective_at == end
        assert next_billing_period_start(sub, now=end) == end
        assert paid_state(sub) == before
        assert not renewal_charge_for(sub, now=end - timedelta(seconds=1)).activates_pending
        assert renewal_charge_for(sub, now=end).activates_pending

        for event_type in ("SUBSCRIPTION_UPDATED", "SUBSCRIPTION_ACTIVE", "PAYMENT_FAILED", "PAYMENT_SUCCEEDED"):
            await apply_normalized_event(db, event_for(sub, preview.requested, start + timedelta(days=2), event_type=event_type))
            assert paid_state(sub) == before
            assert sub.status == "ACTIVE"
            assert sub.pending_plan_id == preview.requested.plan_id and sub.pending_plan_effective_at == end

        await apply_normalized_event(db, event_for(sub, preview.requested, end, event_type="PAYMENT_FAILED"))
        assert sub.status == "PAST_DUE" and paid_state(sub) == before
        # Approval alone cannot recover access after an unpaid renewal.
        await apply_normalized_event(db, event_for(sub, preview.requested, end, event_type="SUBSCRIPTION_ACTIVE"))
        assert sub.status == "PAST_DUE" and paid_state(sub) == before

        payment = event_for(sub, preview.requested, end)
        await apply_normalized_event(db, payment)
        await db.flush()
        assert sub.status == "ACTIVE" and sub.pending_plan_id is None
        assert (sub.current_plan_id, sub.current_plan_version_id, sub.current_plan_price) == (
            preview.requested.plan_id, preview.requested.version_id, preview.requested.price,
        )
        assert sub.billing_period == target_period and sub.lessons_per_week == target_frequency
        assert sub.current_period_start == sub.started_at == end
        assert sub.current_period_end == sub.next_charge_at == payment.period_end
        assert sub.lessons_allocated == target_frequency * (52 if target_period == "YEAR" else 4)
        assert sub.lessons_used == 0
        assert len((await db.scalars(select(SubscriptionAuditEvent).where(SubscriptionAuditEvent.event_type == PLAN_CHANGE_ACTIVATED))).all()) == 1
        sub.lessons_used = 1
        after = paid_state(sub)
        await apply_normalized_event(db, payment)
        assert paid_state(sub) == after


@pytest.mark.parametrize("current_period,target_period", [("MONTH", "YEAR"), ("YEAR", "MONTH")])
@pytest.mark.asyncio
async def test_interval_only_change_requires_new_interval_payment(current_period, target_period):
    async with paid_subscription(current_period, 2) as (db, parent, sub, start, end):
        preview = await schedule(db, parent, sub, start, 2, target_period)
        original = paid_state(sub)
        await apply_normalized_event(db, event_for(sub, preview.requested, end, billing_period=current_period))
        assert paid_state(sub) == original and sub.pending_plan_id == "weekly2"
        await apply_normalized_event(db, event_for(sub, preview.requested, end))
        assert sub.billing_period == target_period and sub.pending_plan_id is None


@pytest.mark.parametrize("invalid", [
    {"plan_id": "", "plan_version_id": "", "provider_plan_id": ""},
    {"plan_id": "weekly4"}, {"plan_version_id": "wrong-version"},
    {"provider_plan_id": "P-UNRELATED"}, {"charged_amount": 0},
    {"charged_amount": 0.01}, {"currency": "USD"}, {"billing_period": "YEAR"},
    {"short_period": True},
])
@pytest.mark.asyncio
async def test_incomplete_or_mismatched_payment_keeps_pending_and_current_period(invalid):
    async with paid_subscription() as (db, parent, sub, start, end):
        preview = await schedule(db, parent, sub, start)
        sub.status = "PAST_DUE"
        before = paid_state(sub)
        overrides = dict(invalid)
        if overrides.pop("short_period", False):
            overrides["period_end"] = end + timedelta(days=7)
        await apply_normalized_event(db, event_for(sub, preview.requested, end, **overrides))
        assert paid_state(sub) == before and sub.status == "PAST_DUE"
        assert sub.pending_plan_effective_at == end and sub.pending_plan_id == "weekly2"
        assert await db.scalar(select(SubscriptionAuditEvent).where(SubscriptionAuditEvent.event_type == PLAN_CHANGE_ACTIVATED)) is None


@pytest.mark.asyncio
async def test_paid_old_plan_renews_old_access_and_defers_request_again():
    async with paid_subscription() as (db, parent, sub, start, end):
        current = current_plan_snapshot(sub)
        preview = await schedule(db, parent, sub, start, period="YEAR")
        old_end = end + relativedelta(months=1)
        await apply_normalized_event(db, event_for(
            sub, current, end, provider_plan_id="P-ORIGINAL", period_end=old_end,
        ))
        assert current_plan_snapshot(sub) == current and sub.lessons_allocated == 4
        assert sub.pending_plan_effective_at == old_end and sub.pending_plan_id == preview.requested.plan_id
        assert sub.current_period_start == end and sub.current_period_end == old_end


@pytest.mark.asyncio
async def test_special_year_renews_at_standard_price_before_pending_change():
    async with paid_subscription("YEAR", 2) as (db, parent, sub, start, end):
        sub.current_plan_price = sub.monthly_price = 599
        sub.special_first_year = True
        sub.standard_renewal_price = 759
        current = current_plan_snapshot(sub)
        preview = await schedule(db, parent, sub, start, 1, "MONTH")
        renewed_end = end + relativedelta(years=1)
        await apply_normalized_event(db, event_for(
            sub, current, end, provider_plan_id="P-ORIGINAL", period_end=renewed_end,
            charged_amount=759, monthly_price=759,
        ))
        assert sub.current_plan_id == current.plan_id and sub.provider_plan_id == "P-ORIGINAL"
        assert sub.current_plan_version_id == current.version_id and sub.billing_period == "YEAR"
        assert sub.current_plan_price == sub.monthly_price == 759 and not sub.special_first_year
        assert sub.current_period_start == end and sub.current_period_end == sub.next_charge_at == renewed_end
        assert sub.pending_plan_effective_at == renewed_end and sub.pending_plan_id == preview.requested.plan_id
        assert sub.lessons_allocated == 104
        sub.lessons_used = 1
        paid_year = paid_state(sub)
        await apply_normalized_event(db, event_for(sub, preview.requested, end + timedelta(days=1)))
        assert paid_state(sub) == paid_year and sub.pending_plan_effective_at == renewed_end
        assert await db.scalar(select(SubscriptionAuditEvent).where(SubscriptionAuditEvent.event_type == PLAN_CHANGE_ACTIVATED)) is None


def delayed_paypal_payment(monkeypatch, sub, requested, at, amount, *, event_type="PAYMENT.SALE.COMPLETED",
                           snapshot_plan_id="P-REQUESTED", agreement_id="I-DEFERRED", sale_plan_id="", currency="EUR"):
    metadata = dict(plan_id=requested.plan_id, plan_version_id=requested.version_id,
                    provider_plan_id=snapshot_plan_id, lessons_per_week=requested.lessons_per_week,
                    billing_period=requested.billing_period, monthly_price=requested.price, currency="EUR")
    monkeypatch.setattr(paypal_adapter, "_meta_from_provider_plan", lambda _plan: metadata)
    snapshot = {
        "id": agreement_id, "plan_id": snapshot_plan_id, "status": "ACTIVE",
        "custom_id": paypal_adapter._custom_id(sub.child_id, sub.course_id, sub.current_plan_id,
                                               sub.current_plan_version_id, sub.lessons_per_week,
                                               sub.current_plan_price, sub.billing_period),
        "billing_info": {
            "last_payment": {"time": at.isoformat() + "Z", "amount": {"value": str(amount), "currency_code": currency}},
            # This fetched snapshot now describes the requested interval.
            "next_billing_time": (at + (relativedelta(years=1) if requested.billing_period == "YEAR" else relativedelta(months=1))).isoformat() + "Z",
        },
    }
    resource = {"billing_agreement_id": agreement_id, "create_time": at.isoformat() + "Z",
                "amount": {"value": str(amount), "currency_code": currency}}
    if sale_plan_id:
        resource["plan_id"] = sale_plan_id
    return paypal_adapter.normalize_paypal_event({
        "id": "delayed-paypal-payment", "event_type": event_type,
        "create_time": (at + timedelta(minutes=5)).isoformat() + "Z", "resource": resource,
    }, snapshot)


@pytest.mark.parametrize("current_period,intro,special,target_period", [
    ("MONTH", False, False, "YEAR"), ("YEAR", False, False, "MONTH"),
    ("MONTH", True, False, "YEAR"), ("YEAR", True, True, "MONTH"),
    ("YEAR", False, True, "MONTH"), ("YEAR", False, True, "YEAR"),
])
@pytest.mark.asyncio
async def test_old_payment_with_revised_paypal_snapshot_keeps_old_paid_period(
    monkeypatch, current_period, intro, special, target_period,
):
    async with paid_subscription(current_period, 2, intro) as (db, parent, sub, start, end):
        if special:
            sub.current_plan_price = sub.monthly_price = 599
            sub.special_first_year = True
            sub.standard_renewal_price = 759
        current = current_plan_snapshot(sub)
        charged = 759 if special and not intro else current.price
        preview = await schedule(db, parent, sub, start, 1, target_period)
        delayed = delayed_paypal_payment(monkeypatch, sub, preview.requested, end, charged)
        assert delayed.plan_id == preview.requested.plan_id and delayed.provider_plan_id == "P-REQUESTED"
        assert delayed.charged_amount == charged
        await apply_normalized_event(db, delayed)
        old_period_end = end + (relativedelta(years=1) if current_period == "YEAR" else relativedelta(months=1))
        assert sub.current_plan_id == current.plan_id and sub.provider_plan_id == "P-ORIGINAL"
        assert sub.current_plan_version_id == current.version_id and sub.billing_period == current_period
        assert sub.current_plan_price == sub.monthly_price == charged
        assert sub.special_first_year is (special and intro)
        assert sub.current_period_start == end and sub.current_period_end == sub.next_charge_at == old_period_end
        assert sub.pending_plan_effective_at == old_period_end and sub.pending_plan_id == preview.requested.plan_id
        assert sub.lessons_allocated == 2 * (52 if current_period == "YEAR" else 4)
        sub.lessons_used = 1
        paid = paid_state(sub)
        await apply_normalized_event(db, delayed)
        await apply_normalized_event(db, event_for(sub, preview.requested, end + timedelta(days=1)))
        assert paid_state(sub) == paid and sub.pending_plan_effective_at == old_period_end
        assert await db.scalar(select(SubscriptionAuditEvent).where(SubscriptionAuditEvent.event_type == PLAN_CHANGE_ACTIVATED)) is None


@pytest.mark.parametrize("invalid", [
    {"snapshot_plan_id": "P-UNKNOWN"}, {"agreement_id": "I-UNKNOWN"}, {"agreement_id": ""},
    {"sale_plan_id": "P-REQUESTED"}, {"currency": "USD"}, {"currency": ""},
    {"event_type": "BILLING.SUBSCRIPTION.UPDATED"},
    {"event_type": "BILLING.SUBSCRIPTION.ACTIVATED"},
    {"amount": 0}, {"amount": 0.01},
])
@pytest.mark.asyncio
async def test_revised_paypal_snapshot_does_not_assume_unproven_old_payment(monkeypatch, invalid):
    async with paid_subscription("YEAR", 2) as (db, parent, sub, start, end):
        preview = await schedule(db, parent, sub, start, 1, "MONTH")
        before = paid_state(sub)
        values = {"amount": sub.current_plan_price, **invalid}
        delayed = delayed_paypal_payment(monkeypatch, sub, preview.requested, end, **values)
        await apply_normalized_event(db, delayed)
        assert paid_state(sub) == before
        assert sub.pending_plan_effective_at == end and sub.pending_plan_id == preview.requested.plan_id
        assert await db.scalar(select(SubscriptionAuditEvent).where(SubscriptionAuditEvent.event_type == PLAN_CHANGE_ACTIVATED)) is None


@pytest.mark.asyncio
async def test_replacement_and_cancellation_keep_exact_paid_boundary():
    async with paid_subscription("YEAR", intro=True) as (db, parent, sub, start, end):
        before = paid_state(sub)
        await schedule(db, parent, sub, start, 4, "YEAR")
        preview = await preview_plan_change(db, sub, parent_id=parent.id, requested_plan_id="weekly2", requested_billing_period="MONTH", now=end)
        assert preview.effective_at == end
        schedule_plan_change(db, sub, parent_id=parent.id, preview=preview, provider_status="SCHEDULED", now=end)
        assert sub.pending_plan_id == "weekly2" and sub.pending_plan_billing_period == "MONTH"
        assert paid_state(sub) == before
        cancel_plan_change(db, sub, parent_id=parent.id, now=end)
        assert paid_state(sub) == before and sub.pending_plan_id is None


@pytest.mark.asyncio
async def test_interval_only_cancel_waits_for_original_provider_identity():
    async with paid_subscription("YEAR", 2) as (db, parent, sub, start, end):
        current = current_plan_snapshot(sub)
        preview = await schedule(db, parent, sub, start, 2, "MONTH")
        sub.pending_provider_status = "CANCEL_PENDING_APPROVAL"
        await apply_normalized_event(db, event_for(sub, preview.requested, start + timedelta(days=2), event_type="SUBSCRIPTION_UPDATED"))
        assert sub.pending_plan_id == "weekly2" and sub.pending_provider_status == "CANCEL_PENDING_APPROVAL"
        await apply_normalized_event(db, event_for(sub, current, start + timedelta(days=2), event_type="SUBSCRIPTION_UPDATED", provider_plan_id="P-ORIGINAL"))
        assert sub.pending_plan_id is None and current_plan_snapshot(sub) == current


@pytest.mark.asyncio
async def test_revision_does_not_purchase_another_intro_week():
    async with paid_subscription("YEAR", 1, intro=True) as (db, parent, sub, start, end):
        sub.special_first_year = True
        sub.standard_renewal_price = 429
        preview = await schedule(db, parent, sub, start, 2, "YEAR")
        before = paid_state(sub)
        await apply_normalized_event(db, event_for(sub, preview.requested, end, charged_amount=6, intro_week_price=6))
        assert paid_state(sub) == before and sub.pending_plan_id == "weekly2"
        await apply_normalized_event(db, event_for(sub, preview.requested, end))
        assert not sub.special_first_year and sub.standard_renewal_price is None
        assert sub.lessons_allocated == 104 and sub.current_period_end == end + relativedelta(years=1)
        after = paid_state(sub)
        await apply_normalized_event(db, event_for(sub, preview.requested, sub.current_period_end, charged_amount=6, intro_week_price=6))
        assert paid_state(sub) == after
