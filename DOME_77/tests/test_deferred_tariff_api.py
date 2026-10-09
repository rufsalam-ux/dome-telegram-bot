"""Authenticated tariff changes preserve the paid agreement until renewal."""

from contextlib import asynccontextmanager
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
from app.db.models import Base, Child, Parent, Subscription
from app.services import payment_provider, paypal_adapter, platform_settings
from app.services.mobile_tokens import issue_session_token
from app.services.pricing_versions import (
    DEFAULT_ANNUAL_PRICES,
    DEFAULT_MONTHLY_PRICES,
    ensure_versioned_pricing_config,
)
from app.services.subscription_provider import ProviderPlanChangeResult
from app.webapp import mobile_api


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_settings, "SETTINGS_DIR", tmp_path / "settings")
    platform_settings.save_settings("pricing", deepcopy(platform_settings.DEFAULT_PRICING))
    ensure_versioned_pricing_config()
    monkeypatch.setattr(settings, "mobile_auth_secret", "deferred-tariff-api-test-secret-long-enough")


@asynccontextmanager
async def paid_api(monkeypatch, paid_period):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.utcnow().replace(microsecond=0)
    start = now - timedelta(days=1)
    end = start + ({"WEEK7": timedelta(days=7), "MONTH": relativedelta(months=1), "YEAR": relativedelta(years=1)}[paid_period])
    period = "MONTH" if paid_period == "MONTH" else "YEAR"
    # A grandfathered/special annual snapshot must not become today's offer.
    locked_price = 59.25 if period == "MONTH" else 615.25
    allocated = {"WEEK7": 2, "MONTH": 8, "YEAR": 104}[paid_period]
    async with sessions() as db:
        parent = Parent(email="deferred@example.test", email_verified=True, account_status="ACTIVE")
        db.add(parent)
        await db.flush()
        child = Child(parent_id=parent.id, display_name="Child", native_language="ru", target_language="en")
        db.add(child)
        await db.flush()
        sub = Subscription(
            child_id=child.id, course_id="conversation", plan_id="weekly2", current_plan_id="weekly2",
            current_plan_version_id="locked-old-version", current_plan_price=locked_price,
            lessons_per_week=2, monthly_price=locked_price, currency="EUR", billing_period=period,
            status="ACTIVE", test_mode=False, payment_provider="paypal",
            provider_subscription_id="I-EXISTING", provider_plan_id="P-OLD",
            started_at=start, current_period_start=start, current_period_end=end, next_charge_at=end,
            lessons_allocated=allocated, lessons_used=1, release_baseline_count=3,
            special_first_year=period == "YEAR", standard_renewal_price=759 if period == "YEAR" else None,
        )
        db.add(sub)
        await db.commit()
        context = SimpleNamespace(
            sessions=sessions, sub_id=sub.id, child_id=child.id, period=period, start=start, end=end,
            locked_price=locked_price, allocated=allocated, base=f"/api/mobile/child/{child.id}/subscription",
            headers={"Authorization": f"Bearer {issue_session_token(parent.id)}"}, scheduled=[], restored=[],
        )

    async def schedule(subscription, target, **kwargs):
        context.scheduled.append((subscription.provider_subscription_id, target, kwargs))
        return ProviderPlanChangeResult(status="SCHEDULED", reference="I-EXISTING", provider_plan_id="P-NEXT")

    async def restore(subscription, current, **kwargs):
        context.restored.append((subscription.provider_subscription_id, current, kwargs))
        return ProviderPlanChangeResult(status="SCHEDULED", reference="I-EXISTING", provider_plan_id="P-OLD")

    monkeypatch.setattr(mobile_api, "SessionLocal", sessions)
    monkeypatch.setattr(mobile_api, "schedule_provider_plan_change", schedule)
    monkeypatch.setattr(mobile_api, "restore_provider_current_plan", restore)
    app = web.Application()
    mobile_api.register_mobile_routes(app)
    try:
        async with TestClient(TestServer(app)) as client:
            context.client = client
            yield context
    finally:
        await engine.dispose()


async def response_json(response):
    assert response.status == 200, await response.text()
    return await response.json()


def assert_locked(subscription, ctx):
    current = subscription["current_plan"]
    assert current["plan_id"] == "weekly2"
    assert current["version_id"] == "locked-old-version"
    assert current["billing_period"] == ctx.period
    assert current["lessons_per_week"] == 2
    assert current["price"] == ctx.locked_price
    assert "intro_week_price" not in current and "first_year_price" not in current
    assert datetime.fromisoformat(subscription["current_period_start"]) == ctx.start
    assert datetime.fromisoformat(subscription["current_period_end"]) == ctx.end
    assert datetime.fromisoformat(subscription["next_charge_at"]) == ctx.end
    assert subscription["lessons_allocated"] == ctx.allocated
    assert subscription["lessons_used"] == 1


API_CASES = [
    (paid, frequency, target_period)
    for paid in ("WEEK7", "MONTH", "YEAR")
    for frequency in range(1, 5)
    for target_period in ("MONTH", "YEAR")
    if not (frequency == 2 and target_period == ("MONTH" if paid == "MONTH" else "YEAR"))
]


@pytest.mark.parametrize("paid_period,frequency,target_period", API_CASES)
@pytest.mark.asyncio
async def test_authenticated_preview_confirm_refetch_cancel_preserve_paid_period(monkeypatch, paid_period, frequency, target_period):
    async with paid_api(monkeypatch, paid_period) as ctx:
        overview = await response_json(await ctx.client.get(ctx.base, headers=ctx.headers))
        assert_locked(overview["subscription"], ctx)
        target = next(p for p in overview["plans"] if p["lessons_per_week"] == frequency and p["billing_period"] == target_period)
        price = (DEFAULT_ANNUAL_PRICES if target_period == "YEAR" else DEFAULT_MONTHLY_PRICES)[frequency]
        assert target["price"] == price
        assert "intro_week_price" not in target and "first_year_price" not in target
        body = {"plan_id": target["plan_id"], "billing_period": target_period, "version_id": target["version_id"],
                "price": 0.01, "lessons_per_week": 99, "intro_week_price": 0.01}

        preview = await response_json(await ctx.client.post(ctx.base + "/plan-change/preview", headers=ctx.headers, json=body))
        assert preview["current_plan"] == overview["subscription"]["current_plan"]
        assert preview["new_plan"] == target
        assert datetime.fromisoformat(preview["effective_at"]) == ctx.end
        assert not ctx.scheduled
        async with ctx.sessions() as db:
            assert (await db.get(Subscription, ctx.sub_id)).pending_plan_id is None

        confirmed = await response_json(await ctx.client.post(ctx.base + "/plan-change", headers=ctx.headers, json=body))
        assert_locked(confirmed["subscription"], ctx)
        pending = confirmed["subscription"]["pending_plan"]
        assert pending["plan_id"] == target["plan_id"]
        assert pending["billing_period"] == target_period
        assert pending["lessons_per_week"] == frequency
        assert pending["price"] == price
        assert pending["version_id"] == target["version_id"]
        assert datetime.fromisoformat(pending["effective_at"]) == ctx.end
        assert len(ctx.scheduled) == 1
        provider_id, scheduled_target, scheduling = ctx.scheduled[0]
        assert provider_id == "I-EXISTING"
        assert scheduled_target.price == price and scheduled_target.billing_period == target_period
        assert scheduling["effective_at"] == ctx.end

        fetched = await response_json(await ctx.client.get(ctx.base, headers=ctx.headers))
        assert fetched["subscription"] == confirmed["subscription"]
        async with ctx.sessions() as db:
            persisted = await db.get(Subscription, ctx.sub_id)
            assert persisted.pending_plan_id == target["plan_id"]
            assert persisted.pending_plan_billing_period == target_period
            assert persisted.pending_provider_plan_id == "P-NEXT"
            assert persisted.provider_plan_id == "P-OLD"
            assert persisted.provider_subscription_id == "I-EXISTING"
            assert persisted.release_baseline_count == 3
            assert persisted.current_plan_price == persisted.monthly_price == ctx.locked_price
            assert len(list(await db.scalars(select(Subscription)))) == 1

        cancelled = await response_json(await ctx.client.delete(ctx.base + "/plan-change", headers=ctx.headers, json={}))
        assert_locked(cancelled["subscription"], ctx)
        assert cancelled["subscription"]["pending_plan"] is None
        assert len(ctx.restored) == 1
        assert ctx.restored[0][1].price == ctx.locked_price
        assert ctx.restored[0][1].provider_plan_id == "P-OLD"
        fetched = await response_json(await ctx.client.get(ctx.base, headers=ctx.headers))
        assert fetched["subscription"] == cancelled["subscription"]


@pytest.mark.parametrize("target_period", ["MONTH", "YEAR"])
@pytest.mark.parametrize("frequency", range(1, 5))
@pytest.mark.asyncio
async def test_paypal_revision_keeps_agreement_and_uses_only_regular_cycle(monkeypatch, target_period, frequency):
    requests = []

    async def product():
        return "PRODUCT-TEST"

    async def request(method, path, *, body=None, request_id=""):
        requests.append((method, path, body, request_id))
        if path == "/v1/billing/plans":
            return {"id": "P-REGULAR"}
        assert path == "/v1/billing/subscriptions/I-EXISTING/revise"
        return {"links": [{"rel": "approve", "href": "https://example.test/approve-change"}]}

    monkeypatch.setattr(paypal_adapter, "_ensure_product", product)
    monkeypatch.setattr(paypal_adapter, "_request", request)
    price = (DEFAULT_ANNUAL_PRICES if target_period == "YEAR" else DEFAULT_MONTHLY_PRICES)[frequency]
    result = await paypal_adapter.change_paypal_subscription_plan(
        subscription_id="I-EXISTING", child_id=42, course_id="conversation", plan_id=f"weekly{frequency}",
        plan_version_id=f"target-{target_period}-{frequency}", lessons_per_week=frequency,
        monthly_price=price, currency="EUR", billing_period=target_period,
        success_url="https://example.test/success", cancel_url="https://example.test/cancel", idempotency_key="change-test",
    )
    assert [path for _, path, _, _ in requests] == ["/v1/billing/plans", "/v1/billing/subscriptions/I-EXISTING/revise"]
    assert all(method == "POST" for method, _, _, _ in requests)
    assert requests[0][2]["billing_cycles"] == [{
        "frequency": {"interval_unit": target_period, "interval_count": 1}, "tenure_type": "REGULAR",
        "sequence": 1, "total_cycles": 0,
        "pricing_scheme": {"fixed_price": {"value": f"{price:.2f}", "currency_code": "EUR"}},
    }]
    assert requests[1][2]["plan_id"] == "P-REGULAR"
    assert requests[1][3] == "change-test"
    assert result["id"] == "I-EXISTING" and result["plan_id"] == "P-REGULAR"
    assert result["approval_url"] == "https://example.test/approve-change"


@pytest.mark.parametrize("paid_period,target_period", [("MONTH", "YEAR"), ("YEAR", "MONTH"), ("WEEK7", "MONTH")])
@pytest.mark.asyncio
async def test_verify_revised_agreement_needs_payment_at_paid_boundary(monkeypatch, paid_period, target_period):
    async with paid_api(monkeypatch, paid_period) as ctx:
        overview = await response_json(await ctx.client.get(ctx.base, headers=ctx.headers))
        target = next(p for p in overview["plans"] if p["plan_id"] == "weekly2" and p["billing_period"] == target_period)
        confirmed = await response_json(await ctx.client.post(ctx.base + "/plan-change", headers=ctx.headers, json=target))
        assert_locked(confirmed["subscription"], ctx)
        platform_settings.save_settings("payments", {"paypal_plan_versions": {"regular-target": {
            "paypal_plan_id": "P-NEXT", "plan_id": "weekly2", "plan_version_id": target["version_id"],
            "billing_period": target_period, "lessons_per_week": 2, "price": target["price"], "currency": "EUR",
        }}})
        paid_at = ctx.end - timedelta(seconds=1)
        next_end = ctx.end + (relativedelta(years=1) if target_period == "YEAR" else relativedelta(months=1))

        class Provider:
            def is_configured(self):
                return True

            async def verify_subscription(self, subscription_id):
                assert subscription_id == "I-EXISTING"
                return payment_provider.VerifyResult(
                    ok=True, status="ACTIVE", active=True, provider="paypal", subscription_id=subscription_id,
                    details={"id": subscription_id, "plan_id": "P-NEXT", "status": "ACTIVE", "billing_info": {
                        "last_payment": {"time": paid_at.isoformat() + "Z", "amount": {"value": str(target["price"]), "currency_code": "EUR"}},
                        "next_billing_time": next_end.isoformat() + "Z",
                    }},
                )

        monkeypatch.setattr(payment_provider, "get_payment_provider", lambda _name: Provider())
        early = await response_json(await ctx.client.post(ctx.base + "/verify", headers=ctx.headers, json={}))
        assert_locked(early["subscription"], ctx)
        assert early["subscription"]["pending_plan"] is not None

        paid_at = ctx.end
        renewed = await response_json(await ctx.client.post(ctx.base + "/verify", headers=ctx.headers, json={}))
        active = renewed["subscription"]
        assert active["current_plan"] == target
        assert active["pending_plan"] is None
        assert datetime.fromisoformat(active["current_period_start"]) == ctx.end
        assert datetime.fromisoformat(active["current_period_end"]) == next_end
        assert active["lessons_allocated"] == (104 if target_period == "YEAR" else 8)
        assert active["lessons_used"] == 0
        assert active["special_first_year"] is False
        async with ctx.sessions() as db:
            sub = await db.get(Subscription, ctx.sub_id)
            assert sub.provider_subscription_id == "I-EXISTING"
            assert sub.provider_plan_id == "P-NEXT"
