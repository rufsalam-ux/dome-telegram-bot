# DOME v51 — optional Kling full-body animation

## Главное
- Без `KLING_API_KEY` и `KLING_API_SECRET` мультфильм работает как раньше: стабильная PNG-анимация.
- С ключами Kling бот сначала ищет движение в персональной библиотеке героя; если его нет — генерирует, автоматически проверяет и сохраняет.
- Если генерация/проверка не прошла после ограниченного числа попыток, сцена автоматически откатывается на PNG fallback. Клиент ничего не подтверждает.
- Движения будущих уроков описываются по-русски в `content/lessons/<lesson_id>/timeline.json`, поле `character_animation.description_ru`.

Пример:
```json
{
  "phrase_id": "example",
  "visible_start": 12,
  "talk_start": 12,
  "end": 17,
  "character_animation": {
    "description_ru": "Герой идёт вправо, останавливается, машет рукой и говорит в камеру.",
    "reuse": true,
    "speaking": true,
    "view": "front"
  }
}
```

`timeline.json` является приоритетным сценарием относительно timeline, встроенного в lesson.json.
