import json
import urllib.error

import pytest

from purchasing.ai import write_texts


@pytest.fixture
def facts():
    return {
        "schema_version": 1,
        "plan_date": "2026-08-13",
        "generated_from": {"sales_rows": 9075},
        "items": [
            {
                "sku_id": "SKU-ABCDEF",
                "category": "test_tools",
                "stock": 17,
                "in_transit_qty": 4,
                "daily_demand": 1.256,
                "runout_date": "2026-08-23",
                "next_order_date": "2026-08-14",
                "order_qty": 40,
                "shortage_risk": True,
                "supplier_id": "PRIVATE-CODE",
                "supplier_name": "Секретный поставщик",
                "notes": ["Не отправлять"],
                "markup_pct": 95,
                "price": 9999,
                "weight": 12,
                "container_count": 5,
            }
        ],
        "totals": {"live_total": 1, "order_qty_total": 40, "shortage_risk_items": 1},
        "lists": {"ask_supplier": [{"po_id": "PRIVATE-ORDER"}]},
    }


def reply(text, used_facts=(), missing=False):
    return {"text": text, "used_facts": list(used_facts), "insufficient_data": missing}


class FakeHTTP:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.requests = []

    def __call__(self, request, *, timeout):
        self.requests.append(request)
        answer = next(self.answers)
        if isinstance(answer, Exception):
            raise answer
        return self.Response(answer)

    class Response:
        def __init__(self, answer):
            self.answer = answer

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            if isinstance(self.answer, bytes):
                return self.answer
            return json.dumps({
                "choices": [{"message": {"content": json.dumps(self.answer)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 50, "prompt_cache_hit_tokens": 20,
                          "prompt_cache_miss_tokens": 30, "completion_tokens": 10},
            }).encode()


@pytest.fixture
def mock_http(monkeypatch):
    def install(*answers):
        # Строка-заглушка не является ключом DeepSeek.
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-for-unit-tests")
        transport = FakeHTTP(answers)
        monkeypatch.setattr("urllib.request.OpenerDirector.open", lambda self, *args, **kwargs: transport(*args, **kwargs))
        return transport
    return install


def body(request):
    return json.loads(request.data)


def passport(request):
    return json.loads(body(request)["messages"][-1]["content"])["facts"]


def test_provider_fallback_and_private_allowlisted_request(facts, tmp_path, mock_http, capsys):
    failure = urllib.error.URLError("fake-for-unit-tests")
    transport = mock_http(
        failure, reply("К заказу 40 шт.", ["order_qty_total"]),
        b"broken envelope", b"broken again",
        reply("Остаток SKU-ABCDEF 17 шт. Прогноз 1,26 шт. в день.",
              ["sku_id", "stock", "daily_demand"]),
    )
    journal = tmp_path / "private.jsonl"
    texts = write_texts(facts, journal_path=journal)
    assert texts.summary.source == "ai" and texts.summary.model == "deepseek-flash"
    assert texts.anomalies.source == "template"
    assert texts.items["SKU-ABCDEF"].source == "ai"
    assert texts.items["SKU-ABCDEF"].text.endswith("1,26 шт. в день.")
    assert [body(req)["model"] for req in transport.requests] == [
        "deepseek-v4-pro", "deepseek-flash", "deepseek-v4-pro", "deepseek-flash", "deepseek-flash"
    ]
    request = transport.requests[-1]
    assert body(request)["temperature"] == 0.2
    assert body(request)["thinking"] == {"type": "disabled"}
    assert body(request)["response_format"] == {"type": "json_object"}
    assert passport(request) == {
        "sku_id": "SKU-ABCDEF", "category": "test_tools", "plan_date": "2026-08-13",
        "stock": 17, "in_transit_qty": 4, "sales_qty": "в данных нет",
        "daily_demand": 1.256, "demand_lead_cover": "в данных нет",
        "runout_date": "2026-08-23", "next_order_date": "2026-08-14", "order_qty": 40,
        "order_now": "в данных нет", "shortage_risk": True, "overstock_risk": "в данных нет",
        "overdue_excluded_qty": "в данных нет",
    }
    assert "PRIVATE" not in journal.read_text()
    assert "fake-for-unit-tests" not in journal.read_text()
    assert "Остаток SKU-ABCDEF" not in journal.read_text()
    assert len(journal.read_text().splitlines()) == 5
    assert journal.stat().st_mode & 0o777 == 0o600
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(("bad_answer", "correction"), [
    (reply("Остаток 999 шт.", ["stock"]), "числа"),
    (reply("Остаток 40 шт.", ["stock"]), "числа"),
    (reply("Заказать 17 шт.", ["stock", "order_qty"]), "числа"),
    (reply("Заказать 17 шт.", ["stock"]), "числа"),
    (reply("Остаток 40 шт.", ["stock", "order_qty"]), "числа"),
    (reply("Остаток 1 256 шт.", ["daily_demand"]), "числа"),
    (reply("Прогноз 1,25 шт.", ["daily_demand"]), "числа"),
    (reply("Остаток сто шт.", ["stock"]), "числа"),
    (reply("Остаток пятьсот штук.", ["stock"]), "числа"),
    (reply("В пути четыре шт.", ["in_transit_qty"]), "числа"),
    (reply("Дата заказа 2026-08-17.", ["next_order_date", "stock", "plan_date"]), "даты"),
    (reply("Дата заказа 2026-08-14.", ["stock"]), "даты"),
    (reply("Дата заказа 2026-08-23.", ["runout_date", "next_order_date"]), "даты"),
    (reply("Дата заказа 8/14.", ["sales_qty", "demand_lead_cover"]), "даты"),
    (reply("Дата заказа 14.08.2026.", ["next_order_date"]), "даты"),
    (reply("Дата заказа 14 августа.", ["next_order_date"]), "даты"),
    (reply("Остаток SKU-UVWXYZ 17 шт.", ["sku_id", "stock"]), "коды"),
    (reply("Остаток 17 шт. у поставщика Ромашка.", ["stock"]), "коды"),
    (reply("Остаток 17 шт. у поставщика ромашка.", ["stock"]), "запрещённые"),
    (reply("Макита в наличии.", ["stock"]), "коды"),
    (reply("Остаток 17 шт. у макиты.", ["stock"]), "запрещённые"),
    (reply("Остаток 17 шт. Вес в данных нет.", ["stock"], True), "запрещённые"),
    (reply("К заказу 40 руб.", ["order_qty"]), "запрещённые"),
    (reply("К заказу 40 ₽.", ["order_qty"]), "запрещённые"),
    (reply("К заказу 40 баксов.", ["order_qty"]), "запрещённые"),
    (reply("К заказу 40 кило.", ["order_qty"]), "запрещённые"),
    (reply("К заказу 40 контейнеров.", ["order_qty"]), "запрещённые"),
    (reply("Риск дефицита отсутствует.", ["shortage_risk"]), "риски"),
    (reply("Риск дефицита не возникает.", ["shortage_risk"]), "риски"),
    (reply("Не нужно заказывать 40 шт.", ["order_qty"]), "рекомендацию"),
    (reply("Риск излишка есть.", ["shortage_risk"]), "риски"),
    (reply("Остаток 17 шт.", ["markup_pct"]), "ключи паспорта"),
    (reply("Остаток 17 шт.", ["stock"], True), "ключи паспорта"),
    ({"text": "Остаток 17 шт.", "used_facts": ["stock"], "insufficient_data": 0}, "JSON"),
    ({"text": "Остаток 17 шт.", "used_facts": ["stock"], "insufficient_data": False, "x": 1}, "JSON"),
    ({"text": "Остаток 17 шт.", "insufficient_data": False}, "JSON"),
    ({"text": ["Остаток 17 шт."], "used_facts": ["stock"], "insufficient_data": False}, "JSON"),
    ({"text": "Остаток 17 шт.", "used_facts": "stock", "insufficient_data": False}, "JSON"),
])
def test_invalid_item_answer_gets_one_corrective_retry_then_template(
    bad_answer, correction, facts, tmp_path, mock_http,
):
    facts["items"][0].update(sales_qty=8, demand_lead_cover=14)
    transport = mock_http(
        reply("В данных нет.", [], True), reply("В данных нет.", [], True), bad_answer, bad_answer,
    )
    texts = write_texts(facts, journal_path=tmp_path / "requests.jsonl")
    assert texts.summary.source == "ai" and texts.summary.insufficient_data
    assert texts.items["SKU-ABCDEF"].source == "template"
    assert "Остаток, шт.: 17." in texts.items["SKU-ABCDEF"].text
    assert "К заказу, шт.: 40." in texts.items["SKU-ABCDEF"].text
    assert len(transport.requests) == 4
    assert correction in json.loads(body(transport.requests[-1])["messages"][-1]["content"])["correction"]


def test_forecast_warning_is_local_and_deterministic(facts, tmp_path, mock_http):
    facts["forecast_accuracy"] = {"warning": "UNTRUSTED WARNING"}
    transport = mock_http(
        reply("К заказу 40 шт.", ["order_qty_total"]), reply("В данных нет.", [], True),
        reply("Остаток 17 шт.", ["stock"]),
    )
    texts = write_texts(facts, journal_path=tmp_path / "requests.jsonl")
    assert texts.summary.text.startswith("К заказу 40 шт.")
    assert "Точность прогноза по товару низкая" in texts.summary.text
    assert "UNTRUSTED" not in texts.summary.text
    assert all(b"UNTRUSTED" not in req.data for req in transport.requests)


@pytest.mark.parametrize(("status", "reason"), [(401, "authentication"), (402, "balance")])
def test_account_error_stops_run_without_disclosing_key(status, reason, facts, tmp_path, mock_http, capsys):
    http_error = urllib.error.HTTPError(
        "https://api.deepseek.com/chat/completions", status, "fake-for-unit-tests", {}, None,
    )
    transport = mock_http(http_error)
    texts = write_texts(facts, journal_path=tmp_path / "requests.jsonl")
    assert texts.requests == len(transport.requests) == 1
    assert texts.errors == {reason: 1}
    assert all(text.source == "template" for text in [texts.summary, texts.anomalies, *texts.items.values()])
    assert capsys.readouterr() == ("", "")


def test_no_key_and_unwritable_journal_keep_texts_available(facts, tmp_path, monkeypatch, mock_http):
    transport = mock_http()
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    journal = tmp_path / "journal.jsonl"
    assert write_texts(facts, journal_path=journal).items["SKU-ABCDEF"].source == "template"
    assert not journal.exists()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-for-unit-tests")
    target = tmp_path / "another-file"
    target.write_text("keep")
    journal.symlink_to(target)
    assert write_texts(facts, journal_path=journal).errors == {"journal": 1}
    assert target.read_text() == "keep"
    assert transport.requests == []


def test_request_budget_is_shared_across_all_texts(facts, tmp_path, mock_http):
    from purchasing.ai import AIConfig

    transport = mock_http(reply("К заказу 40 шт.", ["order_qty_total"]))
    texts = write_texts(facts, config=AIConfig(max_requests=1), journal_path=tmp_path / "requests.jsonl")
    assert len(transport.requests) == texts.requests == 1
    assert texts.summary.source == "ai"
    assert texts.anomalies.source == texts.items["SKU-ABCDEF"].source == "template"


def test_truncated_http_body_uses_fallback(facts, tmp_path, mock_http):
    from http.client import IncompleteRead

    transport = mock_http(IncompleteRead(b""), *([reply("В данных нет.", [], True)] * 3))
    texts = write_texts(facts, journal_path=tmp_path / "requests.jsonl")
    assert texts.summary.model == "deepseek-flash"
    assert texts.unaccounted_requests == 1
    assert len(transport.requests) == 4


def test_acceptance_checks_twenty_products_cost_and_probe_privately(
    facts, tmp_path, mock_http, monkeypatch, capsys,
):
    from purchasing.ai import acceptance

    mock_http(
        reply("К заказу 40 шт.", ["order_qty_total"]), reply("В данных нет.", [], True),
        *([reply("Остаток 17 шт.", ["stock"])] * 20), reply("В данных нет.", [], True),
    )
    monkeypatch.chdir(tmp_path)
    facts["items"] = [dict(facts["items"][0], sku_id=f"SKU-{n:06d}") for n in range(22)]
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(facts))
    assert acceptance.main(["--facts", str(path)]) == 0
    report_path, = (tmp_path / "out" / "ai-private").glob("acceptance-*/report.json")
    report = json.loads(report_path.read_text())
    assert report_path.stat().st_mode & 0o777 == 0o600
    assert len(report["items"]) == 20
    assert report["passed_items"] == 20
    assert report["pass_rate"] == 1
    assert report["probe_passed"] is True
    assert report["requests"] == 23
    assert float(report["estimated_cost_usd"]) == pytest.approx(0.00066264)
    stdout, stderr = capsys.readouterr()
    assert stderr == ""
    assert "SKU-" not in stdout and "Остаток" not in stdout and "fake-for-unit-tests" not in stdout



def test_supported_action_and_dates_remain_available(facts, tmp_path, mock_http):
    transport = mock_http(
        reply("К заказу 40 шт.", ["order_qty_total"]),
        reply("В данных нет.", [], True),
        reply("Заказать 40 шт. Дата заказа 2026-08-14. Запас закончится 2026-08-23.",
              ["order_qty", "next_order_date", "runout_date"]),
    )
    result = write_texts(facts, journal_path=tmp_path / "requests.jsonl").items["SKU-ABCDEF"]
    assert result.source == "ai"
    assert len(transport.requests) == 3


def test_supported_absence_of_order_and_shortage_risk_remains_available(facts, tmp_path, mock_http):
    facts["items"][0].update(order_qty=0, shortage_risk=False)
    transport = mock_http(
        reply("К заказу 40 шт.", ["order_qty_total"]),
        reply("В данных нет.", [], True),
        reply("Заказывать не нужно. Риск дефицита не возникает.", ["order_qty", "shortage_risk"]),
    )
    result = write_texts(facts, journal_path=tmp_path / "requests.jsonl").items["SKU-ABCDEF"]
    assert result.source == "ai"
    assert len(transport.requests) == 3
