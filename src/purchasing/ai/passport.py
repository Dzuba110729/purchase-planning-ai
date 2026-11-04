"""Проекция facts.json и проверка текста по отправленному паспорту."""

import re
import unicodedata
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from math import isfinite

NO_DATA = "в данных нет"
SKU = re.compile(r"[A-Z0-9]+(?:[-_][A-Z0-9]+)*", re.ASCII)
CATEGORY = re.compile(r"[a-z]+(?:_[a-z]+)*", re.ASCII)
ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
ITEM_NUMBERS = (
    "stock", "in_transit_qty", "sales_qty", "daily_demand", "demand_lead_cover",
    "order_qty", "overdue_excluded_qty",
)
ITEM_FLAGS = ("order_now", "shortage_risk", "overstock_risk")
SUMMARY_NUMBERS = ("live_total", "order_now_items", "order_qty_total", "shortage_risk_items")
ANOMALY_NUMBERS = ("shortage_risk_items", "overstock_risk_items", "ask_supplier_items")

FORBIDDEN = re.compile(
    r"[%₽$€£¥¢₴₸₿¤×÷=]|(?<=\d)\s+(?:р|г|т)\b|\b(?:руб\w*|доллар\w*|бакс\w*|евро\w*|копе\w*|валют\w*|бюджет\w*|"
    r"денег|деньг\w*|денеж\w*|цен[аыеуою]|стоимост\w*|сумм[ауые] оплаты|оплат\w*|финанс\w*|"
    r"вес\w*|весит|масса|массой|кг|грамм\w*|кило|килограмм\w*|тонн\w*|"
    r"контейнер\w*|конт|бренд\w*|производител\w*|назван\w*|наименован\w*|"
    r"фирм\w*|компани\w*|артикул\w*|тыс\w*|мл[нрд]\w*|миллион\w*|миллиард\w*)\b"
)
DATE_TOKEN = re.compile(r"\b(?:\d{1,4}[-./]\d{1,2}[-./]\d{1,4}|\d{1,2}\s*/\s*\d{1,2})\b")
DATE_ROLE = re.compile(
    r"\b(?:(?P<next_order_date>дат\w* (?:следующ\w* )?(?:заказ\w*|закуп\w*)|"
    r"следующ\w* заказ\w*|заказат\w*)|"
    r"(?P<runout_date>(?:исчерпани|окончани)\w* (?:запас|остат)\w*|"
    r"(?:запас|остат)\w* (?:законч|иссяк|исчерпа)\w*)|"
    r"(?P<plan_date>дат\w* план\w*|по состоянию на))\b"
)
MONTH = re.compile(
    r"\b(?:январ|феврал|март|апрел|ма[йяе]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*\b"
)
NUMBER = re.compile(r"[+-]?(?:(?:\d{1,3}(?: \d{3})+(?!\d)|\d+)(?:[.,]\d+)*|[.,]\d+)")
NUMBER_WORD = re.compile(
    r"\b(?:нул[ьяюеём]|ноль|од(?:ин|н[аоуйие]|ного|ному|ним|них)|два|две|двух|двумя?|"
    r"три|тр[её]х|четыр[её]\w*|пят[ьи]|шест[ьи]|сем[ьи]|восем\w*|девят[ьи]|"
    r"десят[ьи]|\w+надцат\w*|\w+дцат\w*|двести|триста|четыреста|сорок\w*|сто|"
    r"(?:двух|тр[её]х|четыр[её]х|пят[ьи]|шест[ьи]|сем[ьи]|восьми|девят[ьи])сот|"
    r"(?:двум|тр[её]м|четыр[её]м|пяти|шести|семи|восьми|девяти)стам\w*|"
    r"(?:двух|тр[её]х|четыр[её]х|пяти|шести|семи|восьми|девяти)стах|"
    r"перв\w*|втор\w*|трет\w*|четв[её]рт\w*|пят(?:ый|ая|ое|ые)|"
    r"вдвое|втрое|дважды|трижды|половин\w*|полтора|полторы|сотн\w*|десятк\w*)\b"
)
TOKEN = re.compile(r"\w+(?:[-_]\w+)*")
CAPITAL = re.compile(r"[А-ЯЁ][а-яё]+")
SENTENCE_START = re.compile(r"(?:^|[.!?]\s+|\n)[«\"( ]*\Z")
RISK_TERMS = {
    "shortage_risk": re.compile(r"\b(?:дефицит|нехватк)\w*\b"),
    "overstock_risk": re.compile(r"\b(?:излиш|затовар)\w*\b"),
}
RISK_NEGATION = re.compile(
    r"\b(?:нет|отсутств\w*|не\s+(?:ожида\w*|выяв\w*|зафикс\w*|прогноз\w*|"
    r"возник\w*|гроз\w*|предвид\w*|наблюда\w*|обнаруж\w*|будет))\b"
)
ORDER_NEGATION = re.compile(
    r"\b(?:не\s+(?:(?:нужно|надо|стоит|требуется|рекомендуется)\s+)?(?:заказ[аы]\w*|закуп[аи]\w*)|"
    r"(?:заказ\w*|закуп\w*)\s+не\s+(?:нуж\w*|надо|стоит|требу\w*|рекоменду\w*))\b"
)
IMMEDIATE = re.compile(r"\b(?:сейчас|сегодня|пока)\b")
METRICS = {
    "stock": r"остат(?:ок|ка|ки|ков|ке)|в наличии|на складе",
    "in_transit_qty": r"в пути",
    "sales_qty": r"продаж\w*|продано",
    "daily_demand": r"дневн\w* спрос|ежедневн\w* спрос",
    "order": r"к заказу|для заказа|заказат\w*|рекомендовано|рекомендуется",
    "ask_supplier_items": r"(?:уточнен\w*|уточнит\w*|уточнен[оы]) у поставщика",
}
METRIC_LABEL = re.compile(
    r"\b(?:" + "|".join(f"(?P<{field}>{pattern})" for field, pattern in METRICS.items()) + r")\b"
)
CLAUSE_BREAK = re.compile(r"[,!?;\n]|\.(?!\d)")
NAME_CONTEXT = re.compile(
    r"\b(?:поставщик(?:[ауеои]|ом)?\s+[а-яё-]+|"
    r"у\s+(?!(?:поставщика|товара|товаров|позиции|позиций)\b)[а-яё-]+)\b"
)
STOCK_TERMS = {
    "В", "По", "На", "Из", "Для", "До", "С", "При", "К", "А", "Если", "Известно", "Сейчас",
    "Остаток", "Запас", "Запаса", "Остатков", "Осталось", "Текущий", "Текущего", "Склад",
    "Товар", "Товара", "Товары", "Позиция", "Позиции", "Всего", "Итого", "Нужно", "Можно",
    "Рекомендация", "Рекомендовано", "Рекомендуется", "Рекомендуемый", "Следующий",
    "Заказ", "Заказать", "Заказывать", "Закупка", "Закупок", "Закупку", "Дата", "Поставка", "Поступление",
    "Ожидается", "Плановая", "Плановый", "Планируется", "Данные", "Нет", "Не", "Риск",
    "Риски", "Есть", "Высокий", "Низкий", "Обнаружен", "Обнаружены", "Отмечен", "У",
    "Дефицит", "Излишек", "Излишки", "Аномалии", "Аномалий", "Особых", "Просрочено",
    "Просроченные", "Проверьте", "Уточните", "Требуется", "Уточнение", "Прогноз",
    "Прогнозируемый", "Продажи", "Спрос", "Средний", "Дневной", "Ежедневный", "Наличие",
    "Количество", "Объём", "Срок", "Категория", "Код", "Недостаточно",
}


def _number(value):
    if (type(value) is int or (type(value) is float and isfinite(value))) and abs(value) <= 10 ** 15:
        return value
    return NO_DATA


def _date(value):
    if isinstance(value, str) and ISO_DATE.fullmatch(value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
    return NO_DATA


def totals_passport(facts, fields):
    totals = facts.get("totals", {})
    return {
        "plan_date": _date(facts.get("plan_date")),
        **{field: _number(totals.get(field)) for field in fields},
    }


def item_passport(item, plan_date):
    code = item.get("sku_id")
    category = item.get("category")
    return {
        "sku_id": code if isinstance(code, str) and len(code) <= 40 and SKU.fullmatch(code) else NO_DATA,
        "category": category if isinstance(category, str) and len(category) <= 64
        and CATEGORY.fullmatch(category) else NO_DATA,
        "plan_date": _date(plan_date),
        **{field: _number(item.get(field)) for field in ITEM_NUMBERS},
        "runout_date": _date(item.get("runout_date")),
        "next_order_date": _date(item.get("next_order_date")),
        **{field: item[field] if type(item.get(field)) is bool else NO_DATA for field in ITEM_FLAGS},
    }


def _supported_number(token, numbers):
    try:
        shown = Decimal(token.replace(" ", "").replace(",", "."))
    except ArithmeticError:
        return False
    return any(
        number == shown or (
            shown != 0 and shown.as_tuple().exponent > number.as_tuple().exponent
            and shown == number.quantize(shown, rounding=ROUND_HALF_UP)
        )
        for number in numbers
    )


def _risk_error(text, referenced):
    for clause in re.split(r"[!?;\n]|\.(?!\d)|(?<!\d),|,(?!\d)", text.casefold()):
        for field, term in RISK_TERMS.items():
            if not term.search(clause):
                continue
            flag = referenced.get(field, referenced.get(f"{field}_items"))
            if flag == NO_DATA and (NO_DATA in clause or "нет данных" in clause):
                continue
            if type(flag) not in (bool, int, float):
                return True
            zero_count = type(flag) is int and bool(re.search(r"(?<![\d.,])0(?![\d.,])", clause))
            negative = bool(RISK_NEGATION.search(clause)) or zero_count
            if bool(flag) == negative:
                return True
    return False


def _order_error(text, referenced, passport):
    qty_field = "order_qty" if "order_qty" in passport else "order_qty_total"
    for clause in CLAUSE_BREAK.split(text.casefold()):
        if not ORDER_NEGATION.search(clause):
            continue
        qty = referenced.get(qty_field)
        if type(qty) in (int, float) and qty == 0:
            continue
        if referenced.get("order_now") is False and IMMEDIATE.search(clause):
            continue
        return True
    return False


def _dates_error(text, referenced):
    previous_end = 0
    for match in DATE_TOKEN.finditer(text):
        token = match[0]
        if _date(token) == NO_DATA or token not in referenced.values():
            return True
        preceding = CLAUSE_BREAK.split(text[previous_end:match.start()].casefold())[-1]
        roles = list(DATE_ROLE.finditer(preceding))
        if roles and token != referenced.get(roles[-1].lastgroup):
            return True
        previous_end = match.end()
    return False


def _numbers_error(text, referenced, passport):
    numbers = {field: Decimal(str(value)) for field, value in referenced.items() if type(value) in (int, float)}
    previous_end = 0
    for match in NUMBER.finditer(text):
        preceding = CLAUSE_BREAK.split(text[previous_end:match.start()].casefold())[-1]
        labels = list(METRIC_LABEL.finditer(preceding))
        allowed = numbers.values()
        if labels:
            metric = labels[-1].lastgroup
            if metric == "order":
                metric = "order_qty" if "order_qty" in passport else "order_qty_total"
            # Следом за «к заказу» могут стоять количество позиций или штуки.
            units = re.match(r"\s+(?:позиц|товар)\w*\b", text[match.end():].casefold())
            if metric == "order_qty_total" and units:
                metric = "order_now_items"
            allowed = (numbers[metric],) if metric in numbers else ()
        if not _supported_number(match[0], allowed):
            return True
        previous_end = match.end()
    return any(c.isnumeric() for c in NUMBER.sub(" ", text))


def _text_error(text, referenced, passport):
    normal = unicodedata.normalize("NFKC", text)
    normal = re.sub(r"[̀́]", "", normal)
    normal = re.sub(r"[−‐‑‒–—]", "-", normal)
    if (not re.search(r"[а-яё]", normal.casefold()) or
            any(unicodedata.category(c).startswith("C") and c != "\n" for c in normal)):
        return "text"
    if FORBIDDEN.search(normal.casefold()):
        return "forbidden"
    if MONTH.search(normal.casefold()) or re.search(r"\d:\d", normal):
        return "date"

    identifiers = {referenced.get("sku_id"), referenced.get("category")} - {NO_DATA, None}
    for value in sorted(identifiers, key=len, reverse=True):
        normal = re.sub(rf"(?<![\w-]){re.escape(value)}(?![\w-])", " ", normal)
    if _dates_error(normal, referenced):
        return "date"
    normal = DATE_TOKEN.sub(" ", normal)

    for match in TOKEN.finditer(normal):
        token = match.group()
        if token == "SKU":
            continue
        if re.search(r"[a-zA-Z]", token) or (any(c.isalpha() for c in token) and
                (any(c.isnumeric() for c in token) or (len(token) > 1 and token.isupper()))):
            return "identifier"
    for match in CAPITAL.finditer(normal):
        if match[0] not in STOCK_TERMS or not SENTENCE_START.search(normal[:match.start()]):
            return "identifier"
    if NAME_CONTEXT.search(normal.casefold()):
        return "forbidden"
    if _risk_error(normal, referenced):
        return "risk"
    if _order_error(normal, referenced, passport):
        return "order"
    if NUMBER_WORD.search(normal.casefold()) or _numbers_error(normal, referenced, passport):
        return "number"
    return None


def answer_error(answer, passport):
    if type(answer) is not dict or set(answer) != {"text", "used_facts", "insufficient_data"}:
        return "schema"
    text, used, missing = answer["text"], answer["used_facts"], answer["insufficient_data"]
    if (not isinstance(text, str) or not text.strip() or len(text) > 900 or
            type(used) is not list or any(type(field) is not str for field in used) or type(missing) is not bool):
        return "schema"
    if len(used) != len(set(used)) or not set(used) <= passport.keys():
        return "facts"
    referenced = {field: passport[field] for field in used}
    admits_missing = bool(re.search(r"\b(?:в данных нет|нет данных|не знаю)\b", " ".join(text.casefold().split())))
    if missing != admits_missing or (not missing and (not used or NO_DATA in referenced.values())):
        return "facts"
    return _text_error(text.strip(), referenced, passport)
