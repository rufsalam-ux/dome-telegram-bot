from datetime import datetime
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, Child, Parent, Subscription, SubscriptionAuditEvent
from app.core.config import settings
from app.services.mobile_tokens import issue_session_token
from app.services.payment_lifecycle import NormalizedPaymentEvent, apply_normalized_event
from app.services.subscription_plan_changes import (
    PLAN_CHANGE_ACTIVATED,
    PLAN_CHANGE_CANCELLED,
    PLAN_CHANGE_REQUESTED,
    PLAN_CHANGE_UPDATED,
    cancel_plan_change,
    preview_plan_change,
    renewal_charge_for,
    schedule_plan_change,
)
from app.services.subscription_provider import ProviderPlanChangeResult
from app.webapp import mobile_api


ROOT=Path(__file__).resolve().parents[1]


async def database():
    engine=create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
    return engine,async_sessionmaker(engine,expire_on_commit=False)


async def paid_subscription(session,*,now:datetime):
    parent=Parent(display_name='Parent',email='plan@example.test',email_verified=True)
    session.add(parent);await session.flush()
    child=Child(parent_id=parent.id,display_name='Child',native_language='ru',target_language='en')
    session.add(child);await session.flush()
    end=now+relativedelta(months=1)
    sub=Subscription(
        child_id=child.id,course_id='conversation',plan_id='weekly1',current_plan_id='weekly1',
        lessons_per_week=1,monthly_price=39,currency='EUR',status='ACTIVE',test_mode=False,
        payment_provider='stripe',provider_subscription_id='sub_plan_test',started_at=now,
        current_period_start=now,current_period_end=end,next_charge_at=end,
    )
    session.add(sub);await session.flush()
    return parent,child,sub,end


@pytest.mark.asyncio
async def test_request_update_cancel_are_next_period_only_and_audited():
    engine,Session=await database();now=datetime(2026,8,23,12,0,0)
    async with Session() as db:
        parent,_,sub,effective=await paid_subscription(db,now=now)
        preview=await preview_plan_change(db,sub,parent_id=parent.id,requested_plan_id='weekly2',now=now)
        assert preview.effective_at==effective and preview.requested.price==69
        schedule_plan_change(db,sub,parent_id=parent.id,preview=preview,provider_status='SCHEDULED',now=now)
        assert sub.current_plan_id=='weekly1' and sub.lessons_per_week==1 and sub.monthly_price==39
        assert sub.pending_plan_id=='weekly2' and sub.pending_plan_effective_at==effective

        updated=await preview_plan_change(db,sub,parent_id=parent.id,requested_plan_id='weekly4',now=now)
        schedule_plan_change(db,sub,parent_id=parent.id,preview=updated,provider_status='SCHEDULED',now=now)
        assert sub.current_plan_id=='weekly1' and sub.pending_plan_id=='weekly4'
        cancel_plan_change(db,sub,parent_id=parent.id,now=now)
        await db.commit()
        assert sub.current_plan_id=='weekly1' and sub.pending_plan_id is None
        events=list((await db.scalars(select(SubscriptionAuditEvent).order_by(SubscriptionAuditEvent.id))).all())
        assert [x.event_type for x in events]==[PLAN_CHANGE_REQUESTED,PLAN_CHANGE_UPDATED,PLAN_CHANGE_CANCELLED]
        assert events[0].old_plan_id=='weekly1' and events[0].new_plan_id=='weekly2'
        assert events[1].old_plan_id=='weekly2' and events[1].new_plan_id=='weekly4'
        assert all(x.parent_id==parent.id and x.billing_period=='MONTH' for x in events)
    await engine.dispose()


@pytest.mark.asyncio
async def test_successful_recurring_payment_activates_pending_plan_and_allocation():
    engine,Session=await database();now=datetime(2026,8,23,12,0,0)
    async with Session() as db:
        parent,_,sub,effective=await paid_subscription(db,now=now)
        preview=await preview_plan_change(db,sub,parent_id=parent.id,requested_plan_id='weekly2',now=now)
        schedule_plan_change(db,sub,parent_id=parent.id,preview=preview,provider_status='SCHEDULED',now=now)
        assert renewal_charge_for(sub,now=effective-relativedelta(seconds=1)).plan_id=='weekly1'
        due=renewal_charge_for(sub,now=effective)
        assert due.plan_id=='weekly2' and due.amount==69 and due.activates_pending
        event=NormalizedPaymentEvent(
            provider='stripe',event_id='invoice-1',event_type='PAYMENT_SUCCEEDED',status='ACTIVE',
            child_id=sub.child_id,course_id=sub.course_id,plan_id='weekly2',lessons_per_week=2,
            monthly_price=69,currency='EUR',provider_subscription_id='sub_plan_test',
            occurred_at=effective,period_start=effective,period_end=effective+relativedelta(months=1),charged_amount=69,
        )
        await apply_normalized_event(db,event);await db.commit()
        assert sub.current_plan_id=='weekly2' and sub.plan_id=='weekly2'
        assert sub.pending_plan_id is None and sub.lessons_per_week==2 and sub.monthly_price==69
        assert sub.lessons_allocated==8 and sub.lessons_used==0
        assert sub.current_period_start==effective and sub.next_charge_at==effective+relativedelta(months=1)
        activated=await db.scalar(select(SubscriptionAuditEvent).where(SubscriptionAuditEvent.event_type==PLAN_CHANGE_ACTIVATED))
        assert activated is not None and activated.old_price==39 and activated.new_price==69
    await engine.dispose()


@pytest.mark.asyncio
async def test_wrong_plan_or_amount_never_activates_pending_change():
    engine,Session=await database();now=datetime(2026,8,23,12,0,0)
    async with Session() as db:
        parent,_,sub,effective=await paid_subscription(db,now=now)
        preview=await preview_plan_change(db,sub,parent_id=parent.id,requested_plan_id='weekly3',now=now)
        schedule_plan_change(db,sub,parent_id=parent.id,preview=preview,provider_status='SCHEDULED',now=now)
        event=NormalizedPaymentEvent(
            provider='stripe',event_id='invoice-old',event_type='PAYMENT_SUCCEEDED',status='ACTIVE',
            child_id=sub.child_id,course_id=sub.course_id,plan_id='weekly1',lessons_per_week=1,
            monthly_price=39,currency='EUR',provider_subscription_id='sub_plan_test',
            occurred_at=effective,period_start=effective,period_end=effective+relativedelta(months=1),charged_amount=39,
        )
        await apply_normalized_event(db,event);await db.commit()
        assert sub.current_plan_id=='weekly1' and sub.pending_plan_id=='weekly3'
        assert sub.next_charge_at==effective+relativedelta(months=1)
        assert sub.pending_plan_effective_at==effective+relativedelta(months=1)
        assert await db.scalar(select(SubscriptionAuditEvent).where(SubscriptionAuditEvent.event_type==PLAN_CHANGE_ACTIVATED)) is None
    await engine.dispose()


def test_mobile_plan_ui_uses_backend_prices_and_required_copy():
    screen=(ROOT.parent/'DOME_MOBILE_77/src/screens/PurchaseScreen.tsx').read_text('utf-8')
    api=(ROOT.parent/'DOME_MOBILE_77/src/api/mobile.ts').read_text('utf-8')
    assert "const plans=[" not in screen and "const rates" not in screen
    assert 'Стоимость следующего периода' in screen
    assert 'Подтвердить изменение тарифа' in screen
    assert 'Отменить изменение тарифа' in screen
    assert 'Новый тариф начнёт действовать со следующего оплачиваемого периода' in screen
    assert '/subscription/plan-change/preview' in api and "jsonInit('DELETE'" in api


def test_stripe_plan_change_disables_proration_and_immediate_invoice():
    source=(ROOT/'app/services/payment_adapter.py').read_text('utf-8')
    function=source[source.index('def change_stripe_subscription_plan'):]
    assert "'proration_behavior':'none'" in function
    assert "'proration_behavior':'always_invoice'" not in function
    assert 'payment_behavior' not in function
    assert 'SubscriptionSchedule.create' in function and 'SubscriptionSchedule.modify' in function
    assert "'start_date':current_end" in function


@pytest.mark.asyncio
async def test_authenticated_mobile_plan_change_api_uses_same_domain(monkeypatch):
    engine,Session=await database();now=datetime(2026,8,23,12,0,0)
    async with Session() as db:
        parent,child,sub,effective=await paid_subscription(db,now=now);await db.commit()
        parent_id=parent.id;child_id=child.id

    async def schedule(*_args,**_kwargs):
        return ProviderPlanChangeResult(status='SCHEDULED',reference='sub_plan_test')
    async def restore(*_args,**_kwargs):
        return ProviderPlanChangeResult(status='SCHEDULED',reference='sub_plan_test')
    monkeypatch.setattr(mobile_api,'SessionLocal',Session)
    monkeypatch.setattr(mobile_api,'schedule_provider_plan_change',schedule)
    monkeypatch.setattr(mobile_api,'restore_provider_current_plan',restore)
    monkeypatch.setattr(settings,'mobile_auth_secret','plan-change-api-secret-long-enough')
    token=issue_session_token(parent_id)
    headers={'Authorization':f'Bearer {token}'}
    app=web.Application();mobile_api.register_mobile_routes(app);client=TestClient(TestServer(app));await client.start_server()
    try:
        response=await client.get(f'/api/mobile/child/{child_id}/subscription?course_id=conversation',headers=headers)
        assert response.status==200
        overview=await response.json()
        assert overview['subscription']['current_plan']['plan_id']=='weekly1'
        assert next(x for x in overview['plans'] if x['plan_id']=='weekly2' and x['billing_period']=='MONTH')['price']==69

        response=await client.post(f'/api/mobile/child/{child_id}/subscription/plan-change/preview',headers=headers,json={'course_id':'conversation','plan_id':'weekly2'})
        assert response.status==200
        preview=await response.json()
        assert preview['new_plan']['price']==69
        assert preview['effective_at'].startswith(effective.isoformat())

        response=await client.post(f'/api/mobile/child/{child_id}/subscription/plan-change',headers=headers,json={'course_id':'conversation','plan_id':'weekly2'})
        assert response.status==200
        changed=await response.json()
        assert changed['subscription']['current_plan']['plan_id']=='weekly1'
        assert changed['subscription']['pending_plan']['plan_id']=='weekly2'

        response=await client.delete(f'/api/mobile/child/{child_id}/subscription/plan-change',headers=headers,json={'course_id':'conversation'})
        assert response.status==200
        cancelled=await response.json()
        assert cancelled['subscription']['current_plan']['plan_id']=='weekly1'
        assert cancelled['subscription']['pending_plan'] is None
    finally:
        await client.close();await engine.dispose()
