from pathlib import Path


def test_payment_setting_exists():
    text = Path("app/core/config.py").read_text(encoding="utf-8")
    assert "payment_url" in text


def test_payment_buttons_exist():
    text = Path("app/bot/keyboards.py").read_text(encoding="utf-8")
    assert "payment_prompt_keyboard" in text
    assert "payment:skip" in text
    assert "menu:payment" in text


def test_voice_protocol_is_bounded():
    text = Path("app/bot/handlers.py").read_text(encoding="utf-8")
    assert "technical_count < 2" in text
    assert "correction_count <= 3" in text
    assert "simplified_mode" in text
    assert "ACCEPTED_BEST_ATTEMPT" in text


def test_transcription_has_fallback():
    text = Path("app/services/speech_pipeline.py").read_text(encoding="utf-8")
    assert 'models.append("whisper-1")' in text
    assert 'models.append("gpt-4o-mini")' in text


def test_semantic_match_labels_do_not_crash():
    from app.services.speech_pipeline import _coerce_score
    assert _coerce_score('partial') == 0.5
    assert _coerce_score('85%') == 0.85
    assert _coerce_score('2') == 0.02
    assert _coerce_score('nonsense') == 0.0
