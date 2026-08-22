from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
H = (ROOT / "app/bot/handlers.py").read_text(encoding="utf-8")
C = (ROOT / "app/services/cartoon_builder.py").read_text(encoding="utf-8")


def test_cartoon_is_not_marked_complete_before_telegram_send():
    send_pos = H.index('activity("UPLOAD_START"')
    complete_pos = H.index('complete_session_once(', send_pos)
    assert send_pos < complete_pos
    assert 'await asyncio.wait_for(' in H[send_pos:complete_pos]
    assert 'message.answer_video' in H[send_pos:complete_pos]
    assert 'mark_cartoon_generated' in H[complete_pos:]


def test_primary_render_uses_isolated_temp_output():
    assert 'primary_output = output.with_name(f"{output.stem}.rendering.mp4")' in H
    assert 'build_timeline_cartoon,' in H
    assert 'primary_output,' in H
    assert 'primary_output.replace(output)' in H


def test_fallback_is_transcoded_not_raw_copied():
    movie_section = H[H.index('activity("MOVIE_START"'):H.index('await state.set_state(None)', H.index('activity("MOVIE_START"'))]
    assert 'ensure_telegram_safe_mp4(base_video, output)' in movie_section
    assert 'shutil.copy2(base_video, output)' not in movie_section


def test_telegram_video_has_size_guard_and_send_timeout():
    assert 'telegram_video_max_bytes' in C
    assert 'telegram_send_timeout_seconds' in H
    assert 'Telegram-safe transcode required' in C


def test_failed_send_returns_session_to_retryable_state():
    upload = H.index('activity("UPLOAD_START"')
    tail = H[upload:]
    fail = tail.index('stage="telegram_send"')
    retry = tail.index('session.status = "IN_PROGRESS"', fail)
    assert retry > fail
