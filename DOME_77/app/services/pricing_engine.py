from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Mapping

from app.services.platform_settings import load_settings
from app.services.pricing_versions import MONTH, plan_versions_for_course, set_plan_price


@dataclass(frozen=True)
class PriceQuote:
    customer_price: float
    estimated_cost: float
    markup: float
    quantity: int
    currency: str


def _money(value: float, step: float = 0.01) -> float:
    q = Decimal(str(step))
    return float(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))


def estimate_lesson_cost(service_costs: Mapping[str, float]) -> float:
    cfg = load_settings("pricing")
    services = ((cfg.get("cost_engine") or {}).get("services") or {})
    total = 0.0
    for key, spec in services.items():
        if not spec.get("enabled", True):
            continue
        mode = spec.get("mode", "automatic")
        if mode == "fixed":
            total += float(spec.get("fixed_cost") or 0.0)
        else:
            total += float(service_costs.get(key, 0.0))
    return total


def quote_interest_lessons(service_costs: Mapping[str, float], quantity: int = 1) -> PriceQuote:
    if quantity < 1:
        raise ValueError("quantity must be >= 1")
    cfg = load_settings("pricing")
    p = cfg["interest_lessons"]
    cost_one = estimate_lesson_cost(service_costs)
    buffered = cost_one * (1.0 + float(p.get("cost_buffer_percent", 0.0)) / 100.0)
    profit = float(p.get("profit_per_lesson", 30.0))
    step = float(p.get("round_to", 0.01))
    unit = _money(buffered + profit, step)
    return PriceQuote(
        customer_price=_money(unit * quantity, step),
        estimated_cost=_money(cost_one * quantity, step),
        markup=_money(profit * quantity, step),
        quantity=quantity,
        currency=cfg.get("currency", "USD"),
    )


def quote_regular_period(estimated_cost_per_lesson: float, lesson_count: int, annual: bool = False) -> PriceQuote:
    if lesson_count < 0:
        raise ValueError("lesson_count must be >= 0")
    cfg = load_settings("pricing")
    p = cfg["regular_course"]
    markup = float(p["annual_markup_per_lesson"] if annual else p["monthly_markup_per_lesson"])
    minimum = float(p["annual_minimum"] if annual else p["monthly_minimum"])
    raw = (float(estimated_cost_per_lesson) + markup) * lesson_count
    total = max(minimum, raw)
    return PriceQuote(
        customer_price=_money(total),
        estimated_cost=_money(float(estimated_cost_per_lesson) * lesson_count),
        markup=_money(markup * lesson_count),
        quantity=lesson_count,
        currency=cfg.get("currency", "USD"),
    )


def subscription_plans_for_course(course_id: str | None = None) -> list[dict]:
    """Compatibility monthly catalog backed by immutable active price versions."""
    return [
        {
            "id": str(row.get("plan_id") or ""),
            "plan_id": str(row.get("plan_id") or ""),
            "version_id": str(row.get("version_id") or ""),
            "lessons_per_week": int(row.get("lessons_per_week") or 1),
            "monthly_price": float(row.get("price") or 0.0),
            "currency": str(row.get("currency") or "EUR").upper(),
            "billing_period": MONTH,
        }
        for row in plan_versions_for_course(course_id, MONTH)
    ]

def set_course_plan_price(course_id: str, lessons_per_week: int, monthly_price: float) -> dict:
    set_plan_price(
        course_id=course_id,
        lessons_per_week=lessons_per_week,
        billing_period=MONTH,
        price=monthly_price,
        created_by="admin:legacy-course-command",
    )
    return load_settings("pricing")
