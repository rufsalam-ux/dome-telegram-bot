# DOME Mobile API contract v2

## Standalone mobile identity
POST /api/mobile/register `{name,email,password}`
POST /api/mobile/verify-email `{email,code}`
POST /api/mobile/resend-verification `{email}`
POST /api/mobile/login `{email,password}`

Email is normalized to lowercase by the backend. Registration stores a password hash and a
short-lived verification-code hash. The backend issues an authenticated parent session only
after email verification. Login rejects unverified accounts with `EMAIL_NOT_VERIFIED`.

## Immutable consent ledger
POST /v2/consents/accept
Store: parent_id, child_id if relevant, consent_type, document_version, immutable document_hash, language, UTC accepted_at, verification state, session/device evidence, country, app version.
Never trust a client-provided hash as authoritative; server resolves the canonical document and hash.

## Purchases
POST /v2/subscriptions/quote
POST /v2/subscriptions/checkout
Webhook -> payment success -> entitlement creation.
Never unlock paid lessons from client-side success alone.
Quote records: package, exact amount/currency, number of new lessons, access term, recurring flag, family discount, tax handling if applicable.

## Lesson invariants
- Entitlement controls which lessons are unlocked.
- Each unlock cycle: max 2 full completions.
- Aborted session does NOT consume a completion.
- Resume exact scene/session.
- Each attempt has separate voice-record set and separate movie record.
- Attempt 1 completion => Movie #1 + issue Homework ONCE.
- Attempt 2 completion => Movie #2; no duplicate homework.
- After 2/2 or 10 months from unlocked_at: status CLOSED, history retained.
- Movie and homework delivery are idempotent.

## Privacy controls
POST /v2/children/:id/export
POST /v2/children/:id/deletion-requests {scope: voice|movies|child_all}
POST /v2/consents/withdraw
Backend enforces retention jobs and legal holds where applicable.

## Evidence / dispute support
GET /v2/admin/parents/:parentId/evidence-bundle
Bundle includes verification, consents, quote/purchase, payment webhook evidence, entitlements, lesson usage, movie delivery, homework delivery, cancellation and support history.

## Security
- OpenAI/payment/SMS/email secrets only on backend.
- Do not ship provider secrets in APK/IPA.
- All admin authorization enforced server-side.
- Signed media URLs; short TTL for sensitive media.
- Rate limiting, audit logs, CSRF-equivalent protections for web checkout callbacks, replay protection/idempotency on webhooks.
