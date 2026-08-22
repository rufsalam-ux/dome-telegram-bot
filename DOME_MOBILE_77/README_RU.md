# DOME Mobile v2 — Production Core + Legal/Evidence Flow

Это развитие запущенного на Android DOME Mobile v1.

## Реализовано в клиентской архитектуре
- отдельный язык интерфейса родителя;
- standalone registration and login with name, email, password and email verification;
- отдельные consent documents: Terms, Privacy, Parent Authority, Voice/AI, Movie, Recurring Billing, Immediate Digital Access;
- consent versions/language/hash model;
- package/purchase confirmation showing lessons, price, recurring billing and immediate digital access acknowledgement;
- server-driven lesson manifest model;
- 2 completions per unlocked lesson;
- Attempt 1 -> Movie #1 + homework once;
- Attempt 2 -> Movie #2, no duplicate homework;
- exact resume invariant documented;
- 10 months from unlocked_at;
- movies library model;
- evidence/dispute bundle API and PostgreSQL logical schema;
- deletion/export API contract;
- admin role is server-side only (demo state keeps it hidden).

## Важно
Это рабочая клиентская Production Core сборка + полный контракт backend. Реальные SMS, email, payment webhooks, OpenAI, media storage и movie renderer выполняются сервером и не должны жить в APK.
Юридические тексты — шаблон продукта, не сертификат юридической корректности; перед коммерческим запуском нужен review юриста под рынки продаж.

## Запуск
npm install --legacy-peer-deps
npx expo start --lan

## APK
npx eas build -p android --profile preview
