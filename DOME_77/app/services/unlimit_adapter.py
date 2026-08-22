from __future__ import annotations
import hashlib, hmac, json
from typing import Any
import aiohttp
from app.core.config import settings
from app.services.payment_lifecycle import NormalizedPaymentEvent

class UnlimitError(RuntimeError): pass

async def _access_token()->str:
    if settings.unlimit_api_token:return settings.unlimit_api_token
    if not (settings.unlimit_token_url and settings.unlimit_terminal_code and settings.unlimit_password):
        raise UnlimitError('Нужен UNLIMIT_API_TOKEN либо UNLIMIT_TOKEN_URL + TERMINAL_CODE + PASSWORD')
    payload={'terminal_code':settings.unlimit_terminal_code,'password':settings.unlimit_password}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as s:
        async with s.post(settings.unlimit_token_url,headers={'Accept':'application/json','Content-Type':'application/json'},json=payload) as r:
            text=await r.text()
            if r.status>=400:raise UnlimitError(f'Unlimit auth HTTP {r.status}: {text[:500]}')
            try:data=json.loads(text)
            except:raise UnlimitError('Unlimit auth вернул не-JSON')
    token=str(data.get('access_token') or '')
    if not token:raise UnlimitError('Unlimit auth не вернул access_token')
    return token

async def _post(url:str,payload:dict,idempotency_key:str='')->dict:
    if not url:raise UnlimitError('Unlimit API endpoint не настроен')
    token=await _access_token()
    headers={'Accept':'application/json','Content-Type':'application/json','Authorization':'Bearer '+token}
    if idempotency_key:headers['Idempotency-Key']=idempotency_key
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        async with s.post(url,headers=headers,json=payload) as r:
            text=await r.text()
            if r.status>=400:raise UnlimitError(f'Unlimit HTTP {r.status}: {text[:700]}')
            try:return json.loads(text)
            except:raise UnlimitError('Unlimit вернул не-JSON ответ')

def _meta(child_id,course_id,plan_id,lessons_per_week,monthly_price):
    return {'child_id':child_id,'course_id':course_id,'plan_id':plan_id,'lessons_per_week':lessons_per_week,'monthly_price':monthly_price}

async def create_unlimit_subscription_checkout(*,child_id:int,course_id:str,plan_id:str,lessons_per_week:int,monthly_price:float,currency:str,success_url:str,cancel_url:str,webhook_url:str,idempotency_key:str='')->str:
    # Payment Page flow. Exact merchant endpoint/terminal credentials come from Unlimit onboarding.
    url=(settings.unlimit_recurring_url or settings.unlimit_payment_url).strip()
    payload={'merchant_order':{'id':f'dome-{child_id}-{course_id}-{plan_id}-{idempotency_key[-12:]}','description':f'DOME {lessons_per_week}x/week'},'payment_data':{'amount':f'{monthly_price:.2f}','currency':currency.upper(),'recurring':True},'return_urls':{'success_url':success_url,'decline_url':cancel_url,'cancel_url':cancel_url},'callback_url':webhook_url,'metadata':_meta(child_id,course_id,plan_id,lessons_per_week,monthly_price)}
    data=await _post(url,payload,idempotency_key)
    candidates=[data]
    for k in ('payment_data','redirect','data'):
        if isinstance(data.get(k),dict):candidates.append(data[k])
    for obj in candidates:
        for key in ('redirect_url','payment_url','checkout_url','url'):
            if obj.get(key):return str(obj[key])
    raise UnlimitError('Unlimit не вернул redirect URL')

async def change_unlimit_subscription_plan(*,subscription_id:str,child_id:int,course_id:str,plan_id:str,lessons_per_week:int,monthly_price:float,currency:str,webhook_url:str,idempotency_key:str='')->dict:
    url=settings.unlimit_recurring_update_url.strip()
    if not url:raise UnlimitError('UNLIMIT_RECURRING_UPDATE_URL не настроен; новая подписка поверх активной не создаётся')
    url=url.replace('{subscription_id}',subscription_id)
    payload={'subscription_id':subscription_id,'amount':f'{monthly_price:.2f}','currency':currency.upper(),'metadata':_meta(child_id,course_id,plan_id,lessons_per_week,monthly_price),'callback_url':webhook_url}
    return await _post(url,payload,idempotency_key)

def verify_unlimit_webhook(raw:bytes,headers:Any)->bool:
    secret=settings.unlimit_callback_secret.strip()
    if not secret:return False
    supplied=(headers.get(settings.unlimit_signature_header or 'X-Signature') or headers.get('X-Signature') or headers.get('Signature') or '').strip()
    if supplied.lower().startswith('sha512='):supplied=supplied.split('=',1)[1]
    # Official Unlimit callbacks use SHA-512 with the callback secret and JSON body.
    # Support the two deployed representations seen across merchant stacks while still requiring the secret.
    concat=hashlib.sha512(secret.encode()+raw).hexdigest()
    hm=hmac.new(secret.encode(),raw,hashlib.sha512).hexdigest()
    return bool(supplied) and (hmac.compare_digest(supplied.lower(),concat.lower()) or hmac.compare_digest(supplied.lower(),hm.lower()))

def normalize_unlimit_event(data:dict)->NormalizedPaymentEvent:
    payload=data.get('data') if isinstance(data.get('data'),dict) else data
    meta=payload.get('metadata') if isinstance(payload.get('metadata'),dict) else {}
    event_id=str(data.get('id') or data.get('event_id') or payload.get('id') or payload.get('payment_id') or '')
    raw_type=str(data.get('type') or data.get('event_type') or payload.get('type') or '').lower()
    status=str(payload.get('status') or data.get('status') or '').upper()
    mapping={'payment.completed':'PAYMENT_SUCCEEDED','payment.succeeded':'PAYMENT_SUCCEEDED','payment.failed':'PAYMENT_FAILED','subscription.created':'SUBSCRIPTION_CREATED','subscription.activated':'SUBSCRIPTION_ACTIVE','subscription.updated':'SUBSCRIPTION_UPDATED','subscription.cancelled':'SUBSCRIPTION_CANCELLED','subscription.canceled':'SUBSCRIPTION_CANCELLED','subscription.suspended':'SUBSCRIPTION_PAUSED'}
    typ=mapping.get(raw_type)
    if not typ:
        if status in {'COMPLETED','SUCCESS','SUCCEEDED','ACTIVE'}:typ='PAYMENT_SUCCEEDED'
        elif status in {'FAILED','DECLINED','PAST_DUE'}:typ='PAYMENT_FAILED'
        elif status in {'CANCELLED','CANCELED','SUSPENDED','EXPIRED'}:typ='SUBSCRIPTION_CANCELLED'
        else:typ='SUBSCRIPTION_UPDATED'
    def iv(v,d=0):
        try:return int(v)
        except:return d
    def fv(v,d=0.0):
        try:return float(v)
        except:return d
    return NormalizedPaymentEvent(provider='unlimit',event_id=event_id,event_type=typ,status=status,child_id=iv(meta.get('child_id') or payload.get('child_id')),course_id=str(meta.get('course_id') or payload.get('course_id') or ''),plan_id=str(meta.get('plan_id') or payload.get('plan_id') or ''),lessons_per_week=max(1,min(4,iv(meta.get('lessons_per_week') or payload.get('lessons_per_week'),1))),monthly_price=fv(meta.get('monthly_price') or payload.get('amount')),currency=str(payload.get('currency') or 'EUR'),provider_subscription_id=str(payload.get('subscription_id') or payload.get('recurring_id') or ''),raw=data)
