from __future__ import annotations
import asyncio, base64, json, logging, mimetypes, secrets, time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from aiohttp import web
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models import Parent,Child,Character,LessonSession,VoiceAttempt,InteractiveResult,HomeworkAssignment,LessonMovie,MovieVoiceSlot,Subscription
from app.services.mobile_tokens import issue_session_token,verify_session_token,signed_media_token,verify_media_token
from app.services.lesson_loader import LessonConfigurationError, load_lesson
from app.services.lesson_access import can_start,complete_session_once,mark_cartoon_generated
from app.services.preset_characters import preset_character_geometry,preset_character_path,list_preset_characters
from app.services.character_processor import process_character
from app.services.character_geometry import ANALYSIS_VERSION,analyze_character_geometry,confirm_character_geometry,geometry_from_json,geometry_status
from app.services.audio_processing import VoiceActivity, analyze_voice_activity, prepare_child_voice
from app.services.speech_pipeline import SpeechAssessment, assess_speech
from app.services.lesson_runtime import apply_adaptive_assessment,classify_voice_feedback,complexity_support,correction_for_assessment,no_speech_feedback,voice_attempt_outcome
from app.services.conversational_tutor import TutorTurn,no_speech_turn
from app.services.mobile_lesson_movie import MovieContractError,MovieRenderInputs,build_mobile_lesson_movie,ensure_movie_voice_slots,load_movie_contract,movie_take_status,record_movie_voice_slot,resolve_movie_voice_slots,select_movie_voice_takes
from app.services.email_reports import send_homework_email,_send_with_attachment_sync,send_verification_email,send_password_reset_email
from app.services.ai_speech import synthesize_bilingual_speech, translate_text
from app.services.password_auth import hash_password, hash_verification_code, verify_password, verify_verification_code
from app.services.standalone_demo_access import ensure_free_demo_entitlement
from app.services.visual_localization import VisualLocalizationError, localize_embedded_text_image
from app.services.course_catalog import list_courses
from app.services.authored_content import lesson_dir as authored_lesson_dir
from app.services.subscription_plan_changes import (
    PlanChangeError,
    cancel_plan_change,
    current_plan_snapshot,
    next_billing_period_start,
    plan_catalog_for_child,
    preview_plan_change,
    schedule_plan_change,
)
from app.services.subscription_provider import (
    SubscriptionProviderError,
    restore_provider_current_plan,
    schedule_provider_plan_change,
)

log=logging.getLogger('dome.mobile_api')
MOBILE_LANGUAGES={'ru','en','es','de','fr','it','pt','tr','ar','zh'}
_movie_tasks:set[asyncio.Task]=set()
MOVIE_RETRY_MESSAGE='Мультфильм пока не собрался. Все записи сохранены — попробуйте ещё раз.'


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)

def _base(request:web.Request)->str:
    return f'{request.scheme}://{request.host}'


def _load_mobile_lesson(lesson_id:str)->dict:
    try:return load_lesson(lesson_id)
    except (FileNotFoundError,LessonConfigurationError,KeyError,ValueError) as exc:
        log.error('MOBILE_LESSON_CONFIGURATION_UNAVAILABLE lesson=%s error=%s',lesson_id,exc)
        raise web.HTTPServiceUnavailable(text=json.dumps({'error':'Lesson configuration is unavailable','code':'LESSON_CONFIGURATION_UNAVAILABLE'}),content_type='application/json') from exc

def _bearer(request:web.Request)->str:
    h=request.headers.get('Authorization','')
    return h[7:].strip() if h.lower().startswith('bearer ') else ''

async def _parent(request:web.Request)->Parent:
    pid=verify_session_token(_bearer(request))
    if not pid:
        raise web.HTTPUnauthorized(text=json.dumps({'error':'Mobile session expired','code':'MOBILE_SESSION_INVALID'}),content_type='application/json')
    async with SessionLocal() as db:
        p=await db.get(Parent,pid)
        if not p:
            raise web.HTTPUnauthorized(text=json.dumps({'error':'Mobile session is no longer valid','code':'MOBILE_SESSION_REVOKED'}),content_type='application/json')
        return p

async def _owned_child(parent_id:int, child_id:int)->Child:
    async with SessionLocal() as db:
        c=await db.get(Child,int(child_id))
        if not c or c.parent_id!=parent_id: raise web.HTTPForbidden(text=json.dumps({'error':'Child does not belong to this parent'}),content_type='application/json')
        return c

def _hero_url(request:web.Request,c:Child)->str|None:
    if not c.active_character_id:return None
    val=f'hero:{c.id}:{c.active_character_id}'; t=signed_media_token(val)
    return f'{_base(request)}/api/mobile/hero/file/{c.id}/{c.active_character_id}?t={t}'

def _character_json(character:Character|None)->dict|None:
    if character is None:return None
    payload=geometry_from_json(character.visual_metadata_json)
    if not payload:return None
    return {**payload,'analysisStatus':character.visual_analysis_status,'analysisVersion':character.visual_analysis_version or payload.get('analysisVersion')}

def _child_json(request:web.Request,c:Child,character:Character|None=None)->dict:
    return {'id':c.id,'name':c.display_name,'age_years':c.age_years,'native_language':c.native_language,'target_language':c.target_language,'language_level':c.language_level,'working_difficulty':c.working_difficulty,'country':c.country,'active_character_id':c.active_character_id,'hero_url':_hero_url(request,c),'hero_metadata':_character_json(character)}

async def _ensure_character_geometry(character:Character)->dict:
    payload=geometry_from_json(character.visual_metadata_json)
    if payload.get('userConfirmed') is True:return payload
    if payload and character.visual_analysis_version==ANALYSIS_VERSION:return payload
    path=Path(character.processed_path or character.original_path)
    if not path.exists():return {}
    if character.catalog_id:
        payload=preset_character_geometry(character.catalog_id)
    else:
        payload=(await analyze_character_geometry(path,allow_remote=False)).payload()
    character.visual_metadata_json=json.dumps(payload,ensure_ascii=False)
    character.visual_analysis_version=ANALYSIS_VERSION
    character.visual_analysis_status=geometry_status(payload)
    return payload

async def _children_json(request:web.Request,db,children:list[Child])->list[dict]:
    result=[];changed=False
    for child in children:
        character=await db.get(Character,child.active_character_id) if child.active_character_id else None
        if character and (not geometry_from_json(character.visual_metadata_json) or character.visual_analysis_version!=ANALYSIS_VERSION):
            await _ensure_character_geometry(character);changed=True
        result.append(_child_json(request,child,character))
    if changed:await db.commit()
    return result


async def _mobile_resume_state(db, session_id:int)->tuple[dict,list[str]]:
    rows=(await db.scalars(select(InteractiveResult).where(InteractiveResult.lesson_session_id==session_id).order_by(InteractiveResult.id))).all()
    state={}
    for row in rows:
        try:state[row.slide_id]=json.loads(row.result_json or '{}')
        except (TypeError,ValueError,json.JSONDecodeError):continue
    voices=(await db.scalars(select(VoiceAttempt).where(VoiceAttempt.lesson_session_id==session_id).order_by(VoiceAttempt.id))).all()
    accepted=[]
    for row in voices:
        if movie_take_status(row.status) and row.phrase_id not in accepted:accepted.append(row.phrase_id)
    return state,accepted


def _session_payload(sess:LessonSession,ent,child:Child,*,resumed:bool,interactive_state:dict,recorded_phrases:list[str])->dict:
    return {
        'session_id':sess.id,'run_number':int(ent.completed_runs or 0)+1,'lesson_id':sess.lesson_id,
        'current_step':int(sess.current_step or 0),'resumed':resumed,'interactive_state':interactive_state,
        'recorded_phrases':recorded_phrases,'adaptive_profile':{
            'language_level':child.language_level or 'PRE_A1',
            'working_difficulty':float(child.working_difficulty or 0.15),
        },
    }

async def bootstrap(request:web.Request)->web.Response:
    p=await _parent(request)
    async with SessionLocal() as db:
        cs=(await db.scalars(select(Child).where(Child.parent_id==p.id).order_by(Child.id))).all();children_payload=await _children_json(request,db,list(cs))
    return web.json_response({'parent':{'id':p.id,'name':p.display_name,'email':p.email,'email_verified':bool(p.email_verified),'phone':p.phone},'children':children_payload})


async def lesson_catalog(request:web.Request)->web.Response:
    """Return the published server catalog and child-specific access state.

    The APK contains a universal player, not a baked lesson list. Publishing a
    validated content_v1 lesson therefore changes this response immediately.
    """

    p=await _parent(request)
    try:cid=int(request.match_info['child_id'])
    except (TypeError,ValueError):raise web.HTTPBadRequest(text=json.dumps({'error':'child_id is required'}),content_type='application/json')
    await _owned_child(p.id,cid)
    items=[]
    for course in list_courses():
        if not course.active:continue
        for lesson_id in course.lesson_ids:
            try:data=_load_mobile_lesson(str(lesson_id))
            except web.HTTPException:continue
            if str(data.get('publication_status') or '').upper()!='PUBLISHED':continue
            available,reason,entitlement=await can_start(cid,str(lesson_id),str(course.course_id),audit=False)
            async with SessionLocal() as db:
                resume=await db.scalar(select(LessonSession).where(
                    LessonSession.child_id==cid,LessonSession.lesson_id==str(lesson_id),LessonSession.status=='IN_PROGRESS',
                ).order_by(LessonSession.id.desc()))
            items.append({
                'lesson_id':str(lesson_id),'course_id':str(course.course_id),'course_title':course.title,
                'title':str(data.get('title') or lesson_id),'description':str(data.get('description') or course.description or ''),
                'order':int(data.get('order') or 9999),'revision':int(data.get('revision') or data.get('runtime_revision') or 1),
                'available':bool(available),'access_reason':reason,
                'completed_runs':int(entitlement.completed_runs or 0) if entitlement is not None else 0,
                'max_completed_runs':int(entitlement.max_completed_runs or data.get('max_completed_runs') or 2) if entitlement is not None else int(data.get('max_completed_runs') or 2),
                'resume_step':int(resume.current_step or 0) if resume is not None else None,
            })
    items.sort(key=lambda item:(item['course_title'],item['order'],item['lesson_id']))
    return web.json_response({'lessons':items})

async def create_child(request:web.Request)->web.Response:
    p=await _parent(request);data=await request.json();name=str(data.get('name') or '').strip()
    try:age=int(data.get('age_years'))
    except (TypeError,ValueError):raise web.HTTPBadRequest(text=json.dumps({'error':'Укажите возраст ребёнка'}),content_type='application/json')
    target=str(data.get('target_language') or '').strip().lower();native=str(data.get('native_language') or '').strip().lower()
    if not name or len(name)>120:raise web.HTTPBadRequest(text=json.dumps({'error':'Введите имя ребёнка'}),content_type='application/json')
    if age<2 or age>18:raise web.HTTPBadRequest(text=json.dumps({'error':'Возраст должен быть от 2 до 18 лет'}),content_type='application/json')
    if target not in MOBILE_LANGUAGES or native not in MOBILE_LANGUAGES:raise web.HTTPBadRequest(text=json.dumps({'error':'Выберите язык из списка'}),content_type='application/json')
    async with SessionLocal() as db:
        count=await db.scalar(select(func.count(Child.id)).where(Child.parent_id==p.id))
        if int(count or 0)>=5:raise web.HTTPConflict(text=json.dumps({'error':'Можно добавить не более 5 детей'}),content_type='application/json')
        child=Child(parent_id=p.id,display_name=name,age_years=age,target_language=target,native_language=native)
        db.add(child);await db.flush()
        await ensure_free_demo_entitlement(db,parent_id=p.id,child_id=child.id)
        await db.commit();await db.refresh(child)
    return web.json_response(_child_json(request,child),status=201)


def _date_json(value:datetime|None)->str|None:
    return value.isoformat() if value else None


def _plan_json(plan)->dict:
    return {
        'plan_id':plan.plan_id,'version_id':plan.version_id,'title':plan.title,'lessons_per_week':plan.lessons_per_week,
        'price':plan.price,'currency':plan.currency,'billing_period':plan.billing_period,
    }


async def _subscription_for_child(db,child_id:int,course_id:str)->Subscription|None:
    return await db.scalar(select(Subscription).where(
        Subscription.child_id==child_id,Subscription.course_id==course_id,
    ).order_by(Subscription.id.desc()))


def _subscription_json(sub:Subscription|None)->dict|None:
    if sub is None:return None
    current=current_plan_snapshot(sub)
    pending=None
    if sub.pending_plan_id:
        pending={
            'plan_id':sub.pending_plan_id,'lessons_per_week':sub.pending_lessons_per_week,
            'version_id':sub.pending_plan_version_id,'billing_period':sub.pending_plan_billing_period or 'MONTH',
            'price':sub.pending_plan_price,'currency':sub.pending_plan_currency or sub.currency,
            'created_at':_date_json(sub.pending_plan_created_at),
            'effective_at':_date_json(sub.pending_plan_effective_at),
            'provider_status':sub.pending_provider_status,
        }
    return {
        'id':sub.id,'course_id':sub.course_id,'status':sub.status,'current_plan':_plan_json(current),
        'current_period_start':_date_json(sub.current_period_start or sub.started_at),
        'current_period_end':_date_json(sub.current_period_end),
        'next_charge_at':_date_json(sub.next_charge_at or sub.current_period_end or next_billing_period_start(sub)),
        'lessons_allocated':int(sub.lessons_allocated or 0),'lessons_used':int(sub.lessons_used or 0),
        'pending_plan':pending,'payment_provider':sub.payment_provider,
    }


async def subscription_overview(request:web.Request)->web.Response:
    p=await _parent(request);cid=int(request.match_info['child_id']);course_id=str(request.query.get('course_id') or 'conversation')
    await _owned_child(p.id,cid)
    async with SessionLocal() as db:
        sub=await _subscription_for_child(db,cid,course_id)
        plans=await plan_catalog_for_child(db,parent_id=p.id,child_id=cid,course_id=course_id)
        return web.json_response({'subscription':_subscription_json(sub),'plans':[_plan_json(x) for x in plans]})


async def subscription_plan_change_preview(request:web.Request)->web.Response:
    p=await _parent(request);cid=int(request.match_info['child_id']);data=await request.json();course_id=str(data.get('course_id') or 'conversation');plan_id=str(data.get('plan_id') or '');billing_period=str(data.get('billing_period') or 'MONTH');version_id=str(data.get('version_id') or '')
    await _owned_child(p.id,cid)
    async with SessionLocal() as db:
        sub=await _subscription_for_child(db,cid,course_id)
        if sub is None:raise web.HTTPConflict(text=json.dumps({'error':'Активная подписка не найдена'}),content_type='application/json')
        try:preview=await preview_plan_change(db,sub,parent_id=p.id,requested_plan_id=plan_id,requested_billing_period=billing_period,expected_version_id=version_id)
        except PlanChangeError as exc:raise web.HTTPConflict(text=json.dumps({'error':str(exc)}),content_type='application/json')
        return web.json_response({
            'subscription_id':preview.subscription_id,'current_plan':_plan_json(preview.current),
            'new_plan':_plan_json(preview.requested),'effective_at':_date_json(preview.effective_at),
            'replaces_pending_plan_id':preview.replaces_pending_plan_id,
            'notice':'Новый тариф начнёт действовать со следующего оплачиваемого периода.\nДо этой даты действует ваш текущий тариф.',
        })


async def subscription_plan_change_confirm(request:web.Request)->web.Response:
    p=await _parent(request);cid=int(request.match_info['child_id']);data=await request.json();course_id=str(data.get('course_id') or 'conversation');plan_id=str(data.get('plan_id') or '');billing_period=str(data.get('billing_period') or 'MONTH');version_id=str(data.get('version_id') or '')
    await _owned_child(p.id,cid)
    async with SessionLocal() as db:
        sub=await _subscription_for_child(db,cid,course_id)
        if sub is None:raise web.HTTPConflict(text=json.dumps({'error':'Активная подписка не найдена'}),content_type='application/json')
        try:
            preview=await preview_plan_change(db,sub,parent_id=p.id,requested_plan_id=plan_id,requested_billing_period=billing_period,expected_version_id=version_id)
            provider=await schedule_provider_plan_change(
                sub,preview.requested,effective_at=preview.effective_at,
                base_url=settings.effective_webapp_base_url or _base(request),
                idempotency_key=f'plan-change:{sub.id}:{preview.requested.version_id}:{int(preview.effective_at.timestamp())}',
            )
            event=schedule_plan_change(
                db,sub,parent_id=p.id,preview=preview,provider_status=provider.status,
                provider_reference=provider.reference,provider_plan_id=provider.provider_plan_id,
            )
            await db.commit();await db.refresh(sub)
        except (PlanChangeError,SubscriptionProviderError,RuntimeError) as exc:
            await db.rollback();raise web.HTTPConflict(text=json.dumps({'error':str(exc)}),content_type='application/json')
        date=preview.effective_at.strftime('%d.%m.%Y')
        amount=f'{preview.requested.price:.2f} {preview.requested.currency}'
        message=f'Готово. До {date} действует текущий тариф.\nС {date} начнёт действовать {preview.requested.title}, и автоматически будет списываться {amount} за каждый следующий период, пока тариф не будет изменён или подписка отменена.'
        return web.json_response({'event':event,'subscription':_subscription_json(sub),'approval_url':provider.approval_url or None,'message':message})


async def subscription_plan_change_cancel(request:web.Request)->web.Response:
    p=await _parent(request);cid=int(request.match_info['child_id']);data=await request.json() if request.can_read_body else {};course_id=str(data.get('course_id') or 'conversation')
    await _owned_child(p.id,cid)
    async with SessionLocal() as db:
        sub=await _subscription_for_child(db,cid,course_id)
        if sub is None:raise web.HTTPConflict(text=json.dumps({'error':'Активная подписка не найдена'}),content_type='application/json')
        try:
            if not sub.pending_plan_id:raise PlanChangeError('Запланированного изменения тарифа нет')
            effective_at=sub.pending_plan_effective_at or datetime.utcnow()
            provider=await restore_provider_current_plan(
                sub,current_plan_snapshot(sub),effective_at=effective_at,
                base_url=settings.effective_webapp_base_url or _base(request),
                idempotency_key=f'plan-change-cancel:{sub.id}:{int(effective_at.timestamp())}',
            )
            if provider.approval_url:
                sub.pending_provider_status='CANCEL_PENDING_APPROVAL';sub.pending_provider_reference=provider.reference or None
            else:cancel_plan_change(db,sub,parent_id=p.id)
            await db.commit();await db.refresh(sub)
        except (PlanChangeError,SubscriptionProviderError,RuntimeError) as exc:
            await db.rollback();raise web.HTTPConflict(text=json.dumps({'error':str(exc)}),content_type='application/json')
        if provider.approval_url:
            return web.json_response({'subscription':_subscription_json(sub),'approval_url':provider.approval_url,'message':'Подтвердите отмену смены тарифа в PayPal. До подтверждения запланированное изменение сохраняется.'})
        return web.json_response({'subscription':_subscription_json(sub),'approval_url':None,'message':'Запланированное изменение тарифа отменено. Текущий тариф остаётся без изменений.'})

async def lesson(request:web.Request)->web.Response:
    await _parent(request); lid=request.match_info['lesson_id']; data=_load_mobile_lesson(lid)
    if not data: raise web.HTTPNotFound(text=json.dumps({'error':'Lesson not found'}),content_type='application/json')
    # Do not leak server paths.
    clean=dict(data); clean.pop('source_materials',None)
    clean['visual_asset_version']=str(data.get('revision') or data.get('runtime_revision') or 1)
    return web.json_response(clean)


async def lesson_visual(request:web.Request)->web.StreamResponse:
    p=await _parent(request);lid=str(request.match_info['lesson_id']);filename=str(request.match_info['filename'])
    if '/' in lid or '\\' in lid or '..' in lid:raise web.HTTPNotFound()
    try:cid=int(request.query.get('child_id',''))
    except (TypeError,ValueError):raise web.HTTPBadRequest(text=json.dumps({'error':'child_id is required'}),content_type='application/json')
    child=await _owned_child(p.id,cid)
    if '/' in filename or '\\' in filename or '..' in filename:
        raise web.HTTPNotFound()
    data=_load_mobile_lesson(lid)
    candidates={Path(str(slide.get('image') or '')).name:str(slide.get('image') or '') for slide in data.get('slides',[]) if slide.get('image')}
    relative=candidates.get(filename)
    if not relative:raise web.HTTPNotFound()
    base=((settings.storage_root/'authored-content'/'lessons'/lid) if data.get('content_source')=='persistent' else (settings.content_root/'lessons'/lid)).resolve();source=(base/relative).resolve()
    if base not in source.parents or not source.exists() or source.suffix.lower() not in {'.png','.jpg','.jpeg','.webp'}:
        raise web.HTTPNotFound()
    version=str(request.query.get('version') or data.get('revision') or data.get('runtime_revision') or 1)
    try:
        localized=await localize_embedded_text_image(source,settings.storage_root/'lesson-asset-cache',child.target_language or 'ru',asset_version=version,strict=True)
    except VisualLocalizationError as exc:
        log.warning('MOBILE_VISUAL_LOCALIZATION_UNAVAILABLE lesson=%s asset=%s language=%s error=%s',lid,filename,child.target_language,exc)
        raise web.HTTPServiceUnavailable(text=json.dumps({'error':'Localized lesson image is still being prepared','code':'LESSON_VISUAL_UNAVAILABLE'}),content_type='application/json')
    response=web.FileResponse(localized)
    response.headers['Cache-Control']='private, max-age=31536000, immutable'
    return response


async def lesson_media(request:web.Request)->web.StreamResponse:
    """Serve only local media explicitly referenced by the published lesson."""

    await _parent(request);lid=str(request.match_info['lesson_id']);filename=str(request.match_info['filename'])
    if any(value in lid or value in filename for value in ('/','\\','..')):raise web.HTTPNotFound()
    data=_load_mobile_lesson(lid);references:dict[str,str]={}
    for slide in data.get('slides',[]):
        for item in slide.get('media_sequence') or []:
            if not isinstance(item,dict):continue
            source=str(item.get('src') or item.get('url') or '')
            if source and not source.lower().startswith(('http://','https://')):
                references[Path(source).name]=source
    relative=references.get(filename)
    if not relative:raise web.HTTPNotFound()
    base=((settings.storage_root/'authored-content'/'lessons'/lid) if data.get('content_source')=='persistent' else (settings.content_root/'lessons'/lid)).resolve();path=(base/relative).resolve()
    if base not in path.parents or not path.exists() or path.suffix.lower() not in {'.png','.jpg','.jpeg','.webp','.gif','.mp4','.m4v','.webm','.mp3','.m4a','.ogg','.wav'}:
        raise web.HTTPNotFound()
    response=web.FileResponse(path);response.headers['Cache-Control']='private, max-age=86400'
    return response

async def hero_file(request:web.Request)->web.StreamResponse:
    cid=int(request.match_info['child_id']); chid=int(request.match_info['character_id']); val=f'hero:{cid}:{chid}'
    if not verify_media_token(val,request.query.get('t','')): raise web.HTTPForbidden()
    async with SessionLocal() as db:
        ch=await db.get(Character,chid)
        if not ch or ch.child_id!=cid: raise web.HTTPNotFound()
    path=Path(ch.processed_path or ch.original_path)
    if not path.exists(): raise web.HTTPNotFound()
    return web.FileResponse(path)

async def hero_preset(request:web.Request)->web.Response:
    p=await _parent(request); cid=int(request.match_info['child_id']); c=await _owned_child(p.id,cid); data=await request.json(); catalog=str(data.get('catalog_id',''))
    try:path=preset_character_path(catalog)
    except Exception: raise web.HTTPBadRequest(text=json.dumps({'error':'Unknown hero'}),content_type='application/json')
    metadata=preset_character_geometry(catalog)
    async with SessionLocal() as db:
        ch=Character(child_id=cid,original_path=str(path),processed_path=str(path),status='READY',source='CATALOG',catalog_id=catalog,visual_metadata_json=json.dumps(metadata,ensure_ascii=False),visual_analysis_version=ANALYSIS_VERSION,visual_analysis_status='READY');db.add(ch);await db.flush();c2=await db.get(Child,cid);c2.active_character_id=ch.id;await db.commit();await db.refresh(ch)
    val=f'hero:{cid}:{ch.id}'; t=signed_media_token(val)
    return web.json_response({'character_id':ch.id,'hero_url':f'{_base(request)}/api/mobile/hero/file/{cid}/{ch.id}?t={t}','hero_metadata':_character_json(ch)})

async def hero_upload(request:web.Request)->web.Response:
    p=await _parent(request);cid=int(request.match_info['child_id']);await _owned_child(p.id,cid)
    root=settings.storage_root/'children'/str(cid)/'characters';root.mkdir(parents=True,exist_ok=True);original=root/f'mobile_{int(datetime.utcnow().timestamp())}.jpg'
    if request.content_type.startswith('application/json'):
        data=await request.json();payload=str(data.get('image_base64') or '')
        if not payload: raise web.HTTPBadRequest(text=json.dumps({'error':'image_base64 is required'}),content_type='application/json')
        try: original.write_bytes(base64.b64decode(payload,validate=True))
        except Exception: raise web.HTTPBadRequest(text=json.dumps({'error':'Invalid image_base64'}),content_type='application/json')
    else:
        reader=await request.multipart();part=await reader.next()
        if not part or part.name!='image': raise web.HTTPBadRequest(text=json.dumps({'error':'image is required'}),content_type='application/json')
        with original.open('wb') as f:
            while True:
                chunk=await part.read_chunk()
                if not chunk:break
                f.write(chunk)
    processed=original.with_suffix('.png')
    try:await asyncio.to_thread(process_character,original,processed)
    except Exception as exc:raise web.HTTPBadRequest(text=json.dumps({'error':f'Не удалось удалить фон: {exc}'}),content_type='application/json')
    geometry=await analyze_character_geometry(processed,allow_remote=True);metadata=geometry.payload();analysis_status=geometry_status(geometry)
    if analysis_status!='READY':
        raise web.HTTPUnprocessableEntity(text=json.dumps({'error':'Не удалось уверенно определить голову и направление героя. Выберите более чёткий рисунок целиком.','code':'CHARACTER_GEOMETRY_UNCERTAIN','hero_metadata':metadata},ensure_ascii=False),content_type='application/json')
    async with SessionLocal() as db:
        ch=Character(child_id=cid,original_path=str(original),processed_path=str(processed),status='READY',source='CHILD_DRAWING',visual_metadata_json=json.dumps(metadata,ensure_ascii=False),visual_analysis_version=ANALYSIS_VERSION,visual_analysis_status=analysis_status);db.add(ch);await db.flush();c=await db.get(Child,cid);c.active_character_id=ch.id;await db.commit();await db.refresh(ch)
    val=f'hero:{cid}:{ch.id}';t=signed_media_token(val)
    return web.json_response({'character_id':ch.id,'hero_url':f'{_base(request)}/api/mobile/hero/file/{cid}/{ch.id}?t={t}','hero_metadata':_character_json(ch)})

async def hero_geometry_confirm(request:web.Request)->web.Response:
    p=await _parent(request);cid=int(request.match_info['child_id']);character_id=int(request.match_info['character_id']);await _owned_child(p.id,cid)
    data=await request.json()
    async with SessionLocal() as db:
        character=await db.get(Character,character_id)
        if not character or character.child_id!=cid:raise web.HTTPNotFound(text=json.dumps({'error':'Hero not found'}),content_type='application/json')
        current=geometry_from_json(character.visual_metadata_json)
        try:confirmed=confirm_character_geometry(current,data)
        except ValueError as exc:raise web.HTTPBadRequest(text=json.dumps({'error':str(exc)}),content_type='application/json')
        character.visual_metadata_json=json.dumps(confirmed,ensure_ascii=False)
        character.visual_analysis_version=ANALYSIS_VERSION
        character.visual_analysis_status='CONFIRMED'
        await db.commit();await db.refresh(character)
    log.info('CHARACTER_GEOMETRY_CONFIRMED parent_id=%s child_id=%s character_id=%s facing=%s',p.id,cid,character_id,confirmed.get('facingDirection'))
    return web.json_response({'ok':True,'character_id':character_id,'hero_metadata':_character_json(character)})

async def session_start(request:web.Request)->web.Response:
    p=await _parent(request);data=await request.json();cid=int(data.get('child_id'));lid=str(data.get('lesson_id') or 'demo_001');c=await _owned_child(p.id,cid);lesson_data=_load_mobile_lesson(lid);course=str(lesson_data.get('course_id') or 'conversation')
    ok,reason,ent=await can_start(cid,lid,course)
    if not ok: raise web.HTTPForbidden(text=json.dumps({'error':f'Урок недоступен: {reason}'}),content_type='application/json')
    async with SessionLocal() as db:
        existing=await db.scalar(select(LessonSession).where(LessonSession.child_id==cid,LessonSession.lesson_id==lid,LessonSession.status=='IN_PROGRESS').order_by(LessonSession.id.desc()))
        if existing:
            await ensure_movie_voice_slots(db,existing.id,lesson_data);await db.commit()
            interactive_state,recorded_phrases=await _mobile_resume_state(db,existing.id)
            return web.json_response(_session_payload(existing,ent,c,resumed=True,interactive_state=interactive_state,recorded_phrases=recorded_phrases))
        sess=LessonSession(child_id=cid,lesson_id=lid,current_step=0,status='IN_PROGRESS',level_at_start=c.language_level or 'PRE_A1',lesson_revision=int(lesson_data.get('revision') or 1),runtime_state_json=json.dumps({'source':'mobile'},ensure_ascii=False));db.add(sess);await db.flush();await ensure_movie_voice_slots(db,sess.id,lesson_data);await db.commit();await db.refresh(sess)
    return web.json_response(_session_payload(sess,ent,c,resumed=False,interactive_state={},recorded_phrases=[]))

async def session_progress(request:web.Request)->web.Response:
    p=await _parent(request);sid=int(request.match_info['session_id']);data=await request.json()
    try:current_step=int(data.get('current_step'))
    except (TypeError,ValueError):raise web.HTTPBadRequest(text=json.dumps({'error':'current_step must be an integer'}),content_type='application/json')
    async with SessionLocal() as db:
        sess=await db.get(LessonSession,sid);c=await db.get(Child,sess.child_id) if sess else None
        if not sess:raise web.HTTPNotFound()
        if not c or c.parent_id!=p.id:raise web.HTTPForbidden()
        slides=_load_mobile_lesson(sess.lesson_id).get('slides',[])
        if current_step<0 or current_step>=max(len(slides),1):raise web.HTTPBadRequest(text=json.dumps({'error':'current_step is outside the lesson'}),content_type='application/json')
        sess.current_step=current_step;await db.commit()
    return web.json_response({'ok':True,'session_id':sid,'current_step':current_step})

def _slide(lesson_data:dict,slide_id:str)->dict:
    return next((x for x in lesson_data.get('slides',[]) if x.get('slide_id')==slide_id),{})

def _phrase(lesson_data:dict,pid:str|None)->dict:
    return next((x for x in lesson_data.get('required_phrases',[]) if x.get('phrase_id')==pid),{}) if pid else {}

async def voice(request:web.Request)->web.Response:
    started=time.perf_counter();p=await _parent(request);sid=int(request.match_info['session_id'])
    async with SessionLocal() as db:
        sess=await db.get(LessonSession,sid)
        if not sess:raise web.HTTPNotFound()
        c=await db.get(Child,sess.child_id)
        if not c or c.parent_id!=p.id:raise web.HTTPForbidden()
    fields={};raw=None
    root=settings.storage_root/'children'/str(c.id)/'mobile-voice'/str(sid);root.mkdir(parents=True,exist_ok=True)
    if request.content_type.startswith('application/json'):
        data=await request.json();payload=str(data.get('audio_base64') or '')
        if not payload:raise web.HTTPBadRequest(text=json.dumps({'error':'No audio received'}),content_type='application/json')
        raw=root/f'voice_{secrets.token_hex(6)}.m4a'
        try:raw.write_bytes(base64.b64decode(payload,validate=True))
        except Exception:raise web.HTTPBadRequest(text=json.dumps({'error':'Invalid audio_base64'}),content_type='application/json')
        fields={'slide_id':str(data.get('slide_id') or ''),'prompt':str(data.get('prompt') or ''),'phrase_id':data.get('phrase_id'),'conversation_turn':data.get('conversation_turn',0)}
    else:
        reader=await request.multipart()
        while True:
            part=await reader.next()
            if part is None:break
            if part.name=='audio':
                raw=root/f'voice_{secrets.token_hex(6)}.m4a'
                with raw.open('wb') as f:
                    while True:
                        chunk=await part.read_chunk()
                        if not chunk:break
                        f.write(chunk)
            else:fields[part.name]=await part.text()
    if raw is None:raise web.HTTPBadRequest(text=json.dumps({'error':'No audio received'}),content_type='application/json')
    uploaded=time.perf_counter()
    slide_id=fields.get('slide_id','');prompt=fields.get('prompt','');pid=fields.get('phrase_id') or None
    try:conversation_turn=max(0,int(fields.get('conversation_turn') or 0))
    except (TypeError,ValueError):conversation_turn=0
    lesson_data=_load_mobile_lesson(sess.lesson_id);sl=_slide(lesson_data,slide_id);ph=_phrase(lesson_data,pid or sl.get('required_phrase_id'))
    required_movie_phrase=bool(sl.get('requiredForMovie') is True or sl.get('required_for_movie') is True)
    audio_received=raw.stat().st_size>=1000
    if not audio_received:
        activity=VoiceActivity(0.0,0.0,0.0,None,None,False,'TOO_SHORT')
    else:
        activity=await asyncio.to_thread(analyze_voice_activity,raw)
    wav=raw.with_suffix('.wav');max_sec=None
    if activity.has_speech:
        try:await asyncio.to_thread(prepare_child_voice,raw,wav,max_sec)
        except Exception as exc:raise web.HTTPBadRequest(text=json.dumps({'error':f'Не удалось обработать запись: {exc}'}),content_type='application/json')
    else:
        wav=raw
    prepared=time.perf_counter()
    storage_phrase_id=str(pid or sl.get('required_phrase_id') or slide_id)
    async with SessionLocal() as db:
        n=(await db.scalar(select(func.count(VoiceAttempt.id)).where(VoiceAttempt.lesson_session_id==sid,VoiceAttempt.phrase_id==storage_phrase_id))) or 0
    attempt_number=int(n)+1;max_attempts=max(1,int(sl.get('max_attempts') or 3))
    goal=prompt or ph.get('target_text') or sl.get('question') or sl.get('bot_says_target') or ''
    if activity.has_speech:
        assessment=await assess_speech(
            wav,c.target_language or 'ru',c.native_language or 'ru',goal,
            ph.get('accepted_meaning') or sl.get('accepted_meaning') or [],attempt_number,
            c.display_name,c.working_difficulty,c.language_level or 'PRE_A1',
            allow_follow_up=bool(sl.get('allow_ai_followup')) and not bool(sl.get('suppress_ai_followup')),
            max_follow_ups=max(0,int(sl.get('max_ai_followups') or 0)),
            follow_up_count=conversation_turn,
            conversation_goal=str(sl.get('conversation_goal') or goal),
        )
    else:
        assessment=SpeechAssessment(status='NO_SPEECH')
    assessed=time.perf_counter()
    feedback_state=classify_voice_feedback(audio_received=audio_received,has_speech=activity.has_speech,transcript=assessment.transcript,confidence=assessment.confidence,status=assessment.status,semantic_match=assessment.semantic_match)
    outcome=voice_attempt_outcome(str(assessment.status or 'TECHNICAL_UNCERTAINTY'),attempt_number,max_attempts)
    movie_take_accepted=False
    if required_movie_phrase:
        if not activity.has_speech:
            # A required movie line can never advance on silence, regardless of
            # how many attempts were made.
            outcome=replace(outcome,status='NO_SPEECH',accepted=False,advance_allowed=False,needs_retry=True)
        elif outcome.accepted:
            movie_take_accepted=True
        elif outcome.advance_allowed:
            # Real speech remains the child's recording even when ASR cannot
            # confidently grade its meaning after the authored retry ladder.
            outcome=replace(outcome,status='MOVIE_USABLE_WITH_SUPPORT',accepted=False,advance_allowed=True,needs_retry=False)
            movie_take_accepted=True
    status=outcome.status;accepted=outcome.accepted
    authored_example=str(ph.get('simplified_text') or sl.get('simplified_text') or ph.get('target_text') or goal);correction_target=correction_for_assessment(accepted=accepted,semantic_match=assessment.semantic_match,attempt_number=attempt_number,ai_correction=assessment.corrected_target,authored_example=authored_example,goal=goal)
    feedback=assessment.feedback_native or assessment.response_native or assessment.response_target
    tutor_turn=assessment.tutor_turn
    if tutor_turn and not accepted and correction_target:
        tutor_turn=replace(tutor_turn,correction_target=correction_target,model_answer_target=correction_target)
    if feedback_state in {'NO_AUDIO','NO_SPEECH'}:
        feedback,_legacy_example=no_speech_feedback(attempt_number,max_attempts,correction_target)
        retry_ru = ('Запись не сохранилась. Нажми на микрофон и попробуй ещё раз.' if feedback_state=='NO_AUDIO' else 'Я тебя не услышала. Попробуй ещё раз.') if required_movie_phrase or attempt_number < max_attempts else 'Я тебя не услышала. Пойдём дальше, а попытку отметим как пропущенную.'
        # The client prompt is the exact active card/follow-up question and is
        # already in the target language.  Falling back to a slide-level hint
        # here used to explain Q1 while the child was answering Q2/Q3.
        native_hint_source = goal if str(prompt or '').strip() else str(sl.get('bot_explains_native') or sl.get('question') or '')
        native_hint_source_language = (c.target_language or 'ru') if str(prompt or '').strip() else 'ru'
        example_ru = correction_target if attempt_number > 1 and attempt_number < max_attempts else ''
        target_retry,native_hint,model_answer=await asyncio.gather(
            translate_text(retry_ru,'ru',c.target_language or 'ru'),
            translate_text(native_hint_source,native_hint_source_language,c.native_language or 'ru') if native_hint_source else asyncio.sleep(0,result=''),
            translate_text(example_ru,'ru',c.target_language or 'ru') if example_ru else asyncio.sleep(0,result=''),
        )
        tutor_turn=no_speech_turn(attempt_number,max(max_attempts,attempt_number+1) if required_movie_phrase else max_attempts,target_retry=target_retry,native_hint=native_hint,model_answer=model_answer)
        correction_target=model_answer
    elif feedback_state in {'ASR_FAILED','ANSWER_UNCLEAR'} and not accepted:
        unclear_ru='Я услышала твой голос, но не разобрала слова. Скажи ещё раз чуть медленнее.'
        target_retry,native_hint=await asyncio.gather(
            translate_text(unclear_ru,'ru',c.target_language or 'ru'),
            translate_text(unclear_ru,'ru',c.native_language or 'ru'),
        )
        model_answer=correction_target if attempt_number>1 else ''
        tutor_turn=TutorTurn(reaction_target=target_retry,native_hint=native_hint,model_answer_target=model_answer,emotion='encouraging',complete=False,reason=feedback_state.lower())
        feedback=native_hint
    async with SessionLocal() as db:
        db_child=await db.get(Child,c.id)
        va=VoiceAttempt(lesson_session_id=sid,phrase_id=storage_phrase_id,attempt_number=attempt_number,audio_path=str(wav),status=status,transcript=assessment.transcript,detected_language=assessment.detected_language,confidence=assessment.confidence,grammar_errors=json.dumps(assessment.grammar_errors,ensure_ascii=False),pronunciation_errors=json.dumps(assessment.pronunciation_errors,ensure_ascii=False),semantic_match=assessment.semantic_match);db.add(va);await db.flush();await record_movie_voice_slot(db,sid,storage_phrase_id,va,lesson_data)
        working_difficulty,language_level=apply_adaptive_assessment(db_child,va,assessment)
        try:
            runtime=json.loads(sess.runtime_state_json or '{}')
        except (TypeError,ValueError,json.JSONDecodeError):
            runtime={}
        runtime['adaptive_profile']={'working_difficulty':working_difficulty,'language_level':language_level,'answers_count':int(db_child.answers_count or 0)}
        db_session=await db.get(LessonSession,sid);db_session.runtime_state_json=json.dumps(runtime,ensure_ascii=False)
        await db.commit()
    saved=time.perf_counter()
    log.info('MOBILE_VOICE_LATENCY session=%s slide=%s phrase=%s upload_ms=%d prepare_ms=%d assess_ms=%d save_ms=%d total_ms=%d attempt=%d status=%s activity=%s speech_ms=%d',sid,slide_id,storage_phrase_id,round((uploaded-started)*1000),round((prepared-uploaded)*1000),round((assessed-prepared)*1000),round((saved-assessed)*1000),round((saved-started)*1000),attempt_number,status,activity.reason,round(activity.speech_seconds*1000))
    return web.json_response({'status':status,'feedback_state':feedback_state,'accepted':accepted,'movie_take_accepted':movie_take_accepted,'advance_allowed':outcome.advance_allowed,'needs_retry':outcome.needs_retry,'attempt_number':attempt_number,'max_attempts':max_attempts,'transcript':assessment.transcript,'feedback':feedback,'feedback_source_language':c.native_language or 'ru','correction_target':correction_target if not accepted else '','correction_source_language':c.target_language or 'ru','response_target':assessment.response_target,'response_native':assessment.response_native,'semantic_match':assessment.semantic_match,'tutor_turn':tutor_turn.payload() if tutor_turn else None,'voice_activity':{'reason':activity.reason,'duration_seconds':activity.duration_seconds,'speech_seconds':activity.speech_seconds,'speech_ratio':activity.speech_ratio,'mean_volume_db':activity.mean_volume_db,'max_volume_db':activity.max_volume_db},'adaptive_profile':{'working_difficulty':working_difficulty,'language_level':language_level,'support':complexity_support(working_difficulty)}})

async def interactive(request:web.Request)->web.Response:
    p=await _parent(request);sid=int(request.match_info['session_id']);data=await request.json()
    async with SessionLocal() as db:
        sess=await db.get(LessonSession,sid);c=await db.get(Child,sess.child_id) if sess else None
        if not sess or not c or c.parent_id!=p.id:raise web.HTTPForbidden()
        row=InteractiveResult(lesson_session_id=sid,slide_id=str(data.get('slide_id') or ''),task_type=str(data.get('task_type') or 'choice'),result_json=json.dumps(data.get('result') or {},ensure_ascii=False),score=1.0);db.add(row);await db.commit()
    return web.json_response({'ok':True})

async def translate(request:web.Request)->web.Response:
    await _parent(request);data=await request.json();text=str(data.get('text') or '')[:2000];source=str(data.get('source_language') or 'ru');target=str(data.get('target_language') or 'ru')
    if not text:return web.json_response({'text':''})
    try:translated=await translate_text(text,source,target)
    except Exception as exc:raise web.HTTPServiceUnavailable(text=json.dumps({'error':f'Translation unavailable: {exc}'}),content_type='application/json')
    return web.json_response({'text':translated})

async def update_child_language(request:web.Request)->web.Response:
    p=await _parent(request);cid=int(request.match_info['child_id']);await _owned_child(p.id,cid);data=await request.json();target=str(data.get('target_language') or '').strip().lower();native=str(data.get('native_language') or '').strip().lower();supported={'ru','en','es','de','fr','it','pt','tr','ar','zh'}
    if target not in supported or native not in supported:raise web.HTTPBadRequest(text=json.dumps({'error':'Unsupported language'}),content_type='application/json')
    async with SessionLocal() as db:
        c=await db.get(Child,cid);c.target_language=target;c.native_language=native;await db.commit();await db.refresh(c);character=await db.get(Character,c.active_character_id) if c.active_character_id else None
        if character:await _ensure_character_geometry(character);await db.commit()
    return web.json_response(_child_json(request,c,character))

async def tts(request:web.Request)->web.StreamResponse:
    started=time.perf_counter();await _parent(request)
    text=str(request.query.get('text',''))[:1000];native_text=str(request.query.get('native_text',''))[:1000];source=str(request.query.get('source_language','ru'));native_source=str(request.query.get('native_source_language',source));target=str(request.query.get('target_language','ru'));native=str(request.query.get('native_language','ru'));style=str(request.query.get('style','warm'))[:32]
    if not text and not native_text:raise web.HTTPBadRequest()
    try:
        spoken_target=await translate_text(text,source,target) if text else ''
        spoken_native=await translate_text(native_text,native_source,native) if native_text else ''
    except Exception as exc:raise web.HTTPServiceUnavailable(text=f'Translation unavailable: {exc}')
    translated=time.perf_counter()
    if native==target and spoken_native==spoken_target:spoken_native=''
    path=await synthesize_bilingual_speech(spoken_target,target,spoken_native,native,settings.storage_root/'tts-cache-mobile','mobile',style)
    if not path:raise web.HTTPServiceUnavailable(text='TTS unavailable')
    ready=time.perf_counter();log.info('MOBILE_TTS_LATENCY source=%s target=%s translate_ms=%d synth_or_cache_ms=%d total_ms=%d',source,target,round((translated-started)*1000),round((ready-translated)*1000),round((ready-started)*1000))
    return web.FileResponse(path)


def _spawn_movie_task(coro)->asyncio.Task:
    task=asyncio.create_task(coro,name='dome-mobile-movie-render')
    _movie_tasks.add(task);task.add_done_callback(_movie_tasks.discard)
    return task


async def _render_mobile_movie_job(movie_id:int,inputs:MovieRenderInputs,child_id:int,lesson_id:str,course_id:str,parent_email:str|None,email_enabled:bool,run_no:int,lesson_title:str)->None:
    try:
        async with SessionLocal() as db:
            movie=await db.get(LessonMovie,movie_id);session_id=movie.lesson_session_id if movie else 0
            voices=(await db.scalars(select(VoiceAttempt).where(VoiceAttempt.lesson_session_id==session_id).order_by(VoiceAttempt.id))).all()
            lesson_for_movie={**_load_mobile_lesson(lesson_id),'timeline':inputs.timeline}
            resolved,diagnostics=await resolve_movie_voice_slots(db,session_id,voices,lesson_for_movie,inputs.target_language,settings.storage_root/'tts-cache-mobile');await db.commit()
        inputs=MovieRenderInputs(base_video=inputs.base_video,character=inputs.character,audio_by_phrase=resolved,timeline=inputs.timeline,output=inputs.output,lesson_dir=inputs.lesson_dir,target_language=inputs.target_language,approved_phrase_ids=inputs.approved_phrase_ids,expected_base_sha256=inputs.expected_base_sha256,require_all_phrase_audio=inputs.require_all_phrase_audio,character_metadata=inputs.character_metadata)
        log.info('MOBILE_MOVIE_VOICE_DIAGNOSTICS session=%s slots=%s',session_id,diagnostics)
        await asyncio.to_thread(build_mobile_lesson_movie,inputs)
        async with SessionLocal() as db:
            movie=await db.get(LessonMovie,movie_id)
            if movie:
                movie.status='READY';movie.output_path=str(inputs.output);movie.error=None;await db.commit()
        await mark_cartoon_generated(child_id,lesson_id,course_id)
        if email_enabled and parent_email:
            subject=f'DOME — мультфильм после прохождения {run_no}: {lesson_title}'
            body=f'Прохождение {run_no} завершено. Персональный мультфильм прикреплён к письму.'
            try:await asyncio.to_thread(_send_with_attachment_sync,parent_email,subject,body,str(inputs.output))
            except Exception as exc:log.warning('Mobile movie email failed: %s',exc)
    except Exception as exc:
        log.exception('Mobile authored movie failed: %s',exc)
        async with SessionLocal() as db:
            movie=await db.get(LessonMovie,movie_id)
            if movie:movie.status='FAILED';movie.error=str(exc)[:2000];await db.commit()

async def complete(request:web.Request)->web.Response:
    p=await _parent(request);sid=int(request.match_info['session_id'])
    async with SessionLocal() as db:
        sess=await db.get(LessonSession,sid)
        if not sess:raise web.HTTPNotFound()
        c=await db.get(Child,sess.child_id);par=await db.get(Parent,c.parent_id) if c else None
        if not c or c.parent_id!=p.id:raise web.HTTPForbidden()
    lesson_data=_load_mobile_lesson(sess.lesson_id);course=str(lesson_data.get('course_id') or 'conversation')
    movie_contract=None;movie_contract_error=None
    if lesson_data.get('cartoon_base') or lesson_data.get('cartoon_base_manifest'):
        try:movie_contract=load_movie_contract(sess.lesson_id,lesson_data)
        except MovieContractError as exc:
            movie_contract_error=exc;log.exception('MOBILE_MOVIE_CONTRACT_INVALID lesson=%s',sess.lesson_id)
    movie_lesson_data={**lesson_data,'timeline':movie_contract.timeline} if movie_contract else lesson_data
    async with SessionLocal() as db:
        voices=(await db.scalars(select(VoiceAttempt).where(VoiceAttempt.lesson_session_id==sid).order_by(VoiceAttempt.id))).all();char=await db.get(Character,c.active_character_id) if c.active_character_id else None;slots=await ensure_movie_voice_slots(db,sid,movie_lesson_data);await db.commit()
    audio_by_phrase,missing_exact=select_movie_voice_takes(voices,movie_lesson_data)
    if movie_contract and missing_exact:
        raise web.HTTPConflict(text=json.dumps({'error':'Нужно записать все обязательные реплики для мультфильма.','code':'REQUIRED_MOVIE_RECORDINGS_MISSING','missing_phrase_ids':missing_exact},ensure_ascii=False),content_type='application/json')
    ent,new=await complete_session_once(session_id=sid,child_id=c.id,lesson_id=sess.lesson_id,course_id=course,final_step=len(lesson_data.get('slides',[])))
    run_no=int(ent.completed_runs or 0)
    hero_path=Path(char.processed_path or char.original_path) if char else preset_character_path('explorer')
    voice_diagnostics=[]
    for slot in slots:
        try:detail=json.loads(slot.diagnostics_json or '{}')
        except (TypeError,ValueError,json.JSONDecodeError):detail={}
        voice_diagnostics.append({'required_voice_id':slot.required_voice_id,'status':slot.status,**detail})
    movie_url=None;movie_configured=movie_contract is not None;movie_status='PROCESSING' if movie_configured else ('FAILED' if movie_contract_error else 'NOT_CONFIGURED')
    out=settings.storage_root/'children'/str(c.id)/'cartoons'/f'mobile_{sess.lesson_id}_session{sid}.mp4';out.parent.mkdir(parents=True,exist_ok=True)
    should_render=False;movie_id=None
    if movie_configured and hero_path.exists():
        async with SessionLocal() as db:
            movie=await db.scalar(select(LessonMovie).where(LessonMovie.lesson_session_id==sid))
            if movie is None:
                movie=LessonMovie(lesson_session_id=sid,child_id=c.id,lesson_id=sess.lesson_id,run_number=run_no,status='PROCESSING',output_path=str(out));db.add(movie)
                try:await db.commit();await db.refresh(movie);should_render=True
                except IntegrityError:
                    await db.rollback();movie=await db.scalar(select(LessonMovie).where(LessonMovie.lesson_session_id==sid))
            elif movie.status=='READY' and movie.output_path and Path(movie.output_path).exists():
                out=Path(movie.output_path);movie_status='READY'
            elif movie.status=='FAILED':
                movie.status='PROCESSING';movie.error=None;movie.output_path=str(out);await db.commit();should_render=True
            movie_id=movie.id if movie else None
        if should_render:
            lesson_dir=movie_contract.lesson_dir
            inputs=MovieRenderInputs(base_video=movie_contract.base_video,character=hero_path,audio_by_phrase=audio_by_phrase,timeline=movie_contract.timeline,output=out,lesson_dir=lesson_dir,target_language=c.target_language or 'ru',approved_phrase_ids=movie_contract.approved_phrase_ids,expected_base_sha256=movie_contract.expected_base_sha256,require_all_phrase_audio=bool(movie_contract.audio_policy.get('require_exact_child_recording',True)),character_metadata=geometry_from_json(char.visual_metadata_json) if char else preset_character_geometry('explorer'))
            _spawn_movie_task(_render_mobile_movie_job(movie_id,inputs,c.id,sess.lesson_id,course,par.email if par else None,bool(par and par.email_reports_enabled),run_no,str(lesson_data.get('title') or sess.lesson_id)))
            movie_status='PROCESSING'
        elif movie_status!='READY':
            movie_status='PROCESSING'
        if movie_status=='READY' and out.exists():
            await mark_cartoon_generated(c.id,sess.lesson_id,course)
            val=f'movie:{c.id}:{out.name}';mt=signed_media_token(val,86400*30);movie_url=f'{_base(request)}/api/mobile/movie/{c.id}/{out.name}?t={mt}'
    homework_sent=False
    if run_no==1:
        hw=lesson_data.get('homework') or {};body=str(hw.get('instruction_ru') or 'Нарисуй место, куда ты хотел бы отправиться, и назови три вещи, которые возьмёшь с собой.')
        async with SessionLocal() as db:
            existing=await db.scalar(select(HomeworkAssignment).where(HomeworkAssignment.child_id==c.id,HomeworkAssignment.lesson_id==sess.lesson_id).order_by(HomeworkAssignment.id.desc()))
            if not existing:
                db.add(HomeworkAssignment(child_id=c.id,lesson_session_id=sid,lesson_id=sess.lesson_id,title=str(hw.get('title') or 'Домашнее задание'),body=body,duration_minutes=int(hw.get('duration_minutes') or 5),status='NEW',optional=True));await db.commit();homework_sent=True
        if homework_sent and par and par.email_reports_enabled and par.email:
            try:await send_homework_email(par.email,c.display_name,lesson_data.get('title') or sess.lesson_id,body,None)
            except Exception as exc:log.warning('Mobile homework email failed: %s',exc)
    return web.json_response({'ok':True,'run_number':run_no,'movie_url':movie_url,'movie_status':movie_status,'missing_voice_phrases':missing_exact,'missing_exact_voice_phrases':missing_exact,'movie_voice_diagnostics':voice_diagnostics,'hero_fallback_used':not bool(char),'homework_sent':homework_sent,'completed_runs':ent.completed_runs,'max_runs':ent.max_completed_runs})


async def movie_status(request:web.Request)->web.Response:
    p=await _parent(request);sid=int(request.match_info['session_id'])
    async with SessionLocal() as db:
        sess=await db.get(LessonSession,sid);c=await db.get(Child,sess.child_id) if sess else None
        if not sess or not c or c.parent_id!=p.id:raise web.HTTPForbidden()
        movie=await db.scalar(select(LessonMovie).where(LessonMovie.lesson_session_id==sid));slots=(await db.scalars(select(MovieVoiceSlot).where(MovieVoiceSlot.lesson_session_id==sid).order_by(MovieVoiceSlot.id))).all()
    if not movie:return web.json_response({'status':'NOT_CREATED','url':None})
    url=None;path=Path(movie.output_path) if movie.output_path else None
    if movie.status=='READY' and path and path.exists():
        value=f'movie:{c.id}:{path.name}';token=signed_media_token(value,86400*30);url=f'{_base(request)}/api/mobile/movie/{c.id}/{path.name}?t={token}'
    diagnostics=[]
    for slot in slots:
        try:detail=json.loads(slot.diagnostics_json or '{}')
        except (TypeError,ValueError,json.JSONDecodeError):detail={}
        diagnostics.append({'required_voice_id':slot.required_voice_id,'status':slot.status,**detail})
    return web.json_response({'status':movie.status,'url':url,'error':MOVIE_RETRY_MESSAGE if movie.status=='FAILED' else None,'can_retry':movie.status=='FAILED','run_number':movie.run_number,'voice_diagnostics':diagnostics})

async def movie_file(request:web.Request)->web.StreamResponse:
    cid=int(request.match_info['child_id']);filename=request.match_info['filename'];val=f'movie:{cid}:{filename}'
    if not verify_media_token(val,request.query.get('t','')):raise web.HTTPForbidden()
    if '/' in filename or '..' in filename:raise web.HTTPNotFound()
    path=settings.storage_root/'children'/str(cid)/'cartoons'/filename
    if not path.exists():raise web.HTTPNotFound()
    return web.FileResponse(path)

async def movies(request:web.Request)->web.Response:
    p=await _parent(request);cid=int(request.match_info['child_id']);await _owned_child(p.id,cid);items=[]
    async with SessionLocal() as db:
        rows=(await db.scalars(select(LessonMovie).where(LessonMovie.child_id==cid).order_by(LessonMovie.id.desc()))).all()
    for movie in rows:
        path=Path(movie.output_path) if movie.output_path else None;url=None
        if movie.status=='READY' and path and path.exists():
            value=f'movie:{cid}:{path.name}';token=signed_media_token(value,86400*30);url=f'{_base(request)}/api/mobile/movie/{cid}/{path.name}?t={token}'
        items.append({'filename':path.name if path else None,'title':f'{movie.lesson_id} · прохождение {movie.run_number}','created_at':movie.created_at.isoformat() if movie.created_at else '','url':url,'status':movie.status,'session_id':movie.lesson_session_id})
    return web.json_response({'movies':items})


def _normalize_email(value: object) -> str:
    return str(value or '').strip().lower()


async def _parent_by_email(db, email: str) -> Parent | None:
    return await db.scalar(select(Parent).where(func.lower(Parent.email) == email))


def _new_email_code()->str:
    return f'{secrets.randbelow(1_000_000):06d}'


def _valid_email(email:str)->bool:
    if len(email)>254 or '@' not in email:return False
    local,_,domain=email.rpartition('@')
    return bool(local and domain and '.' in domain and ' ' not in email)


async def register(request:web.Request)->web.Response:
    data=await request.json()
    email=_normalize_email(data.get('email'))
    password=str(data.get('password') or '')
    name=str(data.get('name') or '').strip()
    if not name:raise web.HTTPBadRequest(text=json.dumps({'error':'Введите имя'}),content_type='application/json')
    if not _valid_email(email):raise web.HTTPBadRequest(text=json.dumps({'error':'Введите корректный email'}),content_type='application/json')
    if len(password)<8:raise web.HTTPBadRequest(text=json.dumps({'error':'Пароль должен содержать минимум 8 символов'}),content_type='application/json')
    code=_new_email_code();expires=_utcnow()+timedelta(minutes=10)
    async with SessionLocal() as db:
        parent=await _parent_by_email(db,email)
        if parent and bool(parent.email_verified):
            raise web.HTTPConflict(text=json.dumps({'error':'Аккаунт с этой почтой уже существует'}),content_type='application/json')
        if parent is None:
            parent=Parent(email=email,display_name=name,password_hash=hash_password(password),email_verified=False,email_reports_enabled=settings.email_reports_default)
            db.add(parent)
        else:
            parent.email=email
            parent.display_name=name
            parent.password_hash=hash_password(password)
            parent.email_verified=False
        parent.email_verification_code_hash=hash_verification_code(email,code,'verify')
        parent.email_verification_expires_at=expires
        await db.commit();await db.refresh(parent)
    try:await send_verification_email(email,code,10)
    except Exception as exc:
        log.exception('Verification email failed: %s',exc)
        raise web.HTTPServiceUnavailable(text=json.dumps({'error':'Не удалось отправить письмо. Проверьте настройки почты DOME и попробуйте ещё раз.'}),content_type='application/json')
    return web.json_response({'verification_required':True,'email':email})


async def verify_email(request:web.Request)->web.Response:
    data=await request.json();email=_normalize_email(data.get('email'));code=str(data.get('code') or '').strip()
    if not _valid_email(email):raise web.HTTPBadRequest(text=json.dumps({'error':'Введите корректный email'}),content_type='application/json')
    if len(code)!=6 or not code.isdigit():raise web.HTTPBadRequest(text=json.dumps({'error':'Код должен состоять из 6 цифр'}),content_type='application/json')
    async with SessionLocal() as db:
        parent=await _parent_by_email(db,email)
        if not parent or not parent.email_verification_code_hash:
            raise web.HTTPBadRequest(text=json.dumps({'error':'Неверный код подтверждения'}),content_type='application/json')
        if not parent.email_verification_expires_at or parent.email_verification_expires_at<_utcnow():
            raise web.HTTPBadRequest(text=json.dumps({'error':'Срок действия кода истёк. Отправьте новый код.'}),content_type='application/json')
        if not verify_verification_code(email,code,parent.email_verification_code_hash,'verify'):
            raise web.HTTPBadRequest(text=json.dumps({'error':'Неверный код подтверждения'}),content_type='application/json')
        parent.email_verified=True;parent.email_verification_code_hash=None;parent.email_verification_expires_at=None
        children=(await db.scalars(select(Child).where(Child.parent_id==parent.id).order_by(Child.id))).all()
        if children:await ensure_free_demo_entitlement(db,parent_id=parent.id,child_id=children[0].id)
        await db.commit();token=issue_session_token(parent.id);children_payload=await _children_json(request,db,list(children))
        return web.json_response({'token':token,'parent':{'id':parent.id,'name':parent.display_name,'email':parent.email,'email_verified':True,'phone':parent.phone},'children':children_payload})


async def resend_verification(request:web.Request)->web.Response:
    data=await request.json();email=_normalize_email(data.get('email'))
    if not _valid_email(email):raise web.HTTPBadRequest(text=json.dumps({'error':'Введите корректный email'}),content_type='application/json')
    async with SessionLocal() as db:
        parent=await _parent_by_email(db,email)
        if not parent:raise web.HTTPNotFound(text=json.dumps({'error':'Аккаунт не найден'}),content_type='application/json')
        if parent.email_verified:return web.json_response({'ok':True,'already_verified':True})
        code=_new_email_code();parent.email_verification_code_hash=hash_verification_code(email,code,'verify');parent.email_verification_expires_at=_utcnow()+timedelta(minutes=10);await db.commit()
    try:await send_verification_email(email,code,10)
    except Exception as exc:
        log.exception('Verification resend failed: %s',exc)
        raise web.HTTPServiceUnavailable(text=json.dumps({'error':'Не удалось отправить письмо'}),content_type='application/json')
    return web.json_response({'ok':True})


async def login(request:web.Request)->web.Response:
    data=await request.json();email=_normalize_email(data.get('email'));password=str(data.get('password') or '')
    async with SessionLocal() as db:
        parent=await _parent_by_email(db,email)
        if not parent or not parent.password_hash or not verify_password(password,parent.password_hash):
            raise web.HTTPUnauthorized(text=json.dumps({'error':'Неверный email или пароль'}),content_type='application/json')
        if not bool(parent.email_verified):
            raise web.HTTPForbidden(text=json.dumps({'error':'Сначала подтвердите email','code':'EMAIL_NOT_VERIFIED','verification_required':True,'email':email}),content_type='application/json')
        children=(await db.scalars(select(Child).where(Child.parent_id==parent.id).order_by(Child.id))).all();token=issue_session_token(parent.id);children_payload=await _children_json(request,db,list(children))
        return web.json_response({'token':token,'parent':{'id':parent.id,'name':parent.display_name,'email':parent.email,'email_verified':True,'phone':parent.phone},'children':children_payload})


async def request_password_reset(request:web.Request)->web.Response:
    data=await request.json();email=_normalize_email(data.get('email'))
    code=None
    async with SessionLocal() as db:
        parent=await _parent_by_email(db,email)
        if parent:
            code=_new_email_code();parent.email_verification_code_hash=hash_verification_code(email,code,'reset');parent.email_verification_expires_at=_utcnow()+timedelta(minutes=10);await db.commit()
    if code:
        try:await send_password_reset_email(email,code,10)
        except Exception as exc:log.exception('Password reset email failed: %s',exc)
    return web.json_response({'ok':True,'message':'Если такой аккаунт существует, код отправлен на почту.'})


async def confirm_password_reset(request:web.Request)->web.Response:
    data=await request.json();email=_normalize_email(data.get('email'));code=str(data.get('code') or '').strip();password=str(data.get('password') or '')
    if len(password)<8:raise web.HTTPBadRequest(text=json.dumps({'error':'Пароль должен содержать минимум 8 символов'}),content_type='application/json')
    async with SessionLocal() as db:
        parent=await _parent_by_email(db,email)
        expired=not parent or not parent.email_verification_expires_at or parent.email_verification_expires_at<_utcnow()
        bad=not parent or not parent.email_verification_code_hash or not verify_verification_code(email,code,parent.email_verification_code_hash,'reset')
        if expired or bad:raise web.HTTPBadRequest(text=json.dumps({'error':'Код неверный или истёк'}),content_type='application/json')
        parent.password_hash=hash_password(password);parent.email_verified=True;parent.email_verification_code_hash=None;parent.email_verification_expires_at=None;await db.commit()
    return web.json_response({'ok':True})

def register_mobile_routes(app:web.Application):
    app.router.add_post('/api/mobile/register',register);app.router.add_post('/api/mobile/verify-email',verify_email);app.router.add_post('/api/mobile/resend-verification',resend_verification);app.router.add_post('/api/mobile/login',login);app.router.add_post('/api/mobile/password-reset/request',request_password_reset);app.router.add_post('/api/mobile/password-reset/confirm',confirm_password_reset);app.router.add_get('/api/mobile/bootstrap',bootstrap);app.router.add_post('/api/mobile/children',create_child);app.router.add_get('/api/mobile/child/{child_id}/lessons',lesson_catalog);app.router.add_get('/api/mobile/lesson/{lesson_id}/visual/{filename}',lesson_visual);app.router.add_get('/api/mobile/lesson/{lesson_id}/media/{filename}',lesson_media);app.router.add_get('/api/mobile/lesson/{lesson_id}',lesson)
    app.router.add_get('/api/mobile/hero/file/{child_id}/{character_id}',hero_file);app.router.add_post('/api/mobile/child/{child_id}/hero/preset',hero_preset);app.router.add_post('/api/mobile/child/{child_id}/hero/upload',hero_upload);app.router.add_patch('/api/mobile/child/{child_id}/hero/{character_id}/geometry',hero_geometry_confirm)
    app.router.add_get('/api/mobile/child/{child_id}/subscription',subscription_overview);app.router.add_post('/api/mobile/child/{child_id}/subscription/plan-change/preview',subscription_plan_change_preview);app.router.add_post('/api/mobile/child/{child_id}/subscription/plan-change',subscription_plan_change_confirm);app.router.add_delete('/api/mobile/child/{child_id}/subscription/plan-change',subscription_plan_change_cancel)
    app.router.add_post('/api/mobile/session/start',session_start);app.router.add_post('/api/mobile/session/{session_id}/progress',session_progress);app.router.add_post('/api/mobile/session/{session_id}/voice',voice);app.router.add_post('/api/mobile/session/{session_id}/interactive',interactive);app.router.add_post('/api/mobile/session/{session_id}/complete',complete);app.router.add_get('/api/mobile/session/{session_id}/movie',movie_status)
    app.router.add_get('/api/mobile/tts',tts);app.router.add_post('/api/mobile/translate',translate);app.router.add_patch('/api/mobile/child/{child_id}/language',update_child_language);app.router.add_get('/api/mobile/child/{child_id}/movies',movies);app.router.add_get('/api/mobile/movie/{child_id}/{filename}',movie_file)
