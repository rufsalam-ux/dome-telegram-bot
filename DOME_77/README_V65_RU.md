# DOME v65 PILOT EDITION

Цель версии: подготовить текущий production-бот к продаже закрытого 30-дневного пилота без переписывания Python-кода под каждый новый урок.

## Добавлено
- 3 курса: Разговорная практика / Учимся читать / Читаем и понимаем.
- Последовательное открытие уроков по `order`.
- Автопоиск новых активных `content_v1` уроков по папкам `content/lessons/`.
- Упрощённое меню ребёнка: Продолжить / Мои курсы / Мои успехи.
- Упрощённое меню взрослого: Ребёнок / Курсы / Прогресс / Доступ / Настройки.
- 30-дневные pilot/promocode access codes через `config/pilots.json`.
- Content V1 runtime поверх уже существующего интерактивного движка: passive, choice, memory, drag_drop, visual_pack, voice_answer, roleplay, video, drawing, mini_game.
- Локальные картинки и видео можно подключать из папки конкретного урока.
- Интерактивная домашняя работа в `homework.json` запускается после content_v1 урока.
- Прогресс content_v1 урока сохраняется в `LessonSession.current_step`; кнопка Продолжить возвращает к текущему шагу.
- Шаблон нового урока и подробная инструкция `docs/CONTENT_GUIDE_RU.md`.
- Чек-лист запуска покупателя `docs/PILOT_TOMORROW_CHECKLIST_RU.md`.

## Совместимость
Существующий `demo_001` оставлен legacy-уроком и не переведён на новый runtime. Он назначен первым уроком курса `conversation`.

## Проверки
- Python compile: PASS.
- JSON configs/templates: PASS.
- v65 + v63 + v62 selective regression tests: 13 PASS.
- Полный pytest в текущей audit-среде блокируется отсутствием установленного `aiogram`; `aiogram>=3.13,<4` присутствует в production `requirements.txt`, поэтому это environment-blocked, а не обнаруженная code-level ошибка.

Перед production deploy обязательно пройти `docs/PILOT_TOMORROW_CHECKLIST_RU.md` на тестовом Telegram-аккаунте.
