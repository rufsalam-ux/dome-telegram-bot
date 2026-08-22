from __future__ import annotations
import hashlib, hmac, json
from typing import Any
import aiohttp
from app.core.config import settings
from app.services.payment_lifecycle import NormalizedPaymentEvent

class UniPayError(RuntimeError): pass


def _headers() -> dict[str,str]:
    h={'Accept':'application/json','Content-Type':'application/json'}
    if settings.unipay_access_token:
        h['Authorization']=f'Bearer {settings.unipay_access_token}'
    if settings.unipay_merchant_id:
        h[settings.unipay_merchant_id_header or 'X-Merchant-Id']=settings.unipay_merchant_id
    if settings.unipay_api_key:
        h[settings.unipay_api_key_header or 'X-Api-Key']=settings.unipay_api_key
    return h

async def create_unipay_subscription_checkout(*, child_id:int, course_id:str, plan_id:str, lessons_per_week:int, monthly_price:float, currency:str, success_url:str, cancel_url:str, webhook_url:str, idempotency_key:str='') -> str:
    """Create a hosted recurring-payment checkout using merchant-supplied UniPAY V3 endpoint.

    UniPAY exposes Checkout, saved-card and subscription capabilities, but account-specific
    endpoint/field contracts can differ. DOME therefore does not guess a private merchant URL:
    UNIPAY_SUBSCRIPTION_URL must be copied from the merchant's UniPAY V3 credentials/docs.
    The adapter accepts common response URL fields and keeps all card data on UniPAY.
    """
    endpoint=settings.unipay_subscription_url.strip()
    if not endpoint: raise UniPayError('UNIPAY_SUBSCRIPTION_URL не настроен')
    if not (settings.unipay_access_token or (settings.unipay_merchant_id and settings.unipay_api_key)):
        raise UniPayError('UniPAY credentials не настроены')
    metadata={'child_id':child_id,'course_id':course_id,'plan_id':plan_id,'lessons_per_week':lessons_per_week,'monthly_price':monthly_price}
    payload={
        'amount':round(float(monthly_price),2),'currency':currency.upper(),
        'description':f'DOME · {lessons_per_week}×/нед',
        'recurring':True,'interval':'month','metadata':metadata,
        'success_url':success_url,'cancel_url':cancel_url,'callback_url':webhook_url,
        'webhook_url':webhook_url,'reference':f'dome:{child_id}:{course_id}:{plan_id}'
    }
    timeout=aiohttp.ClientTimeout(total=25)
    headers=_headers()
    if idempotency_key: headers['Idempotency-Key']=idempotency_key
    if idempotency_key: payload['idempotency_key']=idempotency_key
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(endpoint,headers=headers,json=payload) as resp:
            text=await resp.text()
            if resp.status>=400: raise UniPayError(f'UniPAY HTTP {resp.status}: {text[:500]}')
            try: data=json.loads(text)
            except Exception: raise UniPayError('UniPAY вернул не-JSON ответ')
    for key in ('checkout_url','payment_url','redirect_url','url'):
        if data.get(key): return str(data[key])
    nested=data.get('data') if isinstance(data.get('data'),dict) else {}
    for key in ('checkout_url','payment_url','redirect_url','url'):
        if nested.get(key): return str(nested[key])
    raise UniPayError('UniPAY не вернул URL страницы оплаты')


def verify_unipay_webhook(raw:bytes, headers:Any) -> bool:
    secret=settings.unipay_webhook_secret.strip()
    token=settings.unipay_webhook_token.strip()
    if secret:
        configured=settings.unipay_webhook_signature_header or 'X-UniPAY-Signature'
        supplied=(headers.get(configured) or headers.get('X-UniPAY-Signature') or headers.get('X-Unipay-Signature') or headers.get('X-Signature') or '').strip()
        prefix=(settings.unipay_webhook_signature_prefix or '').strip()
        if prefix and supplied.lower().startswith(prefix.lower()): supplied=supplied[len(prefix):]
        elif supplied.lower().startswith('sha256='): supplied=supplied.split('=',1)[1]
        expected=hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()
        return bool(supplied) and hmac.compare_digest(supplied.lower(),expected.lower())
    if token:
        configured=settings.unipay_webhook_token_header or 'X-Webhook-Token'
        supplied=(headers.get('Authorization') or headers.get(configured) or headers.get('X-Webhook-Token') or '').strip()
        if supplied.lower().startswith('bearer '): supplied=supplied[7:].strip()
        return hmac.compare_digest(supplied,token)
    return False


def normalize_unipay_event(data:dict[str,Any]) -> NormalizedPaymentEvent:
    payload=data.get('data') if isinstance(data.get('data'),dict) else data
    raw_meta=payload.get('metadata')
    if isinstance(raw_meta,dict): meta=raw_meta
    elif isinstance(raw_meta,str):
        try: meta=json.loads(raw_meta) if raw_meta.strip().startswith('{') else {}
        except Exception: meta={}
    else: meta={}
    event_id=str(data.get('event_id') or data.get('eventId') or data.get('id') or payload.get('event_id') or payload.get('eventId') or payload.get('transaction_id') or payload.get('transactionId') or payload.get('id') or '')
    raw_type=str(data.get('type') or data.get('event') or payload.get('event_type') or payload.get('type') or '').lower()
    status=str(payload.get('status') or data.get('status') or '').upper()
    mapping={
        'subscription.created':'SUBSCRIPTION_CREATED','subscription.active':'SUBSCRIPTION_ACTIVE',
        'subscription.updated':'SUBSCRIPTION_UPDATED','subscription.changed':'PLAN_CHANGED',
        'subscription.cancelled':'SUBSCRIPTION_CANCELLED','subscription.canceled':'SUBSCRIPTION_CANCELLED',
        'subscription.paused':'SUBSCRIPTION_PAUSED','payment.succeeded':'PAYMENT_SUCCEEDED',
        'payment.paid':'PAYMENT_SUCCEEDED','payment.failed':'PAYMENT_FAILED','payment.declined':'PAYMENT_FAILED',
        'checkout.completed':'CHECKOUT_COMPLETED'
    }
    typ=mapping.get(raw_type)
    if not typ:
        if status in {'PAID','SUCCEEDED','SUCCESS','ACTIVE'}: typ='PAYMENT_SUCCEEDED'
        elif status in {'FAILED','DECLINED','UNPAID','PAST_DUE'}: typ='PAYMENT_FAILED'
        elif status in {'CANCELLED','CANCELED','PAUSED','DELETED'}: typ='SUBSCRIPTION_CANCELLED'
        else: typ='SUBSCRIPTION_UPDATED'
    def _int(v,default=0):
        try:return int(v)
        except:return default
    def _float(v,default=0.0):
        try:return float(v)
        except:return default
    return NormalizedPaymentEvent(
        provider='unipay', event_id=event_id, event_type=typ, status=status,
        child_id=_int(meta.get('child_id') or payload.get('child_id')),
        course_id=str(meta.get('course_id') or payload.get('course_id') or ''),
        plan_id=str(meta.get('plan_id') or payload.get('plan_id') or ''),
        lessons_per_week=max(1,min(4,_int(meta.get('lessons_per_week') or payload.get('lessons_per_week'),1))),
        monthly_price=_float(meta.get('monthly_price') or payload.get('monthly_price') or payload.get('amount')),
        currency=str(payload.get('currency') or data.get('currency') or 'EUR'),
        provider_subscription_id=str(payload.get('subscription_id') or payload.get('subscriptionId') or payload.get('recurring_id') or payload.get('recurringId') or payload.get('RegularpaymentID') or payload.get('RegularPaymentID') or ''), raw=data)


async def change_unipay_subscription_plan(*, subscription_id:str, child_id:int, course_id:str, plan_id:str, lessons_per_week:int, monthly_price:float, currency:str, webhook_url:str, idempotency_key:str='') -> dict:
    endpoint=settings.unipay_subscription_update_url.strip()
    if not endpoint:
        raise UniPayError('UNIPAY_SUBSCRIPTION_UPDATE_URL не настроен; новая подписка поверх старой не создаётся ради защиты от двойного списания')
    endpoint=endpoint.replace('{subscription_id}',subscription_id)
    metadata={'child_id':child_id,'course_id':course_id,'plan_id':plan_id,'lessons_per_week':lessons_per_week,'monthly_price':monthly_price}
    payload={'subscription_id':subscription_id,'amount':round(float(monthly_price),2),'currency':currency.upper(),'interval':'month','metadata':metadata,'callback_url':webhook_url,'webhook_url':webhook_url}
    headers=_headers()
    if idempotency_key: headers['Idempotency-Key']=idempotency_key; payload['idempotency_key']=idempotency_key
    timeout=aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(endpoint,headers=headers,json=payload) as resp:
            text=await resp.text()
            if resp.status>=400: raise UniPayError(f'UniPAY plan change HTTP {resp.status}: {text[:500]}')
            try:return json.loads(text) if text else {'ok':True}
            except Exception:return {'ok':True,'raw':text[:500]}
