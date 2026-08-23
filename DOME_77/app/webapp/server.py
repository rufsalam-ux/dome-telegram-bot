from __future__ import annotations

from pathlib import Path
from datetime import datetime
import json

from aiohttp import web

from app.core.config import settings
from app.services.authored_content import lesson_dir
from app.db.session import SessionLocal
from app.db.models import CourseEnrollment, Subscription, PaymentWebhookEvent, LessonEntitlement
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


async def health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "DOME Mini App"})


async def index(_: web.Request) -> web.FileResponse:
    static = Path(__file__).parent / "static"
    return web.FileResponse(static / "index.html")


async def animal_compare(_: web.Request) -> web.FileResponse:
    static = Path(__file__).parent / "static"
    return web.FileResponse(static / "animal_compare.html")


async def free_topic_task(_: web.Request) -> web.FileResponse:
    static = Path(__file__).parent / "static"
    return web.FileResponse(static / "free_topic_task.html")


async def free_topic_media_file(request: web.Request) -> web.StreamResponse:
    child_id=request.match_info["child_id"]
    lesson_key=request.match_info["lesson_key"]
    filename=request.match_info["filename"]
    # Tight path validation: only generated free-topic PNG/JPG files are public here.
    if not child_id.isdigit() or "/" in lesson_key or ".." in lesson_key or "/" in filename or ".." in filename:
        raise web.HTTPNotFound()
    base=settings.storage_root/"children"/child_id/"free-topic-media"/lesson_key
    path=base/filename
    try:
        path = path.resolve(); base = base.resolve()
        if base != path.parent and base not in path.parents: raise web.HTTPNotFound()
    except Exception:
        raise web.HTTPNotFound()
    if not path.exists() or path.suffix.lower() not in {".png",".jpg",".jpeg",".webp",".mp4",".webm",".mov"}:
        raise web.HTTPNotFound()
    return web.FileResponse(path)




async def lesson_media_file(request: web.Request) -> web.StreamResponse:
    lesson_id=request.match_info["lesson_id"]
    filename=request.match_info["filename"]
    if "/" in lesson_id or ".." in lesson_id or ".." in filename:
        raise web.HTTPNotFound()
    base=lesson_dir(lesson_id)
    path=base/filename
    try:
        path = path.resolve(); base = base.resolve()
        if base != path.parent and base not in path.parents: raise web.HTTPNotFound()
    except Exception:
        raise web.HTTPNotFound()
    if not path.exists() or path.suffix.lower() not in {".png",".jpg",".jpeg",".webp",".mp4",".webm",".mov"}:
        raise web.HTTPNotFound()
    return web.FileResponse(path)

async def video_lesson(_: web.Request) -> web.FileResponse:
    static = Path(__file__).parent / "static"
    return web.FileResponse(static / "video_lesson.html")

async def games_index(_: web.Request) -> web.FileResponse:
    static = Path(__file__).parent / "static" / "games"
    return web.FileResponse(static / "index.html")



async def payment_success(_: web.Request) -> web.Response:
    return web.Response(text='Оплата получена. Вернитесь в Telegram — доступ откроется автоматически.',content_type='text/plain')

async def payment_cancel(_: web.Request) -> web.Response:
    return web.Response(text='Оплата отменена. Можно вернуться в Telegram и выбрать тариф позже.',content_type='text/plain')

# Payment webhooks are provider-neutral in payment_lifecycle.apply_normalized_event.
# Plan updates only mark the provider schedule; business access switches in
# subscription_plan_changes after a successful next-period payment.

async def _reserve_payment_event(db, *, provider:str, event_id:str, event_type:str, raw_payload:str) -> bool:
    """Reserve provider event atomically. Prefixing prevents cross-provider ID collisions."""
    if not event_id:
        raise web.HTTPBadRequest(text='missing event id')
    stored_id=f'{provider}:{event_id}'
    # v70 stored Stripe IDs without a provider prefix. Treat those as already seen too,
    # so upgrading a live bot cannot re-apply an old retried payment event once.
    legacy=await db.scalar(select(PaymentWebhookEvent).where(PaymentWebhookEvent.provider==provider,PaymentWebhookEvent.event_id.in_([event_id,stored_id])))
    if legacy is not None:
        return False
    db.add(PaymentWebhookEvent(provider=provider,event_id=stored_id,event_type=event_type,payload_json=raw_payload[:20000]))
    try:
        await db.flush(); return True
    except IntegrityError:
        await db.rollback(); return False


def _stripe_normalized(event:dict, obj:dict, meta:dict, provider_sub_id:str):
    from app.services.payment_lifecycle import NormalizedPaymentEvent
    from datetime import datetime
    typ=str(event.get('type') or '')
    mapping={
        'checkout.session.completed':'CHECKOUT_COMPLETED',
        'invoice.paid':'PAYMENT_SUCCEEDED','invoice.payment_succeeded':'PAYMENT_SUCCEEDED',
        'invoice.payment_failed':'PAYMENT_FAILED',
        'customer.subscription.deleted':'SUBSCRIPTION_CANCELLED',
        'customer.subscription.paused':'SUBSCRIPTION_PAUSED',
        'customer.subscription.updated':'SUBSCRIPTION_UPDATED',
    }
    status=str(obj.get('status') or '')
    if typ in {'checkout.session.completed','invoice.paid','invoice.payment_succeeded'}: status='ACTIVE'
    elif typ=='invoice.payment_failed': status='PAST_DUE'
    elif typ in {'customer.subscription.deleted','customer.subscription.paused'}: status='CANCELLED'
    child_raw=meta.get('child_id') or 0
    child_id=int(child_raw) if str(child_raw).isdigit() else 0
    try: monthly=float(meta.get('monthly_price') or 0)
    except Exception: monthly=0.0
    try: freq=max(1,min(4,int(meta.get('lessons_per_week') or 1)))
    except Exception: freq=1
    period={}
    lines=((obj.get('lines') or {}).get('data') or []) if isinstance(obj.get('lines'),dict) else []
    if lines and isinstance(lines[0],dict):period=lines[0].get('period') or {}
    provider_plan_id=''
    if lines and isinstance(lines[0],dict):
        raw_price=lines[0].get('price') or {}
        if isinstance(raw_price,dict):provider_plan_id=str(raw_price.get('id') or '')
    if not provider_plan_id:
        items=((obj.get('items') or {}).get('data') or []) if isinstance(obj.get('items'),dict) else []
        if items and isinstance(items[0],dict):
            raw_price=items[0].get('price') or {}
            if isinstance(raw_price,dict):provider_plan_id=str(raw_price.get('id') or '')
    if not period and isinstance(obj.get('current_period_start'),(int,float)):
        period={'start':obj.get('current_period_start'),'end':obj.get('current_period_end')}
    def dt(value):
        try:return datetime.utcfromtimestamp(float(value)) if value else None
        except (TypeError,ValueError,OSError):return None
    try:charged=float(obj.get('amount_paid') or 0)/100.0
    except (TypeError,ValueError):charged=0.0
    return NormalizedPaymentEvent(
        provider='stripe',event_id=str(event.get('id') or ''),event_type=mapping.get(typ,'SUBSCRIPTION_UPDATED'),status=status,
        child_id=child_id,course_id=str(meta.get('course_id') or ''),plan_id=str(meta.get('plan_id') or ''),
        plan_version_id=str(meta.get('plan_version_id') or ''),billing_period=str(meta.get('billing_period') or 'MONTH'),
        provider_plan_id=provider_plan_id,
        lessons_per_week=freq,monthly_price=monthly,currency=str(obj.get('currency') or 'EUR'),
        provider_subscription_id=provider_sub_id,occurred_at=dt(event.get('created')),
        period_start=dt(period.get('start')),period_end=dt(period.get('end')),
        charged_amount=charged,raw=dict(event))


async def stripe_webhook(request:web.Request)->web.Response:
    if not settings.stripe_webhook_secret:
        raise web.HTTPServiceUnavailable(text='Stripe webhook is not configured')
    payload=await request.read(); sig=request.headers.get('Stripe-Signature','')
    try:
        import stripe
        event=stripe.Webhook.construct_event(payload,sig,settings.stripe_webhook_secret)
    except Exception:
        raise web.HTTPBadRequest(text='invalid webhook')
    obj=event['data']['object']; typ=str(event.get('type') or ''); meta=dict(obj.get('metadata') or {})
    provider_sub_id=''
    if typ.startswith('invoice.'):
        provider_sub_id=str(obj.get('subscription') or '')
        if not meta and provider_sub_id:
            stripe.api_key=settings.stripe_secret_key
            sub_obj=stripe.Subscription.retrieve(provider_sub_id)
            meta=dict(sub_obj.get('metadata') or {})
    elif typ.startswith('customer.subscription.'):
        provider_sub_id=str(obj.get('id') or '')
    elif typ=='checkout.session.completed':
        provider_sub_id=str(obj.get('subscription') or '')
    ev=_stripe_normalized(event,obj,meta,provider_sub_id)
    async with SessionLocal() as db:
        if not await _reserve_payment_event(db,provider='stripe',event_id=ev.event_id,event_type=ev.event_type,raw_payload=payload.decode('utf-8',errors='ignore')):
            return web.json_response({'received':True,'duplicate':True})
        try:
            from app.services.payment_lifecycle import apply_normalized_event
            await apply_normalized_event(db,ev)
            await db.commit()
        except Exception:
            await db.rollback(); raise
    return web.json_response({'received':True})


async def unipay_webhook(request:web.Request)->web.Response:
    raw=await request.read()
    from app.services.unipay_adapter import verify_unipay_webhook, normalize_unipay_event
    if not verify_unipay_webhook(raw,request.headers):
        raise web.HTTPUnauthorized(text='invalid UniPAY webhook signature/token')
    try:
        data=json.loads(raw.decode('utf-8'))
        ev=normalize_unipay_event(data)
    except Exception as exc:
        raise web.HTTPBadRequest(text=f'invalid UniPAY webhook: {exc}')
    async with SessionLocal() as db:
        if not await _reserve_payment_event(db,provider='unipay',event_id=ev.event_id,event_type=ev.event_type,raw_payload=raw.decode('utf-8',errors='ignore')):
            return web.json_response({'received':True,'duplicate':True})
        try:
            from app.services.payment_lifecycle import apply_normalized_event
            await apply_normalized_event(db,ev)
            await db.commit()
        except Exception:
            await db.rollback(); raise
    return web.json_response({'received':True})


async def unlimit_webhook(request:web.Request)->web.Response:
    raw=await request.read()
    from app.services.unlimit_adapter import verify_unlimit_webhook, normalize_unlimit_event
    if not verify_unlimit_webhook(raw,request.headers):
        raise web.HTTPUnauthorized(text='invalid Unlimit callback signature')
    try:
        data=json.loads(raw.decode('utf-8')); ev=normalize_unlimit_event(data)
    except Exception as exc:
        raise web.HTTPBadRequest(text=f'invalid Unlimit webhook: {exc}')
    async with SessionLocal() as db:
        if not await _reserve_payment_event(db,provider='unlimit',event_id=ev.event_id,event_type=ev.event_type,raw_payload=raw.decode('utf-8',errors='ignore')):
            return web.json_response({'received':True,'duplicate':True})
        try:
            from app.services.payment_lifecycle import apply_normalized_event
            await apply_normalized_event(db,ev); await db.commit()
        except Exception:
            await db.rollback(); raise
    return web.json_response({'received':True})


async def paypal_webhook(request:web.Request)->web.Response:
    raw=await request.read()
    try:data=json.loads(raw.decode('utf-8'))
    except Exception as exc:raise web.HTTPBadRequest(text=f'invalid PayPal JSON: {exc}')
    from app.services.paypal_adapter import verify_paypal_webhook, normalize_paypal_event, get_paypal_subscription
    if not await verify_paypal_webhook(raw,data,request.headers):
        raise web.HTTPUnauthorized(text='invalid PayPal webhook signature')
    ev=normalize_paypal_event(data)
    # Payment-sale events often identify only billing_agreement_id. Pull the
    # subscription when metadata is absent so renewals/first-payment ordering is safe.
    if ev.provider_subscription_id and (not ev.child_id or not ev.course_id):
        try:
            sub=await get_paypal_subscription(ev.provider_subscription_id)
            ev=normalize_paypal_event(data,sub)
        except Exception:
            pass
    async with SessionLocal() as db:
        if not await _reserve_payment_event(db,provider='paypal',event_id=ev.event_id,event_type=ev.event_type,raw_payload=raw.decode('utf-8',errors='ignore')):
            return web.json_response({'received':True,'duplicate':True})
        try:
            from app.services.payment_lifecycle import apply_normalized_event
            await apply_normalized_event(db,ev); await db.commit()
        except Exception:
            await db.rollback(); raise
    return web.json_response({'received':True})


async def start_webapp_server():
    app = web.Application()
    from app.webapp.mobile_api import register_mobile_routes
    register_mobile_routes(app)
    static = Path(__file__).parent / "static"
    app.router.add_get("/health", health)
    app.router.add_get("/payment/success", payment_success)
    app.router.add_get("/payment/cancel", payment_cancel)
    app.router.add_post("/webhooks/stripe", stripe_webhook)
    app.router.add_post("/webhooks/unipay", unipay_webhook)
    app.router.add_post("/webhooks/unlimit", unlimit_webhook)
    app.router.add_post("/webhooks/paypal", paypal_webhook)
    app.router.add_get("/", index)
    app.router.add_get("/games", games_index)
    app.router.add_get("/video-lesson", video_lesson)
    app.router.add_get("/animal-compare", animal_compare)
    app.router.add_get("/free-topic-task", free_topic_task)
    app.router.add_get("/free-topic-media/{child_id}/{lesson_key}/{filename}", free_topic_media_file)
    app.router.add_get("/lesson-media/{lesson_id}/{filename:.*}", lesson_media_file)
    app.router.add_static("/assets", static / "assets", show_index=False)
    app.router.add_static("/media", static, show_index=False)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.effective_webapp_port)
    await site.start()
    return runner
