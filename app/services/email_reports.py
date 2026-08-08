from __future__ import annotations
import asyncio
import smtplib
from email.message import EmailMessage
from app.core.config import settings


def build_progress_report(child, completed_lessons: int, attempts: list, homework_text: str | None = None) -> tuple[str, str]:
    subject = f"DOME: прогресс {child.display_name}"
    wrong_language = sum(1 for x in attempts if x.status == "WRONG_LANGUAGE")
    retries = sum(1 for x in attempts if x.status == "RETRY_REQUIRED")
    correct = sum(1 for x in attempts if x.status == "ACCEPTED_CORRECT")
    body = f"""Здравствуйте!

Отчёт DOME по ребёнку: {child.display_name}
Изучаемый язык: {child.target_language}
Текущий уровень: {child.language_level}
Рабочая сложность: {child.working_difficulty:.0%}
Завершено уроков: {completed_lessons}
Правильных ответов: {correct}
Повторных попыток: {retries}
Ответов не на изучаемом языке: {wrong_language}

Навыки:
Понимание: {child.comprehension_score:.0%}
Грамматика: {child.grammar_score:.0%}
Словарный запас: {child.vocabulary_score:.0%}
Произношение: {child.pronunciation_score:.0%}
Беглость: {child.fluency_score:.0%}
Самостоятельность: {child.independence_score:.0%}

DOME
"""
    if homework_text:
        body += "\n🏠 Домашнее задание (необязательно, 3–10 минут):\n" + homework_text + "\n"
    return subject, body


def _send_sync(to_email: str, subject: str, body: str) -> None:
    if not settings.smtp_host or not settings.smtp_from_email:
        raise RuntimeError("SMTP is not configured")
    msg = EmailMessage()
    msg["From"] = settings.smtp_from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(msg)


async def send_progress_report(to_email: str, subject: str, body: str) -> None:
    await asyncio.to_thread(_send_sync, to_email, subject, body)
