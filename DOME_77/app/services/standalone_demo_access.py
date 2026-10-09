from __future__ import annotations

FREE_DEMO_LESSON_ID = "demo_001"
FREE_DEMO_SOURCE = "FREE_DEMO"


async def ensure_free_demo_entitlement(*_args, **_kwargs):
    """Deprecated compatibility shim: DOME no longer grants free demo access.

    Kept temporarily so older callers fail closed rather than crashing. Existing
    entitlement/session rows are deliberately not deleted; paid activation may
    promote a legacy FREE_DEMO row without resetting its progress.
    """
    return None, False


async def backfill_free_demo_entitlements(*_args, **_kwargs) -> int:
    """Deprecated no-op retained for old startup hooks; never creates access."""
    return 0

