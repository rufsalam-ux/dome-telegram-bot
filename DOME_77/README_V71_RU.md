# DOME v71 — PAYMENT PRODUCTION READY + FAMILY ACCOUNTS

## Семья до 5 детей

Один Telegram-аккаунт родителя может содержать до **5 отдельных профилей детей**. У каждого ребёнка свои курсы, прогресс, домашние задания, entitlement и подписки. Активный ребёнок сохраняется в БД и не теряется после выхода/перезапуска.

## Семейная скидка — €0.50 С КАЖДОГО УРОКА

Первый ребёнок платит полную цену. Каждый дополнительный ребёнок (позиции 2–5) получает скидку **€0.50 на каждый плановый урок**.

Для ежемесячного биллинга DOME использует 4 billing-недели (параметр можно менять в `config/pricing.json` без нового ZIP):

| Уроков/нед | 1-й ребёнок | 2–5-й ребёнок | Месячная скидка |
|---|---:|---:|---:|
| 1 | €39 | €37 | €2 |
| 2 | €79 | €75 | €4 |
| 3 | €109 | €103 | €6 |
| 4 | €139 | €131 | €8 |

Формула: `monthly_price - €0.50 × lessons_per_week × 4`.

## Production payment providers

v71 поддерживает отдельные provider flows:

- **Stripe** — hosted subscription Checkout + webhook + idempotency + изменение существующей подписки.
- **UniPAY** — отдельный hosted subscription adapter + `/webhooks/unipay`. Exact merchant endpoint/header mapping задаётся Railway Variables.
- **Unlimit** — отдельный recurring/Payment Page adapter + OAuth access token/merchant credentials + `/webhooks/unlimit` + SHA-512 callback verification. Exact regional API URLs задаются Railway Variables из merchant account.
- **PayPal** — официальный REST Subscriptions API + automatic product/plan provisioning/cache + `/webhooks/paypal` + официальная webhook verification. При смене PayPal-плана родитель получает approve URL для повторного согласия, когда PayPal его требует.

`custom` остаётся только для тестовой/ручной ссылки и **не может быть включён как production billing**.

## Главные payment guardrails

- `subscription.created` и `checkout.completed` **не открывают платный контент сами по себе**, если провайдер не подтвердил ACTIVE/успешную оплату.
- Повторные webhook идемпотентны и хранятся как `provider:event_id`.
- Двойное нажатие на checkout использует idempotency key.
- При активной подписке DOME не создаёт вторую подписку поверх неё.
- Смена тарифа идёт через update/revise существующей provider subscription.
- Failed payment переводит подписку в `PAST_DUE`: старые уже открытые уроки не отнимаются, новые не выдаются.
- Cancel/suspend останавливает будущую выдачу, уже выданные entitlement сохраняются.
- Resubscribe начинает новую release baseline без накопления backlog.
- Production billing принудительно выключает test bypass.
- PAN/CVV не хранятся в DOME; используются hosted/provider flows.

## Настройка Unlimit

Официальная Unlimit integration использует OAuth2 access token; terminal code/password и callback secret выдаются merchant account. Для разных регионов endpoint может отличаться, поэтому URL не угадывается внутри DOME.

Railway Variables:

```text
UNLIMIT_TOKEN_URL=...
UNLIMIT_PAYMENT_URL=...
UNLIMIT_RECURRING_URL=...
UNLIMIT_RECURRING_UPDATE_URL=...
UNLIMIT_API_TOKEN=...              # можно использовать готовый token
UNLIMIT_TERMINAL_CODE=...
UNLIMIT_PASSWORD=...
UNLIMIT_CALLBACK_SECRET=...
UNLIMIT_SIGNATURE_HEADER=X-Signature
```

Webhook:

```text
https://YOUR-RAILWAY-DOMAIN/webhooks/unlimit
```

## Настройка PayPal

Railway Variables:

```text
PAYPAL_MODE=sandbox                # затем live
PAYPAL_CLIENT_ID=...
PAYPAL_CLIENT_SECRET=...
PAYPAL_WEBHOOK_ID=...
PAYPAL_PRODUCT_ID=...              # необязательно: DOME может создать product через API
```

Webhook:

```text
https://YOUR-RAILWAY-DOMAIN/webhooks/paypal
```

Подписать webhook на subscription/payment events, включая activation/update/cancel/suspend/payment failed/payment completed.

## Настройка Stripe

```text
STRIPE_SECRET_KEY=...
STRIPE_WEBHOOK_SECRET=...
```

Webhook:

```text
https://YOUR-RAILWAY-DOMAIN/webhooks/stripe
```

## Настройка UniPAY

```text
UNIPAY_SUBSCRIPTION_URL=...
UNIPAY_SUBSCRIPTION_UPDATE_URL=...
UNIPAY_ACCESS_TOKEN=...
# либо merchant pair:
UNIPAY_MERCHANT_ID=...
UNIPAY_API_KEY=...
UNIPAY_WEBHOOK_SECRET=...
# либо UNIPAY_WEBHOOK_TOKEN=...
```

Webhook:

```text
https://YOUR-RAILWAY-DOMAIN/webhooks/unipay
```

## Команды администратора

```text
/dome_provider stripe
/dome_provider unipay
/dome_provider unlimit
/dome_provider paypal
/dome_billing on
/dome_billing off
```

Перед `/dome_billing on` DOME проверяет обязательные credentials выбранного production-провайдера.

## Обязательный sandbox smoke-test перед реальными деньгами

1. Создать родителя и 5 детей; убедиться, что 6-й запрещён.
2. Проверить цены каждого ребёнка: 1-й полная цена, 2–5-й скидка €0.50/урок.
3. Купить 1×/нед и получить ACTIVE только после provider webhook.
4. Повторить тот же webhook — второй раз ничего не должно примениться.
5. Проверить failed payment: новые уроки не появляются, старые остаются.
6. Проверить cancel/suspend.
7. Проверить resubscribe без backlog.
8. Проверить upgrade 1→4 и downgrade 4→1 без второй активной subscription.
9. Для PayPal обязательно завершить buyer re-consent при revise, если PayPal вернул approve URL.
10. Только после этого включить live credentials и `/dome_billing on`.

## Тесты v71

- Целевой payment/family + v69/v70 runtime набор: **34/34 passed**.
- Широкий доступный прогон без двух модулей, требующих отсутствующий локально `aiogram`: **137 passed, 22 legacy failures**.
- 22 legacy failures относятся к историческим тестам старой структуры demo_001/старых literal-механик и совпадают по классу с предыдущим baseline; новых payment/family регрессий этим прогоном не обнаружено.
- `python -m compileall app tests` проходит.

На Railway перед live необходимо провести реальный sandbox end-to-end с credentials провайдера; никакой локальный mock не заменяет provider-side webhook/settlement test.
