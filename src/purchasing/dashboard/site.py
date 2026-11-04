"""Render a standalone page without putting customer data in executable code."""

import base64
import hashlib
import json
from collections import defaultdict
from datetime import date
from html import escape
from pathlib import Path

from .model import (
    DATE_SOURCE, ITEM_DATES, ITEM_NUMBERS, MONTHLY_SOURCE, TOTALS, category, category_label, code,
    has_order, iso_date, item_rows, number, purchase_order,
)

ASSETS = Path(__file__).parent
NO_DATA = "В данных нет"
MONTHS = "Январь Февраль Март Апрель Май Июнь Июль Август Сентябрь Октябрь Ноябрь Декабрь".split()
NEXT_DATE = "next_order_date"
CARD_FIELDS = ("stock", "in_transit_qty", "order_qty", NEXT_DATE, "runout_date", "stock_days",
               "lead_time_days", "cover_days", "daily_demand", "demand_lead_cover", "batch_step",
               "safety_stock", "target_stock", "markup_pct", "overdue_excluded_qty")


def _shown(value):
    if value is None:
        return NO_DATA
    if type(value) is int:
        return f"{value:,}".replace(",", "\u202f")
    if type(value) is float:
        return str(value).removesuffix(".0").replace(".", ",")
    when = date.fromisoformat(value)
    return when.strftime("%d.%m.%Y")


def _fact_attrs(value, key, source):
    raw = "" if value is None else str(value)
    return f'data-fact="{escape(key)}" data-value="{escape(raw)}" title="{escape(source)}"'


def _fact(value, key, source):
    return f'<span {_fact_attrs(value, key, source)}>{_shown(value)}</span>'


def _total(totals, key):
    return _fact(number(totals.get(key)), f"totals.{key}", f"facts.json → totals.{key} · {TOTALS[key][1]}")


def _item_fact(row, field):
    _, source = (ITEM_NUMBERS | ITEM_DATES)[field]
    return _fact(row[field], f"items.{row['sku_id']}.{field}", f"facts.json → items[].{field} · {source}")


def _note_tooltip(text, scope):
    sources = []
    for key in text.used_facts:
        if key == "plan_date":
            sources.append(DATE_SOURCE)
        elif scope == "item" and key in (ITEM_NUMBERS | ITEM_DATES):
            sources.append((ITEM_NUMBERS | ITEM_DATES)[key][1])
        elif scope != "item" and key in TOTALS:
            sources.append(TOTALS[key][1])
        elif key == "forecast_accuracy.warning":
            sources.append("предупреждение из бэктеста")
    return "facts.json · " + " / ".join(dict.fromkeys(sources)) if sources else "facts.json"


def _note(text, scope):
    if text is None:
        return ""
    label = "Пояснение ИИ, цифры проверены" if text.source == "ai" else "Шаблон"
    tooltip = escape(_note_tooltip(text, scope))
    return (f'<div class="note"><span class="provenance">{label}</span>'
            f'<p title="{tooltip}">{escape(text.text)}</p></div>')


def _item_link(sku, active=True):
    sku = code(sku)
    if active:
        return f'<button type="button" class="sku" data-card="{sku}">{sku}<span aria-hidden="true"> ↗</span></button>'
    return f'<span class="sku-plain">{sku}</span>'


def _table(headers, rows, row_attrs=None, table_id=""):
    if not rows:
        return '<p class="empty">Нет позиций в этом списке.</p>'
    headings = "".join(f'<th scope="col">{label}</th>' for label in headers)
    body = []
    for index, cells in enumerate(rows):
        attrs = row_attrs[index] if row_attrs else ""
        columns = "".join(f'<td data-label="{header}">{cell}</td>' for header, cell in zip(headers, cells, strict=True))
        body.append(f"<tr{attrs}>{columns}</tr>")
    return (f'<table class="data-table"{table_id}><thead><tr>{headings}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')


def _stats(totals, stats):
    return "".join(
        f'<article class="metric {tone}"><span>{TOTALS[key][0]}</span>'
        f'<strong>{_total(totals, key)}</strong><small>{unit}</small></article>'
        for key, unit, tone in stats
    )


def _order_cells(row):
    return [_item_link(row["sku_id"]), category_label(row["category"]), *(
        _item_fact(row, key) for key in ("order_qty", NEXT_DATE, "stock", "in_transit_qty")
    )]


def _status(row):
    tags = []
    if row["shortage_risk"]:
        tags.append('<span class="tag danger">Риск дефицита</span>')
    if row["overstock_risk"]:
        tags.append('<span class="tag warning">Риск излишка</span>')
    if row["order_now"] and has_order(row):
        tags.append('<span class="tag">Заказать сейчас</span>')
    if not row["in_auto_purchase"]:
        tags.append('<span class="tag">Пересмотр цены</span>')
    return " ".join(tags) if tags else "—"


def _recommendations(rows):
    options = "".join(f'<option value="{value}">{category_label(value)}</option>'
                      for value in sorted({row["category"] for row in rows}, key=category_label))
    attrs = []
    for row in rows:
        modes = " ".join(key for key, match in (
            ("order", has_order(row)), ("now", has_order(row) and row["order_now"]),
            ("shortage", row["shortage_risk"]), ("overstock", row["overstock_risk"]),
        ) if match)
        attrs.append(f' data-sku="{row["sku_id"]}" data-category="{row["category"]}" data-modes="{modes}"'
                     + ("" if has_order(row) else " hidden"))
    table = _table(
        ("Код товара", "Категория", "К заказу, шт.", "Дата заказа", "На складе, шт.", "В пути, шт.", "Статус"),
        [[*_order_cells(row), _status(row)] for row in rows], attrs, ' id="recommendation-table"',
    )
    return f"""
<section id="recommendations" class="section" aria-labelledby="orders-heading">
  <div class="section-heading"><div><p class="eyebrow">План закупки</p><h2 id="orders-heading">Что заказать</h2>
  <p>Откройте код товара, чтобы увидеть прогноз и детали расчёта.</p></div>
  <a class="button" href="order.xlsx" download>Скачать Excel</a></div>
  <div class="panel">
    <form class="filters" id="filters" role="search">
      <div><label for="sku-filter">Код товара</label>
        <input id="sku-filter" type="search" placeholder="Поиск по коду" autocomplete="off"></div>
      <div><label for="category-filter">Категория</label>
        <select id="category-filter"><option value="">Все категории</option>{options}</select></div>
      <div><label for="mode-filter">Рекомендация</label><select id="mode-filter">
        <option value="order">К заказу</option><option value="now">Заказать сейчас</option>
        <option value="shortage">Риск дефицита</option><option value="overstock">Риск излишка</option>
        <option value="all">Все активные позиции</option>
      </select></div>
      <button class="button secondary" type="reset">Сбросить</button>
    </form>
    {table}
    <p class="empty" id="filter-empty" hidden>По этим фильтрам ничего не найдено.</p>
    <div id="pagination" class="pagination" hidden>
      <button id="previous" type="button" class="button secondary">Назад</button>
      <span id="page-status" role="status"></span>
      <button id="next" type="button" class="button secondary">Далее</button>
    </div>
  </div>
</section>"""


def _calendar(rows):
    groups = defaultdict(list)
    for row in rows:
        if has_order(row):
            month = row[NEXT_DATE][:7] if row[NEXT_DATE] else None
            groups[month].append(row)
    periods = []
    for month in sorted(groups, key=lambda value: value or "9999-12"):
        label = "Дата не рассчитана"
        if month:
            label = f'<span title="facts.json → items[].next_order_date">{MONTHS[int(month[5:7]) - 1]} {month[:4]}</span>'
        group = sorted(groups[month], key=lambda row: (row[NEXT_DATE] or "9999-12-31", row["sku_id"]))
        table = _table(
            ("Код товара", "Категория", "Дата заказа", "К заказу, шт."),
            [[_item_link(row["sku_id"]), category_label(row["category"]), _item_fact(row, NEXT_DATE),
              _item_fact(row, "order_qty")] for row in group],
        )
        periods.append(f'<details class="panel fold"><summary>{label}</summary>{table}</details>')
    return f"""
<section id="calendar" class="section" aria-labelledby="calendar-heading">
  <div class="section-heading"><div><p class="eyebrow">Сроки</p><h2 id="calendar-heading">Календарь заказов</h2>
  <p>Разверните нужный месяц, чтобы увидеть даты размещения заказов.</p></div></div>
  <div class="folds">{"".join(periods) or '<p class="empty">Нет запланированных заказов.</p>'}</div>
</section>"""


def _risks(totals, anomalies):
    open_orders = _total(totals, "open_order_items")
    overdue = _total(totals, "overdue_order_items")
    qty = _total(totals, "overdue_order_qty")
    return f"""
<section id="risks" class="section" aria-labelledby="risks-heading">
  <div class="section-heading"><div><p class="eyebrow">Контроль запаса</p><h2 id="risks-heading">Риски</h2>
  <p>Списки включают все активные позиции, в том числе позиции на пересмотр цены.</p></div></div>
  <div class="risk-grid">
    <article class="risk-card danger"><strong>{_total(totals, "shortage_risk_items")}</strong>
      <h3>Риск дефицита</h3><p>Запас закончится до прихода заказа, размещённого на дату плана.</p>
      <a href="#recommendations" data-risk="shortage">Показать позиции ↗</a></article>
    <article class="risk-card warning"><strong>{_total(totals, "overstock_risk_items")}</strong>
      <h3>Риск излишка</h3><p>Запас с ожидаемыми поставками превышает годовой спрос.</p>
      <a href="#recommendations" data-risk="overstock">Показать позиции ↗</a></article>
    <article class="risk-card"><strong>{open_orders}</strong>
      <h3>Открытые заказы</h3><p>Просрочено заказов: {overdue}. Объём просрочки: {qty} шт.</p>
      <a href="#supplier-review">Список для уточнения ↗</a></article>
  </div>
  {_note(anomalies, "anomalies")}
</section>"""


STATUS_TAGS = (("danger", "Риск дефицита"), ("warning", "Риск излишка"), ("", "Заказать сейчас"), ("", "Пересмотр цены"))


def _status_codes(row):
    """Номера тегов статуса в STATUS_TAGS — в том же порядке, что и `_status`."""
    return [index for index, on in enumerate((
        row["shortage_risk"], row["overstock_risk"], row["order_now"] and has_order(row), not row["in_auto_purchase"],
    )) if on]


def _value(value):
    """[сырое значение для data-value, подпись на странице] или None, если данных нет."""
    return None if value is None else ["" if value is None else str(value), _shown(value)]


def _card_data(rows, texts):
    """Данные карточек для страницы: карточка собирается в браузере при открытии (так страница втрое легче).

    Здесь только публичные поля. Подписи и значения готовит Python, JS ничего не считает и не форматирует.
    """
    fields = {**ITEM_NUMBERS, **ITEM_DATES}
    tooltips, cards = [], {}
    for row in rows:
        note = None
        text = texts.items.get(row["sku_id"])
        if text is not None:
            tip = _note_tooltip(text, "item")
            if tip not in tooltips:
                tooltips.append(tip)
            note = [text.source == "ai", text.text, tooltips.index(tip)]
        cards[row["sku_id"]] = [
            category_label(row["category"]), _status_codes(row),
            [_value(row[key]) for key in CARD_FIELDS],
            [[month, *_value(qty)] for month, qty in row["monthly_forecast"]],
            note,
        ]
    return {
        "nodata": NO_DATA,
        "tags": STATUS_TAGS,
        "fields": [[key, fields[key][0], f"facts.json → items[].{key} · {fields[key][1]}"] for key in CARD_FIELDS],
        "monthly_source": MONTHLY_SOURCE,
        "tooltips": tooltips,
        "cards": cards,
    }


def _data_script(data):
    """JS-объявление с данными. «<» экранируется, чтобы текст не мог закрыть тег script."""
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return "\nconst CARD_DATA = " + raw.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029") + ";\n"


def _cards():
    return ('<dialog id="product" aria-labelledby="product-title"><form method="dialog" class="close">'
            '<button class="button secondary" autofocus>Закрыть</button></form><div id="product-body"></div></dialog>')


def _review_total(totals, key, suffix):
    return f'<span class="fold-count">{_total(totals, key)} {suffix}</span>'


def _reviews(facts, live_codes):
    totals = facts["totals"]
    lists = facts["lists"]

    def list_fact(value, list_name, sku, field, source):
        return _fact(value, f"lists.{list_name}.{sku}.{field}", f"facts.json → lists.{list_name}[].{field} · {source}")

    price = []
    for row in sorted(lists.get("price_review", ()), key=lambda row: (not row["ending"], row["sku_id"])):
        sku = code(row["sku_id"])
        price.append([
            _item_link(sku, sku in live_codes), category_label(category(row.get("category"))),
            *(list_fact(number(row.get(key)), "price_review", sku, key, ITEM_NUMBERS[key][1])
              for key in ("markup_pct", "stock", "stock_days")),
            '<span class="tag warning">Заканчивается</span>' if row.get("ending") is True else "—",
        ])

    # «Вывести» — все мёртвые товары (нет продаж 12 мес.); «Распродать остаток» — только те, у кого есть остаток.
    has_full_list = "discontinue" in lists
    list_key = "discontinue" if has_full_list else "sell_off"
    removed, sell_off = [], []
    for row in sorted(lists.get(list_key, ()), key=lambda row: (not bool(number(row.get("stock"))), row["sku_id"])):
        sku = code(row["sku_id"])
        stock = number(row.get("stock"))
        cells = [_item_link(sku, False), category_label(category(row.get("category"))),
                 list_fact(stock, list_key, sku, "stock", ITEM_NUMBERS["stock"][1])]
        if has_full_list:
            removed.append(cells)
        if stock:
            sell_off.append(cells)

    ask = []
    for row in lists.get("ask_supplier", ()):
        sku = code(row["sku_id"])
        ask.append([
            _item_link(sku, sku in live_codes),
            list_fact(iso_date(row.get("promised_date")), "ask_supplier", sku, "promised_date", "purchase_orders.csv → promised_date"),
            list_fact(number(row.get("days_overdue")), "ask_supplier", sku, "days_overdue", "дата плана минус purchase_orders.csv → promised_date"),
            list_fact(number(row.get("qty")), "ask_supplier", sku, "qty", "purchase_orders.csv → qty_pending"),
        ])

    discontinue_fold = ""
    if has_full_list:
        discontinue_fold = f"""<details class="panel fold">
      <summary>Вывести из ассортимента (нет продаж 12 мес.) {_review_total(totals, "dead_total", "поз.")}</summary>
      <p class="fold-note">Все товары без продаж за последние 12 месяцев. Остаток есть не у всех: он вынесен в список «Распродать остаток».</p>
      {_table(("Код товара", "Категория", "На складе, шт."), removed)}
    </details>"""

    return f"""
<section id="reviews" class="section" aria-labelledby="reviews-heading">
  <div class="section-heading"><div><p class="eyebrow">Ручная работа</p><h2 id="reviews-heading">Списки на пересмотр</h2>
  <p>Позиции и поставки, которые требуют отдельного решения.</p></div></div>
  <div class="folds">
    <details class="panel fold">
      <summary>Пересмотреть цену {_review_total(totals, "price_review_items", "поз.")}</summary>
      <p class="fold-note">Запас заканчивается у {_total(totals, "price_review_ending_items")} позиций.</p>
      {_table(("Код товара", "Категория", "Наценка, %", "На складе, шт.", "Запас, дни", "Статус"), price)}
    </details>
    {discontinue_fold}
    <details class="panel fold">
      <summary>Распродать остаток <span class="fold-count">{_total(totals, "sell_off_items")} поз., {_total(totals, "sell_off_qty")} шт.</span></summary>
      <p class="fold-note">На складе осталось {_total(totals, "sell_off_qty")} шт. на {_total(totals, "sell_off_items")} поз.: это мёртвые товары (нет продаж 12 мес.), которые ещё лежат на складе.</p>
      {_table(("Код товара", "Категория", "На складе, шт."), sell_off)}
    </details>
    <details id="supplier-review" class="panel fold">
      <summary>Уточнить у поставщика {_review_total(totals, "ask_supplier_items", "зак.")}</summary>
      <p class="fold-note">Из запаса в пути исключено {_total(totals, "ask_supplier_qty")} шт. из этих поставок.</p>
      {_table(("Код товара", "Обещанная дата", "Просрочка, дни", "Исключено, шт."), ask)}
    </details>
  </div>
</section>"""


def _csp_hash(content):
    return base64.b64encode(hashlib.sha256(content.encode()).digest()).decode()


def build_site(facts, texts, out_dir) -> Path:
    """Write index.html and return its path. Pair with build_xlsx(..., out_dir / 'order.xlsx')."""
    rows = sorted(item_rows(facts), key=purchase_order)
    totals = facts["totals"]
    date_fact = _fact(iso_date(facts.get("plan_date")), "plan_date", f"facts.json → plan_date · {DATE_SOURCE}")
    sku_count = _fact(number(facts.get("generated_from", {}).get("sku_rows")),
                      "generated_from.sku_rows", "facts.json → generated_from.sku_rows · sku_dim.csv → sku_id")
    weight = _total(totals, "weight_known_items")
    weight_detail = ""
    if number(totals.get("weight_known_items")) is not None:
        weight_detail = f" Вес заполнен у {weight} из {sku_count} позиций."
    stats = _stats(totals, (
        ("order_qty_total", "штук по всему плану", "primary"),
        ("order_now_items", "позиций на дату плана", ""),
        ("shortage_risk_items", "позиций", "danger"),
        ("live_total", "позиций в расчёте", ""),
    ))
    css = (ASSETS / "dashboard.css").read_text(encoding="utf-8")
    script = (ASSETS / "dashboard.js").read_text(encoding="utf-8") + _data_script(_card_data(rows, texts))
    policy = (f"default-src 'none'; script-src 'sha256-{_csp_hash(script)}'; "
              f"style-src 'sha256-{_csp_hash(css)}'; base-uri 'none'; form-action 'none'; img-src data:")
    markup = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{policy}">
<meta name="color-scheme" content="light dark">
<link rel="icon" href="data:,">
<title>План закупок</title>
<style>{css}</style>
</head>
<body>
<a class="skip" href="#main">Перейти к содержанию</a>
<header class="topbar">
  <div class="topbar-inner"><a href="#summary" class="brand"><span aria-hidden="true">▦</span> План закупок</a>
  <nav aria-label="Разделы дашборда">
    <a href="#summary">Сводка</a><a href="#recommendations">Заказы</a><a href="#calendar">Календарь</a>
    <a href="#risks">Риски</a><a href="#reviews">Пересмотр</a>
  </nav></div>
</header>
<main id="main" class="layout">
  <section id="summary" aria-labelledby="summary-heading" class="section hero">
    <p class="eyebrow">Рабочий стол закупщика</p>
    <h1 id="summary-heading">План закупок</h1>
    <p class="plan-date">Дата плана <strong>{date_fact}</strong></p>
    <div class="metrics">{stats}</div>
    {_note(texts.summary, "summary")}
    <p class="limit"><strong>Бюджет:</strong> цен в данных нет.
      <strong>Контейнеры:</strong> в данных нет полного веса для расчёта.{weight_detail}</p>
  </section>
  {_recommendations(rows)}
  {_calendar(rows)}
  {_risks(totals, texts.anomalies)}
  {_cards()}
  {_reviews(facts, {row["sku_id"] for row in rows})}
  <footer>Значения взяты из расчёта Python. Источник указан в подсказке у значения.</footer>
</main>
<script>{script}</script>
</body>
</html>
"""
    target = Path(out_dir) / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(markup, encoding="utf-8")
    return target
