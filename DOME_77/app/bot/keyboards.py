from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

from app.core.i18n import LANGUAGES, tr


ICON_MAP = {
    "кошка": "🐱", "собака": "🐶", "медведь": "🧸", "мишка": "🧸", "компас": "🧭",
    "фотоаппарат": "📷", "телефон": "📱", "цветок": "🌼", "куртка": "🧥", "рыба": "🐟",
    "телескоп": "🔭", "вентилятор": "🌀", "пирог": "🥧", "сумка": "👜", "аптечка": "🩹",
    "остров": "🏝️", "чемодан": "🧳", "айсберг": "🧊", "баобаб": "🌳", "флаг": "🏳️",
}

def _with_icon(label: str) -> str:
    low = label.lower().strip()
    for key, icon in ICON_MAP.items():
        if key in low:
            return f"{icon} {label}"
    if label in {"A", "А"}: return "🅰️ " + label
    if label == "Б": return "🅱️ " + label
    if label == "В": return "🖼️ " + label
    if label == "Г": return "👥 " + label
    if label == "Д": return "🐰 " + label
    if label == "Е": return "⭐ " + label
    if label == "Ж": return "🏳️ " + label
    return "🖼️ " + label


def language_keyboard(prefix: str, native_language: str = "ru") -> InlineKeyboardMarkup:
    rows = []
    items = list(LANGUAGES.items())
    for i in range(0, len(items), 2):
        rows.append([
            InlineKeyboardButton(text=name, callback_data=f"{prefix}:{code}")
            for code, name in items[i:i + 2]
        ])
    rows.append([InlineKeyboardButton(text=tr(native_language, "back_menu"), callback_data="menu:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_menu_keyboard(language: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr(language, "continue_lesson"), callback_data="lesson:continue")],
        [InlineKeyboardButton(text=tr(language, "change_languages"), callback_data="menu:languages")],
        [InlineKeyboardButton(text=tr(language, "my_character"), callback_data="menu:character")],
        [InlineKeyboardButton(text=tr(language, "available_lessons"), callback_data="menu:lessons")],
        [InlineKeyboardButton(text=tr(language, "my_progress"), callback_data="menu:progress")],
        [InlineKeyboardButton(text=tr(language, "profile"), callback_data="menu:profile")],
        [InlineKeyboardButton(text=tr(language, "settings"), callback_data="menu:settings")],
        [InlineKeyboardButton(text=("💳 Привязать карту" if language == "ru" else "💳 Link payment card"), callback_data="menu:payment")],
    ])


def character_source_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr(language, "upload_drawing"), callback_data="character_source:upload")],
        [InlineKeyboardButton(text=tr(language, "choose_other_preset"), callback_data="character_source:preset")],
        [InlineKeyboardButton(text=tr(language, "back_menu"), callback_data="menu:open")],
    ])


def character_menu_keyboard(language: str, allow_preset_change: bool) -> InlineKeyboardMarkup:
    rows = []
    if allow_preset_change:
        rows.append([InlineKeyboardButton(text=tr(language, "choose_other_preset"), callback_data="character_source:preset")])
    rows.append([InlineKeyboardButton(text=tr(language, "upload_drawing"), callback_data="character_source:upload")])
    rows.append([InlineKeyboardButton(text=tr(language, "keep_character"), callback_data="menu:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def preset_character_keyboard(characters: list[dict], language: str = "ru") -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for character in characters:
        row.append(InlineKeyboardButton(text=character["title"], callback_data=f"preset_character:{character['id']}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text=tr(language, "upload_drawing"), callback_data="character_source:upload")])
    rows.append([InlineKeyboardButton(text=tr(language, "back_menu"), callback_data="menu:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_character_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅", callback_data="character:confirm")],
        [InlineKeyboardButton(text=tr(language, "upload_drawing"), callback_data="character:retry")],
        [InlineKeyboardButton(text=tr(language, "choose_other_preset"), callback_data="character_source:preset")],
    ])


def start_lesson_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr(language, "start_lesson"), callback_data="lesson:start")],
        [InlineKeyboardButton(text=tr(language, "back_menu"), callback_data="menu:open")],
    ])


def lesson_next_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr(language, "next_slide"), callback_data="lesson:next")],
        [InlineKeyboardButton(text=tr(language, "back_menu"), callback_data="menu:open")],
    ])


def suitcase_webapp_keyboard(language: str, url: str) -> ReplyKeyboardMarkup:
    # Telegram WebApp.sendData is reliably delivered to the bot when the Mini App
    # is opened from a reply-keyboard WebApp button.
    label = "🧳 Собрать чемодан" if language == "ru" else "🧳 Pack suitcase"
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label, web_app=WebAppInfo(url=url))]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder=("Открой чемодан" if language == "ru" else "Open the suitcase"),
    )


def animal_compare_webapp_keyboard(language: str, url: str) -> ReplyKeyboardMarkup:
    labels={"ru":"🐾 Выбрать животное","en":"🐾 Choose animal","es":"🐾 Elegir animal","de":"🐾 Tier wählen","fr":"🐾 Choisir l’animal","it":"🐾 Scegli animale"}
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=labels.get(language,labels["en"]),web_app=WebAppInfo(url=url))]],resize_keyboard=True,one_time_keyboard=True)

def payment_prompt_keyboard(language: str, payment_url: str, allow_skip: bool = True) -> InlineKeyboardMarkup:
    """Payment/card binding prompt. Skip is temporary and can be disabled later."""
    rows: list[list[InlineKeyboardButton]] = []
    label = "💳 Привязать карту" if language == "ru" else "💳 Link payment card"
    if payment_url:
        rows.append([InlineKeyboardButton(text=label, url=payment_url)])
    else:
        rows.append([InlineKeyboardButton(text=label, callback_data="payment:not_configured")])
    if allow_skip:
        skip = "Пропустить пока" if language == "ru" else "Skip for now"
        rows.append([InlineKeyboardButton(text=skip, callback_data="payment:skip")])
    rows.append([InlineKeyboardButton(text=tr(language, "back_menu"), callback_data="menu:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)



def course_payment_gate_keyboard(language: str = "ru", *, allow_test_bypass: bool = False) -> InlineKeyboardMarkup:
    ru = language == "ru"
    rows = [
        [InlineKeyboardButton(text=("💳 Оплатить курс / пакет" if ru else "💳 Pay for course / package"), callback_data="course_payment:plans")],
    ]
    if allow_test_bypass:
        rows.append([InlineKeyboardButton(text=("🧪 Пропустить оплату — тест" if ru else "🧪 Skip payment — test"), callback_data="course_payment:test_bypass")])
    rows.append([InlineKeyboardButton(text=("👩 Меню родителя" if ru else "👩 Parent menu"), callback_data="menu:parent")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def lesson_voice_keyboard(language: str = "ru", *, allow_skip: bool = False, skip_token: str | None = None) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=("🆘 Помощь" if language == "ru" else "🆘 Help"), callback_data="lesson:help")]]
    if allow_skip:
        rows.append([InlineKeyboardButton(text=("⏭ Пропустить задание" if language == "ru" else "⏭ Skip task"), callback_data=(f"lesson:skip:{skip_token}" if skip_token else "lesson:skip"))])
    rows.append([InlineKeyboardButton(text=tr(language, "back_menu"), callback_data="menu:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def card_choice_keyboard(options: list[str], language: str = "ru") -> InlineKeyboardMarkup:
    """Slide 9 is mandatory: show card choices without Next/Skip controls."""
    rows=[]
    for i in range(0,len(options),3):
        rows.append([InlineKeyboardButton(text=_with_icon(str(x)), callback_data=f"lesson:card:{i+j}") for j,x in enumerate(options[i:i+3])])
    rows.append([InlineKeyboardButton(text=("🆘 Помощь" if language == "ru" else "🆘 Help"), callback_data="lesson:help")])
    rows.append([InlineKeyboardButton(text=tr(language, "back_menu"), callback_data="menu:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def choice_items_keyboard(prefix: str, items: list[str], language: str = "ru") -> InlineKeyboardMarkup:
    rows=[]
    for i,item in enumerate(items):
        rows.append([InlineKeyboardButton(text=_with_icon(item), callback_data=f"{prefix}:{i}")])
    rows.append([InlineKeyboardButton(text=("🆘 Помощь" if language == "ru" else "🆘 Help"), callback_data="lesson:help")])
    rows.append([InlineKeyboardButton(text=("⏭ Пропустить" if language == "ru" else "⏭ Skip"), callback_data="lesson:skip")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mood_keyboard(items: list[str], language: str = "ru") -> InlineKeyboardMarkup:
    emoji=["🙂","😠","😁","😄","😍","😔"]
    rows=[]
    for i,item in enumerate(items):
        rows.append([InlineKeyboardButton(text=f"{emoji[i] if i < len(emoji) else '🙂'} {item}", callback_data=f"lesson:mood:{i}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_plans_keyboard(language: str, *, additional_child_discount_per_lesson: float = 0.0, course_id: str | None = None) -> InlineKeyboardMarkup:
    from app.services.platform_settings import load_settings
    from app.services.pricing_versions import is_plan_profitable, plan_versions_for_course
    cfg=load_settings("pricing"); plans=plan_versions_for_course(course_id)
    family=(cfg.get("family") or {}); monthly_weeks=max(1,int(family.get("billing_weeks_per_month",4) or 4));annual_weeks=max(monthly_weeks,int(family.get("billing_weeks_per_year",52) or 52))
    rows=[]
    for p in sorted(plans,key=lambda x:(str(x.get("billing_period")),int(x.get("lessons_per_week",1)))):
        f=int(p.get("lessons_per_week",1));period=str(p.get("billing_period") or "MONTH").upper();weeks=annual_weeks if period=="YEAR" else monthly_weeks;base=float(p.get("price",0));price=max(0.0,round(base-float(additional_child_discount_per_lesson or 0)*f*weeks,2));suffix="год" if period=="YEAR" else "мес";label=f"{f} урок{'а' if f in {2,3,4} else ''} в неделю · €{price:g}/{suffix}"
        if not is_plan_profitable(p,effective_price=price,course_id=course_id):continue
        rows.append([InlineKeyboardButton(text=label, callback_data=f"payment:plan:{period}:weekly{f}")])
    rows.append([InlineKeyboardButton(text=tr(language, "back_menu"), callback_data="menu:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def consent_agreement_keyboard(consent_type: str, language: str = "ru") -> InlineKeyboardMarkup:
    ru = language == "ru"
    agree = "✅ Согласна, подтвердить по SMS" if ru else "✅ I agree, confirm by SMS"
    back = tr(language, "back_menu")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=agree, callback_data=f"consent:accept:{consent_type}")],
        [InlineKeyboardButton(text=back, callback_data="menu:open")],
    ])


def payment_checkout_keyboard(label: str, url: str, language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, url=url)],
        [InlineKeyboardButton(text=tr(language, "back_menu"), callback_data="menu:open")],
    ])


def plan_change_confirmation_keyboard(plan_id: str, language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подтвердить изменение тарифа", callback_data=f"payment:confirm_plan:{plan_id}")],
        [InlineKeyboardButton(text=tr(language, "back_menu"), callback_data="menu:open")],
    ])


def plan_change_cancel_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отменить изменение тарифа", callback_data="payment:cancel_plan_change")],
        [InlineKeyboardButton(text=tr(language, "back_menu"), callback_data="menu:open")],
    ])



def menu_hub_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr(language, "child_zone"), callback_data="menu:child")],
        [InlineKeyboardButton(text=tr(language, "parent_zone"), callback_data="menu:parent")],
    ])


def child_menu_keyboard(language: str = "ru", *, show_free_topic: bool = False) -> InlineKeyboardMarkup:
    ru = language == "ru"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("▶ Продолжить" if ru else "▶ Continue"), callback_data="lesson:continue")],
        [InlineKeyboardButton(text=("📚 Мои курсы" if ru else "📚 My courses"), callback_data="menu:lessons_child")],
        [InlineKeyboardButton(text=("⭐ Мои успехи" if ru else "⭐ My progress"), callback_data="menu:progress")],
    ])


def parent_menu_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    ru = language == "ru"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("👨‍👩‍👧‍👦 Дети" if ru else "👨‍👩‍👧‍👦 Children"), callback_data="menu:children")],
        [InlineKeyboardButton(text=("👤 Ребёнок" if ru else "👤 Child"), callback_data="menu:profile")],
        [InlineKeyboardButton(text=("📚 Курсы" if ru else "📚 Courses"), callback_data="menu:lessons")],
        [InlineKeyboardButton(text=("📊 Прогресс" if ru else "📊 Progress"), callback_data="menu:progress_parent")],
        [InlineKeyboardButton(text=("🎟 Доступ / промокод" if ru else "🎟 Access / promo code"), callback_data="menu:pilot_access")],
        [InlineKeyboardButton(text=("⚙ Настройки" if ru else "⚙ Settings"), callback_data="menu:settings")],
        [InlineKeyboardButton(text=tr(language, "back_menu"), callback_data="menu:open")],
    ])



def family_children_keyboard(children: list[tuple[int,str]], active_child_id: int | None, language: str = "ru", *, can_add: bool = True) -> InlineKeyboardMarkup:
    ru=language=="ru"
    rows=[]
    for child_id,name in children:
        prefix="✅ " if int(child_id)==int(active_child_id or 0) else "👤 "
        rows.append([InlineKeyboardButton(text=prefix+str(name), callback_data=f"family:select:{child_id}")])
    if can_add:
        rows.append([InlineKeyboardButton(text=("➕ Добавить ребёнка" if ru else "➕ Add child"), callback_data="family:add")])
    rows.append([InlineKeyboardButton(text=("👩 Меню родителя" if ru else "👩 Parent menu"), callback_data="menu:parent")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def course_list_keyboard(courses: list[tuple[str, str]], language: str = "ru") -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"📘 {title}", callback_data=f"course:select:{course_id}")] for course_id, title in courses]
    rows.append([InlineKeyboardButton(text=tr(language, "back_menu"), callback_data="menu:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def age_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=str(n), callback_data=f"age:{n}") for n in (3,4,5)],
        [InlineKeyboardButton(text=str(n), callback_data=f"age:{n}") for n in (6,7,8)],
        [InlineKeyboardButton(text=str(n), callback_data=f"age:{n}") for n in (9,10,11)],
        [InlineKeyboardButton(text=str(n), callback_data=f"age:{n}") for n in (12,13,14)],
        [InlineKeyboardButton(text=("15+"), callback_data="age:15")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def open_webapp_keyboard(label: str, url: str, language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, url=url)],
        [InlineKeyboardButton(text=tr(language, "back_menu"), callback_data="menu:open")],
    ])


def homework_keyboard(homework_id: int, language: str = "ru") -> InlineKeyboardMarkup:
    ru = language == "ru"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("▶️ Выполнить сейчас" if ru else "▶️ Do now"), callback_data=f"homework:do:{homework_id}")],
        [InlineKeyboardButton(text=("🕒 Сделать позже" if ru else "🕒 Later"), callback_data=f"homework:later:{homework_id}")],
        [InlineKeyboardButton(text=("✕ Пропустить" if ru else "✕ Skip"), callback_data=f"homework:skip:{homework_id}")],
        [InlineKeyboardButton(text=("📚 Архив домашних заданий" if ru else "📚 Homework archive"), callback_data="homework:archive")],
    ])



def free_topic_payment_keyboard(language: str = "ru", allow_test_bypass: bool = True) -> InlineKeyboardMarkup:
    rows=[[InlineKeyboardButton(text=("💳 Оплатить урок" if language=="ru" else "💳 Pay for lesson"), callback_data="freepay:pay")]]
    if allow_test_bypass:
        rows.append([InlineKeyboardButton(text=("⏭ Пропустить оплату — тест" if language=="ru" else "⏭ Skip payment — test"), callback_data="freepay:bypass")])
    rows.append([InlineKeyboardButton(text=tr(language,"back_menu"),callback_data="menu:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def free_topic_step_keyboard(language: str = "ru", *, expects_answer: bool, can_skip: bool = True, step: int | None = None) -> InlineKeyboardMarkup:
    rows=[]
    suffix = f":{step}" if step is not None else ""
    if not expects_answer:
        rows.append([InlineKeyboardButton(text=("➡️ Дальше" if language=="ru" else "➡️ Next"),callback_data=f"freetopic:next{suffix}")])
    if can_skip:
        rows.append([InlineKeyboardButton(text=("⏭ Пропустить" if language=="ru" else "⏭ Skip"),callback_data=f"freetopic:skip{suffix}")])
    rows.append([InlineKeyboardButton(text=tr(language,"back_menu"),callback_data="menu:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def free_topic_choice_keyboard(step:int, options:list[str], language:str="ru", can_skip:bool=True) -> InlineKeyboardMarkup:
    rows=[[InlineKeyboardButton(text=str(opt),callback_data=f"freetopic:choice:{step}:{i}")] for i,opt in enumerate(options[:6])]
    if can_skip: rows.append([InlineKeyboardButton(text=("⏭ Пропустить" if language=="ru" else "⏭ Skip"),callback_data=f"freetopic:skip:{step}")])
    rows.append([InlineKeyboardButton(text=tr(language,"back_menu"),callback_data="menu:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def free_topic_webapp_keyboard(label:str,url:str,language:str="ru") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=label,web_app=WebAppInfo(url=url))]],resize_keyboard=True,one_time_keyboard=True)


def free_topic_finished_keyboard(language: str = "ru", *, can_repeat: bool = True) -> InlineKeyboardMarkup:
    rows=[]
    if can_repeat:
        rows.append([InlineKeyboardButton(text=("🔁 Пройти этот урок ещё раз" if language=="ru" else "🔁 Repeat this lesson"), callback_data="freetopic:repeat")])
    rows.append([InlineKeyboardButton(text=tr(language,"back_menu"), callback_data="menu:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def gender_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    ru = language == "ru"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("👧 Девочка" if ru else "👧 Girl"), callback_data="gender:female")],
        [InlineKeyboardButton(text=("👦 Мальчик" if ru else "👦 Boy"), callback_data="gender:male")],
        [InlineKeyboardButton(text=("🙂 Не указывать" if ru else "🙂 Prefer not to say"), callback_data="gender:neutral")],
    ])



def reading_ability_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    ru = language == "ru"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("📖 Да, читает" if ru else "📖 Yes, can read"), callback_data="reading:yes")],
        [InlineKeyboardButton(text=("🔤 Немного" if ru else "🔤 A little"), callback_data="reading:basic")],
        [InlineKeyboardButton(text=("👂 Пока не читает" if ru else "👂 Not yet"), callback_data="reading:no")],
    ])

def support_keyboard(language: str = "ru", chat_url: str = "", call_url: str = "") -> InlineKeyboardMarkup:
    ru = language == "ru"
    rows=[]
    if chat_url:
        rows.append([InlineKeyboardButton(text=("💬 Написать в поддержку" if ru else "💬 Contact support"), url=chat_url)])
    if call_url:
        rows.append([InlineKeyboardButton(text=("📞 Заказать звонок" if ru else "📞 Request a call"), url=call_url)])
    rows.append([InlineKeyboardButton(text=("❓ Частые вопросы" if ru else "❓ FAQ"), callback_data="support:faq")])
    rows.append([InlineKeyboardButton(text=tr(language,"back_menu"), callback_data="menu:parent")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def course_lessons_keyboard(course_id: str, lessons: list[tuple[str,str,str]], language: str = "ru") -> InlineKeyboardMarkup:
    rows=[]
    for lesson_id,title,status in lessons:
        rows.append([InlineKeyboardButton(text=f"{status} {title}", callback_data=f"lessonpick:{course_id}:{lesson_id}")])
    rows.append([InlineKeyboardButton(text=tr(language,"back_menu"), callback_data="menu:lessons")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def course_transition_keyboard(course_id: str, options: list[tuple[str, str]], *, repeat_allowed: bool = True, language: str = "ru") -> InlineKeyboardMarkup:
    rows=[]
    for cid,title in options:
        rows.append([InlineKeyboardButton(text=f"➡️ {title}", callback_data=f"course_transition:{course_id}:{cid}")])
    if repeat_allowed:
        rows.append([InlineKeyboardButton(text=("🔁 Повторить курс" if language == "ru" else "🔁 Repeat course"), callback_data=f"course_transition:{course_id}:repeat")])
    rows.append([InlineKeyboardButton(text=("Решить позже" if language == "ru" else "Decide later"), callback_data="menu:parent")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def course_switch_keyboard(current_course_id: str, options: list[tuple[str,str]], language: str = "ru") -> InlineKeyboardMarkup:
    ru=language=="ru"
    rows=[[InlineKeyboardButton(text=f"➡️ {title}", callback_data=f"course_switch:target:{cid}")] for cid,title in options if cid!=current_course_id]
    rows.append([InlineKeyboardButton(text=("↩ Меню родителя" if ru else "↩ Parent menu"), callback_data="menu:parent")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def course_switch_mode_keyboard(target_course_id: str, language: str = "ru") -> InlineKeyboardMarkup:
    ru=language=="ru"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("После текущего урока" if ru else "After current lesson"), callback_data=f"course_switch:mode:after:{target_course_id}")],
        [InlineKeyboardButton(text=("Перевести немедленно" if ru else "Switch immediately"), callback_data=f"course_switch:mode:now:{target_course_id}")],
        [InlineKeyboardButton(text=("Отмена" if ru else "Cancel"), callback_data="menu:parent")],
    ])
