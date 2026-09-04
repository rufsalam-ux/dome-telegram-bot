from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TariffPlan

log = logging.getLogger("dome.tariff_plans")

DEFAULT_TARIFFS = [
    {
        "plan_id": "start",
        "legacy_code": "weekly1",
        "name": "DOME Start",
        "description": "Базовый тариф для плавного старта и регулярной практики.",
        "lessons_per_week": 1,
        "lessons_per_month": 4,
        "lessons_per_year": 48,
        "monthly_price": 39.0,
        "annual_price": 399.0,
        "currency": "EUR",
        "active": True,
        "visible": True,
        "display_order": 1,
    },
    {
        "plan_id": "smart",
        "legacy_code": "weekly2",
        "name": "DOME Smart",
        "description": "Оптимальный темп: 2 занятия в неделю для уверенного прогресса.",
        "lessons_per_week": 2,
        "lessons_per_month": 8,
        "lessons_per_year": 96,
        "monthly_price": 69.0,
        "annual_price": 699.0,
        "currency": "EUR",
        "active": True,
        "visible": True,
        "display_order": 2,
    },
    {
        "plan_id": "plus",
        "legacy_code": "weekly3",
        "name": "DOME Plus",
        "description": "Интенсивный курс: 3 занятия в неделю для быстрого преодоления языкового барьера.",
        "lessons_per_week": 3,
        "lessons_per_month": 12,
        "lessons_per_year": 144,
        "monthly_price": 99.0,
        "annual_price": 999.0,
        "currency": "EUR",
        "active": True,
        "visible": True,
        "display_order": 3,
    },
    {
        "plan_id": "max",
        "legacy_code": "weekly4",
        "name": "DOME Max",
        "description": "Максимальное погружение: 4 занятия в неделю для билингвов и углублённого обучения.",
        "lessons_per_week": 4,
        "lessons_per_month": 16,
        "lessons_per_year": 192,
        "monthly_price": 129.0,
        "annual_price": 1299.0,
        "currency": "EUR",
        "active": True,
        "visible": True,
        "display_order": 4,
    },
]


async def seed_default_tariffs(db: AsyncSession) -> None:
    for item in DEFAULT_TARIFFS:
        existing = await db.scalar(
            select(TariffPlan).where(TariffPlan.plan_id == item["plan_id"])
        )
        if not existing:
            plan = TariffPlan(**item)
            db.add(plan)
        else:
            # Keep prices synchronized if default
            if existing.annual_price != item["annual_price"]:
                existing.annual_price = item["annual_price"]
            if existing.monthly_price != item["monthly_price"]:
                existing.monthly_price = item["monthly_price"]
            if existing.name != item["name"]:
                existing.name = item["name"]
    await db.commit()


def _tariff_dict(p: TariffPlan) -> dict[str, Any]:
    # Calculate savings for yearly billing
    annual_as_monthly = round(p.annual_price / 12.0, 2)
    monthly_annual_total = p.monthly_price * 12.0
    yearly_savings = max(0.0, round(monthly_annual_total - p.annual_price, 2))

    return {
        "id": p.id,
        "plan_id": p.plan_id,
        "legacy_code": p.legacy_code,
        "name": p.name,
        "description": p.description or "",
        "lessons_per_week": p.lessons_per_week,
        "lessons_per_month": p.lessons_per_month,
        "lessons_per_year": p.lessons_per_year,
        "monthly_price": p.monthly_price,
        "annual_price": p.annual_price,
        "annual_as_monthly": annual_as_monthly,
        "yearly_savings": yearly_savings,
        "currency": p.currency,
        "active": p.active,
        "visible": p.visible,
        "display_order": p.display_order,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


async def get_active_tariffs(db: AsyncSession) -> list[dict[str, Any]]:
    await seed_default_tariffs(db)
    query = (
        select(TariffPlan)
        .where(TariffPlan.active.is_(True), TariffPlan.visible.is_(True))
        .order_by(TariffPlan.display_order.asc())
    )
    plans = (await db.scalars(query)).all()
    return [_tariff_dict(p) for p in plans]


async def list_all_tariffs(db: AsyncSession) -> list[dict[str, Any]]:
    await seed_default_tariffs(db)
    query = select(TariffPlan).order_by(TariffPlan.display_order.asc())
    plans = (await db.scalars(query)).all()
    return [_tariff_dict(p) for p in plans]


async def get_tariff_by_id(db: AsyncSession, plan_id: str) -> TariffPlan | None:
    norm = str(plan_id or "").strip().lower()
    return await db.scalar(
        select(TariffPlan).where(
            (TariffPlan.plan_id == norm) | (TariffPlan.legacy_code == norm)
        )
    )


async def update_tariff(db: AsyncSession, plan_id: str, data: dict[str, Any]) -> TariffPlan:
    plan = await get_tariff_by_id(db, plan_id)
    if not plan:
        raise ValueError(f"Тариф {plan_id} не найден.")

    if "name" in data:
        plan.name = str(data["name"]).strip()
    if "description" in data:
        plan.description = str(data["description"]).strip() or None
    if "monthly_price" in data:
        plan.monthly_price = float(data["monthly_price"])
    if "annual_price" in data:
        plan.annual_price = float(data["annual_price"])
    if "currency" in data:
        plan.currency = str(data["currency"]).strip().upper()
    if "active" in data:
        plan.active = bool(data["active"])
    if "visible" in data:
        plan.visible = bool(data["visible"])
    if "display_order" in data:
        plan.display_order = int(data["display_order"])
    plan.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(plan)
    return plan
