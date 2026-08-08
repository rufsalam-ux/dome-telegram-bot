from __future__ import annotations

import shutil
import json
import logging
from urllib.parse import urlencode
from datetime import datetime
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message, ReplyKeyboardRemove
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
    homework_keyboard,
    free_topic_payment_keyboard,
    free_topic_step_keyboard,
    free_topic_choice_keyboard,
    free_topic_webapp_keyboard,
    free_topic_finished_keyboard,
    course_payment_gate_keyboard,
    gender_keyboard,
    support_keyboard,
)
from app.bot.states import LessonFlow, Onboarding, SettingsFlow, ConsentFlow, FreeTopicFlow
from app.core.config import settings
from app.core.i18n import language_name, tr
from app.db.models import Character, Child, LessonSession, Parent, VoiceAttempt, ConsentRecord, HomeworkAssignment, CourseEnrollment
from app.db.session import SessionLocal
from app.services.ai_speech import AISpeechError, synthesize_speech, translate_text
from app.services.audio_processing import prepare_child_voice
from app.services.speech_pipeline import assess_speech
from app.services.adaptive_learning import score_answer, update_running_average, level_from_score, adapt_prompt
from app.services.conversation_engine import decide_retry, adapted_followup_limit, clamp_difficulty, human_prefix
from app.services.slide_renderer import render_slide
from app.services.email_reports import build_progress_report, send_progress_report
from app.services.cartoon_builder import CartoonBuildError, build_timeline_cartoon
from app.services.character_processor import CharacterProcessingError, process_character
from app.services.lesson_loader import load_lesson
from app.services.lesson_revision import normalize_lesson_step, next_runtime_step
from app.services.sms_consent import send_verification, check_verification, SMSConsentError
from app.services.activity_log import activity
from app.services.homework import resolve_homework
from app.services.free_topic_builder import build_free_topic_lesson, save_free_topic_lesson
from app.services.free_topic_media import ensure_free_topic_image, ensure_free_topic_clip
from app.services.free_topic_cartoon import build_free_topic_cartoon, FreeTopicCartoonError
from app.services.platform_settings import load_settings
from app.services.course_scheduler import choose_next_lesson, first_active_course_id, course_for_lesson
from app.services.cartoon_credit import add_cartoon_credit
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

async def _next_scheduled_lesson_id(child: Child) -> str:
    course_id = first_active_course_id() or "demo_english"
    async with SessionLocal() as db:
        completed = (await db.scalars(select(LessonSession.lesson_id).where(
            LessonSession.child_id == child.id, LessonSession.status == "COMPLETED"
        ).order_by(LessonSession.id.asc()))).all()
    return choose_next_lesson(course_id, completed) or LESSON_ID



async def _has_course_access(child_id: int, course_id: str) -> bool:
    """Return True when the child has active paid/test access to this course."""
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


async def _show_parent_course_payment_gate(message: Message, state: FSMContext, child: Child, lesson_id: str, course_id: str) -> None:
    payments = load_settings("payments")
    allow_test = bool(payments.get("allow_test_course_payment_bypass", False))
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
        return await db.scalar(
            select(Child).where(Child.parent_id == parent.id).order_by(Child.id.desc())
        )


async def get_child_from_state_or_user(state: FSMContext, tg_id: int) -> Child | None:
    data = await state.get_data()
    child_id = data.get("child_id")
    if child_id:
        async with SessionLocal() as db:
            child = await db.get(Child, int(child_id))
            if child:
                return child
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


@router.message(Command("version"))
async def version_command(message: Message):
    await message.answer("DOME v38 FREE TOPIC REAL LESSON + CARTOON\nУроки и ДЗ редактируются с компьютера через Control Center.")

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
    await cb.message.answer(tr(child.native_language, "child_menu_title"), reply_markup=child_menu_keyboard(child.native_language or "en", show_free_topic=interest_on))
    await cb.answer()


@router.callback_query(F.data == "menu:parent")
async def menu_parent(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    await cb.message.answer(tr(child.native_language, "parent_menu_title"), reply_markup=parent_menu_keyboard(child.native_language or "en"))
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
    lesson=await build_free_topic_lesson(topic,target_language=child.target_language or 'en',native_language=child.native_language or 'ru',age=getattr(child,'age_years',None),level=child.language_level or 'PRE_A1',slide_count=21)
    path=save_free_topic_lesson(child.id,lesson); lesson_key=Path(path).stem
    await state.update_data(free_topic_lesson=lesson,free_topic_step=0,free_topic_path=str(path),free_topic_key=lesson_key,free_topic_payment_mode=payment_mode,free_topic_skip_busy=False,free_topic_voice_files=[],free_topic_images=[],character_id=child.active_character_id,free_topic_run=1,free_topic_cartoon_count=0,free_topic_cartoon_cost_total=0.0,free_topic_attempts={})
    await state.set_state(FreeTopicFlow.playing); await _send_free_topic_step(message,state,child)

async def _finish_free_topic(message: Message,state:FSMContext,child:Child):
    data=await state.get_data()
    images=[Path(x) for x in data.get('free_topic_images',[]) if x]
    voices=[Path(x) for x in data.get('free_topic_voice_files',[]) if x]
    run_no=max(1,int(data.get('free_topic_run',1)))
    cartoon_count=max(0,int(data.get('free_topic_cartoon_count',0)))
    cartoon_cost_total=float(data.get('free_topic_cartoon_cost_total',0.0) or 0.0)
    ft_cfg=load_settings('free_topic')
    estimated=float(ft_cfg.get('estimated_cartoon_cost_usd',2.5) or 2.5)
    max_two=float(ft_cfg.get('max_two_cartoons_cost_usd',5.0) or 5.0)
    make_cartoon=(run_no <= 2 and cartoon_count < 2 and cartoon_cost_total + estimated <= max_two + 1e-9)
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
                try: add_cartoon_credit(out,out,child.display_name,getattr(child,'gender',None))
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

    can_repeat=run_no < 3
    await state.set_state(None)
    await message.answer(
        f'Пройдено {run_no}/3.' + (f' Можно пройти ещё {3-run_no} раз.' if can_repeat else ' Все 3 прохождения завершены.'),
        reply_markup=free_topic_finished_keyboard(child.native_language or 'ru',can_repeat=can_repeat),
    )


@router.callback_query(F.data == 'freetopic:repeat')
async def free_topic_repeat(cb: CallbackQuery, state: FSMContext):
    child=await get_child_from_state_or_user(state,cb.from_user.id); data=await state.get_data()
    if not child or not data.get('free_topic_lesson'):
        await cb.answer('Сначала выбери урок на свободную тему.',show_alert=True); return
    run_no=max(1,int(data.get('free_topic_run',1)))
    if run_no>=3:
        await cb.answer('Этот урок уже пройден 3/3.',show_alert=True); return
    run_no += 1
    # The same purchased lesson is replayed; answers/voice recordings are collected anew.
    await state.update_data(free_topic_run=run_no,free_topic_step=0,free_topic_voice_files=[],free_topic_skip_busy=False,free_topic_attempts={})
    await state.set_state(FreeTopicFlow.playing)
    await cb.answer(f'Прохождение {run_no}/3')
    await _send_free_topic_step(cb.message,state,child)


async def _send_free_topic_step(message: Message, state: FSMContext, child: Child):
    data=await state.get_data(); lesson=data.get('free_topic_lesson') or {}; slides=lesson.get('slides') or []; idx=int(data.get('free_topic_step',0))
    if idx>=len(slides): return await _finish_free_topic(message,state,child)
    s=slides[idx]; kind=s.get('type','passive'); expects=bool(s.get('expects_answer')); topic=lesson.get('topic','')
    # Real illustration for the current stage (AI generated once and cached; local illustrated card if image API fails).
    image=None
    try:
        image=await ensure_free_topic_image(child.id,str(data.get('free_topic_key')),s,str(topic))
        imgs=list(data.get('free_topic_images',[]));
        if str(image) not in imgs: imgs.append(str(image)); await state.update_data(free_topic_images=imgs)
    except Exception: pass
    caption=f"{idx+1}/{len(slides)} · {s.get('title','')}\n\n{s.get('prompt') or s.get('teacher_instruction') or ''}"
    support=str(s.get('support_text') or '').strip()
    if support: caption += f"\n\n💬 {support}"
    # Narrate every stage aloud.
    try:
        audio=await synthesize_speech(str(s.get('audio_text') or s.get('prompt') or s.get('title') or ''),child.target_language or 'en',settings.storage_root/'children'/str(child.id)/'free-topic-media'/str(data.get('free_topic_key'))/'tts',f'slide_{idx+1}')
        if audio: await message.answer_voice(FSInputFile(audio))
        if support and (child.native_language or 'ru') != (child.target_language or 'en'):
            native_audio=await synthesize_speech(support,child.native_language or 'ru',settings.storage_root/'children'/str(child.id)/'free-topic-media'/str(data.get('free_topic_key'))/'tts-native',f'native_{idx+1}')
            if native_audio: await message.answer_voice(FSInputFile(native_audio))
    except Exception: pass
    can_skip=bool(s.get('can_skip',True)) and not bool(s.get('required_cartoon_line'))
    if kind in {'choice','mini_game'} and s.get('options'):
        if image: await message.answer_photo(FSInputFile(image),caption=caption,reply_markup=free_topic_choice_keyboard(idx,list(s.get('options') or []),child.native_language or 'ru',can_skip))
        else: await message.answer(caption,reply_markup=free_topic_choice_keyboard(idx,list(s.get('options') or []),child.native_language or 'ru',can_skip))
        await state.set_state(FreeTopicFlow.waiting_answer)
    elif kind in {'drag_drop','memory'}:
        base=settings.effective_webapp_base_url
        items=[str(x) for x in (s.get('items') or [])][:5]; targets=[str(x) for x in (s.get('targets') or [])][:5]
        if base:
            image_url=(base+f"/free-topic-media/{child.id}/{data.get('free_topic_key')}/{Path(image).name}") if image else ''
            url=base+'/free-topic-task?'+urlencode({'type':kind,'title':str(s.get('title','')),'prompt':str(s.get('prompt','')),'items':'|'.join(items),'targets':'|'.join(targets),'step':idx,'image':image_url})
            label='🧩 Открыть задание' if kind=='drag_drop' else '🃏 Открыть Memory'
            if image: await message.answer_photo(FSInputFile(image),caption=caption,reply_markup=free_topic_webapp_keyboard(label,url,child.native_language or 'ru'))
            else: await message.answer(caption,reply_markup=free_topic_webapp_keyboard(label,url,child.native_language or 'ru'))
            await state.set_state(FreeTopicFlow.waiting_answer)
        else:
            await message.answer_photo(FSInputFile(image),caption=caption) if image else await message.answer(caption)
            await state.set_state(FreeTopicFlow.waiting_answer)
    elif kind=='video' and image:
        clip=settings.storage_root/'children'/str(child.id)/'free-topic-media'/str(data.get('free_topic_key'))/f'video_{idx+1}.mp4'
        made=ensure_free_topic_clip(Path(image),clip,seconds=6)
        if made:
            await message.answer_video(FSInputFile(made),caption=caption,reply_markup=free_topic_step_keyboard(child.native_language or 'ru',expects_answer=False,can_skip=can_skip,step=idx))
        else:
            await message.answer_photo(FSInputFile(image),caption=caption,reply_markup=free_topic_step_keyboard(child.native_language or 'ru',expects_answer=False,can_skip=can_skip,step=idx))
        await state.set_state(FreeTopicFlow.playing)
    elif kind in {'voice_answer','roleplay'}:
        if image: await message.answer_photo(FSInputFile(image),caption=caption)
        else: await message.answer(caption)
        target_phrase=str(s.get('target_phrase') or '').strip()
        extra=(f'🎬 Обязательная реплика для мультфильма. Скажи на изучаемом языке:\n«{target_phrase}»\nЗапиши голосом до 5 секунд.' if s.get('required_cartoon_line') else '🎙 Ответь голосом на изучаемом языке.')
        await message.answer(extra,reply_markup=free_topic_step_keyboard(child.native_language or 'ru',expects_answer=True,can_skip=can_skip,step=idx))
        if target_phrase:
            try:
                sample=await synthesize_speech(target_phrase,child.target_language or 'en',settings.storage_root/'children'/str(child.id)/'free-topic-media'/str(data.get('free_topic_key'))/'tts',f'cartoon_line_{idx+1}')
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
    await state.update_data(free_topic_step=idx+1); await cb.answer(); await _send_free_topic_step(cb.message,state,child)

@router.callback_query(F.data.startswith('freetopic:choice:'))
async def free_topic_choice(cb:CallbackQuery,state:FSMContext):
    child=await get_child_from_state_or_user(state,cb.from_user.id); data=await state.get_data()
    if not child: return
    parts=cb.data.split(':'); step=int(parts[2]); choice_idx=int(parts[3]); idx=int(data.get('free_topic_step',0))
    if step!=idx: await cb.answer('Это уже предыдущее задание.'); return
    slides=(data.get('free_topic_lesson') or {}).get('slides') or []; s=slides[idx] if idx<len(slides) else {}
    correct=s.get('correct_option_index')
    attempts=dict(data.get('free_topic_attempts') or {}); attempt=int(attempts.get(str(idx),0))+1; attempts[str(idx)]=attempt
    await state.update_data(free_topic_attempts=attempts)
    if correct is not None and choice_idx!=int(correct) and attempt<2:
        await cb.answer('Попробуй ещё раз.',show_alert=True); return
    await cb.answer('✅'); await state.update_data(free_topic_step=idx+1); await _send_free_topic_step(cb.message,state,child)

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
    if s.get('required_cartoon_line') or not s.get('can_skip',True): await state.update_data(free_topic_skip_busy=False); await cb.answer('Это обязательная реплика для мультфильма — её нужно записать.',show_alert=True); return
    # Advance exactly once. Old/stale skip buttons cannot skip a later step because skip_busy stays set until next screen is rendered.
    await cb.answer('Пропускаю одно задание'); await state.update_data(free_topic_step=idx+1); await _send_free_topic_step(cb.message,state,child)

@router.message(FreeTopicFlow.waiting_answer)
async def free_topic_answer(message: Message, state: FSMContext):
    child=await get_child_from_state_or_user(state,message.from_user.id); data=await state.get_data()
    if not child: return
    slides=(data.get('free_topic_lesson') or {}).get('slides') or []; idx=int(data.get('free_topic_step',0))
    if idx>=len(slides): return
    s=slides[idx]; kind=str(s.get('type') or '')
    if kind=='drawing':
        if not (message.photo or message.document):
            await message.answer('🎨 Пришли фото рисунка или изображение, затем я покажу следующий этап.'); return
        await message.answer('✅ Рисунок получен!')
        await state.update_data(free_topic_step=idx+1)
        await _send_free_topic_step(message,state,child); return
    if kind not in {'voice_answer','roleplay'}:
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
    attempt=int(attempts.get(str(idx),0))+1
    attempts[str(idx)]=attempt
    await state.update_data(free_topic_attempts=attempts)
    goal=str(s.get('target_phrase') or s.get('prompt') or s.get('teacher_instruction') or '')
    accepted=list(s.get('accepted_meaning') or ([goal] if goal else []))
    assessment=await assess_speech(
        wav, child.target_language or 'en', child.native_language or 'ru', goal, accepted, attempt,
        child_name=child.display_name or '', working_difficulty=0.35,
    )
    status=assessment.status
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
        return
    if status in {'WRONG_LANGUAGE','RETRY_REQUIRED'} and attempt >= 3:
        # Required cartoon lines still need a usable target-language recording; do not save a wrong-language/noise take.
        if s.get('required_cartoon_line'):
            corrected=(assessment.corrected_target or goal).strip()
            await message.answer('Для мультфильма нужна понятная реплика на изучаемом языке.' + (f' Скажи: «{corrected}»' if corrected else ''))
            return

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
        await message.answer(response or '✅ Хорошо, идём дальше.')
    # Interactive answer completion advances immediately. No old Continue button is required.
    await state.update_data(free_topic_step=idx+1)
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
    async with SessionLocal() as db:
        child = Child(parent_id=parent.id, display_name=message.text.strip())
        db.add(child)
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
    await cb.message.edit_text(tr(native, "character_source"), reply_markup=character_source_keyboard(native))
    await state.set_state(Onboarding.character_choice)
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
    await cb.message.answer(("Выберите тариф. После выбора откроется безопасная страница оплаты." if (child.native_language or "ru") == "ru" else "Choose a plan. A secure payment page will open next."), reply_markup=payment_plans_keyboard(child.native_language or "en"))
    await cb.answer()



@router.callback_query(F.data == "course_payment:plans")
async def course_payment_plans(cb: CallbackQuery, state: FSMContext):
    child = await get_child_from_state_or_user(state, cb.from_user.id)
    if child is None:
        await cb.answer("/start", show_alert=True); return
    await cb.message.answer(
        "👩 Родителю: выберите пакет для оплаты. После подтверждённой оплаты доступ к курсу открывается ребёнку.",
        reply_markup=payment_plans_keyboard(child.native_language or "ru"),
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
    await _show_checkout(cb.message, child, plan)
    await cb.answer()

async def _show_checkout(message: Message, child: Child, plan: str) -> None:
    urls = {
        "trial": settings.payment_url_trial or settings.payment_url,
        "group": settings.payment_url_group or settings.payment_url,
        "individual": settings.payment_url_individual or settings.payment_url,
    }
    names = {"trial":"Пробный урок", "group":"Групповой тариф", "individual":"Индивидуальный тариф"}
    url = urls.get(plan, "")
    if not url:
        await message.answer("Ссылка оплаты для этого тарифа не настроена. Добавьте её в .env — инструкция есть в docs/PAYMENTS_AND_SMS_RU.md.")
        return
    await message.answer(
        f"Выбран тариф: {names.get(plan, plan)}. Оплата поступит в аккаунт платёжного провайдера, который создал эту ссылку.",
        reply_markup=payment_checkout_keyboard("Перейти к безопасной оплате", url, child.native_language or "ru"),
    )

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
    lesson_id = await _next_scheduled_lesson_id(child)
    try:
        lesson = load_lesson(lesson_id)
        title = lesson.get("title") or lesson_id
    except Exception:
        lesson_id = LESSON_ID
        title = "Р2-1 — первый разговорный урок"
    await state.update_data(selected_lesson_id=lesson_id)
    await cb.message.answer(f"Следующий урок: {title}", reply_markup=start_lesson_keyboard(child.native_language or "en"))
    await cb.answer()


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
    source_language = child.target_language or "en"
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
        await message.answer(f"AI voice error: {exc}")
        return target_text, native_hint


async def send_step(message: Message, state: FSMContext):
    await state.update_data(skip_in_progress=False)
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
        async with SessionLocal() as db:
            completed_before = len((await db.scalars(select(LessonSession).where(
                LessonSession.child_id == child.id,
                LessonSession.lesson_id == lesson_id,
                LessonSession.status == "COMPLETED",
            ))).all())
            character = await db.get(Character, data["character_id"])
            attempts = (await db.scalars(select(VoiceAttempt).where(
                VoiceAttempt.lesson_session_id == data["session_id"],
                VoiceAttempt.status.in_(["ACCEPTED_CORRECT", "ACCEPTED_BEST_ATTEMPT"]),
            ).order_by(VoiceAttempt.id))).all()
            session = await db.get(LessonSession, data["session_id"])
            session.status = "RENDERING" if completed_before == 0 else "COMPLETED"
            if completed_before > 0:
                from datetime import datetime
                session.completed_at = datetime.utcnow()
                session.level_at_end = child.language_level
            await db.commit()
        if completed_before > 0:
            async with SessionLocal() as db:
                db_child = await db.get(Child, child.id)
                parent = await db.get(Parent, db_child.parent_id)
                all_attempts = (await db.scalars(select(VoiceAttempt).where(VoiceAttempt.lesson_session_id == data["session_id"]))).all()
                completed_count = len((await db.scalars(select(LessonSession).where(LessonSession.child_id == db_child.id, LessonSession.status == "COMPLETED"))).all())
            await message.answer("⭐ Урок завершён. Повторные прохождения идут без нового мультфильма.")
            await _create_and_send_homework(message, child, data["session_id"], all_attempts, completed_count, parent, lesson_id=lesson_id)
            await message.answer("Главное меню", reply_markup=menu_hub_keyboard(child.native_language or "en"))
            await state.set_state(None)
            return
        audio_by_phrase = {attempt.phrase_id: Path(attempt.audio_path) for attempt in attempts}
        output = settings.storage_root / "children" / str(child.id) / "cartoons" / f"lesson_{data['session_id']}.mp4"
        base_video = settings.content_root / "lessons" / lesson_id / lesson["cartoon_base"]
        activity("render_start", tg_id=message.chat.id, child_id=child.id, child_name=child.display_name, session_id=data.get("session_id"), output=str(output), accepted_voice_count=len(attempts))
        await message.answer("🎬 Собираю твой мультфильм…")
        try:
            build_timeline_cartoon(base_video, Path(character.processed_path), audio_by_phrase, lesson["timeline"], output)
            try:
                add_cartoon_credit(output, output, child.display_name, getattr(child, "gender", None))
            except Exception as exc:
                log.warning("Cartoon credit failed: %s", exc)
            async with SessionLocal() as db:
                session = await db.get(LessonSession, data["session_id"])
                db_child = await db.get(Child, child.id)
                session.status = "COMPLETED"
                session.level_at_end = db_child.language_level
                from datetime import datetime
                session.completed_at = datetime.utcnow()
                parent = await db.get(Parent, db_child.parent_id)
                all_attempts = (await db.scalars(select(VoiceAttempt).where(VoiceAttempt.lesson_session_id == session.id))).all()
                completed_lessons = len((await db.scalars(select(LessonSession).where(LessonSession.child_id == db_child.id, LessonSession.status == "COMPLETED"))).all()) + 1
                await db.commit()
                report_data = (parent.email, parent.email_reports_enabled, db_child, completed_lessons, all_attempts)
            activity("render_success", tg_id=message.chat.id, child_id=child.id, child_name=child.display_name, session_id=data.get("session_id"), output=str(output))
            await message.answer_video(FSInputFile(output), caption="🎬")
            email, enabled, report_child, completed_count, report_attempts = report_data
            async with SessionLocal() as db:
                db_child = await db.get(Child, child.id)
                report_parent = await db.get(Parent, db_child.parent_id)
            await _create_and_send_homework(message, report_child, data["session_id"], report_attempts, completed_count, report_parent, lesson_id=lesson_id)
            if enabled and email:
                await message.answer("Итоги урока и домашнее задание отправлены родителю на email.")
            await message.answer("Главное меню", reply_markup=menu_hub_keyboard(child.native_language or "en"))
        except CartoonBuildError as exc:
            activity("render_error", tg_id=message.chat.id, child_id=child.id, child_name=child.display_name, session_id=data.get("session_id"), error=str(exc), output=str(output))
            log.exception("Cartoon render failed for session=%s output=%s", data.get("session_id"), output)
            await message.answer("🎬 Мультфильм пока не собрался. Урок сохранён, а техническая ошибка записана в журнал. Попробуйте открыть мультфильм позже.", reply_markup=menu_hub_keyboard(child.native_language or "en"))
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
        )
        caption = f"{slide.get('order', slide_step + 1)}/{len(slides)}\n\n🌍 {slide.get('bot_says_target','')}"
        if slide.get("bot_explains_native"):
            caption += f"\n💬 {slide.get('bot_explains_native')}"
        await message.answer_photo(FSInputFile(localized_image), caption=caption, reply_markup=free_topic_webapp_keyboard("Открыть задание", url, child.native_language or "ru"))
        await state.update_data(current_slide_id=slide.get("slide_id"), generic_task_pending=True)
        await state.set_state(LessonFlow.waiting_webapp)
        return

    # The suitcase is completed inside Telegram Mini App first. Only after the
    # child presses Done do we request the mandatory cartoon voice phrase.
    if slide.get("interactive_task") == "suitcase":
        target = slide.get("bot_says_target", "")
        native = slide.get("bot_explains_native", "")
        spoken_target, spoken_native = await send_bot_speech(message, child, target, native, slide["slide_id"])
        caption = f"{slide.get('order', slide_step + 1)}/49\n\n🌍 {spoken_target}"
        if spoken_native:
            caption += f"\n💬 {spoken_native}"
        rendered_image = await render_slide(
            image_path,
            settings.storage_root / "slide-cache",
            character_path=(Path((await _get_character_path(data.get("character_id")))) if data.get("character_id") else None),
            character_box=slide.get("character_box"),
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
        await state.update_data(current_slide_id=slide.get("slide_id"), suitcase_pending=True, current_required_phrase=True)
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
        caption = f"{slide.get('order', slide_step + 1)}/49\n\n🌍 {target_text}"
        if spoken_native:
            caption += f"\n💬 {spoken_native}"
        caption += f"\n\n{tr(child.native_language, 'record_voice')}"
        localized_image = await render_slide(
            image_path,
            settings.storage_root / "slide-cache",
            character_path=(Path((await _get_character_path(data.get("character_id")))) if data.get("character_id") else None),
            character_box=slide.get("character_box"),
        )
        await message.answer_photo(
            FSInputFile(localized_image), caption=caption,
            reply_markup=lesson_voice_keyboard(child.native_language or "en", allow_skip=(slide.get("slide_id") == "slide_19" and not phrase_id) or (bool(slide.get("allow_skip")) and slide.get("slide_id") != "slide_24" and not phrase_id))
        )
        await state.update_data(
            current_phrase_id=storage_phrase_id,
            current_goal=target_text,
            current_accepted_meaning=accepted_meaning,
            current_simplified_text=simplified_target,
            current_slide_id=slide.get("slide_id"),
            current_required_phrase=bool(phrase_id),
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
    caption = f"{slide.get('order', slide_step + 1)}/49\n\n🌍 {spoken_target}"
    if spoken_native: caption += f"\n💬 {spoken_native}"
    localized_image = await render_slide(
            image_path,
            settings.storage_root / "slide-cache",
            character_path=(Path((await _get_character_path(data.get("character_id")))) if data.get("character_id") else None),
            character_box=slide.get("character_box"),
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
    await message.answer_photo(FSInputFile(localized_image), caption=caption, reply_markup=markup)
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
    if data.get("suitcase_pending") or (step < len(slides) and slides[step].get("slide_id") == "slide_24"):
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


async def _resume_or_start_lesson(message: Message, state: FSMContext, child: Child) -> None:
    state_data = await state.get_data()
    lesson_id = _lesson_id(state_data) if (state_data.get("selected_lesson_id") or state_data.get("current_lesson_id")) else await _next_scheduled_lesson_id(child)
    if not child.active_character_id:
        await message.answer(tr(child.native_language, "character_source"), reply_markup=character_source_keyboard(child.native_language or "en")); return
    async with SessionLocal() as db:
        active = await db.scalar(select(LessonSession).where(
            LessonSession.child_id == child.id, LessonSession.lesson_id == lesson_id,
            LessonSession.status.in_(["IN_PROGRESS", "RENDERING"]),
        ).order_by(LessonSession.id.desc()))
        completed_count = len((await db.scalars(select(LessonSession).where(
            LessonSession.child_id == child.id, LessonSession.lesson_id == lesson_id,
            LessonSession.status == "COMPLETED",
        ))).all())
        if active:
            session = active
            if session.status == "RENDERING": session.status = "IN_PROGRESS"
        else:
            course_id = course_for_lesson(lesson_id) or first_active_course_id() or "demo_english"
            payments = load_settings("payments")
            if payments.get("require_course_payment_before_lesson", True) and not await _has_course_access(child.id, course_id):
                await _show_parent_course_payment_gate(message, state, child, lesson_id, course_id)
                return
            if completed_count >= 3:
                await message.answer("Этот урок уже пройден три раза и больше недоступен."); return
            session = LessonSession(child_id=child.id, lesson_id=lesson_id,
                level_at_start=child.language_level or "PRE_A1", lesson_revision=21)
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
        db_session.current_step = normalized_step; db_session.lesson_revision = 21; db_session.status = "IN_PROGRESS"
        await db.commit()
    await state.clear()
    await state.update_data(child_id=child.id, session_id=session.id, character_id=child.active_character_id,
        current_lesson_id=lesson_id, selected_lesson_id=lesson_id, slide_step=normalized_step, attempt=0)
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
        await cb.answer("Нет незавершённого урока. Откройте «Доступные уроки», чтобы начать.", show_alert=True); return
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

@router.callback_query(F.data == "lesson:skip")
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
    # The suitcase phrase is mandatory regardless of stale buttons/messages.
    if slide.get("slide_id") == "slide_24" or data.get("current_phrase_id") == "take_trip":
        await state.update_data(skip_in_progress=False); await cb.answer("Фраза про чемодан нужна для мультфильма и её нельзя пропустить.", show_alert=True); return
    if data.get("current_required_phrase"):
        await state.update_data(skip_in_progress=False); await cb.answer("Эта фраза нужна для мультфильма и её нельзя пропустить.", show_alert=True); return
    if slide.get("slide_id") == "slide_09":
        new_step = int(data.get("post_voice_jump") or 14)
    else:
        new_step = next_runtime_step(slides, step)
    await state.update_data(
        slide_step=new_step, card_questions=None, card_question_index=0,
        selected_card=None, card_pending=False, post_voice_jump=None,
        current_phrase_id=None, current_required_phrase=False,
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
        post_voice_jump=14,
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
    storage_phrase_id = data["current_phrase_id"]
    phrase = next((item for item in lesson["required_phrases"] if item["phrase_id"] == storage_phrase_id), None)
    goal = data.get("current_goal") or (phrase.get("target_text", "") if phrase else "")
    accepted_meaning = data.get("current_accepted_meaning") or (phrase.get("accepted_meaning") or phrase.get("choices") if phrase else [])
    simplified_text = data.get("current_simplified_text") or (phrase.get("simplified_text") if phrase else goal) or goal
    required_phrase = bool(data.get("current_required_phrase"))
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
                shown = syllabify_phrase(corrected) if correction_count == 1 else corrected
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
        should_wait_for_followup = bool(response_target and is_question and followup_count < max_followups and not other_flow_active)

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
                post_required_started=True,correction_count=0,technical_count=0,recording_number=0,simplified_mode=False)
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
            next_step = next_runtime_step(lesson_slides, int(data.get("slide_step", 0)))
        await message.answer("⭐" if status == "ACCEPTED_CORRECT" else "👏")
        await _persist_step(data.get("session_id"), next_step)
        await state.update_data(
            slide_step=next_step,
            attempt=0,
            correction_count=0,
            technical_count=0,
            recording_number=0,
            simplified_mode=False,
            current_phrase_id=None,
            current_required_phrase=False,
            current_max_voice_seconds=60,
            post_voice_jump=None,
            post_required_started=False,
            ai_followup_count=0,
            followup_pending=False,
        )
        await send_step(message, state)
    elif status == "SIMPLIFIED":
        return
    else:
        # RETRY_REQUIRED / WRONG_LANGUAGE: exactly three pedagogical corrections maximum.
        return




@router.message(LessonFlow.waiting_webapp, ~F.web_app_data)
async def suitcase_waiting_message(message: Message, state: FSMContext):
    """Keep slide 24 blocked until Telegram delivers WebApp data."""
    data = await state.get_data()
    if not data.get("suitcase_pending"):
        return
    await message.answer(
        "🧳 Сначала нажми «Собрать чемодан», выбери предметы и нажми «Чемодан собран». "
        "После этого я попрошу обязательную фразу для мультфильма."
    )

@router.message(F.web_app_data)
async def receive_webapp_data(message: Message, state: FSMContext):
    data = await state.get_data()
    child = await get_child_from_state_or_user(state, message.from_user.id)
    try:
        early_payload = json.loads(message.web_app_data.data)
    except Exception:
        early_payload = {}
    if child and early_payload.get("type") == "free_topic_task" and data.get("free_topic_lesson"):
        idx=int(data.get("free_topic_step",0))
        if int(early_payload.get("step",-1)) != idx:
            await message.answer("Это результат предыдущего задания — текущее задание осталось на месте."); return
        await message.answer("✅ Задание выполнено!", reply_markup=ReplyKeyboardRemove())
        await state.update_data(free_topic_step=idx+1)
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
        "compass": "компас", "camera": "фотоаппарат", "phone": "телефон",
        "teddy": "мишку", "flower": "цветок", "telescope": "телескоп",
        "fish": "рыбу", "jacket": "куртку",
    }
    max_items = 1 if (child.language_level or "PRE_A1") == "PRE_A1" else (2 if (child.language_level or "").startswith("A1") else 3)
    chosen_ru = [ru_names.get(item, item) for item in selected_ids[:max_items]]
    if not chosen_ru:
        await message.answer("Сначала положи хотя бы один предмет в чемодан.")
        return
    phrase_ru = "Я возьму с собой " + ", ".join(chosen_ru) + "."
    target_goal = await translate_text(phrase_ru, "ru", child.target_language or "en")
    native_hint = await translate_text(
        "Чемодан собран. Повтори фразу о выбранных вещах.", "ru", child.native_language or "ru"
    )
    await message.answer("✅ Чемодан собран!", reply_markup=ReplyKeyboardRemove())
    await send_bot_speech(message, child, target_goal, native_hint, "take_trip_prompt")
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
        suitcase_pending=False,
    )
    await state.set_state(LessonFlow.waiting_voice)


@router.message(LessonFlow.waiting_voice)
async def voice_required(message: Message, state: FSMContext):
    child = await get_child_from_state_or_user(state, message.from_user.id)
    await message.answer(tr(child.native_language if child else "ru", "voice_required"))
