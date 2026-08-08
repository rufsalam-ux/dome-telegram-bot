from __future__ import annotations

from copy import deepcopy

from app.services import platform_settings as ps
from app.services.pricing_engine import quote_interest_lessons, quote_regular_period


def test_feature_resolution_global_and_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "CONFIG_DIR", tmp_path)
    ps.ensure_defaults()
    cfg = ps.load_settings("features")
    cfg["features"]["camera"]["mode"] = "enabled"
    cfg["overrides"]["courses"]["reading"] = {"camera": "disabled"}
    cfg["overrides"]["lessons"]["lesson_7"] = {"camera": "enabled"}
    ps.save_settings("features", cfg)
    assert ps.resolve_feature("camera") == "enabled"
    assert ps.resolve_feature("camera", course_id="reading") == "disabled"
    assert ps.resolve_feature("camera", course_id="reading", lesson_id="lesson_7") == "enabled"


def test_interest_pricing_uses_editable_profit_and_buffer(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "CONFIG_DIR", tmp_path)
    ps.ensure_defaults()
    # pricing_engine imported load_settings directly, point it at patched loader
    import app.services.pricing_engine as pe
    monkeypatch.setattr(pe, "load_settings", ps.load_settings)
    cfg = ps.load_settings("pricing")
    cfg["interest_lessons"]["profit_per_lesson"] = 30.0
    cfg["interest_lessons"]["cost_buffer_percent"] = 10.0
    ps.save_settings("pricing", cfg)
    q = pe.quote_interest_lessons({"ai_text": 2.0, "speech_to_text": 1.0, "text_to_speech": 1.0}, 2)
    assert q.quantity == 2
    assert q.customer_price >= 60.0
    assert q.markup == 60.0


def test_regular_price_minimums(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "CONFIG_DIR", tmp_path)
    ps.ensure_defaults()
    import app.services.pricing_engine as pe
    monkeypatch.setattr(pe, "load_settings", ps.load_settings)
    assert pe.quote_regular_period(0.5, 4, annual=False).customer_price == 49.0
    assert pe.quote_regular_period(0.5, 52, annual=True).customer_price == 490.0


def test_defaults_include_risky_features_off(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "CONFIG_DIR", tmp_path)
    ps.ensure_defaults()
    cfg = ps.load_settings("features")
    assert cfg["features"]["group_lessons"]["mode"] == "disabled"
    assert cfg["features"]["random_child_matching"]["mode"] == "disabled"
    assert cfg["features"]["pose_tracking"]["mode"] == "disabled"
