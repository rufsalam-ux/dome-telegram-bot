from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.db.models import Subscription
from app.services.subscription_plan_changes import PlanSnapshot


class SubscriptionProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderPlanChangeResult:
    status: str
    reference: str = ""
    approval_url: str = ""


async def schedule_provider_plan_change(
    sub: Subscription,
    target: PlanSnapshot,
    *,
    effective_at: datetime,
    base_url: str,
    idempotency_key: str,
) -> ProviderPlanChangeResult:
    """Schedule the provider's fixed next-period amount without proration."""
    if sub.test_mode:
        raise SubscriptionProviderError("Тестовую подписку нельзя изменить через production billing")
    provider = str(sub.payment_provider or "").lower()
    subscription_id = str(sub.provider_subscription_id or "")
    if not subscription_id:
        raise SubscriptionProviderError("У подписки нет provider subscription id")
    common = dict(
        subscription_id=subscription_id,
        child_id=sub.child_id,
        course_id=sub.course_id,
        plan_id=target.plan_id,
        lessons_per_week=target.lessons_per_week,
        monthly_price=target.price,
        currency=target.currency,
        idempotency_key=idempotency_key,
    )
    if provider == "stripe":
        from app.services.payment_adapter import change_stripe_subscription_plan

        result = change_stripe_subscription_plan(**common)
        return ProviderPlanChangeResult(status="SCHEDULED", reference=str(result.get("id") or subscription_id))
    if provider == "paypal":
        from app.services.paypal_adapter import change_paypal_subscription_plan

        result = await change_paypal_subscription_plan(
            **common,
            success_url=base_url.rstrip("/") + "/payment/success",
            cancel_url=base_url.rstrip("/") + "/payment/cancel",
        )
        approval = str(result.get("approval_url") or "")
        return ProviderPlanChangeResult(
            status="PENDING_APPROVAL" if approval else "SCHEDULED",
            reference=str(result.get("id") or subscription_id),
            approval_url=approval,
        )
    if provider in {"unipay", "unlimit"}:
        # Merchant-specific APIs in this repository do not expose a documented,
        # verifiable next-period-only contract. Refuse an unsafe immediate update.
        raise SubscriptionProviderError(
            f"{provider} не настроен для гарантированной смены с начала следующего периода без proration"
        )
    raise SubscriptionProviderError("Платёжный провайдер не поддерживает безопасную смену тарифа")


async def restore_provider_current_plan(
    sub: Subscription,
    current: PlanSnapshot,
    *,
    effective_at: datetime,
    base_url: str,
    idempotency_key: str,
) -> ProviderPlanChangeResult:
    if str(sub.pending_provider_status or "").upper() == "PENDING_APPROVAL":
        return ProviderPlanChangeResult(status="CANCELLED_BEFORE_APPROVAL", reference=str(sub.provider_subscription_id or ""))
    return await schedule_provider_plan_change(
        sub,
        current,
        effective_at=effective_at,
        base_url=base_url,
        idempotency_key=idempotency_key,
    )
