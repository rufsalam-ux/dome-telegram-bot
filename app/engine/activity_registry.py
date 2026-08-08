from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable

@dataclass(frozen=True)
class ActivityType:
    id: str
    title_ru: str
    category: str
    input_modes: tuple[str, ...]
    description_ru: str
    implemented_now: bool = False
    renderer: str = "generic"
    future_safe: bool = True

# IMPORTANT: this registry is deliberately broader than the current Telegram renderer.
# Lessons may be authored now; unsupported activities stay explicit instead of being
# silently treated as another task type.
_ACTIVITY_TYPES = [
    ActivityType("speak", "Ответ голосом", "speech", ("voice",), "Свободный или направляемый устный ответ.", True, "telegram_voice"),
    ActivityType("repeat", "Повтори слово/фразу", "speech", ("voice",), "Повторение модели с мягкой проверкой произношения.", True, "telegram_voice"),
    ActivityType("dialogue", "AI-диалог", "speech", ("voice", "text"), "Естественный диалог с уточняющими вопросами и контекстом.", True, "telegram_voice"),
    ActivityType("roleplay", "Ролевая ситуация", "speech", ("voice",), "Магазин, кафе, поездка, врач, аэропорт и другие сценарии."),
    ActivityType("guess_from_description", "Угадай по описанию", "speech", ("voice", "tap"), "AI описывает объект, ребёнок угадывает."),
    ActivityType("describe_ai_guess", "Опиши — AI угадает", "speech", ("voice",), "Ребёнок описывает скрытый объект, AI угадывает."),
    ActivityType("twenty_questions", "20 вопросов", "speech", ("voice",), "Угадывание через вопросы да/нет."),
    ActivityType("read_aloud", "Чтение вслух", "reading", ("voice",), "Слежение за текстом, редкая мягкая коррекция и подсветка."),
    ActivityType("read_roles", "Чтение по ролям", "reading", ("voice",), "Ребёнок и AI читают разные роли."),
    ActivityType("phonics", "Звуки и фонемы", "reading", ("voice", "tap", "drag"), "Blending, segmenting, первый/последний звук."),
    ActivityType("comprehension", "Понимание прочитанного", "reading", ("voice", "tap", "text"), "Вопросы, пересказ, изменение условия."),
    ActivityType("tap_select", "Нажми / выбери", "interactive", ("tap",), "Подсветить, обвести, отметить или открыть выбранный объект.", True, "miniapp"),
    ActivityType("multi_select", "Выбери несколько", "interactive", ("tap",), "Выбор нескольких правильных объектов."),
    ActivityType("remove_extra", "Удали лишнее", "interactive", ("tap",), "Найти и удалить лишний объект."),
    ActivityType("drag_drop", "Перетаскивание", "interactive", ("drag",), "Универсальное drag-and-drop.", True, "miniapp"),
    ActivityType("matching", "Сопоставление", "interactive", ("tap", "drag"), "Соединить пары: слово-картинка, звук-картинка и т.д."),
    ActivityType("sorting", "Сортировка", "interactive", ("drag",), "Разложить элементы по группам/контейнерам."),
    ActivityType("sequence", "Последовательность", "interactive", ("drag",), "Расположить события, слова или изображения по порядку."),
    ActivityType("memory", "Memory", "game", ("tap",), "Переворачивать карточки и находить пары."),
    ActivityType("puzzle", "Пазл", "game", ("drag",), "Собрать изображение или конструкцию."),
    ActivityType("maze", "Лабиринт", "game", ("draw",), "Провести линию по маршруту."),
    ActivityType("word_builder", "Собери слово", "interactive", ("drag", "tap"), "Собрать слово из букв/звуков."),
    ActivityType("sentence_builder", "Собери предложение", "interactive", ("drag", "tap"), "Собрать предложение из слов."),
    ActivityType("fill_gap", "Вставь пропуск", "interactive", ("tap", "drag", "text"), "Вставить букву, слово или часть предложения."),
    ActivityType("coloring", "Раскрашивание", "creative", ("draw",), "Раскрасить по условию или свободно."),
    ActivityType("draw", "Рисование", "creative", ("draw",), "Рисунок на экране с анализом результата."),
    ActivityType("connect_lines", "Соедини линиями", "interactive", ("draw",), "Соединить соответствующие элементы."),
    ActivityType("handwriting_screen", "Письмо на экране", "writing", ("draw",), "Обводка и письмо пальцем/стилусом с анализом траектории."),
    ActivityType("handwriting_paper_photo", "Письмо на бумаге — показать", "writing", ("camera",), "После строки показать лист; AI мягко анализирует результат."),
    ActivityType("handwriting_paper_live", "Письмо на бумаге — камера", "writing", ("camera_video",), "Камера наблюдает процесс письма; редкая дозированная коррекция."),
    ActivityType("video_watch", "Просмотр видео", "media", ("video",), "Просмотр ролика целиком или по сегментам."),
    ActivityType("video_pause_question", "Видео с остановками", "media", ("video", "voice", "tap"), "Видео останавливается и задаёт вопрос/прогноз."),
    ActivityType("camera_action", "Покажи в камеру", "vision", ("camera_video",), "Показ эмоции, жеста, предмета или действия; AI угадывает."),
    ActivityType("pose_action", "Движение телом", "vision", ("camera_video",), "Проверка позы/движения с анатомическим лево/право."),
    ActivityType("spatial_orientation", "Лево/право и пространство", "vision", ("camera_video", "drag"), "Нормализация зеркальности и ориентации относительно ребёнка."),
    ActivityType("real_world_find", "Найди предмет вокруг себя", "vision", ("camera", "camera_video"), "Найти реальный предмет и показать/описать его."),
    ActivityType("paper_scan", "Работа с бумажной страницей", "vision", ("camera",), "Показать страницу, найти/прочитать/указать объект."),
    ActivityType("song_rhythm", "Песня и ритм", "media", ("audio", "voice", "tap"), "Песня, хлопки по слогам, ударение и ритм."),
    ActivityType("dictation", "Диктант", "writing", ("text", "draw", "camera"), "AI диктует; ребёнок пишет на экране или бумаге."),
    ActivityType("explain_to_ai", "Объясни персонажу", "proof", ("voice", "text"), "Ребёнок учит AI и объясняет правило своими словами."),
    ActivityType("find_ai_mistake", "Исправь ошибку AI", "proof", ("voice", "tap", "text"), "Персонаж намеренно ошибается, ребёнок исправляет."),
    ActivityType("proof_transfer", "Проверка переноса знания", "proof", ("voice", "tap", "drag", "text"), "Изменённое условие и повторная проверка навыка."),
    ActivityType("branching_story", "Ветвящаяся история", "story", ("voice", "tap"), "Выбор ребёнка меняет продолжение сюжета."),
    ActivityType("quest", "Квест", "story", ("voice", "tap", "drag", "camera"), "Несколько активностей объединены общей целью."),
    ActivityType("creative_product", "Создание продукта", "creative", ("voice", "draw", "text", "camera"), "Комикс, мини-книга, мультфильм, аудиоистория или постер."),
    ActivityType("math_manipulatives", "Математика с объектами", "math", ("drag", "camera", "tap"), "Счёт, группы, сравнение и действия с объектами."),
    ActivityType("experiment", "Наблюдение/эксперимент", "science", ("camera", "voice", "tap"), "Предсказать, выполнить безопасное наблюдение, объяснить результат."),
]

REGISTRY = {x.id: x for x in _ACTIVITY_TYPES}

def get_activity_type(activity_id: str) -> ActivityType:
    try:
        return REGISTRY[activity_id]
    except KeyError as e:
        raise ValueError(f"Unknown DOME activity type: {activity_id}") from e

def list_activity_types() -> list[dict]:
    return [asdict(x) for x in _ACTIVITY_TYPES]

def validate_activity_ids(ids: Iterable[str]) -> list[str]:
    return [x for x in ids if x not in REGISTRY]
