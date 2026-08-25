from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.i18n import language_name
from app.services.conversational_tutor import TutorTurn, build_assessed_turn

log = logging.getLogger("dome.speech")

_NON_SPEECH_TRANSCRIPTS = {
    "music", "applause", "silence", "background noise", "noise",
    "музыка", "тишина", "шум", "аплодисменты",
    "uh", "um", "erm", "hmm", "mm", "ah", "eh",
    "ээ", "эм", "мм", "м-м", "аа", "а-а",
}


def is_non_speech_transcript(value: str) -> bool:
    """Identify ASR placeholders/garbage without rejecting valid one-word answers."""
    text = str(value or "").strip().lower()
    if not text:
        return True
    normalized = re.sub(r"[\[\](){}<>♪♫.,!?…:;\-—_]+", " ", text)
    normalized = " ".join(normalized.split())
    if not normalized or normalized in _NON_SPEECH_TRANSCRIPTS:
        return True
    if any(marker in normalized for marker in ("subtitles by", "thanks for watching", "продолжение следует")):
        return True
    compact = re.sub(r"\W+", "", normalized, flags=re.UNICODE)
    return bool(compact) and len(set(compact)) == 1 and len(compact) >= 3


def _transcription_confidence(payload: dict) -> float:
    """Derive confidence from provider evidence instead of inventing a score."""
    token_logprobs: list[float] = []
    for item in payload.get("logprobs") or []:
        if not isinstance(item, dict):
            continue
        try:
            token_logprobs.append(float(item["logprob"]))
        except (KeyError, TypeError, ValueError):
            continue
    if token_logprobs:
        return max(0.0, min(1.0, math.exp(sum(token_logprobs) / len(token_logprobs))))

    weighted_logprob = 0.0
    total_weight = 0.0
    no_speech_probability = 0.0
    for segment in payload.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        try:
            start = float(segment.get("start", 0.0))
            end = float(segment.get("end", start))
            weight = max(0.05, end - start)
            weighted_logprob += float(segment["avg_logprob"]) * weight
            total_weight += weight
            no_speech_probability = max(no_speech_probability, float(segment.get("no_speech_prob", 0.0)))
        except (KeyError, TypeError, ValueError):
            continue
    if total_weight:
        confidence = math.exp(weighted_logprob / total_weight) * (1.0 - no_speech_probability)
        return max(0.0, min(1.0, confidence))
    # Missing confidence evidence is unsafe: semantic grading must not receive it.
    return 0.0


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
    tutor_turn: TutorTurn | None = None

    def __post_init__(self):
        self.grammar_errors = self.grammar_errors or []
        self.pronunciation_errors = self.pronunciation_errors or []


async def _transcribe_with_model(wav_path: Path, model: str, language: str = "", prompt: str = "") -> tuple[str, str, float]:
    headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
    data = {"model": model, "response_format": "json"}
    if model.startswith("gpt-4o"):
        data["include[]"] = "logprobs"
    elif model == "whisper-1":
        data["response_format"] = "verbose_json"
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
    return text, language, (_transcription_confidence(payload) if text else 0.0)


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
        "pronunciation_errors, feedback_native, corrected_target, reaction_target, response_native, "
        "follow_up_target, model_answer_target, native_hint, emotion, decision. "
        "decision must be CORRECT, RETRY, WRONG_LANGUAGE, or TECHNICAL_UNCERTAINTY. "
        "Do not punish likely transcription errors. Accept correct close paraphrases. Preserve the child's chosen meaning and nouns: never replace cat with dog or one chosen animal/object with another. "
        "reaction_target must react to the ACTUAL meaning of this answer with genuine delight, curiosity, surprise, support, or a gentle correction. "
        "Never output an interchangeable Nice/Great/Good regardless of the answer, and never praise a wrong or empty answer. "
        "Do not mechanically repeat or paraphrase what the child just said when it is already understandable. "
        "follow_up_target must be empty unless dialogue_policy.allow_follow_up is true, the answer is correct, and follow-up slots remain. "
        "When allowed, ask exactly one short, naturally connected question based on the child's answer. Never create an unrelated task. "
        "When the child needs help, give one short usable example that directly answers the CURRENT goal. Never reuse nouns, animals, places, facts, or questions from another task. "
        "Use the child's name only occasionally when a name is provided, never in every reply. "
        "At low difficulty accept one-word/very short answers. Never invite an extra reason, detail, comparison or dialogue unless the CURRENT goal explicitly requests it. "
        "For PRE_A1 use no more than two very short sentences and at most one question in the whole turn. "
        "corrected_target and model_answer_target must be valid direct answers to the CURRENT goal, never praise. "
        "response_native/native_hint are brief and only needed for wrong-language, off-topic, confused, or explicitly requested progressive help. "
        "emotion must be one of warm, happy, curious, surprised, encouraging, gentle_correction."
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
    allow_follow_up: bool = False,
    max_follow_ups: int = 0,
    follow_up_count: int = 0,
    conversation_goal: str = "",
) -> SpeechAssessment:
    transcript, detected, confidence = await transcribe_audio(wav_path, target_language, native_language, goal)
    if is_non_speech_transcript(transcript) or confidence < 0.35:
        return SpeechAssessment(
            transcript=transcript,
            detected_language=detected,
            confidence=confidence,
            status="NO_SPEECH",
        )

    if not settings.openai_api_key:
        return SpeechAssessment(
            transcript=transcript,
            detected_language=detected,
            confidence=confidence,
            semantic_match=0.5,
            status="ACCEPTED_BEST_ATTEMPT",
            response_target="Let's continue.",
            tutor_turn=TutorTurn(reaction_target="Let's continue.", complete=True, reason="offline_fallback"),
        )

    prompt = {
        "target_language": language_name(target_language),
        "target_language_code": target_language,
        "native_language": language_name(native_language),
        "native_language_code": native_language,
        "transcript": transcript,
        "transcription_detected_language": detected,
        "goal": goal,
        "conversation_goal": conversation_goal or goal,
        "accepted_meaning": accepted_meaning or [],
        "attempt_number": attempt_number,
        "child_name": child_name,
        "working_difficulty_0_to_1": max(0.0, min(1.0, float(working_difficulty or 0.15))),
        "profile_language_level": language_level or "PRE_A1",
        "dialogue_policy": {
            "use_name_sparingly": True,
            "avoid_echo_if_answer_is_understandable": True,
            "offer_real_examples_when_helping": True,
            "adapt_complexity_during_this_lesson": True,
            "allow_follow_up": bool(allow_follow_up),
            "max_follow_ups": max(0, int(max_follow_ups)),
            "follow_up_count": max(0, int(follow_up_count)),
            "remaining_follow_ups": max(0, int(max_follow_ups) - int(follow_up_count)),
            "pre_a1_max_questions_per_turn": 1,
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
    accepted = status.startswith("ACCEPTED")
    turn = build_assessed_turn(
        result,
        accepted=accepted,
        allow_follow_up=allow_follow_up,
        follow_up_count=follow_up_count,
        max_follow_ups=max_follow_ups,
        answer_text=transcript,
    )
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
        response_target=turn.reaction_target,
        response_native=str(result.get("response_native") or ""),
        tutor_turn=turn,
    )
