from __future__ import annotations

# Temporary production safety mode requested by owner:
# only the original conversation course is exposed to clients.
CONVERSATION_ONLY = True
ALLOWED_CLIENT_COURSES = {"conversation"}


def client_course_allowed(course_id: str | None) -> bool:
    if not CONVERSATION_ONLY:
        return True
    return str(course_id or "") in ALLOWED_CLIENT_COURSES
