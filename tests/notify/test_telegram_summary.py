import io
import json
import urllib.request

from purchasing import notify
from purchasing.ai import AIConfig, write_texts
from purchasing.dashboard import build_site


def _page(tmp_path, quantity, *, shortage):
    facts = {
        "schema_version": 1,
        "plan_date": "2026-11-04",
        "totals": {
            "live_total": 1, "order_qty_total": quantity, "order_now_items": 1,
            "shortage_risk_items": int(shortage), "overstock_risk_items": 0,
        },
        "items": [{
            "sku_id": "SKU-AB12CD", "category": "grain_mills", "order_qty": quantity,
            "order_now": True, "next_order_date": "2026-11-04", "shortage_risk": shortage,
            "in_auto_purchase": True, "supplier_id": "PRIVATE-SUPPLIER",
        }],
        "lists": {},
    }
    texts = write_texts(facts, config=AIConfig(max_requests=0))
    return build_site(facts, texts, tmp_path).read_bytes()


def test_summary_uses_current_plan_and_previous_public_page(tmp_path, monkeypatch):
    calls = []

    class Telegram:
        def open(self, request, *, timeout):
            calls.append((request, timeout))
            return io.BytesIO(b'{"ok": true, "result": {"chat": {"id": -123}}}')

    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Telegram())
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-123")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:test-value")
    previous = notify.read_public_plan(_page(tmp_path, 8, shortage=False))
    current = notify.read_public_plan(_page(tmp_path, 12, shortage=True))

    notify.send_summary(current, previous, "https://example.github.io/demo/")

    [(request, _)] = calls
    assert request.full_url == "https://api.telegram.org/bot1:test-value/sendMessage"
    assert request.method == "POST"
    payload = json.loads(request.data)
    assert payload["chat_id"] == -123
    assert "parse_mode" not in payload
    assert "12 шт." in payload["text"] and "+4 шт." in payload["text"]
    assert "SKU-AB12CD" in payload["text"] and "Новый риск дефицита" in payload["text"]
    assert "ИИ недоступен" in payload["text"]
    assert "https://example.github.io/demo/" in payload["text"]
    assert "PRIVATE-SUPPLIER" not in payload["text"]


def _fake_telegram(monkeypatch, replies):
    calls = []

    class Telegram:
        def open(self, request, *, timeout=None):
            calls.append(request)
            return io.BytesIO(replies.pop(0))

    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Telegram())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:test-value")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-123")
    return calls


def test_failure_message_says_dashboard_is_unchanged(monkeypatch):
    calls = _fake_telegram(monkeypatch, [b'{"ok": true, "result": {"chat": {"id": -123}}}'])

    notify.send_failure("data", "https://example.github.io/demo/")

    text = json.loads(calls[0].data)["text"]
    assert "остановлен" in text and "Дашборд не обновлён" in text
    assert "https://example.github.io/demo/" in text


def test_summary_marks_unavailable_ai_and_telegram_errors_hide_token(tmp_path, monkeypatch):
    _fake_telegram(monkeypatch, [b'{"ok": false}'])
    plan = notify.read_public_plan(_page(tmp_path, 8, shortage=False))
    try:
        notify.send_summary(plan, None, "https://example.github.io/demo/", first=True)
    except notify.NotificationError as exc:
        assert "test-value" not in str(exc)
    else:
        raise AssertionError("ожидали отказ при ok=false")
