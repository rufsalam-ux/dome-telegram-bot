import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Base, Parent, Child, Subscription, IntroOfferClaim
from app.services import intro_offer, payment_provider, paypal_adapter, platform_settings, subscription_provider, subscription_release
from app.services.mobile_tokens import issue_session_token
from app.services.payment_lifecycle import NormalizedPaymentEvent, apply_normalized_event
from app.services.pricing_versions import ensure_versioned_pricing_config
from app.webapp import mobile_api


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_settings, 'SETTINGS_DIR', tmp_path / 'settings')
    platform_settings.save_settings('pricing', deepcopy(platform_settings.DEFAULT_PRICING))
    ensure_versioned_pricing_config()
    monkeypatch.setattr(settings, 'mobile_auth_secret', 'intro-once-test-auth-secret-long-enough')
    monkeypatch.setattr(subscription_release, '_course_order', lambda _: ['demo_001'] + [f'lesson_{i}' for i in range(100)])


async def database(path=':memory:'):
    engine = create_async_engine('sqlite+aiosqlite:///' + str(path))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def family(db, email='intro@example.test'):
    parent = Parent(email=email, email_verified=True, account_status='ACTIVE')
    db.add(parent)
    await db.flush()
    child = Child(parent_id=parent.id, display_name='Child')
    db.add(child)
    await db.commit()
    return parent, child


def device(value='one'):
    return {'X-DOME-Device-ID': 'android_' + value.ljust(64, '0'), 'X-DOME-Install-ID': value.ljust(36, '1')}


@pytest.mark.asyncio
async def test_claim_persists_across_accounts_installations_and_phone_verification():
    engine, sessions = await database()
    try:
        async with sessions() as db:
            one, child = await family(db)
            two, _ = await family(db, 'two@example.test')
            assert (await intro_offer.eligibility(db, one, device()))[0]
            await intro_offer.reserve(db, one, device(), 'token1')
            await intro_offer.bind(db, 'token1', 'paypal', 'I-1')
            await db.commit()
            assert await intro_offer.eligibility(db, two, device()) == (False, 'INTRO_CHECKOUT_PENDING')
            assert await intro_offer.eligibility(db, one, device('other')) == (False, 'INTRO_CHECKOUT_PENDING')
            sub = Subscription(child_id=child.id, course_id='conversation', checkout_token='token1', provider_subscription_id='I-1')
            assert await intro_offer.consume(db, sub, paid_at=datetime.utcnow())
            await db.commit()
        async with sessions() as db:
            one = await db.get(Parent, one.id)
            two = await db.get(Parent, two.id)
            assert await intro_offer.eligibility(db, two, device()) == (False, 'INTRO_ALREADY_USED')
            assert await intro_offer.eligibility(db, one, device('newphone')) == (False, 'INTRO_ALREADY_USED')
            await intro_offer.release(db, 'token1')
            assert len((await db.scalars(select(IntroOfferClaim))).all()) == 4
            one.phone = two.phone = '+1 (555) 123-4567'
            assert 'phone' not in intro_offer.identity_keys(one, device())
            one.phone_verified = two.phone_verified = True
            assert intro_offer.identity_keys(one)['phone'] == intro_offer.identity_keys(two)['phone']
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_unbound_releases_but_live_approval_never_expires_locally():
    engine, sessions = await database()
    try:
        async with sessions() as db:
            one, _ = await family(db)
            past = datetime.utcnow() - timedelta(days=1)
            await intro_offer.reserve(db, one, device(), 'expired', now=past)
            assert (await intro_offer.eligibility(db, one, device()))[0]
            await intro_offer.reserve(db, one, device(), 'replacement')
            await intro_offer.bind(db, 'replacement', 'paypal', 'I-AWAITING')
            assert await intro_offer.eligibility(db, one, device(), now=datetime.utcnow()+timedelta(days=30)) == (False, 'INTRO_CHECKOUT_PENDING')
            await intro_offer.release(db, 'replacement')
            assert (await intro_offer.eligibility(db, one, device()))[0]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_same_device_only_one_reservation(tmp_path):
    engine, sessions = await database(tmp_path / 'claims.db')
    try:
        async with sessions() as db:
            one, _ = await family(db)
            two, _ = await family(db, 'other@example.test')
        async def attempt(parent):
            async with sessions() as db:
                try:
                    await intro_offer.reserve(db, parent, device(), f'token-{parent.id}')
                    await db.commit()
                    return True
                except intro_offer.IntroOfferUnavailable:
                    await db.rollback()
                    return False
        assert sorted(await asyncio.gather(attempt(one), attempt(two))) == [False, True]
    finally:
        await engine.dispose()


@pytest.mark.parametrize('days,period', [(7, 'MONTH'), (30, 'MONTH'), (365, 'YEAR')])
@pytest.mark.asyncio
async def test_cancel_calls_provider_keeps_paid_period_and_blocks_future_slots(monkeypatch, days, period):
    engine, sessions = await database()
    calls = []
    now = datetime.utcnow()
    async def cancel(sub):
        calls.append(sub.provider_subscription_id)
        return {'status':'CANCELLED'}
    monkeypatch.setattr(subscription_provider, 'cancel_provider_renewal', cancel)
    monkeypatch.setattr(mobile_api, 'SessionLocal', sessions)
    try:
        async with sessions() as db:
            parent, child = await family(db)
            sub = Subscription(child_id=child.id, course_id='conversation', status='ACTIVE', test_mode=False,
                payment_provider='paypal', provider_subscription_id='I-PAID', current_plan_price=59,
                billing_period=period, started_at=now, current_period_start=now,
                current_period_end=now+timedelta(days=days), lessons_allocated=2, lessons_used=1)
            db.add(sub)
            await db.commit()
        app = web.Application()
        mobile_api.register_mobile_routes(app)
        async with TestClient(TestServer(app)) as client:
            headers = {'Authorization':f'Bearer {issue_session_token(parent.id)}', **device()}
            base = f'/api/mobile/child/{child.id}/subscription'
            response = await client.post(base+'/cancel', headers=headers, json={})
            assert response.status == 200, await response.text()
            result = (await response.json())['subscription']
            assert result['cancel_at_period_end'] is True and result['status'] == 'ACTIVE'
            assert result['next_charge_at'] is None
            assert result['access_until'] == (now+timedelta(days=days)).isoformat()
            assert result['lessons_used'] == 1
            await client.post(base+'/cancel', headers=headers, json={})
            assert calls == ['I-PAID']
            assert await subscription_release.release_due_lessons(child.id, 'conversation', now=now+timedelta(days=days, seconds=1), session_factory=sessions) == []
            async with sessions() as db:
                row = await db.get(Subscription, sub.id)
                await apply_normalized_event(db, NormalizedPaymentEvent(provider='paypal', event_id='late-approval', event_type='SUBSCRIPTION_ACTIVE', status='ACTIVE', provider_subscription_id='I-PAID'))
                assert row.cancel_at_period_end and row.lessons_used == 1
    finally:
        await engine.dispose()


@pytest.mark.parametrize('period', ['MONTH', 'YEAR'])
@pytest.mark.asyncio
async def test_returning_customer_full_period_no_intro_or_approval_unlock(monkeypatch, period):
    engine, sessions = await database()
    captured = {}
    class Provider:
        def is_configured(self): return True
        async def create_subscription_checkout(self, **kwargs):
            captured.update(kwargs)
            return payment_provider.CheckoutResult(ok=True, provider='paypal', subscription_id='I-NEW', provider_plan_id='P-REGULAR', checkout_url='https://example.test/pay')
    monkeypatch.setattr(payment_provider, 'get_payment_provider', lambda _:Provider())
    monkeypatch.setattr(mobile_api, 'SessionLocal', sessions)
    try:
        async with sessions() as db:
            parent, child = await family(db)
            db.add(Subscription(child_id=child.id, course_id='conversation', test_mode=False, status='CANCELLED',
                provider_subscription_id='I-OLD', lessons_allocated=2, current_period_start=datetime.utcnow()-timedelta(days=8), current_period_end=datetime.utcnow()-timedelta(days=1)))
            await db.commit()
        app=web.Application()
        mobile_api.register_mobile_routes(app)
        async with TestClient(TestServer(app)) as client:
            headers={'Authorization':f'Bearer {issue_session_token(parent.id)}', **device('new-device')}
            base=f'/api/mobile/child/{child.id}/subscription'
            overview=await (await client.get(base, headers=headers)).json()
            assert not overview['intro_eligible']
            assert overview['billing_policy_version']=='2026-10-08'
            plan=next(p for p in overview['plans'] if p['billing_period']==period)
            assert plan['intro_week_price']==0 and plan['intro_week_days']==0
            body={'plan_id':plan['plan_id'], 'version_id':plan['version_id'], 'billing_period':period,
                'expected_intro_week_price':3, 'payment_consents':[{'document_type':'SUBSCRIPTION_TERMS', 'version':overview['subscription_terms_version'], 'accepted':True}]}
            assert (await client.post(base+'/checkout', headers=headers, json=body)).status==409
            assert not captured
            body['expected_intro_week_price']=0
            response=await client.post(base+'/checkout', headers=headers, json=body)
            assert response.status==200, await response.text()
            assert captured['intro_week_price']==0 and not captured['special_first_year']
            assert captured['monthly_price']==plan['price']
            async with sessions() as db:
                latest=await db.scalar(select(Subscription).order_by(Subscription.id.desc()))
                assert latest.provider_subscription_id=='I-NEW'
                await apply_normalized_event(db, NormalizedPaymentEvent(provider='paypal',event_id='approve',event_type='SUBSCRIPTION_ACTIVE',status='ACTIVE',provider_subscription_id='I-NEW',child_id=child.id,course_id='conversation'))
                assert latest.status=='PENDING'
                await apply_normalized_event(db, NormalizedPaymentEvent(provider='paypal',event_id='paid',event_type='PAYMENT_SUCCEEDED',status='ACTIVE',provider_subscription_id='I-NEW',child_id=child.id,course_id='conversation',charged_amount=plan['price'],monthly_price=plan['price'],billing_period=period))
                assert latest.status=='ACTIVE'
                assert (latest.current_period_end-latest.current_period_start).days >= (28 if period=='MONTH' else 365)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_paypal_cancellation_is_confirmed_before_local_success(monkeypatch):
    calls=[]
    snapshots=iter([{'status':'ACTIVE'}, {'status':'CANCELLED'}])
    async def get(_): return next(snapshots)
    async def request(method,path,**kwargs): calls.append((method,path));return {}
    monkeypatch.setattr(paypal_adapter,'get_paypal_subscription',get)
    monkeypatch.setattr(paypal_adapter,'_request',request)
    sub=Subscription(test_mode=False,payment_provider='paypal',provider_subscription_id='I-1')
    result=await subscription_provider.cancel_provider_renewal(sub)
    assert result['status']=='CANCELLED'
    assert calls==[('POST','/v1/billing/subscriptions/I-1/cancel')]


@pytest.mark.parametrize('failure', ['definitive', 'timeout'])
@pytest.mark.parametrize('return_period', [None,'MONTH','YEAR'])
@pytest.mark.asyncio
async def test_failed_checkout_reservation_and_retry_idempotency(monkeypatch, failure, return_period):
    engine,sessions=await database()
    keys=[]
    class Provider:
        def is_configured(self):return True
        async def create_subscription_checkout(self, **kw):
            keys.append(kw['idempotency_key'])
            if len(keys)==1:
                return payment_provider.CheckoutResult(ok=False, provider='paypal', error='failure', details={'definitive_failure':failure=='definitive'})
            return payment_provider.CheckoutResult(ok=True, provider='paypal', subscription_id='I-RETRY',provider_plan_id='P-1',checkout_url='https://example.test/approve')
    monkeypatch.setattr(payment_provider,'get_payment_provider',lambda _:Provider())
    monkeypatch.setattr(mobile_api,'SessionLocal',sessions)
    try:
        async with sessions() as db:
            parent,child=await family(db)
            if return_period:
                db.add(Subscription(child_id=child.id,course_id='conversation',test_mode=False,status='CANCELLED',lessons_allocated=1,
                    current_period_end=datetime.utcnow()-timedelta(days=1)))
                await db.commit()
        app=web.Application();mobile_api.register_mobile_routes(app)
        async with TestClient(TestServer(app)) as client:
            headers={'Authorization':f'Bearer {issue_session_token(parent.id)}',**device()}
            base=f'/api/mobile/child/{child.id}/subscription'
            overview=await (await client.get(base,headers=headers)).json()
            plan=next(x for x in overview['plans'] if x['billing_period']==(return_period or 'MONTH'))
            body={'plan_id':plan['plan_id'],'version_id':plan['version_id'],'billing_period':return_period or 'MONTH','expected_intro_week_price':plan['intro_week_price'],
                'payment_consents':[{'document_type':'SUBSCRIPTION_TERMS','version':overview['subscription_terms_version'],'accepted':True}]}
            assert (await client.post(base+'/checkout',headers=headers,json=body)).status==400
            async with sessions() as db:
                claims=(await db.scalars(select(IntroOfferClaim))).all()
                assert bool(claims) is (failure=='timeout' and not return_period)
                if claims:
                    assert all(x.provider_subscription_id.startswith('creating:') for x in claims)
            response=await client.post(base+'/checkout',headers=headers,json=body)
            assert response.status==200,await response.text()
            assert (keys[0]==keys[1]) is (failure=='timeout')
            assert len(keys[1])<108
            # Double tapping reuses the completed approval link, not another create.
            response=await client.post(base+'/checkout',headers=headers,json=body)
            assert response.status==200 and (await response.json())['reused_checkout']
            assert len(keys)==2
    finally:await engine.dispose()


@pytest.mark.parametrize('status', ['PAST_DUE','TRIALING','REGISTERED'])
@pytest.mark.asyncio
async def test_existing_collectible_agreement_cannot_spawn_second_checkout(monkeypatch,status):
    engine,sessions=await database()
    monkeypatch.setattr(mobile_api,'SessionLocal',sessions)
    def provider(_):raise AssertionError('No provider create permitted before old agreement resolution')
    monkeypatch.setattr(payment_provider,'get_payment_provider',provider)
    try:
        async with sessions() as db:
            parent,child=await family(db)
            sub=Subscription(child_id=child.id,course_id='conversation',test_mode=False,status=status,payment_provider='paypal',provider_subscription_id='I-EXISTING')
            db.add(sub);await db.commit()
        app=web.Application();mobile_api.register_mobile_routes(app)
        async with TestClient(TestServer(app)) as client:
            response=await client.post(f'/api/mobile/child/{child.id}/subscription/checkout',headers={'Authorization':f'Bearer {issue_session_token(parent.id)}',**device()},json={'plan_id':'weekly1'})
            assert response.status==409
            assert (await response.json())['code']=='SUBSCRIPTION_AGREEMENT_UNRESOLVED'
            async with sessions() as db:
                rows=(await db.scalars(select(Subscription))).all()
                assert len(rows)==1 and rows[0].provider_subscription_id=='I-EXISTING' and rows[0].status==status
    finally:await engine.dispose()


@pytest.mark.parametrize('paid', [False,True,'before'])
@pytest.mark.asyncio
async def test_abandon_requires_provider_cancellation_and_preserves_racing_payment(monkeypatch,paid):
    engine,sessions=await database()
    now=datetime.utcnow()
    snapshot={'status':'CANCELLED','plan_id':'P-1'}
    if paid:snapshot['billing_info']={'last_payment':{'time':now.isoformat()+'Z','amount':{'value':'3','currency_code':'EUR'}}}
    cancelled=[]
    async def cancel(_):cancelled.append(True);return snapshot
    async def get(_):return snapshot if paid=='before' else {'status':'APPROVAL_PENDING'}
    monkeypatch.setattr(paypal_adapter,'get_paypal_subscription',get)
    monkeypatch.setattr(subscription_provider,'cancel_provider_renewal',cancel)
    monkeypatch.setattr(mobile_api,'SessionLocal',sessions)
    monkeypatch.setattr(paypal_adapter,'_meta_from_provider_plan',lambda _:{'intro_week_price':3,'monthly_price':39,'billing_period':'MONTH','plan_id':'weekly1','lessons_per_week':1})
    try:
        async with sessions() as db:
            parent,child=await family(db)
            await intro_offer.reserve(db,parent,device(),'pending')
            await intro_offer.bind(db,'pending','paypal','I-1')
            sub=Subscription(child_id=child.id,course_id='conversation',status='PENDING',test_mode=False,payment_provider='paypal',provider_subscription_id='I-1',provider_plan_id='P-1',checkout_token='pending',intro_week_price=3,lessons_allocated=0)
            db.add(sub);await db.commit()
        app=web.Application();mobile_api.register_mobile_routes(app)
        async with TestClient(TestServer(app)) as client:
            response=await client.post(f'/api/mobile/child/{child.id}/subscription/checkout/abandon',headers={'Authorization':f'Bearer {issue_session_token(parent.id)}',**device()},json={})
            assert response.status==(409 if paid else 200),await response.text()
            async with sessions() as db:
                row=await db.get(Subscription,sub.id)
                claims=(await db.scalars(select(IntroOfferClaim))).all()
                if paid:
                    assert claims and all(x.status=='CONSUMED' for x in claims)
                    assert row.status=='ACTIVE' and row.cancel_at_period_end is (paid != 'before')
                    assert row.current_period_end==now+timedelta(days=7)
                    if paid=='before':assert not cancelled and row.next_charge_at is not None
                    else:assert row.next_charge_at is None
                else:
                    assert not claims and row.status=='CANCELLED'
                    assert (await intro_offer.eligibility(db,parent,device()))[0]
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_cancellation_failure_never_claims_disabled_renewal(monkeypatch):
    engine,sessions=await database()
    async def cancel(_):raise RuntimeError('provider unavailable')
    monkeypatch.setattr(subscription_provider,'cancel_provider_renewal',cancel)
    monkeypatch.setattr(mobile_api,'SessionLocal',sessions)
    try:
        async with sessions() as db:
            parent,child=await family(db)
            sub=Subscription(child_id=child.id,course_id='conversation',status='ACTIVE',test_mode=False,provider_subscription_id='I-1',current_period_end=datetime.utcnow()+timedelta(days=7),lessons_allocated=1)
            db.add(sub);await db.commit()
        app=web.Application();mobile_api.register_mobile_routes(app)
        async with TestClient(TestServer(app)) as client:
            response=await client.post(f'/api/mobile/child/{child.id}/subscription/cancel',headers={'Authorization':f'Bearer {issue_session_token(parent.id)}'},json={})
            assert response.status==409
            async with sessions() as db:assert not (await db.get(Subscription,sub.id)).cancel_at_period_end
    finally:await engine.dispose()


@pytest.mark.asyncio
async def test_annual_offer_changed_between_display_and_checkout_requires_new_consent(monkeypatch):
    from app.services import special_annual_pricing
    engine,sessions=await database()
    special=True
    called=[]
    async def eligibility(*_a,**_kw):return special
    monkeypatch.setattr(special_annual_pricing,'is_eligible_for_special_annual',eligibility)
    monkeypatch.setattr(mobile_api,'SessionLocal',sessions)
    class Provider:
        def is_configured(self):return True
        async def create_subscription_checkout(self,**kw):
            called.append(kw)
            return payment_provider.CheckoutResult(ok=True,provider='paypal',subscription_id='I-QUOTE',provider_plan_id='P-QUOTE',checkout_url='https://example.test/approve')
    monkeypatch.setattr(payment_provider,'get_payment_provider',lambda _:Provider())
    try:
        async with sessions() as db:parent,child=await family(db)
        app=web.Application();mobile_api.register_mobile_routes(app)
        async with TestClient(TestServer(app)) as client:
            headers={'Authorization':f'Bearer {issue_session_token(parent.id)}',**device()}
            base=f'/api/mobile/child/{child.id}/subscription'
            overview=await (await client.get(base,headers=headers)).json()
            plan=next(p for p in overview['plans'] if p['billing_period']=='YEAR')
            meta={'plan_id':plan['plan_id'],'version_id':plan['version_id'],'billing_period':'YEAR','currency':plan['currency'],
                'first_week_amount':str(plan['intro_week_price']),'intro_week_days':'7','annual_amount':str(plan['price']),'renewal_amount':str(plan['standard_renewal_price'])}
            body={'plan_id':plan['plan_id'],'version_id':plan['version_id'],'billing_period':'YEAR','expected_intro_week_price':plan['intro_week_price'],
                'payment_consents':[{'document_type':'SUBSCRIPTION_TERMS','version':overview['subscription_terms_version'],'accepted':True,'metadata':meta}]}
            special=False
            response=await client.post(base+'/checkout',headers=headers,json=body)
            assert response.status==409 and (await response.json())['code']=='CHECKOUT_OFFER_CHANGED'
            assert not called
            special=True
            response=await client.post(base+'/checkout',headers=headers,json=body)
            assert response.status==200,await response.text()
            meta['annual_amount']=str(float(meta['annual_amount'])+1)
            response=await client.post(base+'/checkout',headers=headers,json=body)
            assert response.status==409 and (await response.json())['code']=='CHECKOUT_OFFER_CHANGED'
            assert len(called)==1
    finally:await engine.dispose()
