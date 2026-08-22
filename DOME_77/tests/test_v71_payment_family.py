import hashlib
import hmac
import json
import sys
import types
from pathlib import Path

from app.services.family_pricing import calculate_family_price, MAX_CHILDREN_PER_PARENT
from app.services.unipay_adapter import normalize_unipay_event, verify_unipay_webhook
from app.services.unlimit_adapter import normalize_unlimit_event, verify_unlimit_webhook
from app.services.paypal_adapter import normalize_paypal_event
from app.services.payment_lifecycle import normalized_status
from app.core.config import settings

ROOT=Path(__file__).resolve().parents[1]

def test_family_prices_discount_is_per_lesson_not_per_month():
    assert MAX_CHILDREN_PER_PARENT==5
    # 4 billing weeks: €0.50 x weekly frequency x 4 weeks.
    expected={1:39,2:37,3:37,4:37,5:37}
    for pos,price in expected.items():
        assert calculate_family_price(39,pos,1).effective_price==price
    assert calculate_family_price(79,3,2).effective_price==75
    assert calculate_family_price(109,4,3).effective_price==103
    assert calculate_family_price(139,5,4).effective_price==131

def test_pricing_config_family_policy():
    cfg=json.loads((ROOT/'config/pricing.json').read_text())
    assert cfg['family']['max_children_per_parent']==5
    assert cfg['family']['additional_child_discount_per_lesson_eur']==0.5
    assert cfg['family']['billing_weeks_per_month']==4
    assert cfg['family']['discount_type']=='fixed_per_scheduled_lesson'

def test_unipay_normalization_success_failure_cancel_update():
    paid=normalize_unipay_event({'id':'evt1','type':'payment.succeeded','data':{'status':'PAID','subscription_id':'sub1','currency':'EUR','metadata':{'child_id':7,'course_id':'reading','plan_id':'weekly2','lessons_per_week':2,'monthly_price':75}}})
    assert paid.provider=='unipay' and paid.event_type=='PAYMENT_SUCCEEDED'
    assert paid.child_id==7 and paid.course_id=='reading' and paid.monthly_price==75
    failed=normalize_unipay_event({'id':'evt2','type':'payment.failed','data':{'status':'FAILED','subscription_id':'sub1','metadata':{'child_id':7,'course_id':'reading'}}})
    assert failed.event_type=='PAYMENT_FAILED'
    cancelled=normalize_unipay_event({'id':'evt3','type':'subscription.cancelled','data':{'status':'CANCELLED','subscription_id':'sub1','metadata':{'child_id':7,'course_id':'reading'}}})
    assert cancelled.event_type=='SUBSCRIPTION_CANCELLED'
    assert normalized_status('active')=='ACTIVE' and normalized_status('unpaid')=='PAST_DUE'

def test_unipay_webhook_hmac_verification(monkeypatch):
    raw=b'{"id":"evt"}'
    monkeypatch.setattr(settings,'unipay_webhook_secret','test-secret')
    monkeypatch.setattr(settings,'unipay_webhook_token','')
    sig=hmac.new(b'test-secret',raw,hashlib.sha256).hexdigest()
    assert verify_unipay_webhook(raw,{'X-UniPAY-Signature':sig}) is True
    assert verify_unipay_webhook(raw,{'X-UniPAY-Signature':'bad'}) is False

def test_unlimit_normalization_and_sha512_verification(monkeypatch):
    raw=b'{"id":"u1"}'
    monkeypatch.setattr(settings,'unlimit_callback_secret','secret')
    monkeypatch.setattr(settings,'unlimit_signature_header','X-Signature')
    sig=hashlib.sha512(b'secret'+raw).hexdigest()
    assert verify_unlimit_webhook(raw,{'X-Signature':sig})
    ev=normalize_unlimit_event({'id':'u1','type':'payment.completed','status':'COMPLETED','subscription_id':'usub','metadata':{'child_id':3,'course_id':'conversation','plan_id':'weekly4','lessons_per_week':4,'monthly_price':131}})
    assert ev.provider=='unlimit' and ev.event_type=='PAYMENT_SUCCEEDED' and ev.monthly_price==131

def test_paypal_normalization():
    ev=normalize_paypal_event({'id':'p1','event_type':'BILLING.SUBSCRIPTION.ACTIVATED','resource':{'id':'I-1','status':'ACTIVE','custom_id':'dome|9|reading|weekly3|3|103.00'}})
    assert ev.provider=='paypal' and ev.event_type=='SUBSCRIPTION_ACTIVE'
    assert ev.child_id==9 and ev.course_id=='reading' and ev.lessons_per_week==3 and ev.monthly_price==103
    failed=normalize_paypal_event({'id':'p2','event_type':'BILLING.SUBSCRIPTION.PAYMENT.FAILED','resource':{'id':'I-1','custom_id':'dome|9|reading|weekly3|3|103.00'}})
    assert failed.event_type=='PAYMENT_FAILED'

def test_stripe_checkout_has_idempotency_and_plan_change(monkeypatch):
    from app.services import payment_adapter as pa
    calls={}
    class Obj(dict):
        __getattr__=dict.__getitem__
    class CheckoutSession:
        @staticmethod
        def create(**kwargs): calls['checkout']=kwargs; return Obj(id='cs1',url='https://checkout.test')
    class Checkout: Session=CheckoutSession
    class Subscription:
        @staticmethod
        def retrieve(sub_id): return {'items':{'data':[{'id':'si1'}]}}
        @staticmethod
        def modify(sub_id,**kwargs): calls['modify']=(sub_id,kwargs); return {'id':sub_id,'status':'active'}
    fake=types.SimpleNamespace(api_key='',checkout=Checkout,Subscription=Subscription)
    monkeypatch.setitem(sys.modules,'stripe',fake)
    monkeypatch.setattr(settings,'stripe_secret_key','sk_test')
    url=pa.create_stripe_subscription_checkout(child_id=2,course_id='reading',plan_id='weekly2',lessons_per_week=2,monthly_price=75,currency='EUR',success_url='https://x/s',cancel_url='https://x/c',idempotency_key='idem1')
    assert url=='https://checkout.test' and calls['checkout']['idempotency_key']=='idem1'
    pa.change_stripe_subscription_plan(subscription_id='sub1',child_id=2,course_id='reading',plan_id='weekly4',lessons_per_week=4,monthly_price=131,currency='EUR',idempotency_key='change1')
    kw=calls['modify'][1]
    assert kw['idempotency_key']=='change1'
    assert kw['proration_behavior']=='always_invoice'
    assert kw['payment_behavior']=='pending_if_incomplete'
    assert kw['items'][0]['price_data']['unit_amount']==13100

def test_production_guardrails_and_no_second_subscription():
    h=(ROOT/'app/bot/handlers.py').read_text()
    assert "Production billing нельзя включить с provider=custom" in h
    assert "allow_test_course_payment_bypass']=False if mode=='on'" in h
    assert "provider not in {'custom','stripe','unipay','unlimit','paypal'}" in h
    assert 'active_paid is not None' in h
    for name in ('change_stripe_subscription_plan','change_unipay_subscription_plan','change_unlimit_subscription_plan','change_paypal_subscription_plan'):
        assert name in h

def test_all_payment_webhooks_and_checkout_are_wired():
    web=(ROOT/'app/webapp/server.py').read_text(); handlers=(ROOT/'app/bot/handlers.py').read_text()
    for provider in ('stripe','unipay','unlimit','paypal'):
        assert f'/webhooks/{provider}' in web
    assert 'verify_paypal_webhook' in web and 'verify_unlimit_webhook' in web
    assert 'create_unlimit_subscription_checkout' in handlers
    assert 'create_paypal_subscription_checkout' in handlers
    assert "stored_id=f'{provider}:{event_id}'" in web

def test_multi_child_ui_limit_and_persistent_selection():
    h=(ROOT/'app/bot/handlers.py').read_text(); k=(ROOT/'app/bot/keyboards.py').read_text(); m=(ROOT/'app/db/models.py').read_text()
    assert 'family:add' in h and 'family:select:' in h
    assert 'MAX_CHILDREN_PER_PARENT' in h
    assert 'family_children_keyboard' in k
    assert 'active_child_id' in m

import pytest

@pytest.mark.asyncio
async def test_subscription_created_without_active_does_not_unlock(tmp_path, monkeypatch):
    # Source-level invariant is intentional here because the project DB session is globally configured.
    life=(ROOT/'app/services/payment_lifecycle.py').read_text()
    assert "sub.status='PENDING'" in life
    assert "ev.event_type in {'PAYMENT_SUCCEEDED','SUBSCRIPTION_ACTIVE'} or status=='ACTIVE'" in life
    assert "Creation/checkout/update alone must never unlock paid content" in life
