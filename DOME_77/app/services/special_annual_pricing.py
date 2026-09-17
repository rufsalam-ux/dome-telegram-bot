from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Child, CourseEnrollment, Subscription
from app.services.platform_settings import load_settings

log = logging.getLogger("dome.special_annual_pricing")

SPECIAL_ANNUAL_START_DEFAULT = "2026-09-17"
SPECIAL_ANNUAL_END_DEFAULT = "2027-04-17"

SPECIAL_FIRST_YEAR_PRICES: dict[int, float] = {
    1: 349.0,
    2: 599.0,
    3: 849.0,
    4: 1199.0,
}

STANDARD_ANNUAL_PRICES: dict[int, float] = {
    1: 439.0,
    2: 759.0,
    3: 1089.0,
    4: 1535.0,
}

INTRO_WEEK_PRICES: dict[int, float] = {
    1: 3.0,
    2: 6.0,
    3: 9.0,
    4: 12.0,
}


def _parse_date(val: Any) -> date | None:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    raw = str(val or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except (ValueError, TypeError):
        return None


def get_special_annual_config() -> dict[str, Any]:
    cfg = load_settings("pricing")
    offer = cfg.get("special_annual_offer")
    if isinstance(offer, dict):
        return offer
    return {
        "enabled": True,
        "start_date": SPECIAL_ANNUAL_START_DEFAULT,
        "end_date": SPECIAL_ANNUAL_END_DEFAULT,
        "eligibility": "new_annual_customers_only",
        "plans": {
            f"weekly{freq}": {
                "first_year_price": SPECIAL_FIRST_YEAR_PRICES[freq],
                "standard_renewal_price": STANDARD_ANNUAL_PRICES[freq],
            }
            for freq in (1, 2, 3, 4)
        },
    }


def is_special_annual_offer_active(now: datetime | None = None) -> bool:
    """Checks whether the special annual offer is active based on server time."""
    cfg = get_special_annual_config()
    if not bool(cfg.get("enabled", True)):
        return False
    start_d = _parse_date(cfg.get("start_date") or SPECIAL_ANNUAL_START_DEFAULT)
    end_d = _parse_date(cfg.get("end_date") or SPECIAL_ANNUAL_END_DEFAULT)
    point = (now.date() if isinstance(now, datetime) else date.today()) if now else datetime.utcnow().date()
    if start_d and point < start_d:
        return False
    if end_d and point > end_d:
        return False
    return True


async def is_eligible_for_special_annual(
    db: AsyncSession,
    *,
    parent_id: int,
    child_id: int,
    now: datetime | None = None,
) -> bool:
    """Server-side eligibility check.
    
    1. Offer must be currently active (date window: 2026-09-17 to 2027-04-17).
    2. Must be a new annual customer: parent or child must NOT have any previous
       active, completed, or historical annual subscription (one-time special rule).
    """
    if not is_special_annual_offer_active(now):
        return False

    # Check child's subscriptions
    child_subs = (
        await db.scalars(
            select(Subscription).where(
                Subscription.child_id == child_id,
                Subscription.billing_period == "YEAR",
            )
        )
    ).all()

    for sub in child_subs:
        st = str(sub.status or "").upper()
        # If the user ever had an annual subscription that was ACTIVE, PAST_DUE, or CANCELLED,
        # they have already used their first-year benefit.
        if st in {"ACTIVE", "PAST_DUE", "CANCELLED"} or sub.current_period_start is not None:
            return False

    # Also check other children of the same parent for family safety
    if parent_id > 0:
        sibling_ids = (
            await db.scalars(select(Child.id).where(Child.parent_id == parent_id))
        ).all()
        if sibling_ids:
            sibling_subs = (
                await db.scalars(
                    select(Subscription).where(
                        Subscription.child_id.in_(sibling_ids),
                        Subscription.billing_period == "YEAR",
                    )
                )
            ).all()
            for sub in sibling_subs:
                st = str(sub.status or "").upper()
                if getattr(sub, "special_first_year", False):
                    # Parent already claimed special first year on a child
                    return False

    return True


def get_plan_annual_offer_detail(plan_id: str, lessons_per_week: int, is_eligible: bool) -> dict[str, Any]:
    """Generates display details, pricing, and required renewal disclosure text."""
    freq = max(1, min(4, int(lessons_per_week or 1)))
    cfg = get_special_annual_config()
    plans_cfg = cfg.get("plans", {})
    plan_entry = plans_cfg.get(plan_id, {})

    std_renewal = float(plan_entry.get("standard_renewal_price") or STANDARD_ANNUAL_PRICES.get(freq, 439.0))
    first_year = float(plan_entry.get("first_year_price") or SPECIAL_FIRST_YEAR_PRICES.get(freq, 349.0))
    intro_price = INTRO_WEEK_PRICES.get(freq, float(freq * 3))

    if is_eligible:
        effective_price = first_year
        savings = round(std_renewal - first_year, 2)
        has_special = True
    else:
        effective_price = std_renewal
        savings = 0.0
        has_special = False

    disclosure = (
        f"Первый год: €{int(first_year) if first_year.is_integer() else first_year:.2f}. "
        f"Затем подписка автоматически продлевается за €{int(std_renewal) if std_renewal.is_integer() else std_renewal:.2f} в год до отмены."
    )

    return {
        "plan_id": plan_id,
        "lessons_per_week": freq,
        "special_first_year": has_special,
        "effective_price": effective_price,
        "first_year_price": first_year,
        "standard_renewal_price": std_renewal,
        "annual_savings": savings,
        "intro_week_price": intro_price,
        "renewal_disclosure": disclosure,
        "title_badge": "Специальная цена первого года" if has_special else "Годовой тариф",
    }
