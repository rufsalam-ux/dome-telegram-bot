from __future__ import annotations

# Source slide orders excluded from the runtime route.
REMOVED_SOURCE_ORDERS = {2, *range(25, 40)}
CURRENT_REVISION = 21
CURRENT_LAST_INDEX = 32  # 33 source slides remain in lesson.json


def normalize_lesson_step(step: int, lesson_revision: int = 0) -> int:
    """Map persisted positions from old archives to the v21 list indexes.

    v20 stored raw lesson.json indexes. v21 physically removes source slides
    25–31, while slides 32–39 were already skipped at runtime. Any old position
    in the deleted suitcase block resumes at source slide 40. Positions after
    that block shift seven indexes left.
    """
    step = max(0, int(step or 0))
    revision = int(lesson_revision or 0)
    if revision >= CURRENT_REVISION:
        return min(step, CURRENT_LAST_INDEX)

    # First retain the established removal of source slide 2 for old versions.
    if revision < 16:
        if 31 <= step <= 38:
            step = 31
        elif step >= 39:
            step -= 8
        if step >= 2:
            step -= 1

    # v16–v20 raw index 22 is slide 24. Old indexes 23–38 are deleted or
    # skipped source slides 25–39; resume at new index 32 (source slide 40).
    if 23 <= step <= 38:
        return 32
    if step >= 39:
        step -= 7
    return min(step, CURRENT_LAST_INDEX)


def normalize_v12_lesson_step(step: int) -> int:
    return normalize_lesson_step(step, 0)


def next_runtime_step(slides: list[dict], current_step: int) -> int:
    step = max(0, int(current_step or 0)) + 1
    while step < len(slides):
        order = int(slides[step].get("order", 0) or 0)
        if order not in REMOVED_SOURCE_ORDERS and not slides[step].get("skip_in_runtime"):
            return step
        step += 1
    return len(slides)
