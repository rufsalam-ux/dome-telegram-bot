from __future__ import annotations
from dataclasses import dataclass

LEVELS = ["PRE_A1", "A1", "A2", "B1", "B2"]
LEVEL_CENTERS = {"PRE_A1": 0.12, "A1": 0.30, "A2": 0.50, "B1": 0.70, "B2": 0.88}

@dataclass
class AdaptiveScores:
    comprehension: float
    grammar: float
    vocabulary: float
    pronunciation: float
    fluency: float
    independence: float
    recommended_difficulty: float


def clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def score_answer(*, semantic_match: float, grammar_errors: list[str], pronunciation_errors: list[str],
                 transcript: str, attempt_number: int, status: str, response_latency_ms: int = 0,
                 hints_used: int = 0, used_native_language: bool = False,
                 open_question: bool = False) -> AdaptiveScores:
    words = len((transcript or "").split())
    comprehension = clamp(semantic_match)
    grammar = clamp(1.0 - min(len(grammar_errors), 4) * 0.18)
    pronunciation = clamp(1.0 - min(len(pronunciation_errors), 4) * 0.18)
    vocabulary = clamp(0.20 + words / 14.0)
    fluency = clamp(0.25 + words / 18.0)
    latency_penalty = .0 if response_latency_ms <= 0 else min(.18, max(0, response_latency_ms - 3500) / 45_000)
    independence = clamp(1.0 - max(attempt_number - 1, 0) * 0.22 - min(3, max(0, hints_used)) * .10 - latency_penalty)
    if open_question and attempt_number == 1 and words >= 3 and semantic_match >= .7:
        independence = clamp(independence + .08)
    if status == "WRONG_LANGUAGE" or used_native_language:
        comprehension *= 0.65
        independence *= 0.55
    if status == "TECHNICAL_UNCERTAINTY":
        return AdaptiveScores(0, 0, 0, 0, 0, 0, 0.0)
    overall = (
        comprehension * .28 + grammar * .18 + vocabulary * .15 + pronunciation * .14 +
        fluency * .10 + independence * .15
    )
    return AdaptiveScores(comprehension, grammar, vocabulary, pronunciation, fluency, independence, clamp(overall))


def update_running_average(old: float, count: int, new: float) -> float:
    return clamp((old * count + new) / (count + 1))


def level_from_score(score: float, previous: str = "PRE_A1", min_answers: int = 6) -> str:
    if min_answers < 6:
        return previous
    if score >= .80: return "B2"
    if score >= .64: return "B1"
    if score >= .46: return "A2"
    if score >= .25: return "A1"
    return "PRE_A1"


def proficiency_band(score: float) -> str:
    value = clamp(score)
    if value < .25: return "beginner"
    if value < .50: return "emerging"
    if value < .72: return "intermediate"
    return "strong"


def adapt_prompt(base: str, difficulty: float) -> str:
    # The same lesson task can shrink or expand during the lesson.
    if difficulty < .25:
        return base + " Можно ответить одним словом или просто назвать то, что видишь."
    if difficulty < .50:
        return base + " Ответь короткой фразой или одним предложением."
    if difficulty < .72:
        return base + " Ответь предложением и добавь одну деталь."
    return base + " Расскажи подробнее: объясни почему или добавь пример."
