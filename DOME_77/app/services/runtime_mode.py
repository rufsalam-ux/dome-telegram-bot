from __future__ import annotations

# Set to False so courses managed via Admin Panel appear dynamically in mobile client
CONVERSATION_ONLY = False
ALLOWED_CLIENT_COURSES = {"conversation"}


def client_course_allowed(course_id: str | None) -> bool:
    if not CONVERSATION_ONLY:
        return True
    return str(course_id or "") in ALLOWED_CLIENT_COURSES
