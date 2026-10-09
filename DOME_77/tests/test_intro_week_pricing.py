from datetime import datetime
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Base, Child, LessonEntitlement, Parent, Subscription, UserConsent
from app.services import lesson_access, payment_provider, paypal_adapter, subscription_release
from app.services.mobile_tokens import issue_session_token
from app.webapp import mobile_api
from app.webapp.mobile_api import _plan_json


def test_monthly_plan_shows_three_euros_per_lesson_for_first_week():
    for lessons_per_week in range(1, 5):
        plan = SimpleNamespace(
            plan_id=f"weekly{lessons_per_week}",
            version_id=f"plan-v{lessons_per_week}",
            title=f"Plan {lessons_per_week}",
            lessons_per_week=lessons_per_week,
            price=39.0 * lessons_per_week,
            currency="EUR",
            billing_period="MONTH",
        )

        result = _plan_json(plan)

        assert result["intro_week_price"] == lessons_per_week * 3
        assert result["intro_week_days"] == 7
        assert result["price"] == plan.price
        assert result["billing_period"] == "MONTH"


@pytest.mark.asyncio
async def test_paypal_schedule_charges_paid_week_then_selected_monthly_price(monkeypatch):
    captured = {}

    async def fake_product():
        return "product-test"

    async def fake_request(method, path, *, body=None, request_id=None):
        captured.update(method=method, path=path, body=body, request_id=request_id)
        return {"id": "plan-test"}

    monkeypatch.setattr(paypal_adapter, "load_settings", lambda _name: {})
    monkeypatch.setattr(paypal_adapter, "save_settings", lambda *_args: None)
    monkeypatch.setattr(paypal_adapter, "_ensure_product", fake_product)
    monkeypatch.setattr(paypal_adapter, "_request", fake_request)

    result = await paypal_adapter.ensure_paypal_plan(
        plan_id="weekly2", plan_version_id="weekly2-v1", lessons_per_week=2,
        monthly_price=69, currency="EUR", billing_period="MONTH", intro_week_price=6,
    )

    assert result == "plan-test"
    cycles = captured["body"]["billing_cycles"]
    assert cycles == [
        {
            "frequency": {"interval_unit": "WEEK", "interval_count": 1},
            "tenure_type": "TRIAL", "sequence": 1, "total_cycles": 1,
            "pricing_scheme": {"fixed_price": {"value": "6.00", "currency_code": "EUR"}},
        },
        {
            "frequency": {"interval_unit": "MONTH", "interval_count": 1},
            "tenure_type": "REGULAR", "sequence": 2, "total_cycles": 0,
            "pricing_scheme": {"fixed_price": {"value": "69.00", "currency_code": "EUR"}},
        },
    ]


@pytest.mark.asyncio
async def test_confirmed_first_week_promotes_legacy_demo_without_losing_progress(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    now = datetime(2026, 10, 7, 12, 0, 0)
    try:
        async with sessions() as db:
            parent = Parent(email="paid-first-week@example.com", password_hash="hash", email_verified=True)
            db.add(parent)
            await db.flush()
            child = Child(parent_id=parent.id, display_name="Paid child")
            db.add(child)
            await db.flush()
            subscription = Subscription(
                child_id=child.id, course_id="conversation", plan_id="weekly2",
                current_plan_id="weekly2", billing_period="MONTH", status="ACTIVE",
                started_at=now, current_period_start=now, current_period_end=now.replace(day=14),
                lessons_per_week=2, monthly_price=69, current_plan_price=69, currency="EUR",
            )
            legacy = LessonEntitlement(
                child_id=child.id, lesson_id="demo_001", course_id="conversation",
                source="FREE_DEMO", status="ACTIVE", max_completed_runs=2, completed_runs=1,
            )
            db.add_all([subscription, legacy])
            await db.commit()
            child_id = child.id

        monkeypatch.setattr(subscription_release, "SessionLocal", sessions)
        monkeypatch.setattr(lesson_access, "SessionLocal", sessions)
        released = await subscription_release.release_due_lessons(child_id, "conversation", now=now)
        assert [row.lesson_id for row in released] == ["demo_001"]
        async with sessions() as db:
            entitlement = await db.scalar(select(LessonEntitlement).where(LessonEntitlement.child_id == child_id))
            assert entitlement is not None
            assert entitlement.source == "SUBSCRIPTION"
            assert entitlement.completed_runs == 1
        ok, reason, paid_entitlement = await lesson_access.can_start(child_id, "demo_001", "conversation")
        assert ok is True and reason == "OK"
        assert paid_entitlement is not None and paid_entitlement.source == "SUBSCRIPTION"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_new_monthly_checkout_is_paid_first_week_and_does_not_unlock_before_approval(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with sessions() as db:
            parent = Parent(email="checkout@example.com", password_hash="hash", email_verified=True)
            db.add(parent)
            await db.flush()
            child = Child(parent_id=parent.id, display_name="Checkout child")
            db.add(child)
            await db.commit()
            parent_id, child_id = parent.id, child.id

        captured = {}
        payment_confirmed = False

        class FakeProvider:
            def is_configured(self):
                return True

            async def create_subscription_checkout(self, **kwargs):
                captured.update(kwargs)
                return payment_provider.CheckoutResult(
                    ok=True, provider="paypal", checkout_url="https://paypal.example/approve",
                    subscription_id="I-TEST-123", provider_plan_id="P-TEST-123",
                )

            async def verify_subscription(self, _subscription_id):
                return payment_provider.VerifyResult(
                    ok=True, status="ACTIVE", active=True, provider="paypal",
                    subscription_id="I-TEST-123",
                    details={"plan_id":"P-TEST-123", "billing_info":{"last_payment":{"time":datetime.utcnow().isoformat()+"Z", "amount":{"value":"6", "currency_code":"EUR"}}}} if payment_confirmed else {},
                )

        monkeypatch.setattr(mobile_api, "SessionLocal", sessions)
        monkeypatch.setattr(lesson_access, "SessionLocal", sessions)
        monkeypatch.setattr(settings, "mobile_auth_secret", "pricing-checkout-secret-that-is-long-enough")
        monkeypatch.setattr(payment_provider, "get_payment_provider", lambda _name: FakeProvider())

        app = web.Application()
        mobile_api.register_mobile_routes(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            response = await client.post(
                f"/api/mobile/child/{child_id}/subscription/checkout",
                headers={"Authorization": f"Bearer {issue_session_token(parent_id)}", "X-DOME-Install-ID":"intro-monthly-installation-001"},
                json={
                    "plan_id": "weekly2", "billing_period": "MONTH", "course_id": "conversation",
                    "provider": "paypal", "payment_consents":[{
                        "document_type":"SUBSCRIPTION_TERMS", "version":"2026.10.08", "accepted":True,
                    }],
                },
            )
            assert response.status == 200
            payload = await response.json()
            assert payload["intro_week_price"] == 6
            assert payload["monthly_reference_price"] == 69
            assert payload["next_charge_after_days"] == 7
            assert captured["intro_week_price"] == 6
            assert captured["monthly_price"] == 69
            assert captured["billing_period"] == "MONTH"
            assert captured["lessons_per_week"] == 2

            async with sessions() as db:
                pending_sub = await db.scalar(select(Subscription).where(Subscription.child_id == child_id))
                assert pending_sub is not None and pending_sub.status == "PENDING"
                assert await db.scalar(select(LessonEntitlement).where(LessonEntitlement.child_id == child_id)) is None
            allowed, reason, _ = await lesson_access.can_start(child_id, "demo_001", "conversation")
            assert allowed is False and reason == "PAYMENT_REQUIRED"

            response = await client.post(
                f"/api/mobile/child/{child_id}/subscription/verify",
                headers={"Authorization": f"Bearer {issue_session_token(parent_id)}"},
                json={"subscription_id":"I-TEST-123", "course_id":"conversation"},
            )
            assert response.status == 200
            verified = await response.json()
            # Agreement approval/status without a payment cannot buy access.
            assert verified["active"] is False
            assert verified["status"] == "PENDING"
            payment_confirmed = True
            from app.services import paypal_adapter
            monkeypatch.setattr(paypal_adapter, "_meta_from_provider_plan", lambda _: captured)
            response = await client.post(
                f"/api/mobile/child/{child_id}/subscription/verify",
                headers={"Authorization": f"Bearer {issue_session_token(parent_id)}"},
                json={"subscription_id":"I-TEST-123", "course_id":"conversation"},
            )
            assert (await response.json())["active"] is True
        finally:
            await client.close()

        async with sessions() as db:
            sub = await db.scalar(select(Subscription).where(Subscription.child_id == child_id))
            consent = await db.scalar(select(UserConsent).where(UserConsent.parent_id == parent_id))
            entitlement = await db.scalar(select(LessonEntitlement).where(LessonEntitlement.child_id == child_id))
            assert consent is not None and consent.accepted is True
            # Payment confirmation turns PENDING into ACTIVE and releases the first paid week.
            assert sub.status == "ACTIVE"
            assert sub.current_period_end is not None and sub.next_charge_at == sub.current_period_end
            assert entitlement is not None and entitlement.source == "SUBSCRIPTION"
        allowed, reason, paid_entitlement = await lesson_access.can_start(child_id, "demo_001", "conversation")
        assert allowed is True and reason == "OK"
        assert paid_entitlement is not None and paid_entitlement.source == "SUBSCRIPTION"
    finally:
        await engine.dispose()
