from __future__ import annotations
import base64, json
from datetime import datetime
from typing import Any
import aiohttp
from app.core.config import settings
from app.services.payment_lifecycle import NormalizedPaymentEvent
from app.services.platform_settings import load_settings, save_settings

class PayPalError(RuntimeError): pass

def _paypal_datetime(value:object)->datetime|None:
    raw=str(value or '').strip()
    if not raw:return None
    try:return datetime.fromisoformat(raw.replace('Z','+00:00')).replace(tzinfo=None)
    except (TypeError,ValueError):return None

def _base() -> str:
    return 'https://api-m.paypal.com' if str(settings.paypal_mode).lower()=='live' else 'https://api-m.sandbox.paypal.com'

async def _token() -> str:
    if not (settings.paypal_client_id and settings.paypal_client_secret):
        raise PayPalError('PAYPAL_CLIENT_ID/PAYPAL_CLIENT_SECRET не настроены')
    auth=base64.b64encode(f'{settings.paypal_client_id}:{settings.paypal_client_secret}'.encode()).decode()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as s:
        async with s.post(_base()+'/v1/oauth2/token',headers={'Authorization':'Basic '+auth,'Content-Type':'application/x-www-form-urlencoded'},data='grant_type=client_credentials') as r:
            data=await r.json(content_type=None)
            if r.status>=400 or not data.get('access_token'): raise PayPalError(f'PayPal OAuth {r.status}: {str(data)[:500]}')
            return str(data['access_token'])

async def _request(method:str,path:str,*,body:dict|None=None,request_id:str='') -> dict:
    token=await _token(); headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
    if request_id: headers['PayPal-Request-Id']=request_id[:108]
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        async with s.request(method,_base()+path,headers=headers,json=body) as r:
            text=await r.text()
            if r.status>=400: raise PayPalError(f'PayPal HTTP {r.status}: {text[:700]}')
            if not text: return {'ok':True}
            try:return json.loads(text)
            except:return {'raw':text}

def _plan_cache_key(plan_id:str,monthly_price:float,currency:str)->str:
    return f'{plan_id}:{currency.upper()}:{monthly_price:.2f}'

async def _ensure_product() -> str:
    if settings.paypal_product_id: return settings.paypal_product_id
    cfg=load_settings('payments'); cached=str(cfg.get('paypal_product_id') or '')
    if cached:return cached
    data=await _request('POST','/v1/catalogs/products',body={'name':'DOME language learning','description':'DOME child language-learning subscription','type':'SERVICE','category':'EDUCATIONAL_AND_TEXTBOOKS'},request_id='dome-product-v71')
    pid=str(data.get('id') or '')
    if not pid: raise PayPalError('PayPal не вернул product_id')
    cfg['paypal_product_id']=pid; save_settings('payments',cfg); return pid

async def ensure_paypal_plan(*,plan_id:str,lessons_per_week:int,monthly_price:float,currency:str)->str:
    cfg=load_settings('payments'); cache=dict(cfg.get('paypal_plan_cache') or {})
    key=_plan_cache_key(plan_id,monthly_price,currency)
    if cache.get(key): return str(cache[key])
    product_id=await _ensure_product()
    body={'product_id':product_id,'name':f'DOME {lessons_per_week}x/week €{monthly_price:.2f}','description':f'DOME {lessons_per_week} lessons/week monthly subscription','status':'ACTIVE','billing_cycles':[{'frequency':{'interval_unit':'MONTH','interval_count':1},'tenure_type':'REGULAR','sequence':1,'total_cycles':0,'pricing_scheme':{'fixed_price':{'value':f'{monthly_price:.2f}','currency_code':currency.upper()}}}],'payment_preferences':{'auto_bill_outstanding':True,'payment_failure_threshold':3}}
    data=await _request('POST','/v1/billing/plans',body=body,request_id='dome-plan-'+key.replace(':','-'))
    pp=str(data.get('id') or '')
    if not pp: raise PayPalError('PayPal не вернул plan_id')
    cache[key]=pp; cfg['paypal_plan_cache']=cache; save_settings('payments',cfg); return pp

def _custom_id(child_id:int,course_id:str,plan_id:str,freq:int,price:float)->str:
    return f'dome|{child_id}|{course_id}|{plan_id}|{freq}|{price:.2f}'[:127]

def _parse_custom_id(value:str)->dict:
    parts=str(value or '').split('|')
    if len(parts)>=6 and parts[0]=='dome':
        try:return {'child_id':int(parts[1]),'course_id':parts[2],'plan_id':parts[3],'lessons_per_week':int(parts[4]),'monthly_price':float(parts[5])}
        except:return {}
    return {}

def _meta_from_provider_plan(provider_plan_id:str)->dict:
    if not provider_plan_id:return {}
    cache=dict(load_settings('payments').get('paypal_plan_cache') or {})
    for key,value in cache.items():
        if str(value)==str(provider_plan_id):
            try:
                plan_id,currency,price=key.split(':',2)
                freq=int(str(plan_id).replace('weekly',''))
                return {'plan_id':plan_id,'lessons_per_week':freq,'monthly_price':float(price),'currency':currency}
            except Exception:return {}
    return {}

async def create_paypal_subscription_checkout(*,child_id:int,course_id:str,plan_id:str,lessons_per_week:int,monthly_price:float,currency:str,success_url:str,cancel_url:str,idempotency_key:str='')->str:
    pp=await ensure_paypal_plan(plan_id=plan_id,lessons_per_week=lessons_per_week,monthly_price=monthly_price,currency=currency)
    body={'plan_id':pp,'custom_id':_custom_id(child_id,course_id,plan_id,lessons_per_week,monthly_price),'application_context':{'brand_name':'DOME / BilingvaDom','user_action':'SUBSCRIBE_NOW','return_url':success_url,'cancel_url':cancel_url}}
    data=await _request('POST','/v1/billing/subscriptions',body=body,request_id=idempotency_key)
    for link in data.get('links') or []:
        if str(link.get('rel'))=='approve' and link.get('href'): return str(link['href'])
    raise PayPalError('PayPal не вернул approve URL')

async def change_paypal_subscription_plan(*,subscription_id:str,child_id:int,course_id:str,plan_id:str,lessons_per_week:int,monthly_price:float,currency:str,success_url:str,cancel_url:str,idempotency_key:str='')->dict:
    pp=await ensure_paypal_plan(plan_id=plan_id,lessons_per_week=lessons_per_week,monthly_price=monthly_price,currency=currency)
    body={'plan_id':pp,'application_context':{'brand_name':'DOME / BilingvaDom','return_url':success_url,'cancel_url':cancel_url}}
    data=await _request('POST',f'/v1/billing/subscriptions/{subscription_id}/revise',body=body,request_id=idempotency_key)
    # PayPal requires buyer re-consent for PayPal-funded subscription plan changes.
    approval=''
    for link in data.get('links') or []:
        if str(link.get('rel'))=='approve': approval=str(link.get('href') or '')
    return {'id':subscription_id,'status':'PENDING_APPROVAL','approval_url':approval,'raw':data}

async def get_paypal_subscription(subscription_id:str)->dict:
    return await _request('GET',f'/v1/billing/subscriptions/{subscription_id}')

async def verify_paypal_webhook(raw:bytes, data:dict, headers:Any)->bool:
    """Verify with PayPal postback while preserving the original webhook_event bytes.

    PayPal explicitly warns not to parse and re-stringify webhook_event for the
    postback verification method, so this function embeds the original raw JSON.
    """
    if not settings.paypal_webhook_id:return False
    fields={
        'auth_algo':headers.get('PAYPAL-AUTH-ALGO') or headers.get('Paypal-Auth-Algo'),
        'cert_url':headers.get('PAYPAL-CERT-URL') or headers.get('Paypal-Cert-Url'),
        'transmission_id':headers.get('PAYPAL-TRANSMISSION-ID') or headers.get('Paypal-Transmission-Id'),
        'transmission_sig':headers.get('PAYPAL-TRANSMISSION-SIG') or headers.get('Paypal-Transmission-Sig'),
        'transmission_time':headers.get('PAYPAL-TRANSMISSION-TIME') or headers.get('Paypal-Transmission-Time'),
        'webhook_id':settings.paypal_webhook_id,
    }
    if not all(fields.values()):return False
    token=await _token(); headers_out={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'}
    prefix=json.dumps(fields,separators=(',',':'))[:-1]+',"webhook_event":'
    body=prefix.encode()+raw+b'}'
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as sess:
        async with sess.post(_base()+'/v1/notifications/verify-webhook-signature',headers=headers_out,data=body) as r:
            try:result=await r.json(content_type=None)
            except:return False
            return r.status<400 and str(result.get('verification_status') or '').upper()=='SUCCESS'

def normalize_paypal_event(data:dict,subscription:dict|None=None)->NormalizedPaymentEvent:
    typ=str(data.get('event_type') or '').upper(); resource=data.get('resource') if isinstance(data.get('resource'),dict) else {}; sub=subscription or {}
    mapping={'BILLING.SUBSCRIPTION.CREATED':'SUBSCRIPTION_CREATED','BILLING.SUBSCRIPTION.ACTIVATED':'SUBSCRIPTION_ACTIVE','BILLING.SUBSCRIPTION.UPDATED':'SUBSCRIPTION_UPDATED','BILLING.SUBSCRIPTION.CANCELLED':'SUBSCRIPTION_CANCELLED','BILLING.SUBSCRIPTION.EXPIRED':'SUBSCRIPTION_CANCELLED','BILLING.SUBSCRIPTION.SUSPENDED':'SUBSCRIPTION_PAUSED','BILLING.SUBSCRIPTION.PAYMENT.FAILED':'PAYMENT_FAILED','PAYMENT.SALE.COMPLETED':'PAYMENT_SUCCEEDED','PAYMENT.SALE.REVERSED':'PAYMENT_FAILED','PAYMENT.SALE.REFUNDED':'PAYMENT_FAILED'}
    event_type=mapping.get(typ,'SUBSCRIPTION_UPDATED')
    provider_sub_id=str(resource.get('id') or '') if typ.startswith('BILLING.SUBSCRIPTION.') else str(resource.get('billing_agreement_id') or resource.get('billing_agreement') or '')
    if not provider_sub_id: provider_sub_id=str(sub.get('id') or '')
    meta=_parse_custom_id(resource.get('custom_id') or sub.get('custom_id') or '')
    plan_meta=_meta_from_provider_plan(str(resource.get('plan_id') or sub.get('plan_id') or ''))
    if plan_meta:
        meta.update(plan_meta)
    status=str(resource.get('status') or sub.get('status') or '')
    if typ in {'BILLING.SUBSCRIPTION.ACTIVATED','PAYMENT.SALE.COMPLETED'}:status='ACTIVE'
    elif typ in {'BILLING.SUBSCRIPTION.PAYMENT.FAILED','PAYMENT.SALE.REVERSED'}:status='PAST_DUE'
    elif typ in {'BILLING.SUBSCRIPTION.CANCELLED','BILLING.SUBSCRIPTION.EXPIRED','BILLING.SUBSCRIPTION.SUSPENDED'}:status='CANCELLED'
    amount=resource.get('amount') if isinstance(resource.get('amount'),dict) else {}
    try:charged=float(amount.get('value') or amount.get('total') or 0)
    except (TypeError,ValueError):charged=0.0
    billing=(sub.get('billing_info') if isinstance(sub.get('billing_info'),dict) else {}) or (resource.get('billing_info') if isinstance(resource.get('billing_info'),dict) else {})
    last=billing.get('last_payment') if isinstance(billing.get('last_payment'),dict) else {}
    return NormalizedPaymentEvent(provider='paypal',event_id=str(data.get('id') or ''),event_type=event_type,status=status,child_id=int(meta.get('child_id') or 0),course_id=str(meta.get('course_id') or ''),plan_id=str(meta.get('plan_id') or ''),lessons_per_week=int(meta.get('lessons_per_week') or 1),monthly_price=float(meta.get('monthly_price') or 0),currency=str(meta.get('currency') or amount.get('currency') or amount.get('currency_code') or 'EUR'),provider_subscription_id=provider_sub_id,occurred_at=_paypal_datetime(data.get('create_time')),period_start=_paypal_datetime(last.get('time')) or _paypal_datetime(data.get('create_time')),period_end=_paypal_datetime(billing.get('next_billing_time')),charged_amount=charged,raw=data)
