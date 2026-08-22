from __future__ import annotations
import asyncio
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
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


def _message(to_email: str, subject: str, body: str) -> EmailMessage:
    missing = settings.smtp_missing_variables
    if missing:
        raise RuntimeError(
            "SMTP configuration is incomplete; missing Railway variables: "
            + ", ".join(missing)
        )
    msg = EmailMessage()
    msg["From"] = formataddr((settings.smtp_from_name.strip(), settings.smtp_from_email))
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def _deliver(msg: EmailMessage) -> None:
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        smtp.ehlo()
        if settings.smtp_starttls:
            smtp.starttls()
            smtp.ehlo()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(msg)


def _send_sync(to_email: str, subject: str, body: str) -> None:
    _deliver(_message(to_email, subject, body))


async def send_progress_report(to_email: str, subject: str, body: str) -> None:
    await asyncio.to_thread(_send_sync, to_email, subject, body)



async def send_verification_email(to_email: str, code: str, ttl_minutes: int = 10) -> None:
    subject = "DOME — подтверждение email"
    body = f"""Здравствуйте!

Код подтверждения DOME: {code}

Код действует {ttl_minutes} минут. Если вы не создавали аккаунт DOME, просто проигнорируйте это письмо.

DOME / BilingvaDom
"""
    await asyncio.to_thread(_send_sync, to_email, subject, body)


async def send_password_reset_email(to_email: str, code: str, ttl_minutes: int = 10) -> None:
    subject = "DOME — восстановление пароля"
    body = f"""Здравствуйте!

Код для восстановления пароля DOME: {code}

Код действует {ttl_minutes} минут. Если вы не запрашивали восстановление, ничего делать не нужно.

DOME / BilingvaDom
"""
    await asyncio.to_thread(_send_sync, to_email, subject, body)

def _send_with_attachment_sync(to_email: str, subject: str, body: str, attachment_path: str | None = None) -> None:
    msg = _message(to_email, subject, body)
    if attachment_path:
        from pathlib import Path
        p=Path(attachment_path)
        if p.exists() and p.is_file():
            ext=p.suffix.lower(); maintype,subtype=('application','pdf') if ext=='.pdf' else ('application','octet-stream')
            msg.add_attachment(p.read_bytes(),maintype=maintype,subtype=subtype,filename=p.name)
    _deliver(msg)

async def send_homework_email(to_email: str, child_name: str, lesson_title: str, summary: str, attachment_path: str | None = None) -> None:
    subject=f'DOME: домашнее задание — {lesson_title}'
    body=f'''Здравствуйте!\n\nПосле урока {lesson_title} для {child_name} доступно домашнее задание.\n\n{summary}\n\nИнтерактивную версию ребёнок может выполнить прямо в приложении DOME.\n\nDOME'''
    await asyncio.to_thread(_send_with_attachment_sync,to_email,subject,body,attachment_path)


def build_course_ending_email(child_name: str, course_title: str, remaining: int, options: list[str], recommended: str | None = None) -> tuple[str, str]:
    subject = f"DOME: в курсе «{course_title}» осталось {remaining} занятия" if remaining != 1 else f"DOME: последний урок курса «{course_title}»"
    rec = f"\nМы рекомендуем следующий шаг: {recommended}." if recommended else ""
    opts = "\n".join(f"• {x}" for x in options)
    body = f"""Здравствуйте!\n\n{child_name} подходит к завершению курса «{course_title}».\nВ текущем курсе осталось занятий: {remaining}.{rec}\n\nВы уже можете выбрать, что будет дальше:\n{opts}\n\nВыбор можно сделать прямо в приложении DOME. Если вы выберете следующий курс заранее, обучение продолжится без перерыва после последнего занятия.\n\nС уважением,\nDOME / BilingvaDom"""
    return subject, body


def build_course_completed_email(child_name: str, course_title: str, options: list[str], recommended: str | None = None) -> tuple[str, str]:
    subject = f"DOME: {child_name} завершил(а) курс «{course_title}»"
    rec = f"\nПо результатам обучения мы рекомендуем: {recommended}." if recommended else ""
    opts = "\n".join(f"• {x}" for x in options)
    body = f"""Здравствуйте!\n\nПоздравляем — {child_name} завершил(а) курс «{course_title}». Это был последний урок текущего курса.{rec}\n\nЧтобы продолжить обучение без перерыва, выберите следующий вариант:\n{opts}\n\nВы можете выбрать следующий курс или повторить текущий курс для закрепления. При повторе задания будут использоваться как новый цикл обучения, а история прогресса ребёнка сохранится.\n\nОткройте DOME и выберите вариант продолжения.\n\nС уважением,\nDOME / BilingvaDom"""
    return subject, body
