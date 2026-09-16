from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.password_auth import hash_password, verify_password

log = logging.getLogger("dome.admin_credentials")

DEFAULT_ADMIN_PASSWORD = "11111111"


def _credentials_dir() -> Path:
    p = settings.storage_root / "platform-settings"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _credentials_path() -> Path:
    return _credentials_dir() / "admin_credentials.json"


def _read_credentials() -> dict[str, Any]:
    path = _credentials_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("Failed to parse admin_credentials.json: %s", e)
    # Initialize default with 11111111
    hashed = hash_password(DEFAULT_ADMIN_PASSWORD)
    payload = {
        "version": 1,
        "password_hash": hashed,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        log.warning("Failed to write initial admin_credentials.json: %s", e)
    return payload


def verify_admin_password(password: str) -> bool:
    password = str(password or "").strip()
    if not password:
        return False
    creds = _read_credentials()
    stored_hash = str(creds.get("password_hash") or "")
    if not stored_hash:
        return password == DEFAULT_ADMIN_PASSWORD
    return verify_password(password, stored_hash)


def change_admin_password(old_password: str, new_password: str) -> tuple[bool, str]:
    if not verify_admin_password(old_password):
        return False, "Текущий пароль указан неверно."
    new_password = str(new_password or "").strip()
    if len(new_password) < 6:
        return False, "Новый пароль должен содержать не менее 6 символов."
    path = _credentials_path()
    hashed = hash_password(new_password)
    payload = {
        "version": 1,
        "password_hash": hashed,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    log.info("Admin password successfully updated.")
    return True, "Пароль администратора успешно изменён."
