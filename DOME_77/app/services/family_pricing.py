from __future__ import annotations

from dataclasses import dataclass

MAX_CHILDREN_PER_PARENT = 5
ADDITIONAL_CHILD_DISCOUNT_PER_LESSON_EUR = 0.50
BILLING_WEEKS_PER_MONTH = 4

@dataclass(frozen=True)
class FamilyPrice:
    base_price: float
    effective_price: float
    child_position: int
    discount: float
    discount_per_lesson: float = 0.0
    lessons_per_month: int = 0


def calculate_family_price(
    base_price: float,
    child_position: int,
    lessons_per_week: int = 1,
    additional_child_discount_per_lesson_eur: float = ADDITIONAL_CHILD_DISCOUNT_PER_LESSON_EUR,
    billing_weeks_per_month: int = BILLING_WEEKS_PER_MONTH,
) -> FamilyPrice:
    """Calculate family price for a monthly subscription.

    Business rule v71: child #1 pays the full monthly plan price. Children #2-#5
    get EUR 0.50 off *each scheduled lesson*. DOME uses 4 billing weeks for the
    monthly family discount, matching the plan presentation (1/2/3/4 lessons a
    week billed monthly). Both values are admin-configurable in pricing.json.
    """
    position=max(1,min(MAX_CHILDREN_PER_PARENT,int(child_position or 1)))
    freq=max(1,min(4,int(lessons_per_week or 1)))
    weeks=max(1,int(billing_weeks_per_month or BILLING_WEEKS_PER_MONTH))
    per_lesson=0.0 if position == 1 else max(0.0,float(additional_child_discount_per_lesson_eur or 0.0))
    lesson_count=freq*weeks
    discount=round(per_lesson*lesson_count,2)
    effective=max(0.0, round(float(base_price)-discount, 2))
    return FamilyPrice(float(base_price), effective, position, discount, per_lesson, lesson_count)


async def child_position_in_family(parent_id:int, child_id:int) -> int:
    from sqlalchemy import select
    from app.db.models import Child
    from app.db.session import SessionLocal
    async with SessionLocal() as db:
        ids=list((await db.scalars(select(Child.id).where(Child.parent_id==parent_id).order_by(Child.id.asc()))).all())
    try:
        return ids.index(int(child_id))+1
    except ValueError:
        return max(1,len(ids)+1)


async def family_price_for_child(parent_id:int, child_id:int, base_price:float, lessons_per_week:int=1) -> FamilyPrice:
    from app.services.platform_settings import load_settings
    family=(load_settings("pricing").get("family") or {})
    discount=float(family.get("additional_child_discount_per_lesson_eur",ADDITIONAL_CHILD_DISCOUNT_PER_LESSON_EUR) or 0.0)
    weeks=int(family.get("billing_weeks_per_month",BILLING_WEEKS_PER_MONTH) or BILLING_WEEKS_PER_MONTH)
    return calculate_family_price(base_price, await child_position_in_family(parent_id,child_id), lessons_per_week, discount, weeks)


async def family_child_count(parent_id:int) -> int:
    from sqlalchemy import select
    from app.db.models import Child
    from app.db.session import SessionLocal
    async with SessionLocal() as db:
        ids=(await db.scalars(select(Child.id).where(Child.parent_id==parent_id))).all()
    return len(ids)
