from __future__ import annotations

import re


_CHILD_FORMS = {
    "выбрал": ("выбрал", "выбрала"),
    "сказал": ("сказал", "сказала"),
    "назвал": ("назвал", "назвала"),
    "ответил": ("ответил", "ответила"),
    "подумал": ("подумал", "подумала"),
    "готов": ("готов", "готова"),
}
_AI_FORMS = {
    "понял": "поняла", "рад": "рада", "готов": "готова", "заметил": "заметила",
    "подумал": "подумала", "услышал": "услышала", "увидел": "увидела", "решил": "решила",
}
_PLACEHOLDERS = {
    "выбрал(а)": "выбрал", "сказал(а)": "сказал", "назвал(а)": "назвал",
    "ответил(а)": "ответил", "подумал(а)": "подумал", "готов(а)": "готов",
}


def russian_accusative(value: str, *, animate: bool = False) -> str:
    """Conservative Russian accusative fallback; authored case forms always win.

    It intentionally changes only the final noun-like token. Indeclinable and
    plural phrases remain unchanged unless the lesson author supplies a form.
    """
    text = str(value or "").strip()
    if not text:
        return text
    words = text.split()
    word = words[-1]
    lower = word.lower()
    if lower.endswith(("ки", "ги", "хи", "ы", "и", "очки")):
        return text
    if lower.endswith("а"):
        changed = word[:-1] + ("У" if word[-1].isupper() else "у")
    elif lower.endswith("я"):
        changed = word[:-1] + ("Ю" if word[-1].isupper() else "ю")
    elif animate and lower.endswith("ь"):
        changed = word[:-1] + ("Я" if word[-1].isupper() else "я")
    elif animate and re.search(r"[бвгджзклмнпрстфхцчшщ]$", lower):
        changed = word + ("А" if word[-1].isupper() else "а")
    else:
        changed = word
    return " ".join([*words[:-1], changed])


def resolve_gender_placeholders(text: str, child_gender: str) -> str:
    value = str(text or "")
    female = str(child_gender or "").strip().lower() in {"girl", "female", "f"}
    for placeholder, base in _PLACEHOLDERS.items():
        value = re.sub(re.escape(placeholder), _CHILD_FORMS[base][1 if female else 0], value, flags=re.I)
    for base, forms in _CHILD_FORMS.items():
        chosen = forms[1 if female else 0]
        value = re.sub(rf"(?i)(\bты\s+){re.escape(base)}(?:а)?\b", lambda match: match.group(1) + chosen, value)
    return value


def enforce_female_tutor(text: str) -> str:
    value = str(text or "")
    for masculine, feminine in _AI_FORMS.items():
        value = re.sub(rf"(?i)(\bя\s+){re.escape(masculine)}\b", lambda match: match.group(1) + feminine, value)
    return value


def realize_tutor_text(text: str, language: str, child_gender: str) -> str:
    value = str(text or "").strip()
    if str(language or "").lower().startswith("ru"):
        value = enforce_female_tutor(resolve_gender_placeholders(value, child_gender))
    return value


def contains_technical_gender_placeholder(text: str) -> bool:
    value = str(text or "")
    return bool(re.search(r"(?i)\b\w+\([ая]\)|\b\w+\s*/\s*\w+", value))


def realize_tutor_result(result: dict, target_language: str, native_language: str, child_gender: str) -> dict:
    output = dict(result or {})
    target_fields = ("reaction_target", "follow_up_target", "model_answer_target", "corrected_target")
    native_fields = ("reaction_native", "response_native", "follow_up_native", "model_answer_native", "native_hint", "feedback_native")
    for field in target_fields:
        output[field] = realize_tutor_text(output.get(field, ""), target_language, child_gender)
    for field in native_fields:
        output[field] = realize_tutor_text(output.get(field, ""), native_language, child_gender)
    for field in (*target_fields, *native_fields):
        if contains_technical_gender_placeholder(output.get(field, "")):
            output[field] = ""
    return output
