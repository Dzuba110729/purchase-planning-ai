"""Проекция facts.json и допустимые утверждения для каждого паспорта."""

import re
from dataclasses import dataclass
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


MAX_PHRASES = 4


@dataclass(frozen=True)
class Phrase:
    text: str
    used_facts: tuple[str, ...]
    insufficient_data: bool = False


UNKNOWN = Phrase("В данных нет.", (), True)
SUMMARY_PHRASES = (
    ("order_now_items", "Позиций к заказу сейчас — {value}."),
    ("order_qty_total", "К заказу {value} шт."),
    ("shortage_risk_items", "Позиций с риском дефицита — {value}."),
    ("live_total", "Активных позиций — {value}."),
    ("plan_date", "Дата плана {value}."),
)
ANOMALY_PHRASES = (
    ("shortage_risk_items", "Позиций с риском дефицита — {value}."),
    ("overstock_risk_items", "Позиций с риском излишка — {value}."),
    ("ask_supplier_items", "Заказов для уточнения у поставщика — {value}."),
)
ITEM_PHRASES = (
    ("stock", "Остаток {value} шт."),
    ("in_transit_qty", "В пути {value} шт."),
    ("order_qty", "К заказу {value} шт."),
    ("next_order_date", "Дата заказа {value}."),
    ("runout_date", "По прогнозу запас закончится {value}."),
    ("daily_demand", "Прогноз {value} шт. в день."),
    ("demand_lead_cover", "Прогноз за плечо поставки и период покрытия — {value} шт."),
    ("sales_qty", "Продажи {value} шт."),
    ("overdue_excluded_qty", "Из расчёта исключены просроченные заказы на {value} шт."),
    ("sku_id", "Код товара {value}."),
)


def _quantity(value):
    return type(value) in (int, float) and value > 0


def _format(value):
    if isinstance(value, Decimal):
        return format(value, "f").replace(".", ",")
    if type(value) in (int, float):
        return format(Decimal(str(value)).normalize(), "f").replace(".", ",")
    return value


def _item_phrases(passport):
    quantity = passport["order_qty"]
    order_now = passport["order_now"]
    plan_date, next_date = passport["plan_date"], passport["next_order_date"]
    planned = _quantity(quantity) and next_date != NO_DATA
    if planned and plan_date != NO_DATA:
        planned = next_date >= plan_date and (
            order_now == NO_DATA or order_now == (next_date == plan_date)
        )
    for field, pattern in ITEM_PHRASES:
        value = passport[field]
        if value == NO_DATA:
            if field == "order_qty":
                yield Phrase("Количество к заказу — в данных нет.", (field,), True)
            continue
        if field == "next_order_date" and not planned:
            continue
        if field == "overdue_excluded_qty" and not _quantity(value):
            continue
        fields = ("order_qty", field) if field == "next_order_date" else (field,)
        yield Phrase(pattern.format(value=_format(value)), fields)
        if field == "daily_demand":
            demand = Decimal(str(value))
            rounded = demand.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if rounded and rounded != demand:
                yield Phrase(f"Прогноз около {_format(rounded)} шт. в день.", (field,))

    if planned and order_now is True and next_date == plan_date:
        yield Phrase(
            "Заказ рекомендован к размещению сейчас.", ("order_qty", "order_now", "plan_date", "next_order_date"),
        )
    for field, risk in (("shortage_risk", "дефицита"), ("overstock_risk", "излишка")):
        value = passport[field]
        if type(value) is bool:
            word = "отмечен" if value else "не отмечен"
            yield Phrase(f"Риск {risk} {word}.", (field,))


def allowed_phrases(kind, passport):
    if kind == "probe":
        return (UNKNOWN,)
    if kind == "item":
        return (*_item_phrases(passport), UNKNOWN)
    labels = SUMMARY_PHRASES if kind == "summary" else ANOMALY_PHRASES
    inconsistent = kind == "summary" and passport["order_qty_total"] == 0 and _quantity(passport["order_now_items"])
    return (
        *(Phrase(pattern.format(value=_format(passport[field])), (field,))
          for field, pattern in labels if passport[field] != NO_DATA
          and not (inconsistent and field in ("order_now_items", "order_qty_total"))),
        UNKNOWN,
    )


def combine_phrases(phrases):
    return Phrase(
        " ".join(phrase.text for phrase in phrases),
        tuple(dict.fromkeys(field for phrase in phrases for field in phrase.used_facts)),
        any(phrase.insufficient_data for phrase in phrases),
    )


def answer_error(answer, choices):
    if type(answer) is not dict or set(answer) != {"text", "used_facts", "insufficient_data"}:
        return "schema"
    text, used, missing = answer["text"], answer["used_facts"], answer["insufficient_data"]
    if (not isinstance(text, str) or not text.strip() or len(text) > 900 or
            type(used) is not list or any(type(field) is not str for field in used) or type(missing) is not bool):
        return "schema"

    remaining = text.strip()
    selected = []
    references = set()
    for _ in range(MAX_PHRASES):
        phrase = next(
            (choice for choice in choices if remaining == choice.text or remaining.startswith(choice.text + " ")),
            None,
        )
        if phrase is None or phrase.used_facts in references:
            return "text"
        references.add(phrase.used_facts)
        selected.append(phrase)
        remaining = remaining[len(phrase.text):].removeprefix(" ")
        if not remaining:
            break
    if remaining or (UNKNOWN in selected and len(selected) > 1):
        return "text"

    expected = combine_phrases(selected)
    if len(used) != len(set(used)) or set(used) != set(expected.used_facts) or missing != expected.insufficient_data:
        return "facts"
    return None
