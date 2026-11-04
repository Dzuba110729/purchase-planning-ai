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
        reply("Код товара SKU-ABCDEF. Остаток 17 шт. Прогноз около 1,26 шт. в день.",
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
    request_message = json.loads(body(request)["messages"][-1]["content"])
    assert reply("Остаток 17 шт.", ["stock"]) in request_message["phrases"]
    assert reply("Дата заказа 2026-08-14.", ["order_qty", "next_order_date"]) in request_message["phrases"]
    assert reply("Прогноз около 1,26 шт. в день.", ["daily_demand"]) in request_message["phrases"]
    assert "Код товара SKU-ABCDEF. Остаток 17 шт." not in journal.read_text()
    assert len(journal.read_text().splitlines()) == 5
    assert journal.stat().st_mode & 0o777 == 0o600
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(("bad_answer", "correction"), [
    (reply("Остаток 999 шт.", ["stock"]), "фразы"),
    (reply("Остаток 40 шт.", ["stock"]), "фразы"),
    (reply("Заказать 17 шт.", ["stock", "order_qty"]), "фразы"),
    (reply("Заказать 17 шт.", ["stock"]), "фразы"),
    (reply("Остаток 40 шт.", ["stock", "order_qty"]), "фразы"),
    (reply("Остаток 1 256 шт.", ["daily_demand"]), "фразы"),
    (reply("Прогноз 1,25 шт.", ["daily_demand"]), "фразы"),
    (reply("Остаток сто шт.", ["stock"]), "фразы"),
    (reply("Остаток пятьсот штук.", ["stock"]), "фразы"),
    (reply("В пути четыре шт.", ["in_transit_qty"]), "фразы"),
    (reply("Дата заказа 2026-08-17.", ["next_order_date", "stock", "plan_date"]), "фразы"),
    (reply("Дата заказа 2026-08-14.", ["stock"]), "ключ"),
    (reply("Дата заказа 2026-08-23.", ["runout_date", "next_order_date"]), "фразы"),
    (reply("Дата заказа 8/14.", ["sales_qty", "demand_lead_cover"]), "фразы"),
    (reply("Дата заказа 14.08.2026.", ["next_order_date"]), "фразы"),
    (reply("Дата заказа 14 августа.", ["next_order_date"]), "фразы"),
    (reply("Остаток SKU-UVWXYZ 17 шт.", ["sku_id", "stock"]), "фразы"),
    (reply("Остаток 17 шт. у поставщика Ромашка.", ["stock"]), "фразы"),
    (reply("Остаток 17 шт. у поставщика ромашка.", ["stock"]), "фразы"),
    (reply("Макита в наличии.", ["stock"]), "фразы"),
    (reply("Остаток 17 шт. у макиты.", ["stock"]), "фразы"),
    (reply("Остаток 17 шт. Вес в данных нет.", ["stock"], True), "фразы"),
    (reply("К заказу 40 руб.", ["order_qty"]), "фразы"),
    (reply("К заказу 40 ₽.", ["order_qty"]), "фразы"),
    (reply("К заказу 40 баксов.", ["order_qty"]), "фразы"),
    (reply("К заказу 40 кило.", ["order_qty"]), "фразы"),
    (reply("К заказу 40 контейнеров.", ["order_qty"]), "фразы"),
    (reply("Риск дефицита отсутствует.", ["shortage_risk"]), "фразы"),
    (reply("Риск дефицита не возникает.", ["shortage_risk"]), "фразы"),
    (reply("Не нужно заказывать 40 шт.", ["order_qty"]), "фразы"),
    (reply("Риск излишка есть.", ["shortage_risk"]), "фразы"),
    (reply("К заказу 40 у.е.", ["order_qty"]), "фразы"),
    (reply("Остаток макиты 17 шт.", ["stock"]), "фразы"),
    (reply("2026-08-23 — дата заказа.", ["runout_date", "next_order_date"]), "фразы"),
    (reply("Риск дефицита исключён.", ["shortage_risk"]), "фразы"),
    (reply("Заказывать 40 шт. не следует.", ["order_qty"]), "фразы"),
    (reply("Остаток 17 шт.", ["markup_pct"]), "ключ"),
    (reply("Остаток 17 шт.", ["stock"], True), "ключ"),
    (reply("В данных нет. Остаток 17 шт.", ["stock"], True), "фразы"),
    (reply("Остаток 17 шт. Остаток 17 шт.", ["stock"]), "фразы"),
    (reply("Остаток 17 шт.", ["stock", "stock"]), "ключ"),
    (reply("Остаток 17 шт.", ["stock", "order_qty"]), "ключ"),
    (reply("Дата заказа 2026-08-14.", ["next_order_date"]), "ключ"),
    (reply("Прогноз 1,256 шт. в день. Прогноз около 1,26 шт. в день.", ["daily_demand"]), "фразы"),
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
    assert "Остаток 17 шт." in texts.items["SKU-ABCDEF"].text
    assert "К заказу 40 шт." in texts.items["SKU-ABCDEF"].text
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
        reply("К заказу 40 шт. Дата заказа 2026-08-14. По прогнозу запас закончится 2026-08-23.",
              ["order_qty", "next_order_date", "runout_date"]),
    )
    result = write_texts(facts, journal_path=tmp_path / "requests.jsonl").items["SKU-ABCDEF"]
    assert result.source == "ai"
    assert len(transport.requests) == 3


def test_supported_zero_order_and_absence_of_shortage_risk_remain_available(facts, tmp_path, mock_http):
    facts["items"][0].update(order_qty=0, shortage_risk=False)
    transport = mock_http(
        reply("К заказу 40 шт.", ["order_qty_total"]),
        reply("В данных нет.", [], True),
        reply("К заказу 0 шт. Риск дефицита не отмечен.", ["order_qty", "shortage_risk"]),
    )
    result = write_texts(facts, journal_path=tmp_path / "requests.jsonl").items["SKU-ABCDEF"]
    assert result.source == "ai"
    assert len(transport.requests) == 3



def test_conflicting_order_facts_have_no_date_or_urgent_action(facts, tmp_path, mock_http):
    facts["items"][0].update(order_qty=0, order_now=True, next_order_date=facts["plan_date"])
    facts["totals"].update(order_qty_total=0, order_now_items=1)
    rejected = reply("Заказ рекомендован к размещению сейчас.", ["order_qty", "order_now", "plan_date", "next_order_date"])
    transport = mock_http(
        reply("Активных позиций — 1.", ["live_total"]), reply("В данных нет.", [], True), rejected, rejected,
    )
    texts = write_texts(facts, journal_path=tmp_path / "requests.jsonl")
    summary, item = [json.loads(body(request)["messages"][-1]["content"]) for request in transport.requests[::2]]
    assert all(not {"order_qty_total", "order_now_items"}.intersection(phrase["used_facts"])
               for phrase in summary["phrases"])
    assert all(not {"order_now", "next_order_date"}.intersection(phrase["used_facts"])
               for phrase in item["phrases"])
    assert texts.items["SKU-ABCDEF"].source == "template"
    assert "К заказу 0 шт." in texts.items["SKU-ABCDEF"].text
    assert "Дата заказа" not in texts.items["SKU-ABCDEF"].text


def test_missing_order_quantity_is_explicit_and_grounded(facts, tmp_path, mock_http):
    facts["items"][0]["order_qty"] = None
    transport = mock_http(
        reply("К заказу 40 шт.", ["order_qty_total"]),
        reply("В данных нет.", [], True),
        reply("Остаток 17 шт. Количество к заказу — в данных нет.", ["stock", "order_qty"], True),
    )
    text = write_texts(facts, journal_path=tmp_path / "requests.jsonl").items["SKU-ABCDEF"]
    assert text.source == "ai"
    assert text.insufficient_data is True
    assert len(transport.requests) == 3



def test_summary_and_anomaly_choices_reference_only_their_own_facts(facts, tmp_path, mock_http):
    facts["totals"].update(overstock_risk_items=2, ask_supplier_items=3)
    transport = mock_http(
        reply("К заказу 40 шт. Позиций с риском дефицита — 1.", ["shortage_risk_items", "order_qty_total"]),
        reply("Заказов для уточнения у поставщика — 3. Позиций с риском излишка — 2.",
              ["ask_supplier_items", "overstock_risk_items"]),
        reply("Риск дефицита отмечен.", ["shortage_risk"]),
    )
    texts = write_texts(facts, journal_path=tmp_path / "requests.jsonl")
    assert texts.summary.source == texts.anomalies.source == "ai"
    assert texts.anomalies.used_facts == ("ask_supplier_items", "overstock_risk_items")
    assert texts.items["SKU-ABCDEF"].used_facts == ("shortage_risk",)
    assert len(transport.requests) == 3
