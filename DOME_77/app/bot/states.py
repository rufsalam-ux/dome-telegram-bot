from aiogram.fsm.state import State, StatesGroup


class Onboarding(StatesGroup):
    child_name = State()
    child_age = State()
    child_gender = State()
    native_language = State()
    target_language = State()
    target_reading = State()
    schedule_timezone = State()
    schedule_days = State()
    schedule_time = State()
    character_choice = State()
    character_upload = State()
    character_confirm = State()


class SettingsFlow(StatesGroup):
    native_language = State()
    free_topic = State()
    target_language = State()
    character_upload = State()
    character_confirm = State()
    parent_email = State()
    birthday = State()
    profile_gender = State()
    target_reading = State()
    schedule_timezone = State()
    schedule_days = State()
    schedule_time = State()


class LessonFlow(StatesGroup):
    waiting_voice = State()
    waiting_card = State()
    waiting_webapp = State()


class ConsentFlow(StatesGroup):
    phone = State()
    code = State()


class FreeTopicFlow(StatesGroup):
    payment = State()
    playing = State()
    waiting_answer = State()


class PilotAccessFlow(StatesGroup):
    code = State()

class AdminLessonImport(StatesGroup):
    lesson_file = State()
    instruction_file = State()
    homework_file = State()
    extras = State()
