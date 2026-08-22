from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger("dome.activity")


def activity(event: str, **fields: Any) -> None:
    """Write one searchable structured activity line to Railway stdout."""
    payload = {"event": event}
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            payload[key] = value
        else:
            payload[key] = str(value)
    log.info("ACTIVITY | %s", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
