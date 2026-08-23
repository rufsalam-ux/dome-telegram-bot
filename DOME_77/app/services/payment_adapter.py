from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from app.core.config import settings
from app.services.platform_settings import load_settings, save_settings

@dataclass(frozen=True)
class SavedPaymentMethod:
    provider: str
    customer_token: str
    payment_method_token: str
    last4: str = ""

class PaymentAdapter(Protocol):
    name: str
    async def create_card_setup(self, *, customer_ref: str, return_url: str) -> dict: ...
    async def charge_saved_method(self, *, payment_method_token: str, amount_minor: int, currency: str, idempotency_key: str, description: str) -> dict: ...

class UnsupportedPaymentAdapter:
    def __init__(self,name:str): self.name=name
    async def create_card_setup(self, **kwargs): raise RuntimeError(f"Платёжный адаптер {self.name} ещё не подключён к API провайдера")
    async def charge_saved_method(self, **kwargs): raise RuntimeError(f"Платёжный адаптер {self.name} ещё не подключён к API провайдера")

class StripeAdapter:
    name='stripe'
    def _stripe(self):
        if not settings.stripe_secret_key: raise RuntimeError('STRIPE_SECRET_KEY не настроен')
        import stripe
        stripe.api_key=settings.stripe_secret_key
        return stripe
    async def create_card_setup(self, *, customer_ref:str, return_url:str)->dict:
        st=self._stripe(); session=st.checkout.Session.create(mode='setup',success_url=return_url,cancel_url=return_url,metadata={'customer_ref':customer_ref})
        return {'id':session.id,'url':session.url}
    async def charge_saved_method(self, *, payment_method_token:str, amount_minor:int, currency:str, idempotency_key:str, description:str)->dict:
        st=self._stripe(); pi=st.PaymentIntent.create(amount=amount_minor,currency=currency.lower(),payment_method=payment_method_token,confirm=True,off_session=True,description=description,idempotency_key=idempotency_key)
        return {'id':pi.id,'status':pi.status}

def get_payment_adapter(name:str) -> PaymentAdapter:
    n=(name or settings.payment_provider or 'custom').lower()
    if n=='stripe': return StripeAdapter()
    if n=='unipay':
        # Hosted checkout/subscription integration lives in unipay_adapter; saved-card direct debit
        # requires merchant-specific token API credentials and is intentionally not faked here.
        return UnsupportedPaymentAdapter('unipay_saved_card')
    return UnsupportedPaymentAdapter(n)

def create_stripe_subscription_checkout(*,child_id:int,course_id:str,plan_id:str,plan_version_id:str='',lessons_per_week:int,monthly_price:float,currency:str,billing_period:str='MONTH',success_url:str,cancel_url:str,idempotency_key:str="")->str:
    if not settings.stripe_secret_key: raise RuntimeError('STRIPE_SECRET_KEY не настроен')
    import stripe
    stripe.api_key=settings.stripe_secret_key
    period=str(billing_period or 'MONTH').upper(); interval='year' if period=='YEAR' else 'month'
    metadata={'child_id':str(child_id),'course_id':course_id,'plan_id':plan_id,'plan_version_id':str(plan_version_id),'billing_period':period,'lessons_per_week':str(lessons_per_week),'monthly_price':str(monthly_price)}
    kwargs=dict(
        mode='subscription',
        line_items=[{'price_data':{'currency':currency.lower(),'product_data':{'name':f'DOME · {lessons_per_week}×/нед · {period}'},'unit_amount':int(round(monthly_price*100)),'recurring':{'interval':interval}},'quantity':1}],
        success_url=success_url, cancel_url=cancel_url, metadata=metadata, subscription_data={'metadata':metadata},
    )
    if idempotency_key: kwargs['idempotency_key']=idempotency_key
    session=stripe.checkout.Session.create(**kwargs)
    return str(session.url)


def change_stripe_subscription_plan(*, subscription_id:str, child_id:int, course_id:str, plan_id:str, plan_version_id:str='', provider_plan_id:str='', lessons_per_week:int, monthly_price:float, currency:str, billing_period:str='MONTH', effective_at:datetime|None=None, idempotency_key:str="") -> dict:
    """Schedule the immutable price for the next period without changing this one."""
    if not settings.stripe_secret_key: raise RuntimeError('STRIPE_SECRET_KEY не настроен')
    import stripe
    stripe.api_key=settings.stripe_secret_key
    current=stripe.Subscription.retrieve(subscription_id)
    items=((current.get('items') or {}).get('data') or [])
    if not items: raise RuntimeError('Stripe subscription has no items')
    current_price=items[0].get('price') or {}
    current_price_id=str(current_price.get('id') if isinstance(current_price,dict) else current_price)
    if not current_price_id: raise RuntimeError('Stripe subscription price id missing')
    quantity=max(1,int(items[0].get('quantity') or 1))
    period=str(billing_period or 'MONTH').upper(); interval='year' if period=='YEAR' else 'month'
    version_id=str(plan_version_id or f'legacy-{plan_id}-{period.lower()}-{currency.lower()}-{monthly_price:.2f}')
    metadata={'child_id':str(child_id),'course_id':course_id,'plan_id':plan_id,'plan_version_id':version_id,'billing_period':period,'lessons_per_week':str(lessons_per_week),'monthly_price':f'{monthly_price:.2f}'}
    cfg=load_settings('payments'); cache=dict(cfg.get('stripe_price_cache') or {})
    cache_key=f'{version_id}:{period}:{currency.upper()}:{monthly_price:.2f}'
    price_id=str(provider_plan_id or cache.get(cache_key) or '')
    if not price_id:
        price_kwargs=dict(
            currency=currency.lower(), unit_amount=int(round(monthly_price*100)),
            recurring={'interval':interval}, product_data={'name':f'DOME · {lessons_per_week}×/нед · {period} · {version_id}'},
        )
        if idempotency_key: price_kwargs['idempotency_key']='price:'+idempotency_key
        price=stripe.Price.create(**price_kwargs)
        price_id=str(price.get('id') or '')
        if not price_id: raise RuntimeError('Stripe did not return price id')
        cache[cache_key]=price_id; cfg['stripe_price_cache']=cache; save_settings('payments',cfg)
    def timestamp(value):
        if isinstance(value,(int,float)):return int(value)
        if isinstance(value,datetime):
            aware=value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            return int(aware.timestamp())
        return 0
    current_start=timestamp(current.get('current_period_start'))
    current_end=timestamp(current.get('current_period_end')) or timestamp(effective_at)
    if not current_end: raise RuntimeError('Stripe subscription current period end missing')
    if not current_start or current_start>=current_end: current_start='now'
    current_metadata=dict(current.get('metadata') or {})
    schedule_ref=current.get('schedule')
    if isinstance(schedule_ref,dict):schedule_id=str(schedule_ref.get('id') or '')
    else:schedule_id=str(schedule_ref or '')
    if not schedule_id:
        create_kwargs={'from_subscription':subscription_id}
        if idempotency_key:create_kwargs['idempotency_key']='schedule:'+idempotency_key
        schedule=stripe.SubscriptionSchedule.create(**create_kwargs)
        schedule_id=str(schedule.get('id') or '')
    if not schedule_id:raise RuntimeError('Stripe did not return subscription schedule id')
    phases=[
        {'start_date':current_start,'end_date':current_end,'items':[{'price':current_price_id,'quantity':quantity}],
         'metadata':current_metadata,'proration_behavior':'none'},
        {'start_date':current_end,'iterations':1,'items':[{'price':price_id,'quantity':quantity}],
         'metadata':metadata,'proration_behavior':'none'},
    ]
    modify_kwargs={'end_behavior':'release','phases':phases,'proration_behavior':'none'}
    if idempotency_key:modify_kwargs['idempotency_key']=idempotency_key
    updated=stripe.SubscriptionSchedule.modify(schedule_id,**modify_kwargs)
    return {'id':subscription_id,'status':str(updated.get('status') or 'scheduled'),'price_id':price_id,'schedule_id':schedule_id}
