from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Iterable

@dataclass(frozen=True)
class ActivityType:
    id:str; title_ru:str; category:str; input_modes:tuple[str,...]; description_ru:str; implemented_now:bool=False; renderer:str='generic'; future_safe:bool=True

A=ActivityType
_ACTIVITY_TYPES=[
 A('speak','Ответ голосом','speech',('voice',),'Свободный или направляемый устный ответ.',True,'telegram_voice'),
 A('repeat','Повтори','speech',('voice',),'Повторение слова или фразы с мягкой помощью.',True,'telegram_voice'),
 A('dialogue','AI-диалог','speech',('voice','text'),'Короткий естественный диалог по теме.',True,'telegram_voice'),
 A('roleplay','Ролевая ситуация','speech',('voice',),'Сюжетный разговор в роли.',True,'telegram_voice'),
 A('read_aloud','Чтение вслух','reading',('voice',),'Бот слушает, не цепляется к мелочам и помогает при затруднении.',True,'telegram_voice'),
 A('read_roles','Чтение по ролям','reading',('voice','audio'),'AI читает другие роли и динамически забирает больше текста при трудностях.',True,'telegram_voice'),
 A('echo_reading','Эхо-чтение','reading',('audio','voice'),'AI читает короткий фрагмент, ребёнок повторяет.',True,'telegram_voice'),
 A('shared_reading','Совместное чтение','reading',('audio','voice'),'AI и ребёнок читают попеременно.',True,'telegram_voice'),
 A('comprehension','Вопрос по прочитанному','reading',('voice','tap'),'Обсуждение смысла и вопросы по инструкции.',True,'telegram_voice'),
 A('retell','Пересказ','reading',('voice',),'Короткий пересказ с подсказками при необходимости.',True,'telegram_voice'),
 A('continue_story','Продолжи историю','reading',('voice',),'Ребёнок придумывает продолжение.',True,'telegram_voice'),
 A('tap_select','Нажми / выбери','interactive',('tap',),'Выбор одного объекта.',True,'miniapp'),
 A('multi_select','Выбери несколько','interactive',('tap',),'Выбор нескольких объектов.',True,'miniapp'),
 A('tap_sound','Нажми и послушай','interactive',('tap','audio'),'Кликабельные зоны на исходном слайде.',True,'miniapp'),
 A('listen_choose','Послушай и выбери','interactive',('audio','tap'),'Выбор буквы/слова/картинки на слух.',True,'miniapp'),
 A('drag_drop','Перетаскивание','interactive',('drag','tap'),'Универсальное drag-and-drop.',True,'miniapp'),
 A('matching','Сопоставление','interactive',('tap','drag'),'Сопоставить пары.',True,'miniapp'),
 A('match_visible','Пары без переворота','interactive',('tap',),'Memory-подобное задание с открытыми карточками.',True,'miniapp'),
 A('memory','Memory','game',('tap',),'Переворачивать карточки и находить пары.',True,'miniapp'),
 A('sorting','Сортировка','interactive',('drag','tap'),'Разложить элементы по категориям.',True,'miniapp'),
 A('sequence','Последовательность','interactive',('tap','drag'),'Восстановить порядок событий.',True,'miniapp'),
 A('word_builder','Собери слово','reading',('tap','drag'),'Собрать слово из букв.',True,'miniapp'),
 A('syllable_builder','Собери из слогов','reading',('tap','drag'),'Собрать слово из слогов.',True,'miniapp'),
 A('sentence_builder','Собери предложение','reading',('tap','drag'),'Собрать предложение из слов.',True,'miniapp'),
 A('fill_gap','Вставь пропуск','reading',('tap','drag','text'),'Вставить букву, слог или слово.',True,'miniapp'),
 A('odd_one_out','Найди лишнее','interactive',('tap',),'Найти лишний объект/слово.',True,'miniapp'),
 A('sound_position','Где звук?','reading',('tap','audio'),'Начало, середина или конец слова.',True,'miniapp'),
 A('syllable_split','Раздели на слоги','reading',('tap','drag'),'Деление слова на слоги.',True,'miniapp'),
 A('find_in_text','Найди в тексте','reading',('tap',),'Найти букву, слово или фрагмент.',True,'miniapp'),
 A('connect_lines','Соедини линиями','writing',('draw',),'Соединить элементы линиями.',True,'miniapp'),
 A('trace','Обводка','writing',('draw',),'Обводить линии и буквы пальцем/стилусом.',True,'miniapp'),
 A('handwriting_screen','Письмо на экране','writing',('draw',),'Писать прямо на странице.',True,'miniapp'),
 A('draw','Рисование','creative',('draw',),'Свободное рисование.',True,'miniapp'),
 A('coloring','Раскрашивание','creative',('draw',),'Раскрашивание по условию.',True,'miniapp'),
 A('maze','Лабиринт','game',('draw',),'Провести героя по маршруту.',True,'miniapp'),
 A('dictation','Мини-диктант','writing',('audio','draw'),'Бот диктует, ребёнок пишет.',True,'miniapp'),
 A('video','Видео / мультфильм','media',('video',),'Просмотр видео.',True,'telegram'),
 A('video_pause_question','Видео с вопросами','media',('video','voice','tap'),'Остановки для вопроса или прогноза.',True,'miniapp'),
 A('interactive_scene','Интерактивная сцена','interactive',('tap','drag','audio'),'Несколько активных объектов на одном слайде.',True,'miniapp'),
 A('real_world_find','Найди вокруг себя','creative',('camera','voice'),'Найти реальный предмет и показать/описать.',True,'telegram'),
 A('photo_task','Фотозадание','creative',('camera',),'Сделать фото по заданию.',True,'telegram'),
 A('physical_action','Двигательная пауза','creative',('physical',),'Хлопок, жест или короткое движение без обязательной камеры.',True,'telegram'),
 A('mood_choice','Настроение','social',('tap',),'Выбрать эмоциональную реакцию после занятия.',True,'miniapp'),
]
REGISTRY={x.id:x for x in _ACTIVITY_TYPES}
def get_activity_type(activity_id:str)->ActivityType:
    try:return REGISTRY[activity_id]
    except KeyError as e: raise ValueError(f'Unknown DOME activity type: {activity_id}') from e
def list_activity_types()->list[dict]: return [asdict(x) for x in _ACTIVITY_TYPES]
def validate_activity_ids(ids:Iterable[str])->list[str]: return [x for x in ids if x not in REGISTRY]
