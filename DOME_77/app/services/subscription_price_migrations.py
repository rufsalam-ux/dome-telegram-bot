from __future__ import annotations

from sqlalchemy import select

from app.db.models import Subscription
from app.db.session import SessionLocal
from app.services.platform_settings import load_settings
from app.services.pricing_versions import MONTH, normalize_billing_period


def _mapped_provider_plan_id(sub: Subscription, payments: dict) -> str:
    provider = str(sub.payment_provider or "").lower()
    version_id = str(sub.current_plan_version_id or "")
    period = normalize_billing_period(sub.billing_period or MONTH)
    currency = str(sub.currency or "EUR").upper()
    price = round(float(sub.current_plan_price if sub.current_plan_price is not None else sub.monthly_price or 0.0), 2)
    if provider == "paypal":
        for entry in (payments.get("paypal_plan_versions") or {}).values():
            if not isinstance(entry, dict):
                continue
            if (
                str(entry.get("plan_version_id") or "") == version_id
                and str(entry.get("billing_period") or MONTH).upper() == period
                and str(entry.get("currency") or "EUR").upper() == currency
                and abs(float(entry.get("price") or 0.0) - price) < 0.001
            ):
                return str(entry.get("paypal_plan_id") or "")
        legacy_key = f"{sub.current_plan_id or sub.plan_id}:{currency}:{price:.2f}"
        return str((payments.get("paypal_plan_cache") or {}).get(legacy_key) or "")
    if provider == "stripe":
        cache = payments.get("stripe_price_cache") or {}
        keys = [
            f"{version_id}:{period}:{currency}:{price:.2f}",
            f"{sub.current_plan_id or sub.plan_id}:{currency}:{price:.2f}",
        ]
        return next((str(cache.get(key) or "") for key in keys if cache.get(key)), "")
    return ""


async def backfill_subscription_provider_plan_ids() -> int:
    """Best-effort snapshot migration using only already persisted provider mappings."""
    payments = load_settings("payments")
    changed = 0
    async with SessionLocal() as db:
        rows = list((await db.scalars(select(Subscription).where(Subscription.provider_plan_id.is_(None)))).all())
        for sub in rows:
            provider_plan_id = _mapped_provider_plan_id(sub, payments)
            if provider_plan_id:
                sub.provider_plan_id = provider_plan_id
                changed += 1
        if changed:
            await db.commit()
    return changed
