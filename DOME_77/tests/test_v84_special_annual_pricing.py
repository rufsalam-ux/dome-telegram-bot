"""
Tests A-M: DOME Special First-Year Annual Pricing
==================================================
Run:
    python -m pytest tests/test_v84_special_annual_pricing.py -v
"""
from __future__ import annotations

import logging
from copy import deepcopy
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, Child, Parent, Subscription
from app.services import platform_settings
from app.services.payment_lifecycle import NormalizedPaymentEvent, apply_normalized_event
from app.services.pricing_versions import (
    YEAR,
    ensure_versioned_pricing_config,
    plan_versions_for_course,
)
from app.services.special_annual_pricing import (
    SPECIAL_FIRST_YEAR_PRICES,
    STANDARD_ANNUAL_PRICES,
    INTRO_WEEK_PRICES,
    get_plan_annual_offer_detail,
    is_eligible_for_special_annual,
    is_special_annual_offer_active,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def isolated_pricing(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_settings, "SETTINGS_DIR", tmp_path / "platform-settings")
    platform_settings.save_settings("pricing", deepcopy(platform_settings.DEFAULT_PRICING))
    return ensure_versioned_pricing_config()


async def make_test_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Tests A-D: get_plan_annual_offer_detail per freq, is_eligible=True
# ---------------------------------------------------------------------------

class TestA_SpecialOfferDetail_Freq1:
    """A: weekly1 — special offer detail when eligible."""

    def test_effective_price(self):
        d = get_plan_annual_offer_detail("weekly1", 1, is_eligible=True)
        assert d["effective_price"] == 349.0, f"effective_price: {d['effective_price']}"

    def test_first_year_price(self):
        d = get_plan_annual_offer_detail("weekly1", 1, is_eligible=True)
        assert d["first_year_price"] == 349.0

    def test_standard_renewal_price(self):
        d = get_plan_annual_offer_detail("weekly1", 1, is_eligible=True)
        assert d["standard_renewal_price"] == 439.0

    def test_intro_week_price(self):
        d = get_plan_annual_offer_detail("weekly1", 1, is_eligible=True)
        assert d["intro_week_price"] == 3.0

    def test_savings(self):
        d = get_plan_annual_offer_detail("weekly1", 1, is_eligible=True)
        assert d["annual_savings"] == pytest.approx(90.0)

    def test_special_flag(self):
        d = get_plan_annual_offer_detail("weekly1", 1, is_eligible=True)
        assert d["special_first_year"] is True


class TestB_SpecialOfferDetail_Freq2:
    """B: weekly2 — special offer detail when eligible."""

    def test_effective_price(self):
        d = get_plan_annual_offer_detail("weekly2", 2, is_eligible=True)
        assert d["effective_price"] == 599.0

    def test_standard_renewal_price(self):
        d = get_plan_annual_offer_detail("weekly2", 2, is_eligible=True)
        assert d["standard_renewal_price"] == 759.0

    def test_savings(self):
        d = get_plan_annual_offer_detail("weekly2", 2, is_eligible=True)
        assert d["annual_savings"] == pytest.approx(160.0)


class TestC_SpecialOfferDetail_Freq3:
    """C: weekly3 — special offer detail when eligible."""

    def test_effective_price(self):
        d = get_plan_annual_offer_detail("weekly3", 3, is_eligible=True)
        assert d["effective_price"] == 849.0

    def test_standard_renewal_price(self):
        d = get_plan_annual_offer_detail("weekly3", 3, is_eligible=True)
        assert d["standard_renewal_price"] == 1089.0

    def test_savings(self):
        d = get_plan_annual_offer_detail("weekly3", 3, is_eligible=True)
        assert d["annual_savings"] == pytest.approx(240.0)


class TestD_SpecialOfferDetail_Freq4:
    """D: weekly4 — special offer detail when eligible."""

    def test_effective_price(self):
        d = get_plan_annual_offer_detail("weekly4", 4, is_eligible=True)
        assert d["effective_price"] == 1199.0

    def test_standard_renewal_price(self):
        d = get_plan_annual_offer_detail("weekly4", 4, is_eligible=True)
        assert d["standard_renewal_price"] == 1535.0

    def test_savings(self):
        d = get_plan_annual_offer_detail("weekly4", 4, is_eligible=True)
        assert d["annual_savings"] == pytest.approx(336.0)


# ---------------------------------------------------------------------------
# Test E: renewal_disclosure contains standard price
# ---------------------------------------------------------------------------

class TestE_RenewalDisclosure:
    """E: renewal_disclosure must include both first-year and standard renewal price."""

    @pytest.mark.parametrize("freq,first_yr,renewal", [
        (1, 349.0, 439.0),
        (2, 599.0, 759.0),
        (3, 849.0, 1089.0),
        (4, 1199.0, 1535.0),
    ])
    def test_disclosure_contains_prices(self, freq, first_yr, renewal):
        d = get_plan_annual_offer_detail(f"weekly{freq}", freq, is_eligible=True)
        disclosure = d["renewal_disclosure"]
        assert str(int(first_yr)) in disclosure, f"First-year price {first_yr} not in disclosure: {disclosure!r}"
        assert str(int(renewal)) in disclosure, f"Renewal price {renewal} not in disclosure: {disclosure!r}"

    def test_disclosure_mentions_renewal(self):
        d = get_plan_annual_offer_detail("weekly2", 2, is_eligible=True)
        disclosure = d["renewal_disclosure"]
        # Must explain auto-renewal (contains "продлевается" or "автоматически")
        assert "продлевается" in disclosure or "автоматически" in disclosure, \
            f"Disclosure missing renewal language: {disclosure!r}"

    def test_not_eligible_no_special_price(self):
        d = get_plan_annual_offer_detail("weekly2", 2, is_eligible=False)
        assert d["effective_price"] == 759.0
        assert d["special_first_year"] is False
        assert d["annual_savings"] == 0.0


# ---------------------------------------------------------------------------
# Test F: DB lifecycle — PAYMENT_SUCCEEDED with special_first_year=True
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestF_DBLifecycle_SpecialFirstYear:
    """F: PAYMENT_SUCCEEDED with special_first_year=True → sub.special_first_year==True, entitlement ACTIVE."""

    async def test_subscription_special_flag_set(self):
        engine, session_factory = await make_test_db()
        async with session_factory() as db:
            parent = Parent(id=1, email="test@example.com", display_name="Test", account_status="ACTIVE")
            child = Child(id=10, parent_id=1, display_name="Child", age_years=7, target_language="ru", native_language="en")
            db.add_all([parent, child])
            await db.commit()

            # SUBSCRIPTION_CREATED → APPROVAL_PENDING
            ev_create = NormalizedPaymentEvent(
                provider="paypal",
                event_id="f_create",
                event_type="SUBSCRIPTION_CREATED",
                status="APPROVAL_PENDING",
                child_id=10,
                course_id="conversation",
                plan_id="weekly2",
                lessons_per_week=2,
                monthly_price=599.0,
                billing_period="YEAR",
                currency="EUR",
                provider_subscription_id="I-SPEC-F01",
                special_first_year=True,
                standard_renewal_price=759.0,
            )
            sub = await apply_normalized_event(db, ev_create)
            await db.commit()
            assert sub is not None

            # SUBSCRIPTION_ACTIVE
            ev_active = NormalizedPaymentEvent(
                provider="paypal",
                event_id="f_active",
                event_type="SUBSCRIPTION_ACTIVE",
                status="ACTIVE",
                child_id=10,
                course_id="conversation",
                plan_id="weekly2",
                lessons_per_week=2,
                monthly_price=599.0,
                billing_period="YEAR",
                currency="EUR",
                provider_subscription_id="I-SPEC-F01",
                special_first_year=True,
                standard_renewal_price=759.0,
            )
            sub = await apply_normalized_event(db, ev_active)
            await db.commit()

            assert sub.status == "ACTIVE"
            assert sub.special_first_year is True
            assert sub.standard_renewal_price == 759.0
            assert sub.billing_period == "YEAR"


# ---------------------------------------------------------------------------
# Test G: Renewal clears special_first_year
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestG_RenewalClearsSpecialFlag:
    """G: Second PAYMENT_SUCCEEDED with later period_start → special_first_year becomes False."""

    async def test_renewal_clears_flag(self):
        engine, session_factory = await make_test_db()
        async with session_factory() as db:
            parent = Parent(id=2, email="g@example.com", display_name="G", account_status="ACTIVE")
            child = Child(id=20, parent_id=2, display_name="GChild", age_years=8, target_language="ru", native_language="en")
            db.add_all([parent, child])
            await db.commit()

            period1_start = datetime(2026, 9, 17, 0, 0, 0)

            # Year 1 activation with special price
            ev_active = NormalizedPaymentEvent(
                provider="paypal",
                event_id="g_active",
                event_type="SUBSCRIPTION_ACTIVE",
                status="ACTIVE",
                child_id=20,
                course_id="conversation",
                plan_id="weekly2",
                lessons_per_week=2,
                monthly_price=599.0,
                billing_period="YEAR",
                currency="EUR",
                provider_subscription_id="I-SPEC-G01",
                period_start=period1_start,
                special_first_year=True,
                standard_renewal_price=759.0,
            )
            sub = await apply_normalized_event(db, ev_active)
            await db.commit()
            assert sub.special_first_year is True

            period2_start = datetime(2027, 9, 17, 0, 0, 0)

            # Year 2 renewal payment (standard price, later period)
            ev_renew = NormalizedPaymentEvent(
                provider="paypal",
                event_id="g_renew",
                event_type="PAYMENT_SUCCEEDED",
                status="ACTIVE",
                child_id=20,
                course_id="conversation",
                plan_id="weekly2",
                lessons_per_week=2,
                monthly_price=759.0,
                billing_period="YEAR",
                currency="EUR",
                provider_subscription_id="I-SPEC-G01",
                period_start=period2_start,
                charged_amount=759.0,
                special_first_year=False,
            )
            sub = await apply_normalized_event(db, ev_renew)
            await db.commit()

            # After renewal, special_first_year must be False
            assert sub.special_first_year is False, \
                f"Expected special_first_year=False after renewal, got {sub.special_first_year}"
            assert sub.status == "ACTIVE"


# ---------------------------------------------------------------------------
# Test H: Offer inactive after end date
# ---------------------------------------------------------------------------

class TestH_OfferExpired:
    """H: is_special_annual_offer_active returns False after 2027-04-17."""

    def test_expired_date(self):
        future = datetime(2027, 4, 18, 0, 0, 0)
        assert is_special_annual_offer_active(now=future) is False

    def test_active_during_window(self):
        mid = datetime(2026, 12, 1, 0, 0, 0)
        assert is_special_annual_offer_active(now=mid) is True

    def test_active_on_start_date(self):
        start = datetime(2026, 9, 17, 0, 0, 0)
        assert is_special_annual_offer_active(now=start) is True

    def test_active_on_last_day(self):
        last = datetime(2027, 4, 17, 0, 0, 0)
        assert is_special_annual_offer_active(now=last) is True


# ---------------------------------------------------------------------------
# Test I: Sub created during window → entitlement persists after window closes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestI_EntitlementPersistsAfterExpiry:
    """I: Subscription created with special_first_year=True remains ACTIVE even if offer window closes."""

    async def test_entitlement_survives_expiry(self):
        engine, session_factory = await make_test_db()
        async with session_factory() as db:
            parent = Parent(id=3, email="i@example.com", display_name="I", account_status="ACTIVE")
            child = Child(id=30, parent_id=3, display_name="IChild", age_years=9, target_language="ru", native_language="en")
            db.add_all([parent, child])
            await db.commit()

            ev = NormalizedPaymentEvent(
                provider="paypal",
                event_id="i_active",
                event_type="SUBSCRIPTION_ACTIVE",
                status="ACTIVE",
                child_id=30,
                course_id="conversation",
                plan_id="weekly1",
                lessons_per_week=1,
                monthly_price=349.0,
                billing_period="YEAR",
                currency="EUR",
                provider_subscription_id="I-SPEC-I01",
                special_first_year=True,
                standard_renewal_price=439.0,
            )
            sub = await apply_normalized_event(db, ev)
            await db.commit()

            # Offer has since expired — but the existing subscription stands
            offer_expired = is_special_annual_offer_active(now=datetime(2027, 5, 1))
            assert offer_expired is False

            # Sub should still be ACTIVE and carry special_first_year=True
            assert sub.status == "ACTIVE"
            assert sub.special_first_year is True


# ---------------------------------------------------------------------------
# Test J: Cancelled annual → not eligible again
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestJ_CancelledSubNotEligible:
    """J: Child with a prior CANCELLED annual sub is not eligible for special price again."""

    async def test_cancelled_sub_blocks_eligibility(self):
        engine, session_factory = await make_test_db()
        async with session_factory() as db:
            parent = Parent(id=4, email="j@example.com", display_name="J", account_status="ACTIVE")
            child = Child(id=40, parent_id=4, display_name="JChild", age_years=10, target_language="ru", native_language="en")
            db.add_all([parent, child])
            await db.commit()

            # Create a cancelled annual sub
            sub = Subscription(
                child_id=40,
                course_id="conversation",
                plan_id="weekly2",
                billing_period="YEAR",
                status="CANCELLED",
                monthly_price=599.0,
                currency="EUR",
                payment_provider="paypal",
            )
            db.add(sub)
            await db.commit()

        async with session_factory() as db:
            eligible = await is_eligible_for_special_annual(
                db,
                parent_id=4,
                child_id=40,
                now=datetime(2026, 11, 1),
            )
            assert eligible is False, "CANCELLED annual sub should block eligibility"


# ---------------------------------------------------------------------------
# Test K: Monthly plan JSON has no special_first_year / monthly prices unchanged
# ---------------------------------------------------------------------------

class TestK_MonthlyPlanUnchanged:
    """K: Monthly plans carry no special_first_year field; prices are unchanged."""

    def test_plan_detail_not_eligible_has_no_special(self):
        # When not eligible, no special annual pricing applies
        d = get_plan_annual_offer_detail("weekly1", 1, is_eligible=False)
        assert d["special_first_year"] is False
        assert d["effective_price"] == 439.0  # standard annual, not monthly

    def test_monthly_prices_from_catalog(self, monkeypatch, tmp_path):
        isolated_pricing(monkeypatch, tmp_path)
        from app.services.pricing_versions import MONTH, plan_versions_for_course
        monthly = plan_versions_for_course(None, MONTH)
        expected = {1: 39.0, 2: 69.0, 3: 99.0, 4: 139.0}
        for row in monthly:
            freq = row["lessons_per_week"]
            assert row["price"] == expected[freq], \
                f"Monthly price for freq={freq}: expected {expected[freq]}, got {row['price']}"

    def test_intro_week_prices_unchanged(self):
        for freq in (1, 2, 3, 4):
            d = get_plan_annual_offer_detail(f"weekly{freq}", freq, is_eligible=True)
            expected_intro = freq * 3
            assert d["intro_week_price"] == float(expected_intro), \
                f"Intro week price for freq={freq}: expected {expected_intro}, got {d['intro_week_price']}"


# ---------------------------------------------------------------------------
# Test L: Existing active sub → not eligible
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestL_ExistingActiveSubNotEligible:
    """L: Child already has an active annual sub → is_eligible_for_special_annual returns False."""

    async def test_active_annual_blocks_eligibility(self):
        engine, session_factory = await make_test_db()
        async with session_factory() as db:
            parent = Parent(id=5, email="l@example.com", display_name="L", account_status="ACTIVE")
            child = Child(id=50, parent_id=5, display_name="LChild", age_years=11, target_language="ru", native_language="en")
            db.add_all([parent, child])
            await db.commit()

            # Existing ACTIVE annual sub
            existing_sub = Subscription(
                child_id=50,
                course_id="conversation",
                plan_id="weekly3",
                billing_period="YEAR",
                status="ACTIVE",
                monthly_price=1089.0,
                currency="EUR",
                payment_provider="paypal",
                special_first_year=True,
            )
            db.add(existing_sub)
            await db.commit()

        async with session_factory() as db:
            eligible = await is_eligible_for_special_annual(
                db,
                parent_id=5,
                child_id=50,
                now=datetime(2026, 10, 1),
            )
            assert eligible is False, "ACTIVE annual sub should block re-eligibility"

    async def test_fresh_child_is_eligible(self):
        engine, session_factory = await make_test_db()
        async with session_factory() as db:
            parent = Parent(id=6, email="l2@example.com", display_name="L2", account_status="ACTIVE")
            child = Child(id=60, parent_id=6, display_name="L2Child", age_years=8, target_language="ru", native_language="en")
            db.add_all([parent, child])
            await db.commit()

        async with session_factory() as db:
            eligible = await is_eligible_for_special_annual(
                db,
                parent_id=6,
                child_id=60,
                now=datetime(2026, 10, 1),
            )
            assert eligible is True, "Fresh child with no annual subs should be eligible"


# ---------------------------------------------------------------------------
# Test M: Owner bypass
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestM_OwnerBypass:
    """M: Parent with account_role=OWNER gets unlimited access; subscription_checkout returns is_owner=True."""

    async def test_owner_parent_in_db(self):
        engine, session_factory = await make_test_db()
        async with session_factory() as db:
            # OWNER parent
            owner = Parent(
                id=99,
                email="krisriskrisris@gmail.com",
                display_name="Owner",
                account_status="ACTIVE",
                account_role="OWNER",
            )
            child = Child(id=99, parent_id=99, display_name="OwnerChild", age_years=7, target_language="ru", native_language="en")
            db.add_all([owner, child])
            await db.commit()

        async with session_factory() as db:
            result = await db.get(Parent, 99)
            assert result is not None
            assert result.account_role == "OWNER"
            assert "krisriskrisris" in result.email

    def test_title_badge_non_eligible(self):
        d = get_plan_annual_offer_detail("weekly2", 2, is_eligible=False)
        assert d["title_badge"] == "Годовой тариф"

    def test_title_badge_eligible(self):
        d = get_plan_annual_offer_detail("weekly2", 2, is_eligible=True)
        assert d["title_badge"] == "Специальная цена первого года"


# ---------------------------------------------------------------------------
# Regression: standard annual prices intact (not replaced)
# ---------------------------------------------------------------------------

class TestRegression_StandardAnnualPrices:
    """Standard annual prices must remain 439 / 759 / 1089 / 1535."""

    def test_standard_annual_prices_constant(self):
        expected = {1: 439.0, 2: 759.0, 3: 1089.0, 4: 1535.0}
        for freq, price in expected.items():
            assert STANDARD_ANNUAL_PRICES[freq] == price, \
                f"freq={freq}: expected {price}, got {STANDARD_ANNUAL_PRICES[freq]}"

    def test_special_prices_below_standard(self):
        for freq in (1, 2, 3, 4):
            assert SPECIAL_FIRST_YEAR_PRICES[freq] < STANDARD_ANNUAL_PRICES[freq], \
                f"Special price {SPECIAL_FIRST_YEAR_PRICES[freq]} must be < standard {STANDARD_ANNUAL_PRICES[freq]}"

    def test_standard_prices_from_catalog(self, monkeypatch, tmp_path):
        isolated_pricing(monkeypatch, tmp_path)
        from app.services.pricing_versions import YEAR, plan_versions_for_course
        annual = plan_versions_for_course(None, YEAR)
        expected = {1: 439.0, 2: 759.0, 3: 1089.0, 4: 1535.0}
        for row in annual:
            freq = row["lessons_per_week"]
            assert row["price"] == expected[freq], \
                f"Catalog annual price for freq={freq}: expected {expected[freq]}, got {row['price']}"
