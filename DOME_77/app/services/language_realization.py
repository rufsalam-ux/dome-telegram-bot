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


_KNOWN_ACCUSATIVES = {
    "куртка": "куртку",
    "бутылка воды": "бутылку воды",
    "бутылка": "бутылку",
    "книга": "книгу",
    "камера": "камеру",
    "рыба": "рыбу",
    "мишка": "мишку",
    "бинокль": "бинокль",
    "компас": "компас",
    "фотоаппарат": "фотоаппарат",
    "телескоп": "телескоп",
    "блокнот": "блокнот",
    "солнцезащитные очки": "солнцезащитные очки",
    "очки": "очки",
    "машина": "машину",
    "шляпа": "шляпу",
    "шапка": "шапку",
    "кепка": "кепку",
    "вода": "воду",
    "кошка": "кошку",
    "собака": "собаку",
    "черепаха": "черепаху",
    "птица": "птицу",
    "медведь": "медведя",
    "кот": "кота",
    "слон": "слона",
    "жираф": "жирафа",
    "пингвин": "пингвина",
    "зебра": "зебру",
    "попугай": "попугая",
    "лев": "льва",
}

KNOWN_ITEM_ACCUSATIVE_BY_ID = {
    "teddy": "мишку",
    "jacket": "куртку",
    "water": "бутылку воды",
    "compass": "компас",
    "camera": "фотоаппарат",
    "telescope": "телескоп",
    "fish": "рыбу",
    "notebook": "блокнот",
    "binoculars": "бинокль",
    "sunglasses": "солнцезащитные очки",
    "book": "книгу",
}


def russian_accusative(value: str, *, animate: bool = False) -> str:
    """Conservative Russian accusative fallback; authored case forms always win.

    It handles common head nouns, compound phrases (e.g. 'бутылка воды' -> 'бутылку воды'),
    and regular feminine/animate noun declension.
    """
    text = str(value or "").strip()
    if not text:
        return text
    lower_text = text.lower()
    if lower_text in _KNOWN_ACCUSATIVES:
        correct = _KNOWN_ACCUSATIVES[lower_text]
        return correct.capitalize() if text[0].isupper() else correct

    words = text.split()
    if len(words) > 1:
        first_lower = words[0].lower()
        if first_lower in _KNOWN_ACCUSATIVES:
            first_acc = _KNOWN_ACCUSATIVES[first_lower]
            if words[0][0].isupper():
                first_acc = first_acc.capitalize()
            return " ".join([first_acc, *words[1:]])
        if first_lower.endswith("а"):
            first_changed = words[0][:-1] + ("У" if words[0][-1].isupper() else "у")
            return " ".join([first_changed, *words[1:]])
        if first_lower.endswith("я"):
            first_changed = words[0][:-1] + ("Ю" if words[0][-1].isupper() else "ю")
            return " ".join([first_changed, *words[1:]])

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


def build_selected_item_phrase(item: str | dict, child_gender: str = "boy") -> str:
    is_girl = str(child_gender or "").strip().lower() in {"girl", "female", "f"}
    verb = "выбрала" if is_girl else "выбрал"
    accusative = ""
    if isinstance(item, dict):
        accusative = str(
            item.get("labelAccusative")
            or item.get("label_accusative")
            or item.get("label_ru_accusative")
            or item.get("label_target_accusative")
            or item.get("label_native_accusative")
            or ""
        ).strip()
        if not accusative:
            item_id = str(item.get("id") or "").lower()
            if item_id in KNOWN_ITEM_ACCUSATIVE_BY_ID:
                accusative = KNOWN_ITEM_ACCUSATIVE_BY_ID[item_id]
            else:
                nom = str(item.get("labelNominative") or item.get("label_nominative") or item.get("label_ru") or item.get("label") or "").strip()
                accusative = russian_accusative(nom, animate=True) if nom else ""
    else:
        raw = str(item or "").strip()
        raw_id = raw.lower()
        if raw_id in KNOWN_ITEM_ACCUSATIVE_BY_ID:
            accusative = KNOWN_ITEM_ACCUSATIVE_BY_ID[raw_id]
        else:
            accusative = russian_accusative(raw, animate=True)
    return f"Ты {verb} {accusative}."


def format_choice_replica(
    item: str | dict,
    child_gender: str = "boy",
    language: str = "ru",
    action: str = "chose",
    punctuation: str = ".",
    *,
    target_language: str = "",
    native_language: str = "",
) -> str:
    """Format grammatically sound selection replica with correct gender and case."""
    is_girl = str(child_gender or "").strip().lower() in {"girl", "female", "f"}
    lang = str(language or "ru").strip().lower()[:2]
    t_lang = str(target_language or "").strip().lower()[:2]
    n_lang = str(native_language or "").strip().lower()[:2]

    if lang == "ru":
        if action == "why_chose":
            verb = "выбрала" if is_girl else "выбрал"
            acc = ""
            if isinstance(item, dict):
                acc = str(item.get("labelAccusative") or item.get("label_accusative") or item.get("label_ru_accusative") or item.get("label_target_accusative") or item.get("label_native_accusative") or "").strip()
                if not acc:
                    item_id = str(item.get("id") or "").lower()
                    acc = KNOWN_ITEM_ACCUSATIVE_BY_ID.get(item_id) or russian_accusative(str(item.get("labelNominative") or item.get("label_nominative") or item.get("label_ru") or item.get("label") or ""), animate=True)
            else:
                raw = str(item or "").strip()
                acc = KNOWN_ITEM_ACCUSATIVE_BY_ID.get(raw.lower()) or russian_accusative(raw, animate=True)
            return f"Почему ты {verb} {acc}?"
        phrase = build_selected_item_phrase(item, child_gender)
        if punctuation and punctuation != ".":
            return phrase[:-1] + punctuation
        return phrase
    else:
        label_en = ""
        if isinstance(item, dict):
            label_en = (
                item.get("label_en_accusative")
                or (item.get("label_target_accusative") if t_lang == "en" else None)
                or (item.get("label_native_accusative") if n_lang == "en" else None)
                or (item.get("label_native_accusative") if not n_lang and item.get("label_native_accusative") else None)
                or item.get("label_en")
                or item.get("label")
                or item.get("id")
                or ""
            )
            label_en = str(label_en).strip()
        else:
            label_en = str(item or "").strip()
        if action == "why_chose":
            return f"Why did you choose {label_en}?"
        punc = punctuation if punctuation in {".", "!", "?"} else "."
        return f"You chose {label_en}{punc}"


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
