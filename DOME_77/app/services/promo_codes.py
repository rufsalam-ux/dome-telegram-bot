from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Parent, PromoCode, PromoCodeUsage, Subscription


BENEFIT_PERCENTAGE = "PERCENTAGE"
BENEFIT_FIXED = "FIXED_AMOUNT"
BENEFIT_SPECIAL_PRICE = "SPECIAL_PRICE"
BENEFIT_FREE_PERIOD = "FREE_PERIOD_DAYS"
BENEFIT_EXTRA_LESSONS = "EXTRA_LESSONS"
BENEFIT_EXTRA_COURSE = "EXTRA_COURSE"
BENEFIT_TRIAL_OFFER = "TRIAL_OFFER"
BENEFIT_N_PERIODS_DISCOUNT = "N_PERIODS_DISCOUNT"
BENEFIT_PERMANENT_SPECIAL_PRICE = "PERMANENT_SPECIAL_PRICE"

SUPPORTED_BENEFIT_TYPES = {
    BENEFIT_PERCENTAGE,
    BENEFIT_FIXED,
    BENEFIT_SPECIAL_PRICE,
    BENEFIT_FREE_PERIOD,
    BENEFIT_EXTRA_LESSONS,
    BENEFIT_EXTRA_COURSE,
    BENEFIT_TRIAL_OFFER,
    BENEFIT_N_PERIODS_DISCOUNT,
    BENEFIT_PERMANENT_SPECIAL_PRICE,
}


@dataclass
class PromoValidationResult:
    valid: bool
    code: str
    error: str = ""
    benefit_type: str = ""
    benefit_value: float = 0.0
    description: str = ""
    original_price: float = 0.0
    discount_amount: float = 0.0
    final_price: float = 0.0
    currency: str = "EUR"
    trial_days: int = 0
    extra_lessons: int = 0
    extra_course: str = ""
    duration_periods: int | None = None
    is_permanent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_list(val: str | None) -> list[str]:
    if not val:
        return []
    val = val.strip()
    if val.startswith("["):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return [str(x).strip().lower() for x in parsed if str(x).strip()]
        except Exception:
            pass
    return [x.strip().lower() for x in val.split(",") if x.strip()]


async def validate_promo_code(
    db: AsyncSession,
    *,
    code: str,
    parent_id: int,
    child_id: int | None = None,
    plan_id: str = "",
    course_id: str = "",
    original_price: float = 0.0,
    currency: str = "EUR",
    now: datetime | None = None,
) -> PromoValidationResult:
    clean_code = str(code or "").strip().upper()
    if not clean_code:
        return PromoValidationResult(valid=False, code="", error="Введите промокод.")

    current_time = now or datetime.utcnow()

    promo = await db.scalar(
        select(PromoCode).where(
            func.upper(PromoCode.code) == clean_code,
            PromoCode.archived.is_(False),
        )
    )

    if promo is None:
        return PromoValidationResult(valid=False, code=clean_code, error="Промокод не найден.")

    if not promo.active:
        return PromoValidationResult(valid=False, code=clean_code, error="Промокод отключён.")

    if promo.valid_from and current_time < promo.valid_from:
        return PromoValidationResult(valid=False, code=clean_code, error="Промокод ещё не вступил в действие.")

    if promo.valid_until and current_time > promo.valid_until:
        return PromoValidationResult(valid=False, code=clean_code, error="Срок действия промокода истёк.")

    # Total usage count check
    if promo.max_uses is not None and promo.max_uses > 0:
        total_usages = await db.scalar(
            select(func.count(PromoCodeUsage.id)).where(PromoCodeUsage.promo_code_id == promo.id)
        )
        if (total_usages or 0) >= promo.max_uses:
            return PromoValidationResult(valid=False, code=clean_code, error="Лимит использований промокода исчерпан.")

    # Per-user usage count check
    if parent_id > 0 and promo.max_uses_per_user > 0:
        user_usages = await db.scalar(
            select(func.count(PromoCodeUsage.id)).where(
                PromoCodeUsage.promo_code_id == promo.id,
                PromoCodeUsage.parent_id == parent_id,
            )
        )
        if (user_usages or 0) >= promo.max_uses_per_user:
            return PromoValidationResult(valid=False, code=clean_code, error="Вы уже использовали этот промокод.")

    # Plan restriction
    allowed_plans = _parse_list(promo.allowed_plan_ids)
    if allowed_plans and plan_id:
        if plan_id.strip().lower() not in allowed_plans:
            return PromoValidationResult(
                valid=False,
                code=clean_code,
                error=f"Промокод не применим к тарифу {plan_id}.",
            )

    # Course restriction
    allowed_courses = _parse_list(promo.allowed_course_ids)
    if allowed_courses and course_id:
        if course_id.strip().lower() not in allowed_courses:
            return PromoValidationResult(
                valid=False,
                code=clean_code,
                error=f"Промокод не применим к курсу {course_id}.",
            )

    # New users only check
    if promo.new_users_only and parent_id > 0:
        existing_sub = await db.scalar(
            select(Subscription.id).join(Parent, Parent.id == parent_id).where(
                Subscription.status.in_(["ACTIVE", "CANCELLED", "PAST_DUE"])
            ).limit(1)
        )
        if existing_sub is not None:
            return PromoValidationResult(
                valid=False,
                code=clean_code,
                error="Промокод действует только для новых пользователей.",
            )

    # Calculation based on benefit_type
    b_type = str(promo.benefit_type or BENEFIT_PERCENTAGE).upper()
    b_val = float(promo.benefit_value or 0.0)
    orig = max(0.0, float(original_price))
    discount = 0.0
    final = orig
    trial_days = 0
    extra_lessons = 0
    extra_course = ""
    duration_periods = promo.duration_periods
    is_permanent = False
    description = promo.description or ""

    if b_type == BENEFIT_PERCENTAGE:
        pct = max(0.0, min(100.0, b_val))
        discount = round(orig * (pct / 100.0), 2)
        final = max(0.0, round(orig - discount, 2))
        if not description:
            description = f"Скидка {pct:.0f}%"

    elif b_type == BENEFIT_FIXED:
        discount = min(orig, round(b_val, 2))
        final = max(0.0, round(orig - discount, 2))
        if not description:
            description = f"Скидка {b_val:.2f} {promo.currency}"

    elif b_type == BENEFIT_SPECIAL_PRICE:
        final = max(0.0, round(b_val, 2))
        discount = max(0.0, round(orig - final, 2))
        if not description:
            description = f"Специальная цена {final:.2f} {promo.currency}"

    elif b_type == BENEFIT_FREE_PERIOD:
        trial_days = max(1, int(b_val))
        discount = orig
        final = 0.0
        if not description:
            description = f"Бесплатный период: {trial_days} дн."

    elif b_type == BENEFIT_EXTRA_LESSONS:
        extra_lessons = max(1, int(b_val))
        final = orig
        discount = 0.0
        if not description:
            description = f"+{extra_lessons} доп. уроков в подарок"

    elif b_type == BENEFIT_EXTRA_COURSE:
        extra_course = str(promo.description or promo.allowed_course_ids or "bonus_course")
        final = orig
        discount = 0.0
        if not description:
            description = f"Дополнительный курс в подарок"

    elif b_type == BENEFIT_TRIAL_OFFER:
        final = max(0.0, round(b_val, 2))
        discount = max(0.0, round(orig - final, 2))
        if not description:
            description = f"Пробный период за {final:.2f} {promo.currency}"

    elif b_type == BENEFIT_N_PERIODS_DISCOUNT:
        pct = max(0.0, min(100.0, b_val))
        discount = round(orig * (pct / 100.0), 2)
        final = max(0.0, round(orig - discount, 2))
        n = int(duration_periods or 1)
        if not description:
            description = f"Скидка {pct:.0f}% на первые {n} периода(ов)"

    elif b_type == BENEFIT_PERMANENT_SPECIAL_PRICE:
        final = max(0.0, round(b_val, 2))
        discount = max(0.0, round(orig - final, 2))
        is_permanent = True
        if not description:
            description = f"Постоянная закреплённая цена {final:.2f} {promo.currency}"

    else:
        return PromoValidationResult(valid=False, code=clean_code, error=f"Неизвестный тип промокода: {b_type}")

    return PromoValidationResult(
        valid=True,
        code=clean_code,
        benefit_type=b_type,
        benefit_value=b_val,
        description=description,
        original_price=orig,
        discount_amount=discount,
        final_price=final,
        currency=promo.currency or currency,
        trial_days=trial_days,
        extra_lessons=extra_lessons,
        extra_course=extra_course,
        duration_periods=duration_periods,
        is_permanent=is_permanent,
    )


async def record_promo_usage(
    db: AsyncSession,
    *,
    code: str,
    parent_id: int,
    child_id: int | None = None,
    plan_id: str = "",
    original_price: float = 0.0,
    final_price: float = 0.0,
    payment_reference: str = "",
) -> PromoCodeUsage | None:
    clean_code = str(code or "").strip().upper()
    if not clean_code:
        return None

    promo = await db.scalar(
        select(PromoCode).where(func.upper(PromoCode.code) == clean_code)
    )
    if promo is None:
        return None

    discount = max(0.0, round(float(original_price) - float(final_price), 2))
    usage = PromoCodeUsage(
        promo_code_id=promo.id,
        parent_id=parent_id,
        child_id=child_id,
        plan_id=plan_id,
        discount_amount=discount,
        original_price=float(original_price),
        final_price=float(final_price),
        payment_reference=payment_reference or None,
        used_at=datetime.utcnow(),
    )
    db.add(usage)
    await db.flush()
    return usage


async def list_promo_codes(db: AsyncSession, include_archived: bool = False) -> list[dict[str, Any]]:
    query = select(PromoCode)
    if not include_archived:
        query = query.where(PromoCode.archived.is_(False))
    query = query.order_by(PromoCode.created_at.desc())

    promos = (await db.scalars(query)).all()
    results = []
    for p in promos:
        usages_count = await db.scalar(
            select(func.count(PromoCodeUsage.id)).where(PromoCodeUsage.promo_code_id == p.id)
        )
        results.append({
            "id": p.id,
            "code": p.code,
            "benefit_type": p.benefit_type,
            "benefit_value": p.benefit_value,
            "currency": p.currency,
            "duration_periods": p.duration_periods,
            "valid_from": p.valid_from.isoformat() if p.valid_from else None,
            "valid_until": p.valid_until.isoformat() if p.valid_until else None,
            "max_uses": p.max_uses,
            "max_uses_per_user": p.max_uses_per_user,
            "allowed_plan_ids": _parse_list(p.allowed_plan_ids),
            "allowed_course_ids": _parse_list(p.allowed_course_ids),
            "new_users_only": bool(p.new_users_only),
            "active": bool(p.active),
            "archived": bool(p.archived),
            "description": p.description or "",
            "usage_count": int(usages_count or 0),
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })
    return results


async def create_promo_code(db: AsyncSession, data: dict[str, Any]) -> PromoCode:
    code = str(data.get("code") or "").strip().upper()
    if not code or len(code) < 2:
        raise ValueError("Код промокода должен содержать не менее 2 символов.")

    existing = await db.scalar(select(PromoCode).where(func.upper(PromoCode.code) == code))
    if existing is not None:
        raise ValueError(f"Промокод '{code}' уже существует.")

    b_type = str(data.get("benefit_type") or BENEFIT_PERCENTAGE).upper()
    if b_type not in SUPPORTED_BENEFIT_TYPES:
        raise ValueError(f"Неподдерживаемый тип выгоды: {b_type}")

    valid_from = None
    if data.get("valid_from"):
        try:
            valid_from = datetime.fromisoformat(str(data["valid_from"]).replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            pass

    valid_until = None
    if data.get("valid_until"):
        try:
            valid_until = datetime.fromisoformat(str(data["valid_until"]).replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            pass

    allowed_plans = data.get("allowed_plan_ids")
    if isinstance(allowed_plans, list):
        allowed_plans = json.dumps(allowed_plans)
    elif allowed_plans is not None:
        allowed_plans = str(allowed_plans)

    allowed_courses = data.get("allowed_course_ids")
    if isinstance(allowed_courses, list):
        allowed_courses = json.dumps(allowed_courses)
    elif allowed_courses is not None:
        allowed_courses = str(allowed_courses)

    promo = PromoCode(
        code=code,
        benefit_type=b_type,
        benefit_value=float(data.get("benefit_value") or 0.0),
        currency=str(data.get("currency") or "EUR").upper(),
        duration_periods=int(data["duration_periods"]) if data.get("duration_periods") else None,
        valid_from=valid_from,
        valid_until=valid_until,
        max_uses=int(data["max_uses"]) if data.get("max_uses") else None,
        max_uses_per_user=int(data.get("max_uses_per_user") or 1),
        allowed_plan_ids=allowed_plans,
        allowed_course_ids=allowed_courses,
        new_users_only=bool(data.get("new_users_only", False)),
        active=bool(data.get("active", True)),
        archived=False,
        description=str(data.get("description") or "").strip() or None,
    )
    db.add(promo)
    await db.commit()
    await db.refresh(promo)
    return promo


async def update_promo_code(db: AsyncSession, promo_id: int, data: dict[str, Any]) -> PromoCode:
    promo = await db.get(PromoCode, promo_id)
    if promo is None:
        raise ValueError("Промокод не найден.")

    if "benefit_type" in data:
        b_type = str(data["benefit_type"]).upper()
        if b_type in SUPPORTED_BENEFIT_TYPES:
            promo.benefit_type = b_type
    if "benefit_value" in data:
        promo.benefit_value = float(data["benefit_value"] or 0.0)
    if "description" in data:
        promo.description = str(data["description"] or "").strip() or None
    if "max_uses" in data:
        promo.max_uses = int(data["max_uses"]) if data["max_uses"] else None
    if "max_uses_per_user" in data:
        promo.max_uses_per_user = int(data["max_uses_per_user"] or 1)
    if "active" in data:
        promo.active = bool(data["active"])
    if "archived" in data:
        promo.archived = bool(data["archived"])
    if "new_users_only" in data:
        promo.new_users_only = bool(data["new_users_only"])
    if "valid_from" in data:
        if data["valid_from"]:
            try:
                promo.valid_from = datetime.fromisoformat(str(data["valid_from"]).replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                pass
        else:
            promo.valid_from = None
    if "valid_until" in data:
        if data["valid_until"]:
            try:
                promo.valid_until = datetime.fromisoformat(str(data["valid_until"]).replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                pass
        else:
            promo.valid_until = None

    await db.commit()
    await db.refresh(promo)
    return promo


async def toggle_promo_code(db: AsyncSession, promo_id: int) -> bool:
    promo = await db.get(PromoCode, promo_id)
    if promo is None:
        raise ValueError("Промокод не найден.")
    promo.active = not bool(promo.active)
    await db.commit()
    return promo.active


async def delete_promo_code(db: AsyncSession, promo_id: int, hard: bool = False) -> bool:
    promo = await db.get(PromoCode, promo_id)
    if promo is None:
        return False
    if hard:
        await db.delete(promo)
    else:
        promo.archived = True
        promo.active = False
    await db.commit()
    return True
