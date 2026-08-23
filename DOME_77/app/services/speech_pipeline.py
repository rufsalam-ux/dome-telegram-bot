from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.i18n import language_name

log = logging.getLogger("dome.speech")


@dataclass
class SpeechAssessment:
    transcript: str = ""
    detected_language: str = ""
    confidence: float = 0.0
    grammar_errors: list[str] | None = None
    pronunciation_errors: list[str] | None = None
    semantic_match: float = 0.0
    status: str = "TECHNICAL_UNCERTAINTY"
    feedback_native: str = ""
    corrected_target: str = ""
    response_target: str = ""
    response_native: str = ""

    def __post_init__(self):
        self.grammar_errors = self.grammar_errors or []
        self.pronunciation_errors = self.pronunciation_errors or []


async def _transcribe_with_model(wav_path: Path, model: str, language: str = "", prompt: str = "") -> tuple[str, str, float]:
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
    data = {"model": model, "response_format": "json"}
    if language:
        data["language"] = language
    if prompt:
        data["prompt"] = prompt[:800]
    async with httpx.AsyncClient(timeout=120) as client:
        with wav_path.open("rb") as fh:
            response = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers=headers,
                data=data,
                files={"file": (wav_path.name, fh, "audio/wav")},
            )
    if response.status_code >= 400:
        log.warning("Transcription failed model=%s status=%s body=%s", model, response.status_code, response.text[:500])
        return "", "", 0.0
    payload = response.json()
    text = str(payload.get("text", "")).strip()
    language = str(payload.get("language", "")).strip().lower()
    return text, language, (0.9 if text else 0.0)


async def transcribe_audio(wav_path: Path, target_language: str = "", native_language: str = "", goal: str = "") -> tuple[str, str, float]:
    """Transcribe child speech without over-biasing the recognizer.

    First try automatic language detection. Forced-language attempts are only
    fallbacks. This avoids accepting a plausible but wrong hallucinated phrase
    merely because the API was forced to the target language.
    """
    if not settings.openai_api_key:
        return "", "", 0.0
    preferred = settings.openai_transcription_model or "gpt-4o-mini-transcribe"
    models = [preferred] + ([] if preferred == "whisper-1" else ["whisper-1"])
    prompt = f"A child is answering this lesson prompt: {goal}. Transcribe exactly; do not invent missing words."
    for model in models:
        # Automatic detection is the only normal request. Forced-language
        # requests are concurrent fallbacks, so one voice take is not sent to
        # the transcription provider three times in sequence.
        text, detected, confidence = await _transcribe_with_model(wav_path, model, "", prompt)
        if text:
            return text.strip(), detected.strip().lower(), confidence
        languages=list(dict.fromkeys(lang for lang in (target_language,native_language) if lang))
        fallbacks=await asyncio.gather(*[_transcribe_with_model(wav_path,model,lang,prompt) for lang in languages])
        candidates=[(value,lang) for value,lang in zip(fallbacks,languages) if value[0]]
        if candidates:
            (text,detected,confidence),hint=max(candidates,key=lambda item:min(len(item[0][0]),120))
            return text.strip(),(detected or hint).strip().lower(),confidence
    return "", "", 0.0




def _coerce_score(value: object, default: float = 0.0) -> float:
    """Convert model output to a 0..1 float without crashing on labels like 'partial'."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    text = str(value).strip().lower().replace(',', '.')
    labels = {
        'none': 0.0, 'no': 0.0, 'false': 0.0, 'incorrect': 0.0,
        'low': 0.25, 'partial': 0.5, 'partly': 0.5, 'medium': 0.5,
        'mostly': 0.75, 'high': 0.85, 'correct': 1.0, 'full': 1.0, 'yes': 1.0, 'true': 1.0,
    }
    if text in labels:
        return labels[text]
    if text.endswith('%'):
        try:
            return max(0.0, min(1.0, float(text[:-1]) / 100.0))
        except ValueError:
            return default
    try:
        number = float(text)
    except ValueError:
        log.warning('Unexpected semantic_match value: %r', value)
        return default
    if number > 1.0 and number <= 100.0:
        number /= 100.0
    return max(0.0, min(1.0, number))

def _safe_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    return json.loads(text)


async def _evaluate_with_chat(prompt: dict) -> dict | None:
    instructions = (
        "You are a careful child language tutor. Evaluate a short spoken answer. "
        "Return valid JSON only with keys: detected_language_code, semantic_match, grammar_errors, "
        "pronunciation_errors, feedback_native, corrected_target, response_target, response_native, decision. "
        "decision must be CORRECT, RETRY, WRONG_LANGUAGE, or TECHNICAL_UNCERTAINTY. "
        "Do not punish likely transcription errors. Accept correct close paraphrases. Preserve the child's chosen meaning and nouns: never replace cat with dog or one chosen animal/object with another. "
        "response_target must sound like a real human dialogue with a child, not a test script. "
        "Do not mechanically repeat or paraphrase what the child just said when it is already understandable. "
        "Never ask a follow-up question and never create a new task. The lesson script alone decides what happens next. "
        "When the child needs help, give one short usable example that directly answers the CURRENT goal. Never reuse nouns, animals, places, facts, or questions from another task. "
        "Use the child's name only occasionally when a name is provided, never in every reply. "
        "At low difficulty accept one-word/very short answers. Never invite an extra reason, detail, comparison or dialogue unless the CURRENT goal explicitly requests it. "
        "corrected_target must be a valid direct answer to the CURRENT goal, never praise such as That is correct. response_target may only be a short acknowledgement, never a question. response_native may briefly explain the result in the child's native language."
    )
    headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}
    models = [settings.openai_text_model or "gpt-4o-mini"]
    if models[0] != "gpt-4o-mini":
        models.append("gpt-4o-mini")
    async with httpx.AsyncClient(timeout=90) as client:
        for model in models:
            payload = {
                "model": model,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
            }
            response = await client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
            if response.status_code >= 400:
                log.warning("Assessment failed model=%s status=%s body=%s", model, response.status_code, response.text[:500])
                continue
            try:
                text = response.json()["choices"][0]["message"]["content"]
                return _safe_json(text)
            except Exception as exc:
                log.warning("Assessment parse failed model=%s: %s", model, exc)
    return None


async def assess_speech(
    wav_path: Path,
    target_language: str,
    native_language: str,
    goal: str,
    accepted_meaning: list[str] | None,
    attempt_number: int,
    child_name: str = "",
    working_difficulty: float = 0.15,
    language_level: str = "PRE_A1",
) -> SpeechAssessment:
    transcript, detected, confidence = await transcribe_audio(wav_path, target_language, native_language, goal)
    if not transcript or confidence < 0.35:
        return SpeechAssessment(transcript=transcript, detected_language=detected, confidence=confidence)

    if not settings.openai_api_key:
        return SpeechAssessment(
            transcript=transcript,
            detected_language=detected,
            confidence=confidence,
            semantic_match=0.5,
            status="ACCEPTED_BEST_ATTEMPT",
            response_target="Good. Let's continue.",
        )

    prompt = {
        "target_language": language_name(target_language),
        "target_language_code": target_language,
        "native_language": language_name(native_language),
        "native_language_code": native_language,
        "transcript": transcript,
        "transcription_detected_language": detected,
        "goal": goal,
        "accepted_meaning": accepted_meaning or [],
        "attempt_number": attempt_number,
        "child_name": child_name,
        "working_difficulty_0_to_1": max(0.0, min(1.0, float(working_difficulty or 0.15))),
        "profile_language_level": language_level or "PRE_A1",
        "dialogue_policy": {
            "use_name_sparingly": True,
            "avoid_echo_if_answer_is_understandable": True,
            "offer_real_examples_when_helping": True,
            "adapt_complexity_during_this_lesson": True
        },
    }
    result = await _evaluate_with_chat(prompt)
    if not result:
        return SpeechAssessment(transcript=transcript, detected_language=detected, confidence=confidence)

    decision = str(result.get("decision", "TECHNICAL_UNCERTAINTY")).upper()
    status = {
        "CORRECT": "ACCEPTED_CORRECT",
        "RETRY": "RETRY_REQUIRED",
        "WRONG_LANGUAGE": "WRONG_LANGUAGE",
        "TECHNICAL_UNCERTAINTY": "TECHNICAL_UNCERTAINTY",
    }.get(decision, "TECHNICAL_UNCERTAINTY")
    return SpeechAssessment(
        transcript=transcript,
        detected_language=str(result.get("detected_language_code") or detected),
        confidence=confidence,
        grammar_errors=list(result.get("grammar_errors") or []),
        pronunciation_errors=list(result.get("pronunciation_errors") or []),
        semantic_match=_coerce_score(result.get("semantic_match"), 0.0),
        status=status,
        feedback_native=str(result.get("feedback_native") or ""),
        corrected_target=str(result.get("corrected_target") or goal),
        response_target=str(result.get("response_target") or ""),
        response_native=str(result.get("response_native") or ""),
    )
