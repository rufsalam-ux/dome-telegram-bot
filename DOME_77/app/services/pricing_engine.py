from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Mapping

from app.services.platform_settings import load_settings


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
    cfg = load_settings("pricing")
    regular = cfg.get("regular_course") or {}
    default = [dict(x) for x in (regular.get("subscription_plans") or [])]
    if not course_id:
        return default
    overrides = ((cfg.get("course_prices") or {}).get(str(course_id)) or {}).get("subscription_plans")
    return [dict(x) for x in overrides] if overrides else default

def set_course_plan_price(course_id: str, lessons_per_week: int, monthly_price: float) -> dict:
    from app.services.platform_settings import save_settings
    cfg = load_settings("pricing")
    freq=max(1,min(4,int(lessons_per_week)))
    price=float(monthly_price)
    if price <= 0: raise ValueError("price must be > 0")
    cp=cfg.setdefault("course_prices",{}).setdefault(str(course_id),{})
    plans=cp.setdefault("subscription_plans", subscription_plans_for_course(None))
    found=False
    for p in plans:
        if int(p.get("lessons_per_week",0))==freq:
            p["id"]=str(p.get("id") or f"weekly{freq}"); p["monthly_price"]=price; found=True
    if not found: plans.append({"id":f"weekly{freq}","lessons_per_week":freq,"monthly_price":price})
    save_settings("pricing",cfg)
    return cfg
