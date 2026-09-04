from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import settings

log = logging.getLogger("dome.payment_provider")


@dataclass(frozen=True)
class CheckoutResult:
    ok: bool
    provider: str
    checkout_url: str = ""
    subscription_id: str = ""
    provider_plan_id: str = ""
    configured: bool = True
    error: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    status: str
    active: bool
    provider: str
    subscription_id: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class PaymentProvider(Protocol):
    name: str

    def is_configured(self) -> bool:
        ...

    async def create_subscription_checkout(
        self,
        *,
        child_id: int,
        course_id: str,
        plan_id: str,
        plan_version_id: str = "",
        lessons_per_week: int = 1,
        monthly_price: float = 39.0,
        currency: str = "EUR",
        billing_period: str = "MONTH",
        success_url: str = "",
        cancel_url: str = "",
        promo_code: str = "",
        idempotency_key: str = "",
    ) -> CheckoutResult:
        ...

    async def verify_subscription(self, provider_subscription_id: str) -> VerifyResult:
        ...


class PayPalPaymentProvider:
    name = "paypal"

    def is_configured(self) -> bool:
        return bool(settings.paypal_client_id and settings.paypal_client_secret)

    async def create_subscription_checkout(
        self,
        *,
        child_id: int,
        course_id: str,
        plan_id: str,
        plan_version_id: str = "",
        lessons_per_week: int = 1,
        monthly_price: float = 39.0,
        currency: str = "EUR",
        billing_period: str = "MONTH",
        success_url: str = "",
        cancel_url: str = "",
        promo_code: str = "",
        idempotency_key: str = "",
    ) -> CheckoutResult:
        if not self.is_configured():
            return CheckoutResult(
                ok=False,
                provider=self.name,
                configured=False,
                error="PAYPAL_NOT_CONFIGURED",
                message="Платёжная система PayPal Sandbox находится в режиме настройки. Укажите PAYPAL_CLIENT_ID и PAYPAL_CLIENT_SECRET в переменных окружения.",
            )

        from app.services.paypal_adapter import create_paypal_subscription_checkout_detail

        try:
            detail = await create_paypal_subscription_checkout_detail(
                child_id=child_id,
                course_id=course_id,
                plan_id=plan_id,
                plan_version_id=plan_version_id,
                lessons_per_week=lessons_per_week,
                monthly_price=monthly_price,
                currency=currency,
                billing_period=billing_period,
                success_url=success_url,
                cancel_url=cancel_url,
                idempotency_key=idempotency_key,
            )
            return CheckoutResult(
                ok=True,
                provider=self.name,
                checkout_url=detail["approval_url"],
                subscription_id=detail["subscription_id"],
                provider_plan_id=detail["provider_plan_id"],
                configured=True,
                message="Перенаправление на страницу оплаты PayPal...",
                details=detail,
            )
        except Exception as exc:
            log.exception("PayPal checkout creation error: %s", exc)
            return CheckoutResult(
                ok=False,
                provider=self.name,
                configured=True,
                error=str(exc),
                message=f"Ошибка создания подписки PayPal: {exc}",
            )

    async def verify_subscription(self, provider_subscription_id: str) -> VerifyResult:
        if not self.is_configured():
            return VerifyResult(
                ok=False,
                status="NOT_CONFIGURED",
                active=False,
                provider=self.name,
                message="PayPal не настроен",
            )
        from app.services.paypal_adapter import get_paypal_subscription

        try:
            sub = await get_paypal_subscription(provider_subscription_id)
            raw_status = str(sub.get("status") or "").upper()
            is_active = raw_status in {"ACTIVE", "APPROVED"}
            return VerifyResult(
                ok=True,
                status=raw_status,
                active=is_active,
                provider=self.name,
                subscription_id=provider_subscription_id,
                message="Подписка активна" if is_active else f"Статус подписки: {raw_status}",
                details=sub,
            )
        except Exception as exc:
            log.warning("Failed to verify PayPal subscription %s: %s", provider_subscription_id, exc)
            return VerifyResult(
                ok=False,
                status="ERROR",
                active=False,
                provider=self.name,
                subscription_id=provider_subscription_id,
                message=str(exc),
            )


class GooglePlayBillingProvider:
    """Adapter for Google Play Billing (channel separation per Stage 15).

    When distributed through Google Play, digital goods must comply with Play Billing policies.
    This adapter defines the contract and product IDs for future in-app billing without breaking
    the universal entitlement architecture.
    """
    name = "google_play"

    def is_configured(self) -> bool:
        return bool(os.environ.get("GOOGLE_PLAY_BILLING_ENABLED"))

    async def create_subscription_checkout(
        self,
        *,
        child_id: int,
        course_id: str,
        plan_id: str,
        plan_version_id: str = "",
        lessons_per_week: int = 1,
        monthly_price: float = 39.0,
        currency: str = "EUR",
        billing_period: str = "MONTH",
        success_url: str = "",
        cancel_url: str = "",
        promo_code: str = "",
        idempotency_key: str = "",
    ) -> CheckoutResult:
        sku = f"dome_{plan_id}_{billing_period.lower()}"
        return CheckoutResult(
            ok=False,
            provider=self.name,
            configured=self.is_configured(),
            error="PLAY_BILLING_CHANNEL",
            message=f"Google Play Billing адаптер подготовлен (SKU: {sku}). Для внешнего/демо тестирования используйте прямой провайдер PayPal.",
            details={"sku": sku, "channel": "google_play"},
        )

    async def verify_subscription(self, provider_subscription_id: str) -> VerifyResult:
        return VerifyResult(
            ok=False,
            status="NOT_IMPLEMENTED",
            active=False,
            provider=self.name,
            message="Google Play Billing проверка на этапе подготовки",
        )


def get_payment_provider(name: str = "paypal") -> PaymentProvider:
    norm = str(name or "paypal").strip().lower()
    if norm in {"google_play", "play_billing", "googleplay"}:
        return GooglePlayBillingProvider()
    return PayPalPaymentProvider()
