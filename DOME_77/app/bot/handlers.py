from __future__ import annotations

import shutil
import json
import logging
import asyncio
import time
from urllib.parse import urlencode
from datetime import datetime
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select, desc

from app.bot.keyboards import (
    character_menu_keyboard,
    character_source_keyboard,
    confirm_character_keyboard,
    language_keyboard,
    main_menu_keyboard,
    menu_hub_keyboard,
    child_menu_keyboard,
    parent_menu_keyboard,
    family_children_keyboard,
    age_keyboard,
    open_webapp_keyboard,
    preset_character_keyboard,
    start_lesson_keyboard,
    lesson_next_keyboard,
    suitcase_webapp_keyboard,
    payment_prompt_keyboard,
    lesson_voice_keyboard,
    card_choice_keyboard,
    choice_items_keyboard,
    mood_keyboard,
    payment_plans_keyboard,
    consent_agreement_keyboard,
    payment_checkout_keyboard,
    plan_change_confirmation_keyboard,
    plan_change_cancel_keyboard,
    homework_keyboard,
    free_topic_payment_keyboard,
    free_topic_step_keyboard,
    free_topic_choice_keyboard,
    free_topic_webapp_keyboard,
    free_topic_finished_keyboard,
    course_payment_gate_keyboard,
    gender_keyboard,
    support_keyboard,
    reading_ability_keyboard,
    course_list_keyboard,
    course_lessons_keyboard,
    course_transition_keyboard,
    course_switch_keyboard,
    course_switch_mode_keyboard,
)
from app.bot.states import LessonFlow, Onboarding, SettingsFlow, ConsentFlow, FreeTopicFlow, PilotAccessFlow, AdminLessonImport
from app.core.config import settings
from app.core.i18n import language_name, tr
from app.db.models import Character, Child, LessonSession, Parent, VoiceAttempt, ConsentRecord, HomeworkAssignment, CourseEnrollment, LessonEntitlement, Subscription
from app.db.session import SessionLocal
from app.services.animal_compare import build_compare_task
from app.services.ai_speech import AISpeechError, synthesize_speech, translate_text
from app.services.audio_processing import prepare_child_voice
from app.services.speech_pipeline import assess_speech
from app.services.adaptive_learning import score_answer, update_running_average, level_from_score, adapt_prompt
from app.services.conversation_engine import decide_retry, adapted_followup_limit, clamp_difficulty, human_prefix
from app.services.slide_renderer import render_slide
from app.services.email_reports import build_progress_report, send_progress_report, send_homework_email, build_course_ending_email, build_course_completed_email
from app.services.cartoon_builder import CartoonBuildError, build_timeline_cartoon, ensure_telegram_safe_mp4
from app.services.character_processor import CharacterProcessingError, process_character
from app.services.lesson_loader import load_lesson
from app.services.lesson_reminders import save_schedule, load_schedule
from app.services.lesson_revision import normalize_lesson_step, next_runtime_step
from app.services.sms_consent import send_verification, check_verification, SMSConsentError
from app.services.activity_log import activity
from app.services.homework import resolve_homework
from app.services.free_topic_builder import build_free_topic_lesson, save_free_topic_lesson
from app.services.free_topic_repository import choose_unused_variant, save_variant
from app.services.free_topic_media import ensure_free_topic_image, ensure_free_topic_clip, ensure_free_topic_item_image
from app.services.free_topic_cartoon import build_free_topic_cartoon, FreeTopicCartoonError
from app.services.platform_settings import load_settings, save_settings
from app.services.family_pricing import MAX_CHILDREN_PER_PARENT, family_child_count, family_price_for_child, child_position_in_family
from app.services.course_scheduler import choose_next_lesson, first_active_course_id, course_for_lesson
from app.services.course_catalog import list_courses
from app.services.course_transitions import get_route, set_route, course_titles, save_choice, load_choice, apply_transition, course_progress, load_notice_state, mark_notice, save_course_switch, load_course_switch, clear_course_switch, apply_course_switch
from app.services.pricing_engine import subscription_plans_for_course, set_course_plan_price
from app.services.runtime_mode import CONVERSATION_ONLY, client_course_allowed
from app.services.pilot_access import find_pilot, access_until
from app.services.authored_content import load_authored_lesson, load_homework, lesson_dir, ensure_persistent_lesson, validate_content_lesson, validate_homework, backup_lesson_version, list_lesson_versions, restore_lesson_version, canonical_content_type
from app.services.lesson_access import can_start as can_start_authored, complete_session_once, get_entitlement as get_authored_entitlement, mark_cartoon_generated
from app.services.subscription_release import release_due_lessons, ensure_test_entitlement, active_subscription
from app.services.lesson_pacing import decide_pacing
from app.services.lesson_importer import import_package as import_lesson_package
from app.services.cartoon_credit import add_cartoon_credit
from app.services.cartoon_text_overlay import apply_cartoon_text_overlays
from app.services.preset_characters import (
    get_preset_character,
    list_preset_characters,
    preset_character_path,
    preset_collage_path,
)

router = Router()
log = logging.getLogger("dome.handlers")
LESSON_ID = "demo_001"

def _lesson_id(data: dict | None = None) -> str:
    return str((data or {}).get("current_lesson_id") or (data or {}).get("selected_lesson_id") or LESSON_ID)

async def _personal_release_enabled(child_id: int, course_id: str) -> bool:
    """Paid mode always enforces plans; in free QA mode an explicit test plan also does."""
    payments=load_settings("payments")
    if bool(payments.get("billing_enabled",False)):
        return True
    return await active_subscription(child_id,course_id) is not None


async def _next_scheduled_lesson_id(child: Child, course_id: str | None = None) -> str:
    if CONVERSATION_ONLY:
        course_id = "conversation"
    else:
        course_id = course_id or first_active_course_id() or "conversation"
    if await _personal_release_enabled(child.id,course_id):
        await release_due_lessons(child.id,course_id)
        async with SessionLocal() as db:
            ents=(await db.scalars(select(LessonEntitlement).where(
                LessonEntitlement.child_id==child.id,LessonEntitlement.course_id==course_id,LessonEntitlement.status.in_(["ACTIVE","COMPLETED"])
            ).order_by(LessonEntitlement.unlocked_at.asc(),LessonEntitlement.id.asc()))).all()
        now=datetime.utcnow()
        for e in ents:
            if e.completed_runs < e.max_completed_runs and (e.expires_at is None or e.expires_at>=now):
                return e.lesson_id
        return ""
    # No explicit test plan: billing-off remains an unrestricted QA mode.
    async with SessionLocal() as db:
        completed = (await db.scalars(select(LessonSession.lesson_id).where(
            LessonSession.child_id == child.id, LessonSession.status == "COMPLETED"
        ).order_by(LessonSession.id.asc()))).all()
    return choose_next_lesson(course_id, completed) or (LESSON_ID if course_id == "conversation" else "")



async def _has_course_access(child_id: int, course_id: str) -> bool:
    """Return True when the child has active paid/test access to this course."""
    payments = load_settings("payments")
    if not bool(payments.get("billing_enabled", False)):
        return True
    async with SessionLocal() as db:
        rows = (await db.scalars(select(CourseEnrollment).where(
            CourseEnrollment.child_id == child_id,
            CourseEnrollment.course_id == course_id,
            CourseEnrollment.status == "ACTIVE",
        ).order_by(CourseEnrollment.id.desc()))).all()
    now = datetime.utcnow()
    for row in rows:
        if row.access_until is None or row.access_until >= now:
            return True
    return False



async def _current_active_course_id(child_id: int) -> str:
    async with SessionLocal() as db:
        sub=await db.scalar(select(Subscription).where(Subscription.child_id==child_id,Subscription.status=='ACTIVE').order_by(Subscription.id.desc()))
    return str(sub.course_id) if sub and sub.course_id else str(first_active_course_id() or 'conversation')

async def _course_transition_options(course_id: str) -> tuple[list[tuple[str,str]], str|None, bool]:
    route=get_route(course_id); titles=course_titles()
    options=[(cid,titles.get(cid,cid)) for cid in (route.get('next') or []) if cid in titles]
    rec=route.get('recommended'); recommended=titles.get(rec,rec) if rec else None
    return options,recommended,bool(route.get('repeat_allowed',True))

async def _maybe_notify_course_progress(message: Message, child: Child, course_id: str) -> None:
    if CONVERSATION_ONLY:
        return
    completed,total,remaining=await course_progress(child.id,course_id)
    if total<=0: return
    route=get_route(course_id); thresholds={int(x) for x in (load_transition_settings().get('notify_remaining_lessons') or [4,1]) if str(x).isdigit()} if False else {4,1}
    # v72 defaults to 4 and 1; settings file keeps this editable without code.
    try:
        from app.services.course_transitions import load_transition_settings
        thresholds={int(x) for x in (load_transition_settings().get('notify_remaining_lessons') or [4,1])}
    except Exception:
        thresholds={4,1}
    titles=course_titles(); title=titles.get(course_id,course_id)
    options,recommended,repeat_allowed=await _course_transition_options(course_id)
    option_titles=[x[1] for x in options] + ([f'Повторить «{title}»'] if repeat_allowed else [])
    state=load_notice_state(child.id,course_id)
    if remaining in thresholds and f'remaining_{remaining}' not in state:
        text=(f'📚 {child.display_name}: в курсе «{title}» осталось {remaining} занятия.\n\n'
              + (f'⭐ Рекомендуем следующий шаг: {recommended}.\n\n' if recommended else '')
              + 'Вы уже можете выбрать, что будет дальше. Прогресс текущего курса сохранится.')
        await message.answer(text,reply_markup=course_transition_keyboard(course_id,options,repeat_allowed=repeat_allowed,language=child.native_language or 'ru'))
        try:
            async with SessionLocal() as db: parent=await db.get(Parent,child.parent_id)
            if parent and parent.email_reports_enabled and parent.email:
                subject,body=build_course_ending_email(child.display_name,title,remaining,option_titles,recommended)
                await send_progress_report(parent.email,subject,body)
        except Exception as exc: log.warning('Course ending email failed: %s',exc)
        mark_notice(child.id,course_id,f'remaining_{remaining}')
    if remaining==0 and 'completed' not in state:
        choice=load_choice(child.id,course_id)
        text=(f'🎓 {child.display_name} завершил(а) курс «{title}»!\n\n'
              + (f'⭐ Рекомендуем продолжить: {recommended}.\n\n' if recommended else '')
              + 'Выберите следующий курс или повтор курса.')
        await message.answer(text,reply_markup=course_transition_keyboard(course_id,options,repeat_allowed=repeat_allowed,language=child.native_language or 'ru'))
        try:
            async with SessionLocal() as db: parent=await db.get(Parent,child.parent_id)
            if parent and parent.email_reports_enabled and parent.email:
                subject,body=build_course_completed_email(child.display_name,title,option_titles,recommended)
                await send_progress_report(parent.email,subject,body)
        except Exception as exc: log.warning('Course completed email failed: %s',exc)
        mark_notice(child.id,course_id,'completed')
        if choice and choice.get('target'):
            result=await apply_transition(child.id,course_id,str(choice['target']))
            if result in {'MOVED','REPEATED'}:
                await message.answer('✅ Выбранное продолжение обучения применено автоматически.')

async def _apply_pending_course_switch_after_lesson(message: Message, child: Child, from_course_id: str) -> bool:
    if CONVERSATION_ONLY:
        clear_course_switch(child.id)
        return False
    sw=load_course_switch(child.id)
    if not sw or sw.get('mode')!='after_current' or sw.get('from_course_id')!=from_course_id:
        return False
    target=str(sw.get('target_course_id') or '')
    result=await apply_course_switch(child.id,from_course_id,target)
    if result=='MOVED':
        await message.answer(f'✅ Курс изменён. Следующие новые уроки будут из «{course_titles().get(target,target)}». Прогресс прошлого курса сохранён.')
        return True
    return False


async def _show_parent_course_payment_gate(message: Message, state: FSMContext, child: Child, lesson_id: str, course_id: str) -> None:
    payments = load_settings("payments")
    allow_test = bool(payments.get("allow_test_course_payment_bypass", False)) and not bool(payments.get("billing_enabled",False))
    await state.update_data(pending_course_id=course_id, pending_lesson_id=lesson_id, selected_lesson_id=lesson_id)
    native = child.native_language or "ru"
    text = (
        "👩 Для родителя\n\nПеред началом нового урока нужно оплатить курс или пакет. "
        "После оплаты доступ к уроку откроется ребёнку."
        if native == "ru" else
        "👩 For the parent\n\nPlease pay for the course or package before the new lesson starts. "
        "The lesson will unlock for the learner after payment."
    )
    await message.answer(text, reply_markup=course_payment_gate_keyboard(native, allow_test_bypass=allow_test))


@router.message(Command("dome_prices"))
async def admin_prices(message: Message):
    if message.from_user.id not in settings.admin_ids:
        return
    cfg=load_settings('pricing'); plans=(cfg.get('regular_course') or {}).get('subscription_plans') or []
    lines=['💶 DOME · тарифы (одинаковые для всех 3 курсов)']+[f"{p['lessons_per_week']}×/нед — €{p['monthly_price']}/мес" for p in plans]
    family=cfg.get('family') or {}; lines.append(f"Семья: 1-й ребёнок — полная цена; 2–5-й — минус €{float(family.get('additional_child_discount_per_lesson_eur',0.5)):g} с каждого урока")
    pay=load_settings('payments'); lines.append('Оплата: '+('ВКЛ' if pay.get('billing_enabled') else 'ВЫКЛ — тест бесплатно'))
    await message.answer('\n'.join(lines))

@router.message(Command("dome_price"))
async def admin_set_price(message: Message):
    if message.from_user.id not in settings.admin_ids: return
    parts=(message.text or '').split()
    if len(parts)!=3 or not parts[1].isdigit():
        await message.answer('Формат: /dome_price 2 79'); return
    freq=int(parts[1])
    try: price=float(parts[2].replace(',','.'))
    except Exception: await message.answer('Цена должна быть числом.'); return
    if freq not in {1,2,3,4} or price<=0: await message.answer('Частота 1–4, цена > 0.'); return
    cfg=load_settings('pricing'); r=cfg.setdefault('regular_course',{}); plans=r.setdefault('subscription_plans',[])
    found=False
    for p in plans:
        if int(p.get('lessons_per_week',0))==freq: p['monthly_price']=price; found=True
    if not found: plans.append({'id':f'weekly{freq}','lessons_per_week':freq,'monthly_price':price})
    save_settings('pricing',cfg); await message.answer(f'✅ {freq}×/нед = €{price:g}/мес. Сохранено без redeploy.')


@router.message(Command("dome_course_price"))
async def admin_set_course_price(message: Message):
    if message.from_user.id not in settings.admin_ids: return
    parts=(message.text or '').split()
    valid={c.course_id for c in list_courses()}
    if len(parts)!=4 or parts[1] not in valid or not parts[2].isdigit():
        await message.answer('Формат: /dome_course_price conversation 2 79'); return
    course_id=parts[1]; freq=int(parts[2])
    try: price=float(parts[3].replace(',','.'))
    except Exception: await message.answer('Цена должна быть числом.'); return
    if freq not in {1,2,3,4} or price<=0:
        await message.answer('Частота 1–4, цена > 0.'); return
    set_course_plan_price(course_id,freq,price)
    await message.answer(f'✅ Цена курса {course_id}: {freq}×/нед = €{price:g}/мес. Изменено без redeploy.')

@router.message(Command("dome_route"))
async def admin_set_course_route(message: Message):
    if message.from_user.id not in settings.admin_ids: return
    # /dome_route conversation learn_to_read,reading recommended=learn_to_read repeat=on
    parts=(message.text or '').split()
    if len(parts)<3:
        await message.answer('Формат: /dome_route conversation learn_to_read,reading recommended=learn_to_read repeat=on'); return
    course_id=parts[1]
    valid={c.course_id for c in list_courses()}
    if course_id not in valid:
        await message.answer('Неизвестный курс.'); return
    nxt=[x.strip() for x in parts[2].split(',') if x.strip()]
    recommended=None; repeat=True
    for x in parts[3:]:
        if x.startswith('recommended='): recommended=x.split('=',1)[1].strip() or None
        if x.startswith('repeat='): repeat=x.split('=',1)[1].lower() in {'on','yes','1','true'}
    cfg=set_route(course_id,nxt,recommended=recommended,repeat_allowed=repeat)
    route=(cfg.get('courses') or {}).get(course_id) or {}
    await message.answer(f"✅ Переходы {course_id}: {', '.join(route.get('next') or []) or 'нет'}; рекомендуемый={route.get('recommended') or 'нет'}; повтор={'да' if route.get('repeat_allowed') else 'нет'}. Без redeploy.")

@router.message(Command("dome_billing"))
async def admin_billing(message: Message):
    if message.from_user.id not in settings.admin_ids: return
    parts=(message.text or '').split(); mode=(parts[1].lower() if len(parts)>1 else '')
    if mode not in {'on','off'}: await message.answer('Формат: /dome_billing on или /dome_billing off'); return
    cfg=load_settings('payments')
    if mode=='on':
        provider=str(cfg.get('provider') or settings.payment_provider or '').lower()
        if provider=='stripe' and not (settings.stripe_secret_key and settings.stripe_webhook_secret):
            await message.answer('❌ Billing не включён: для Stripe нужны STRIPE_SECRET_KEY и STRIPE_WEBHOOK_SECRET.'); return
        if provider=='unipay' and not (settings.unipay_subscription_url and (settings.unipay_access_token or (settings.unipay_merchant_id and settings.unipay_api_key)) and (settings.unipay_webhook_secret or settings.unipay_webhook_token)):
            await message.answer('❌ Billing не включён: для UniPAY нужны endpoint + merchant credentials + защита webhook.'); return
        if provider=='paypal' and not (settings.paypal_client_id and settings.paypal_client_secret and settings.paypal_webhook_id):
            await message.answer('❌ Для PayPal нужны PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET и PAYPAL_WEBHOOK_ID.'); return
        if provider=='unlimit' and not ((settings.unlimit_recurring_url or settings.unlimit_payment_url) and (settings.unlimit_api_token or (settings.unlimit_terminal_code and settings.unlimit_password)) and settings.unlimit_callback_secret):
            await message.answer('❌ Для Unlimit нужны API endpoint/credentials и UNLIMIT_CALLBACK_SECRET.'); return
        if provider not in {'stripe','unipay','unlimit','paypal'}:
            await message.answer('❌ Production billing нельзя включить с provider=custom: нет автоматического подтверждения оплаты. Выберите stripe, unipay, unlimit или paypal.'); return
    cfg['billing_enabled']=mode=='on'; cfg['test_payment_mode']=mode!='on'; cfg['allow_test_course_payment_bypass']=False if mode=='on' else True; cfg['allow_test_free_topic_bypass']=False if mode=='on' else True; save_settings('payments',cfg)
    await message.answer('✅ Оплата '+('ВКЛЮЧЕНА. Тестовые обходы принудительно выключены.' if mode=='on' else 'ВЫКЛЮЧЕНА. Тестовый доступ бесплатный.'))

@router.message(Command("dome_provider"))
async def admin_payment_provider(message: Message):
    if message.from_user.id not in settings.admin_ids: return
    parts=(message.text or '').split(); provider=(parts[1].lower() if len(parts)>1 else '')
    if provider not in {'custom','stripe','unipay','unlimit','paypal'}:
        await message.answer('Формат: /dome_provider stripe | unipay | unlimit | paypal | custom'); return
    cfg=load_settings('payments'); cfg['provider']=provider; save_settings('payments',cfg)
    await message.answer(f'✅ Платёжный провайдер: {provider}. Сохранено без redeploy.')


@router.message(Command("dome_testplan"))
async def admin_test_plan(message: Message):
    """Assign/change a free test subscription without payment; useful before billing goes live."""
    if message.from_user.id not in settings.admin_ids: return
    parts=(message.text or '').split()
    if len(parts)!=4 or not parts[1].isdigit() or parts[2] not in {'conversation','learn_to_read','reading'} or not parts[3].isdigit():
        await message.answer('Формат: /dome_testplan child_id reading 2'); return
    child_id=int(parts[1]); course_id=parts[2]; freq=int(parts[3])
    if freq not in {1,2,3,4}: await message.answer('Частота 1–4.'); return
    pricing=load_settings('pricing'); plans=((pricing.get('regular_course') or {}).get('subscription_plans') or []); spec=next((x for x in plans if int(x.get('lessons_per_week',0))==freq),None)
    if not spec: await message.answer('Тариф не найден.'); return
    async with SessionLocal() as db:
        sub=await db.scalar(select(Subscription).where(Subscription.child_id==child_id,Subscription.course_id==course_id).order_by(Subscription.id.desc()))
        released=(await db.scalars(select(LessonEntitlement.id).where(
            LessonEntitlement.child_id==child_id,LessonEntitlement.course_id==course_id,LessonEntitlement.source=='SUBSCRIPTION'
        ))).all(); baseline=len(released)
        # Re-activating a cancelled/paid subscription or changing frequency is a NEW
        # release segment. Previously unlocked lessons remain, but old weeks never
        # become backlog and never delay the new plan.
        restart = sub is None or sub.status != 'ACTIVE' or not sub.test_mode or int(sub.lessons_per_week or 1)!=freq
        if sub is None or sub.status != 'ACTIVE' or not sub.test_mode:
            sub=Subscription(child_id=child_id,course_id=course_id,started_at=datetime.utcnow(),release_baseline_count=baseline); db.add(sub)
        elif restart:
            sub.started_at=datetime.utcnow(); sub.release_baseline_count=baseline
        sub.plan_id=str(spec.get('id') or f'weekly{freq}'); sub.current_plan_id=sub.plan_id; sub.lessons_per_week=freq; sub.monthly_price=float(spec.get('monthly_price') or 0); sub.currency=str(pricing.get('currency') or 'EUR'); sub.status='ACTIVE'; sub.test_mode=True; sub.cancelled_at=None; sub.provider_subscription_id=None
        enroll=await db.scalar(select(CourseEnrollment).where(CourseEnrollment.child_id==child_id,CourseEnrollment.course_id==course_id).order_by(CourseEnrollment.id.desc()))
        if enroll is None: db.add(CourseEnrollment(child_id=child_id,course_id=course_id,status='ACTIVE',access_source='ADMIN_TEST',payment_reference='ADMIN_TEST'))
        else: enroll.status='ACTIVE'; enroll.access_source='ADMIN_TEST'
        await db.commit()
    created=await release_due_lessons(child_id,course_id)
    await message.answer(f'✅ Тестовый тариф {freq}×/нед назначен. Сейчас открыто новых уроков: {len(created)}.')

@router.message(Command("addlesson"))
async def admin_add_lesson(message: Message, state: FSMContext):
    if message.from_user.id not in settings.admin_ids: return
    parts=(message.text or '').split(maxsplit=3)
    if len(parts)<4 or parts[1] not in {'conversation','learn_to_read','reading'}:
        await message.answer('Формат: /addlesson reading lesson_002 Название урока\nКурсы: conversation, learn_to_read, reading'); return
    await state.update_data(admin_import_course=parts[1],admin_import_id=parts[2],admin_import_title=parts[3],admin_import_files={})
    await state.set_state(AdminLessonImport.lesson_file)
    await message.answer('1/4 Пришли основной файл урока PDF или PPTX.')

async def _download_admin_doc(message:Message,state:FSMContext,key:str)->Path|None:
    if not message.document: return None
    name=message.document.file_name or f'{key}.bin'; root=settings.storage_root/'admin-imports'/str(message.from_user.id); root.mkdir(parents=True,exist_ok=True); path=root/name
    await message.bot.download(message.document,destination=path); data=await state.get_data(); files=dict(data.get('admin_import_files') or {}); files[key]=str(path); await state.update_data(admin_import_files=files); return path

@router.message(AdminLessonImport.lesson_file, F.document)
async def admin_import_lesson_file(message:Message,state:FSMContext):
    p=await _download_admin_doc(message,state,'lesson')
    if not p or p.suffix.lower() not in {'.pdf','.pptx'}: await message.answer('Нужен PDF или PPTX.'); return
    await state.set_state(AdminLessonImport.instruction_file); await message.answer('2/4 Пришли инструкцию преподавателя DOCX/PDF/TXT. Если её нет — /skip_instruction')

@router.message(AdminLessonImport.instruction_file, F.document)
async def admin_import_instruction(message:Message,state:FSMContext):
    p=await _download_admin_doc(message,state,'instruction')
    if not p or p.suffix.lower() not in {'.docx','.pdf','.txt'}: await message.answer('Нужен DOCX, PDF или TXT.'); return
    await state.set_state(AdminLessonImport.homework_file); await message.answer('3/4 Пришли домашнее задание PDF/PPTX. Если его нет — /skip_homework')

@router.message(AdminLessonImport.instruction_file, Command('skip_instruction'))
async def admin_skip_instruction(message:Message,state:FSMContext):
    await state.set_state(AdminLessonImport.homework_file); await message.answer('3/4 Пришли ДЗ PDF/PPTX или /skip_homework')

async def _finish_admin_import(message:Message,state:FSMContext):
    data=await state.get_data(); files=data.get('admin_import_files') or {}
    try:
        lesson=await asyncio.to_thread(
            import_lesson_package,
            lesson_id=data['admin_import_id'],course_id=data['admin_import_course'],title=data['admin_import_title'],
            lesson_file=Path(files['lesson']),instruction_file=Path(files['instruction']) if files.get('instruction') else None,
            homework_file=Path(files['homework']) if files.get('homework') else None,
            extra_files=[Path(x) for x in (data.get('admin_import_extra_files') or [])],
            extra_texts=list(data.get('admin_import_extra_texts') or []),extra_file_notes=list(data.get('admin_import_extra_file_notes') or []),target_language='ru',order=999,
        )
    except Exception as exc:
        await message.answer(f'Не получилось импортировать: {exc}'); return
    await state.clear(); preview='\n'.join(f"{x.get('order')}: {x.get('type')} — {x.get('prompt','')[:55]}" for x in (lesson.get('slides') or [])[:20])
    await message.answer(
        f"✅ Черновик {lesson['lesson_id']} создан. Он пока НЕ опубликован.\n\nКарта первых слайдов:\n{preview}\n\n"
        f"Проверь: /lessonmap {lesson['lesson_id']} и /validate_lesson {lesson['lesson_id']}\n"
        f"Тест без расхода прохождения: /previewlesson {lesson['lesson_id']}\n"
        f"После проверки: /publishlesson {lesson['lesson_id']}"
    )

@router.message(AdminLessonImport.homework_file, F.document)
async def admin_import_homework(message:Message,state:FSMContext):
    p=await _download_admin_doc(message,state,'homework')
    if not p or p.suffix.lower() not in {'.pdf','.pptx'}: await message.answer('Нужен PDF или PPTX.'); return
    await state.set_state(AdminLessonImport.extras); await message.answer('4/4 Пришли дополнительные видео/файлы или ссылки. Для ссылок удобно: «слайд 3 https://…». Можно несколько сообщений. Когда готово — /done_extras')

@router.message(AdminLessonImport.homework_file, Command('skip_homework'))
async def admin_skip_homework(message:Message,state:FSMContext):
    await state.set_state(AdminLessonImport.extras); await message.answer('4/4 Пришли дополнительные видео/файлы или ссылки. Когда готово — /done_extras')

@router.message(AdminLessonImport.extras, F.document)
async def admin_import_extra_file(message:Message,state:FSMContext):
    p=await _download_admin_doc(message,state,f'extra_{int(time.time()*1000)}')
    if not p: return
    data=await state.get_data(); arr=list(data.get('admin_import_extra_files') or []); arr.append(str(p))
    notes=list(data.get('admin_import_extra_file_notes') or []); notes.append(str(message.caption or ''))
    await state.update_data(admin_import_extra_files=arr,admin_import_extra_file_notes=notes)
    await message.answer('✅ Дополнительный файл добавлен. Если в подписи был номер слайда, файл будет привязан к нему. Ещё файл/ссылка или /done_extras')

@router.message(AdminLessonImport.extras, F.video)
async def admin_import_extra_video(message:Message,state:FSMContext):
    video=message.video
    if not video: return
    root=settings.storage_root/'admin-imports'/str(message.from_user.id); root.mkdir(parents=True,exist_ok=True)
    name=str(video.file_name or f'video_{int(time.time()*1000)}.mp4')
    path=root/name
    await message.bot.download(video,destination=path)
    data=await state.get_data(); arr=list(data.get('admin_import_extra_files') or []); arr.append(str(path))
    notes=list(data.get('admin_import_extra_file_notes') or []); notes.append(str(message.caption or ''))
    await state.update_data(admin_import_extra_files=arr,admin_import_extra_file_notes=notes)
    await message.answer('✅ Видео добавлено. Подпись вроде «слайд 11» привяжет его к нужному слайду. Ещё файл/ссылка или /done_extras')

@router.message(AdminLessonImport.extras, F.text & ~Command('done_extras'))
async def admin_import_extra_text(message:Message,state:FSMContext):
    data=await state.get_data(); arr=list(data.get('admin_import_extra_texts') or []); arr.append(message.text or ''); await state.update_data(admin_import_extra_texts=arr)
    await message.answer('✅ Ссылка/примечание добавлено. Ещё или /done_extras')

@router.message(AdminLessonImport.extras, Command('done_extras'))
async def admin_done_extras(message:Message,state:FSMContext):
    await _finish_admin_import(message,state)

@router.message(Command('publishlesson'))
async def admin_publish_lesson(message:Message):
    if message.from_user.id not in settings.admin_ids: return
    parts=(message.text or '').split()
    if len(parts)!=2: await message.answer('Формат: /publishlesson lesson_id'); return
    path=ensure_persistent_lesson(parts[1])/'lesson.json'
    if not path.exists(): await message.answer('Урок не найден.'); return
    d=json.loads(path.read_text('utf-8')); errors=validate_content_lesson(d)
    hw_path=path.parent/'homework.json'
    if hw_path.exists(): errors += validate_homework(json.loads(hw_path.read_text('utf-8')))
    if errors:
        text='❌ Публикация заблокирована. Исправь ошибки:\n'+'\n'.join('• '+x for x in errors[:40])
        await message.answer(text[:3900]); return
    backup_lesson_version(parts[1],'before_publish')
    d['active']=True; d['import_status']='PUBLISHED'; path.write_text(json.dumps(d,ensure_ascii=False,indent=2),'utf-8'); await message.answer('✅ Урок опубликован. Новый ZIP и redeploy не нужны.')


@router.message(Command('unpublishlesson'))
async def admin_unpublish_lesson(message:Message):
    if message.from_user.id not in settings.admin_ids: return
    parts=(message.text or '').split(); lid=parts[1] if len(parts)==2 else ''
    path=ensure_persistent_lesson(lid)/'lesson.json' if lid else None
    if not path or not path.exists(): await message.answer('Формат: /unpublishlesson lesson_id'); return
    backup_lesson_version(lid,'before_unpublish'); d=json.loads(path.read_text('utf-8')); d['active']=False; d['import_status']='DRAFT'; path.write_text(json.dumps(d,ensure_ascii=False,indent=2),'utf-8')
    await message.answer('✅ Урок скрыт. Уже существующие данные прогресса не удалены.')


@router.message(Command('lessonorder'))
async def admin_lesson_order(message:Message):
    if message.from_user.id not in settings.admin_ids: return
    parts=(message.text or '').split()
    if len(parts)!=3 or not parts[2].isdigit(): await message.answer('Формат: /lessonorder lesson_id 20'); return
    lid,order=parts[1],int(parts[2]); path=ensure_persistent_lesson(lid)/'lesson.json'
    if not path.exists(): await message.answer('Урок не найден.'); return
    backup_lesson_version(lid,'order'); d=json.loads(path.read_text('utf-8')); d['order']=order; path.write_text(json.dumps(d,ensure_ascii=False,indent=2),'utf-8')
    await message.answer(f'✅ Порядок урока: {order}. Новая выдача учитывает порядок, но уже выданные уроки не дублируются.')


@router.message(Command('extrarun'))
async def admin_extra_run(message:Message):
    if message.from_user.id not in settings.admin_ids: return
    parts=(message.text or '').split()
    if len(parts) not in {3,4} or not parts[1].isdigit(): await message.answer('Формат: /extrarun child_id lesson_id [course_id]'); return
    child_id,lid=int(parts[1]),parts[2]; course_id=parts[3] if len(parts)==4 else str((load_authored_lesson(lid) or {}).get('course_id') or course_for_lesson(lid) or 'conversation')
    async with SessionLocal() as db:
        e=await db.scalar(select(LessonEntitlement).where(LessonEntitlement.child_id==child_id,LessonEntitlement.lesson_id==lid,LessonEntitlement.course_id==course_id).order_by(LessonEntitlement.id.desc()))
        if not e: await message.answer('Сначала урок должен быть выдан ребёнку.'); return
        e.max_completed_runs=max(int(e.max_completed_runs or 2),int(e.completed_runs or 0)+1); e.status='ACTIVE'; await db.commit()
    await message.answer(f'✅ Добавлено дополнительное прохождение. Теперь лимит для этого ребёнка: {e.max_completed_runs}.')


@router.message(Command('slidetype'))
async def admin_slide_type(message:Message):
    if message.from_user.id not in settings.admin_ids: return
    parts=(message.text or '').split()
    if len(parts)!=4 or not parts[2].isdigit(): await message.answer('Формат: /slidetype lesson_id 25 tap_sound'); return
    lid,n,typ=parts[1],int(parts[2]),parts[3]
    from app.services.authored_content import SUPPORTED_CONTENT_TYPES
    if typ not in SUPPORTED_CONTENT_TYPES: await message.answer('Неизвестный тип задания.'); return
    path=ensure_persistent_lesson(lid)/'lesson.json'
    if not path.exists(): await message.answer('Урок не найден.'); return
    d=json.loads(path.read_text('utf-8')); slides=d.get('slides') or []
    if not 1<=n<=len(slides): await message.answer('Нет такого слайда.'); return
    backup_lesson_version(lid,'slidetype'); slides[n-1]['type']=typ; slides[n-1]['expects_answer']=typ not in {'passive','video','physical_action'}; path.write_text(json.dumps(d,ensure_ascii=False,indent=2),'utf-8')
    await message.answer(f'✅ Слайд {n}: {typ}. Сохранено без redeploy.')

@router.message(Command('slideconfig'))
async def admin_slide_config(message:Message):
    """Patch any slide fields without a ZIP, e.g. hotspots/pairs/items/targets/prompt."""
    if message.from_user.id not in settings.admin_ids: return
    parts=(message.text or '').split(maxsplit=3)
    if len(parts)!=4 or not parts[2].isdigit():
        await message.answer('Формат: /slideconfig lesson_id 25 {"hotspots":[...]}'); return
    lid,n,raw=parts[1],int(parts[2]),parts[3]
    try:
        patch=json.loads(raw)
        if not isinstance(patch,dict): raise ValueError()
    except Exception:
        await message.answer('Последний параметр должен быть JSON-объектом.'); return
    forbidden={'slide_id','order'}
    if forbidden & set(patch):
        await message.answer('slide_id/order менять этой командой нельзя.'); return
    path=ensure_persistent_lesson(lid)/'lesson.json'
    if not path.exists(): await message.answer('Урок не найден.'); return
    d=json.loads(path.read_text('utf-8')); slides=d.get('slides') or []
    if not 1<=n<=len(slides): await message.answer('Нет такого слайда.'); return
    backup_lesson_version(lid,'slideconfig'); slides[n-1].update(patch); path.write_text(json.dumps(d,ensure_ascii=False,indent=2),'utf-8')
    await message.answer(f'✅ Параметры слайда {n} обновлены без redeploy.')

@router.message(Command('slideprompt'))
async def admin_slide_prompt(message:Message):
    if message.from_user.id not in settings.admin_ids: return
    parts=(message.text or '').split(maxsplit=3)
    if len(parts)!=4 or not parts[2].isdigit(): await message.answer('Формат: /slideprompt lesson_id 25 Новый текст'); return
    lid,n,text=parts[1],int(parts[2]),parts[3]
    path=ensure_persistent_lesson(lid)/'lesson.json'
    if not path.exists(): await message.answer('Урок не найден.'); return
    d=json.loads(path.read_text('utf-8')); slides=d.get('slides') or []
    if not 1<=n<=len(slides): await message.answer('Нет такого слайда.'); return
    backup_lesson_version(lid,'slideprompt'); slides[n-1]['prompt']=text; slides[n-1]['audio_text']=text; path.write_text(json.dumps(d,ensure_ascii=False,indent=2),'utf-8')
    await message.answer(f'✅ Текст слайда {n} обновлён.')

@router.message(Command('hwtype'))
async def admin_homework_type(message:Message):
    if message.from_user.id not in settings.admin_ids: return
    parts=(message.text or '').split()
    if len(parts)!=4 or not parts[2].isdigit(): await message.answer('Формат: /hwtype lesson_id 2 trace'); return
    lid,n,typ=parts[1],int(parts[2]),parts[3]
    from app.services.authored_content import SUPPORTED_CONTENT_TYPES
    if typ not in SUPPORTED_CONTENT_TYPES: await message.answer('Неизвестный тип задания.'); return
    path=ensure_persistent_lesson(lid)/'homework.json'
    if not path.exists(): await message.answer('ДЗ не найдено.'); return
    d=json.loads(path.read_text('utf-8')); slides=d.get('slides') or []
    if not 1<=n<=len(slides): await message.answer('Нет такой страницы ДЗ.'); return
    backup_lesson_version(lid,'hwtype'); slides[n-1]['type']=typ; slides[n-1]['expects_answer']=True; path.write_text(json.dumps(d,ensure_ascii=False,indent=2),'utf-8')
    await message.answer(f'✅ ДЗ · страница {n}: {typ}.')

@router.message(Command('hwconfig'))
async def admin_homework_config(message:Message):
    if message.from_user.id not in settings.admin_ids: return
    parts=(message.text or '').split(maxsplit=3)
    if len(parts)!=4 or not parts[2].isdigit(): await message.answer('Формат: /hwconfig lesson_id 2 {"items":[...]}'); return
    lid,n,raw=parts[1],int(parts[2]),parts[3]
    try:
        patch=json.loads(raw)
        if not isinstance(patch,dict): raise ValueError()
    except Exception: await message.answer('Нужен JSON-объект.'); return
    path=ensure_persistent_lesson(lid)/'homework.json'
    if not path.exists(): await message.answer('ДЗ не найдено.'); return
    d=json.loads(path.read_text('utf-8')); slides=d.get('slides') or []
    if not 1<=n<=len(slides): await message.answer('Нет такой страницы ДЗ.'); return
    backup_lesson_version(lid,'hwconfig'); slides[n-1].update({k:v for k,v in patch.items() if k not in {'slide_id','order'}}); path.write_text(json.dumps(d,ensure_ascii=False,indent=2),'utf-8')
    await message.answer(f'✅ Параметры ДЗ · страница {n} обновлены без redeploy.')

@router.message(Command('hwmap'))
async def admin_homework_map(message:Message):
    if message.from_user.id not in settings.admin_ids: return
    parts=(message.text or '').split(); lid=parts[1] if len(parts)==2 else ''
    path=ensure_persistent_lesson(lid)/'homework.json' if lid else None
    if not path or not path.exists(): await message.answer('Формат: /hwmap lesson_id'); return
    d=json.loads(path.read_text('utf-8')); text='\n'.join(f"{x.get('order')}: {x.get('type')} — {x.get('prompt','')[:60]}" for x in (d.get('slides') or []))
    for i in range(0,len(text),3500): await message.answer(text[i:i+3500])

@router.message(Command('lessonmap'))
async def admin_lesson_map(message:Message):
    if message.from_user.id not in settings.admin_ids: return
    parts=(message.text or '').split(); d=load_authored_lesson(parts[1]) if len(parts)==2 else None
    if not d: await message.answer('Формат: /lessonmap lesson_id'); return
    lines=[f"{x.get('order')}: {x.get('type')} — {x.get('prompt','')[:60]}" for x in (d.get('slides') or [])]
    text='\n'.join(lines)
    for i in range(0,len(text),3500): await message.answer(text[i:i+3500])



@router.message(Command('validate_lesson'))
async def admin_validate_lesson(message:Message):
    if message.from_user.id not in settings.admin_ids: return
    parts=(message.text or '').split(); lid=parts[1] if len(parts)==2 else ''
    d=load_authored_lesson(lid) if lid else None
    if not d: await message.answer('Формат: /validate_lesson lesson_id'); return
    errors=validate_content_lesson(d); hw=load_homework(lid)
    if hw: errors+=validate_homework(hw)
    if errors: await message.answer(('❌ Есть ошибки:\n'+'\n'.join('• '+x for x in errors[:50]))[:3900])
    else: await message.answer('✅ Валидация пройдена. Урок и ДЗ готовы к предпросмотру/публикации.')

@router.message(Command('lessonversions'))
async def admin_lesson_versions(message:Message):
    if message.from_user.id not in settings.admin_ids: return
    parts=(message.text or '').split(); lid=parts[1] if len(parts)==2 else ''
    versions=list_lesson_versions(lid) if lid else []
    if not lid: await message.answer('Формат: /lessonversions lesson_id'); return
    if not versions: await message.answer('Версий пока нет.'); return
    await message.answer('\n'.join(p.name for p in versions[:20]))

@router.message(Command('lessonrestore'))
async def admin_lesson_restore(message:Message):
    if message.from_user.id not in settings.admin_ids: return
    parts=(message.text or '').split(maxsplit=2)
    if len(parts)!=3: await message.answer('Формат: /lessonrestore lesson_id version_name'); return
    ok=restore_lesson_version(parts[1],parts[2]); await message.answer('✅ Версия восстановлена.' if ok else 'Версия не найдена.')

@router.message(Command('previewlesson'))
async def admin_preview_lesson(message:Message,state:FSMContext):
    if message.from_user.id not in settings.admin_ids: return
    parts=(message.text or '').split(); lid=parts[1] if len(parts)==2 else ''
    lesson=load_authored_lesson(lid) if lid else None
    if not lesson: await message.answer('Формат: /previewlesson lesson_id'); return
    errors=validate_content_lesson(lesson)
    hw=load_homework(lid)
    if hw: errors += validate_homework(hw)
    if errors: await message.answer(('❌ Сначала исправь урок/ДЗ:\n'+'\n'.join('• '+x for x in errors[:30]))[:3900]); return
    child=await get_child_from_state_or_user(state,message.from_user.id)
    if child is None: await message.answer('Для предпросмотра администратору нужен профиль ребёнка в этом Telegram-аккаунте.'); return
    runtime={'title':lesson.get('title') or lid,'topic':lesson.get('title') or lid,'slides':lesson.get('slides') or [],'make_cartoon':False,'lesson_id':lid,'course_id':lesson.get('course_id'),'target_language':lesson.get('target_language') or 'ru','target_duration_minutes':lesson.get('target_duration_minutes',35)}
    await state.update_data(authored_mode=True,authored_preview_mode=True,authored_homework_mode=False,authored_lesson_id=lid,authored_course_id=lesson.get('course_id'),authored_lesson_dir=str(lesson_dir(lid)),free_topic_lesson=runtime,free_topic_key=f'preview_{lid}',free_topic_step=0,free_topic_run=0,free_topic_voice_files=[],free_topic_images=[],free_topic_attempts={},free_topic_skip_busy=False,lesson_started_monotonic=time.monotonic(),reading_support=0)
    await state.set_state(FreeTopicFlow.playing); await message.answer('🧪 Предпросмотр: прохождение не расходуется, мультфильм не создаётся.'); await _send_free_topic_step(message,state,child)

VOWELS = set("аеёиоуыэюяaeiouyAEIOUYАЕЁИОУЫЭЮЯ")


def _slide_expects_answer(slide: dict | None) -> bool:
    """Return True only for activities that explicitly require learner input.

    Passive presentation/explanation slides must never be blocked merely because
    they contain a legacy `question` field or because a previous AI follow-up
    was pending.
    """
    if not slide:
        return False
    if slide.get("expects_answer") is not None:
        return bool(slide.get("expects_answer"))
    if slide.get("answer_mode") in {"required_voice", "optional_voice"}:
        return True
    if slide.get("type") in {"card_selector", "image_choice", "object_click", "mood_choice"}:
        return True
    if slide.get("interactive_task"):
        return True
    return False

def _resolve_next_step(slides: list[dict], current_step: int, slide: dict | None = None) -> int:
    """Resolve the scenario-defined next slide, falling back to runtime order.

    v56 makes the lesson script authoritative. This prevents the voice state from
    drifting to an unrelated array neighbour when slides were reordered.
    """
    slide = slide or (slides[current_step] if 0 <= current_step < len(slides) else {})
    target_id = (slide or {}).get("next_slide")
    if target_id:
        for i, item in enumerate(slides):
            if item.get("slide_id") == target_id and not item.get("skip_in_runtime"):
                return i
    return next_runtime_step(slides, current_step)


def _lesson_emission_key(slide: dict, data: dict) -> str:
    """Stable idempotency key for one visible lesson emission.

    Animal compare intentionally has multiple questions on one source slide, so
    question index is part of the key. Everything else emits once per slide.
    """
    slide_id = str(slide.get("slide_id") or data.get("slide_step", 0))
    if slide.get("interactive_task") == "animal_compare":
        return f"{slide_id}:animal_compare:{int(data.get('animal_compare_question_index', 0) or 0)}"
    return slide_id


async def _reset_for_next_step(state: FSMContext, *, next_step: int) -> None:
    """Atomically clear all transient state before a new lesson step opens."""
    await state.update_data(
        slide_step=next_step, context_slide_step=None,
        pending_question=None, expected_answer=None, selected_animal=None, selected_choice=None,
        current_goal=None, current_simplified_text=None, current_accepted_meaning=[],
        current_phrase_id=None, current_required_phrase=False, current_max_voice_seconds=60,
        required_phrase_owner_slide_id=None,
        followup_pending=False, followup_slide_id=None, ai_followup_count=0,
        correction_count=0, technical_count=0, recording_number=0, simplified_mode=False, attempt=0,
        post_required_started=False, skip_in_progress=False, post_voice_jump=None,
        animal_compare_pending=False, animal_compare_task=None, post_compare_phrase_id=None,
        animal_compare_resume=False, animal_compare_pair_id=None, animal_compare_question_index=0,
        suitcase_pending=False, suitcase_completed=False,
        last_emission_key=None,
    )


def _syllabify_word(word: str) -> str:
    if len(word) < 4:
        return word
    parts=[]; current=""; vowel_seen=False
    for i,ch in enumerate(word):
        current += ch
        if ch in VOWELS:
            vowel_seen=True
            # Close a syllable before the next vowel group, keeping one following consonant when possible.
            tail=word[i+1:]
            if tail and any(c in VOWELS for c in tail):
                if i+1 < len(word) and word[i+1] not in VOWELS:
                    current += word[i+1]
                    parts.append(current); current=""
                else:
                    parts.append(current); current=""
    if current:
        parts.append(current)
    return "·".join(x for x in parts if x) if vowel_seen and len(parts)>1 else word

def syllabify_phrase(text: str) -> str:
    return " ".join(_syllabify_word(w) for w in text.split())

async def _get_character_path(character_id: int | None) -> str | None:
    if not character_id:
        return None
    async with SessionLocal() as db:
        character = await db.get(Character, int(character_id))
        if not character:
            return None
        return character.processed_path or character.original_path


async def _persist_step(session_id: int | None, slide_step: int) -> None:
    if not session_id:
        return
    async with SessionLocal() as db:
        session = await db.get(LessonSession, int(session_id))
        if session and session.status == "IN_PROGRESS":
            session.current_step = int(slide_step)
            await db.commit()


VOICE_CONSENT_TEXT_RU = (
    "Согласие родителя (законного представителя) на обработку данных ребёнка. "
    "Я подтверждаю, что являюсь родителем, усыновителем, опекуном либо иным законным представителем "
    "несовершеннолетнего ребёнка и вправе действовать от его имени. Добровольно и осознанно даю DOME "
    "согласие на запись, получение, систематизацию, хранение, использование и автоматизированную обработку "
    "голосовых записей ребёнка и связанных с ними персональных данных в целях проведения языковых занятий, "
    "распознавания и анализа речи, оценки произношения и учебного прогресса, персонализации заданий, подготовки "
    "отчётов и создания персонализированных аудиовизуальных материалов, включая мультфильмы. Согласие включает "
    "обработку голосовых данных, в том числе как биометрических персональных данных в случаях, когда они могут "
    "использоваться для установления личности, а также передачу минимально необходимого объёма данных поставщикам "
    "облачной инфраструктуры, распознавания речи, синтеза речи и иных технических сервисов, привлекаемых для оказания "
    "услуги. Тренировочные записи, не включённые в итоговый материал, подлежат удалению после завершения обработки "
    "в соответствии с политикой хранения данных. Я уведомлена, что могу отозвать согласие, направив обращение оператору; "
    "после отзыва функции, требующие записи и обработки голоса, могут стать недоступны. Подтверждение настоящего согласия "
    "осуществляется одноразовым кодом, направленным по SMS на указанный мной номер телефона, и фиксируется с датой, временем, "
    "номером телефона, Telegram ID, версией текста согласия и техническими данными подтверждения."
)
PAYMENT_CONSENT_TEXT_RU = (
    "Соглашение об оплате. Я подтверждаю выбранный тариф и понимаю, что оплата проводится на защищённой "
    "странице платёжного провайдера. DOME не получает и не хранит полные данные банковской карты. "
    "Условия возврата и периодичность списаний должны быть указаны на странице оплаты выбранного тарифа."
)

async def _has_consent(parent_id: int, child_id: int, consent_type: str, version: str) -> bool:
    async with SessionLocal() as db:
        row = await db.scalar(select(ConsentRecord).where(
            ConsentRecord.parent_id == parent_id,
            ConsentRecord.child_id == child_id,
            ConsentRecord.consent_type == consent_type,
            ConsentRecord.version == version,
        ).order_by(ConsentRecord.id.desc()))
        return row is not None

async def _request_consent(message: Message, child: Child, consent_type: str) -> None:
    text = VOICE_CONSENT_TEXT_RU if consent_type == "VOICE_RECORDING" else PAYMENT_CONSENT_TEXT_RU
    heading = "🎙 Согласие на запись голоса" if consent_type == "VOICE_RECORDING" else "💳 Соглашение об оплате"
    await message.answer(
        f"{heading}\n\n{text}\n\nПодтверждение выполняется кодом из SMS.",
        reply_markup=consent_agreement_keyboard(consent_type, child.native_language or "ru"),
    )


async def get_or_create_parent(tg_id: int, name: str) -> Parent:
    async with SessionLocal() as db:
        parent = await db.scalar(select(Parent).where(Parent.telegram_user_id == tg_id))
        if parent is None:
            parent = Parent(telegram_user_id=tg_id, display_name=name)
            db.add(parent)
            await db.commit()
            await db.refresh(parent)
        return parent


async def get_current_child(tg_id: int) -> Child | None:
    async with SessionLocal() as db:
        parent = await db.scalar(select(Parent).where(Parent.telegram_user_id == tg_id))
        if parent is None:
            return None
        if getattr(parent,"active_child_id",None):
            chosen=await db.scalar(select(Child).where(Child.id==parent.active_child_id,Child.parent_id==parent.id))
            if chosen is not None: return chosen
        chosen=await db.scalar(select(Child).where(Child.parent_id == parent.id).order_by(Child.id.asc()))
        if chosen is not None:
            parent.active_child_id=chosen.id; await db.commit()
        return chosen


async def get_child_from_state_or_user(state: FSMContext, tg_id: int) -> Child | None:
    data = await state.get_data()
    child_id = data.get("child_id")
    if child_id:
        async with SessionLocal() as db:
            child = await db.scalar(select(Child).join(Parent,Child.parent_id==Parent.id).where(Child.id==int(child_id),Parent.telegram_user_id==tg_id))
            if child:
                return child
        # Never trust a stale/cross-parent FSM child id. Fall back to the parent's persisted active child.
        await state.update_data(child_id=None)
    child = await get_current_child(tg_id)
    if child:
        changed = False
        if not child.native_language:
            child.native_language = "ru"; changed = True
        if not child.target_language:
            child.target_language = "en"; changed = True
        if changed:
            async with SessionLocal() as db:
                db_child = await db.get(Child, child.id)
                db_child.native_language = child.native_language
                db_child.target_language = child.target_language
                await db.commit()
        await state.update_data(child_id=child.id)
    return child


async def _maybe_birthday_greeting(message: Message, child: Child) -> None:
    if not getattr(child,'birth_day',None) or not getattr(child,'birth_month',None):
        return
    today=datetime.now().date()
    if (today.day,today.month)!=(child.birth_day,child.birth_month) or getattr(child,'birthday_greeted_year',None)==today.year:
        return
    native=child.native_language or 'ru'
    text=(f"🎉 С днём рождения, {child.display_name}! Желаю тебе ярких приключений и новых побед!" if native=='ru' else f"🎉 Happy birthday, {child.display_name}! Wishing you a wonderful day and new adventures!")
    await message.answer(text)
    async with SessionLocal() as db:
        row=await db.get(Child,child.id); row.birthday_greeted_year=today.year; await db.commit()


async def show_menu(message: Message, state: FSMContext, child: Child) -> None:
    await _maybe_birthday_greeting(message, child)
    await state.update_data(child_id=child.id)
    await message.answer(tr(child.native_language, "menu_title"), reply_markup=menu_hub_keyboard(child.native_language or "en"))


async def show_payment_prompt(message: Message, child: Child, allow_skip: bool = True) -> None:
    native = child.native_language or "en"
    text = ("Привяжите карту для будущей оплаты уроков. Сейчас этот шаг можно пропустить." if native == "ru" else "Link a payment card for future lessons. You can skip this step for now.")
    await message.answer(text, reply_markup=payment_prompt_keyboard(native, settings.payment_url, allow_skip=allow_skip))


async def _create_and_send_homework(message: Message, child: Child, session_id: int, attempts: list, completed_lessons: int, parent: Parent | None = None, lesson_id: str = LESSON_ID) -> HomeworkAssignment | None:
    homework_text, duration_minutes, hw_cfg = await resolve_homework(child, attempts, lesson_id=lesson_id)
    if not homework_text:
        return None
    async with SessionLocal() as db:
        row = HomeworkAssignment(
            child_id=child.id, lesson_session_id=session_id, lesson_id=lesson_id,
            title="Домашнее задание", body=homework_text, duration_minutes=duration_minutes,
            status="NEW", optional=bool(hw_cfg.get("optional", True)),
        )
        db.add(row); await db.commit(); await db.refresh(row)
        if parent is None:
            db_child = await db.get(Child, child.id)
            parent = await db.get(Parent, db_child.parent_id)
    if hw_cfg.get("send_to_bot", True):
        await message.answer(
            f"🏠 Домашнее задание · около {duration_minutes} минут · выполнять необязательно\n\n" + homework_text +
            "\n\nМожно выполнить сейчас, оставить на потом или пропустить. Пропуск не влияет на следующий урок.",
            reply_markup=homework_keyboard(row.id, child.native_language or "ru"),
        )
    if hw_cfg.get("send_to_parent_email", True) and parent and parent.email_reports_enabled and parent.email:
        try:
            subject, body = build_progress_report(child, completed_lessons, attempts, homework_text=homework_text)
            await send_progress_report(parent.email, subject, body)
        except Exception as exc:
            log.warning("Homework email failed: %s", exc)
    return row


@router.callback_query(F.data.startswith("homework:do:"))
async def homework_do(cb: CallbackQuery, state: FSMContext):
    hid=int(cb.data.rsplit(":",1)[1])
    async with SessionLocal() as db:
        row=await db.get(HomeworkAssignment,hid)
        if row: row.status="OPENED"; await db.commit()
    if not row:
        await cb.answer("Домашнее задание не найдено", show_alert=True); return
    child=await get_child_from_state_or_user(state,cb.from_user.id)
    if child and load_homework(row.lesson_id):
        await _start_authored_homework(cb.message,state,child,row.lesson_id,row.id)
    else:
        await cb.message.answer("🏠 Домашнее задание\n\n"+row.body+"\n\nКогда закончишь, просто возвращайся в меню. Это задание необязательное.")
    await cb.answer()


@router.callback_query(F.data.startswith("homework:later:"))
async def homework_later(cb: CallbackQuery):
    hid=int(cb.data.rsplit(":",1)[1])
    async with SessionLocal() as db:
        row=await db.get(HomeworkAssignment,hid)
        if row: row.status="DEFERRED"; await db.commit()
    await cb.answer("Оставила на потом")


@router.callback_query(F.data.startswith("homework:skip:"))
async def homework_skip(cb: CallbackQuery):
    hid=int(cb.data.rsplit(":",1)[1])
    async with SessionLocal() as db:
        row=await db.get(HomeworkAssignment,hid)
        if row: row.status="SKIPPED"; await db.commit()
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("Пропущено. Это не влияет на прогресс и доступ к урокам.")


@router.callback_query(F.data == "homework:archive")
async def homework_archive(cb: CallbackQuery, state: FSMContext):
    child=await get_child_from_state_or_user(state, cb.from_user.id)
    if not child:
        await cb.answer("/start",show_alert=True); return
    async with SessionLocal() as db:
        rows=(await db.scalars(select(HomeworkAssignment).where(HomeworkAssignment.child_id==child.id).order_by(HomeworkAssignment.id.desc()).limit(10))).all()
    if not rows:
        await cb.message.answer("Домашних заданий пока нет.")
    else:
        text = "📚 Домашние задания\n\n" + "\n\n".join(
            f"#{x.id} · {x.status}\n{x.body}" for x in rows
        )
        await cb.message.answer(text[:3900])
    await cb.answer()


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    activity("telegram_start", tg_id=message.from_user.id, tg_name=message.from_user.full_name, username=message.from_user.username)
    await state.clear()
    await get_or_create_parent(message.from_user.id, message.from_user.full_name)
    child = await get_current_child(message.from_user.id)
    if child and child.native_language and child.target_language:
        await state.update_data(child_id=child.id)
        if not getattr(child, "gender", None):
            await message.answer("Для правильной подписи финального мультфильма укажи пол ребёнка:", reply_markup=gender_keyboard(child.native_language or "ru"))
            await state.set_state(SettingsFlow.profile_gender)
            return
        if getattr(child, "can_read_target", None) is None:
            await message.answer("Умеет ли ребёнок читать на изучаемом языке? Это нужно, чтобы задания не зависели от чтения.", reply_markup=reading_ability_keyboard(child.native_language or "ru"))
            await state.set_state(SettingsFlow.target_reading)
            return
        await show_menu(message, state, child)
        return
    await message.answer(tr("ru", "name_question"))
    await state.set_state(Onboarding.child_name)


@router.message(Command("set_email"))
async def set_email_command(message: Message, state: FSMContext):
    child = await get_child_from_state_or_user(state, message.from_user.id)
    if child is None:
        await start(message, state); return
    await state.set_state(SettingsFlow.parent_email)
    await message.answer("Введите email родителя для отчётов о прогрессе:")


@router.message(SettingsFlow.parent_email, F.text)
async def save_parent_email(message: Message, state: FSMContext):
    email = message.text.strip()
    if "@" not in email or "." not in email.split("@")[-1]:
        await message.answer("Email выглядит неверно. Введите ещё раз."); return
    async with SessionLocal() as db:
        child = await db.get(Child, (await state.get_data()).get("child_id"))
        parent = await db.get(Parent, child.parent_id)
        parent.email = email; parent.email_reports_enabled = True
        await db.commit()
    await state.set_state(None)
    child = await get_child_from_state_or_user(state, message.from_user.id)
    await message.answer("Email сохранён. Отчёт будет отправляться после завершённого урока.", reply_markup=parent_menu_keyboard((child.native_language if child else "ru") or "ru"))


@router.message(Command("progress"))
async def progress_command(message: Message, state: FSMContext):
    child = await get_child_from_state_or_user(state, message.from_user.id)
    if child is None:
        await start(message, state); return
    await message.answer(
        f"Уровень: {child.language_level}\nРабочая сложность: {child.working_difficulty:.0%}\n"
        f"Понимание: {child.comprehension_score:.0%}\nГрамматика: {child.grammar_score:.0%}\n"
        f"Словарь: {child.vocabulary_score:.0%}\nПроизношение: {child.pronunciation_score:.0%}\n"
        f"Беглость: {child.fluency_score:.0%}\nСамостоятельность: {child.independence_score:.0%}"
    )



@router.message(Command("whoami"))
async def whoami_command(message: Message):
    await message.answer(f"Ваш Telegram ID: {message.from_user.id}")


@router.message(Command("activity"))
async def activity_command(message: Message):
    if message.from_user.id not in settings.admin_ids:
        await message.answer("Команда доступна только администратору. Сначала добавьте свой Telegram ID в Railway → Variables → ADMIN_TELEGRAM_IDS.")
        return
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(LessonSession, Child, Parent)
            .join(Child, LessonSession.child_id == Child.id)
            .join(Parent, Child.parent_id == Parent.id)
            .order_by(desc(LessonSession.id))
            .limit(12)
        )).all()
        lines = ["📋 Последние действия в уроках"]
        for session, child, parent in rows:
            last_attempt = await db.scalar(
                select(VoiceAttempt)
                .where(VoiceAttempt.lesson_session_id == session.id)
                .order_by(desc(VoiceAttempt.id))
            )
            last = "нет голосовых ответов"
            if last_attempt:
                txt = (last_attempt.transcript or "").strip().replace("\n", " ")
                if len(txt) > 70:
                    txt = txt[:67] + "…"
                last = f"{last_attempt.status}; {last_attempt.phrase_id}; ‘{txt}’"
            when = session.completed_at or session.created_at
            lines.append(
                f"\n👦 {child.display_name} | TG {parent.telegram_user_id}"
                f"\nУрок: {session.lesson_id} | session {session.id}"
                f"\nСтатус: {session.status} | шаг: {session.current_step}"
                f"\nПоследний ответ: {last}"
                f"\nВремя: {when:%Y-%m-%d %H:%M}"
            )
    text = "\n".join(lines)
    await message.answer(text[:4000])


def _lesson_admin_title(lesson_id: str) -> str:
    authored=load_authored_lesson(lesson_id)
    if authored:
        return str(authored.get("title") or lesson_id)
    try:
        legacy=load_lesson(lesson_id)
        return str(legacy.get("title") or legacy.get("topic") or lesson_id)
    except Exception:
        return lesson_id


def _authored_homework_attachment(lesson_id: str) -> str | None:
    hw=load_homework(lesson_id)
    if not hw:
        return None
    src=hw.get("source_file")
    if not src:
        return None
    p=lesson_dir(lesson_id)/str(src)
    return str(p) if p.exists() and p.is_file() else None


@router.message(Command("resendhomework"))
async def admin_resend_homework(message: Message):
    """Resend the latest existing homework without creating a duplicate assignment."""
    if message.from_user.id not in settings.admin_ids:
        return
    parts=(message.text or "").split()
    if len(parts)!=3 or not parts[1].isdigit():
        await message.answer("Формат: /resendhomework child_id lesson_id")
        return
    child_id=int(parts[1]); lesson_id=parts[2]
    async with SessionLocal() as db:
        child=await db.get(Child,child_id)
        if not child:
            await message.answer("Ребёнок не найден."); return
        parent=await db.get(Parent,child.parent_id)
        hw=await db.scalar(select(HomeworkAssignment).where(
            HomeworkAssignment.child_id==child_id, HomeworkAssignment.lesson_id==lesson_id
        ).order_by(HomeworkAssignment.id.desc()))
    if not hw:
        await message.answer("Домашнее задание для этого ребёнка и урока ещё не выдавалось.")
        return
    bot_ok=email_ok=False
    errors=[]
    if parent and parent.telegram_user_id:
        try:
            await message.bot.send_message(
                parent.telegram_user_id,
                f"🏠 Домашнее задание · повторная отправка\n\n{hw.body}\n\nМожно выполнить сейчас, оставить на потом или пропустить.",
                reply_markup=homework_keyboard(hw.id, child.native_language or "ru"),
            )
            bot_ok=True
        except Exception as exc:
            errors.append(f"бот: {exc}")
    if parent and parent.email_reports_enabled and parent.email:
        try:
            await send_homework_email(parent.email,child.display_name,_lesson_admin_title(lesson_id),hw.body,_authored_homework_attachment(lesson_id))
            email_ok=True
        except Exception as exc:
            errors.append(f"email: {exc}")
    status=f"✅ ДЗ переотправлено. В бот: {'да' if bot_ok else 'нет'}; email: {'да' if email_ok else 'нет'}."
    if errors: status += "\n⚠️ " + " | ".join(errors)[:1200]
    await message.answer(status)


@router.message(Command("resendreport"))
async def admin_resend_report(message: Message):
    """Rebuild and resend a progress report from the latest completed session."""
    if message.from_user.id not in settings.admin_ids:
        return
    parts=(message.text or "").split()
    if len(parts)!=3 or not parts[1].isdigit():
        await message.answer("Формат: /resendreport child_id lesson_id")
        return
    child_id=int(parts[1]); lesson_id=parts[2]
    async with SessionLocal() as db:
        child=await db.get(Child,child_id)
        if not child:
            await message.answer("Ребёнок не найден."); return
        parent=await db.get(Parent,child.parent_id)
        session=await db.scalar(select(LessonSession).where(
            LessonSession.child_id==child_id, LessonSession.lesson_id==lesson_id, LessonSession.status=="COMPLETED"
        ).order_by(LessonSession.id.desc()))
        completed=(await db.scalars(select(LessonSession.id).where(
            LessonSession.child_id==child_id, LessonSession.status=="COMPLETED"
        ))).all()
        attempts=[]
        if session:
            attempts=(await db.scalars(select(VoiceAttempt).where(VoiceAttempt.lesson_session_id==session.id).order_by(VoiceAttempt.id.asc()))).all()
        hw=await db.scalar(select(HomeworkAssignment).where(
            HomeworkAssignment.child_id==child_id, HomeworkAssignment.lesson_id==lesson_id
        ).order_by(HomeworkAssignment.id.desc()))
    if not session:
        await message.answer("У ребёнка нет завершённого прохождения этого урока."); return
    if not parent or not parent.email:
        await message.answer("У родителя не указан email."); return
    subject,body=build_progress_report(child,len(completed),attempts,homework_text=hw.body if hw else None)
    # Add a human-readable lesson-specific summary before the numeric detail.
    try: runtime=json.loads(session.runtime_state_json or "{}")
    except Exception: runtime={}
    stats=runtime.get("authored_stats") or {}
    if stats:
        intro=(f"{child.display_name} завершил(а) урок «{_lesson_admin_title(lesson_id)}». "
               f"В этом прохождении было {int(stats.get('voice_answers',0) or 0)} голосовых ответов и "
               f"{int(stats.get('interactive_tasks',0) or 0)} интерактивных заданий. "
               f"Дополнительных попыток понадобилось: {int(stats.get('retries',0) or 0)}.\n\n")
        body=intro+body
    try:
        await send_progress_report(parent.email,subject,body)
    except Exception as exc:
        await message.answer(f"Не удалось отправить отчёт: {exc}"); return
    await message.answer(f"✅ Отчёт по «{_lesson_admin_title(lesson_id)}» повторно отправлен на {parent.email}.")


@router.message(Command("retrycartoon"))
async def admin_retry_cartoon(message: Message, state: FSMContext):
    """Retry a first-run authored conversation cartoon without consuming a lesson run."""
    if message.from_user.id not in settings.admin_ids:
        return
    parts=(message.text or "").split()
    if len(parts)!=3 or not parts[1].isdigit():
        await message.answer("Формат: /retrycartoon child_id lesson_id")
        return
    child_id=int(parts[1]); lesson_id=parts[2]
    lesson=load_authored_lesson(lesson_id)
    if not lesson or not lesson.get("make_cartoon"):
        await message.answer("Это не импортированный разговорный урок с мультфильмом."); return
    course_id=str(lesson.get("course_id") or "conversation")
    async with SessionLocal() as db:
        child=await db.get(Child,child_id)
        session=await db.scalar(select(LessonSession).where(
            LessonSession.child_id==child_id,LessonSession.lesson_id==lesson_id,LessonSession.status=="COMPLETED"
        ).order_by(LessonSession.id.desc())) if child else None
    entitlement=await get_authored_entitlement(child_id,lesson_id,course_id) if child else None
    if not child or not session or not entitlement:
        await message.answer("Не найдены ребёнок, завершённая сессия или доступ к уроку."); return
    if entitlement.cartoon_generated:
        await message.answer("Мультфильм первого прохождения уже был создан."); return
    try: saved=json.loads(session.runtime_state_json or "{}")
    except Exception: saved={}
    data={"free_topic_lesson":{"title":lesson.get("title") or lesson_id,"make_cartoon":True,"course_id":course_id},
          "authored_lesson_id":lesson_id,"authored_course_id":course_id,"character_id":child.active_character_id,**saved}
    ok=await _maybe_build_authored_cartoon(message,state,child,data,entitlement)
    await message.answer("✅ Повторная сборка мультфильма завершена." if ok else "⚠️ Мультфильм пока не удалось собрать; прохождение не списано.")


@router.message(Command("version"))
async def version_command(message: Message):
    await message.answer("DOME v75\nВременно доступен только разговорный курс · мультфильм после первого полного прохождения")

@router.message(Command("menu"))
async def menu_command(message: Message, state: FSMContext):
    child = await get_child_from_state_or_user(state, message.from_user.id)
    if child is None:
        await start(message, state)
        return
    data = await state.get_data()
    if data.get("session_id") is not None and data.get("slide_step") is not None:
        await _persist_step(data.get("session_id"), int(data.get("slide_step", 0)))
    await state.clear()
    await state.update_data(child_id=child.id)
    await show_menu(message, state, child)


@router.message(Command("reset_session"))
async def reset_session_command(message: Message, state: FSMContext):
    child = await get_current_child(message.from_user.id)
    await state.clear()
    if child is None:
        await start(message, state)
        return
    async with SessionLocal() as db:
        sessions = (await db.scalars(select(LessonSession).where(
            LessonSession.child_id == child.id, LessonSession.status.in_(["IN_PROGRESS", "RENDERING"])
        ))).all()
        for session in sessions:
            session.status = "CANCELLED"
        await db.commit()
    await show_menu(message, state, child)


@router.callback_query(F.data == "menu:open")
async def menu_open(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True)
        return
    # Persist the exact runtime position before leaving the lesson. The menu
    # must never convert Continue into a restart.
    data = await state.get_data()
    if data.get("session_id") is not None and data.get("slide_step") is not None:
        await _persist_step(data.get("session_id"), int(data.get("slide_step", 0)))
    await state.clear()
    await state.update_data(child_id=child.id)
    await cb.message.answer(tr(child.native_language, "menu_title"), reply_markup=menu_hub_keyboard(child.native_language or "en"))
    await cb.answer()



@router.callback_query(F.data == "menu:child")
async def menu_child(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    features = load_settings("features").get("features", {})
    interest_on = (features.get("interest_lessons", {}).get("mode", "enabled") != "disabled")
    free_topic_cfg = load_settings("free_topic")
    show_free_topic = interest_on and bool(free_topic_cfg.get("show_in_child_menu", False))
    await cb.message.answer(tr(child.native_language, "child_menu_title"), reply_markup=child_menu_keyboard(child.native_language or "en", show_free_topic=show_free_topic))
    await cb.answer()


@router.callback_query(F.data == "menu:parent")
async def menu_parent(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    await cb.message.answer(tr(child.native_language, "parent_menu_title"), reply_markup=parent_menu_keyboard(child.native_language or "en"))
    await cb.answer()



@router.callback_query(F.data == "course_switch:open")
async def course_switch_open(cb: CallbackQuery, state: FSMContext):
    if CONVERSATION_ONLY:
        await cb.answer("Смена курса временно отключена.", show_alert=True); return
    child=await get_child_from_state_or_user(state,cb.from_user.id)
    if child is None: await cb.answer('/start',show_alert=True); return
    current=await _current_active_course_id(child.id)
    courses=[(c.course_id,c.title) for c in list_courses() if c.active and c.course_id!=current]
    if not courses:
        await cb.message.answer('Других активных курсов пока нет.'); await cb.answer(); return
    await state.update_data(course_switch_from=current)
    await cb.message.answer(f'Текущий курс: «{course_titles().get(current,current)}».\nПрогресс не удалится. Выберите новый курс:',reply_markup=course_switch_keyboard(current,courses,child.native_language or 'ru'))
    await cb.answer()

@router.callback_query(F.data.startswith("course_switch:target:"))
async def course_switch_target(cb: CallbackQuery, state: FSMContext):
    if CONVERSATION_ONLY:
        await cb.answer("Смена курса временно отключена.", show_alert=True); return
    child=await get_child_from_state_or_user(state,cb.from_user.id)
    if child is None: await cb.answer('/start',show_alert=True); return
    target=cb.data.split(':',2)[2]
    current=await _current_active_course_id(child.id)
    valid={c.course_id for c in list_courses() if c.active}
    if target not in valid or target==current:
        await cb.answer('Недоступный курс',show_alert=True); return
    await state.update_data(course_switch_from=current,course_switch_target=target)
    await cb.message.answer(
        f'Перевести {child.display_name} с «{course_titles().get(current,current)}» на «{course_titles().get(target,target)}»?\n\n'
        'Пройденные уроки и история останутся сохранены. Можно перейти после текущего урока или сразу.',
        reply_markup=course_switch_mode_keyboard(target,child.native_language or 'ru'))
    await cb.answer()

@router.callback_query(F.data.startswith("course_switch:mode:"))
async def course_switch_mode(cb: CallbackQuery, state: FSMContext):
    if CONVERSATION_ONLY:
        await cb.answer("Смена курса временно отключена.", show_alert=True); return
    child=await get_child_from_state_or_user(state,cb.from_user.id)
    if child is None: await cb.answer('/start',show_alert=True); return
    parts=cb.data.split(':')
    if len(parts)<4: await cb.answer('Ошибка',show_alert=True); return
    mode,target=parts[2],parts[3]
    current=await _current_active_course_id(child.id)
    if target not in {c.course_id for c in list_courses() if c.active} or target==current:
        await cb.answer('Недоступный курс',show_alert=True); return
    # If course-specific price differs, safely reprice via provider first.
    async with SessionLocal() as db:
        sub=await db.scalar(select(Subscription).where(Subscription.child_id==child.id,Subscription.course_id==current,Subscription.status=='ACTIVE').order_by(Subscription.id.desc()))
    freq=int(sub.lessons_per_week or 1) if sub else 1
    new_spec=next((p for p in subscription_plans_for_course(target) if int(p.get('lessons_per_week',0))==freq),None)
    old_price=float(sub.monthly_price or 0) if sub else 0
    new_base=float((new_spec or {}).get('monthly_price') or old_price)
    fp=await family_price_for_child(child.parent_id,child.id,new_base,freq)
    new_price=fp.effective_price
    if sub and not sub.test_mode and abs(new_price-old_price)>0.001:
        await state.update_data(pending_course_id=target,pending_payment_plan=str((new_spec or {}).get('id') or f'weekly{freq}'),course_switch_from=current,course_switch_target=target,course_switch_mode=('after_current' if mode=='after' else 'immediate'))
        await cb.message.answer(f'Цена нового курса для этого ребёнка: €{new_price:g}/мес вместо €{old_price:g}/мес. Сначала подтвердите изменение подписки у платёжного провайдера; курс переключится после подтверждения оплаты.')
        await _show_checkout(cb.message,state,child,str((new_spec or {}).get('id') or f'weekly{freq}'))
        await cb.answer(); return
    if mode=='after':
        save_course_switch(child.id,current,target,'after_current')
        await cb.message.answer('✅ Перевод запланирован после завершения текущего урока. До этого момента можно отменить/изменить выбор, выбрав другой курс.')
    else:
        result=await apply_course_switch(child.id,current,target)
        await cb.message.answer('✅ Курс изменён немедленно. Прогресс прошлого курса сохранён.' if result=='MOVED' else f'Не удалось сменить курс: {result}')
    await cb.answer()

@router.callback_query(F.data.startswith("course_transition:"))
async def choose_course_transition(cb: CallbackQuery, state: FSMContext):
    if CONVERSATION_ONLY:
        await cb.answer("Переходы между курсами временно отключены.", show_alert=True); return
    child=await get_child_from_state_or_user(state,cb.from_user.id)
    if child is None: await cb.answer('/start',show_alert=True); return
    parts=cb.data.split(':')
    if len(parts)!=3: await cb.answer('Ошибка',show_alert=True); return
    course_id,target=parts[1],parts[2]
    route=get_route(course_id)
    valid=set(route.get('next') or []) | ({'repeat'} if route.get('repeat_allowed',True) else set())
    if target not in valid:
        await cb.answer('Этот переход сейчас недоступен',show_alert=True); return
    save_choice(child.id,course_id,target)
    _,_,remaining=await course_progress(child.id,course_id)
    if remaining==0:
        result=await apply_transition(child.id,course_id,target)
        await cb.message.answer('✅ Продолжение обучения выбрано и уже применено.' if result in {'MOVED','REPEATED'} else f'Выбор сохранён. Статус: {result}')
    else:
        await cb.message.answer(f'✅ Выбор сохранён. После последнего урока курс переключится автоматически. До конца осталось {remaining}.')
    await cb.answer()

@router.callback_query(F.data == "menu:games")
async def menu_games(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    base = settings.effective_webapp_base_url or ''
    if not base:
        await cb.message.answer("Сначала настройте WEBAPP_BASE_URL или RAILWAY_PUBLIC_DOMAIN в .env / Railway.")
        await cb.answer(); return
    url = base + f"/games?lang={child.target_language or 'en'}&native={child.native_language or 'ru'}"
    await cb.message.answer("🎮 Открой яркий блок игр DOME.", reply_markup=open_webapp_keyboard("🎮 Открыть игры DOME", url, child.native_language or 'ru'))
    await cb.answer()


@router.callback_query(F.data == "menu:free_topic")
async def menu_free_topic(cb: CallbackQuery, state: FSMContext):
    features = load_settings("features").get("features", {})
    if features.get("interest_lessons", {}).get("mode", "enabled") == "disabled":
        await cb.answer("Уроки по интересам сейчас отключены.", show_alert=True); return
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    await state.set_state(SettingsFlow.free_topic)
    await cb.message.answer("Напишите тему, которая интересна ребёнку. Например: динозавры, рисование, Roblox, космос.")
    await cb.answer()


@router.message(SettingsFlow.free_topic, F.text)
async def menu_free_topic_save(message: Message, state: FSMContext):
    child = await get_child_from_state_or_user(state, message.from_user.id)
    if not child:
        await message.answer('/start'); return
    topic = message.text.strip()[:180]
    await state.update_data(free_topic=topic)
    await state.set_state(FreeTopicFlow.payment)
    await message.answer(
        f"🎨 Тема: {topic}\n\nСначала подтверждаем оплату. В тестовом режиме её можно пропустить.",
        reply_markup=free_topic_payment_keyboard(child.native_language or 'ru', allow_test_bypass=True),
    )


async def _start_free_topic(message: Message, state: FSMContext, child: Child, *, payment_mode: str):
    data=await state.get_data(); topic=str(data.get('free_topic') or 'интересная тема')
    if not child.active_character_id:
        await message.answer('🎭 Для мультфильма нужен герой. Сначала открой «Мой герой», загрузи своего или выбери готового, затем снова запусти урок на свободную тему.')
        await state.set_state(None); return
    await message.answer('✨ Готовлю настоящий персональный урок: картинки, аудио, задания и 5 обязательных реплик для мультфильма…')
    # Reuse a cached lesson for other children, but never repeat the same variant for the same child.
    lesson, variant_id = choose_unused_variant(child.id, topic, child.target_language or 'en', child.native_language or 'ru', getattr(child,'age_years',None), child.language_level or 'PRE_A1')
    if lesson is None:
        lesson=await build_free_topic_lesson(topic,target_language=child.target_language or 'en',native_language=child.native_language or 'ru',age=getattr(child,'age_years',None),level=child.language_level or 'PRE_A1',can_read_target=getattr(child,'can_read_target',None),slide_count=21)
        lesson, variant_id = save_variant(child.id, lesson, topic, child.target_language or 'en', child.native_language or 'ru', getattr(child,'age_years',None), child.language_level or 'PRE_A1')
    path=save_free_topic_lesson(child.id,lesson); lesson_key=f"{Path(path).stem}_{variant_id or 'local'}"
    await state.update_data(free_topic_lesson=lesson,free_topic_step=0,free_topic_path=str(path),free_topic_key=lesson_key,free_topic_payment_mode=payment_mode,free_topic_skip_busy=False,free_topic_voice_files=[],free_topic_images=[],character_id=child.active_character_id,free_topic_run=1,free_topic_cartoon_count=0,free_topic_cartoon_cost_total=0.0,free_topic_attempts={})
    await state.set_state(FreeTopicFlow.playing); await _send_free_topic_step(message,state,child)

async def _maybe_build_authored_cartoon(message: Message, state: FSMContext, child: Child, data: dict, entitlement: LessonEntitlement) -> bool:
    lesson=data.get("free_topic_lesson") or {}
    lesson_id=str(data.get("authored_lesson_id") or "")
    course_id=str(data.get("authored_course_id") or lesson.get("course_id") or "conversation")
    if course_id!="conversation" or not bool(lesson.get("make_cartoon",False)):
        return False
    # Exactly one personalized movie: after the first completed run only.
    if int(entitlement.completed_runs or 0)!=1 or bool(entitlement.cartoon_generated):
        return False
    voices=[Path(x) for x in (data.get("free_topic_voice_files") or []) if x and Path(x).exists()]
    images=[Path(x) for x in (data.get("free_topic_images") or []) if x and Path(x).exists()]
    if not voices:
        await message.answer("🎬 Урок завершён, но для персонального мультфильма не нашлось сохранённой голосовой реплики. Прохождение сохранено; мультфильм не помечен созданным.")
        return False
    char=await _get_character_path(data.get("character_id") or child.active_character_id)
    if not char:
        await message.answer("🎭 Урок сохранён. Для мультфильма нужен выбранный герой; мультфильм пока не помечен созданным.")
        return False
    out=settings.storage_root/'children'/str(child.id)/'cartoons'/f'authored_{lesson_id}_first_run.mp4'
    out.parent.mkdir(parents=True,exist_ok=True)
    companion=[]
    try: friend=preset_character_path('fox')
    except Exception: friend=None
    try:
        await message.answer("🎬 Первое прохождение завершено. Собираю персональный мультфильм с твоим героем и голосом…")
        await asyncio.to_thread(build_free_topic_cartoon,images[:10],Path(char),voices[:10],companion,out,75,friend,8)
        try: add_cartoon_credit(out,out,child.display_name,getattr(child,'gender',None),target_language=child.target_language or 'ru')
        except Exception as exc: log.warning("Authored cartoon credit failed: %s",exc)
        await message.answer_video(FSInputFile(out),caption="🎉 Твой персональный мультфильм готов!")
        await mark_cartoon_generated(child.id,lesson_id,course_id)
        return True
    except Exception as exc:
        log.exception("Authored cartoon failed")
        await message.answer("🎬 Мультфильм сейчас не собрался. Голос и прохождение сохранены; система не считает мультфильм созданным, поэтому его можно повторить после исправления технической ошибки.")
        return False


async def _send_authored_parent_report(child: Child, lesson_title: str, run_no: int, data: dict) -> None:
    try:
        async with SessionLocal() as db:
            parent=await db.get(Parent,child.parent_id)
        if not parent or not parent.email_reports_enabled or not parent.email:
            return
        stats=data.get("authored_stats") or {}
        voice=int(stats.get("voice_answers",0) or 0); interactive=int(stats.get("interactive_tasks",0) or 0); retries=int(stats.get("retries",0) or 0)
        subject=f"DOME: {child.display_name} завершил(а) «{lesson_title}»"
        body=(f"{child.display_name} завершил(а) урок «{lesson_title}», прохождение {run_no}/2.\n\n"
              f"За урок: голосовых ответов — {voice}, интерактивных заданий — {interactive}, дополнительных попыток — {retries}.\n"
              "Незавершённые выходы не считались отдельным прохождением. Прогресс сохранён автоматически.")
        await send_progress_report(parent.email,subject,body)
    except Exception as exc:
        log.warning("Authored parent report failed: %s",exc)


async def _finish_free_topic(message: Message,state:FSMContext,child:Child):
    data=await state.get_data()
    if data.get("authored_mode"):
        lesson_id = str(data.get("authored_lesson_id") or "")
        if data.get("authored_preview_mode"):
            await state.set_state(None)
            await state.update_data(authored_preview_mode=False, authored_mode=False, child_id=child.id)
            await message.answer("🧪 Предпросмотр завершён. Прохождение не списано, ДЗ не выдано, мультфильм не создан.", reply_markup=child_menu_keyboard(child.native_language or "ru"))
            return
        if data.get("authored_homework_mode"):
            hid=data.get("authored_homework_assignment_id")
            async with SessionLocal() as db:
                row=await db.get(HomeworkAssignment,int(hid)) if hid else await db.scalar(select(HomeworkAssignment).where(HomeworkAssignment.child_id==child.id,HomeworkAssignment.lesson_id==lesson_id).order_by(HomeworkAssignment.id.desc()))
                if row:
                    row.status="COMPLETED"; row.completed_at=datetime.utcnow(); row.current_step=len((data.get("free_topic_lesson") or {}).get("slides") or [])
                    await db.commit()
            course_id=str(data.get("authored_course_id") or course_for_lesson(lesson_id) or "conversation")
            next_id=await _next_scheduled_lesson_id(child,course_id)
            await state.set_state(None); await state.update_data(child_id=child.id,selected_course_id=course_id)
            text="🎉 Домашнее задание выполнено!"
            if next_id and next_id != lesson_id: text += " Следующий урок уже готов в кнопке «Продолжить»."
            await message.answer(text,reply_markup=child_menu_keyboard(child.native_language or "ru"))
            return
        sid=int(data.get("authored_session_id") or 0)
        if not sid:
            await message.answer("Не удалось найти активную сессию урока; прохождение не списано.")
            return
        course_id=str(data.get("authored_course_id") or course_for_lesson(lesson_id) or "conversation")
        # Persist the final runtime snapshot before marking the session completed.
        # This preserves first-run voice/image inputs for a safe cartoon retry.
        await _persist_authored_runtime(state)
        entitlement,newly_completed=await complete_session_once(session_id=sid,child_id=child.id,lesson_id=lesson_id,course_id=course_id,final_step=len((data.get("free_topic_lesson") or {}).get("slides") or []))
        await state.update_data(authored_completed_runs=int(entitlement.completed_runs), authored_max_runs=int(entitlement.max_completed_runs))
        # Duplicate Telegram/WebApp callbacks cannot create a second homework or consume another run.
        if newly_completed:
            await _maybe_build_authored_cartoon(message,state,child,data,entitlement)
            await _send_authored_parent_report(child,str((data.get("free_topic_lesson") or {}).get("title") or lesson_id),int(entitlement.completed_runs),data)
            await _maybe_notify_course_progress(message,child,course_id)
            await _apply_pending_course_switch_after_lesson(message,child,course_id)
        homework=load_homework(lesson_id)
        if newly_completed and homework and (homework.get("slides") or []):
            async with SessionLocal() as db:
                hw=HomeworkAssignment(child_id=child.id,lesson_session_id=sid,lesson_id=lesson_id,title=str(homework.get("title") or "Домашнее задание"),body=str(homework.get("summary") or "Интерактивное домашнее задание"),duration_minutes=int(homework.get("duration_minutes",10) or 10),status="NEW",optional=bool(homework.get("optional",False)),current_step=0)
                db.add(hw); await db.commit(); await db.refresh(hw)
            try:
                async with SessionLocal() as db:
                    parent=await db.get(Parent,child.parent_id)
                if parent and parent.email_reports_enabled and parent.email and homework.get("send_to_parent_email",True):
                    src=homework.get("source_file"); attach=str(lesson_dir(lesson_id)/src) if src else None
                    await send_homework_email(parent.email,child.display_name,str((data.get("free_topic_lesson") or {}).get("title") or lesson_id),str(homework.get("summary") or "Домашнее задание после урока."),attach)
            except Exception as exc:
                log.warning("Authored homework email failed: %s",exc)
            await message.answer("🎉 Урок завершён. Сейчас открою домашнее задание.")
            await _start_authored_homework(message,state,child,lesson_id,hw.id)
            return
        await state.set_state(None); await state.update_data(child_id=child.id,selected_course_id=course_id)
        if entitlement.completed_runs>=entitlement.max_completed_runs:
            text="🎉 Урок завершён 2/2 и теперь закрыт."
        elif not newly_completed:
            text="✅ Это завершение уже было сохранено; дополнительное прохождение не списано."
        else:
            text="🎉 Урок завершён! Доступно ещё одно прохождение."
        await message.answer(text,reply_markup=child_menu_keyboard(child.native_language or "ru"))
        return
    images=[Path(x) for x in data.get('free_topic_images',[]) if x]
    voices=[Path(x) for x in data.get('free_topic_voice_files',[]) if x]
    run_no=max(1,int(data.get('free_topic_run',1)))
    cartoon_count=max(0,int(data.get('free_topic_cartoon_count',0)))
    cartoon_cost_total=float(data.get('free_topic_cartoon_cost_total',0.0) or 0.0)
    ft_cfg=load_settings('free_topic')
    estimated=float(ft_cfg.get('estimated_cartoon_cost_usd',2.5) or 2.5)
    max_two=float(ft_cfg.get('max_two_cartoons_cost_usd',5.0) or 5.0)
    make_cartoon=(run_no == 1 and cartoon_count < 1 and cartoon_cost_total + estimated <= max_two + 1e-9)
    topic=str((data.get('free_topic_lesson') or {}).get('topic') or 'the topic')

    if make_cartoon:
        await message.answer('🎬 Урок закончен. Сейчас собираю мультфильм 60–90 секунд с твоим героем и твоими записанными репликами…')
        char=await _get_character_path(data.get('character_id') or child.active_character_id)
        if not char:
            await message.answer('🎭 Не нашла выбранного героя. Урок сохранён, а мультфильм попробуем собрать после выбора героя.')
        else:
            companion=[]
            cache=settings.storage_root/'children'/str(child.id)/'free-topic-media'/str(data.get('free_topic_key'))/'tts'
            slides=(data.get('free_topic_lesson') or {}).get('slides') or []
            replies=[str(x.get('companion_reply') or '').strip() for x in slides if x.get('companion_reply')]
            if not replies:
                replies=[f"Great! Let's explore {topic} together.","Wow! Show me what you learned.","That was fun! See you next time."]
            for i,text in enumerate(replies[:4]):
                try:
                    p=await synthesize_speech(text,child.target_language or 'en',cache,f'companion_run{run_no}_{i}')
                    if p: companion.append(p)
                except Exception:
                    pass
            try:
                friend=preset_character_path('fox')
            except Exception:
                friend=None
            out=settings.storage_root/'children'/str(child.id)/'cartoons'/f"free_topic_{data.get('free_topic_key')}_run{run_no}.mp4"
            try:
                build_free_topic_cartoon(images[:8],Path(char),voices,companion,out,duration=75,companion_png=friend,first_child_scene_seconds=8)
                try: add_cartoon_credit(out,out,child.display_name,getattr(child,'gender',None),target_language=child.target_language or 'ru')
                except Exception as exc: log.warning('Free-topic credit failed: %s',exc)
                await message.answer_video(FSInputFile(out),caption=f'🎉 Твой мультфильм по теме «{topic}» готов!')
                cartoon_count += 1
                cartoon_cost_total += estimated
                await state.update_data(free_topic_cartoon_count=cartoon_count,free_topic_cartoon_cost_total=cartoon_cost_total)
            except Exception:
                log.exception('Free topic cartoon failed')
                await message.answer('🎬 Мультфильм пока не собрался. Я сохранила урок и записи; техническая ошибка записана в журнал. Текст ошибки ребёнку не показываю.')
    else:
        await message.answer('✅ Урок пройден. Для этого прохождения новый мультфильм не создаётся.')

    can_repeat=run_no < 2
    await state.set_state(None)
    await message.answer(
        f'Пройдено {run_no}/2.' + (f' Можно пройти ещё {2-run_no} раз.' if can_repeat else ' Оба прохождения завершены.'),
        reply_markup=free_topic_finished_keyboard(child.native_language or 'ru',can_repeat=can_repeat),
    )


@router.callback_query(F.data == 'freetopic:repeat')
async def free_topic_repeat(cb: CallbackQuery, state: FSMContext):
    child=await get_child_from_state_or_user(state,cb.from_user.id); data=await state.get_data()
    if not child or not data.get('free_topic_lesson'):
        await cb.answer('Сначала выбери урок на свободную тему.',show_alert=True); return
    run_no=max(1,int(data.get('free_topic_run',1)))
    if run_no>=2:
        await cb.answer('Этот урок уже пройден 2/2.',show_alert=True); return
    run_no += 1
    # The same purchased lesson is replayed; answers/voice recordings are collected anew.
    await state.update_data(free_topic_run=run_no,free_topic_step=0,free_topic_voice_files=[],free_topic_skip_busy=False,free_topic_attempts={})
    await state.set_state(FreeTopicFlow.playing)
    await cb.answer(f'Прохождение {run_no}/2')
    await _send_free_topic_step(cb.message,state,child)


def _ordered_role_turns(slide: dict, child_role: str = "") -> list[dict]:
    turns=slide.get('role_turns') or []
    cleaned=[]
    role_choice=str(child_role or slide.get('child_role') or '').strip()
    dynamic_roles=[str(x).strip() for x in (slide.get('available_roles') or []) if str(x).strip()]
    if isinstance(turns,list):
        for t in turns:
            if not isinstance(t,dict): continue
            text=str(t.get('text') or '').strip()
            if not text: continue
            role=str(t.get('role') or '').strip()
            speaker=str(t.get('speaker') or '').lower()
            # For choice-based role scenes, the selected character belongs to the
            # child; every other character is voiced by the bot. This is resolved
            # at runtime instead of hard-coding one child role in Python.
            if dynamic_roles and role_choice:
                speaker='child' if role==role_choice else 'bot'
            elif speaker not in {'child','bot'}:
                speaker='child' if role and role==role_choice else 'bot'
            cleaned.append({
                'role':role or speaker,'text':text,'speaker':speaker,
                'image_file':str(t.get('image_file') or '').strip(),
            })
    if cleaned: return cleaned
    bot_text=str(slide.get('bot_role_text') or '').strip(); child_text=str(slide.get('reading_text') or '').strip()
    if bot_text: cleaned.append({'role':'bot','text':bot_text,'speaker':'bot','image_file':''})
    if child_text: cleaned.append({'role':role_choice or 'ребёнок','text':child_text,'speaker':'child','image_file':''})
    return cleaned


async def _continue_role_reading(message: Message, state: FSMContext, child: Child, slide: dict, idx: int) -> None:
    """Play a role-reading scene in original turn order, one child turn at a time."""
    data=await state.get_data()
    if int(data.get('role_slide_step',-1)) != idx:
        cursor=0; child_read=0
        await state.update_data(
            role_slide_step=idx,role_turn_cursor=0,role_child_read=0,
            role_active_text='',role_active_role='',role_selected_role='',role_choice_slide_step=idx,
        )
        data=await state.get_data()
    else:
        cursor=max(0,int(data.get('role_turn_cursor',0) or 0)); child_read=max(0,int(data.get('role_child_read',0) or 0))

    available=[str(x).strip() for x in (slide.get('available_roles') or []) if str(x).strip()]
    selected=str(data.get('role_selected_role') or '').strip()
    if available and selected not in available:
        rows=[[InlineKeyboardButton(text=f'🎭 {role}',callback_data=f'rolepick:{idx}:{i}')] for i,role in enumerate(available)]
        await message.answer('Выбери, за какого персонажа ты будешь читать:',reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await state.set_state(FreeTopicFlow.waiting_answer)
        return

    turns=_ordered_role_turns(slide,selected)
    if not turns:
        await message.answer('На этом слайде не заданы реплики по ролям.'); return
    share=max(0.2,min(0.9,float(data.get('reading_child_share',0.6) or 0.6)))
    total_child=sum(1 for t in turns if t['speaker']=='child')
    import math
    target_child=max(1,math.ceil(total_child*share)) if total_child else 0
    child_index_before=sum(1 for t in turns[:cursor] if t['speaker']=='child')
    lesson_lang=str((data.get('free_topic_lesson') or {}).get('target_language') or child.target_language or 'ru')
    cache=settings.storage_root/'children'/str(child.id)/'free-topic-media'/str(data.get('free_topic_key'))/'role-tts'
    lesson_root=Path(str(data.get('authored_lesson_dir') or ''))
    while cursor < len(turns):
        turn=turns[cursor]
        # A dialogue may span several original pages. Show the page belonging to
        # the current turn before that turn, so the child never reads unseen text.
        if cursor > 0 and turn.get('image_file') and lesson_root:
            turn_image=lesson_root/str(turn['image_file'])
            if turn_image.exists():
                try: await message.answer_photo(FSInputFile(turn_image),caption=f"🎭 {turn['role']}")
                except Exception: pass
        child_turn_index=child_index_before
        if turn['speaker']=='child': child_index_before += 1
        should_child=turn['speaker']=='child' and child_turn_index < target_child
        if should_child:
            await state.update_data(role_slide_step=idx,role_turn_cursor=cursor,role_child_read=child_read,role_active_text=turn['text'],role_active_role=turn['role'])
            await _persist_authored_runtime(state)
            await message.answer(f"🎭 Твоя роль — {turn['role']}. Прочитай только эту реплику:\n\n{turn['text']}",reply_markup=free_topic_step_keyboard(child.native_language or 'ru',expects_answer=True,can_skip=False,step=idx))
            await state.set_state(FreeTopicFlow.waiting_answer)
            return
        # Bot reads both its own roles and the child turns it takes over adaptively.
        label='🤖 '+turn['role']
        await message.answer(f"{label}: {turn['text']}")
        try:
            sample=await synthesize_speech(turn['text'],lesson_lang,cache,f'role_{idx+1}_{cursor+1}')
            if sample: await message.answer_voice(FSInputFile(sample))
        except Exception: pass
        cursor += 1
        await state.update_data(role_turn_cursor=cursor)
        await _persist_authored_runtime(state)
    # Scene is complete; only now advance the slide.
    await state.update_data(
        role_slide_step=-1,role_turn_cursor=0,role_child_read=0,role_active_text='',role_active_role='',
        role_selected_role='',role_choice_slide_step=-1,free_topic_step=idx+1,
    )
    await _persist_authored_step(state,idx+1)
    await _send_free_topic_step(message,state,child)


@router.callback_query(F.data.startswith('rolepick:'))
async def authored_role_pick(cb: CallbackQuery, state: FSMContext):
    child=await get_child_from_state_or_user(state,cb.from_user.id)
    if child is None:
        await cb.answer('/start',show_alert=True); return
    try:
        _,idx_raw,role_idx_raw=cb.data.split(':',2); idx=int(idx_raw); role_idx=int(role_idx_raw)
    except Exception:
        await cb.answer('Некорректный выбор.',show_alert=True); return
    data=await state.get_data(); lesson=data.get('free_topic_lesson') or {}; slides=lesson.get('slides') or []
    current=int(data.get('free_topic_step',0) or 0)
    if idx!=current or idx<0 or idx>=len(slides) or str(slides[idx].get('type'))!='read_roles':
        await cb.answer('Эта сцена уже изменилась.',show_alert=True); return
    roles=[str(x).strip() for x in (slides[idx].get('available_roles') or []) if str(x).strip()]
    if role_idx<0 or role_idx>=len(roles):
        await cb.answer('Роль не найдена.',show_alert=True); return
    await state.update_data(role_selected_role=roles[role_idx],role_choice_slide_step=idx)
    await _persist_authored_runtime(state)
    try: await cb.message.edit_reply_markup(reply_markup=None)
    except Exception: pass
    await cb.answer(f'Твоя роль: {roles[role_idx]}')
    await _continue_role_reading(cb.message,state,child,slides[idx],idx)


async def _send_teacher_voice(message: Message, child: Child, data: dict, text: str, purpose: str, *, language: str | None = None) -> None:
    """Speak short pedagogical feedback aloud; text is still sent separately for accessibility."""
    text=str(text or '').strip()
    if not text:
        return
    lang=str(language or (data.get('free_topic_lesson') or {}).get('target_language') or child.target_language or 'ru')
    try:
        cache=settings.storage_root/'children'/str(child.id)/'free-topic-media'/str(data.get('free_topic_key') or 'lesson')/'teacher-feedback'
        audio=await synthesize_speech(text,lang,cache,purpose)
        if audio:
            await message.answer_voice(FSInputFile(audio))
    except Exception as exc:
        logging.getLogger('dome.speech').warning('Teacher feedback TTS unavailable purpose=%s: %s',purpose,exc)


async def _send_free_topic_step(message: Message, state: FSMContext, child: Child):
    data=await state.get_data(); lesson=data.get('free_topic_lesson') or {}; slides=lesson.get('slides') or []; idx=int(data.get('free_topic_step',0))
    # Imported lessons may fold several source pages into one multi-turn activity.
    # Such source pages stay in the manifest for traceability/preview but are skipped
    # in child runtime instead of being shown twice.
    while idx < len(slides) and bool(slides[idx].get('skip_in_runtime')):
        idx += 1
        await state.update_data(free_topic_step=idx)
        await _persist_authored_step(state,idx)
        data=await state.get_data()
    if idx>=len(slides): return await _finish_free_topic(message,state,child)
    if data.get('authored_mode') and data.get('lesson_started_monotonic'):
        elapsed=(time.monotonic()-float(data.get('lesson_started_monotonic')))/60.0
        pace=decide_pacing(elapsed_minutes=elapsed,step=idx,total_steps=max(1,len(slides)),target_minutes=float(lesson.get('target_duration_minutes') or 35))
        await state.update_data(pacing_mode=pace.mode,pacing_followups=pace.suggested_followups)
    s=slides[idx]; kind=canonical_content_type(s.get('type','passive')); expects=bool(s.get('expects_answer')); topic=lesson.get('topic','')
    # Real illustration for the current stage (AI generated once and cached; local illustrated card if image API fails).
    image=None
    if data.get("authored_mode") and s.get("image_file"):
        candidate=Path(str(data.get("authored_lesson_dir") or ""))/str(s.get("image_file"))
        if candidate.exists(): image=candidate
    if image is None:
        try:
            image=await ensure_free_topic_image(child.id,str(data.get('free_topic_key')),s,str(topic))
        except Exception: pass
    if image:
        imgs=list(data.get('free_topic_images',[]));
        if str(image) not in imgs: imgs.append(str(image)); await state.update_data(free_topic_images=imgs)
    caption=f"{idx+1}/{len(slides)} · {s.get('title','')}\n\n{s.get('prompt') or s.get('teacher_instruction') or ''}"
    authored_image_url=''
    if data.get('authored_mode') and s.get('image_file') and settings.effective_webapp_base_url:
        authored_image_url=settings.effective_webapp_base_url+f"/lesson-media/{data.get('authored_lesson_id')}/{str(s.get('image_file')).lstrip('/')}"
    lesson_lang=str(lesson.get('target_language') or child.target_language or 'en')
    support=str(s.get('support_text') or '').strip()
    if support: caption += f"\n\n💬 {support}"
    # Narrate every stage aloud.
    try:
        audio=await synthesize_speech(str(s.get('audio_text') or s.get('prompt') or s.get('title') or ''),lesson_lang,settings.storage_root/'children'/str(child.id)/'free-topic-media'/str(data.get('free_topic_key'))/'tts',f'slide_{idx+1}')
        if audio: await message.answer_voice(FSInputFile(audio))
        if support and (child.native_language or 'ru') != (child.target_language or 'en'):
            native_audio=await synthesize_speech(support,child.native_language or 'ru',settings.storage_root/'children'/str(child.id)/'free-topic-media'/str(data.get('free_topic_key'))/'tts-native',f'native_{idx+1}')
            if native_audio: await message.answer_voice(FSInputFile(native_audio))
    except Exception as exc:
        logging.getLogger("dome.speech").warning("Authored slide narration unavailable lesson=%s step=%s: %s", data.get("authored_lesson_id"), idx, exc)
        if not data.get("voice_unavailable_notified"):
            await message.answer("🔊 Голосовое сопровождение временно недоступно. Можно продолжить урок по тексту; после восстановления голос включится автоматически.")
            await state.update_data(voice_unavailable_notified=True)
    can_skip=bool(s.get('can_skip',True)) and not bool(s.get('required_cartoon_line'))
    if kind in {'choice','mini_game'} and s.get('options'):
        if image: await message.answer_photo(FSInputFile(image),caption=caption,reply_markup=free_topic_choice_keyboard(idx,list(s.get('options') or []),child.native_language or 'ru',can_skip))
        else: await message.answer(caption,reply_markup=free_topic_choice_keyboard(idx,list(s.get('options') or []),child.native_language or 'ru',can_skip))
        await state.set_state(FreeTopicFlow.waiting_answer)
    elif kind in {'letter_path','trace','handwriting_screen','draw','drawing','coloring','maze','dictation','connect_lines','tap_sound','tap_select','multi_select','listen_choose','odd_one_out','find_in_text','match_visible','matching','sorting','sequence','word_builder','syllable_builder','sentence_builder','fill_gap','sound_position','syllable_split','interactive_scene'}:
        base=settings.effective_webapp_base_url
        if base:
            canonical={'handwriting_screen':'trace','draw':'trace','drawing':'trace','matching':'match_visible'}.get(kind,kind)
            params={'type':canonical,'payload':'free_topic_task','title':str(s.get('title','')),'prompt':str(s.get('prompt','')),'audio_text':str(s.get('audio_text') or s.get('prompt') or ''),'step':idx,'image':authored_image_url,'instance':str(data.get('authored_session_id') or ('hw:'+str(data.get('authored_homework_assignment_id') or '')) or data.get('free_topic_key') or '')}
            if canonical=='letter_path':
                params.update({'letter':str(s.get('letter') or 'А'),'count':str(int(s.get('count') or 6)),'success_text':str(s.get('success_text') or 'Готово!')})
            elif canonical=='tap_sound':
                params.update({'hotspots':json.dumps(s.get('hotspots') or [],ensure_ascii=False,separators=(',',':')),'min_taps':str(int(s.get('min_taps') or 3))})
            elif canonical=='match_visible':
                params.update({'pairs':json.dumps(s.get('pairs') or [],ensure_ascii=False,separators=(',',':'))})
            elif canonical in {'interactive_scene'}:
                params.update({'hotspots':json.dumps(s.get('hotspots') or [],ensure_ascii=False,separators=(',',':')),'min_taps':str(int(s.get('min_taps') or 1))})
            elif canonical=='connect_lines':
                params.update({'left_points':json.dumps(s.get('left_points') or [],ensure_ascii=False,separators=(',',':')),'right_points':json.dumps(s.get('right_points') or [],ensure_ascii=False,separators=(',',':'))})
            elif canonical=='dictation':
                params.update({'dictation_text':str(s.get('dictation_text') or s.get('audio_text') or '')})
            elif canonical=='maze':
                params.update({'start_point':json.dumps(s.get('start_point') or {},ensure_ascii=False,separators=(',',':')),'end_point':json.dumps(s.get('end_point') or {},ensure_ascii=False,separators=(',',':'))})
            elif canonical=='trace':
                params.update({
                    'trace_regions':json.dumps(s.get('trace_regions') or [],ensure_ascii=False,separators=(',',':')),
                    'trace_checkpoints':json.dumps(s.get('trace_checkpoints') or [],ensure_ascii=False,separators=(',',':')),
                    'trace_min_coverage':str(float(s.get('trace_min_coverage') or 0.68)),
                    'trace_max_outside':str(float(s.get('trace_max_outside') or 0.18)),
                    'trace_max_scribble':str(float(s.get('trace_max_scribble') or 7.5)),
                })
            if canonical in {'tap_select','multi_select','listen_choose','odd_one_out','find_in_text','sorting','sequence','word_builder','syllable_builder','sentence_builder','fill_gap','sound_position','syllable_split','interactive_scene'}:
                params.update({'items':'|'.join(str(x) for x in (s.get('items') or s.get('options') or [])),'targets':'|'.join(str(x) for x in (s.get('targets') or [])),'correct_indices':','.join(str(x) for x in (s.get('correct_indices') or []))})
            url=base+'/free-topic-task?'+urlencode(params)
            labels={'letter_path':'🔤 Открыть дорожку','trace':'✏️ Писать пальчиком','tap_sound':'🔊 Нажать на картинки','match_visible':'🧩 Найти пары'}
            if image: await message.answer_photo(FSInputFile(image),caption=caption,reply_markup=free_topic_webapp_keyboard(labels.get(kind,'🎮 Открыть задание'),url,child.native_language or 'ru'))
            else: await message.answer(caption,reply_markup=free_topic_webapp_keyboard(labels.get(kind,'🎮 Открыть задание'),url,child.native_language or 'ru'))
            await state.set_state(FreeTopicFlow.waiting_answer)
        else:
            await message.answer('Для интерактивного задания нужен Mini App URL.'); await state.set_state(FreeTopicFlow.waiting_answer)
    elif kind=='visual_pack':
        base=settings.effective_webapp_base_url
        items=[str(x) for x in (s.get('items') or [])][:6]
        correct=[int(x) for x in (s.get('correct_indices') or []) if str(x).isdigit()]
        item_urls=[]
        if base:
            for j,label in enumerate(items):
                try:
                    ip=await ensure_free_topic_item_image(child.id,str(data.get('free_topic_key')),label,str(topic),j)
                    item_urls.append(base+f"/free-topic-media/{child.id}/{data.get('free_topic_key')}/{Path(ip).name}")
                except Exception:
                    item_urls.append('')
            image_url=(base+f"/free-topic-media/{child.id}/{data.get('free_topic_key')}/{Path(image).name}") if image else ''
            url=base+'/free-topic-task?'+urlencode({'type':'visual_pack','title':str(s.get('title','')),'prompt':str(s.get('prompt','')),'items':'|'.join(items),'item_images':'|'.join(item_urls),'correct_indices':','.join(map(str,correct)),'step':idx,'image':image_url})
            if image: await message.answer_photo(FSInputFile(image),caption=caption,reply_markup=free_topic_webapp_keyboard('🧳 Открыть задание',url,child.native_language or 'ru'))
            else: await message.answer(caption,reply_markup=free_topic_webapp_keyboard('🧳 Открыть задание',url,child.native_language or 'ru'))
            await state.set_state(FreeTopicFlow.waiting_answer)
        else:
            await message.answer('Открой задание на компьютере/в Mini App.'); await state.set_state(FreeTopicFlow.waiting_answer)
    elif kind in {'drag_drop','memory'}:
        base=settings.effective_webapp_base_url
        items=[str(x) for x in (s.get('items') or [])]; targets=[str(x) for x in (s.get('targets') or [])]
        if base:
            image_url=authored_image_url if data.get('authored_mode') else ((base+f"/free-topic-media/{child.id}/{data.get('free_topic_key')}/{Path(image).name}") if image else '')
            instance=str(data.get('authored_session_id') or ('hw:'+str(data.get('authored_homework_assignment_id') or '')) or data.get('free_topic_key') or '')
            url=base+'/free-topic-task?'+urlencode({'type':kind,'title':str(s.get('title','')),'prompt':str(s.get('prompt','')),'audio_text':str(s.get('audio_text') or s.get('prompt') or ''),'items':'|'.join(items),'targets':'|'.join(targets),'drop_zones':json.dumps(s.get('drop_zones') or [],ensure_ascii=False,separators=(',',':')),'step':idx,'image':image_url,'instance':instance})
            label='🧩 Открыть задание' if kind=='drag_drop' else '🃏 Открыть Memory'
            if image: await message.answer_photo(FSInputFile(image),caption=caption,reply_markup=free_topic_webapp_keyboard(label,url,child.native_language or 'ru'))
            else: await message.answer(caption,reply_markup=free_topic_webapp_keyboard(label,url,child.native_language or 'ru'))
            await state.set_state(FreeTopicFlow.waiting_answer)
        else:
            await message.answer_photo(FSInputFile(image),caption=caption) if image else await message.answer(caption)
            await state.set_state(FreeTopicFlow.waiting_answer)
    elif kind=='video_pause_question' and settings.effective_webapp_base_url and (s.get('video_url') or (data.get("authored_mode") and s.get("video_file"))):
        source=str(s.get('video_url') or '')
        if not source and s.get('video_file'):
            source=settings.effective_webapp_base_url+f"/lesson-media/{data.get('authored_lesson_id')}/{str(s.get('video_file')).lstrip('/')}"
        url=settings.effective_webapp_base_url+'/video-lesson?'+urlencode({
            'url':source,'title':str(s.get('title') or ''),'pause_at':str(float(s.get('pause_at_seconds') or 0)),
            'question':str(s.get('question') or s.get('prompt') or ''),'options':json.dumps(s.get('options') or [],ensure_ascii=False),
            'correct_indices':','.join(str(x) for x in (s.get('correct_indices') or ([s.get('correct_option_index')] if s.get('correct_option_index') is not None else []))),'step':str(idx)
        })
        if image: await message.answer_photo(FSInputFile(image),caption=caption,reply_markup=free_topic_webapp_keyboard('▶️ Смотреть и отвечать',url,child.native_language or 'ru'))
        else: await message.answer(caption,reply_markup=free_topic_webapp_keyboard('▶️ Смотреть и отвечать',url,child.native_language or 'ru'))
        await state.set_state(FreeTopicFlow.waiting_answer)
    elif kind in {'video','video_pause_question'} and s.get('video_url'):
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='▶️ Смотреть мультфильм',url=str(s.get('video_url')))], [InlineKeyboardButton(text=('➡️ Дальше' if (child.native_language or 'ru')=='ru' else '➡️ Next'),callback_data=f'freetopic:next:{idx}')]])
        if image: await message.answer_photo(FSInputFile(image),caption=caption,reply_markup=kb)
        else: await message.answer(caption,reply_markup=kb)
        await state.set_state(FreeTopicFlow.playing)
    elif kind=='video' and data.get("authored_mode") and s.get("video_file"):
        video_path=Path(str(data.get("authored_lesson_dir") or ""))/str(s.get("video_file"))
        if video_path.exists():
            await message.answer_video(FSInputFile(video_path),caption=caption,reply_markup=free_topic_step_keyboard(child.native_language or 'ru',expects_answer=False,can_skip=can_skip,step=idx))
        else:
            await message.answer(caption+"\n\n⚠️ Видео пока не загружено.",reply_markup=free_topic_step_keyboard(child.native_language or 'ru',expects_answer=False,can_skip=can_skip,step=idx))
        await state.set_state(FreeTopicFlow.playing)
    elif kind in {'video','video_pause_question'} and image:
        clip=settings.storage_root/'children'/str(child.id)/'free-topic-media'/str(data.get('free_topic_key'))/f'video_{idx+1}.mp4'
        made=ensure_free_topic_clip(Path(image),clip,seconds=6)
        if made:
            await message.answer_video(FSInputFile(made),caption=caption,reply_markup=free_topic_step_keyboard(child.native_language or 'ru',expects_answer=False,can_skip=can_skip,step=idx))
        else:
            await message.answer_photo(FSInputFile(image),caption=caption,reply_markup=free_topic_step_keyboard(child.native_language or 'ru',expects_answer=False,can_skip=can_skip,step=idx))
        await state.set_state(FreeTopicFlow.playing)
    elif kind in {'photo_task','real_world_find'}:
        if image: await message.answer_photo(FSInputFile(image),caption=caption)
        else: await message.answer(caption)
        hint='📷 Пришли фотографию по заданию.' if kind=='photo_task' else '🔎 Найди предмет вокруг себя. Можно прислать фото или коротко рассказать голосом.'
        await message.answer(hint,reply_markup=free_topic_step_keyboard(child.native_language or 'ru',expects_answer=True,can_skip=can_skip,step=idx))
        await state.set_state(FreeTopicFlow.waiting_answer)
    elif kind in {'voice_answer','roleplay','speak','repeat','dialogue','read_aloud','read_roles','echo_reading','shared_reading','comprehension','retell','continue_story'}:
        if image: await message.answer_photo(FSInputFile(image),caption=caption)
        else: await message.answer(caption)
        target_phrase=str(s.get('target_phrase') or '').strip()
        if kind=='read_roles':
            await message.answer('🎭 Читаем по ролям по очереди. Если станет трудно, я автоматически возьму больше реплик на себя.')
            await _continue_role_reading(message,state,child,s,idx)
            return
        extra=(f'🎬 Обязательная реплика для мультфильма. Скажи на изучаемом языке:\n«{target_phrase}»\nЗапиши голосом до 5 секунд.' if s.get('required_cartoon_line') else ('📖 Читай вслух. Я буду слушать и помогу только когда это действительно нужно.' if kind in {'read_aloud','read_roles','echo_reading','shared_reading'} else '🎙 Ответь голосом.'))
        await message.answer(extra,reply_markup=free_topic_step_keyboard(child.native_language or 'ru',expects_answer=True,can_skip=can_skip,step=idx))
        if target_phrase:
            try:
                sample=await synthesize_speech(target_phrase,lesson_lang,settings.storage_root/'children'/str(child.id)/'free-topic-media'/str(data.get('free_topic_key'))/'tts',f'cartoon_line_{idx+1}')
                if sample: await message.answer_voice(FSInputFile(sample))
            except Exception: pass
        await state.set_state(FreeTopicFlow.waiting_answer)
    else:
        if image: await message.answer_photo(FSInputFile(image),caption=caption,reply_markup=free_topic_step_keyboard(child.native_language or 'ru',expects_answer=expects,can_skip=can_skip,step=idx))
        else: await message.answer(caption,reply_markup=free_topic_step_keyboard(child.native_language or 'ru',expects_answer=expects,can_skip=can_skip,step=idx))
        await state.set_state(FreeTopicFlow.waiting_answer if expects else FreeTopicFlow.playing)
    await state.update_data(free_topic_skip_busy=False)

@router.callback_query(FreeTopicFlow.payment, F.data == 'freepay:bypass')
async def free_topic_payment_bypass(cb: CallbackQuery, state: FSMContext):
    child=await get_child_from_state_or_user(state,cb.from_user.id)
    if not child: await cb.answer('/start',show_alert=True); return
    await cb.answer('Тестовая оплата пропущена'); await _start_free_topic(cb.message,state,child,payment_mode='TEST_BYPASS')

@router.callback_query(FreeTopicFlow.payment, F.data == 'freepay:pay')
async def free_topic_payment_pay(cb: CallbackQuery, state: FSMContext):
    child=await get_child_from_state_or_user(state,cb.from_user.id)
    if not child: await cb.answer('/start',show_alert=True); return
    await state.update_data(consent_return='free_topic_payment'); await cb.message.answer('💳 Рабочая схема: карта сохраняется один раз у платёжного провайдера, следующие пакеты подтверждаются SMS-кодом. Пока для теста можно пропустить оплату.'); await cb.answer()

@router.callback_query(F.data.startswith('freetopic:next'))
async def free_topic_next(cb: CallbackQuery, state: FSMContext):
    child=await get_child_from_state_or_user(state,cb.from_user.id); data=await state.get_data()
    if not child: await cb.answer('/start',show_alert=True); return
    slides=(data.get('free_topic_lesson') or {}).get('slides') or []; idx=int(data.get('free_topic_step',0))
    parts=cb.data.split(':')
    if len(parts)>2 and parts[2].isdigit() and int(parts[2])!=idx:
        await cb.answer('Это кнопка предыдущего задания.'); return
    if idx<len(slides) and slides[idx].get('expects_answer'): await cb.answer('Сначала выполни задание.',show_alert=True); return
    await state.update_data(free_topic_step=idx+1); await _persist_authored_step(state,idx+1); await cb.answer(); await _send_free_topic_step(cb.message,state,child)

@router.callback_query(F.data.startswith('freetopic:choice:'))
async def free_topic_choice(cb:CallbackQuery,state:FSMContext):
    child=await get_child_from_state_or_user(state,cb.from_user.id); data=await state.get_data()
    if not child: return
    parts=cb.data.split(':'); step=int(parts[2]); choice_idx=int(parts[3]); idx=int(data.get('free_topic_step',0))
    if step!=idx: await cb.answer('Это уже предыдущее задание.'); return
    slides=(data.get('free_topic_lesson') or {}).get('slides') or []; s=slides[idx] if idx<len(slides) else {}
    correct_raw=s.get('correct_indices') or ([s.get('correct_option_index')] if s.get('correct_option_index') is not None else [])
    correct={int(x) for x in correct_raw if str(x).lstrip('-').isdigit()}
    attempts=dict(data.get('free_topic_attempts') or {}); attempt=int(attempts.get(str(idx),0))+1; attempts[str(idx)]=attempt
    await state.update_data(free_topic_attempts=attempts)
    if correct and choice_idx not in correct and attempt<2:
        await cb.answer('Попробуй ещё раз.',show_alert=True); return
    stats=dict(data.get('authored_stats') or {}); stats['interactive_tasks']=int(stats.get('interactive_tasks',0) or 0)+1
    if attempt>1: stats['retries']=int(stats.get('retries',0) or 0)+1
    await cb.answer('✅'); await state.update_data(free_topic_step=idx+1,authored_stats=stats); await _persist_authored_step(state,idx+1); await _send_free_topic_step(cb.message,state,child)

@router.callback_query(F.data.startswith('freetopic:skip'))
async def free_topic_skip(cb: CallbackQuery, state: FSMContext):
    data=await state.get_data(); child=await get_child_from_state_or_user(state,cb.from_user.id)
    if not child: await cb.answer('/start',show_alert=True); return
    if data.get('free_topic_skip_busy'): await cb.answer('Уже пропускаю это задание…'); return
    slides=(data.get('free_topic_lesson') or {}).get('slides') or []; idx=int(data.get('free_topic_step',0))
    parts=cb.data.split(':')
    if len(parts)>2 and parts[2].isdigit() and int(parts[2])!=idx:
        await cb.answer('Это кнопка предыдущего задания.'); return
    await state.update_data(free_topic_skip_busy=True)
    if idx>=len(slides): await state.update_data(free_topic_skip_busy=False); return
    s=slides[idx]
    if s.get('required_cartoon_line') or not s.get('can_skip',True):
        await state.update_data(free_topic_skip_busy=False)
        text=('Это обязательная реплика для мультфильма — её нужно записать.' if s.get('required_cartoon_line') else 'Это важное задание урока. Я помогу выполнить его, но пропускать его нельзя.')
        await cb.answer(text,show_alert=True); return
    # Advance exactly once. Old/stale skip buttons cannot skip a later step because skip_busy stays set until next screen is rendered.
    await cb.answer('Пропускаю одно задание'); await state.update_data(free_topic_step=idx+1); await _persist_authored_step(state,idx+1); await _send_free_topic_step(cb.message,state,child)

@router.message(FreeTopicFlow.waiting_answer, ~F.web_app_data)
async def free_topic_answer(message: Message, state: FSMContext):
    child=await get_child_from_state_or_user(state,message.from_user.id); data=await state.get_data()
    if not child: return
    slides=(data.get('free_topic_lesson') or {}).get('slides') or []; idx=int(data.get('free_topic_step',0))
    if idx>=len(slides): return
    s=slides[idx]; kind=str(s.get('type') or '')
    if kind in {'drawing','photo_task','real_world_find'}:
        if kind=='real_world_find' and message.voice:
            pass  # continue into speech assessment below
        else:
            if not (message.photo or message.document):
                await message.answer('📷 Пришли фото/изображение по заданию.' if kind!='drawing' else '🎨 Пришли фото рисунка или изображение.'); return
            await message.answer('✅ Получено!')
            await state.update_data(free_topic_step=idx+1); await _persist_authored_step(state,idx+1)
            await _send_free_topic_step(message,state,child); return
    if kind not in {'voice_answer','roleplay','speak','repeat','dialogue','read_aloud','read_roles','echo_reading','shared_reading','comprehension','retell','continue_story','real_world_find'}:
        await message.answer('Выполни текущее задание кнопкой или в мини-приложении.'); return
    if not message.voice:
        await message.answer('🎙 Ответь голосовым сообщением на изучаемом языке.'); return
    if s.get('required_cartoon_line') and message.voice.duration and message.voice.duration>6:
        await message.answer('Реплика слишком длинная. Запиши коротко — до 5 секунд.'); return

    rootv=settings.storage_root/'children'/str(child.id)/'free-topic-media'/str(data.get('free_topic_key'))/'child-voice'
    rootv.mkdir(parents=True,exist_ok=True)
    raw=rootv/f'run{int(data.get("free_topic_run",1))}_step{idx+1}_attempt.ogg'
    wav=rootv/f'run{int(data.get("free_topic_run",1))}_step{idx+1}_attempt.wav'
    await message.bot.download(message.voice,destination=raw)
    try:
        prepare_child_voice(raw,wav,max_seconds=5 if s.get('required_cartoon_line') else None)
    except Exception:
        await message.answer('Не получилось обработать запись. Попробуй записать ещё раз.'); return

    attempts=dict(data.get('free_topic_attempts') or {})
    attempt_key=(f"{idx}:role:{int(data.get('role_turn_cursor',0) or 0)}" if kind=='read_roles' else str(idx))
    attempt=int(attempts.get(attempt_key,0))+1
    attempts[attempt_key]=attempt
    await state.update_data(free_topic_attempts=attempts)
    reading_kind = kind in {'read_aloud','read_roles','echo_reading','shared_reading'}
    goal=(str(data.get('role_active_text') or s.get('reading_text') or '').strip() if kind=='read_roles' else (str(s.get('reading_text') or '').strip() if reading_kind else str(s.get('target_phrase') or s.get('prompt') or s.get('teacher_instruction') or '')))
    accepted=list(s.get('accepted_meaning') or ([goal] if goal else []))
    lesson_lang=str((data.get('free_topic_lesson') or {}).get('target_language') or child.target_language or 'en')
    assessment=await assess_speech(
        wav, lesson_lang, child.native_language or 'ru', goal, accepted, attempt,
        child_name=child.display_name or '', working_difficulty=0.35,
    )
    status=assessment.status
    if kind=='read_roles':
        stats=dict(data.get('authored_stats') or {})
        if attempt>1: stats['retries']=int(stats.get('retries',0) or 0)+1
        if status in {'TECHNICAL_UNCERTAINTY','WRONG_LANGUAGE','RETRY_REQUIRED'}:
            if attempt < 2:
                corrected=(assessment.corrected_target or goal).strip()
                await state.update_data(authored_stats=stats)
                await _persist_authored_runtime(state)
                feedback_text=('Я не расслышала реплику. Попробуй ещё раз.' if status=='TECHNICAL_UNCERTAINTY' else 'Попробуй прочитать эту реплику ещё раз.') + (f' Можно послушать/прочитать: «{corrected}»' if corrected else '')
                await message.answer(feedback_text)
                await _send_teacher_voice(message,child,data,feedback_text,f'role_retry_{idx+1}_{attempt}',language=child.native_language or 'ru')
                return
            # After two unsuccessful attempts the bot takes only THIS turn, not the whole scene.
            share=max(0.2,float(data.get('reading_child_share',0.6) or 0.6)-0.15)
            cursor=int(data.get('role_turn_cursor',0) or 0)
            await message.answer('Я возьму эту реплику на себя, а дальше продолжим по ролям.')
            if goal:
                try:
                    sample=await synthesize_speech(goal,lesson_lang,rootv/'reading-model',f'role_help_{idx+1}_{cursor+1}')
                    if sample: await message.answer_voice(FSInputFile(sample))
                except Exception: pass
            await state.update_data(reading_child_share=share,reading_support=min(3,int(data.get('reading_support',0) or 0)+1),role_turn_cursor=cursor+1,role_active_text='',role_active_role='',authored_stats=stats)
            await _persist_authored_runtime(state)
            await _continue_role_reading(message,state,child,s,idx)
            return
        # Accepted child turn: continue from the next ordered turn without replaying the slide.
        cursor=int(data.get('role_turn_cursor',0) or 0); child_read=int(data.get('role_child_read',0) or 0)+1
        stats['voice_answers']=int(stats.get('voice_answers',0) or 0)+1
        await state.update_data(reading_child_share=min(0.9,float(data.get('reading_child_share',0.6) or 0.6)+0.05),reading_support=max(0,int(data.get('reading_support',0) or 0)-1),role_turn_cursor=cursor+1,role_child_read=child_read,role_active_text='',role_active_role='',authored_stats=stats)
        await _persist_authored_runtime(state)
        feedback_text=(assessment.response_target or assessment.response_native or '').strip() or 'Отлично, продолжаем сцену.'
        await message.answer(feedback_text)
        await _send_teacher_voice(message,child,data,feedback_text,f'role_ok_{idx+1}_{cursor+1}',language=child.native_language or 'ru')
        await _continue_role_reading(message,state,child,s,idx)
        return
    if reading_kind:
        support=int(data.get('reading_support',0) or 0)
        if status in {'TECHNICAL_UNCERTAINTY','WRONG_LANGUAGE','RETRY_REQUIRED'}:
            support=min(3,support+1); await state.update_data(reading_support=support, reading_child_share=max(0.2, float(data.get('reading_child_share',0.6) or 0.6)-0.15))
            if attempt >= 2:
                model_text=str(s.get('reading_text') or s.get('prompt') or '').strip()
                await message.answer('Давай я помогу и возьму этот кусочек на себя. Ты можешь читать дальше вместе со мной.')
                if model_text:
                    try:
                        sample=await synthesize_speech(model_text,lesson_lang,rootv/'reading-model',f'step_{idx+1}')
                        if sample: await message.answer_voice(FSInputFile(sample))
                    except Exception: pass
                await state.update_data(free_topic_step=idx+1); await _persist_authored_step(state,idx+1)
                await _send_free_topic_step(message,state,child); return
        else:
            support=max(0,support-1); await state.update_data(reading_support=support, reading_child_share=min(0.8, float(data.get('reading_child_share',0.6) or 0.6)+0.05))
    # Never accept arbitrary noise or a clear native-language answer as a target-language response.
    if status in {'TECHNICAL_UNCERTAINTY'}:
        if attempt < 3:
            await message.answer('Я не расслышала ответ. Запиши ещё раз чуть громче и короче.'); return
        await message.answer('Я всё ещё не уверена, что правильно услышала. Давай попробуем по образцу.')
        if goal: await message.answer(f'Скажи: «{goal}»')
        return
    if status in {'WRONG_LANGUAGE','RETRY_REQUIRED'} and attempt < 3:
        feedback=(assessment.feedback_native or assessment.response_native or '').strip()
        corrected=(assessment.corrected_target or goal).strip()
        if status=='WRONG_LANGUAGE':
            text='Попробуй ответить на изучаемом языке.'
        else:
            text=feedback or 'Почти. Попробуй ещё раз.'
        if corrected: text += f' Можно сказать: «{corrected}»'
        await message.answer(text)
        await _send_teacher_voice(message,child,data,text,f'speech_retry_{idx+1}_{attempt}',language=child.native_language or 'ru')
        return
    if status in {'WRONG_LANGUAGE','RETRY_REQUIRED'} and attempt >= 3:
        # Required cartoon lines still need a usable target-language recording; do not save a wrong-language/noise take.
        if s.get('required_cartoon_line'):
            corrected=(assessment.corrected_target or goal).strip()
            await message.answer('Для мультфильма нужна понятная реплика на изучаемом языке.' + (f' Скажи: «{corrected}»' if corrected else ''))
            return

    # Persist useful first-run conversation speech for the authored personalized movie,
    # not only slides explicitly flagged as required_cartoon_line.
    accepted_status=status not in {'TECHNICAL_UNCERTAINTY','WRONG_LANGUAGE','RETRY_REQUIRED'}
    if data.get('authored_mode') and not data.get('authored_homework_mode'):
        stats=dict((await state.get_data()).get('authored_stats') or {})
        if accepted_status: stats['voice_answers']=int(stats.get('voice_answers',0) or 0)+1
        if attempt>1: stats['retries']=int(stats.get('retries',0) or 0)+1
        await state.update_data(authored_stats=stats)
        if accepted_status and str(data.get('authored_course_id') or '')=='conversation' and int(data.get('free_topic_run',1) or 1)==1 and kind not in {'read_aloud','echo_reading','shared_reading'} and (not message.voice.duration or message.voice.duration<=8):
            final_auto=rootv/f'run1_voice_{idx+1}.ogg'
            try: final_auto.write_bytes(raw.read_bytes())
            except Exception: final_auto=raw
            voices=list((await state.get_data()).get('free_topic_voice_files') or [])
            if str(final_auto) not in voices: voices.append(str(final_auto))
            await state.update_data(free_topic_voice_files=voices)
            await _persist_authored_runtime(state)

    if s.get('required_cartoon_line'):
        final=rootv/f'run{int(data.get("free_topic_run",1))}_line_{idx+1}.ogg' 
        try:
            final.write_bytes(raw.read_bytes())
        except Exception:
            final=raw
        voices=list(data.get('free_topic_voice_files',[])); voices.append(str(final))
        await state.update_data(free_topic_voice_files=voices)
        await message.answer('✅ Реплика сохранена для мультфильма.')
    else:
        response=(assessment.response_target or assessment.response_native or '').strip()
        feedback_text=response or 'Хорошо, идём дальше.'
        await message.answer(feedback_text)
        await _send_teacher_voice(message,child,data,feedback_text,f'speech_ok_{idx+1}_{attempt}',language=child.native_language or 'ru')
    # Interactive answer completion advances immediately. No old Continue button is required.
    await state.update_data(free_topic_step=idx+1); await _persist_authored_step(state,idx+1)
    await _send_free_topic_step(message,state,child)


@router.callback_query(F.data == "menu:set_email")
async def menu_set_email(cb: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsFlow.parent_email)
    await cb.message.answer("Введите email родителя для отчётов о прогрессе:")
    await cb.answer()


@router.callback_query(F.data == "menu:support")
async def menu_support(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    native = child.native_language if child else 'ru'
    chat = (settings.support_chat_url or '').strip()
    call = (settings.support_call_label or '').strip()
    call_url = call if call.startswith(('http://','https://','tel:')) else (('tel:'+call) if call and '000000000' not in call else '')
    text = ("💬 Поддержка DOME\n\nВыберите удобный способ." if native=='ru' else "💬 DOME Support\n\nChoose how you would like to contact us.")
    if not chat and not call_url:
        text += ("\n\nКонтакты поддержки пока настраиваются. Можно открыть FAQ." if native=='ru' else "\n\nSupport contacts are being configured. You can open FAQ.")
    await cb.message.answer(text, reply_markup=support_keyboard(native or 'ru',chat,call_url))
    await cb.answer()

@router.callback_query(F.data == "support:faq")
async def support_faq(cb: CallbackQuery):
    await cb.message.answer("❓ Частые вопросы\n\n• Если урок остановился — откройте меню и нажмите «Продолжить урок».\n• Если не записывается голос — проверьте доступ Telegram к микрофону.\n• По вопросам оплаты используйте раздел «Оплата» в меню родителя.")
    await cb.answer()


@router.callback_query(F.data == "menu:birthday")
async def menu_birthday(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if not child:
        await cb.answer('/start', show_alert=True); return
    current=''
    if getattr(child,'birth_day',None) and getattr(child,'birth_month',None):
        current=f" Сейчас сохранено: {child.birth_day:02d}.{child.birth_month:02d}" + (f".{child.birth_year}" if getattr(child,'birth_year',None) else '')
    await state.set_state(SettingsFlow.birthday)
    await cb.message.answer("🎂 Введите дату рождения ребёнка в формате ДД.ММ.ГГГГ (например 14.05.2014)."+current+"\nБот будет поздравлять автоматически раз в год.")
    await cb.answer()

@router.message(SettingsFlow.birthday, F.text)
async def save_birthday(message: Message, state: FSMContext):
    import re as _re
    child=await get_child_from_state_or_user(state,message.from_user.id)
    m=_re.fullmatch(r'\s*(\d{1,2})[./-](\d{1,2})(?:[./-](\d{4}))?\s*',message.text or '')
    if not m:
        await message.answer("Напишите дату так: 14.05.2014"); return
    day,month,year=int(m.group(1)),int(m.group(2)),int(m.group(3) or 0)
    try:
        from datetime import date
        date(year or 2000,month,day)
    except Exception:
        await message.answer("Такой даты нет. Проверьте и введите ещё раз."); return
    async with SessionLocal() as db:
        row=await db.get(Child,child.id); row.birth_day=day; row.birth_month=month; row.birth_year=year or None
        if year:
            today=datetime.utcnow().date(); row.age_years=today.year-year-((today.month,today.day)<(month,day))
        await db.commit()
    await state.set_state(None)
    await message.answer(f"🎂 Дата рождения сохранена: {day:02d}.{month:02d}"+(f".{year}" if year else ''),reply_markup=parent_menu_keyboard(child.native_language or 'ru'))


@router.callback_query(F.data == "menu:lessons_child")
async def menu_lessons_child(cb: CallbackQuery, state: FSMContext):
    return await menu_lessons(cb, state)


@router.callback_query(F.data == "menu:progress_parent")
async def menu_progress_parent(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer('/start', show_alert=True); return
    async with SessionLocal() as db:
        completed = (await db.scalars(select(LessonSession).where(LessonSession.child_id == child.id, LessonSession.status == 'COMPLETED'))).all()
        attempts = (await db.scalars(select(VoiceAttempt).join(LessonSession, VoiceAttempt.lesson_session_id == LessonSession.id).where(LessonSession.child_id == child.id).order_by(VoiceAttempt.id.desc()))).all()
        recent = [a.transcript for a in attempts if a.transcript][:5]
    summary = (
        f"👤 Ребёнок: {child.display_name}\n"
        f"Возраст: {getattr(child,'age_years', None) or '—'}\n"
        f"Уровень: {child.language_level}\n"
        f"Завершено уроков: {len(completed)}\n\n"
        f"Навыки:\n"
        f"• понимание: {child.comprehension_score:.0%}\n"
        f"• грамматика: {child.grammar_score:.0%}\n"
        f"• словарь: {child.vocabulary_score:.0%}\n"
        f"• произношение: {child.pronunciation_score:.0%}\n"
        f"• беглость: {child.fluency_score:.0%}\n"
        f"• самостоятельность: {child.independence_score:.0%}\n\n"
        f"Последние удачные фразы/слова:\n" + ('\n'.join('• '+x for x in recent) if recent else '• пока нет данных')
    )
    await cb.message.answer(summary, reply_markup=parent_menu_keyboard(child.native_language or 'ru'))
    await cb.answer()


@router.message(Onboarding.child_name, F.text)
async def child_name(message: Message, state: FSMContext):
    parent = await get_or_create_parent(message.from_user.id, message.from_user.full_name)
    count=await family_child_count(parent.id)
    if count >= MAX_CHILDREN_PER_PARENT:
        await state.set_state(None)
        await message.answer("Можно подключить максимум 5 детей к одному родительскому аккаунту.", reply_markup=parent_menu_keyboard("ru"))
        return
    async with SessionLocal() as db:
        child = Child(parent_id=parent.id, display_name=message.text.strip())
        db.add(child)
        await db.flush()
        db_parent=await db.get(Parent,parent.id); db_parent.active_child_id=child.id
        await db.commit()
        await db.refresh(child)
    activity("child_created", tg_id=message.from_user.id, child_id=child.id, child_name=child.display_name)
    await state.update_data(child_id=child.id)
    await message.answer(tr("ru", "child_age_question"), reply_markup=age_keyboard("ru"))
    await state.set_state(Onboarding.child_age)


@router.callback_query(Onboarding.child_age, F.data.startswith("age:"))
async def child_age_pick(cb: CallbackQuery, state: FSMContext):
    age = int(cb.data.split(":", 1)[1])
    data = await state.get_data()
    async with SessionLocal() as db:
        child = await db.get(Child, data["child_id"])
        child.age_years = age
        await db.commit()
    await cb.message.edit_text("Укажи пол ребёнка — это нужно только для правильной подписи мультфильма:", reply_markup=gender_keyboard("ru"))
    await state.set_state(Onboarding.child_gender)
    await cb.answer()


@router.callback_query(Onboarding.child_gender, F.data.startswith("gender:"))
async def child_gender_pick(cb: CallbackQuery, state: FSMContext):
    gender=cb.data.split(":",1)[1]
    data=await state.get_data()
    async with SessionLocal() as db:
        child=await db.get(Child,data["child_id"]); child.gender=gender; await db.commit()
    await cb.message.edit_text(tr("ru", "native_question"), reply_markup=language_keyboard("native", "ru"))
    await state.set_state(Onboarding.native_language)
    await cb.answer()


@router.callback_query(SettingsFlow.profile_gender, F.data.startswith("gender:"))
async def existing_gender_pick(cb: CallbackQuery, state: FSMContext):
    child=await get_child_from_state_or_user(state,cb.from_user.id)
    if not child:
        await cb.answer('/start',show_alert=True); return
    gender=cb.data.split(":",1)[1]
    async with SessionLocal() as db:
        row=await db.get(Child,child.id); row.gender=gender; await db.commit()
    await state.set_state(None)
    child=await get_child_from_state_or_user(state,cb.from_user.id)
    await cb.message.answer("Сохранено.",reply_markup=menu_hub_keyboard(child.native_language or 'ru'))
    await cb.answer()




@router.callback_query(Onboarding.native_language, F.data.startswith("native:"))
async def native_language(cb: CallbackQuery, state: FSMContext):
    code = cb.data.split(":", 1)[1]
    data = await state.get_data()
    async with SessionLocal() as db:
        child = await db.get(Child, data["child_id"])
        child.native_language = code
        await db.commit()
    await cb.message.edit_text(tr(code, "target_question"), reply_markup=language_keyboard("target", code))
    await state.set_state(Onboarding.target_language)
    await cb.answer()


@router.callback_query(Onboarding.target_language, F.data.startswith("target:"))
async def target_language(cb: CallbackQuery, state: FSMContext):
    code = cb.data.split(":", 1)[1]
    data = await state.get_data()
    async with SessionLocal() as db:
        child = await db.get(Child, data["child_id"])
        child.target_language = code
        native = child.native_language or "en"
        await db.commit()
    await cb.message.edit_text("Умеет ли ребёнок читать на изучаемом языке?", reply_markup=reading_ability_keyboard(native))
    await state.set_state(Onboarding.target_reading)
    await cb.answer()


@router.callback_query(Onboarding.target_reading, F.data.startswith("reading:"))
async def onboarding_target_reading(cb: CallbackQuery, state: FSMContext):
    level=cb.data.split(":",1)[1]
    data=await state.get_data()
    async with SessionLocal() as db:
        child=await db.get(Child,data["child_id"]); child.can_read_target=(level != "no"); native=child.native_language or "ru"; await db.commit()
    await cb.message.edit_text(tr(native, "character_source"), reply_markup=character_source_keyboard(native))
    await state.set_state(Onboarding.character_choice)
    await cb.answer()

@router.callback_query(SettingsFlow.target_reading, F.data.startswith("reading:"))
async def settings_target_reading(cb: CallbackQuery, state: FSMContext):
    child=await get_child_from_state_or_user(state,cb.from_user.id)
    if not child: await cb.answer('/start',show_alert=True); return
    level=cb.data.split(":",1)[1]
    async with SessionLocal() as db:
        row=await db.get(Child,child.id); row.can_read_target=(level != "no"); await db.commit()
    await state.set_state(None)
    child=await get_child_from_state_or_user(state,cb.from_user.id)
    await cb.message.answer("Сохранила. Задания будут учитывать навык чтения.",reply_markup=menu_hub_keyboard(child.native_language or 'ru'))
    await cb.answer()


@router.callback_query(F.data == "menu:languages")
async def menu_languages(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    await cb.message.answer(tr(child.native_language, "choose_native"), reply_markup=language_keyboard("settings_native", child.native_language or "en"))
    await state.set_state(SettingsFlow.native_language)
    await cb.answer()


@router.callback_query(SettingsFlow.native_language, F.data.startswith("settings_native:"))
async def settings_native(cb: CallbackQuery, state: FSMContext):
    code = cb.data.split(":", 1)[1]
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    async with SessionLocal() as db:
        db_child = await db.get(Child, child.id)
        db_child.native_language = code
        await db.commit()
    await cb.message.edit_text(tr(code, "choose_target"), reply_markup=language_keyboard("settings_target", code))
    await state.set_state(SettingsFlow.target_language)
    await cb.answer()


@router.callback_query(SettingsFlow.target_language, F.data.startswith("settings_target:"))
async def settings_target(cb: CallbackQuery, state: FSMContext):
    code = cb.data.split(":", 1)[1]
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    async with SessionLocal() as db:
        db_child = await db.get(Child, child.id)
        db_child.target_language = code
        native = db_child.native_language or "en"
        await db.commit()
    await state.set_state(None)
    await cb.message.answer(tr(native, "languages_saved"), reply_markup=menu_hub_keyboard(native))
    await cb.answer()


@router.callback_query(F.data == "menu:payment")
async def menu_payment(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    fp=await family_price_for_child(child.parent_id,child.id,0,1)
    discount_per_lesson=0.5 if fp.child_position>1 else 0.0
    note=(f" Для этого ребёнка действует семейная скидка €{discount_per_lesson:.2f} с каждого урока." if discount_per_lesson else "")
    await cb.message.answer(("Выберите тариф. После выбора откроется безопасная страница оплаты."+note if (child.native_language or "ru") == "ru" else "Choose a plan. A secure payment page will open next."), reply_markup=payment_plans_keyboard(child.native_language or "en",additional_child_discount_per_lesson=discount_per_lesson))
    await cb.answer()



@router.callback_query(F.data == "course_payment:plans")
async def course_payment_plans(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    fp=await family_price_for_child(child.parent_id,child.id,0,1)
    discount_per_lesson=0.5 if fp.child_position>1 else 0.0
    await cb.message.answer(
        "👩 Родителю: выберите пакет для оплаты. После подтверждённой оплаты доступ к курсу открывается выбранному ребёнку." + (f"\n👨‍👩‍👧‍👦 Семейная скидка для этого ребёнка: €{discount_per_lesson:.2f} с каждого урока." if discount_per_lesson else ""),
        reply_markup=payment_plans_keyboard(child.native_language or "ru",additional_child_discount_per_lesson=discount_per_lesson),
    )
    await cb.answer()


@router.callback_query(F.data == "course_payment:test_bypass")
async def course_payment_test_bypass(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    payments = load_settings("payments")
    if not payments.get("allow_test_course_payment_bypass", False):
        await cb.answer("Тестовый пропуск оплаты выключен.", show_alert=True); return
    data = await state.get_data()
    course_id = str(data.get("pending_course_id") or first_active_course_id() or "demo_english")
    lesson_id = str(data.get("pending_lesson_id") or data.get("selected_lesson_id") or await _next_scheduled_lesson_id(child))
    async with SessionLocal() as db:
        existing = await db.scalar(select(CourseEnrollment).where(
            CourseEnrollment.child_id == child.id,
            CourseEnrollment.course_id == course_id,
            CourseEnrollment.status == "ACTIVE",
        ).order_by(CourseEnrollment.id.desc()))
        if existing is None:
            db.add(CourseEnrollment(
                child_id=child.id, course_id=course_id, status="ACTIVE",
                access_source="TEST_BYPASS", payment_reference="TEST_BYPASS",
            ))
        sub=await db.scalar(select(Subscription).where(Subscription.child_id==child.id,Subscription.course_id==course_id,Subscription.status=='ACTIVE').order_by(Subscription.id.desc()))
        if sub is None:
            pricing=load_settings('pricing'); plans=((pricing.get('regular_course') or {}).get('subscription_plans') or [])
            spec=next((x for x in plans if int(x.get('lessons_per_week',1))==1),{'id':'weekly1','lessons_per_week':1,'monthly_price':39})
            plan_id=str(spec.get('id') or 'weekly1')
            db.add(Subscription(child_id=child.id,course_id=course_id,plan_id=plan_id,current_plan_id=plan_id,lessons_per_week=int(spec.get('lessons_per_week') or 1),monthly_price=float(spec.get('monthly_price') or 39),currency=str(pricing.get('currency') or 'EUR'),status='ACTIVE',test_mode=True))
        await db.commit()
    await state.update_data(selected_lesson_id=lesson_id, current_lesson_id=lesson_id)
    await cb.message.answer("🧪 Тестовый режим: оплата пропущена. Открываю урок.")
    await _resume_or_start_lesson(cb.message, state, child)
    await cb.answer()


@router.callback_query(F.data.startswith("payment:plan:"))
async def payment_plan(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if not child:
        await cb.answer("/start", show_alert=True); return
    plan = cb.data.rsplit(":", 1)[1]
    await state.update_data(pending_payment_plan=plan, consent_return="payment")
    if not await _has_consent(child.parent_id, child.id, "PAYMENT", settings.payment_consent_version):
        await _request_consent(cb.message, child, "PAYMENT")
        await cb.answer(); return
    await _show_checkout(cb.message, state, child, plan)
    await cb.answer()

async def _show_checkout(message: Message, state: FSMContext, child: Child, plan: str) -> None:
    pricing=load_settings("pricing"); data=await state.get_data(); course_id=str(data.get("pending_course_id") or first_active_course_id() or "conversation"); plans={str(p.get("id")):p for p in subscription_plans_for_course(course_id)}
    spec=plans.get(plan,{})
    url=settings.payment_url
    base_price=float(spec.get('monthly_price',0))
    freq=int(spec.get('lessons_per_week',1))
    fp=await family_price_for_child(child.parent_id,child.id,base_price,freq)
    effective_price=fp.effective_price
    names={}
    for pid,p in plans.items():
        pfreq=int(p.get('lessons_per_week',1)); pfp=await family_price_for_child(child.parent_id,child.id,float(p.get('monthly_price',0)),pfreq)
        names[pid]=f"{pfreq}×/нед · €{pfp.effective_price:g}/мес"

    payments=load_settings('payments'); provider=str(payments.get('provider') or settings.payment_provider or 'custom').lower()
    base=settings.effective_webapp_base_url or 'https://t.me'
    currency=str(pricing.get('currency') or 'EUR')
    idem=f"dome:{provider}:{child.id}:{course_id}:{plan}:{int(time.time()//600)}"
    switch_from=str(data.get('course_switch_from') or '')
    async with SessionLocal() as db:
        q=select(Subscription).where(Subscription.child_id==child.id,Subscription.status=='ACTIVE',Subscription.test_mode==False)
        q=q.where(Subscription.course_id==(switch_from or course_id))
        active_paid=await db.scalar(q.order_by(Subscription.id.desc()))
    if active_paid is not None:
        if str(active_paid.plan_id)==str(plan) and int(active_paid.lessons_per_week or 1)==freq:
            await message.answer(f"✅ У {child.display_name} уже активен этот тариф: {names.get(plan,plan)}.")
            return
        try:
            from app.services.subscription_plan_changes import preview_plan_change
            async with SessionLocal() as db:
                stored=await db.get(Subscription,active_paid.id)
                preview=await preview_plan_change(db,stored,parent_id=child.parent_id,requested_plan_id=plan)
            date=preview.effective_at.strftime('%d.%m.%Y')
            await state.update_data(pending_payment_plan_confirmation=plan,pending_course_id=course_id)
            await message.answer(
                "Новый тариф начнёт действовать со следующего оплачиваемого периода.\n"
                "До этой даты действует ваш текущий тариф.\n\n"
                f"Текущий тариф: {preview.current.title}\n"
                f"Действует до: {date}\n\n"
                f"Новый тариф: {preview.requested.title}\n"
                f"Стоимость следующего периода: {preview.requested.price:.2f} {preview.requested.currency}\n"
                f"Начнет действовать: {date}",
                reply_markup=plan_change_confirmation_keyboard(plan,child.native_language or 'ru'),
            )
            return
        except Exception as exc:
            await message.answer(f"Не удалось подготовить изменение тарифа: {exc}")
            return
    if provider=='stripe' and settings.stripe_secret_key:
        try:
            from app.services.payment_adapter import create_stripe_subscription_checkout
            url=create_stripe_subscription_checkout(child_id=child.id,course_id=course_id,plan_id=plan,lessons_per_week=freq,monthly_price=effective_price,currency=currency,success_url=base+'/payment/success',cancel_url=base+'/payment/cancel',idempotency_key=idem)
        except Exception as exc:
            await message.answer(f'Не удалось создать страницу оплаты: {exc}'); return
    elif provider=='unipay':
        try:
            from app.services.unipay_adapter import create_unipay_subscription_checkout
            url=await create_unipay_subscription_checkout(child_id=child.id,course_id=course_id,plan_id=plan,lessons_per_week=freq,monthly_price=effective_price,currency=currency,success_url=base+'/payment/success',cancel_url=base+'/payment/cancel',webhook_url=base+'/webhooks/unipay',idempotency_key=idem)
        except Exception as exc:
            await message.answer(f'Не удалось создать страницу UniPAY: {exc}'); return
    elif provider=='unlimit':
        try:
            from app.services.unlimit_adapter import create_unlimit_subscription_checkout
            url=await create_unlimit_subscription_checkout(child_id=child.id,course_id=course_id,plan_id=plan,lessons_per_week=freq,monthly_price=effective_price,currency=currency,success_url=base+'/payment/success',cancel_url=base+'/payment/cancel',webhook_url=base+'/webhooks/unlimit',idempotency_key=idem)
        except Exception as exc:
            await message.answer(f'Не удалось создать страницу Unlimit: {exc}'); return
    elif provider=='paypal':
        try:
            from app.services.paypal_adapter import create_paypal_subscription_checkout
            url=await create_paypal_subscription_checkout(child_id=child.id,course_id=course_id,plan_id=plan,lessons_per_week=freq,monthly_price=effective_price,currency=currency,success_url=base+'/payment/success',cancel_url=base+'/payment/cancel',idempotency_key=idem)
        except Exception as exc:
            await message.answer(f'Не удалось создать страницу PayPal: {exc}'); return
    if not url:
        await message.answer("Платёжный провайдер пока не настроен. Настройте выбранный провайдер в Railway Variables. Для Stripe, UniPAY, Unlimit или PayPal добавьте соответствующие merchant credentials и webhook настройки. ZIP менять не потребуется.")
        return
    await message.answer(
        f"Выбран тариф: {names.get(plan, plan)}. Оплата поступит в аккаунт платёжного провайдера, который создал эту ссылку.",
        reply_markup=payment_checkout_keyboard("Перейти к безопасной оплате", url, child.native_language or "ru"),
    )


@router.callback_query(F.data.startswith("payment:confirm_plan:"))
async def payment_confirm_plan_change(cb: CallbackQuery, state: FSMContext):
    child=await get_child_from_state_or_user(state,cb.from_user.id)
    if child is None:await cb.answer('/start',show_alert=True);return
    plan=cb.data.rsplit(':',1)[1];data=await state.get_data()
    if str(data.get('pending_payment_plan_confirmation') or '')!=plan:
        await cb.answer('Сначала заново выберите тариф.',show_alert=True);return
    course_id=str(data.get('pending_course_id') or first_active_course_id() or 'conversation')
    try:
        from app.services.subscription_plan_changes import preview_plan_change,schedule_plan_change
        from app.services.subscription_provider import schedule_provider_plan_change
        async with SessionLocal() as db:
            sub=await db.scalar(select(Subscription).where(
                Subscription.child_id==child.id,Subscription.course_id==course_id,
                Subscription.status=='ACTIVE',Subscription.test_mode==False,
            ).order_by(Subscription.id.desc()))
            if sub is None:raise RuntimeError('Активная оплаченная подписка не найдена')
            preview=await preview_plan_change(db,sub,parent_id=child.parent_id,requested_plan_id=plan)
            base=settings.effective_webapp_base_url or 'https://t.me'
            provider=await schedule_provider_plan_change(
                sub,preview.requested,effective_at=preview.effective_at,base_url=base,
                idempotency_key=f'plan-change:{sub.id}:{plan}:{int(preview.effective_at.timestamp())}',
            )
            schedule_plan_change(db,sub,parent_id=child.parent_id,preview=preview,provider_status=provider.status,provider_reference=provider.reference)
            await db.commit()
        await state.update_data(pending_payment_plan_confirmation=None)
        date=preview.effective_at.strftime('%d.%m.%Y')
        if provider.approval_url:
            await cb.message.answer('Войдите в PayPal и подтвердите смену. Без этого текущий тариф останется без изменений.',reply_markup=payment_checkout_keyboard('Подтвердить новый тариф в PayPal',provider.approval_url,child.native_language or 'ru'))
            await cb.message.answer('Запланированную смену можно отменить до следующего периода.',reply_markup=plan_change_cancel_keyboard(child.native_language or 'ru'))
        else:
            await cb.message.answer(
                f'Готово. До {date} действует текущий тариф.\n'
                f'С {date} начнёт действовать {preview.requested.title}, и автоматически будет списываться '
                f'{preview.requested.price:.2f} {preview.requested.currency} за каждый следующий период, пока тариф не будет изменён или подписка отменена.',
                reply_markup=plan_change_cancel_keyboard(child.native_language or 'ru'),
            )
        await cb.answer()
    except Exception as exc:
        await cb.answer(str(exc)[:180],show_alert=True)


@router.callback_query(F.data == "payment:cancel_plan_change")
async def payment_cancel_plan_change(cb: CallbackQuery, state: FSMContext):
    child=await get_child_from_state_or_user(state,cb.from_user.id)
    if child is None:await cb.answer('/start',show_alert=True);return
    data=await state.get_data();course_id=str(data.get('pending_course_id') or first_active_course_id() or 'conversation')
    try:
        from app.services.subscription_plan_changes import cancel_plan_change,current_plan_snapshot
        from app.services.subscription_provider import restore_provider_current_plan
        async with SessionLocal() as db:
            sub=await db.scalar(select(Subscription).where(
                Subscription.child_id==child.id,Subscription.course_id==course_id,
                Subscription.status=='ACTIVE',Subscription.test_mode==False,
            ).order_by(Subscription.id.desc()))
            if sub is None or not sub.pending_plan_id:raise RuntimeError('Запланированного изменения тарифа нет')
            effective_at=sub.pending_plan_effective_at or datetime.utcnow()
            provider=await restore_provider_current_plan(
                sub,current_plan_snapshot(sub),effective_at=effective_at,
                base_url=settings.effective_webapp_base_url or 'https://t.me',
                idempotency_key=f'plan-change-cancel:{sub.id}:{int(effective_at.timestamp())}',
            )
            if provider.approval_url:
                sub.pending_provider_status='CANCEL_PENDING_APPROVAL';sub.pending_provider_reference=provider.reference or None
            else:cancel_plan_change(db,sub,parent_id=child.parent_id)
            await db.commit()
        if provider.approval_url:
            await cb.message.answer('Подтвердите отмену смены тарифа в PayPal. До подтверждения запланированное изменение сохраняется.',reply_markup=payment_checkout_keyboard('Подтвердить отмену в PayPal',provider.approval_url,child.native_language or 'ru'))
        else:await cb.message.answer('Запланированное изменение тарифа отменено. Текущий тариф остаётся без изменений.')
        await cb.answer()
    except Exception as exc:
        await cb.answer(str(exc)[:180],show_alert=True)

@router.callback_query(F.data == "payment:skip")
async def payment_skip(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    native = child.native_language or "en"
    await cb.message.answer("Хорошо, привязку карты можно завершить позже." if native == "ru" else "Okay, you can link a card later.", reply_markup=menu_hub_keyboard(native))
    await cb.answer()


@router.callback_query(F.data == "payment:not_configured")
async def payment_not_configured(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    native = child.native_language if child else "ru"
    await cb.answer("Ссылка на оплату ещё не настроена. Добавьте PAYMENT_URL в .env." if native == "ru" else "Payment link is not configured yet. Add PAYMENT_URL to .env.", show_alert=True)


@router.callback_query(F.data.startswith("consent:accept:"))
async def consent_accept(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if not child:
        await cb.answer("/start", show_alert=True); return
    consent_type = cb.data.rsplit(":", 1)[1]
    await state.update_data(pending_consent_type=consent_type)
    await state.set_state(ConsentFlow.phone)
    await cb.message.answer("Введите номер телефона родителя в международном формате, например +9955... Код подтверждения придёт по SMS.")
    await cb.answer()

@router.message(ConsentFlow.phone, F.text)
async def consent_phone(message: Message, state: FSMContext):
    phone = message.text.strip().replace(" ", "")
    if not phone.startswith("+") or len(phone) < 9:
        await message.answer("Введите номер в международном формате, начиная с +."); return
    try:
        challenge = await send_verification(phone)
    except SMSConsentError as exc:
        await message.answer(str(exc)); return
    await state.update_data(
        consent_phone=phone,
        consent_code_hash=challenge.code_hash,
        consent_code_salt=challenge.salt,
        consent_code_expires_at=challenge.expires_at,
        consent_code_attempts=0,
    )
    await state.set_state(ConsentFlow.code)
    await message.answer("Введите шестизначный код из SMS. Код действует 10 минут.")

@router.message(ConsentFlow.code, F.text)
async def consent_code(message: Message, state: FSMContext):
    data = await state.get_data()
    child = await get_child_from_state_or_user(state, message.from_user.id)
    if not child:
        await message.answer("/start"); return
    attempts = int(data.get("consent_code_attempts", 0))
    if attempts >= settings.sms_otp_max_attempts:
        await state.set_state(ConsentFlow.phone)
        await message.answer("Слишком много попыток. Введите номер телефона ещё раз, чтобы получить новый код.")
        return
    try:
        approved = await check_verification(
            data.get("consent_phone", ""),
            message.text.strip(),
            code_hash=data.get("consent_code_hash", ""),
            salt=data.get("consent_code_salt", ""),
            expires_at=int(data.get("consent_code_expires_at", 0)),
        )
    except SMSConsentError as exc:
        await message.answer(str(exc)); return
    if not approved:
        attempts += 1
        await state.update_data(consent_code_attempts=attempts)
        remaining = max(0, settings.sms_otp_max_attempts - attempts)
        await message.answer(f"Неверный или истёкший код. Осталось попыток: {remaining}.")
        return
    consent_type = data.get("pending_consent_type", "VOICE_RECORDING")
    version = settings.voice_consent_version if consent_type == "VOICE_RECORDING" else settings.payment_consent_version
    async with SessionLocal() as db:
        db_child = await db.get(Child, child.id)
        parent = await db.get(Parent, db_child.parent_id)
        parent.phone = data.get("consent_phone")
        db.add(ConsentRecord(parent_id=parent.id, child_id=db_child.id, consent_type=consent_type,
            version=version, phone=data.get("consent_phone"), telegram_user_id=message.from_user.id,
            details_json=json.dumps({"method":"smscenter_georgia_otp","telegram_chat_id":message.chat.id}, ensure_ascii=False)))
        await db.commit()
    await state.set_state(None)
    await message.answer("✅ Согласие подтверждено по SMS.")
    if consent_type == "PAYMENT" and data.get("pending_payment_plan"):
        await _show_checkout(message, child, data["pending_payment_plan"])
    elif data.get("consent_return") == "lesson":
        await _resume_or_start_lesson(message, state, child)
    else:
        await show_menu(message, state, child)

@router.callback_query(F.data == "menu:character")
async def menu_character(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    native = child.native_language or "en"
    active: Character | None = None
    async with SessionLocal() as db:
        if child.active_character_id:
            active = await db.get(Character, child.active_character_id)
    allow_preset_change = active is None or active.source == "BOT_CATALOG"
    text = tr(native, "character_menu")
    if active and active.source == "CHILD_DRAWING":
        text += "\n\n" + tr(native, "drawing_locked")
    await cb.message.answer(text, reply_markup=character_menu_keyboard(native, allow_preset_change))
    await cb.answer()


@router.callback_query(F.data == "menu:lessons")
async def menu_lessons(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    courses = [(c.course_id, c.title) for c in list_courses() if c.active]
    await cb.message.answer(
        "📚 Выберите курс:" if (child.native_language or "ru") == "ru" else "📚 Choose a course:",
        reply_markup=course_list_keyboard(courses, child.native_language or "ru"),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("course:select:"))
async def course_select(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    course_id = cb.data.split(":", 2)[2]
    course = next((c for c in list_courses() if c.course_id == course_id), None)
    if not client_course_allowed(course_id):
        await cb.answer("Сейчас доступен только разговорный курс.", show_alert=True); return
    if course is None or not course.active or not course.lesson_ids:
        await cb.answer("В этом курсе уроки скоро появятся.", show_alert=True); return
    payments=load_settings("payments")
    billing_on=bool(payments.get("billing_enabled",False))
    personal_release=await _personal_release_enabled(child.id,course_id)
    if personal_release:
        await release_due_lessons(child.id,course_id)
    async with SessionLocal() as db:
        ents=(await db.scalars(select(LessonEntitlement).where(LessonEntitlement.child_id==child.id,LessonEntitlement.course_id==course_id))).all()
    by={e.lesson_id:e for e in ents}
    rows=[]
    now=datetime.utcnow()
    for lid in course.lesson_ids:
        authored=load_authored_lesson(lid)
        try: title=(authored or load_lesson(lid)).get("title") or lid
        except Exception: continue
        e=by.get(lid)
        if not personal_release and e is None:
            # Billing off + no explicit test plan: unrestricted QA preview of published lessons.
            status="🧪"
        elif e is None:
            status="⏳"
        elif e.expires_at and e.expires_at<now:
            status="⌛"
        elif e.completed_runs>=e.max_completed_runs: status="🔒"
        elif e.completed_runs==1: status="🔁"
        else: status="▶️"
        rows.append((lid,title,status))
    await state.update_data(selected_course_id=course_id)
    await cb.message.answer("📚 Уроки курса. 🔁 — доступно второе прохождение, 🔒 — два прохождения использованы.",reply_markup=course_lessons_keyboard(course_id,rows,child.native_language or "ru"))
    await cb.answer()

@router.callback_query(F.data.startswith("lessonpick:"))
async def lesson_pick(cb: CallbackQuery, state: FSMContext):
    child=await get_child_from_state_or_user(state,cb.from_user.id)
    if child is None: await cb.answer("/start",show_alert=True); return
    _,course_id,lesson_id=cb.data.split(":",2)
    if not client_course_allowed(course_id):
        await cb.answer("Этот курс временно отключён.", show_alert=True); return
    authored=load_authored_lesson(lesson_id)
    # Legacy conversation lessons and universal content_v1 lessons share the same
    # entitlement rules. Do not hide the original conversation lesson merely
    # because it uses the legacy timeline renderer.
    try:
        manifest = authored or load_lesson(lesson_id)
    except Exception:
        await cb.answer("Урок временно недоступен.",show_alert=True); return
    personal_release=await _personal_release_enabled(child.id,course_id)
    if personal_release:
        await release_due_lessons(child.id,course_id)
    else:
        await ensure_test_entitlement(child.id,lesson_id,course_id)
    allowed,reason,_=await can_start_authored(child.id,lesson_id,course_id)
    if not allowed:
        if reason=="RUN_LIMIT": msg="Урок уже пройден два раза."
        elif reason=="EXPIRED": msg="Срок доступа к уроку закончился."
        else: msg="Этот урок ещё не открыт по вашему тарифу."
        await cb.answer(msg,show_alert=True); return
    await state.update_data(selected_course_id=course_id,selected_lesson_id=lesson_id,current_lesson_id=lesson_id)
    await cb.message.answer(f"▶ {manifest.get('title') or lesson_id}",reply_markup=start_lesson_keyboard(child.native_language or "ru"))
    await cb.answer()


@router.callback_query(F.data == "menu:pilot_access")
async def menu_pilot_access(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    await state.set_state(PilotAccessFlow.code)
    await cb.message.answer("🎟 Введите код доступа вашей школы или пилотной группы.")
    await cb.answer()


@router.message(PilotAccessFlow.code, F.text, ~F.text.startswith("/"))
async def pilot_access_code(message: Message, state: FSMContext):
    child = await get_child_from_state_or_user(state, message.from_user.id)
    if child is None:
        await state.clear(); return
    code = message.text.strip().upper()
    pilot = find_pilot(code)
    if not pilot:
        await message.answer("Код не найден или отключён. Проверьте написание и попробуйте ещё раз.")
        return
    courses = [str(x) for x in (pilot.get("courses") or [])]
    max_children = max(1, int(pilot.get("max_children", 9999) or 9999))
    async with SessionLocal() as db:
        existing_children = set((await db.scalars(select(CourseEnrollment.child_id).where(
            CourseEnrollment.payment_reference == f"PILOT:{code}",
            CourseEnrollment.status == "ACTIVE",
        ))).all())
        if child.id not in existing_children and len(existing_children) >= max_children:
            await message.answer("В этой пилотной группе уже использованы все места. Свяжитесь со школой.")
            return
        until = access_until(pilot)
        for course_id in courses:
            row = await db.scalar(select(CourseEnrollment).where(
                CourseEnrollment.child_id == child.id, CourseEnrollment.course_id == course_id,
                CourseEnrollment.status == "ACTIVE",
            ).order_by(CourseEnrollment.id.desc()))
            if row is None:
                db.add(CourseEnrollment(
                    child_id=child.id, course_id=course_id, status="ACTIVE",
                    access_source="PILOT_CODE", payment_reference=f"PILOT:{code}", access_until=until,
                ))
            else:
                row.access_until = until
                row.access_source = "PILOT_CODE"
                row.payment_reference = f"PILOT:{code}"
        await db.commit()
    await state.set_state(None)
    await state.update_data(child_id=child.id)
    org = pilot.get("organization") or "вашей организации"
    await message.answer(f"✅ Доступ к пилоту «{org}» активирован. Код действует для {len(courses)} курс(ов).", reply_markup=parent_menu_keyboard(child.native_language or "ru"))


@router.callback_query(F.data == "menu:progress")
async def menu_progress(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    async with SessionLocal() as db:
        completed = (await db.scalars(select(LessonSession).where(LessonSession.child_id == child.id, LessonSession.status == "COMPLETED"))).all()
    await cb.message.answer(f"Завершено уроков: {len(completed)}", reply_markup=child_menu_keyboard(child.native_language or "en"))
    await cb.answer()


@router.callback_query(F.data == "menu:settings")
async def menu_settings(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    await cb.message.answer(tr(child.native_language, "parent_menu_title"), reply_markup=parent_menu_keyboard(child.native_language or "en"))
    await cb.answer()



@router.callback_query(F.data == "menu:schedule")
async def menu_schedule(cb: CallbackQuery, state: FSMContext):
    child=await get_child_from_state_or_user(state,cb.from_user.id)
    if child is None: await cb.answer("/start",show_alert=True); return
    current=load_schedule(child.id)
    native=child.native_language or "ru"
    if current:
        txt=(f"Текущее расписание: {', '.join(current.get('days',[]))} {current.get('local_time')} · {current.get('timezone')}" if native=='ru' else f"Current schedule: {', '.join(current.get('days',[]))} {current.get('local_time')} · {current.get('timezone')}")
        await cb.message.answer(txt)
    await state.update_data(schedule_child_id=child.id)
    await state.set_state(SettingsFlow.schedule_timezone)
    await cb.message.answer("Напиши свой часовой пояс, например Europe/Berlin, America/New_York или Asia/Tbilisi." if native=='ru' else "Send your time zone, e.g. Europe/Berlin, America/New_York or Asia/Tbilisi.")
    await cb.answer()

@router.message(SettingsFlow.schedule_timezone)
async def schedule_timezone(message: Message, state: FSMContext):
    from zoneinfo import ZoneInfo
    tz=(message.text or '').strip()
    try: ZoneInfo(tz)
    except Exception:
        await message.answer("Не узнаю этот часовой пояс. Пример: Europe/Berlin"); return
    await state.update_data(schedule_timezone=tz); await state.set_state(SettingsFlow.schedule_days)
    await message.answer("Напиши дни занятий через пробел: mon tue wed thu fri sat sun. Например: tue thu")

@router.message(SettingsFlow.schedule_days)
async def schedule_days(message: Message, state: FSMContext):
    allowed={'mon','tue','wed','thu','fri','sat','sun'}; days=[x.lower()[:3] for x in (message.text or '').replace(',',' ').split()]
    if not days or any(x not in allowed for x in days):
        await message.answer("Пример: tue thu"); return
    await state.update_data(schedule_days=days); await state.set_state(SettingsFlow.schedule_time)
    await message.answer("Во сколько удобно заниматься по вашему местному времени? Напиши HH:MM, например 18:30")

@router.message(SettingsFlow.schedule_time)
async def schedule_time(message: Message, state: FSMContext):
    value=(message.text or '').strip(); data=await state.get_data(); child_id=int(data.get('schedule_child_id') or 0)
    try:
        cfg=save_schedule(child_id,data['schedule_timezone'],data['schedule_days'],value,15)
    except Exception:
        await message.answer("Не понимаю время. Напиши, например: 18:30"); return
    await state.clear(); await message.answer(f"✅ Расписание сохранено: {', '.join(cfg['days'])} {cfg['local_time']} · {cfg['timezone']}. Напомню за 15 минут и ночью писать не буду.")

@router.callback_query(F.data == "menu:children")
async def menu_children(cb: CallbackQuery, state: FSMContext):
    parent=await get_or_create_parent(cb.from_user.id,cb.from_user.full_name)
    async with SessionLocal() as db:
        children=list((await db.scalars(select(Child).where(Child.parent_id==parent.id).order_by(Child.id.asc()))).all())
    data=await state.get_data(); active_id=int(data.get("child_id") or (children[0].id if children else 0))
    lang=(next((c.native_language for c in children if c.id==active_id),None) or "ru")
    text="👨‍👩‍👧‍👦 Выберите ребёнка. Все уроки, прогресс и подписки хранятся отдельно для каждого ребёнка. Максимум — 5 детей."
    await cb.message.answer(text,reply_markup=family_children_keyboard([(c.id,c.display_name) for c in children],active_id,lang,can_add=len(children)<MAX_CHILDREN_PER_PARENT))
    await cb.answer()

@router.callback_query(F.data.startswith("family:select:"))
async def family_select_child(cb: CallbackQuery, state: FSMContext):
    raw=cb.data.rsplit(":",1)[1]
    if not raw.isdigit(): await cb.answer("Ошибка",show_alert=True); return
    child_id=int(raw)
    parent=await get_or_create_parent(cb.from_user.id,cb.from_user.full_name)
    async with SessionLocal() as db:
        child=await db.scalar(select(Child).where(Child.id==child_id,Child.parent_id==parent.id))
    if child is None: await cb.answer("Ребёнок не найден",show_alert=True); return
    async with SessionLocal() as db:
        db_parent=await db.get(Parent,parent.id); db_parent.active_child_id=child.id; await db.commit()
    # Clear lesson/payment FSM data when switching children; incomplete lesson progress is already persisted in DB.
    await state.clear()
    await state.update_data(child_id=child.id)
    await cb.message.answer(f"✅ Активный ребёнок: {child.display_name}",reply_markup=parent_menu_keyboard(child.native_language or "ru"))
    await cb.answer()

@router.callback_query(F.data == "family:add")
async def family_add_child(cb: CallbackQuery, state: FSMContext):
    parent=await get_or_create_parent(cb.from_user.id,cb.from_user.full_name)
    if await family_child_count(parent.id) >= MAX_CHILDREN_PER_PARENT:
        await cb.answer("Можно подключить максимум 5 детей.",show_alert=True); return
    await state.clear()
    await state.set_state(Onboarding.child_name)
    await cb.message.answer("Как зовут ребёнка?")
    await cb.answer()

@router.callback_query(F.data == "menu:profile")
async def menu_profile(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    native = child.native_language or "en"
    character_label = tr(native, "no_character")
    if child.active_character_id:
        async with SessionLocal() as db:
            character = await db.get(Character, child.active_character_id)
        if character:
            character_label = tr(native, "preset_character" if character.source == "BOT_CATALOG" else "own_drawing")
    await cb.message.answer(
        tr(native, "profile_text", name=child.display_name, native=language_name(child.native_language), target=language_name(child.target_language), character=character_label),
        reply_markup=menu_hub_keyboard(native),
    )
    await cb.answer()


@router.callback_query(F.data == "character_source:upload")
async def choose_upload_character(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    native = child.native_language if child else "ru"
    await cb.message.answer(tr(native, "draw_upload"))
    await state.set_state(SettingsFlow.character_upload if child and child.active_character_id else Onboarding.character_upload)
    await cb.answer()


@router.callback_query(F.data == "character_source:preset")
async def choose_preset_character(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    native = child.native_language if child else "ru"
    characters = list_preset_characters()
    await cb.message.answer_photo(
        FSInputFile(preset_collage_path()),
        caption=tr(native, "preset_prompt"),
        reply_markup=preset_character_keyboard(characters, native or "en"),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("preset_character:"))
async def select_preset_character(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    native = child.native_language or "en"
    character_id = cb.data.split(":", 1)[1]
    try:
        preset = get_preset_character(character_id)
        source = preset_character_path(character_id)
    except (KeyError, FileNotFoundError):
        await cb.answer("Unavailable", show_alert=True); return
    root = settings.storage_root / "children" / str(child.id) / "characters"
    root.mkdir(parents=True, exist_ok=True)
    selected_path = root / f"preset_{character_id}.png"
    shutil.copy2(source, selected_path)
    async with SessionLocal() as db:
        character = Character(
            child_id=child.id, original_path=str(source), processed_path=str(selected_path),
            status="CONFIRMED", source="BOT_CATALOG", catalog_id=character_id,
        )
        db.add(character)
        await db.commit(); await db.refresh(character)
        db_child = await db.get(Child, child.id)
        db_child.active_character_id = character.id
        await db.commit()
    await state.update_data(child_id=child.id, character_id=character.id)
    await cb.message.answer_photo(
        FSInputFile(selected_path), caption=f"{preset['title']}\n{tr(native, 'chosen_character')}",
    )
    await show_payment_prompt(cb.message, child, allow_skip=True)
    await cb.answer()


@router.message(Onboarding.character_upload, F.photo)
@router.message(SettingsFlow.character_upload, F.photo)
async def character_photo(message: Message, state: FSMContext):
    child = await get_child_from_state_or_user(state, message.from_user.id)
    if child is None:
        await message.answer("/start"); return
    native = child.native_language or "en"
    root = settings.storage_root / "children" / str(child.id) / "characters"
    root.mkdir(parents=True, exist_ok=True)
    original = root / f"original_{message.message_id}.jpg"
    processed = root / f"processed_{message.message_id}.png"
    await message.bot.download(message.photo[-1], destination=original)
    try:
        process_character(original, processed)
    except CharacterProcessingError as exc:
        await message.answer(f"{exc}")
        return
    async with SessionLocal() as db:
        character = Character(
            child_id=child.id, original_path=str(original), processed_path=str(processed),
            status="WAITING_CONFIRMATION", source="CHILD_DRAWING",
        )
        db.add(character); await db.commit(); await db.refresh(character)
    await state.update_data(child_id=child.id, character_id=character.id)
    await message.answer_photo(FSInputFile(processed), caption="OK?", reply_markup=confirm_character_keyboard(native))
    await state.set_state(SettingsFlow.character_confirm)


@router.callback_query(F.data == "character:retry")
async def retry_character(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    native = child.native_language if child else "ru"
    await cb.message.answer(tr(native, "draw_upload"))
    await state.set_state(SettingsFlow.character_upload)
    await cb.answer()


@router.callback_query(F.data == "character:confirm")
async def confirm_character(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    data = await state.get_data()
    if child is None or not data.get("character_id"):
        await cb.answer("/start", show_alert=True); return
    async with SessionLocal() as db:
        character = await db.get(Character, data["character_id"])
        character.status = "CONFIRMED"
        db_child = await db.get(Child, child.id)
        db_child.active_character_id = character.id
        await db.commit()
    native = child.native_language or "en"
    await cb.message.answer(tr(native, "character_ready"))
    await show_payment_prompt(cb.message, child, allow_skip=True)
    await state.set_state(None)
    await cb.answer()


async def send_bot_speech(message: Message, child: Child, target_text: str, native_hint: str, phrase_id: str) -> tuple[str, str]:
    source_language = "ru"
    try:
        spoken_target = await translate_text(target_text, source_language, child.target_language or source_language)
        spoken_native = await translate_text(native_hint, source_language, child.native_language or source_language)
        cache = settings.storage_root / "tts-cache"
        target_audio = await synthesize_speech(spoken_target, child.target_language or source_language, cache, f"{phrase_id}_target")
        native_audio = await synthesize_speech(spoken_native, child.native_language or source_language, cache, f"{phrase_id}_native")
        if target_audio:
            await message.answer_voice(FSInputFile(target_audio), caption=f"🌍 {spoken_target}")
        if native_audio and (child.native_language != child.target_language or spoken_native != spoken_target):
            await message.answer_voice(FSInputFile(native_audio), caption=f"💬 {spoken_native}")
        return spoken_target, spoken_native
    except AISpeechError as exc:
        logging.getLogger("dome.speech").warning("AI speech temporarily unavailable: %s", exc)
        await message.answer("🔊 Голосовой помощник сейчас временно недоступен. Текст задания остаётся на экране — можно продолжить урок.")
        return target_text, native_hint


async def _send_course_slide_media(message: Message, slide: dict, image_file: Path, caption: str, reply_markup, lesson_id: str):
    """Send image or short video for a course step.

    New lessons can set media_type=video and video=<relative path>. If a video
    is unavailable, the image remains a safe fallback so the lesson never stalls.
    """
    if str(slide.get("media_type") or "image").lower() == "video" and slide.get("video"):
        video_path = settings.content_root / "lessons" / lesson_id / str(slide.get("video"))
        if video_path.exists():
            await message.answer_video(FSInputFile(video_path), caption=caption, reply_markup=reply_markup)
            return
    await message.answer_photo(FSInputFile(image_file), caption=caption, reply_markup=reply_markup)


async def send_step(message: Message, state: FSMContext):
    await state.update_data(skip_in_progress=False)
    data = await state.get_data()
    # v54: every slide owns an isolated transient context. Nothing from a previous
    # task is allowed to leak into Help, assessment or the next prompt.
    _step_now = int(data.get("slide_step", 0))
    if data.get("context_slide_step") != _step_now:
        await state.update_data(
            context_slide_step=_step_now,
            pending_question=None, expected_answer=None, selected_animal=None,
            current_goal=None, current_simplified_text=None, current_accepted_meaning=[],
            current_phrase_id=None, current_required_phrase=False, current_max_voice_seconds=60,
            followup_pending=False, followup_slide_id=None, ai_followup_count=0,
            correction_count=0, technical_count=0, recording_number=0, simplified_mode=False,
            post_required_started=False, skip_in_progress=False, animal_compare_pending=False,
            animal_compare_task=None, post_compare_phrase_id=None,
            required_phrase_owner_slide_id=None,
            animal_compare_resume=False, animal_compare_pair_id=None, animal_compare_question_index=0,
            suitcase_pending=False, suitcase_completed=False,
        )
        data = await state.get_data()
    child = await get_child_from_state_or_user(state, message.chat.id)
    if child is None:
        await message.answer("/start"); return
    lesson_id = _lesson_id(data)
    lesson = load_lesson(lesson_id)
    slides = lesson.get("slides") or []
    slide_step = int(data.get("slide_step", 0))
    await _persist_step(data.get("session_id"), slide_step)
    if slide_step < len(slides):
        _slide_for_log = slides[slide_step]
        activity("slide_open", tg_id=message.chat.id, child_id=child.id, child_name=child.display_name, session_id=data.get("session_id"), lesson=lesson_id, step=slide_step, slide_id=_slide_for_log.get("slide_id"), slide_order=_slide_for_log.get("order"))

    if slide_step >= len(slides):
        # The original conversation timeline now uses the same entitlement ledger
        # as universal lessons. That makes 2 runs, 10-month expiry and first-run
        # cartoon behavior consistent across every engine.
        course_id = str(lesson.get("course_id") or course_for_lesson(lesson_id) or "conversation")
        entitlement = await get_authored_entitlement(child.id, lesson_id, course_id)
        if entitlement is None:
            # Backward-compatible recovery for a legacy in-progress session created
            # before the entitlement migration. Never fabricate paid access while
            # billing is on; QA mode may safely create its normal test entitlement.
            if not bool(load_settings("payments").get("billing_enabled", False)):
                entitlement = await ensure_test_entitlement(child.id, lesson_id, course_id)
            else:
                await message.answer("Не удалось подтвердить доступ к уроку. Вернись в меню и открой урок снова.")
                await state.set_state(None)
                return
        completed_before = int(entitlement.completed_runs or 0)
        if completed_before >= int(entitlement.max_completed_runs or 2):
            await message.answer("Этот урок уже пройден два раза и больше недоступен.", reply_markup=menu_hub_keyboard(child.native_language or "en"))
            await state.set_state(None)
            return

        async with SessionLocal() as db:
            character = await db.get(Character, data["character_id"])
            attempts = (await db.scalars(select(VoiceAttempt).where(
                VoiceAttempt.lesson_session_id == data["session_id"],
                VoiceAttempt.status.in_(["ACCEPTED_CORRECT", "ACCEPTED_BEST_ATTEMPT"]),
            ).order_by(VoiceAttempt.id))).all()
            session = await db.get(LessonSession, data["session_id"])
            # Only first completion renders. The second completion is finalized
            # immediately and cannot accidentally create a second movie.
            if completed_before == 0 and session:
                session.status = "RENDERING"
                await db.commit()

        if completed_before > 0:
            entitlement, newly_completed = await complete_session_once(
                session_id=int(data["session_id"]), child_id=child.id, lesson_id=lesson_id,
                course_id=course_id, final_step=len(slides),
            )
            if not newly_completed:
                await message.answer("Урок уже был завершён.", reply_markup=menu_hub_keyboard(child.native_language or "en"))
                await state.set_state(None)
                return
            async with SessionLocal() as db:
                db_child = await db.get(Child, child.id)
                parent = await db.get(Parent, db_child.parent_id)
                all_attempts = (await db.scalars(select(VoiceAttempt).where(VoiceAttempt.lesson_session_id == data["session_id"]))).all()
                completed_count = len((await db.scalars(select(LessonSession).where(
                    LessonSession.child_id == db_child.id, LessonSession.status == "COMPLETED"
                ))).all())
            await _maybe_notify_course_progress(message,child,course_id)
            await _apply_pending_course_switch_after_lesson(message,child,course_id)
            await message.answer("⭐ Урок завершён во второй раз. Новый мультфильм не создаётся.")
            try:
                await _create_and_send_homework(message, child, data["session_id"], all_attempts, completed_count, parent, lesson_id=lesson_id)
            except Exception as exc:
                log.warning("Homework after second run failed: %s", exc)
            await message.answer("Главное меню", reply_markup=menu_hub_keyboard(child.native_language or "en"))
            await state.set_state(None)
            return
        audio_by_phrase = {attempt.phrase_id: Path(attempt.audio_path) for attempt in attempts if attempt.audio_path}
        output = settings.storage_root / "children" / str(child.id) / "cartoons" / f"lesson_{data['session_id']}.mp4"
        base_video = settings.content_root / "lessons" / lesson_id / lesson["cartoon_base"]
        activity("MOVIE_START", tg_id=message.chat.id, child_id=child.id, child_name=child.display_name, session_id=data.get("session_id"), output=str(output), accepted_voice_count=len(attempts))
        await message.answer("🎬 Собираю твой мультфильм…")
        render_ok = False
        # Render into a separate file. asyncio cannot forcibly stop a worker thread after
        # wait_for() expires; keeping the primary render separate prevents a late worker
        # from overwriting/corrupting the fallback that is being sent to Telegram.
        primary_output = output.with_name(f"{output.stem}.rendering.mp4")
        primary_output.unlink(missing_ok=True)
        try:
            cfg_path = Path("config/cartoon.json")
            cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
            render_timeout = float(cfg.get("total_render_timeout_seconds", 90))
            activity("RENDER_START", tg_id=message.chat.id, child_id=child.id, session_id=data.get("session_id"))
            await asyncio.wait_for(
                asyncio.to_thread(build_timeline_cartoon,
                    base_video,
                    Path(character.processed_path),
                    audio_by_phrase,
                    lesson["timeline"],
                    primary_output,
                ),
                timeout=render_timeout,
            )
            render_ok = primary_output.exists() and primary_output.stat().st_size > 10_000
            if render_ok:
                primary_output.replace(output)
                activity("RENDER_DONE", tg_id=message.chat.id, child_id=child.id, session_id=data.get("session_id"), bytes=output.stat().st_size)
        except asyncio.TimeoutError as exc:
            activity("MOVIE_FAILED", tg_id=message.chat.id, child_id=child.id, session_id=data.get("session_id"), stage="render_timeout", error=str(exc))
            log.exception("Primary cartoon render timed out")
        except Exception as exc:
            activity("MOVIE_FAILED", tg_id=message.chat.id, child_id=child.id, session_id=data.get("session_id"), stage="render", error=str(exc))
            log.exception("Primary cartoon render failed")

        # Guaranteed fallback. IMPORTANT: the v62 base video is ~62 MB, so a raw copy can
        # exceed Telegram Bot API upload limits. Always make the fallback Telegram-safe.
        if not render_ok:
            try:
                activity("FALLBACK_START", tg_id=message.chat.id, child_id=child.id, session_id=data.get("session_id"))
                ensure_telegram_safe_mp4(base_video, output)
                render_ok = output.exists() and output.stat().st_size > 10_000
                activity("FALLBACK_DONE", tg_id=message.chat.id, child_id=child.id, session_id=data.get("session_id"), output=str(output), bytes=output.stat().st_size if output.exists() else 0)
            except Exception as exc:
                log.exception("Base cartoon fallback failed")
                activity("MOVIE_FAILED", tg_id=message.chat.id, child_id=child.id, session_id=data.get("session_id"), stage="fallback", error=str(exc))

        if render_ok:
            # Do NOT mark the lesson COMPLETED before Telegram confirms the video was sent.
            # v62 did that, so a failed/oversized upload made subsequent attempts look completed
            # even though the child never received the cartoon.
            try:
                cfg_path = Path("config/cartoon.json")
                cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
                send_timeout = float(cfg.get("telegram_send_timeout_seconds", 120))
                activity("UPLOAD_START", tg_id=message.chat.id, child_id=child.id, session_id=data.get("session_id"), bytes=output.stat().st_size)
                await asyncio.wait_for(
                    message.answer_video(FSInputFile(output), caption="🎬"),
                    timeout=send_timeout,
                )
                activity("MOVIE_SENT", tg_id=message.chat.id, child_id=child.id, session_id=data.get("session_id"), bytes=output.stat().st_size)
            except Exception as exc:
                log.exception("Cartoon upload to Telegram failed")
                activity("MOVIE_FAILED", tg_id=message.chat.id, child_id=child.id, session_id=data.get("session_id"), stage="telegram_send", error=str(exc))
                async with SessionLocal() as db:
                    session = await db.get(LessonSession, data["session_id"])
                    if session:
                        session.status = "IN_PROGRESS"
                        await db.commit()
                await message.answer(
                    "Не удалось отправить мультфильм. Ответы сохранены. Открой урок ещё раз — бот повторит сборку.",
                    reply_markup=menu_hub_keyboard(child.native_language or "en"),
                )
                await state.set_state(None)
                return

            entitlement, newly_completed = await complete_session_once(
                session_id=int(data["session_id"]), child_id=child.id, lesson_id=lesson_id,
                course_id=course_id, final_step=len(slides),
            )
            if newly_completed:
                await mark_cartoon_generated(child.id, lesson_id, course_id)
                await _maybe_notify_course_progress(message,child,course_id)
                await _apply_pending_course_switch_after_lesson(message,child,course_id)
            async with SessionLocal() as db:
                db_child = await db.get(Child, child.id)
                parent = await db.get(Parent, db_child.parent_id)
                all_attempts = (await db.scalars(select(VoiceAttempt).where(VoiceAttempt.lesson_session_id == data["session_id"]))).all()
                completed_lessons = len((await db.scalars(select(LessonSession).where(
                    LessonSession.child_id == db_child.id, LessonSession.status == "COMPLETED"
                ))).all())
            activity("MOVIE_COMPLETE", tg_id=message.chat.id, child_id=child.id, child_name=child.display_name, session_id=data.get("session_id"), output=str(output))
            if newly_completed:
                try:
                    await _create_and_send_homework(message, db_child, data["session_id"], all_attempts, completed_lessons, parent, lesson_id=lesson_id)
                except Exception as exc:
                    log.warning("Homework after cartoon failed: %s", exc)
            await message.answer("Главное меню", reply_markup=menu_hub_keyboard(child.native_language or "en"))
        else:
            async with SessionLocal() as db:
                session = await db.get(LessonSession, data["session_id"])
                if session:
                    session.status = "IN_PROGRESS"
                    await db.commit()
            await message.answer(
                "Не удалось собрать MP4. Голосовые ответы сохранены. Открой урок ещё раз — бот повторит сборку.",
                reply_markup=menu_hub_keyboard(child.native_language or "en"),
            )
        await state.set_state(None)
        return

    slide = slides[slide_step]
    while slide.get("skip_in_runtime") or slide.get("slide_id") == "slide_02" or (32 <= int(slide.get("order", 0) or 0) <= 39):
        slide_step += 1
        if slide_step >= len(slides):
            await state.update_data(slide_step=slide_step)
            return await send_step(message, state)
        slide = slides[slide_step]
    if slide_step != int(data.get("slide_step", 0)):
        await state.update_data(slide_step=slide_step)
        data = await state.get_data()

    # v57: idempotent slide/task emission. Telegram retries or two handler paths
    # must never send the same task twice in a row. Animal compare question index
    # is part of the key, so its next planned question still emits normally.
    emission_key = _lesson_emission_key(slide, data)
    if data.get("last_emission_key") == emission_key:
        activity("duplicate_slide_suppressed", tg_id=message.chat.id, session_id=data.get("session_id"), slide_id=slide.get("slide_id"), emission_key=emission_key)
        return
    await state.update_data(last_emission_key=emission_key)
    data = await state.get_data()

    # A passive slide must always be navigable. Clear any stale follow-up state
    # left by the previous activity before rendering it.
    if not _slide_expects_answer(slide) and (data.get("followup_pending") or data.get("followup_slide_id")):
        await state.update_data(followup_pending=False, followup_slide_id=None)
        data = await state.get_data()

    phrase_id = slide.get("required_phrase_id")
    if slide.get("prelude_before_required") and not data.get("post_required_started"):
        phrase_id = None
    image_path = settings.content_root / "lessons" / lesson_id / slide["image"]

    # Generic data-driven Mini App tasks created in the desktop Lesson Builder.
    if slide.get("interactive_task") in {"drag_drop", "memory"}:
        base = settings.effective_webapp_base_url
        if not base:
            await message.answer("Интерактив временно недоступен. Настройте WEBAPP_BASE_URL в Railway.")
            return
        items = [str(x) for x in (slide.get("task_items") or [])]
        targets = [str(x) for x in (slide.get("task_targets") or items)]
        query = urlencode({
            "payload": "course_task", "type": slide.get("interactive_task"),
            "title": slide.get("bot_says_target") or "Задание",
            "prompt": slide.get("bot_explains_native") or "",
            "items": "|".join(items), "targets": "|".join(targets),
            "step": slide_step, "slide": slide.get("slide_id") or "",
        })
        url = base + "/free-topic-task?" + query
        await send_bot_speech(message, child, slide.get("bot_says_target", ""), slide.get("bot_explains_native", ""), slide.get("slide_id") or f"task_{slide_step}")
        localized_image = await render_slide(
            image_path, settings.storage_root / "slide-cache",
            character_path=(Path((await _get_character_path(data.get("character_id")))) if data.get("character_id") else None),
            character_box=slide.get("character_box"),
            target_language=child.target_language or "ru",
        )
        caption = f"{slide_step + 1}/{len(slides)}\n\n🌍 {slide.get('bot_says_target','')}"
        if slide.get("bot_explains_native"):
            caption += f"\n💬 {slide.get('bot_explains_native')}"
        await message.answer_photo(FSInputFile(localized_image), caption=caption, reply_markup=free_topic_webapp_keyboard("Открыть задание", url, child.native_language or "ru"))
        await state.update_data(current_slide_id=slide.get("slide_id"), generic_task_pending=True)
        await state.set_state(LessonFlow.waiting_webapp)
        return

    # Animal comparison Mini App: v56 runs TWO deterministic questions per pair,
    # one for each animal. The visual cards are mandatory and are shown before
    # the learner can answer.
    if slide.get("interactive_task") == "animal_compare":
        pair_id=str(slide.get("pair_id") or "")
        if pair_id not in {"penguin_parrot","lion_turtle"}:
            raise RuntimeError(f"Unsupported animal pair: {pair_id}")
        q_index=int(data.get("animal_compare_question_index",0) or 0)
        task=await build_compare_task(pair_id, child.target_language or "ru", seed=f"{data.get('session_id')}:{slide.get('slide_id')}", question_index=q_index)
        target,native=await send_bot_speech(message, child, task.get("question_ru") or task["question"], slide.get("bot_explains_native", ""), f"{slide.get('slide_id') or pair_id}_q{q_index}")
        base=settings.effective_webapp_base_url
        caption=f"{slide_step + 1}/{len(slides)}\n\n🌍 {target}"
        if native: caption += f"\n💬 {native}"
        if not base:
            await message.answer_photo(FSInputFile(image_path), caption=caption+"\n\n⚠️ Mini App domain is not configured.")
            return
        query=urlencode({"pair":pair_id,"question":task["question"],"correct":task["correct"],"a":task["animals"][0],"b":task["animals"][1],"la":task["labels"][task["animals"][0]],"lb":task["labels"][task["animals"][1]],"lang":child.target_language or "ru","native":child.native_language or "ru","slide":slide.get("slide_id") or "","qi":q_index})
        url=base+"/animal-compare?"+query
        from app.bot.keyboards import animal_compare_webapp_keyboard
        await message.answer_photo(FSInputFile(image_path),caption=caption,reply_markup=animal_compare_webapp_keyboard(child.native_language or "ru",url))
        await state.update_data(current_slide_id=slide.get("slide_id"),animal_compare_pending=True,animal_compare_task=task,post_compare_phrase_id=None,animal_compare_pair_id=pair_id,animal_compare_question_index=q_index)
        await state.set_state(LessonFlow.waiting_webapp)
        return

    # The suitcase is completed inside Telegram Mini App first. Only after the
    # child presses Done do we request the mandatory cartoon voice phrase.
    if slide.get("interactive_task") == "suitcase":
        target = slide.get("bot_says_target", "")
        native = slide.get("bot_explains_native", "")
        spoken_target, spoken_native = await send_bot_speech(message, child, target, native, slide["slide_id"])
        caption = f"{slide_step + 1}/{len(slides)}\n\n🌍 {spoken_target}"
        if spoken_native:
            caption += f"\n💬 {spoken_native}"
        rendered_image = await render_slide(
            image_path,
            settings.storage_root / "slide-cache",
            character_path=(Path((await _get_character_path(data.get("character_id")))) if data.get("character_id") else None),
            character_box=slide.get("character_box"),
            target_language=child.target_language or "ru",
        )
        webapp_url = settings.effective_webapp_base_url
        if webapp_url:
            url = webapp_url + (
                f"/?lang={child.target_language or 'en'}&native={child.native_language or 'ru'}"
                f"&level={child.language_level or 'PRE_A1'}&session={data.get('session_id')}"
                f"&slide={slide.get('slide_id')}&task=suitcase"
            )
            markup = suitcase_webapp_keyboard(child.native_language or "en", url)
        else:
            markup = None
            caption += "\n\n⚠️ Интерактив пока недоступен: в Railway откройте Settings → Networking → Generate Domain. Это обязательное задание и пропустить его нельзя."
        activity("suitcase_prompt", tg_id=message.chat.id, child_id=child.id, child_name=child.display_name, session_id=data.get("session_id"), slide_id=slide.get("slide_id"), webapp_ready=bool(webapp_url))
        await message.answer_photo(FSInputFile(rendered_image), caption=caption, reply_markup=markup)
        await state.update_data(current_slide_id=slide.get("slide_id"), suitcase_pending=True, suitcase_completed=False, current_required_phrase=False)
        await state.set_state(LessonFlow.waiting_webapp)
        return

    if slide.get("answer_mode") in {"required_voice", "optional_voice"}:
        if phrase_id:
            phrase = next(item for item in lesson["required_phrases"] if item["phrase_id"] == phrase_id)
            goal = phrase["target_text"]
            native_hint = phrase.get("native_hint") or slide.get("bot_explains_native", "")
            accepted_meaning = phrase.get("accepted_meaning") or phrase.get("choices") or []
            simplified_text = phrase.get("simplified_text") or goal
            storage_phrase_id = phrase_id
        else:
            goal = slide.get("question") or slide.get("bot_says_target", "")
            native_hint = slide.get("bot_explains_native", "")
            accepted_meaning = []
            simplified_text = slide.get("simplified_text") or goal
            storage_phrase_id = f"practice_{slide['slide_id']}"
        adaptive_target = adapt_prompt(slide.get("bot_says_target", goal), child.working_difficulty or 0.15) if slide.get("adaptive") else slide.get("bot_says_target", goal)
        target_text, spoken_native = await send_bot_speech(message, child, adaptive_target, native_hint, storage_phrase_id)
        simplified_target = await translate_text(simplified_text, "ru", child.target_language or "en")
        caption = f"{slide_step + 1}/{len(slides)}\n\n🌍 {target_text}"
        if spoken_native:
            caption += f"\n💬 {spoken_native}"
        caption += f"\n\n{tr(child.native_language, 'record_voice')}"
        localized_image = await render_slide(
            image_path,
            settings.storage_root / "slide-cache",
            character_path=(Path((await _get_character_path(data.get("character_id")))) if data.get("character_id") else None),
            character_box=slide.get("character_box"),
            target_language=child.target_language or "ru",
        )
        voice_markup = lesson_voice_keyboard(child.native_language or "en", allow_skip=(slide.get("slide_id") == "slide_19" and not phrase_id) or (bool(slide.get("allow_skip")) and slide.get("slide_id") != "slide_24" and not phrase_id), skip_token=str(slide.get("slide_id") or slide_step))
        await _send_course_slide_media(message, slide, Path(localized_image), caption, voice_markup, lesson_id)
        await state.update_data(
            current_phrase_id=storage_phrase_id,
            current_goal=target_text,
            current_accepted_meaning=accepted_meaning,
            current_simplified_text=simplified_target,
            current_slide_id=slide.get("slide_id"),
            current_required_phrase=bool(phrase_id),
            required_phrase_owner_slide_id=(slide.get("slide_id") if phrase_id else None),
            current_max_voice_seconds=float(slide.get("max_voice_seconds", 5 if phrase_id else 60)),
            attempt=0,
            correction_count=0,
            technical_count=0,
            recording_number=0,
            simplified_mode=False,
        )
        await state.set_state(LessonFlow.waiting_voice)
        return

    target = slide.get("bot_says_target", "")
    native = slide.get("bot_explains_native", "")
    # Static content is translated/voiced through the same two-language policy.
    spoken_target, spoken_native = await send_bot_speech(message, child, target, native, slide["slide_id"])
    caption = f"{slide_step + 1}/{len(slides)}\n\n🌍 {spoken_target}"
    if spoken_native: caption += f"\n💬 {spoken_native}"
    localized_image = await render_slide(
            image_path,
            settings.storage_root / "slide-cache",
            character_path=(Path((await _get_character_path(data.get("character_id")))) if data.get("character_id") else None),
            character_box=slide.get("character_box"),
            target_language=child.target_language or "ru",
        )
    if slide.get("type") == "card_selector":
        # Slide 9 is a blocking interaction: the lesson must not advance until
        # the child chooses exactly one card and answers its questions.
        markup = card_choice_keyboard(slide.get("card_options", []), child.native_language or "en")
        await state.update_data(
            card_pending=True,
            selected_card=None,
            card_questions=None,
            card_question_index=0,
            current_slide_id=slide.get("slide_id"),
        )
    elif slide.get("type") in {"image_choice", "object_click"}:
        raw_items = slide.get("image_choices") or slide.get("object_labels") or []
        if slide.get("slide_id") == "slide_32" and slide.get("object_sequence"):
            first = slide["object_sequence"][0]
            items = [f"{first['letter']} — {first['label_ru']}"]
            await state.update_data(object_sequence=slide["object_sequence"], object_position=0)
        else:
            items = [x.get("label_ru", x.get("id", "")) if isinstance(x, dict) else str(x) for x in raw_items]
        markup = choice_items_keyboard("lesson:item", items, child.native_language or "en")
    elif slide.get("type") == "mood_choice":
        markup = mood_keyboard(slide.get("mood_options", []), child.native_language or "en")
    elif slide.get("interactive_task") == "suitcase" and settings.effective_webapp_base_url:
        url = settings.effective_webapp_base_url + f"/?lang={child.target_language or 'en'}&native={child.native_language or 'ru'}&level={child.language_level or 'PRE_A1'}&session={data.get('session_id')}&slide={slide.get('slide_id')}&task=suitcase"
        markup = suitcase_webapp_keyboard(child.native_language or "en", url)
    else:
        markup = lesson_next_keyboard(child.native_language or "en")
    await _send_course_slide_media(message, slide, Path(localized_image), caption, markup, lesson_id)
    if slide.get("type") == "card_selector":
        await state.set_state(LessonFlow.waiting_card)
    else:
        await state.set_state(None)


@router.callback_query(F.data == "lesson:next")
async def next_lesson_slide(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("session_id"):
        await cb.answer("/menu", show_alert=True); return
    # Do not let a stale “Next” button skip slide 9. The selected card is
    # mandatory and the bot must wait for every answer before moving on.
    lesson_id = _lesson_id(data)
    lesson = load_lesson(lesson_id)
    step = int(data.get("slide_step", 0))
    slides = lesson.get("slides") or []
    if step < len(slides) and slides[step].get("type") == "card_selector" and (data.get("card_pending", True) or data.get("card_questions")):
        await cb.answer("Сначала выбери одну карточку.", show_alert=True)
        return
    # A stale Next button must never bypass the mandatory suitcase Mini App or its voice line.
    if step < len(slides) and slides[step].get("slide_id") == "slide_24":
        await cb.answer("Сначала собери чемодан и запиши обязательную фразу.", show_alert=True)
        return
    if data.get("followup_pending"):
        current_slide = slides[step] if step < len(slides) else None
        same_slide = (not data.get("followup_slide_id")) or data.get("followup_slide_id") == (current_slide or {}).get("slide_id")
        if same_slide and _slide_expects_answer(current_slide):
            await cb.answer("Сначала ответь на вопрос бота.", show_alert=True)
            return
        # Stale flag from a previous activity: never block a passive slide.
        await state.update_data(followup_pending=False, followup_slide_id=None)
        data = await state.get_data()
    new_step = next_runtime_step(slides, step)
    await state.update_data(slide_step=new_step)
    await _persist_step(data.get("session_id"), new_step)
    await cb.answer()
    await send_step(cb.message, state)



async def _start_authored_content_lesson(message: Message, state: FSMContext, child: Child, lesson_id: str) -> None:
    lesson = load_authored_lesson(lesson_id)
    if not lesson:
        raise FileNotFoundError(lesson_id)
    course_id = str(lesson.get("course_id") or course_for_lesson(lesson_id) or "conversation")
    payments = load_settings("payments")
    personal_release=await _personal_release_enabled(child.id,course_id)
    if personal_release:
        await release_due_lessons(child.id,course_id)
    else:
        await ensure_test_entitlement(child.id,lesson_id,course_id)
    allowed, access_reason, entitlement = await can_start_authored(child.id, lesson_id, course_id)
    if not allowed:
        if access_reason == "RUN_LIMIT":
            await message.answer("✅ Этот урок уже пройден два раза. Он больше не доступен для повторного прохождения.")
        elif access_reason == "EXPIRED":
            await message.answer("⌛ Срок доступа к этому уроку закончился.")
        elif bool(payments.get("billing_enabled",False)):
            # Cancellation stops FUTURE release only. An already unlocked entitlement
            # reaches this point as allowed and is never taken away.
            await _show_parent_course_payment_gate(message,state,child,lesson_id,course_id)
        else:
            await message.answer("⏳ Этот урок ещё не открыт по назначенному тестовому тарифу.")
        return
    # If first-run rendering previously failed, retry it from the persisted
    # completed-session snapshot before starting run 2. This never consumes a run.
    if bool(lesson.get("make_cartoon",False)) and int(entitlement.completed_runs or 0)==1 and not bool(entitlement.cartoon_generated):
        async with SessionLocal() as db:
            prev=await db.scalar(select(LessonSession).where(
                LessonSession.child_id==child.id, LessonSession.lesson_id==lesson_id, LessonSession.status=="COMPLETED"
            ).order_by(LessonSession.id.desc()))
        if prev:
            try: saved=json.loads(prev.runtime_state_json or "{}")
            except Exception: saved={}
            if saved.get("free_topic_voice_files"):
                recovery={
                    "free_topic_lesson": {
                        "title": lesson.get("title") or lesson_id,
                        "make_cartoon": bool(lesson.get("make_cartoon",False)),
                        "course_id": course_id,
                    },
                    "authored_lesson_id": lesson_id, "authored_course_id": course_id,
                    "character_id": child.active_character_id, **saved,
                }
                await _maybe_build_authored_cartoon(message,state,child,recovery,entitlement)
                entitlement=await get_authored_entitlement(child.id,lesson_id,course_id) or entitlement
    first_cartoon_run=bool(lesson.get("make_cartoon",False)) and int(entitlement.completed_runs or 0)==0 and not bool(entitlement.cartoon_generated)
    if first_cartoon_run and not child.active_character_id:
        await state.update_data(selected_course_id=course_id,selected_lesson_id=lesson_id,current_lesson_id=lesson_id)
        await message.answer("🎭 Для первого прохождения разговорного урока нужен герой для персонального мультфильма. Сначала выбери или загрузи героя.",reply_markup=character_source_keyboard(child.native_language or "ru"))
        return
    async with SessionLocal() as db:
        session = await db.scalar(select(LessonSession).where(
            LessonSession.child_id == child.id, LessonSession.lesson_id == lesson_id,
            LessonSession.status == "IN_PROGRESS",
        ).order_by(LessonSession.id.desc()))
        if session is None:
            session = LessonSession(child_id=child.id, lesson_id=lesson_id, level_at_start=child.language_level or "PRE_A1", lesson_revision=70)
            db.add(session); await db.commit(); await db.refresh(session)
        start_step = max(0, int(session.current_step or 0))
        try: runtime_saved=json.loads(session.runtime_state_json or "{}")
        except Exception: runtime_saved={}
    runtime_lesson = {
        "title": lesson.get("title") or lesson_id, "topic": lesson.get("title") or lesson_id,
        "slides": lesson.get("slides") or [], "make_cartoon": bool(lesson.get("make_cartoon", False)),
        "lesson_id": lesson_id, "course_id": course_id, "target_language": lesson.get("target_language") or "ru",
        "target_duration_minutes": lesson.get("target_duration_minutes",35),
    }
    restored={k:v for k,v in runtime_saved.items() if k in {"reading_support","reading_child_share","role_slide_step","role_turn_cursor","role_child_read","role_active_text","role_active_role","free_topic_attempts","free_topic_voice_files","free_topic_images","authored_stats"}}
    await state.update_data(
        authored_mode=True, authored_homework_mode=False, authored_lesson_id=lesson_id, authored_course_id=course_id,
        authored_lesson_dir=str(lesson_dir(lesson_id)), authored_session_id=session.id,
        free_topic_lesson=runtime_lesson, free_topic_key=f"authored_{lesson_id}", free_topic_step=start_step,
        free_topic_run=int(entitlement.completed_runs)+1, free_topic_voice_files=[], free_topic_images=[], free_topic_attempts={}, free_topic_skip_busy=False,
        selected_lesson_id=lesson_id, current_lesson_id=lesson_id, session_id=session.id, lesson_started_monotonic=time.monotonic(), reading_support=0,reading_child_share=0.6,authored_stats={},
        character_id=child.active_character_id, voice_unavailable_notified=False, role_selected_role='', role_choice_slide_step=-1, **restored,
    )
    await state.set_state(FreeTopicFlow.playing)
    await _send_free_topic_step(message, state, child)


def _authored_runtime_snapshot(data: dict) -> dict:
    keys={"reading_support","reading_child_share","role_slide_step","role_turn_cursor","role_child_read","role_active_text","role_active_role","role_selected_role","role_choice_slide_step","free_topic_attempts","free_topic_voice_files","free_topic_images","authored_stats"}
    return {k:data.get(k) for k in keys if data.get(k) is not None}


async def _persist_authored_step(state: FSMContext, step: int) -> None:
    data = await state.get_data()
    if not data.get("authored_mode"):
        return
    if data.get("authored_homework_mode"):
        hid=data.get("authored_homework_assignment_id")
        if hid:
            async with SessionLocal() as db:
                row=await db.get(HomeworkAssignment,int(hid))
                if row:
                    row.current_step=max(0,int(step)); row.status="IN_PROGRESS"
                    await db.commit()
        return
    sid = data.get("authored_session_id")
    if sid:
        async with SessionLocal() as db:
            row=await db.get(LessonSession,int(sid))
            if row:
                row.current_step=max(0,int(step))
                row.runtime_state_json=json.dumps(_authored_runtime_snapshot(data),ensure_ascii=False)
                await db.commit()


async def _persist_authored_runtime(state: FSMContext) -> None:
    data=await state.get_data()
    if data.get("authored_mode") and not data.get("authored_homework_mode"):
        await _persist_authored_step(state,int(data.get("free_topic_step",0)))


async def _start_authored_homework(message: Message, state: FSMContext, child: Child, lesson_id: str, assignment_id: int | None = None) -> bool:
    homework = load_homework(lesson_id)
    if not homework or not (homework.get("slides") or []):
        return False
    async with SessionLocal() as db:
        row=await db.get(HomeworkAssignment,int(assignment_id)) if assignment_id else None
        if row is None:
            row=await db.scalar(select(HomeworkAssignment).where(
                HomeworkAssignment.child_id==child.id,HomeworkAssignment.lesson_id==lesson_id,
                HomeworkAssignment.status.in_(["NEW","OPENED","IN_PROGRESS","DEFERRED"])
            ).order_by(HomeworkAssignment.id.desc()))
        if row:
            row.status="IN_PROGRESS"; await db.commit(); start_step=max(0,int(row.current_step or 0)); assignment_id=row.id
        else:
            start_step=0
    course_id=str((load_authored_lesson(lesson_id) or {}).get("course_id") or course_for_lesson(lesson_id) or "conversation")
    await state.update_data(
        authored_mode=True, authored_homework_mode=True, authored_lesson_id=lesson_id,authored_course_id=course_id,
        authored_homework_assignment_id=assignment_id,authored_lesson_dir=str(lesson_dir(lesson_id)),
        free_topic_lesson={"title": homework.get("title") or "Домашнее задание", "topic": homework.get("title") or "Домашнее задание", "slides": homework.get("slides") or [], "make_cartoon": False,"target_language":(load_authored_lesson(lesson_id) or {}).get("target_language") or "ru"},
        free_topic_key=f"homework_{lesson_id}", free_topic_step=start_step, free_topic_voice_files=[], voice_unavailable_notified=False, free_topic_images=[], free_topic_attempts={}, free_topic_skip_busy=False,
    )
    await state.set_state(FreeTopicFlow.playing)
    await message.answer("🏠 Домашнее задание" + (f" · продолжаем с шага {start_step+1}" if start_step else ""))
    await _send_free_topic_step(message, state, child)
    return True


async def _resume_or_start_lesson(message: Message, state: FSMContext, child: Child) -> None:
    state_data = await state.get_data()
    selected_course = str(state_data.get("selected_course_id") or "") or None
    lesson_id = _lesson_id(state_data) if (state_data.get("selected_lesson_id") or state_data.get("current_lesson_id")) else await _next_scheduled_lesson_id(child, selected_course)
    if not lesson_id:
        await message.answer("По вашему тарифу новый урок пока не открыт.")
        return
    authored = load_authored_lesson(lesson_id)
    if authored:
        await _start_authored_content_lesson(message, state, child, lesson_id)
        return

    # Legacy conversation renderer is kept for demo_001, but access accounting is
    # deliberately NOT legacy: it uses the exact same 2-run/10-month entitlement
    # ledger as every content_v1 lesson.
    course_id = selected_course or course_for_lesson(lesson_id) or first_active_course_id() or "conversation"
    if await _personal_release_enabled(child.id, course_id):
        await release_due_lessons(child.id, course_id)
    else:
        await ensure_test_entitlement(child.id, lesson_id, course_id)
    allowed, reason, _entitlement = await can_start_authored(child.id, lesson_id, course_id)
    if not allowed:
        if reason == "RUN_LIMIT":
            await message.answer("Этот урок уже пройден два раза и больше недоступен.")
        elif reason == "EXPIRED":
            await message.answer("Срок доступа к этому уроку закончился (10 месяцев с момента выдачи).")
        else:
            # With billing enabled, a missing entitlement means the plan has not
            # released this lesson. Existing entitlements survive cancellation.
            payments = load_settings("payments")
            if bool(payments.get("billing_enabled", False)):
                await _show_parent_course_payment_gate(message, state, child, lesson_id, course_id)
            else:
                await message.answer("Этот урок ещё не открыт по вашему тарифу.")
        return

    if not child.active_character_id:
        await message.answer(tr(child.native_language, "character_source"), reply_markup=character_source_keyboard(child.native_language or "en")); return
    async with SessionLocal() as db:
        active = await db.scalar(select(LessonSession).where(
            LessonSession.child_id == child.id, LessonSession.lesson_id == lesson_id,
            LessonSession.status.in_(["IN_PROGRESS", "RENDERING"]),
        ).order_by(LessonSession.id.desc()))
        if active:
            session = active
            if session.status == "RENDERING": session.status = "IN_PROGRESS"
        else:
            session = LessonSession(child_id=child.id, lesson_id=lesson_id,
                level_at_start=child.language_level or "PRE_A1", lesson_revision=24)
            db.add(session)
        await db.commit(); await db.refresh(session)
    normalized_step = normalize_lesson_step(session.current_step or 0, session.lesson_revision or 0)
    # v16 hard guard: removed source slides 32-39 can never resume.
    lesson = load_lesson(lesson_id)
    slides = lesson.get("slides") or []
    if normalized_step >= len(slides): normalized_step = max(0, len(slides)-1)
    while normalized_step < len(slides) and slides[normalized_step].get("order") in range(32, 40):
        normalized_step += 1
    async with SessionLocal() as db:
        db_session = await db.get(LessonSession, session.id)
        db_session.current_step = normalized_step; db_session.lesson_revision = 24; db_session.status = "IN_PROGRESS"
        await db.commit()
    await state.clear()
    await state.update_data(child_id=child.id, session_id=session.id, character_id=child.active_character_id,
        current_lesson_id=lesson_id, selected_lesson_id=lesson_id, selected_course_id=course_id,
        slide_step=normalized_step, attempt=0)
    await send_step(message, state)

@router.callback_query(F.data == "lesson:continue")
async def continue_lesson(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    async with SessionLocal() as db:
        active = await db.scalar(select(LessonSession).where(
            LessonSession.child_id == child.id,
            LessonSession.status.in_(["IN_PROGRESS", "RENDERING"]),
        ).order_by(LessonSession.id.desc()))
    if active:
        await state.update_data(selected_lesson_id=active.lesson_id, current_lesson_id=active.lesson_id)
    if not active:
        data=await state.get_data()
        course_id=str(data.get("selected_course_id") or "")
        if not course_id:
            async with SessionLocal() as db:
                enrollments=(await db.scalars(select(CourseEnrollment).where(
                    CourseEnrollment.child_id==child.id, CourseEnrollment.status=="ACTIVE"
                ).order_by(CourseEnrollment.id.asc()))).all()
            now=datetime.utcnow()
            valid=[x for x in enrollments if x.access_until is None or x.access_until>=now]
            course_id=(valid[0].course_id if valid else (first_active_course_id() or "conversation"))
        lesson_id=await _next_scheduled_lesson_id(child,course_id)
        if not lesson_id:
            await cb.answer("В этом курсе пока нет следующего урока.",show_alert=True); return
        await state.update_data(selected_course_id=course_id,selected_lesson_id=lesson_id,current_lesson_id=lesson_id)
    if not await _has_consent(child.parent_id, child.id, "VOICE_RECORDING", settings.voice_consent_version):
        await state.update_data(consent_return="lesson")
        await _request_consent(cb.message, child, "VOICE_RECORDING")
        await cb.answer(); return
    await _resume_or_start_lesson(cb.message, state, child)
    await cb.answer()

@router.callback_query(F.data == "lesson:start")
async def start_lesson(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    if not await _has_consent(child.parent_id, child.id, "VOICE_RECORDING", settings.voice_consent_version):
        await state.update_data(consent_return="lesson")
        await _request_consent(cb.message, child, "VOICE_RECORDING")
        await cb.answer(); return
    await _resume_or_start_lesson(cb.message, state, child)
    await cb.answer()



CARD_QUESTIONS = {
    "A": ["Что ты любишь на завтрак?", "Что ты любишь делать на улице?", "Ты хотел бы летать как птица или плавать как рыба?"],
    "Б": ["Какой супергерой тебе нравится?", "Чего ты боишься?", "Какое животное тебе нравится?"],
    "В": ["Какая твоя любимая игрушка?", "Что тебе не нравится?", "Что бы ты хотел, если бы шёл дождь из еды?"],
    "Г": ["Каким должен быть настоящий друг?", "Что может заставить тебя грустить?", "Какие три слова лучше всего тебя описывают?"],
    "Д": ["Что бы ты продавал в собственном магазине?", "Какая еда для тебя самая вкусная?", "Какой вопрос мог бы задать твой питомец?"],
    "Е": ["Какие три желания ты бы загадал?", "Куда ты хотел бы отправиться?", "Что необычное ты хотел бы увидеть?"],
}

@router.callback_query(F.data == "lesson:help")
async def lesson_help(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    data = await state.get_data()
    if not child:
        await cb.answer("/start", show_alert=True); return
    simple = data.get("current_simplified_text") or data.get("current_goal") or "Повтори за мной."
    target = await translate_text(simple, "ru", child.target_language or "en")
    native = await translate_text("Я помогу. Сначала послушай пример, затем повтори.", "ru", child.native_language or "ru")
    prompt_native = await translate_text("Вариант ответа. Послушай и повтори его.", "ru", child.native_language or "ru")
    await cb.message.answer(f"🆘 {prompt_native}\n\n🌍 {target}\n💬 {native}")
    try:
        audio = await synthesize_speech(target, child.target_language or "en", settings.storage_root / "tts-cache", "help")
        if audio: await cb.message.answer_voice(FSInputFile(audio))
    except AISpeechError:
        pass
    await cb.answer()

@router.callback_query(F.data.startswith("lesson:skip"))
async def lesson_skip(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("skip_in_progress"):
        await cb.answer("Уже пропускаю это задание…")
        return
    await state.update_data(skip_in_progress=True)
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    lesson = load_lesson(_lesson_id(data))
    slides = lesson.get("slides") or []
    step = int(data.get("slide_step", 0))
    if step >= len(slides):
        await cb.answer("Урок уже завершён.", show_alert=True); return
    slide = slides[step]
    # v51: a stale Skip button belongs only to the slide that created it.
    parts=(cb.data or "").split(":",2)
    token=parts[2] if len(parts)>2 else ""
    if token and token != str(slide.get("slide_id") or step):
        await state.update_data(skip_in_progress=False)
        await cb.answer("Эта кнопка относится к предыдущему заданию.", show_alert=True)
        return
    # Required cartoon lines are unskippable, except a non-cartoon animal phrase
    # selected inside an animal-compare task. Those optional animal phrases may be skipped.
    if (slide.get("required_phrase_id") or slide.get("unskippable") or slide.get("preserve_required_movie_line")) and not (data.get("animal_compare_resume") and not data.get("current_required_phrase")):
        await state.update_data(skip_in_progress=False)
        await cb.answer("Эта реплика нужна для мультфильма и её нельзя пропустить.", show_alert=True)
        return
    # Slide 19 has a skippable warm-up question (hot/cold), followed by the
    # first mandatory cartoon line about Lesha's clothes. Skip only the warm-up.
    if slide.get("slide_id") == "slide_19" and not data.get("post_required_started"):
        req_id = "lesha_clothes"
        req = next(x for x in lesson["required_phrases"] if x["phrase_id"] == req_id)
        child = await get_child_from_state_or_user(state, cb.from_user.id)
        req_target, _ = await send_bot_speech(cb.message, child, req["target_text"], req.get("native_hint", ""), req_id)
        req_simplified = await translate_text(req.get("simplified_text") or req["target_text"], "ru", child.target_language or "en")
        await cb.message.answer(
            "Эта короткая реплика войдёт в мультфильм. Запиши её до 5 секунд.",
            reply_markup=lesson_voice_keyboard(child.native_language or "en", allow_skip=False),
        )
        await state.update_data(
            current_phrase_id=req_id, current_goal=req_target,
            current_simplified_text=req_simplified,
            current_accepted_meaning=req.get("accepted_meaning") or [],
            current_required_phrase=True, current_max_voice_seconds=5,
            post_required_started=True, correction_count=0, technical_count=0,
            recording_number=0, simplified_mode=False,
        )
        await state.set_state(LessonFlow.waiting_voice)
        await state.update_data(skip_in_progress=False)
        await cb.answer("Переходим к обязательной реплике")
        return
    # v57: a non-cartoon animal phrase may be skipped without skipping the whole
    # comparison slide. Resume the next compare question (or next lesson step).
    if data.get("animal_compare_resume") and not data.get("current_required_phrase"):
        completed=list(data.get("completed_animal_voice") or [])
        animal=str(data.get("selected_animal") or "")
        if animal and animal not in completed:
            completed.append(animal)
        q_index=int(data.get("animal_compare_question_index",0) or 0)
        if q_index < 1:
            await state.update_data(
                completed_animal_voice=completed, animal_compare_question_index=q_index+1,
                animal_compare_resume=False, selected_animal=None, current_phrase_id=None,
                current_required_phrase=False, current_goal=None, current_simplified_text=None,
                current_accepted_meaning=[], skip_in_progress=False, last_emission_key=None,
            )
            await cb.answer("Пропускаю")
            await send_step(cb.message,state)
            return
        next_step=_resolve_next_step(slides,step,slide)
        await _persist_step(data.get("session_id"),next_step)
        await _reset_for_next_step(state,next_step=next_step)
        await state.update_data(completed_animal_voice=completed,skip_in_progress=False)
        await cb.answer("Пропускаю")
        await send_step(cb.message,state)
        return

    # The suitcase phrase is mandatory regardless of stale buttons/messages.
    if slide.get("slide_id") == "slide_24" or data.get("current_phrase_id") == "take_trip":
        await state.update_data(skip_in_progress=False); await cb.answer("Фраза про чемодан нужна для мультфильма и её нельзя пропустить.", show_alert=True); return
    if data.get("current_required_phrase"):
        await state.update_data(skip_in_progress=False); await cb.answer("Эта фраза нужна для мультфильма и её нельзя пропустить.", show_alert=True); return
    if slide.get("slide_id") == "slide_09":
        target_id = slide.get('next_slide') or 'slide_04'
        new_step = next((i for i, x in enumerate(slides) if x.get('slide_id') == target_id), next_runtime_step(slides, step))
    else:
        new_step = next_runtime_step(slides, step)
    await state.update_data(
        slide_step=new_step, card_questions=None, card_question_index=0,
        selected_card=None, card_pending=False, post_voice_jump=None,
        current_phrase_id=None, current_required_phrase=False, last_emission_key=None,
    )
    await _persist_step(data.get("session_id"), new_step)
    await cb.answer("Пропускаю")
    await send_step(cb.message, state)

@router.callback_query(F.data.startswith("lesson:card:"))
async def lesson_card(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    data = await state.get_data()
    if not child or not data.get("session_id"):
        await cb.answer("Открой урок заново через меню.", show_alert=True)
        return
    lesson = load_lesson(_lesson_id(data))
    step = int(data.get("slide_step", 0))
    slides = lesson.get("slides") or []
    if step >= len(slides) or slides[step].get("slide_id") != "slide_09":
        await cb.answer("Эта карточка уже не активна.", show_alert=True)
        return
    if data.get("selected_card") and not data.get("card_pending", True):
        await cb.answer("Карточка уже выбрана.", show_alert=True)
        return
    slide = slides[step]
    options = slide.get("card_options", [])
    idx = int(cb.data.rsplit(":", 1)[1])
    if idx < 0 or idx >= len(options):
        await cb.answer("Карточка не найдена.", show_alert=True)
        return
    card = options[idx]
    questions = CARD_QUESTIONS.get(card, CARD_QUESTIONS["A"])
    q = questions[0]
    # Disable the selector immediately so a double tap cannot start two flows.
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cb.answer(f"Выбрана карточка {card}")
    target_q = await translate_text(q, "ru", child.target_language or "en")
    await state.update_data(
        card_pending=True,
        card_questions=questions,
        card_question_index=0,
        selected_card=card,
        current_phrase_id=f"practice_card_{card}_0",
        current_goal=target_q,
        current_simplified_text=target_q,
        current_accepted_meaning=[],
        current_required_phrase=False,
        current_max_voice_seconds=60,
        post_voice_jump=next((i for i, x in enumerate(slides) if x.get('slide_id') == (slide.get('next_slide') or 'slide_04')), None),
        correction_count=0,
        technical_count=0,
        recording_number=0,
        simplified_mode=False,
    )
    await send_bot_speech(cb.message, child, q, q, f"card_{card}_0")
    await cb.message.answer(
        "Ответь голосом. Я дождусь ответа и только потом задам следующий вопрос.",
        reply_markup=lesson_voice_keyboard(child.native_language or "en", allow_skip=True),
    )
    await state.set_state(LessonFlow.waiting_voice)

@router.callback_query(F.data.startswith("lesson:item:"))
async def lesson_item(cb: CallbackQuery, state: FSMContext):
    child=await get_child_from_state_or_user(state, cb.from_user.id); data=await state.get_data(); lesson=load_lesson(_lesson_id(data))
    step=int(data.get("slide_step",0)); slide=lesson["slides"][step]; idx=int(cb.data.rsplit(":",1)[1])
    raw=slide.get("image_choices") or slide.get("object_labels") or []
    if slide.get("slide_id") == "slide_32":
        sequence = slide.get("object_sequence") or []
        pos = int(data.get("object_position", 0))
        if not sequence:
            await cb.answer("Последовательность задания не настроена", show_alert=True); return
        item = sequence[pos]
        label = item["label_ru"]
        target=await translate_text(label,"ru",child.target_language or "en"); native=await translate_text(label,"ru",child.native_language or "ru")
        extra=""
        if (child.working_difficulty or 0) >= .45 and item.get("use_ru"):
            extra=await translate_text(item["use_ru"],"ru",child.target_language or "en")
        await cb.message.answer(f"{item['icon']} {item['letter']}: 🌍 {target}" + (f"\n💬 {native}" if child.native_language != child.target_language else "") + (f"\n⭐ {extra}" if extra else "") + "\nПовтори голосом.", reply_markup=lesson_voice_keyboard(child.native_language or "en",allow_skip=True))
        try:
            audio=await synthesize_speech(target,child.target_language or "en",settings.storage_root/"tts-cache",f"object32_{pos}")
            if audio: await cb.message.answer_voice(FSInputFile(audio))
        except AISpeechError: pass
        await state.update_data(object_sequence=sequence, object_position=pos, current_phrase_id=f"practice_object32_{pos}", current_goal=label, current_simplified_text=label, current_accepted_meaning=[label], current_required_phrase=False, current_max_voice_seconds=60, correction_count=0,technical_count=0,recording_number=0,simplified_mode=False)
        await state.set_state(LessonFlow.waiting_voice); await cb.answer(); return
    value=raw[idx]; label=value.get("label_ru",value.get("id","")) if isinstance(value,dict) else str(value)
    target=await translate_text(label,"ru",child.target_language or "en"); native=await translate_text(label,"ru",child.native_language or "ru")
    extra = ""
    if slide.get("type") == "object_click" and (child.working_difficulty or 0) >= .45:
        use=f"Это {label}. Скажи, зачем это нужно."; extra=await translate_text(use,"ru",child.target_language or "en")
    await cb.message.answer(f"🌍 {target}" + (f"\n💬 {native}" if child.native_language != child.target_language else "") + (f"\n⭐ {extra}" if extra else ""))
    try:
        audio=await synthesize_speech(target,child.target_language or "en",settings.storage_root/"tts-cache",f"item_{step}_{idx}")
        if audio: await cb.message.answer_voice(FSInputFile(audio))
    except AISpeechError: pass
    if slide.get("type") == "object_click":
        goal = f"Повтори слово: {label}" if not extra else f"Скажи: Это {label}. Оно нужно для путешествия."
        await state.update_data(current_phrase_id=f"practice_object_{step}_{idx}", current_goal=goal, current_simplified_text=label,
            current_accepted_meaning=[label], current_required_phrase=False, current_max_voice_seconds=60,
            correction_count=0,technical_count=0,recording_number=0,simplified_mode=False)
        await state.set_state(LessonFlow.waiting_voice)
        await cb.message.answer("Повтори голосом.",reply_markup=lesson_voice_keyboard(child.native_language or "en",allow_skip=True))
    else:
        await state.update_data(slide_step=step+1); await _persist_step(data.get("session_id"), step+1); await send_step(cb.message,state)
    await cb.answer()

@router.callback_query(F.data.startswith("lesson:mood:"))
async def lesson_mood(cb: CallbackQuery, state: FSMContext):
    child=await get_child_from_state_or_user(state,cb.from_user.id); data=await state.get_data(); lesson=load_lesson(_lesson_id(data))
    slide=lesson["slides"][int(data.get("slide_step",0))]; idx=int(cb.data.rsplit(":",1)[1]); mood=slide.get("mood_options",[])[idx]
    target=await translate_text(mood,"ru",child.target_language or "en"); native=await translate_text(mood,"ru",child.native_language or "ru")
    await cb.message.answer(f"⭐ 🌍 {target}\n💬 {native}\nПовтори голосом.", reply_markup=lesson_voice_keyboard(child.native_language or "en",allow_skip=True))
    try:
        audio=await synthesize_speech(target,child.target_language or "en",settings.storage_root/"tts-cache",f"mood_{idx}")
        if audio: await cb.message.answer_voice(FSInputFile(audio))
    except AISpeechError: pass
    await state.update_data(current_phrase_id=f"practice_mood_{idx}",current_goal=mood,current_simplified_text=mood,
        current_accepted_meaning=[mood],current_required_phrase=False,current_max_voice_seconds=60,
        correction_count=0,technical_count=0,recording_number=0,simplified_mode=False)
    await state.set_state(LessonFlow.waiting_voice); await cb.answer()

@router.message(LessonFlow.waiting_voice, F.voice)
async def receive_voice(message: Message, state: FSMContext):
    data = await state.get_data()
    child = await get_child_from_state_or_user(state, message.from_user.id)
    if child:
        activity("voice_received", tg_id=message.from_user.id, child_id=child.id, child_name=child.display_name, session_id=data.get("session_id"), slide_id=data.get("current_slide_id"), phrase_id=data.get("current_phrase_id"), duration=getattr(message.voice, "duration", None))
    if child is None:
        await message.answer("/start"); return
    lesson = load_lesson(_lesson_id(data))
    storage_phrase_id = data.get("current_phrase_id")
    if not storage_phrase_id:
        # v62 recovery guard: a delayed Telegram voice update can arrive after the
        # lesson already advanced and transient voice state was cleared. Never write
        # a VoiceAttempt with phrase_id=None; re-render the current step instead.
        activity(
            "voice_without_phrase_recovered",
            tg_id=message.from_user.id,
            child_id=child.id,
            child_name=child.display_name,
            session_id=data.get("session_id"),
            slide_id=data.get("current_slide_id"),
        )
        await state.update_data(
            current_required_phrase=False,
            required_phrase_owner_slide_id=None,
            correction_count=0,
            technical_count=0,
            recording_number=0,
            simplified_mode=False,
            last_emission_key=None,
        )
        await message.answer("Эта запись пришла между заданиями. Ничего не потеряно — повторяю текущий шаг.")
        await state.set_state(None)
        await send_step(message, state)
        return
    phrase = next((item for item in lesson["required_phrases"] if item["phrase_id"] == storage_phrase_id), None)
    goal = data.get("current_goal") or (phrase.get("target_text", "") if phrase else "")
    accepted_meaning = data.get("current_accepted_meaning") or (phrase.get("accepted_meaning") or phrase.get("choices") if phrase else [])
    simplified_text = data.get("current_simplified_text") or (phrase.get("simplified_text") if phrase else goal) or goal
    required_phrase = bool(data.get("current_required_phrase"))
    owner_slide_id = data.get("required_phrase_owner_slide_id")
    if required_phrase and owner_slide_id and owner_slide_id != data.get("current_slide_id"):
        # v56 hard guard: a movie phrase can only belong to the currently visible step.
        await state.update_data(current_phrase_id=None,current_required_phrase=False,required_phrase_owner_slide_id=None)
        await message.answer("Этот вопрос уже закончился. Продолжаем текущее задание.")
        await send_step(message,state)
        return
    maximum = float(data.get("current_max_voice_seconds") or (5.0 if required_phrase else 60.0))
    duration = float(message.voice.duration or 0)
    hard_maximum = 12.0 if required_phrase else maximum
    if duration > hard_maximum:
        await message.answer(tr(child.native_language, "voice_too_long", duration=duration, maximum=hard_maximum)); return

    # correction_count counts only pedagogical corrections. Technical retries do not count.
    correction_count = int(data.get("correction_count", data.get("attempt", 0)))
    technical_count = int(data.get("technical_count", 0))
    simplified_mode = bool(data.get("simplified_mode"))
    recording_number = int(data.get("recording_number", 0)) + 1

    root = settings.storage_root / "children" / str(child.id) / "audio" / str(data["session_id"])
    root.mkdir(parents=True, exist_ok=True)
    raw_path = root / f"{storage_phrase_id}_recording_{recording_number}.ogg"
    wav_path = root / f"{storage_phrase_id}_recording_{recording_number}.wav"
    await message.bot.download(message.voice, destination=raw_path)
    try:
        prepare_child_voice(raw_path, wav_path, max_seconds=5.0 if required_phrase else None)
    except Exception:
        technical_count += 1
        if technical_count >= 2:
            await message.answer("Запись получена, но качество не позволило точно проверить речь. Я сохраню лучший вариант и продолжу урок.")
            status = "ACCEPTED_BEST_ATTEMPT"
            assessment = None
            wav_path = raw_path
        else:
            await state.update_data(technical_count=technical_count, recording_number=recording_number)
            await message.answer("Не удалось проверить качество записи. Это не считается ошибкой. Запиши ещё раз ближе к микрофону.")
            return
    else:
        try:
            assessment = await assess_speech(
                wav_path=wav_path,
                target_language=child.target_language or "en",
                native_language=child.native_language or "ru",
                goal=goal,
                accepted_meaning=accepted_meaning,
                attempt_number=correction_count + 1,
                child_name=child.display_name or "",
                working_difficulty=child.working_difficulty or 0.15,
            )
            status = assessment.status
            current_slide = next(
                (item for item in lesson.get("slides", []) if item.get("slide_id") == data.get("current_slide_id")),
                None,
            )
            if current_slide and current_slide.get("strict_target_language"):
                detected_code = (assessment.detected_language or "").lower().split("-")[0].split("_")[0]
                target_code = (child.target_language or "en").lower().split("-")[0].split("_")[0]
                native_code = (child.native_language or "ru").lower().split("-")[0].split("_")[0]
                if detected_code and target_code != native_code and detected_code == native_code:
                    status = "WRONG_LANGUAGE"
                    assessment.status = status
                    assessment.corrected_target = goal
                    assessment.feedback_native = (
                        f"Ответ понятен, но сейчас мы говорим на {language_name(child.target_language)}."
                    )
        except Exception:
            log.exception("Speech assessment failed; treating as technical uncertainty")
            assessment = None
            status = "TECHNICAL_UNCERTAINTY"

    # v56: when the object is already visible/known, a short meaningful sentence
    # such as "It is beautiful" is enough. Do not demand an extra invented detail.
    current_slide = next((item for item in lesson.get("slides", []) if item.get("slide_id") == data.get("current_slide_id")), None)
    if assessment and status == "RETRY_REQUIRED":
        transcript_words = [w for w in (assessment.transcript or "").strip().split() if w]
        if storage_phrase_id in {"penguin","parrot","lion","turtle","polar_bear","giraffe"} and len(transcript_words) >= 2:
            status = "ACCEPTED_BEST_ATTEMPT"
        elif current_slide and current_slide.get("open_answer") and transcript_words:
            status = "ACCEPTED_BEST_ATTEMPT"

    # v54: a mandatory movie line can never trap a child. Three voice recordings max.
    if required_phrase and recording_number >= 3 and status not in {"ACCEPTED_CORRECT", "ACCEPTED_BEST_ATTEMPT"}:
        status = "ACCEPTED_BEST_ATTEMPT"

    # After the simplified prompt, the next usable recording is always kept as the best attempt.
    if simplified_mode and status != "TECHNICAL_UNCERTAINTY":
        status = "ACCEPTED_BEST_ATTEMPT" if status != "ACCEPTED_CORRECT" else status

    # v31 conversation policy: if the answer is understandable, do not force a mechanical repeat.
    if assessment and not simplified_mode and status in {"RETRY_REQUIRED", "WRONG_LANGUAGE"}:
        policy = decide_retry(
            status=status,
            semantic_match=assessment.semantic_match,
            grammar_errors=assessment.grammar_errors or [],
            pronunciation_errors=assessment.pronunciation_errors or [],
            correction_count=correction_count,
        )
        if policy.accept_without_retry:
            status = "ACCEPTED_BEST_ATTEMPT"

    if status == "TECHNICAL_UNCERTAINTY":
        technical_count += 1
        if technical_count < 2:
            await state.update_data(technical_count=technical_count, recording_number=recording_number)
            await message.answer(f"Я не уверена, что правильно расслышала запись. Это не считается ошибкой. Послушай вариант ответа по теме задания и повтори: {simplified_text}")
            try:
                retry_audio = await synthesize_speech(
                    simplified_text,
                    child.target_language or "en",
                    settings.storage_root / "tts-cache",
                    f"retry_{storage_phrase_id}_{technical_count}",
                )
                if retry_audio:
                    await message.answer_voice(FSInputFile(retry_audio))
                else:
                    await message.answer("Голосовой пример сейчас недоступен. Скажи короткую фразу своими словами.")
            except Exception as exc:
                log.warning("Retry TTS failed: %s", exc)
                await message.answer("Голосовой пример сейчас недоступен. Скажи короткую фразу своими словами.")
            return
        # Never trap a child in an endless technical loop.
        status = "ACCEPTED_BEST_ATTEMPT"
        await message.answer(f"Я не смогла надёжно распознать запись. Сохраняю лучший вариант. Для тренировки правильный ответ по теме: {simplified_text}")

    elif status in {"WRONG_LANGUAGE", "RETRY_REQUIRED"} and not simplified_mode:
        policy = decide_retry(
            status=status,
            semantic_match=(assessment.semantic_match if assessment else 0.0),
            grammar_errors=(assessment.grammar_errors if assessment else []),
            pronunciation_errors=(assessment.pronunciation_errors if assessment else []),
            correction_count=correction_count,
        )
        correction_count += 1
        corrected = assessment.corrected_target or goal
        if policy.should_correct:
            if status == "WRONG_LANGUAGE":
                text = (
                    f"Я поняла твой ответ, но сейчас мы тренируем {language_name(child.target_language)}. "
                    f"Скажи это на {language_name(child.target_language)}.\n\n{corrected}"
                )
            else:
                feedback = assessment.feedback_native or "Чуть поправим и пойдём дальше."
                shown = corrected
                prefix = human_prefix(child_name=child.display_name or "", child_id=child.id, session_id=data.get("session_id") or 0, turn_key=f"correct:{storage_phrase_id}:{recording_number}")
                text = f"{prefix + ' ' if prefix else ''}{feedback}\n\nГотовый вариант: {shown}"
            await message.answer(text)
            try:
                audio = await synthesize_speech(corrected, child.target_language or "en", settings.storage_root / "tts-cache", f"correct_{storage_phrase_id}")
                if audio: await message.answer_voice(FSInputFile(audio))
            except AISpeechError:
                pass
            await state.update_data(
                correction_count=correction_count,
                attempt=correction_count,
                technical_count=0,
                recording_number=recording_number,
            )
            # Store this failed pedagogical attempt below, then wait for retry.
        else:
            simplified_source = simplified_text or corrected or goal
            simplified = await translate_text(simplified_source, "ru", child.target_language or "en")
            status = "SIMPLIFIED"
            await state.update_data(
                simplified_mode=True,
                current_simplified_text=simplified,
                current_goal=simplified,
                correction_count=correction_count,
                attempt=correction_count,
                technical_count=0,
                recording_number=recording_number,
            )
            native_instruction = await translate_text(
                "Сейчас повтори короткую фразу на изучаемом языке.", "ru", child.native_language or "ru"
            )
            prefix = human_prefix(child_name=child.display_name or "", child_id=child.id, session_id=data.get("session_id") or 0, turn_key=f"simplify:{storage_phrase_id}:{recording_number}")
            await message.answer(f"{prefix + ' ' if prefix else ''}{native_instruction}\n\n🌍 {simplified}")
            try:
                audio = await synthesize_speech(simplified, child.target_language or "en", settings.storage_root / "tts-cache", f"simple_{storage_phrase_id}")
                if audio: await message.answer_voice(FSInputFile(audio))
            except AISpeechError:
                pass

    activity("voice_assessed", tg_id=message.from_user.id, child_id=child.id, child_name=child.display_name, session_id=data.get("session_id"), slide_id=data.get("current_slide_id"), phrase_id=storage_phrase_id, status=status, transcript=(assessment.transcript if assessment else ""), detected_language=(assessment.detected_language if assessment else ""), semantic_match=(assessment.semantic_match if assessment else 0.0), correction_count=correction_count, simplified_mode=simplified_mode)

    # Build adaptive metrics even when the final recording is accepted as the best attempt.
    transcript = assessment.transcript if assessment else ""
    detected_language = assessment.detected_language if assessment else ""
    confidence = assessment.confidence if assessment else 0.0
    grammar_errors = assessment.grammar_errors if assessment else []
    pronunciation_errors = assessment.pronunciation_errors if assessment else []
    semantic_match = assessment.semantic_match if assessment else 0.0
    adaptive = score_answer(
        semantic_match=semantic_match,
        grammar_errors=grammar_errors,
        pronunciation_errors=pronunciation_errors,
        transcript=transcript,
        attempt_number=max(1, correction_count + 1),
        status=status,
    )
    async with SessionLocal() as db:
        db_child = await db.get(Child, child.id)
        if status != "TECHNICAL_UNCERTAINTY":
            n = db_child.answers_count or 0
            db_child.comprehension_score = update_running_average(db_child.comprehension_score or 0, n, adaptive.comprehension)
            db_child.grammar_score = update_running_average(db_child.grammar_score or 0, n, adaptive.grammar)
            db_child.vocabulary_score = update_running_average(db_child.vocabulary_score or 0, n, adaptive.vocabulary)
            db_child.pronunciation_score = update_running_average(db_child.pronunciation_score or 0, n, adaptive.pronunciation)
            db_child.fluency_score = update_running_average(db_child.fluency_score or 0, n, adaptive.fluency)
            db_child.independence_score = update_running_average(db_child.independence_score or 0, n, adaptive.independence)
            db_child.working_difficulty = clamp_difficulty((db_child.working_difficulty or .15) * .65 + adaptive.recommended_difficulty * .35)
            db_child.answers_count = n + 1
            overall = (db_child.comprehension_score + db_child.grammar_score + db_child.vocabulary_score + db_child.pronunciation_score + db_child.fluency_score + db_child.independence_score) / 6
            db_child.language_level = level_from_score(overall, db_child.language_level or "PRE_A1", db_child.answers_count)
        keep_audio = bool(data.get("current_required_phrase"))
        attempt_audio_path = str(wav_path) if keep_audio else ""
        db.add(VoiceAttempt(
            lesson_session_id=data["session_id"], phrase_id=storage_phrase_id, attempt_number=recording_number,
            audio_path=attempt_audio_path, status=status, transcript=transcript,
            detected_language=detected_language, confidence=confidence,
            grammar_errors=json.dumps(grammar_errors, ensure_ascii=False),
            pronunciation_errors=json.dumps(pronunciation_errors, ensure_ascii=False),
            semantic_match=semantic_match,
            comprehension_score=adaptive.comprehension, grammar_score=adaptive.grammar,
            vocabulary_score=adaptive.vocabulary, pronunciation_score=adaptive.pronunciation,
            fluency_score=adaptive.fluency, independence_score=adaptive.independence,
            recommended_difficulty=adaptive.recommended_difficulty,
        ))
        await db.commit()
    if not data.get("current_required_phrase"):
        for temp_path in (raw_path, wav_path):
            try:
                if temp_path.exists(): temp_path.unlink()
            except OSError:
                pass

    accepted = status in {"ACCEPTED_CORRECT", "ACCEPTED_BEST_ATTEMPT"}
    if accepted:
        # v56: deterministic completion of the suitcase voice line. Once the
        # Mini App said completed, never ask the learner to pack it again.
        if required_phrase and storage_phrase_id == "take_trip" and data.get("suitcase_completed"):
            lesson_now=load_lesson(_lesson_id(data)); slides_now=lesson_now.get("slides") or []
            current_idx=int(data.get("slide_step",0)); slide_now=slides_now[current_idx] if current_idx < len(slides_now) else {}
            next_step=_resolve_next_step(slides_now,current_idx,slide_now)
            await message.answer("⭐")
            await _persist_step(data.get("session_id"),next_step)
            await _reset_for_next_step(state,next_step=next_step)
            await send_step(message,state)
            return

        # v56: animal compare is a two-question mini state machine. Each correct
        # choice MUST be followed by a voice line about that SAME animal. After
        # the voice line, either ask the second pair question or advance.
        if required_phrase and data.get("animal_compare_resume"):
            completed=list(data.get("completed_animal_voice") or [])
            if storage_phrase_id not in completed:
                completed.append(storage_phrase_id)
            pair_id=str(data.get("animal_compare_pair_id") or "")
            q_index=int(data.get("animal_compare_question_index",0) or 0)
            await message.answer("⭐")
            if q_index < 1:
                await state.update_data(
                    completed_animal_voice=completed, animal_compare_question_index=q_index+1,
                    animal_compare_resume=False, selected_animal=None, current_phrase_id=None,
                    current_required_phrase=False, required_phrase_owner_slide_id=None,
                    current_goal=None,current_simplified_text=None,current_accepted_meaning=[],
                    correction_count=0,technical_count=0,recording_number=0,simplified_mode=False,
                    last_emission_key=None,
                )
                await send_step(message,state)
                return
            lesson_now=load_lesson(_lesson_id(data)); slides_now=lesson_now.get("slides") or []
            current_idx=int(data.get("slide_step",0)); slide_now=slides_now[current_idx] if current_idx < len(slides_now) else {}
            next_step=_resolve_next_step(slides_now,current_idx,slide_now)
            await _persist_step(data.get("session_id"),next_step)
            await _reset_for_next_step(state,next_step=next_step)
            await state.update_data(completed_animal_voice=completed)
            await send_step(message,state)
            return

        # The answer just received resolves any previously pending follow-up.
        if data.get("followup_pending"):
            await state.update_data(followup_pending=False, followup_slide_id=None)
            data = await state.get_data()
        # Decide whether the AI response is a follow-up question before sending it.
        # Any question the bot asks must block progression until the child answers.
        current_slide_id = data.get("current_slide_id")
        lesson_now = load_lesson(_lesson_id(data))
        slide_now = next((x for x in lesson_now.get("slides", []) if x.get("slide_id") == current_slide_id), None)
        response_target = (assessment.response_target or "").strip() if assessment else ""
        response_native = (assessment.response_native or "").strip() if assessment else ""
        is_question = response_target.endswith("?") or response_target.endswith("？")
        followup_count = int(data.get("ai_followup_count", 0))
        configured_max = int((slide_now or {}).get("max_ai_followups", 1))
        max_followups = adapted_followup_limit(
            configured_max=max(1, configured_max),
            working_difficulty=child.working_difficulty or 0.15,
            answer_score=(adaptive.recommended_difficulty if adaptive else 0.0),
        )
        other_flow_active = bool(data.get("card_questions") or data.get("object_sequence"))
        should_wait_for_followup = bool((slide_now or {}).get("allow_ai_followup") is True and response_target and is_question and followup_count < max_followups and not other_flow_active)

        # Send normal praise/comment. Send a question only when we are about to wait for its answer.
        if assessment and response_target and (not is_question or should_wait_for_followup):
            await message.answer(response_target)
            try:
                audio = await synthesize_speech(
                    response_target,
                    child.target_language or "en",
                    settings.storage_root / "tts-cache",
                    f"reply_{storage_phrase_id}_{recording_number}",
                )
                if audio:
                    await message.answer_voice(FSInputFile(audio))
            except AISpeechError:
                pass
        if assessment and response_native and child.native_language != child.target_language and (not is_question or should_wait_for_followup):
            await message.answer(response_native)

        if should_wait_for_followup:
            await state.update_data(
                ai_followup_count=followup_count + 1,
                followup_pending=True,
                followup_slide_id=current_slide_id,
                current_phrase_id=f"practice_followup_{current_slide_id}_{followup_count + 1}",
                current_goal=response_target,
                current_simplified_text=response_target,
                current_accepted_meaning=[],
                current_required_phrase=False,
                current_max_voice_seconds=60,
                correction_count=0,
                technical_count=0,
                recording_number=0,
                simplified_mode=False,
            )
            await message.answer(
                "🎙 Я слушаю. Следующий шаг появится только после твоего ответа.",
                reply_markup=lesson_voice_keyboard(child.native_language or "en", allow_skip=True),
            )
            await state.set_state(LessonFlow.waiting_voice)
            return
        if slide_now and slide_now.get("post_required_phrase_id") and not data.get("post_required_started"):
            req_id = slide_now["post_required_phrase_id"]
            req = next(x for x in lesson_now["required_phrases"] if x["phrase_id"] == req_id)
            # Give the planned Madagascar/clothes explanation before the cartoon line.
            if current_slide_id == "slide_19":
                script = "Лёша приехал на жаркий Мадагаскар в очень тёплой одежде: шапке, куртке, варежках и сапогах. Ему будет жарко. Скажи на изучаемом языке: It's hot."
                await send_bot_speech(message, child, script, script, "madagascar_clothes_explain")
            req_target, _ = await send_bot_speech(message, child, req["target_text"], req.get("native_hint", ""), req_id)
            req_simplified = await translate_text(req.get("simplified_text") or req["target_text"], "ru", child.target_language or "en")
            await message.answer("Эта короткая реплика войдёт в мультфильм. Запиши её до 5 секунд.", reply_markup=lesson_voice_keyboard(child.native_language or "en", allow_skip=False))
            await state.update_data(current_phrase_id=req_id,current_goal=req_target,current_simplified_text=req_simplified,
                current_accepted_meaning=req.get("accepted_meaning") or [],current_required_phrase=True,current_max_voice_seconds=5,
                required_phrase_owner_slide_id=current_slide_id, post_required_started=True,correction_count=0,technical_count=0,recording_number=0,simplified_mode=False)
            return
        if data.get("object_sequence"):
            sequence = list(data.get("object_sequence") or [])
            pos = int(data.get("object_position", 0)) + 1
            if pos < len(sequence):
                item = sequence[pos]
                target = await translate_text(item["label_ru"], "ru", child.target_language or "en")
                native = await translate_text(item["label_ru"], "ru", child.native_language or "ru")
                await message.answer(f"{item['icon']} {item['letter']}: 🌍 {target}\n💬 {native}\nПовтори голосом.", reply_markup=lesson_voice_keyboard(child.native_language or "en", allow_skip=True))
                try:
                    audio = await synthesize_speech(target, child.target_language or "en", settings.storage_root / "tts-cache", f"object32_{pos}")
                    if audio: await message.answer_voice(FSInputFile(audio))
                except AISpeechError: pass
                await state.update_data(object_position=pos, current_phrase_id=f"practice_object32_{pos}", current_goal=item["label_ru"], current_simplified_text=item["label_ru"], current_accepted_meaning=[item["label_ru"]], current_required_phrase=False, current_max_voice_seconds=60, correction_count=0, technical_count=0, recording_number=0, simplified_mode=False)
                return
            await message.answer("⭐ Задание выполнено!")
            await state.update_data(object_sequence=None, object_position=0)
        if data.get("card_questions"):
            questions = list(data.get("card_questions") or [])
            index = int(data.get("card_question_index", 0)) + 1
            if index < len(questions):
                question = questions[index]
                target_question = await translate_text(question, "ru", child.target_language or "en")
                native_question = await translate_text(question, "ru", child.native_language or "ru")
                await send_bot_speech(message, child, question, question, f"card_{data.get('selected_card','x')}_{index}")
                await message.answer(
                    f"🌍 {target_question}\n💬 {native_question}\n\n🎙 Я слушаю. Следующий вопрос появится только после твоего ответа.",
                    reply_markup=lesson_voice_keyboard(child.native_language or "en", allow_skip=True),
                )
                await state.update_data(
                    card_question_index=index, current_goal=target_question, current_simplified_text=target_question,
                    current_phrase_id=f"practice_card_{data.get('selected_card','x')}_{index}",
                    current_required_phrase=False, current_max_voice_seconds=60,
                    correction_count=0, technical_count=0, recording_number=0, simplified_mode=False,
                )
                await state.set_state(LessonFlow.waiting_voice)
                return
            await state.update_data(card_questions=None, card_question_index=0, selected_card=None, card_pending=False)
        if data.get("post_voice_jump") is not None:
            next_step = int(data.get("post_voice_jump"))
        else:
            lesson_slides = lesson_now.get("slides") or []
            next_step = _resolve_next_step(lesson_slides, int(data.get("slide_step", 0)), slide_now)
        await message.answer("⭐" if status == "ACCEPTED_CORRECT" else "👏")
        await _persist_step(data.get("session_id"), next_step)
        await _reset_for_next_step(state,next_step=next_step)
        await send_step(message, state)
    elif status == "SIMPLIFIED":
        return
    else:
        # RETRY_REQUIRED / WRONG_LANGUAGE: exactly three pedagogical corrections maximum.
        return




@router.message(LessonFlow.waiting_webapp, F.voice)
async def waiting_webapp_voice_guard(message: Message, state: FSMContext):
    """Scope WebApp gating to the CURRENT task only.

    v57 critical fix: suitcase gating must never leak into animal compare tasks.
    """
    data=await state.get_data()
    slide_id=str(data.get("current_slide_id") or "")
    if slide_id == "slide_24":
        if data.get("suitcase_completed") and data.get("current_phrase_id") == "take_trip":
            await state.set_state(LessonFlow.waiting_voice)
            await receive_voice(message,state)
            return
        await message.answer("🧳 Сначала закончи сбор чемодана. После кнопки «Чемодан собран» я сразу приму голосовую фразу.")
        return
    if data.get("animal_compare_pending"):
        await message.answer("🐾 Сначала выбери животное на картинке. После выбора я попрошу короткую фразу именно про него.")
        return
    # Never invent a suitcase requirement for unrelated WebApp tasks.
    await message.answer("Сначала закончи текущее задание на экране.")

@router.message(LessonFlow.waiting_webapp, ~F.web_app_data)
async def waiting_webapp_message_guard(message: Message, state: FSMContext):
    """Keep only the actual current WebApp task blocked."""
    data = await state.get_data()
    slide_id=str(data.get("current_slide_id") or "")
    if slide_id == "slide_24":
        if data.get("suitcase_completed"):
            await message.answer("🎙 Чемодан уже собран. Теперь жду только короткую голосовую фразу.")
            return
        if data.get("suitcase_pending"):
            await message.answer("🧳 Сначала закончи сбор чемодана и нажми «Чемодан собран». После этого я попрошу одну короткую фразу.")
        return
    if data.get("animal_compare_pending"):
        await message.answer("🐾 Сначала выбери животное на картинке.")
        return

@router.message(F.web_app_data)
async def receive_webapp_data(message: Message, state: FSMContext):
    data = await state.get_data()
    child = await get_child_from_state_or_user(state, message.from_user.id)
    try:
        early_payload = json.loads(message.web_app_data.data)
    except Exception:
        early_payload = {}
    if child and early_payload.get("type") == "free_topic_task" and data.get("free_topic_lesson"):
        idx=int(data.get("free_topic_step",0)); slides=(data.get("free_topic_lesson") or {}).get("slides") or []; current=slides[idx] if idx<len(slides) else {}
        canonical=canonical_content_type(current.get('type')); canonical={'handwriting_screen':'trace','draw':'trace','drawing':'trace','matching':'match_visible'}.get(canonical,canonical)
        expected_instance=str(data.get('authored_session_id') or ('hw:'+str(data.get('authored_homework_assignment_id') or '')) or data.get('free_topic_key') or '')
        if str(early_payload.get('instance') or '') != expected_instance:
            await message.answer("Это окно относится к другому уроку или старой сессии. Открой текущее задание заново."); return
        if int(early_payload.get("step",-1)) != idx:
            await message.answer("Это результат предыдущего задания — текущее задание осталось на месте."); return
        if early_payload.get('completed') is not True or str(early_payload.get('task_type') or '') != canonical:
            await message.answer("Задание ещё не завершено корректно — остаёмся на текущем шаге."); return
        stats=dict(data.get('authored_stats') or {}); stats['interactive_tasks']=int(stats.get('interactive_tasks',0) or 0)+1
        await message.answer("✅ Задание выполнено!", reply_markup=ReplyKeyboardRemove())
        await state.update_data(free_topic_step=idx+1,authored_stats=stats); await _persist_authored_step(state,idx+1)
        await _send_free_topic_step(message,state,child); return
    if child and early_payload.get("type") == "course_task" and data.get("session_id"):
        if not data.get("generic_task_pending"):
            await message.answer("Это задание уже завершено.", reply_markup=ReplyKeyboardRemove()); return
        lesson = load_lesson(_lesson_id(data))
        slides = lesson.get("slides") or []
        idx = int(data.get("slide_step", 0))
        current = slides[idx] if idx < len(slides) else {}
        if int(early_payload.get("step", -1)) != idx or early_payload.get("slide") != current.get("slide_id"):
            await message.answer("Это результат предыдущего задания — текущее задание осталось на месте."); return
        await state.update_data(generic_task_pending=False, slide_step=idx+1)
        await _persist_step(data.get("session_id"), idx+1)
        await message.answer("✅ Задание выполнено!", reply_markup=ReplyKeyboardRemove())
        await send_step(message, state); return
    if not child or not data.get("session_id"):
        await message.answer("Открой задание из активного урока."); return
    try:
        payload = json.loads(message.web_app_data.data)
    except Exception:
        await message.answer("Не удалось прочитать результат задания."); return
    if payload.get("type") == "animal_compare":
        if not data.get("animal_compare_pending"):
            await message.answer("Это задание уже завершено.", reply_markup=ReplyKeyboardRemove()); return
        task=data.get("animal_compare_task") or {}
        if payload.get("pair") != task.get("pair_id") or payload.get("selected") != task.get("correct"):
            await message.answer("Попробуй ещё раз."); return
        if str(payload.get("slide") or "") != str(data.get("current_slide_id") or ""):
            await message.answer("Это результат предыдущего задания. Текущее задание осталось на месте."); return
        try:
            payload_qi=int(payload.get("qi",-1)); task_qi=int(task.get("question_index",-2))
        except Exception:
            payload_qi=-1; task_qi=-2
        if payload_qi != task_qi:
            await message.answer("Это результат предыдущего вопроса. Открой текущие карточки ещё раз."); return
        from app.services.animal_compare import NAMES_RU
        animal=str(payload.get("selected"))
        animal_name=await translate_text(NAMES_RU.get(animal,animal),"ru",child.target_language or "ru")
        await message.answer("✨ "+animal_name+"!", reply_markup=ReplyKeyboardRemove())
        try:
            audio=await synthesize_speech(animal_name,child.target_language or "ru",settings.storage_root/"tts-cache","animal_name")
            if audio: await message.answer_voice(FSInputFile(audio))
        except Exception: pass
        phrase_id=animal
        lesson=load_lesson(_lesson_id(data))
        completed=set(data.get("completed_animal_voice") or [])
        # If this animal already supplied its phrase earlier in the same lesson, do not
        # ask it again. Continue the compare state machine instead.
        if animal in completed:
            if task_qi < 1:
                await state.update_data(animal_compare_pending=False,animal_compare_task=None,animal_compare_question_index=task_qi+1,last_emission_key=None)
                await send_step(message,state); return
            slides_now=lesson.get("slides") or []; current_idx=int(data.get("slide_step",0)); slide_now=slides_now[current_idx]
            next_step=_resolve_next_step(slides_now,current_idx,slide_now)
            await _persist_step(data.get("session_id"),next_step)
            await _reset_for_next_step(state,next_step=next_step)
            await state.update_data(completed_animal_voice=list(completed))
            await send_step(message,state); return
        phrase=next(x for x in lesson["required_phrases"] if x["phrase_id"]==phrase_id)
        cartoon_phrase_ids={str(x.get("phrase_id")) for x in (lesson.get("timeline") or []) if x.get("phrase_id")}
        is_cartoon_phrase=phrase_id in cartoon_phrase_ids
        goal,hint=await send_bot_speech(message,child,phrase["target_text"],phrase.get("native_hint") or "Скажи одну короткую фразу.",phrase_id+"_after_compare")
        if is_cartoon_phrase:
            prompt="🎬 Скажи одну короткую фразу про это животное — до 5 секунд. Эта реплика нужна для мультфильма."
        else:
            prompt="🎙 Скажи одну короткую фразу про это животное — до 5 секунд. Если не хочешь записывать, задание можно пропустить."
        await message.answer(prompt, reply_markup=lesson_voice_keyboard(child.native_language or "ru",allow_skip=not is_cartoon_phrase,skip_token=str(data.get("current_slide_id") or "animal")))
        simplified=await translate_text(phrase.get("simplified_text") or phrase["target_text"],"ru",child.target_language or "ru")
        await state.update_data(
            selected_animal=animal, animal_compare_pending=False, animal_compare_task=None,
            current_phrase_id=phrase_id,current_goal=goal,current_accepted_meaning=phrase.get("accepted_meaning") or [],
            current_simplified_text=simplified,current_required_phrase=is_cartoon_phrase,current_max_voice_seconds=5,
            required_phrase_owner_slide_id=(data.get("current_slide_id") if is_cartoon_phrase else None),
            animal_compare_resume=True,animal_compare_pair_id=task.get("pair_id"),animal_compare_question_index=task_qi,
            correction_count=0,technical_count=0,recording_number=0,simplified_mode=False,
        )
        await state.set_state(LessonFlow.waiting_voice); return

    if payload.get("type") != "suitcase":
        activity("webapp_ignored", tg_id=message.from_user.id, child_id=child.id, session_id=data.get("session_id"), payload_type=payload.get("type"))
        return
    activity("suitcase_result", tg_id=message.from_user.id, child_id=child.id, child_name=child.display_name, session_id=data.get("session_id"), selected=payload.get("selected", []), score=payload.get("score", 0))
    from app.db.models import InteractiveResult
    async with SessionLocal() as db:
        db.add(InteractiveResult(lesson_session_id=data["session_id"], slide_id="slide_24", task_type="suitcase", result_json=json.dumps(payload, ensure_ascii=False), score=float(payload.get("score",0))))
        await db.commit()
    selected_ids = [str(x) for x in payload.get("selected", [])]
    ru_names = {
        "jacket":"куртку", "binoculars":"бинокль", "water":"бутылку воды", "compass":"компас",
        "teddy":"мишку", "camera":"фотоаппарат", "telescope":"телескоп", "fish":"рыбу",
        "notebook":"блокнот", "sunglasses":"солнцезащитные очки",
    }
    max_items = 1 if (child.language_level or "PRE_A1") == "PRE_A1" else (2 if (child.language_level or "").startswith("A1") else 3)
    chosen_ru = [ru_names.get(item, item) for item in selected_ids[:max_items]]
    if not chosen_ru:
        await message.answer("Сначала положи хотя бы один предмет в чемодан.")
        return
    phrase_ru = "Я возьму с собой " + ", ".join(chosen_ru) + "."
    await message.answer("✅ Чемодан собран!", reply_markup=ReplyKeyboardRemove())
    target_goal, native_hint = await send_bot_speech(message, child, phrase_ru, "Чемодан собран. Повтори фразу о выбранных вещах.", "take_trip_prompt")
    await message.answer(
        "🎬 Эта фраза войдёт в мультфильм. Запиши её голосом до 5 секунд. Я обязательно дождусь ответа.",
        reply_markup=lesson_voice_keyboard(child.native_language or "en", allow_skip=False),
    )
    simplified_ru = "Я возьму " + chosen_ru[0] + "."
    simplified_target = await translate_text(simplified_ru, "ru", child.target_language or "en")
    await state.update_data(
        current_phrase_id="take_trip", current_goal=target_goal,
        current_accepted_meaning=selected_ids, current_simplified_text=simplified_target,
        current_required_phrase=True, current_max_voice_seconds=5, current_slide_id="slide_24",
        attempt=0, correction_count=0, technical_count=0, recording_number=0, simplified_mode=False,
        suitcase_pending=False, suitcase_completed=True, required_phrase_owner_slide_id="slide_24",
    )
    await state.set_state(LessonFlow.waiting_voice)


@router.message(LessonFlow.waiting_voice)
async def voice_required(message: Message, state: FSMContext):
    child = await get_child_from_state_or_user(state, message.from_user.id)
    await message.answer(tr(child.native_language if child else "ru", "voice_required"))


@router.message(Command("mobile"))
async def mobile_pair_command(message: Message, state: FSMContext):
    """Standalone DOME uses email/password; Telegram pairing is retired."""
    await message.answer(
        "📱 DOME Mobile теперь работает как самостоятельное приложение.\n\n"
        "Установите приложение и войдите по email и паролю. Код привязки Telegram больше не нужен."
    )
