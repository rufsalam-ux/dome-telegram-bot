from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol
from app.core.config import settings

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

def create_stripe_subscription_checkout(*,child_id:int,course_id:str,plan_id:str,lessons_per_week:int,monthly_price:float,currency:str,success_url:str,cancel_url:str,idempotency_key:str="")->str:
    if not settings.stripe_secret_key: raise RuntimeError('STRIPE_SECRET_KEY не настроен')
    import stripe
    stripe.api_key=settings.stripe_secret_key
    metadata={'child_id':str(child_id),'course_id':course_id,'plan_id':plan_id,'lessons_per_week':str(lessons_per_week),'monthly_price':str(monthly_price)}
    kwargs=dict(
        mode='subscription',
        line_items=[{'price_data':{'currency':currency.lower(),'product_data':{'name':f'DOME · {lessons_per_week}×/нед'},'unit_amount':int(round(monthly_price*100)),'recurring':{'interval':'month'}},'quantity':1}],
        success_url=success_url, cancel_url=cancel_url, metadata=metadata, subscription_data={'metadata':metadata},
    )
    if idempotency_key: kwargs['idempotency_key']=idempotency_key
    session=stripe.checkout.Session.create(**kwargs)
    return str(session.url)


def change_stripe_subscription_plan(*, subscription_id:str, child_id:int, course_id:str, plan_id:str, lessons_per_week:int, monthly_price:float, currency:str, idempotency_key:str="") -> dict:
    """Change the existing recurring subscription instead of creating a second one.

    Stripe supports price_data on subscription item updates. `always_invoice` +
    `pending_if_incomplete` charges/credits prorations and only applies a paid
    upgrade when required payment succeeds.
    """
    if not settings.stripe_secret_key: raise RuntimeError('STRIPE_SECRET_KEY не настроен')
    import stripe
    stripe.api_key=settings.stripe_secret_key
    current=stripe.Subscription.retrieve(subscription_id)
    items=((current.get('items') or {}).get('data') or [])
    if not items: raise RuntimeError('Stripe subscription has no items')
    item_id=str(items[0].get('id') or '')
    if not item_id: raise RuntimeError('Stripe subscription item id missing')
    metadata={'child_id':str(child_id),'course_id':course_id,'plan_id':plan_id,'lessons_per_week':str(lessons_per_week),'monthly_price':str(monthly_price)}
    kwargs={
        'items':[{'id':item_id,'price_data':{'currency':currency.lower(),'product_data':{'name':f'DOME · {lessons_per_week}×/нед'},'unit_amount':int(round(monthly_price*100)),'recurring':{'interval':'month'}}}],
        'metadata':metadata,'proration_behavior':'always_invoice','payment_behavior':'pending_if_incomplete'
    }
    if idempotency_key: kwargs['idempotency_key']=idempotency_key
    updated=stripe.Subscription.modify(subscription_id,**kwargs)
    return {'id':str(updated.get('id') or subscription_id),'status':str(updated.get('status') or ''),'pending_update':bool(updated.get('pending_update'))}
