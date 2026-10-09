import json
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Base, Child, LessonEntitlement, Parent, Subscription, UserConsent
from app.services import lesson_access, payment_provider, paypal_adapter, platform_settings, special_annual_pricing, subscription_release
from app.services.mobile_tokens import issue_session_token
from app.services.payment_lifecycle import NormalizedPaymentEvent, apply_normalized_event
from app.services.pricing_versions import DEFAULT_ANNUAL_PRICES, DEFAULT_MONTHLY_PRICES, ensure_versioned_pricing_config
from app.services.special_annual_pricing import SPECIAL_FIRST_YEAR_PRICES
from app.webapp import mobile_api


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_settings, "SETTINGS_DIR", tmp_path / "settings")
    platform_settings.save_settings("pricing", deepcopy(platform_settings.DEFAULT_PRICING))
    ensure_versioned_pricing_config()
    monkeypatch.setattr(subscription_release, "_course_order", lambda _course: ["demo_001"] + [f"lesson_{i}" for i in range(1, 240)])


CASES = [("MONTH", False), ("YEAR", False), ("YEAR", True)]


def price_for(period, special, frequency):
    return (SPECIAL_FIRST_YEAR_PRICES if special else DEFAULT_ANNUAL_PRICES if period == "YEAR" else DEFAULT_MONTHLY_PRICES)[frequency]


@pytest.mark.parametrize("frequency", range(1, 5))
@pytest.mark.parametrize("period,special", CASES)
@pytest.mark.asyncio
async def test_provider_cycles_and_advertised_offer(monkeypatch, frequency, period, special):
    captured = {}

    async def product():
        return "product-test"

    async def request(method, path, *, body=None, request_id=None):
        captured.update(body=body)
        return {"id": "P-INTRO"}

    monkeypatch.setattr(paypal_adapter, "_ensure_product", product)
    monkeypatch.setattr(paypal_adapter, "_request", request)
    price = price_for(period, special, frequency)
    await paypal_adapter.ensure_paypal_plan(
        plan_id=f"weekly{frequency}", plan_version_id=f"version-{period}-{frequency}",
        lessons_per_week=frequency, monthly_price=price, currency="EUR",
        billing_period=period, special_first_year=special,
        standard_renewal_price=DEFAULT_ANNUAL_PRICES[frequency] if period == "YEAR" else 0,
        intro_week_price=3 * frequency,
    )
    cycles = captured["body"]["billing_cycles"]
    assert [(x["frequency"]["interval_unit"], x["total_cycles"]) for x in cycles] == (
        [("WEEK", 1), ("YEAR", 1), ("YEAR", 0)] if special else [("WEEK", 1), (period, 0)]
    )
    assert [float(x["pricing_scheme"]["fixed_price"]["value"]) for x in cycles] == (
        [3 * frequency, price, DEFAULT_ANNUAL_PRICES[frequency]] if special else [3 * frequency, price]
    )
    assert [x["sequence"] for x in cycles] == list(range(1, len(cycles) + 1))
    assert paypal_adapter._meta_from_provider_plan("P-INTRO")["intro_week_price"] == 3 * frequency
    offer = mobile_api._plan_json(SimpleNamespace(
        plan_id=f"weekly{frequency}", version_id="v1", title="Plan", lessons_per_week=frequency,
        price=price, currency="EUR", billing_period=period,
    ), is_eligible_special=special)
    assert offer["intro_week_price"] == 3 * frequency and offer["intro_week_days"] == 7
    assert offer["price"] == price
    if period == "YEAR":
        assert f"€{price:.2f}" in offer["renewal_disclosure"]


async def database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.parametrize("frequency", range(1, 5))
@pytest.mark.parametrize("period,special", CASES)
@pytest.mark.asyncio
async def test_checkout_week_then_selected_period_requires_payment(monkeypatch, frequency, period, special):
    engine, sessions = await database()
    paid_at = datetime.utcnow().replace(microsecond=0)
    captured = {}
    confirmed = False
    payment_amount = 3 * frequency
    payment_at = paid_at
    payment_end = paid_at + timedelta(days=7, seconds=1)

    class Provider:
        def is_configured(self):
            return True

        async def create_subscription_checkout(self, **kwargs):
            captured.update(kwargs)
            return payment_provider.CheckoutResult(ok=True, provider="paypal", checkout_url="https://example.test/approve", subscription_id="I-INTRO", provider_plan_id="P-INTRO")

        async def verify_subscription(self, _subscription_id):
            return payment_provider.VerifyResult(ok=True, status="ACTIVE" if confirmed else "APPROVED", active=confirmed, provider="paypal", subscription_id="I-INTRO", details={"billing_info": {
                "last_payment": {"time": payment_at.isoformat() + "Z", "amount": {"value": str(payment_amount), "currency_code": "EUR"}},
                "next_billing_time": payment_end.isoformat() + "Z",
            }})

    async def eligible(*_args, **_kwargs):
        return special

    monkeypatch.setattr(special_annual_pricing, "is_eligible_for_special_annual", eligible)
    monkeypatch.setattr(mobile_api, "SessionLocal", sessions)
    monkeypatch.setattr(lesson_access, "SessionLocal", sessions)
    monkeypatch.setattr(subscription_release, "SessionLocal", sessions)
    monkeypatch.setattr(settings, "mobile_auth_secret", "annual-intro-test-secret-long-enough")
    monkeypatch.setattr(payment_provider, "get_payment_provider", lambda _name: Provider())
    monkeypatch.setattr(paypal_adapter, "_meta_from_provider_plan", lambda _id: captured)
    try:
        async with sessions() as db:
            parent = Parent(email="intro@example.test", email_verified=True)
            db.add(parent)
            await db.flush()
            child = Child(parent_id=parent.id, display_name="Child")
            db.add(child)
            await db.commit()
            child_id, parent_id = child.id, parent.id
        app = web.Application()
        mobile_api.register_mobile_routes(app)
        async with TestClient(TestServer(app)) as client:
            headers = {"Authorization": f"Bearer {issue_session_token(parent_id)}", "X-DOME-Install-ID": "annual-intro-test-installation-001"}
            base = f"/api/mobile/child/{child_id}/subscription"
            overview = await (await client.get(base, headers=headers)).json()
            selected = next(p for p in overview["plans"] if p["billing_period"] == period and p["lessons_per_week"] == frequency)
            body = {"plan_id": selected["plan_id"], "version_id": "stale-version", "billing_period": period,
                    "intro_week_price": 0.01, "price": 0.01, "lessons_per_week": 99,
                    "payment_consents": [{"document_type": "SUBSCRIPTION_TERMS", "version": overview["subscription_terms_version"], "accepted": True}]}
            response = await client.post(base + "/checkout", headers=headers, json=body)
            assert response.status == 409 and not captured
            body["version_id"] = selected["version_id"]
            response = await client.post(base + "/checkout", headers=headers, json=body)
            assert response.status == 200
            result = await response.json()
            assert captured["intro_week_price"] == result["intro_week_price"] == 3 * frequency
            assert captured["monthly_price"] == result["next_charge_price"] == selected["price"] == price_for(period, special, frequency)
            assert captured["plan_version_id"] == selected["version_id"]
            assert captured["billing_period"] == period and captured["lessons_per_week"] == frequency
            assert result["next_charge_after_days"] == 7
            response = await client.post(base + "/verify", headers=headers, json={"subscription_id": "I-SOMEONE-ELSE"})
            assert response.status == 403
            response = await client.post(base + "/verify", headers=headers, json={"subscription_id": "I-INTRO"})
            assert (await response.json())["active"] is False
            async with sessions() as db:
                assert (await db.scalar(select(Subscription))).status == "PENDING"
                assert await db.scalar(select(LessonEntitlement)) is None
            confirmed = True
            response = await client.post(base + "/verify", headers=headers, json={"subscription_id": "I-INTRO"})
            assert (await response.json())["active"] is True
            async with sessions() as db:
                sub = await db.scalar(select(Subscription))
                assert sub.current_period_start == paid_at
                assert sub.current_period_end == sub.next_charge_at == paid_at + timedelta(days=7)
                assert sub.lessons_allocated == frequency
                context = json.loads((await db.scalar(select(UserConsent))).metadata_json)
                assert context["intro_week_price"] == 3 * frequency and context["intro_week_days"] == 7
                assert context["billing_period"] == period and context["next_charge_price"] == selected["price"]
                assert context["special_first_year"] is special
                sub.lessons_used = 1
                await db.commit()
            await client.post(base + "/verify", headers=headers, json={"subscription_id": "I-INTRO"})
            async with sessions() as db:
                sub = await db.scalar(select(Subscription))
                assert sub.lessons_used == 1
                webhook = paypal_adapter.normalize_paypal_event({
                    "id": "intro-webhook-after-verify", "event_type": "PAYMENT.SALE.COMPLETED",
                    "resource": {"billing_agreement_id": "I-INTRO", "create_time": (paid_at + timedelta(seconds=1)).isoformat() + "Z", "amount": {"total": str(payment_amount)}},
                }, {"plan_id": "P-INTRO", "billing_info": {
                    "last_payment": {"time": paid_at.isoformat() + "Z"},
                    "next_billing_time": payment_end.isoformat() + "Z",
                }})
                await apply_normalized_event(db, webhook)
                await db.commit()
                assert sub.lessons_used == 1 and sub.current_period_start == paid_at
                assert sub.current_period_end == paid_at + timedelta(days=7)
                baseline = sub.release_baseline_count
                started_at = sub.started_at
                sub.status = 'PAST_DUE'
                await db.commit()
            response = await client.post(base + "/verify", headers=headers, json={"subscription_id": "I-INTRO"})
            assert (await response.json())["active"] is False
            async with sessions() as db:
                sub = await db.scalar(select(Subscription))
                assert sub.status == 'PAST_DUE' and sub.lessons_used == 1
                assert sub.release_baseline_count == baseline and sub.started_at == started_at
            for elapsed in (7, 8, 30):
                assert await subscription_release.release_due_lessons(child_id, "conversation", now=paid_at + timedelta(days=elapsed)) == []
            # Verification can also recover the paid regular period before its
            # webhook arrives; both paths must produce the same quota/dates.
            payment_at = paid_at + timedelta(days=30)
            payment_end = payment_at + (relativedelta(years=1) if period == "YEAR" else relativedelta(months=1))
            payment_amount = selected["price"]
            response = await client.post(base + "/verify", headers=headers, json={"subscription_id": "I-INTRO"})
            assert (await response.json())["active"] is True
            async with sessions() as db:
                sub = await db.scalar(select(Subscription))
                assert sub.current_period_start == payment_at and sub.current_period_end == payment_end
                assert sub.lessons_allocated == frequency * (52 if period == "YEAR" else 4)
                assert sub.started_at == payment_at
        async with sessions() as db:
            assert len((await db.scalars(select(LessonEntitlement))).all()) == frequency
            sub = await db.scalar(select(Subscription))
            regular_start = paid_at + timedelta(days=30)
            regular_end = regular_start + (relativedelta(years=1) if period == "YEAR" else relativedelta(months=1))
            event = NormalizedPaymentEvent(provider="paypal", event_id="regular-paid", event_type="PAYMENT_SUCCEEDED", status="ACTIVE", child_id=child_id, course_id="conversation", plan_id=sub.plan_id, plan_version_id=sub.current_plan_version_id, provider_subscription_id="I-INTRO", lessons_per_week=frequency, billing_period=period, monthly_price=selected["price"], charged_amount=selected["price"], intro_week_price=3 * frequency, period_start=regular_start, period_end=regular_end, special_first_year=special, standard_renewal_price=DEFAULT_ANNUAL_PRICES[frequency] if special else 0)
            await apply_normalized_event(db, event)
            await db.commit()
            assert sub.lessons_allocated == frequency * (52 if period == "YEAR" else 4)
            assert sub.started_at == regular_start
            assert sub.special_first_year is special
        released = await subscription_release.release_due_lessons(child_id, "conversation", now=regular_start)
        assert len(released) == frequency  # No catch-up for the unpaid gap.
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_paypal_approval_without_first_payment_is_not_active(monkeypatch):
    monkeypatch.setattr(settings, "paypal_client_id", "test")
    monkeypatch.setattr(settings, "paypal_client_secret", "test")
    monkeypatch.setattr(paypal_adapter, "_meta_from_provider_plan", lambda _plan: {"intro_week_price": 6})

    async def unpaid(_id):
        return {"id": "I-TEST", "plan_id": "P-TEST", "status": "ACTIVE", "billing_info": {}}

    monkeypatch.setattr(paypal_adapter, "get_paypal_subscription", unpaid)
    result = await payment_provider.PayPalPaymentProvider().verify_subscription("I-TEST")
    assert result.ok and not result.active


@pytest.mark.asyncio
async def test_webhook_intro_then_annual_then_standard_renewal_and_stale_payment(monkeypatch):
    engine, sessions = await database()
    started = datetime(2026, 10, 7, 12)
    metadata = {"plan_id": "weekly2", "plan_version_id": "year-v1", "lessons_per_week": 2, "monthly_price": 599, "billing_period": "YEAR", "currency": "EUR", "intro_week_price": 6, "special_first_year": True, "standard_renewal_price": 759}
    monkeypatch.setattr(paypal_adapter, "_meta_from_provider_plan", lambda _id: metadata)
    try:
        async with sessions() as db:
            parent = Parent(email="webhook@example.test")
            db.add(parent)
            await db.flush()
            child = Child(parent_id=parent.id, display_name="Child")
            db.add(child)
            await db.flush()
            sub = Subscription(child_id=child.id, course_id="conversation", status="PENDING", billing_period="YEAR", plan_id="weekly2", current_plan_id="weekly2", lessons_per_week=2, provider_subscription_id="I-TEST", current_plan_price=599, payment_provider="paypal", special_first_year=True)
            db.add(sub)
            await db.flush()
            custom = paypal_adapter._custom_id(child.id, "conversation", "weekly2", "year-v1", 2, 599, "YEAR", True, 759)

            def normalized(amount, at, end, event_id):
                snapshot = {"id": "I-TEST", "plan_id": "P-TEST", "custom_id": custom, "billing_info": {"last_payment": {"time": at.isoformat() + "Z"}, "next_billing_time": end.isoformat() + "Z"}}
                return paypal_adapter.normalize_paypal_event({"id": event_id, "event_type": "PAYMENT.SALE.COMPLETED", "create_time": at.isoformat() + "Z", "resource": {"billing_agreement_id": "I-TEST", "amount": {"total": str(amount), "currency": "EUR"}}}, snapshot)

            intro = normalized(6, started, started + timedelta(days=7), "intro")
            await apply_normalized_event(db, intro)
            await db.flush()
            assert sub.lessons_allocated == 2 and sub.current_period_end == started + timedelta(days=7)
            assert sub.special_first_year
            sub.lessons_used = 1
            await apply_normalized_event(db, intro)
            assert sub.lessons_used == 1
            annual_start = started + timedelta(days=7)
            annual_end = annual_start + relativedelta(years=1)
            await apply_normalized_event(db, normalized(599, annual_start, annual_end, "first-year"))
            assert sub.special_first_year and sub.lessons_allocated == 104 and sub.current_plan_price == 599
            await apply_normalized_event(db, normalized(759, annual_end, annual_end + relativedelta(years=1), "renewal"))
            assert not sub.special_first_year and sub.current_plan_price == 759
            await apply_normalized_event(db, intro)
            assert sub.current_period_start == annual_end and sub.current_plan_price == 759
            late_activation = paypal_adapter.normalize_paypal_event({
                "id": "late-activation", "event_type": "BILLING.SUBSCRIPTION.ACTIVATED",
                "resource": {"id": "I-TEST", "plan_id": "P-TEST", "custom_id": custom,
                             "billing_info": {"last_payment": {"time": started.isoformat() + "Z", "amount": {"value": "6"}}}},
            })
            await apply_normalized_event(db, late_activation)
            assert not sub.special_first_year and sub.current_plan_price == 759
            late_update = paypal_adapter.normalize_paypal_event({
                "id": "late-update", "event_type": "BILLING.SUBSCRIPTION.UPDATED",
                "resource": {"id": "I-TEST", "plan_id": "P-TEST", "custom_id": custom},
            })
            await apply_normalized_event(db, late_update)
            assert not sub.special_first_year and sub.current_plan_price == 759
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_standard_annual_does_not_reuse_old_monthly_schedule_cache(monkeypatch):
    cfg = platform_settings.load_settings("payments")
    cfg["paypal_plan_versions"] = {
        "year-v1:YEAR:EUR:439.00:intro:3.00": {"paypal_plan_id": "P-OLD-MONTHLY", "price": 439},
    }
    platform_settings.save_settings("payments", cfg)
    requests = []

    async def product():
        return "product-test"

    async def request(method, path, *, body=None, request_id=None):
        requests.append(body)
        return {"id": "P-NEW-YEARLY"}

    monkeypatch.setattr(paypal_adapter, "_ensure_product", product)
    monkeypatch.setattr(paypal_adapter, "_request", request)
    result = await paypal_adapter.ensure_paypal_plan(plan_id="weekly1", plan_version_id="year-v1", lessons_per_week=1,
        monthly_price=439, currency="EUR", billing_period="YEAR", intro_week_price=3)
    assert result == "P-NEW-YEARLY"
    assert requests[0]["billing_cycles"][1]["frequency"]["interval_unit"] == "YEAR"
    assert platform_settings.load_settings("payments")["paypal_plan_versions"]["year-v1:YEAR:EUR:439.00:intro:3.00"]["paypal_plan_id"] == "P-OLD-MONTHLY"
