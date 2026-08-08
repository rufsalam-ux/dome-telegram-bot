from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_FEATURES: dict[str, Any] = {
    "schema_version": "1.0",
    "features": {
        "interest_lessons": {"mode": "enabled", "title": "Уроки по интересам"},
        "interest_generation": {"mode": "enabled", "title": "Генерация новых AI-уроков"},
        "interest_search_library_first": {"mode": "enabled", "title": "Сначала искать готовый урок"},
        "homework": {"mode": "enabled", "title": "Домашние задания"},
        "lesson_feedback": {"mode": "enabled", "title": "Отзывы после урока"},
        "voice_feedback": {"mode": "disabled", "title": "Голосовой отзыв"},
        "cartoon": {"mode": "enabled", "title": "Персональный мультфильм"},
        "lesson_replays": {"mode": "enabled", "title": "До трёх прохождений урока"},
        "seasonal_lessons": {"mode": "enabled", "title": "Сезонные уроки"},
        "notifications": {"mode": "enabled", "title": "Уведомления о новых уроках"},
        "unfinished_reminders": {"mode": "enabled", "title": "Мягкие напоминания"},
        "memory_game": {"mode": "enabled", "title": "Memory"},
        "drag_drop": {"mode": "enabled", "title": "Drag & Drop"},
        "video": {"mode": "enabled", "title": "Видео"},
        "camera": {"mode": "disabled", "title": "Камера"},
        "paper_handwriting_live": {"mode": "disabled", "title": "Письмо на бумаге в реальном времени"},
        "pose_tracking": {"mode": "disabled", "title": "Pose tracking / лево-право"},
        "realtime_reading": {"mode": "disabled", "title": "Чтение с отслеживанием текста"},
        "group_lessons": {"mode": "disabled", "title": "Групповые уроки"},
        "random_child_matching": {"mode": "disabled", "title": "Подбор незнакомых детей"},
    },
    "overrides": {"languages": {}, "courses": {}, "lessons": {}},
}

DEFAULT_PRICING: dict[str, Any] = {
    "schema_version": "1.0",
    "currency": "USD",
    "regular_course": {
        "monthly_markup_per_lesson": 6.0,
        "monthly_minimum": 49.0,
        "annual_markup_per_lesson": 5.8,
        "annual_minimum": 490.0,
        "lesson_access_months": 10,
        "max_replays": 3,
        "cartoon_on_first_run_only": True,
        "trial_days": 7,
    },
    "interest_lessons": {
        "profit_per_lesson": 30.0,
        "cost_buffer_percent": 15.0,
        "include_generation_cost": True,
        "include_cartoon_cost": True,
        "include_three_runs_cost": True,
        "include_homework_cost": True,
        "round_to": 0.01,
    },
    "cost_engine": {
        "pricing_window_days": 30,
        "use_rolling_average": True,
        "services": {
            "ai_text": {"enabled": True, "mode": "automatic", "fixed_cost": 0.0},
            "speech_to_text": {"enabled": True, "mode": "automatic", "fixed_cost": 0.0},
            "text_to_speech": {"enabled": True, "mode": "automatic", "fixed_cost": 0.0},
            "vision": {"enabled": True, "mode": "automatic", "fixed_cost": 0.0},
            "memory_database": {"enabled": True, "mode": "fixed", "fixed_cost": 0.0},
            "storage": {"enabled": True, "mode": "fixed", "fixed_cost": 0.0},
            "bandwidth": {"enabled": True, "mode": "fixed", "fixed_cost": 0.0},
            "render_compute": {"enabled": True, "mode": "fixed", "fixed_cost": 0.0},
            "hosting": {"enabled": True, "mode": "fixed", "fixed_cost": 0.0},
            "sms": {"enabled": True, "mode": "fixed", "fixed_cost": 0.0},
            "email": {"enabled": True, "mode": "fixed", "fixed_cost": 0.0},
            "payments": {"enabled": True, "mode": "fixed", "fixed_cost": 0.0},
            "monitoring": {"enabled": True, "mode": "fixed", "fixed_cost": 0.0},
            "other": {"enabled": True, "mode": "fixed", "fixed_cost": 0.0},
        },
    },
}

DEFAULT_LANGUAGES: dict[str, Any] = {
    "schema_version": "1.0",
    "languages": [
        {"code": "en", "title": "English", "active": True},
        {"code": "es", "title": "Español", "active": False},
        {"code": "fr", "title": "Français", "active": False},
        {"code": "de", "title": "Deutsch", "active": False},
    ],
}

DEFAULT_NOTIFICATIONS: dict[str, Any] = {
    "schema_version": "1.0",
    "new_lesson_notice": True,
    "unfinished_reminders": True,
    "max_unfinished_reminders": 2,
    "first_reminder_hours": 36,
    "second_reminder_hours": 96,
    "quiet_hours": {"start": "20:00", "end": "09:00"},
    "expiry_notice_days": [30, 7],
}


DEFAULT_CONVERSATION: dict[str, Any] = {
    "schema_version": "1.0",
    "enabled": True,
    "name_usage_probability": 0.22,
    "avoid_echo_if_good": True,
    "good_enough_semantic_threshold": 0.62,
    "good_enough_max_grammar_errors": 2,
    "good_enough_max_pronunciation_errors": 2,
    "correction_style": "gentle",
    "max_corrections": 1,
    "simplify_after_corrections": 1,
    "sometimes_accept_imperfect": True,
    "live_adaptation": True,
    "difficulty_floor": 0.05,
    "difficulty_ceiling": 0.95,
    "extra_followups_if_strong": 2,
    "extra_followup_threshold": 0.68,
    "minimal_task_threshold": 0.26,
    "human_dialogue_phrases": True,
    "offer_real_help": True,
    "help_phrases_ru": [
        "Как ты думаешь, {name}?",
        "{name}, давай подумаем вместе!",
        "Хочешь, я помогу?",
        "У меня есть вариант, хочешь скажу?",
        "Давай решим это вместе, я с тобой."
    ]
}


DEFAULT_HOMEWORK: dict[str, Any] = {
    "schema_version": "1.1",
    "enabled": True,
    "default_source": "manual",
    "optional": True,
    "send_to_bot": True,
    "send_to_parent_email": True,
    "max_duration_minutes": 10,
    "default_duration_minutes": 5,
    "allow_skip": True,
    "allow_defer": True,
    "keep_in_archive": True,
    "ai_generate_only_when_lesson_requests_it": True,
    "fallback_without_ai": True,
    "max_tasks": 3,
}

DEFAULT_GAMES: dict[str, Any] = {
    "schema_version": "1.1",
    "menu_title": "Игры DOME",
    "design": "bright_light_saturated",
    "weekly_budget_usd_per_user": 0.50,
    "reuse_existing_language_version_first": True,
    "cache_generated_game_versions": True,
    "cache_tts_audio": True,
    "stop_paid_generation_at_budget": True,
    "paid_generation_soft_limit_usd": 0.40,
    "games": [],
}

DEFAULT_LEGAL: dict[str, Any] = {
    "schema_version": "1.0",
    "terms_version": "draft-1",
    "privacy_version": "draft-1",
    "parent_consent_version": "draft-1",
    "media_consent_version": "draft-1",
    "billing_version": "draft-1",
    "require_reconsent_on_material_change": True,
}


DEFAULT_FREE_TOPIC = json.loads((CONFIG_DIR / "free_topic.json").read_text("utf-8")) if (CONFIG_DIR / "free_topic.json").exists() else {"schema_version":"1.0","enabled":True,"min_slides":18,"max_slides":25,"default_slides":21,"allow_test_payment_bypass":True}
DEFAULT_CARTOON = json.loads((CONFIG_DIR / "cartoon.json").read_text("utf-8")) if (CONFIG_DIR / "cartoon.json").exists() else {"schema_version":"1.0","first_child_scene_seconds":8,"companions":{"enabled":True}}
DEFAULT_PAYMENTS = json.loads((CONFIG_DIR / "payments.json").read_text("utf-8")) if (CONFIG_DIR / "payments.json").exists() else {"schema_version":"1.0","save_card_once":True,"future_package_confirmation":"sms_otp","allow_test_free_topic_bypass":True}

DEFAULTS = {
    "features": DEFAULT_FEATURES,
    "pricing": DEFAULT_PRICING,
    "languages": DEFAULT_LANGUAGES,
    "notifications": DEFAULT_NOTIFICATIONS,
    "conversation": DEFAULT_CONVERSATION,
    "legal": DEFAULT_LEGAL,
    "homework": DEFAULT_HOMEWORK,
    "games": DEFAULT_GAMES,
    "free_topic": DEFAULT_FREE_TOPIC,
    "cartoon": DEFAULT_CARTOON,
    "payments": DEFAULT_PAYMENTS,
}


def _path(name: str) -> Path:
    if name not in DEFAULTS:
        raise KeyError(name)
    return CONFIG_DIR / f"{name}.json"


def ensure_defaults() -> None:
    for name, default in DEFAULTS.items():
        p = _path(name)
        if not p.exists():
            save_settings(name, deepcopy(default))


def load_settings(name: str) -> dict[str, Any]:
    ensure_defaults()
    p = _path(name)
    try:
        data = json.loads(p.read_text("utf-8"))
        return data if isinstance(data, dict) else deepcopy(DEFAULTS[name])
    except Exception:
        return deepcopy(DEFAULTS[name])


def save_settings(name: str, data: dict[str, Any]) -> dict[str, Any]:
    if name not in DEFAULTS:
        raise KeyError(name)
    p = _path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(p)
    return data


def resolve_feature(feature: str, *, language: str | None = None, course_id: str | None = None, lesson_id: str | None = None) -> str:
    """Resolve feature state from global -> language -> course -> lesson.

    Returns: enabled | disabled | no_new_sales
    """
    cfg = load_settings("features")
    global_spec = (cfg.get("features") or {}).get(feature, {})
    mode = global_spec.get("mode", "disabled")
    overrides = cfg.get("overrides") or {}
    if language:
        mode = ((overrides.get("languages") or {}).get(language, {}) or {}).get(feature, mode)
    if course_id:
        mode = ((overrides.get("courses") or {}).get(course_id, {}) or {}).get(feature, mode)
    if lesson_id:
        mode = ((overrides.get("lessons") or {}).get(lesson_id, {}) or {}).get(feature, mode)
    return mode


ensure_defaults()
