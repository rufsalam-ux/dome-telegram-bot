import json
from pathlib import Path

def test_slide19_cartoon_line_cannot_skip():
    d=json.loads(Path("content/lessons/demo_001/lesson.json").read_text(encoding="utf-8"))
    s=next(x for x in d["slides"] if x["slide_id"]=="slide_19")
    assert s["required_phrase_id"]=="lesha_clothes"
    assert s["allow_skip"] is False
    assert s["answer_mode"]=="required_voice"

def test_payment_is_tokenized_and_server_checked():
    d=json.loads(Path("config/payments.json").read_text(encoding="utf-8"))
    assert d["tokenized_card_required"] is True
    assert d["store_raw_card_data"] is False
    assert d["server_entitlement_check"] is True

def test_memory_has_hidden_cards():
    s=Path("app/webapp/static/free_topic_task.html").read_text(encoding="utf-8")
    assert "b.textContent='❓'" in s
    assert "setTimeout" in s
