from copy import deepcopy
from datetime import datetime

import pytest
from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, Child, Parent, Subscription, SubscriptionAuditEvent
from app.db.session import _backfill_subscription_price_versions
from app.services import paypal_adapter, platform_settings
from app.services.payment_lifecycle import NormalizedPaymentEvent, apply_normalized_event
from app.services.pricing_versions import (
    MONTH,
    YEAR,
    ensure_versioned_pricing_config,
    minimum_viable_price,
    plan_versions_for_course,
    set_plan_price,
)
from app.services.subscription_plan_changes import (
    PLAN_CHANGE_ACTIVATED,
    cancel_plan_change,
    current_plan_snapshot,
    preview_plan_change,
    renewal_charge_for,
    schedule_plan_change,
)
from app.services.subscription_price_migrations import _mapped_provider_plan_id


def isolated_pricing(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_settings, "SETTINGS_DIR", tmp_path / "platform-settings")
    platform_settings.save_settings("pricing", deepcopy(platform_settings.DEFAULT_PRICING))
    return ensure_versioned_pricing_config()


async def database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def active_version(plan_id: str, period: str = MONTH):
    return next(row for row in plan_versions_for_course(None, period) if row["plan_id"] == plan_id)


def test_default_catalog_and_profitability_gate(monkeypatch, tmp_path):
    cfg = isolated_pricing(monkeypatch, tmp_path)
    assert cfg["schema_version"] == "2.0"
    assert [row["price"] for row in plan_versions_for_course(None, MONTH)] == [39, 69, 99, 139]
    assert [row["price"] for row in plan_versions_for_course(None, YEAR)] == [429, 759, 1089, 1536]
    cfg = platform_settings.load_settings("pricing")
    cfg["regular_course"]["profitability"].update({"estimated_cost_per_lesson": 40, "minimum_margin_percent": 10})
    platform_settings.save_settings("pricing", cfg)
    assert plan_versions_for_course(None) == []
    assert minimum_viable_price({"lessons_per_week": 1, "billing_period": MONTH}) == 177.78
    legacy = Subscription(
        child_id=1, course_id="conversation", current_plan_id="weekly1", plan_id="weekly1",
        current_plan_version_id="old-v1", current_plan_price=39, monthly_price=39,
        billing_period=MONTH, lessons_per_week=1, currency="EUR",
    )
    assert current_plan_snapshot(legacy).price == 39


@pytest.mark.asyncio
async def test_existing_keeps_old_version_and_new_subscriber_gets_admin_price(monkeypatch, tmp_path):
    isolated_pricing(monkeypatch, tmp_path)
    old = active_version("weekly1")
    engine, Session = await database()
    now = datetime(2026, 8, 23, 12, 0, 0)
    async with Session() as db:
        parent = Parent(display_name="Parent", email="versions@example.test", email_verified=True)
        db.add(parent); await db.flush()
        existing_child = Child(parent_id=parent.id, display_name="Existing")
        new_child = Child(parent_id=parent.id, display_name="New")
        db.add_all([existing_child, new_child]); await db.flush()
        existing = Subscription(
            child_id=existing_child.id, course_id="conversation", plan_id="weekly1", current_plan_id="weekly1",
            current_plan_version_id=old["version_id"], current_plan_price=39, monthly_price=39,
            billing_period=MONTH, lessons_per_week=1, currency="EUR", status="ACTIVE", test_mode=False,
            payment_provider="paypal", provider_subscription_id="I-OLD", provider_plan_id="P-OLD",
            current_period_start=now, current_period_end=now + relativedelta(months=1),
        )
        db.add(existing); await db.flush()
        latest = set_plan_price(lessons_per_week=1, billing_period=MONTH, price=49, created_by="test-admin")
        history = platform_settings.load_settings("pricing")["regular_course"]["subscription_plan_versions"]
        persisted_old = next(row for row in history if row["version_id"] == old["version_id"])
        assert persisted_old["price"] == 39 and persisted_old["active"] is False
        assert next(row for row in history if row["version_id"] == latest["version_id"])["price"] == 49
        assert existing.current_plan_version_id == old["version_id"]
        assert existing.current_plan_price == 39 and existing.provider_plan_id == "P-OLD"
        created = await apply_normalized_event(db, NormalizedPaymentEvent(
            provider="paypal", event_id="new-sub", event_type="PAYMENT_SUCCEEDED", status="ACTIVE",
            child_id=new_child.id, course_id="conversation", plan_id="weekly1",
            plan_version_id=latest["version_id"], billing_period=MONTH, provider_plan_id="P-NEW",
            lessons_per_week=1, monthly_price=49, charged_amount=49, currency="EUR",
            provider_subscription_id="I-NEW", occurred_at=now, period_start=now,
            period_end=now + relativedelta(months=1),
        ))
        await db.commit()
        assert created is not None and created.current_plan_version_id == latest["version_id"]
        assert created.current_plan_price == 49 and created.provider_plan_id == "P-NEW"
        assert existing.current_plan_price == 39 and existing.provider_plan_id == "P-OLD"
    await engine.dispose()


@pytest.mark.asyncio
async def test_first_100_subscribers_keep_snapshot_and_101st_gets_new_price(monkeypatch, tmp_path):
    isolated_pricing(monkeypatch, tmp_path)
    old = active_version("weekly1")
    engine, Session = await database()
    now = datetime(2026, 8, 23, 12, 0, 0)
    async with Session() as db:
        parent = Parent(display_name="Founding cohort", email="first100@example.test", email_verified=True)
        db.add(parent); await db.flush()
        children = [Child(parent_id=parent.id, display_name=f"Child {number}") for number in range(1, 102)]
        db.add_all(children); await db.flush()
        founding = [
            Subscription(
                child_id=child.id, course_id="conversation", plan_id="weekly1", current_plan_id="weekly1",
                current_plan_version_id=old["version_id"], current_plan_price=39, monthly_price=39,
                billing_period=MONTH, lessons_per_week=1, currency="EUR", status="ACTIVE", test_mode=False,
                payment_provider="paypal", provider_subscription_id=f"I-FOUNDING-{index}", provider_plan_id="P-OLD",
                current_period_start=now, current_period_end=now + relativedelta(months=1),
            )
            for index, child in enumerate(children[:100], start=1)
        ]
        db.add_all(founding); await db.commit()
        latest = set_plan_price(lessons_per_week=1, billing_period=MONTH, price=49, created_by="test-admin")
        for sub in founding:
            await db.refresh(sub)
            assert (sub.current_plan_version_id, sub.current_plan_price, sub.provider_plan_id) == (old["version_id"], 39, "P-OLD")
        newcomer = await apply_normalized_event(db, NormalizedPaymentEvent(
            provider="paypal", event_id="subscriber-101", event_type="PAYMENT_SUCCEEDED", status="ACTIVE",
            child_id=children[100].id, course_id="conversation", plan_id="weekly1",
            plan_version_id=latest["version_id"], billing_period=MONTH, provider_plan_id="P-NEW",
            lessons_per_week=1, monthly_price=49, charged_amount=49, currency="EUR",
            provider_subscription_id="I-NEW-101", occurred_at=now, period_start=now,
            period_end=now + relativedelta(months=1),
        ))
        await db.commit()
        assert newcomer is not None
        assert (newcomer.current_plan_version_id, newcomer.current_plan_price, newcomer.provider_plan_id) == (latest["version_id"], 49, "P-NEW")
    await engine.dispose()


@pytest.mark.asyncio
async def test_pricing_json_change_never_reprices_existing_snapshot(monkeypatch, tmp_path):
    isolated_pricing(monkeypatch, tmp_path)
    old = active_version("weekly2")
    engine, Session = await database()
    async with Session() as db:
        parent = Parent(display_name="Parent", email="json-change@example.test", email_verified=True)
        db.add(parent); await db.flush()
        child = Child(parent_id=parent.id, display_name="Child"); db.add(child); await db.flush()
        sub = Subscription(
            child_id=child.id, course_id="conversation", plan_id="weekly2", current_plan_id="weekly2",
            current_plan_version_id=old["version_id"], current_plan_price=69, monthly_price=69,
            billing_period=MONTH, lessons_per_week=2, currency="EUR", status="ACTIVE", test_mode=False,
            payment_provider="paypal", provider_subscription_id="I-SNAPSHOT", provider_plan_id="P-SNAPSHOT",
        )
        db.add(sub); await db.commit()
        cfg = platform_settings.load_settings("pricing")
        for row in cfg["regular_course"]["subscription_plan_versions"]:
            if row["version_id"] == old["version_id"]:
                row["price"] = 999
        platform_settings.save_settings("pricing", cfg)
        await db.refresh(sub)
        assert current_plan_snapshot(sub).price == 69
        assert sub.current_plan_version_id == old["version_id"]
        assert sub.provider_plan_id == "P-SNAPSHOT"
    await engine.dispose()


@pytest.mark.asyncio
async def test_existing_plan_change_locks_latest_version_and_activates_next_period(monkeypatch, tmp_path):
    isolated_pricing(monkeypatch, tmp_path)
    old = active_version("weekly1")
    latest = set_plan_price(lessons_per_week=2, billing_period=MONTH, price=75, created_by="test-admin")
    engine, Session = await database()
    now = datetime(2026, 8, 23, 12, 0, 0); effective = now + relativedelta(months=1)
    async with Session() as db:
        parent = Parent(display_name="Parent", email="change@example.test", email_verified=True)
        db.add(parent); await db.flush()
        child = Child(parent_id=parent.id, display_name="Child"); db.add(child); await db.flush()
        sub = Subscription(
            child_id=child.id, course_id="conversation", plan_id="weekly1", current_plan_id="weekly1",
            current_plan_version_id=old["version_id"], current_plan_price=39, monthly_price=39,
            billing_period=MONTH, lessons_per_week=1, currency="EUR", status="ACTIVE", test_mode=False,
            payment_provider="stripe", provider_subscription_id="sub-old", provider_plan_id="price-old",
            current_period_start=now, current_period_end=effective, next_charge_at=effective,
        )
        db.add(sub); await db.flush()
        preview = await preview_plan_change(db, sub, parent_id=parent.id, requested_plan_id="weekly2", requested_billing_period=MONTH)
        assert preview.requested.version_id == latest["version_id"] and preview.requested.price == 75
        schedule_plan_change(db, sub, parent_id=parent.id, preview=preview, provider_status="SCHEDULED", provider_plan_id="price-new", now=now)
        due = renewal_charge_for(sub, now=effective)
        assert due.plan_version_id == latest["version_id"] and due.amount == 75
        assert sub.current_plan_version_id == old["version_id"] and sub.current_plan_price == 39
        await apply_normalized_event(db, NormalizedPaymentEvent(
            provider="stripe", event_id="renewal", event_type="PAYMENT_SUCCEEDED", status="ACTIVE",
            child_id=child.id, course_id="conversation", plan_id="weekly2",
            plan_version_id=latest["version_id"], billing_period=MONTH, provider_plan_id="price-new",
            lessons_per_week=2, monthly_price=75, charged_amount=75, currency="EUR",
            provider_subscription_id="sub-old", occurred_at=effective, period_start=effective,
            period_end=effective + relativedelta(months=1),
        ))
        await db.commit()
        assert sub.current_plan_id == "weekly2" and sub.current_plan_version_id == latest["version_id"]
        assert sub.current_plan_price == 75 and sub.provider_plan_id == "price-new"
        assert sub.pending_plan_id is None and sub.lessons_allocated == 8
        audit = await db.scalar(select(SubscriptionAuditEvent).where(SubscriptionAuditEvent.event_type == PLAN_CHANGE_ACTIVATED))
        assert audit is not None and audit.old_plan_version_id == old["version_id"]
        assert audit.new_plan_version_id == latest["version_id"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_cancel_pending_preserves_old_plan_price_version_and_provider_id(monkeypatch, tmp_path):
    isolated_pricing(monkeypatch, tmp_path)
    old = active_version("weekly1"); latest = set_plan_price(lessons_per_week=3, billing_period=MONTH, price=105, created_by="test-admin")
    engine, Session = await database(); now = datetime(2026, 8, 23, 12, 0, 0)
    async with Session() as db:
        parent = Parent(display_name="Parent", email="cancel@example.test", email_verified=True); db.add(parent); await db.flush()
        child = Child(parent_id=parent.id, display_name="Child"); db.add(child); await db.flush()
        sub = Subscription(child_id=child.id,course_id="conversation",plan_id="weekly1",current_plan_id="weekly1",current_plan_version_id=old["version_id"],current_plan_price=39,monthly_price=39,billing_period=MONTH,lessons_per_week=1,currency="EUR",status="ACTIVE",test_mode=False,payment_provider="paypal",provider_subscription_id="I-OLD",provider_plan_id="P-OLD",current_period_start=now,current_period_end=now+relativedelta(months=1))
        db.add(sub); await db.flush()
        preview=await preview_plan_change(db,sub,parent_id=parent.id,requested_plan_id="weekly3")
        assert preview.requested.version_id==latest["version_id"]
        schedule_plan_change(db,sub,parent_id=parent.id,preview=preview,provider_status="SCHEDULED",provider_plan_id="P-PENDING",now=now)
        cancel_plan_change(db,sub,parent_id=parent.id,now=now)
        assert sub.pending_plan_id is None and sub.pending_plan_version_id is None and sub.pending_provider_plan_id is None
        assert (sub.current_plan_id,sub.current_plan_version_id,sub.current_plan_price,sub.provider_plan_id)==("weekly1",old["version_id"],39,"P-OLD")
    await engine.dispose()


@pytest.mark.asyncio
async def test_cancelled_subscriber_loses_grandfathering_when_returning(monkeypatch, tmp_path):
    isolated_pricing(monkeypatch, tmp_path)
    old = active_version("weekly1")
    engine, Session = await database(); now = datetime(2026, 8, 23, 12, 0, 0)
    async with Session() as db:
        parent = Parent(display_name="Parent", email="return@example.test", email_verified=True); db.add(parent); await db.flush()
        child = Child(parent_id=parent.id, display_name="Child"); db.add(child); await db.flush()
        old_sub = Subscription(
            child_id=child.id, course_id="conversation", plan_id="weekly1", current_plan_id="weekly1",
            current_plan_version_id=old["version_id"], current_plan_price=39, monthly_price=39,
            billing_period=MONTH, lessons_per_week=1, currency="EUR", status="ACTIVE", test_mode=False,
            payment_provider="paypal", provider_subscription_id="I-CANCELLED", provider_plan_id="P-OLD",
        )
        db.add(old_sub); await db.commit()
        await apply_normalized_event(db, NormalizedPaymentEvent(
            provider="paypal", event_id="cancel-old", event_type="SUBSCRIPTION_CANCELLED", status="CANCELLED",
            child_id=child.id, course_id="conversation", provider_subscription_id="I-CANCELLED", occurred_at=now,
        ))
        await db.commit()
        latest = set_plan_price(lessons_per_week=1, billing_period=MONTH, price=49, created_by="test-admin")
        returned = await apply_normalized_event(db, NormalizedPaymentEvent(
            provider="paypal", event_id="return-new", event_type="PAYMENT_SUCCEEDED", status="ACTIVE",
            child_id=child.id, course_id="conversation", plan_id="weekly1",
            plan_version_id=latest["version_id"], billing_period=MONTH, provider_plan_id="P-NEW",
            lessons_per_week=1, monthly_price=49, charged_amount=49, currency="EUR",
            provider_subscription_id="I-RETURNED", occurred_at=now + relativedelta(days=1),
            period_start=now + relativedelta(days=1), period_end=now + relativedelta(months=1, days=1),
        ))
        await db.commit()
        rows = list((await db.scalars(select(Subscription).where(
            Subscription.child_id == child.id, Subscription.course_id == "conversation"
        ).order_by(Subscription.id))).all())
        assert len(rows) == 2 and returned is rows[1]
        assert (rows[0].status, rows[0].current_plan_price, rows[0].provider_plan_id) == ("CANCELLED", 39, "P-OLD")
        assert (returned.status, returned.current_plan_version_id, returned.current_plan_price, returned.provider_plan_id) == (
            "ACTIVE", latest["version_id"], 49, "P-NEW"
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_paypal_plan_mapping_is_immutable_per_pricing_version(monkeypatch, tmp_path):
    isolated_pricing(monkeypatch, tmp_path)
    old = active_version("weekly1"); new = set_plan_price(lessons_per_week=1,billing_period=MONTH,price=49,created_by="test-admin")
    calls=[]
    async def product():return "PROD-1"
    async def request(method,path,*,body=None,request_id=""):
        calls.append((method,path,body,request_id));return {"id":f"P-{len(calls)}"}
    monkeypatch.setattr(paypal_adapter,"_ensure_product",product);monkeypatch.setattr(paypal_adapter,"_request",request)
    old_id=await paypal_adapter.ensure_paypal_plan(plan_id="weekly1",plan_version_id=old["version_id"],lessons_per_week=1,monthly_price=39,currency="EUR",billing_period=MONTH)
    new_id=await paypal_adapter.ensure_paypal_plan(plan_id="weekly1",plan_version_id=new["version_id"],lessons_per_week=1,monthly_price=49,currency="EUR",billing_period=MONTH)
    old_again=await paypal_adapter.ensure_paypal_plan(plan_id="weekly1",plan_version_id=old["version_id"],lessons_per_week=1,monthly_price=39,currency="EUR",billing_period=MONTH)
    assert old_id==old_again and old_id!=new_id and len(calls)==2
    mappings=platform_settings.load_settings("payments")["paypal_plan_versions"]
    assert {entry["paypal_plan_id"] for entry in mappings.values()}=={old_id,new_id}
    webhook = paypal_adapter.normalize_paypal_event({
        "id": "WH-NEW", "event_type": "BILLING.SUBSCRIPTION.ACTIVATED",
        "resource": {"id": "I-WEBHOOK", "status": "ACTIVE", "plan_id": new_id,
                     "custom_id": f"dome2|9|conversation|weekly1|{new['version_id']}|1|49.00|MONTH"},
    })
    assert webhook.provider_plan_id == new_id
    assert webhook.plan_version_id == new["version_id"] and webhook.monthly_price == 49


@pytest.mark.asyncio
async def test_database_migration_backfills_legacy_snapshot_fields():
    engine,Session=await database()
    async with Session() as db:
        parent=Parent(display_name="P",email="legacy@example.test",email_verified=True);db.add(parent);await db.flush()
        child=Child(parent_id=parent.id,display_name="C");db.add(child);await db.flush()
        sub=Subscription(child_id=child.id,course_id="conversation",plan_id="weekly2",current_plan_id="weekly2",current_plan_version_id=None,current_plan_price=None,monthly_price=69,currency="EUR")
        db.add(sub);await db.commit();sub_id=sub.id
    async with engine.begin() as conn:await _backfill_subscription_price_versions(conn)
    async with Session() as db:
        sub=await db.get(Subscription,sub_id)
        assert sub.current_plan_version_id=="legacy-weekly2-month-eur-69.00"
        assert sub.current_plan_price==69 and sub.billing_period==MONTH
    await engine.dispose()


def test_legacy_provider_plan_id_is_recovered_without_repricing():
    sub=Subscription(child_id=1,course_id="conversation",plan_id="weekly2",current_plan_id="weekly2",current_plan_version_id="legacy-weekly2-month-eur-69.00",current_plan_price=69,monthly_price=69,billing_period=MONTH,currency="EUR",payment_provider="paypal")
    payments={"paypal_plan_cache":{"weekly2:EUR:69.00":"P-LEGACY"}}
    assert _mapped_provider_plan_id(sub,payments)=="P-LEGACY"
    assert sub.current_plan_price==69 and sub.current_plan_version_id=="legacy-weekly2-month-eur-69.00"
