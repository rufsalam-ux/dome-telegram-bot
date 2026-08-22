from __future__ import annotations
import asyncio, base64, json, logging, mimetypes, secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from aiohttp import web
from sqlalchemy import select, func
from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models import Parent,Child,Character,LessonSession,VoiceAttempt,InteractiveResult,HomeworkAssignment,LessonEntitlement
from app.services.mobile_tokens import issue_session_token,verify_session_token,signed_media_token,verify_media_token
from app.services.lesson_loader import load_lesson
from app.services.lesson_access import can_start,complete_session_once
from app.services.preset_characters import preset_character_path,list_preset_characters
from app.services.character_processor import process_character
from app.services.audio_processing import prepare_child_voice
from app.services.speech_pipeline import assess_speech
from app.services.free_topic_cartoon import build_free_topic_cartoon
from app.services.email_reports import send_homework_email,_send_with_attachment_sync,send_verification_email,send_password_reset_email
from app.services.ai_speech import synthesize_speech, translate_text
from app.services.password_auth import hash_password, hash_verification_code, verify_password, verify_verification_code

log=logging.getLogger('dome.mobile_api')
MOBILE_LANGUAGES={'ru','en','es','de','fr','it','pt','tr','ar','zh'}


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)

def _base(request:web.Request)->str:
    return f'{request.scheme}://{request.host}'

def _bearer(request:web.Request)->str:
    h=request.headers.get('Authorization','')
    return h[7:].strip() if h.lower().startswith('bearer ') else ''

async def _parent(request:web.Request)->Parent:
    pid=verify_session_token(_bearer(request))
    if not pid: raise web.HTTPUnauthorized(text=json.dumps({'error':'Mobile session expired'}),content_type='application/json')
    async with SessionLocal() as db:
        p=await db.get(Parent,pid)
        if not p: raise web.HTTPUnauthorized()
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

def _child_json(request:web.Request,c:Child)->dict:
    return {'id':c.id,'name':c.display_name,'age_years':c.age_years,'native_language':c.native_language,'target_language':c.target_language,'language_level':c.language_level,'country':c.country,'active_character_id':c.active_character_id,'hero_url':_hero_url(request,c)}

async def bootstrap(request:web.Request)->web.Response:
    p=await _parent(request)
    async with SessionLocal() as db:cs=(await db.scalars(select(Child).where(Child.parent_id==p.id).order_by(Child.id))).all()
    return web.json_response({'parent':{'id':p.id,'name':p.display_name,'email':p.email,'email_verified':bool(p.email_verified),'phone':p.phone},'children':[_child_json(request,c) for c in cs]})

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
        db.add(child);await db.commit();await db.refresh(child)
    return web.json_response(_child_json(request,child),status=201)

async def lesson(request:web.Request)->web.Response:
    await _parent(request); lid=request.match_info['lesson_id']; data=load_lesson(lid)
    if not data: raise web.HTTPNotFound(text=json.dumps({'error':'Lesson not found'}),content_type='application/json')
    # Do not leak server paths.
    clean=dict(data); clean.pop('source_materials',None)
    return web.json_response(clean)

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
    async with SessionLocal() as db:
        ch=Character(child_id=cid,original_path=str(path),processed_path=str(path),status='READY',source='CATALOG',catalog_id=catalog);db.add(ch);await db.flush();c2=await db.get(Child,cid);c2.active_character_id=ch.id;await db.commit();await db.refresh(ch)
    val=f'hero:{cid}:{ch.id}'; t=signed_media_token(val)
    return web.json_response({'character_id':ch.id,'hero_url':f'{_base(request)}/api/mobile/hero/file/{cid}/{ch.id}?t={t}'})

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
    async with SessionLocal() as db:
        ch=Character(child_id=cid,original_path=str(original),processed_path=str(processed),status='READY',source='CHILD_DRAWING');db.add(ch);await db.flush();c=await db.get(Child,cid);c.active_character_id=ch.id;await db.commit();await db.refresh(ch)
    val=f'hero:{cid}:{ch.id}';t=signed_media_token(val)
    return web.json_response({'character_id':ch.id,'hero_url':f'{_base(request)}/api/mobile/hero/file/{cid}/{ch.id}?t={t}'})

async def session_start(request:web.Request)->web.Response:
    p=await _parent(request);data=await request.json();cid=int(data.get('child_id'));lid=str(data.get('lesson_id') or 'demo_001');c=await _owned_child(p.id,cid);lesson_data=load_lesson(lid);course=str(lesson_data.get('course_id') or 'conversation')
    ok,reason,ent=await can_start(cid,lid,course)
    if not ok and p.telegram_user_id in settings.admin_ids and reason=='LOCKED':
        async with SessionLocal() as db:
            ent=LessonEntitlement(child_id=cid,lesson_id=lid,course_id=course,max_completed_runs=2,completed_runs=0,source='ADMIN_TEST',status='ACTIVE');db.add(ent);await db.commit()
        ok=True;reason='ADMIN_TEST'
    if not ok: raise web.HTTPForbidden(text=json.dumps({'error':f'Урок недоступен: {reason}'}),content_type='application/json')
    async with SessionLocal() as db:
        sess=LessonSession(child_id=cid,lesson_id=lid,current_step=0,status='IN_PROGRESS',level_at_start=c.language_level or 'PRE_A1',lesson_revision=int(lesson_data.get('revision') or 1),runtime_state_json=json.dumps({'source':'mobile'},ensure_ascii=False));db.add(sess);await db.commit();await db.refresh(sess)
    return web.json_response({'session_id':sess.id,'run_number':int(ent.completed_runs or 0)+1,'lesson_id':lid})

def _slide(lesson_data:dict,slide_id:str)->dict:
    return next((x for x in lesson_data.get('slides',[]) if x.get('slide_id')==slide_id),{})

def _phrase(lesson_data:dict,pid:str|None)->dict:
    return next((x for x in lesson_data.get('required_phrases',[]) if x.get('phrase_id')==pid),{}) if pid else {}

async def voice(request:web.Request)->web.Response:
    p=await _parent(request);sid=int(request.match_info['session_id'])
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
        fields={'slide_id':str(data.get('slide_id') or ''),'prompt':str(data.get('prompt') or ''),'phrase_id':data.get('phrase_id')}
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
    slide_id=fields.get('slide_id','');prompt=fields.get('prompt','');pid=fields.get('phrase_id') or None
    lesson_data=load_lesson(sess.lesson_id);sl=_slide(lesson_data,slide_id);ph=_phrase(lesson_data,pid or sl.get('required_phrase_id'))
    if raw.stat().st_size<1000:raise web.HTTPBadRequest(text=json.dumps({'error':'Запись слишком короткая или пустая'}),content_type='application/json')
    wav=raw.with_suffix('.wav');max_sec=5 if (pid or sl.get('required_phrase_id')) else 60
    try:await asyncio.to_thread(prepare_child_voice,raw,wav,max_sec)
    except Exception as exc:raise web.HTTPBadRequest(text=json.dumps({'error':f'Не удалось обработать запись: {exc}'}),content_type='application/json')
    async with SessionLocal() as db:
        n=(await db.scalar(select(func.count(VoiceAttempt.id)).where(VoiceAttempt.lesson_session_id==sid,VoiceAttempt.phrase_id==(pid or slide_id)))) or 0
    assessment=await assess_speech(wav,c.target_language or 'ru',c.native_language or 'ru',prompt or sl.get('question') or sl.get('bot_says_target') or '',ph.get('accepted_meaning') or [],int(n)+1,c.display_name,c.working_difficulty)
    async with SessionLocal() as db:
        va=VoiceAttempt(lesson_session_id=sid,phrase_id=pid or slide_id,attempt_number=int(n)+1,audio_path=str(wav),status=assessment.status,transcript=assessment.transcript,detected_language=assessment.detected_language,confidence=assessment.confidence,grammar_errors=json.dumps(assessment.grammar_errors,ensure_ascii=False),pronunciation_errors=json.dumps(assessment.pronunciation_errors,ensure_ascii=False),semantic_match=assessment.semantic_match);db.add(va);await db.commit()
    feedback=assessment.feedback_native or assessment.response_native or assessment.response_target
    return web.json_response({'status':assessment.status,'transcript':assessment.transcript,'feedback':feedback,'response_target':assessment.response_target,'response_native':assessment.response_native,'semantic_match':assessment.semantic_match})

async def interactive(request:web.Request)->web.Response:
    p=await _parent(request);sid=int(request.match_info['session_id']);data=await request.json()
    async with SessionLocal() as db:
        sess=await db.get(LessonSession,sid);c=await db.get(Child,sess.child_id) if sess else None
        if not sess or not c or c.parent_id!=p.id:raise web.HTTPForbidden()
        row=InteractiveResult(lesson_session_id=sid,slide_id=str(data.get('slide_id') or ''),task_type=str(data.get('task_type') or 'choice'),result_json=json.dumps(data.get('result') or {},ensure_ascii=False),score=1.0);db.add(row);await db.commit()
    return web.json_response({'ok':True})

async def _mobile_token_ok(request:web.Request)->bool:
    tok=_bearer(request) or str(request.query.get('token') or '')
    pid=verify_session_token(tok)
    if not pid:return False
    async with SessionLocal() as db:return bool(await db.get(Parent,pid))

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
        c=await db.get(Child,cid);c.target_language=target;c.native_language=native;await db.commit();await db.refresh(c)
    return web.json_response(_child_json(request,c))

async def tts(request:web.Request)->web.StreamResponse:
    if not await _mobile_token_ok(request):raise web.HTTPUnauthorized()
    text=str(request.query.get('text',''))[:1000];native_text=str(request.query.get('native_text',''))[:1000];source=str(request.query.get('source_language','ru'));target=str(request.query.get('target_language','ru'));native=str(request.query.get('native_language','ru'))
    if not text and not native_text:raise web.HTTPBadRequest()
    try:
        spoken_target=await translate_text(text,source,target) if text else ''
        spoken_native=await translate_text(native_text,source,native) if native_text else ''
    except Exception as exc:raise web.HTTPServiceUnavailable(text=f'Translation unavailable: {exc}')
    combined=spoken_target
    if spoken_native and (native!=target or spoken_native!=spoken_target):combined=(combined+'\n\n'+spoken_native).strip()
    path=await synthesize_speech(combined,target,settings.storage_root/'tts-cache-mobile','mobile')
    if not path:raise web.HTTPServiceUnavailable(text='TTS unavailable')
    return web.FileResponse(path)

async def complete(request:web.Request)->web.Response:
    p=await _parent(request);sid=int(request.match_info['session_id'])
    async with SessionLocal() as db:
        sess=await db.get(LessonSession,sid)
        if not sess:raise web.HTTPNotFound()
        c=await db.get(Child,sess.child_id);par=await db.get(Parent,c.parent_id) if c else None
        if not c or c.parent_id!=p.id:raise web.HTTPForbidden()
    lesson_data=load_lesson(sess.lesson_id);course=str(lesson_data.get('course_id') or 'conversation')
    ent,new=await complete_session_once(session_id=sid,child_id=c.id,lesson_id=sess.lesson_id,course_id=course,final_step=len(lesson_data.get('slides',[])))
    if not new:
        run_no=int(ent.completed_runs or 0)
    else: run_no=int(ent.completed_runs or 0)
    async with SessionLocal() as db:
        voices=(await db.scalars(select(VoiceAttempt).where(VoiceAttempt.lesson_session_id==sid).order_by(VoiceAttempt.id))).all();char=await db.get(Character,c.active_character_id) if c.active_character_id else None
    accepted=[Path(v.audio_path) for v in voices if str(v.status).startswith('ACCEPTED') and Path(v.audio_path).exists()]
    movie_url=None
    if char and accepted:
        char_path=Path(char.processed_path or char.original_path);base=Path(settings.content_root)/'lessons'/sess.lesson_id
        backgrounds=[]
        for sl in lesson_data.get('slides',[]):
            rel=sl.get('image')
            if rel:
                q=base/rel
                if q.exists():backgrounds.append(q)
        out=settings.storage_root/'children'/str(c.id)/'cartoons'/f'mobile_{sess.lesson_id}_run{run_no}.mp4';out.parent.mkdir(parents=True,exist_ok=True)
        try:
            await asyncio.to_thread(build_free_topic_cartoon,backgrounds[:10],char_path,accepted[:10],[],out,75,None,9)
            val=f'movie:{c.id}:{out.name}';mt=signed_media_token(val,86400*30);movie_url=f'{_base(request)}/api/mobile/movie/{c.id}/{out.name}?t={mt}'
            if par and par.email_reports_enabled and par.email:
                subject=f'DOME — мультфильм после прохождения {run_no}: {lesson_data.get("title") or sess.lesson_id}'
                body=f'{c.display_name} завершил(а) прохождение {run_no}. Персональный мультфильм прикреплён к письму.'
                try:await asyncio.to_thread(_send_with_attachment_sync,par.email,subject,body,str(out))
                except Exception as exc:log.warning('Mobile movie email failed: %s',exc)
        except Exception as exc:log.exception('Mobile cartoon failed: %s',exc)
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
    return web.json_response({'ok':True,'run_number':run_no,'movie_url':movie_url,'homework_sent':homework_sent,'completed_runs':ent.completed_runs,'max_runs':ent.max_completed_runs})

async def movie_file(request:web.Request)->web.StreamResponse:
    cid=int(request.match_info['child_id']);filename=request.match_info['filename'];val=f'movie:{cid}:{filename}'
    if not verify_media_token(val,request.query.get('t','')):raise web.HTTPForbidden()
    if '/' in filename or '..' in filename:raise web.HTTPNotFound()
    path=settings.storage_root/'children'/str(cid)/'cartoons'/filename
    if not path.exists():raise web.HTTPNotFound()
    return web.FileResponse(path)

async def movies(request:web.Request)->web.Response:
    p=await _parent(request);cid=int(request.match_info['child_id']);await _owned_child(p.id,cid);root=settings.storage_root/'children'/str(cid)/'cartoons';items=[]
    if root.exists():
        for path in sorted(root.glob('mobile_*.mp4'),key=lambda x:x.stat().st_mtime,reverse=True):
            val=f'movie:{cid}:{path.name}';t=signed_media_token(val,86400*30);items.append({'filename':path.name,'title':path.stem.replace('_',' '),'created_at':datetime.fromtimestamp(path.stat().st_mtime).isoformat(),'url':f'{_base(request)}/api/mobile/movie/{cid}/{path.name}?t={t}'})
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
        await db.commit();token=issue_session_token(parent.id)
        return web.json_response({'token':token,'parent':{'id':parent.id,'name':parent.display_name,'email':parent.email,'email_verified':True,'phone':parent.phone},'children':[_child_json(request,c) for c in children]})


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
        children=(await db.scalars(select(Child).where(Child.parent_id==parent.id).order_by(Child.id))).all();token=issue_session_token(parent.id)
        return web.json_response({'token':token,'parent':{'id':parent.id,'name':parent.display_name,'email':parent.email,'email_verified':True,'phone':parent.phone},'children':[_child_json(request,c) for c in children]})


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
    app.router.add_post('/api/mobile/register',register);app.router.add_post('/api/mobile/verify-email',verify_email);app.router.add_post('/api/mobile/resend-verification',resend_verification);app.router.add_post('/api/mobile/login',login);app.router.add_post('/api/mobile/password-reset/request',request_password_reset);app.router.add_post('/api/mobile/password-reset/confirm',confirm_password_reset);app.router.add_get('/api/mobile/bootstrap',bootstrap);app.router.add_post('/api/mobile/children',create_child);app.router.add_get('/api/mobile/lesson/{lesson_id}',lesson)
    app.router.add_get('/api/mobile/hero/file/{child_id}/{character_id}',hero_file);app.router.add_post('/api/mobile/child/{child_id}/hero/preset',hero_preset);app.router.add_post('/api/mobile/child/{child_id}/hero/upload',hero_upload)
    app.router.add_post('/api/mobile/session/start',session_start);app.router.add_post('/api/mobile/session/{session_id}/voice',voice);app.router.add_post('/api/mobile/session/{session_id}/interactive',interactive);app.router.add_post('/api/mobile/session/{session_id}/complete',complete)
    app.router.add_get('/api/mobile/tts',tts);app.router.add_post('/api/mobile/translate',translate);app.router.add_patch('/api/mobile/child/{child_id}/language',update_child_language);app.router.add_get('/api/mobile/child/{child_id}/movies',movies);app.router.add_get('/api/mobile/movie/{child_id}/{filename}',movie_file)
