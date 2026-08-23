from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.services.platform_settings import load_settings, save_settings


MONTH = "MONTH"
YEAR = "YEAR"
SUPPORTED_PERIODS = {MONTH, YEAR}

DEFAULT_MONTHLY_PRICES = {1: 39.0, 2: 69.0, 3: 99.0, 4: 139.0}
DEFAULT_ANNUAL_PRICES = {1: 429.0, 2: 759.0, 3: 1089.0, 4: 1536.0}
LEGACY_CODE_PRICES = {1: 39.0, 2: 79.0, 3: 109.0, 4: 139.0}
DEFAULT_PROFITABILITY = {
    "hide_unprofitable_plans": True,
    "estimated_cost_per_lesson": 0.0,
    "fixed_cost_per_period": 0.0,
    "minimum_margin_percent": 0.0,
    "billing_weeks_per_month": 4,
    "billing_weeks_per_year": 52,
}


class PricingVersionError(ValueError):
    pass


def normalize_billing_period(value: object) -> str:
    raw = str(value or MONTH).strip().upper()
    aliases = {"MONTHLY": MONTH, "ANNUAL": YEAR, "YEARLY": YEAR}
    result = aliases.get(raw, raw)
    if result not in SUPPORTED_PERIODS:
        raise PricingVersionError("billing_period должен быть MONTH или YEAR")
    return result


def _scope(value: str | None) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value or "global").lower()).strip("-")
    return cleaned or "global"


def _created_at() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _record(
    *,
    scope: str,
    plan_id: str,
    lessons_per_week: int,
    billing_period: str,
    price: float,
    currency: str,
    version: int,
    created_by: str,
) -> dict[str, Any]:
    period = normalize_billing_period(billing_period)
    return {
        "version_id": f"{_scope(scope)}-{plan_id}-{period.lower()}-v{int(version)}",
        "plan_id": str(plan_id),
        "version": int(version),
        "lessons_per_week": int(lessons_per_week),
        "billing_period": period,
        "price": round(float(price), 2),
        "currency": str(currency or "EUR").upper(),
        "active": True,
        "created_at": _created_at(),
        "created_by": str(created_by or "system"),
    }


def _legacy_prices(plans: list[dict[str, Any]]) -> dict[int, float]:
    result: dict[int, float] = {}
    for plan in plans:
        try:
            result[int(plan.get("lessons_per_week") or 0)] = float(plan.get("monthly_price"))
        except (TypeError, ValueError):
            continue
    return result


def _seed_versions(
    *,
    scope: str,
    legacy_plans: list[dict[str, Any]],
    currency: str,
    use_new_business_defaults: bool,
) -> list[dict[str, Any]]:
    legacy = _legacy_prices(legacy_plans)
    monthly = DEFAULT_MONTHLY_PRICES if use_new_business_defaults else {
        frequency: float(legacy.get(frequency, default))
        for frequency, default in DEFAULT_MONTHLY_PRICES.items()
    }
    versions: list[dict[str, Any]] = []
    for period, prices in ((MONTH, monthly), (YEAR, DEFAULT_ANNUAL_PRICES)):
        for frequency, price in prices.items():
            versions.append(_record(
                scope=scope,
                plan_id=f"weekly{frequency}",
                lessons_per_week=frequency,
                billing_period=period,
                price=price,
                currency=currency,
                version=1,
                created_by="system:versioned-pricing-migration",
            ))
    return versions


def _active(records: list[dict[str, Any]], period: str | None = None) -> list[dict[str, Any]]:
    wanted = normalize_billing_period(period) if period else None
    rows = [
        deepcopy(row) for row in records
        if bool(row.get("active", True)) and (wanted is None or normalize_billing_period(row.get("billing_period")) == wanted)
    ]
    return sorted(rows, key=lambda row: (normalize_billing_period(row.get("billing_period")), int(row.get("lessons_per_week") or 0)))


def _sync_legacy_catalog(container: dict[str, Any], currency: str) -> None:
    monthly = _active(list(container.get("subscription_plan_versions") or []), MONTH)
    annual = {
        int(row.get("lessons_per_week") or 0): float(row.get("price") or 0.0)
        for row in _active(list(container.get("subscription_plan_versions") or []), YEAR)
    }
    container["subscription_plans"] = [
        {
            "id": str(row.get("plan_id") or f"weekly{row.get('lessons_per_week')}"),
            "lessons_per_week": int(row.get("lessons_per_week") or 1),
            "monthly_price": round(float(row.get("price") or 0.0), 2),
            "annual_price": round(float(annual.get(int(row.get("lessons_per_week") or 0), 0.0)), 2),
            "currency": str(row.get("currency") or currency).upper(),
            "monthly_version_id": str(row.get("version_id") or ""),
        }
        for row in monthly
    ]


def migrate_pricing_config(config: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    cfg = deepcopy(config)
    changed = False
    currency = str(cfg.get("currency") or "EUR").upper()
    regular = cfg.setdefault("regular_course", {})
    if not isinstance(regular.get("profitability"), dict):
        regular["profitability"] = deepcopy(DEFAULT_PROFITABILITY)
        changed = True
    else:
        for key, value in DEFAULT_PROFITABILITY.items():
            if key not in regular["profitability"]:
                regular["profitability"][key] = value
                changed = True
    existing = list(regular.get("subscription_plan_versions") or [])
    if not existing:
        legacy = list(regular.get("subscription_plans") or [])
        legacy_prices = _legacy_prices(legacy)
        use_defaults = all(abs(legacy_prices.get(k, v) - v) < 0.001 for k, v in LEGACY_CODE_PRICES.items())
        regular["subscription_plan_versions"] = _seed_versions(
            scope="global", legacy_plans=legacy, currency=currency, use_new_business_defaults=use_defaults
        )
        changed = True

    for course_id, raw in list((cfg.get("course_prices") or {}).items()):
        if not isinstance(raw, dict) or raw.get("subscription_plan_versions"):
            continue
        legacy = list(raw.get("subscription_plans") or [])
        if not legacy:
            continue
        legacy_prices = _legacy_prices(legacy)
        use_defaults = all(abs(legacy_prices.get(k, v) - v) < 0.001 for k, v in LEGACY_CODE_PRICES.items())
        raw["subscription_plan_versions"] = _seed_versions(
            scope=str(course_id), legacy_plans=legacy, currency=currency, use_new_business_defaults=use_defaults
        )
        changed = True

    if str(cfg.get("schema_version") or "") != "2.0":
        cfg["schema_version"] = "2.0"
        changed = True
    before = deepcopy(regular.get("subscription_plans") or [])
    _sync_legacy_catalog(regular, currency)
    changed = changed or before != regular.get("subscription_plans")
    for raw in (cfg.get("course_prices") or {}).values():
        if isinstance(raw, dict) and raw.get("subscription_plan_versions"):
            before = deepcopy(raw.get("subscription_plans") or [])
            _sync_legacy_catalog(raw, currency)
            changed = changed or before != raw.get("subscription_plans")
    return cfg, changed


def ensure_versioned_pricing_config() -> dict[str, Any]:
    cfg, changed = migrate_pricing_config(load_settings("pricing"))
    return save_settings("pricing", cfg) if changed else cfg


def profitability_config(course_id: str | None = None) -> dict[str, Any]:
    cfg = ensure_versioned_pricing_config()
    regular = cfg.get("regular_course") or {}
    course = ((cfg.get("course_prices") or {}).get(str(course_id)) or {}) if course_id else {}
    result = dict(regular.get("profitability") or {})
    result.update(course.get("profitability") or {})
    return result


def minimum_viable_price(plan: dict[str, Any], course_id: str | None = None) -> float:
    guard = profitability_config(course_id)
    period = normalize_billing_period(plan.get("billing_period"))
    weeks = int(guard.get("billing_weeks_per_year", 52) or 52) if period == YEAR else int(guard.get("billing_weeks_per_month", 4) or 4)
    lessons = max(1, int(plan.get("lessons_per_week") or 1)) * max(1, weeks)
    cost = float(guard.get("estimated_cost_per_lesson", 0.0) or 0.0) * lessons
    cost += float(guard.get("fixed_cost_per_period", 0.0) or 0.0)
    margin = min(99.0, max(0.0, float(guard.get("minimum_margin_percent", 0.0) or 0.0)))
    # Margin is profit / revenue, not a markup on cost. For a 20% requested
    # margin, revenue must therefore be cost / 0.8.
    return round(cost / (1.0 - margin / 100.0), 2)


def is_plan_profitable(plan: dict[str, Any], *, effective_price: float | None = None, course_id: str | None = None) -> bool:
    guard = profitability_config(course_id)
    if not bool(guard.get("hide_unprofitable_plans", True)):
        return True
    amount = float(plan.get("price") or 0.0) if effective_price is None else float(effective_price)
    return amount + 0.001 >= minimum_viable_price(plan, course_id)


def plan_versions_for_course(course_id: str | None = None, billing_period: str | None = None, *, include_unprofitable: bool = False) -> list[dict[str, Any]]:
    cfg = ensure_versioned_pricing_config()
    regular = cfg.get("regular_course") or {}
    course = ((cfg.get("course_prices") or {}).get(str(course_id)) or {}) if course_id else {}
    records = list(course.get("subscription_plan_versions") or regular.get("subscription_plan_versions") or [])
    active = _active(records, billing_period)
    return active if include_unprofitable else [row for row in active if is_plan_profitable(row, course_id=course_id)]


def get_plan_version(version_id: str) -> dict[str, Any] | None:
    cfg = ensure_versioned_pricing_config()
    containers = [cfg.get("regular_course") or {}] + [
        row for row in (cfg.get("course_prices") or {}).values() if isinstance(row, dict)
    ]
    for container in containers:
        for record in container.get("subscription_plan_versions") or []:
            if str(record.get("version_id") or "") == str(version_id):
                return deepcopy(record)
    return None


def set_plan_price(
    *,
    lessons_per_week: int,
    billing_period: str,
    price: float,
    course_id: str | None = None,
    created_by: str = "admin",
) -> dict[str, Any]:
    frequency = int(lessons_per_week)
    if frequency not in {1, 2, 3, 4}:
        raise PricingVersionError("Частота должна быть от 1 до 4 уроков в неделю")
    amount = round(float(price), 2)
    if amount <= 0:
        raise PricingVersionError("Цена должна быть больше нуля")
    period = normalize_billing_period(billing_period)
    cfg = ensure_versioned_pricing_config()
    currency = str(cfg.get("currency") or "EUR").upper()
    regular = cfg.setdefault("regular_course", {})
    if course_id:
        container = cfg.setdefault("course_prices", {}).setdefault(str(course_id), {})
        if not container.get("subscription_plan_versions"):
            container["subscription_plan_versions"] = [
                {**deepcopy(row), "version_id": f"{_scope(course_id)}-{row['plan_id']}-{str(row['billing_period']).lower()}-v1", "version": 1}
                for row in _active(list(regular.get("subscription_plan_versions") or []))
            ]
    else:
        container = regular
    records = list(container.get("subscription_plan_versions") or [])
    plan_id = f"weekly{frequency}"
    matching = [
        row for row in records
        if str(row.get("plan_id") or "") == plan_id and normalize_billing_period(row.get("billing_period")) == period
    ]
    version = max([int(row.get("version") or 0) for row in matching] or [0]) + 1
    for row in matching:
        row["active"] = False
        row.setdefault("superseded_at", _created_at())
    created = _record(
        scope=str(course_id or "global"), plan_id=plan_id, lessons_per_week=frequency,
        billing_period=period, price=amount, currency=currency, version=version, created_by=created_by,
    )
    records.append(created)
    container["subscription_plan_versions"] = records
    _sync_legacy_catalog(container, currency)
    save_settings("pricing", cfg)
    return deepcopy(created)
