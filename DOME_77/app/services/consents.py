from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Parent, UserConsent

log = logging.getLogger("dome.consents")

CURRENT_DOCUMENT_VERSIONS: dict[str, str] = {
    "TERMS_OF_SERVICE": "2026.1",
    "PRIVACY_POLICY": "2026.1",
    "SUBSCRIPTION_TERMS": "2026.1",
    "CANCELLATION_POLICY": "2026.1",
    "PARENT_LEGAL_REP": "2026.1",
    "CHILD_DATA_PROCESSING": "2026.1",
    "MARKETING_NEWSLETTER": "2026.1",
}

DOCUMENT_METADATA = {
    "TERMS_OF_SERVICE": {
        "title": "Пользовательское соглашение",
        "title_en": "Terms of Service",
        "description": "Правила использования интерактивной платформы DOME и условия предоставления сервиса.",
        "required": True,
        "default_checked": False,
    },
    "PRIVACY_POLICY": {
        "title": "Политика конфиденциальности",
        "title_en": "Privacy Policy",
        "description": "Порядок обработки, защиты и хранения персональных данных пользователей платформы.",
        "required": True,
        "default_checked": False,
    },
    "SUBSCRIPTION_TERMS": {
        "title": "Условия подписки и автопродления",
        "title_en": "Subscription & Auto-Renewal Terms",
        "description": "Условия оплаты, периодичность списаний, правила продления тарифов и управления подпиской.",
        "required": True,
        "default_checked": False,
    },
    "CANCELLATION_POLICY": {
        "title": "Правила отмены и возврата",
        "title_en": "Cancellation & Refund Policy",
        "description": "Порядок отмены подписки в любое время и правила возврата средств.",
        "required": True,
        "default_checked": False,
    },
    "PARENT_LEGAL_REP": {
        "title": "Подтверждение статуса законного представителя",
        "title_en": "Parental / Legal Guardian Authority",
        "description": "Подтверждение, что лицо является родителем/законным представителем ребёнка и имеет право дать согласие на обучение.",
        "required": True,
        "default_checked": False,
    },
    "CHILD_DATA_PROCESSING": {
        "title": "Согласие на обработку данных ребёнка для обучения",
        "title_en": "Child Educational Data Processing Consent",
        "description": "Согласие на обработку голосовых записей ответов ребёнка, рисунков и генерацию персонализированного мультфильма урока.",
        "required": True,
        "default_checked": False,
    },
    "MARKETING_NEWSLETTER": {
        "title": "Новости и спецпредложения DOME",
        "title_en": "Marketing Updates & Offers",
        "description": "Получать полезные материалы для родителей, обновления программы и персональные акции DOME.",
        "required": False,
        "default_checked": False,
    },
}

MANDATORY_DOCUMENTS = [k for k, v in DOCUMENT_METADATA.items() if v["required"]]


def get_legal_documents(locale: str = "ru") -> list[dict[str, Any]]:
    is_ru = str(locale or "ru").lower().startswith("ru")
    docs = []
    for doc_type, meta in DOCUMENT_METADATA.items():
        docs.append({
            "document_type": doc_type,
            "version": CURRENT_DOCUMENT_VERSIONS.get(doc_type, "2026.1"),
            "title": meta["title"] if is_ru else meta["title_en"],
            "description": meta["description"],
            "required": meta["required"],
            "default_checked": meta["default_checked"],
        })
    return docs


async def record_user_consents(
    db: AsyncSession,
    *,
    parent_id: int,
    consents: list[dict[str, Any]],
    ip_address: str | None = None,
    user_agent: str | None = None,
    locale: str = "ru",
) -> list[UserConsent]:
    records: list[UserConsent] = []
    now = datetime.utcnow()

    for item in consents:
        doc_type = str(item.get("document_type") or "").strip().upper()
        if doc_type not in DOCUMENT_METADATA:
            continue
        accepted = bool(item.get("accepted", True))
        version = str(item.get("version") or CURRENT_DOCUMENT_VERSIONS.get(doc_type, "2026.1")).strip()

        consent = UserConsent(
            parent_id=parent_id,
            document_type=doc_type,
            document_version=version,
            accepted=accepted,
            accepted_at=now,
            locale=locale[:16] if locale else "ru",
            ip_address=ip_address[:64] if ip_address else None,
            user_agent=user_agent[:512] if user_agent else None,
            metadata_json=json.dumps(item.get("metadata") or {}, ensure_ascii=False),
        )
        db.add(consent)
        records.append(consent)

        # Update marketing opt in directly on parent if this is the newsletter consent
        if doc_type == "MARKETING_NEWSLETTER":
            parent = await db.get(Parent, parent_id)
            if parent:
                parent.marketing_opt_in = accepted

    await db.commit()
    return records


async def get_user_consents(db: AsyncSession, parent_id: int) -> list[dict[str, Any]]:
    query = (
        select(UserConsent)
        .where(UserConsent.parent_id == parent_id)
        .order_by(UserConsent.accepted_at.desc())
    )
    records = (await db.scalars(query)).all()
    return [
        {
            "id": r.id,
            "document_type": r.document_type,
            "document_name": DOCUMENT_METADATA.get(r.document_type, {}).get("title", r.document_type),
            "document_version": r.document_version,
            "accepted": r.accepted,
            "accepted_at": r.accepted_at.isoformat() if r.accepted_at else None,
            "locale": r.locale,
            "ip_address": r.ip_address,
            "user_agent": r.user_agent,
        }
        for r in records
    ]


async def check_parent_consents_up_to_date(db: AsyncSession, parent_id: int) -> tuple[bool, list[str]]:
    missing: list[str] = []
    for doc in MANDATORY_DOCUMENTS:
        curr_ver = CURRENT_DOCUMENT_VERSIONS.get(doc, "2026.1")
        accepted = await db.scalar(
            select(UserConsent.id).where(
                UserConsent.parent_id == parent_id,
                UserConsent.document_type == doc,
                UserConsent.document_version == curr_ver,
                UserConsent.accepted.is_(True),
            ).limit(1)
        )
        if not accepted:
            missing.append(doc)

    return len(missing) == 0, missing
