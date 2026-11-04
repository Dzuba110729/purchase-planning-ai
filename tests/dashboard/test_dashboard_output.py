import json
from datetime import date
from html.parser import HTMLParser

from purchasing.cli import main


class PublishedFacts(HTMLParser):
    def __init__(self, path):
        super().__init__()
        self.values = {}
        self.labels = {}
        self.active = None
        self.scripts = 0
        self.card_data = None
        text = path.read_text(encoding="utf-8")
        self.feed(text)
        self._read_cards(text)

    def _read_cards(self, text):
        """Карточки товаров собираются в браузере из CARD_DATA: читаем те же значения, что попадут в data-fact."""
        marker = "const CARD_DATA = "
        self.card_data, _ = json.JSONDecoder().raw_decode(text[text.index(marker) + len(marker):])
        fields = self.card_data["fields"]
        for sku, (_, _, values, points, _) in self.card_data["cards"].items():
            for (field, _, source), value in zip(fields, values, strict=True):
                assert source
                self._add(f"items.{sku}.{field}", *(value or ("", self.card_data["nodata"])))
            for month, raw, shown in points:
                self._add(f"items.{sku}.monthly_forecast.{month}", raw, shown)

    def _add(self, key, raw, shown):
        self.values.setdefault(key, set()).add(raw)
        self.labels.setdefault((key, raw), set()).add(shown)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "data-fact" in attrs:
            assert attrs["title"]
            key, value = attrs["data-fact"], attrs["data-value"]
            self.values.setdefault(key, set()).add(value)
            if tag == "span":
                self.active = (key, value)
        if tag == "script":
            self.scripts += 1

    def handle_data(self, data):
        if self.active:
            self.labels.setdefault(self.active, set()).add(data)

    def handle_endtag(self, tag):
        if tag == "span":
            self.active = None


def test_dashboard_cli_builds_local_files(sample_facts, tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-key")
    from urllib.request import OpenerDirector

    def forbid_network(*args, **kwargs):
        raise AssertionError("unexpected provider request")

    monkeypatch.setattr(OpenerDirector, "open", forbid_network)
    facts_path = tmp_path / "facts.json"
    facts_path.write_text(json.dumps(sample_facts), encoding="utf-8")
    target = tmp_path / "site"
    assert main(["dashboard", "--facts", str(facts_path), "--out", str(target)]) == 0
    html = target / "index.html"
    assert html.is_file()
    assert (target / "order.xlsx").is_file()
    assert set(p.name for p in target.iterdir()) == {"index.html", "order.xlsx"}
    assert "Шаблон" in html.read_text(encoding="utf-8")


def assert_numeric_facts(page, facts):
    expected = {"plan_date": facts["plan_date"], "generated_from.sku_rows": facts["generated_from"]["sku_rows"]}
    item_fields = {
        "stock", "in_transit_qty", "order_qty", "next_order_date", "runout_date", "stock_days",
        "lead_time_days", "cover_days", "daily_demand", "demand_lead_cover", "batch_step",
        "safety_stock", "target_stock", "markup_pct", "overdue_excluded_qty",
    }
    expected.update({f"totals.{key}": value for key, value in facts["totals"].items()})
    for row in facts["items"]:
        sku = row["sku_id"]
        for field in item_fields:
            key = f"items.{sku}.{field}"
            assert key in page.values, key
            expected[key] = row.get(field)
        for point in row.get("monthly_forecast", ()):
            key = f"items.{sku}.monthly_forecast.{point['month']}"
            assert key in page.values, key
            expected[key] = point["qty"]

    published_lists = ("price_review", "discontinue" if "discontinue" in facts["lists"] else "sell_off", "ask_supplier")
    for name in published_lists:
        for row in facts["lists"][name]:
            for field in ("markup_pct", "stock", "stock_days", "promised_date", "days_overdue", "qty"):
                if field not in row:
                    continue
                key = f"lists.{name}.{row['sku_id']}.{field}"
                assert key in page.values, key
                expected.setdefault(key, set()).add("" if row[field] is None else str(row[field]))

    for key, values in page.values.items():
        value = expected[key]
        assert values == (value if isinstance(value, set) else {"" if value is None else str(value)}), key
    for (key, raw), labels in page.labels.items():
        if not raw:
            assert labels == {"В данных нет"}, key
        elif raw.count("-") == 2:
            assert {date.fromisoformat(raw)} == {date.fromisoformat("-".join(reversed(label.split(".")))) for label in labels}, key
        else:
            assert {float(raw)} == {float(label.replace("\u202f", "").replace(",", ".")) for label in labels}, key


def assert_workbook_matches_page(path, page, facts):
    from datetime import datetime

    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True)
    try:
        orders = list(workbook["К заказу"].values)[1:]
        actual = {}
        for sku, _, quantity, when in orders:
            assert type(quantity) is int and quantity > 0
            assert when is None or type(when) is datetime
            actual[sku] = (quantity, when.date().isoformat() if when else None)
        expected = {
            row["sku_id"]: (row["order_qty"], row["next_order_date"])
            for row in facts["items"] if (row["order_qty"] or 0) > 0
        }
        assert len(orders) == len(expected)
        assert actual == expected
        assert sum(row[2] for row in orders) == int(next(iter(page.values["totals.order_qty_total"])))
        assert workbook["Сводка"]["B3"].value == sum(row[2] for row in orders)
    finally:
        workbook.close()


def test_page_facts_workbook_and_public_fields(sample_facts, tmp_path):
    from zipfile import ZipFile

    from purchasing.ai import AIConfig, write_texts
    from purchasing.dashboard import build_site, build_xlsx

    path = build_site(sample_facts, write_texts(sample_facts, config=AIConfig(max_requests=0)), tmp_path)
    xlsx = build_xlsx(sample_facts, tmp_path / "order.xlsx")
    assert_numeric_facts(PublishedFacts(path), sample_facts)
    assert_workbook_matches_page(xlsx, PublishedFacts(path), sample_facts)

    public_html = path.read_bytes()
    with ZipFile(xlsx) as workbook:
        public_sheet_xml = b"".join(workbook.read(entry) for entry in workbook.namelist())
    for private in (b"SUP-PRIVATE", b"PO-PRIVATE", b"PRIVATE NOTES", b"PRIVATE WARNING", b"supplier_id=", b"qty_pending="):
        assert private not in public_html + public_sheet_xml


def test_legacy_facts_without_monthly_rows_and_full_discontinue_list(sample_facts, tmp_path):
    from purchasing.ai import AIConfig, write_texts
    from purchasing.dashboard import build_site

    del sample_facts["lists"]["discontinue"]
    del sample_facts["totals"]["weight_known_items"]
    for row in sample_facts["items"]:
        del row["monthly_forecast"]
    path = build_site(sample_facts, write_texts(sample_facts, config=AIConfig(max_requests=0)), tmp_path)
    assert_numeric_facts(PublishedFacts(path), sample_facts)
    assert "Помесячный прогноз: в данных нет." in path.read_text(encoding="utf-8")


def test_escaped_ai_text_is_literal_html(sample_facts, tmp_path):
    from dataclasses import replace

    from purchasing.ai import AIConfig, Text, write_texts
    from purchasing.dashboard import build_site

    texts = write_texts(sample_facts, config=AIConfig(max_requests=0))
    injection = Text("<script>alert(1)</script>", (), False, "ai")
    texts = replace(texts, summary=injection, anomalies=injection, items={sample_facts["items"][0]["sku_id"]: injection})
    path = build_site(sample_facts, texts, tmp_path)
    rendered = path.read_text(encoding="utf-8")
    assert PublishedFacts(path).scripts == 1
    # сводка и риски — в самой странице; пояснение к товару — в данных карточки (в JSON знак «<» экранирован)
    assert rendered.count("&lt;script&gt;alert(1)&lt;/script&gt;") == 2
    assert rendered.count("Пояснение ИИ, цифры проверены") == 3  # две записи в странице и подпись в сборщике карточки
    assert "\\u003cscript>alert(1)\\u003c/script>" in rendered
    assert "<script>alert(1)</script>" not in rendered


def test_every_real_fact_and_workbook_row(real_facts, tmp_path):
    from purchasing.ai import AIConfig, write_texts
    from purchasing.dashboard import build_site, build_xlsx

    page = PublishedFacts(build_site(real_facts, write_texts(real_facts, config=AIConfig(max_requests=0)), tmp_path))
    xlsx = build_xlsx(real_facts, tmp_path / "order.xlsx")
    assert_numeric_facts(page, real_facts)
    assert_workbook_matches_page(xlsx, page, real_facts)


def test_real_page_is_light_enough_for_a_phone(real_facts, tmp_path):
    # Правка 3: было 6,3 МБ (все карточки товаров лежали в странице готовыми); цель — не больше 2 МБ без сжатия.
    from purchasing.ai import AIConfig, write_texts
    from purchasing.dashboard import build_site

    path = build_site(real_facts, write_texts(real_facts, config=AIConfig(max_requests=0)), tmp_path)
    assert path.stat().st_size <= 2 * 1024 * 1024, path.stat().st_size


def test_discontinue_and_sell_off_lists_are_labelled_honestly(sample_facts, tmp_path):
    # Правка 4: «вывести» — все мёртвые товары, «распродать остаток» — только с остатком на складе.
    from purchasing.ai import AIConfig, write_texts
    from purchasing.dashboard import build_site

    sample_facts["lists"]["discontinue"] = [
        {"sku_id": "SKU-000004", "category": "grain_mills", "stock": 5},
        {"sku_id": "SKU-000005", "category": "grain_mills", "stock": 0},
    ]
    sample_facts["lists"]["sell_off"] = [{"sku_id": "SKU-000004", "category": "grain_mills", "stock": 5}]
    sample_facts["totals"].update(dead_total=2, sell_off_items=1, sell_off_qty=5)
    html = build_site(sample_facts, write_texts(sample_facts, config=AIConfig(max_requests=0)), tmp_path).read_text(encoding="utf-8")
    assert "Распродать / вывести" not in html
    import re

    summaries = [re.sub(r"<[^>]+>", "", m) for m in re.findall(r"<summary>(.*?)</summary>", html)]
    assert "Вывести из ассортимента (нет продаж 12 мес.) 2 поз." in summaries
    assert "Распродать остаток 1 поз., 5 шт." in summaries
    folds = html.split("<summary>Распродать остаток")[1].split("</details>")[0]
    assert "SKU-000004" in folds and "SKU-000005" not in folds


def test_real_labels_match_facts(real_facts, tmp_path):
    import re

    from purchasing.ai import AIConfig, write_texts
    from purchasing.dashboard import build_site

    html = build_site(real_facts, write_texts(real_facts, config=AIConfig(max_requests=0)), tmp_path).read_text(encoding="utf-8")
    t = real_facts["totals"]
    summaries = [re.sub(r"<[^>]+>", "", m).replace(" ", " ") for m in re.findall(r"<summary>(.*?)</summary>", html)]
    assert f"Вывести из ассортимента (нет продаж 12 мес.) {t['dead_total']} поз." in summaries
    assert f"Распродать остаток {t['sell_off_items']} поз., {t['sell_off_qty']} шт." in summaries
    assert t["sell_off_items"] == sum(1 for x in real_facts["lists"]["discontinue"] if x["stock"] > 0)
