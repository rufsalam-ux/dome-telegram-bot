from copy import deepcopy
from datetime import datetime
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Base, Child, CourseEnrollment, Parent, PaymentWebhookEvent, Subscription
from app.services import paypal_adapter, platform_settings
from app.services.payment_lifecycle import NormalizedPaymentEvent, apply_normalized_event
from app.services.payment_provider import PayPalPaymentProvider, get_payment_provider
from app.services.pricing_versions import (
    MONTH,
    YEAR,
    ensure_versioned_pricing_config,
    plan_versions_for_course,
)
from app.webapp.server import _reserve_payment_event


def isolated_pricing(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_settings, "SETTINGS_DIR", tmp_path / "platform-settings")
    platform_settings.save_settings("pricing", deepcopy(platform_settings.DEFAULT_PRICING))
    return ensure_versioned_pricing_config()


async def make_test_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_paypal_all_four_plans_catalog(monkeypatch, tmp_path):
    """Verify that all 4 plans (weekly 1, 2, 3, 4) exist with canonical prices for both MONTH and YEAR."""
    isolated_pricing(monkeypatch, tmp_path)
    monthly = plan_versions_for_course(None, MONTH)
    annual = plan_versions_for_course(None, YEAR)

    assert len(monthly) == 4
    assert len(annual) == 4

    expected_monthly = [
        {"plan_id": "weekly1", "lessons_per_week": 1, "price": 39.0},
        {"plan_id": "weekly2", "lessons_per_week": 2, "price": 69.0},
        {"plan_id": "weekly3", "lessons_per_week": 3, "price": 99.0},
        {"plan_id": "weekly4", "lessons_per_week": 4, "price": 139.0},
    ]
    for row, exp in zip(monthly, expected_monthly):
        assert row["plan_id"] == exp["plan_id"]
        assert row["lessons_per_week"] == exp["lessons_per_week"]
        assert row["price"] == exp["price"]

    expected_annual = [
        {"plan_id": "weekly1", "lessons_per_week": 1, "price": 439.0},
        {"plan_id": "weekly2", "lessons_per_week": 2, "price": 759.0},
        {"plan_id": "weekly3", "lessons_per_week": 3, "price": 1089.0},
        {"plan_id": "weekly4", "lessons_per_week": 4, "price": 1535.0},
    ]
    for row, exp in zip(annual, expected_annual):
        assert row["plan_id"] == exp["plan_id"]
        assert row["lessons_per_week"] == exp["lessons_per_week"]
        assert row["price"] == exp["price"]


def test_paypal_custom_id_serialization_and_parsing():
    """Verify PayPal custom_id format (dome2) carries child_id, course_id, plan_id, freq, price, billing_period."""
    for freq, price, period in [(1, 39.0, MONTH), (2, 759.0, YEAR), (3, 99.0, MONTH), (4, 1536.0, YEAR)]:
        cid = paypal_adapter._custom_id(
            child_id=42,
            course_id="conversation",
            plan_id=f"weekly{freq}",
            plan_version_id="v1",
            freq=freq,
            price=price,
            billing_period=period,
        )
        assert cid.startswith("dome2|42|conversation|")
        parsed = paypal_adapter._parse_custom_id(cid)
        assert parsed["child_id"] == 42
        assert parsed["course_id"] == "conversation"
        assert parsed["plan_id"] == f"weekly{freq}"
        assert parsed["lessons_per_week"] == freq
        assert parsed["monthly_price"] == price
        assert parsed["billing_period"] == period


def test_paypal_event_normalization_all_lifecycle_types():
    """Verify normalization of PayPal webhook events across all lifecycle states."""
    custom = "dome2|10|reading|weekly2|v2|2|69.00|MONTH"

    # 1. Activated
    ev_act = paypal_adapter.normalize_paypal_event({
        "id": "evt_act_1",
        "event_type": "BILLING.SUBSCRIPTION.ACTIVATED",
        "create_time": "2026-03-01T12:00:00Z",
        "resource": {"id": "I-SUB123", "status": "ACTIVE", "custom_id": custom},
    })
    assert ev_act.provider == "paypal"
    assert ev_act.event_type == "SUBSCRIPTION_ACTIVE"
    assert ev_act.status == "ACTIVE"
    assert ev_act.child_id == 10
    assert ev_act.course_id == "reading"
    assert ev_act.lessons_per_week == 2
    assert ev_act.monthly_price == 69.0
    assert ev_act.provider_subscription_id == "I-SUB123"

    # 2. Payment Succeeded (PAYMENT.SALE.COMPLETED)
    ev_paid = paypal_adapter.normalize_paypal_event({
        "id": "evt_paid_1",
        "event_type": "PAYMENT.SALE.COMPLETED",
        "create_time": "2026-03-01T12:05:00Z",
        "resource": {
            "id": "TX-1",
            "billing_agreement_id": "I-SUB123",
            "amount": {"total": "69.00", "currency": "EUR"},
            "custom_id": custom,
        },
    })
    assert ev_paid.provider == "paypal"
    assert ev_paid.event_type == "PAYMENT_SUCCEEDED"
    assert ev_paid.status == "ACTIVE"
    assert ev_paid.charged_amount == 69.0

    # 3. Payment Failed
    ev_fail = paypal_adapter.normalize_paypal_event({
        "id": "evt_fail_1",
        "event_type": "BILLING.SUBSCRIPTION.PAYMENT.FAILED",
        "resource": {"id": "I-SUB123", "custom_id": custom},
    })
    assert ev_fail.event_type == "PAYMENT_FAILED"
    assert ev_fail.status == "PAST_DUE"

    # 4. Cancelled
    ev_cancel = paypal_adapter.normalize_paypal_event({
        "id": "evt_cancel_1",
        "event_type": "BILLING.SUBSCRIPTION.CANCELLED",
        "resource": {"id": "I-SUB123", "custom_id": custom},
    })
    assert ev_cancel.event_type == "SUBSCRIPTION_CANCELLED"
    assert ev_cancel.status == "CANCELLED"

    # 5. Expired
    ev_exp = paypal_adapter.normalize_paypal_event({
        "id": "evt_exp_1",
        "event_type": "BILLING.SUBSCRIPTION.EXPIRED",
        "resource": {"id": "I-SUB123", "custom_id": custom},
    })
    assert ev_exp.event_type == "SUBSCRIPTION_CANCELLED"
    assert ev_exp.status == "CANCELLED"


@pytest.mark.asyncio
async def test_paypal_full_subscription_lifecycle_in_db():
    """Verify the complete database lifecycle: creation (pending) -> payment (active) -> payment failed (past due) -> recovery payment (active) -> cancellation."""
    engine, session_factory = await make_test_db()

    async with session_factory() as db:
        parent = Parent(id=1, email="student@example.com", display_name="Test Parent", account_status="ACTIVE")
        child = Child(id=5, parent_id=1, display_name="Alex", age_years=6, target_language="ru", native_language="en")
        db.add_all([parent, child])
        await db.commit()

        # STEP 1: SUBSCRIPTION_CREATED - user reached PayPal approval, pending approval
        ev_created = NormalizedPaymentEvent(
            provider="paypal",
            event_id="evt_create",
            event_type="SUBSCRIPTION_CREATED",
            status="APPROVAL_PENDING",
            child_id=5,
            course_id="conversation",
            plan_id="weekly3",
            lessons_per_week=3,
            monthly_price=99.0,
            currency="EUR",
            provider_subscription_id="I-SUB-E2E",
        )
        sub = await apply_normalized_event(db, ev_created)
        await db.commit()

        assert sub is not None
        assert sub.status == "PENDING", "Pending checkout must NEVER unlock content before payment"
        assert sub.lessons_per_week == 3
        assert sub.monthly_price == 99.0

        # Verify no active course enrollment yet
        enroll = await db.scalar(select(CourseEnrollment).where(CourseEnrollment.child_id == 5, CourseEnrollment.status == "ACTIVE"))
        assert enroll is None

        # STEP 2: PAYMENT_SUCCEEDED - buyer pays and PayPal sends webhook
        ev_paid = NormalizedPaymentEvent(
            provider="paypal",
            event_id="evt_paid_1",
            event_type="PAYMENT_SUCCEEDED",
            status="ACTIVE",
            child_id=5,
            course_id="conversation",
            plan_id="weekly3",
            lessons_per_week=3,
            monthly_price=99.0,
            charged_amount=99.0,
            currency="EUR",
            provider_subscription_id="I-SUB-E2E",
        )
        sub = await apply_normalized_event(db, ev_paid)
        await db.commit()

        assert sub.status == "ACTIVE"
        enroll = await db.scalar(select(CourseEnrollment).where(CourseEnrollment.child_id == 5, CourseEnrollment.status == "ACTIVE"))
        assert enroll is not None
        assert enroll.access_source == "PAYPAL"

        # STEP 3: PAYMENT_FAILED - monthly renewal attempt fails
        ev_failed = NormalizedPaymentEvent(
            provider="paypal",
            event_id="evt_fail_1",
            event_type="PAYMENT_FAILED",
            status="PAST_DUE",
            child_id=5,
            course_id="conversation",
            provider_subscription_id="I-SUB-E2E",
        )
        sub = await apply_normalized_event(db, ev_failed)
        await db.commit()

        assert sub.status == "PAST_DUE"

        # STEP 4: PAYMENT_SUCCEEDED - renewal retry succeeds
        ev_retry = NormalizedPaymentEvent(
            provider="paypal",
            event_id="evt_retry_1",
            event_type="PAYMENT_SUCCEEDED",
            status="ACTIVE",
            child_id=5,
            course_id="conversation",
            provider_subscription_id="I-SUB-E2E",
        )
        sub = await apply_normalized_event(db, ev_retry)
        await db.commit()

        assert sub.status == "ACTIVE"

        # STEP 5: SUBSCRIPTION_CANCELLED - buyer cancels subscription
        ev_cancel = NormalizedPaymentEvent(
            provider="paypal",
            event_id="evt_cancel_1",
            event_type="SUBSCRIPTION_CANCELLED",
            status="CANCELLED",
            child_id=5,
            course_id="conversation",
            provider_subscription_id="I-SUB-E2E",
        )
        sub = await apply_normalized_event(db, ev_cancel)
        await db.commit()

        assert sub.status == "CANCELLED"
        assert sub.cancelled_at is not None
        enroll = await db.scalar(select(CourseEnrollment).where(CourseEnrollment.child_id == 5, CourseEnrollment.payment_reference == "I-SUB-E2E"))
        assert enroll.status == "CANCELLED"


@pytest.mark.asyncio
async def test_paypal_webhook_idempotency_prevents_duplicate_processing():
    """Verify that receiving the same PayPal webhook event multiple times is safely ignored."""
    engine, session_factory = await make_test_db()

    async with session_factory() as db:
        first = await _reserve_payment_event(
            db,
            provider="paypal",
            event_id="WH-EVENT-UNIQUE-123",
            event_type="PAYMENT.SALE.COMPLETED",
            raw_payload="{}",
        )
        await db.commit()
        assert first is True

        # Second arrival with exact same event ID
        second = await _reserve_payment_event(
            db,
            provider="paypal",
            event_id="WH-EVENT-UNIQUE-123",
            event_type="PAYMENT.SALE.COMPLETED",
            raw_payload="{}",
        )
        assert second is False, "Duplicate webhook event must be rejected to prevent duplicate processing"


@pytest.mark.asyncio
async def test_paypal_webhook_signature_verification_guard(monkeypatch):
    """Verify webhook signature verification: fails safely when credentials or headers are missing."""
    monkeypatch.setattr(settings, "paypal_webhook_id", "")
    assert await paypal_adapter.verify_paypal_webhook(b"{}", {}, {}) is False

    monkeypatch.setattr(settings, "paypal_webhook_id", "WH-VALID-123")
    # Missing headers
    assert await paypal_adapter.verify_paypal_webhook(b"{}", {}, {}) is False


def test_paypal_provider_abstraction():
    """Verify provider-agnostic abstraction: get_payment_provider returns PayPal provider with correct interface."""
    provider = get_payment_provider("paypal")
    assert isinstance(provider, PayPalPaymentProvider)
    assert provider.name == "paypal"
    assert hasattr(provider, "is_configured")
    assert hasattr(provider, "create_subscription_checkout")
    assert hasattr(provider, "verify_subscription")
