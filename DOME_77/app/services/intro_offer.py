"""One paid introductory week per account and installation identity.

Device IDs are an anti-abuse signal, not a claim of tamper-proof identification.
No contacts, advertising identifiers or hardware fingerprint are collected.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from app.db.models import Child, IntroOfferClaim, Parent, Subscription


class IntroOfferUnavailable(ValueError):
    pass


def identity_keys(parent: Parent, headers=None) -> dict[str, str]:
    headers = headers or {}
    raw = {"parent": str(parent.id)}
    email = str(parent.email or "").strip().casefold()
    if parent.email_verified and email:
        raw["email"] = email
    if getattr(parent, "phone_verified", False) and parent.phone:
        raw["phone"] = re.sub(r"\D", "", parent.phone)
    for scope, header in (("device", "X-DOME-Device-ID"), ("install", "X-DOME-Install-ID")):
        value = str(headers.get(header) or "").strip()
        if re.fullmatch(r"[A-Za-z0-9:_-]{16,180}", value):
            raw[scope] = value
    return {scope: hashlib.sha256(f"dome-intro-v1:{scope}:{value}".encode()).hexdigest() for scope, value in raw.items()}


async def eligibility(db, parent: Parent, headers=None, *, now=None) -> tuple[bool, str]:
    now = now or datetime.utcnow()
    keys = identity_keys(parent, headers)
    # A typed, unverified email/phone must not reserve somebody else's offer.
    if not parent.email_verified:
        return False, "EMAIL_NOT_VERIFIED"
    if not ({"device", "install"} & keys.keys()):
        return False, "DEVICE_ID_REQUIRED"
    # Existing paid families predate the registry. Never give them another intro
    # just because the new table was installed or their old agreement cancelled.
    paid = await db.scalar(select(Subscription.id).join(Child, Child.id == Subscription.child_id).where(
        Child.parent_id == parent.id, Subscription.test_mode.is_(False), Subscription.lessons_allocated > 0,
    ).limit(1))
    if paid is not None:
        return False, "INTRO_ALREADY_USED"
    rows = (await db.scalars(select(IntroOfferClaim).where(IntroOfferClaim.identity_hash.in_(keys.values())))).all()
    for row in rows:
        if row.status == "CONSUMED":
            return False, "INTRO_ALREADY_USED"
        # A provider-bound reservation cannot expire locally: its approval link
        # could still collect money. It must be cancelled/expired at the provider.
        if row.provider_subscription_id or (row.reserved_until and row.reserved_until > now):
            return False, "INTRO_CHECKOUT_PENDING"
    return True, "ELIGIBLE"


async def reserve(db, parent: Parent, headers, token: str, *, now=None) -> None:
    now = now or datetime.utcnow()
    keys = identity_keys(parent, headers)
    if not ({"device", "install"} & keys.keys()):
        raise IntroOfferUnavailable("DEVICE_ID_REQUIRED")
    try:
        async with db.begin_nested():
            # Remove only expired, unbound reservations, never consumed history.
            await db.execute(delete(IntroOfferClaim).where(
                IntroOfferClaim.identity_hash.in_(keys.values()), IntroOfferClaim.status == "RESERVED",
                IntroOfferClaim.provider_subscription_id.is_(None), IntroOfferClaim.reserved_until <= now,
            ))
            for scope, key in sorted(keys.items()):
                db.add(IntroOfferClaim(identity_hash=key, scope=scope, parent_id=parent.id,
                    checkout_token=token, status="RESERVED", reserved_until=now + timedelta(minutes=30)))
            await db.flush()
    except IntegrityError as exc:
        raise IntroOfferUnavailable("INTRO_CHECKOUT_PENDING") from exc


async def bind(db, token: str, provider: str, subscription_id: str) -> None:
    await db.execute(update(IntroOfferClaim).where(IntroOfferClaim.checkout_token == token,
        IntroOfferClaim.status == "RESERVED").values(provider=provider, provider_subscription_id=subscription_id))


async def release(db, token: str) -> None:
    # Caller must have proved provider creation failed or cancellation/expiry
    # with no payment. Consumed claims are immutable, including after refunds.
    await db.execute(delete(IntroOfferClaim).where(IntroOfferClaim.checkout_token == token, IntroOfferClaim.status == "RESERVED"))


async def consume(db, sub: Subscription, *, paid_at: datetime) -> bool:
    """Atomically consume a known reservation after a verified positive payment."""
    if not sub.checkout_token:
        return True  # Legacy agreement; paid subscription history denies reuse.
    rows = (await db.scalars(select(IntroOfferClaim).where(IntroOfferClaim.checkout_token == sub.checkout_token))).all()
    if not rows:
        return False
    if any(row.provider_subscription_id != sub.provider_subscription_id for row in rows):
        return False
    for row in rows:
        row.status = "CONSUMED"
        row.consumed_at = row.consumed_at or paid_at
        row.reserved_until = None
    return True
