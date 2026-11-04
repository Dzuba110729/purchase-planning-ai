"""The public projection of facts.json. Free-form notes and supplier/order IDs stay private."""

import re
from datetime import date
from math import isfinite


CATEGORIES = {
    "grain_mills": "Зернодробилки",
    "concrete_mixers": "Бетономешалки",
    "space_heaters": "Обогреватели",
    "string_trimmers": "Триммеры",
    "trimmer_blades_accessories": "Ножи и оснастка для триммеров",
    "sanitary_faucets": "Смесители",
}
SKU = re.compile(r"[A-Z0-9]+(?:[-_][A-Z0-9]+)*", re.ASCII)
ITEM_NUMBERS = {
    "order_qty": ("К заказу, шт.", "остаток, прогноз и заказы · stock_monthly.csv → stock_qty · sales_monthly.csv → qty_sold, qty_returned · purchase_orders.csv → qty_pending, qty_ordered"),
    "stock": ("На складе, шт.", "stock_monthly.csv → stock_qty (пропуск строки = нулевой остаток)"),
    "in_transit_qty": ("В пути, шт.", "purchase_orders.csv → qty_pending, status, promised_date"),
    "stock_days": ("Запас, дни", "stock_monthly.csv → stock_qty · purchase_orders.csv → qty_pending · sales_monthly.csv → qty_sold, qty_returned"),
    "daily_demand": ("Спрос, шт./день", "прогноз по sales_monthly.csv → qty_sold, qty_returned, month"),
    "demand_lead_cover": ("Спрос за плечо и покрытие, шт.", "прогноз по sales_monthly.csv → qty_sold, qty_returned, month"),
    "lead_time_days": ("Плечо, дни", "purchase_orders.csv → planned_lead_days, supplier_id, sku_id"),
    "cover_days": ("Покрытие, дни", "receipts.csv → receipt_date, supplier_id, sku_id"),
    "safety_stock": ("Страховой запас, шт.", "разброс спроса из sales_monthly.csv → qty_sold, qty_returned, month"),
    "target_stock": ("Целевой запас, шт.", "спрос за плечо и покрытие плюс страховой запас · sales_monthly.csv → qty_sold, qty_returned"),
    "batch_step": ("Шаг партии, шт.", "purchase_orders.csv → qty_ordered либо шаг по умолчанию"),
    "markup_pct": ("Наценка, %", "sku_dim.csv → avg_markup_pct_12m"),
    "overdue_excluded_qty": ("Исключено просроченных, шт.", "purchase_orders.csv → qty_pending, status, promised_date"),
}
ITEM_DATES = {
    "next_order_date": ("Дата заказа", "запас, прогноз и плечо · stock_monthly.csv → stock_qty · sales_monthly.csv → qty_sold, qty_returned · purchase_orders.csv → planned_lead_days"),
    "runout_date": ("Запас закончится", "stock_monthly.csv → stock_qty · purchase_orders.csv → qty_pending, promised_date · sales_monthly.csv → qty_sold, qty_returned"),
}
TOTALS = {
    "order_qty_total": ("Рекомендовано к заказу", "сумма items[].order_qty"),
    "order_now_items": ("Разместить сейчас", "items[].order_now и items[].order_qty"),
    "shortage_risk_items": ("Риск дефицита", "items[].shortage_risk"),
    "overstock_risk_items": ("Риск излишка", "items[].overstock_risk"),
    "live_total": ("Активные позиции", "sku_dim.csv → avg_markup_pct_12m"),
    "dead_total": ("Вывести из ассортимента", "sku_dim.csv → avg_markup_pct_12m"),
    "weight_known_items": ("Вес указан", "sku_dim.csv → weight_kg"),
    "open_order_items": ("Открытые заказы", "purchase_orders.csv → status"),
    "overdue_order_items": ("Просроченные", "purchase_orders.csv → status, promised_date"),
    "overdue_order_qty": ("Просрочено, шт.", "purchase_orders.csv → status, promised_date, qty_pending"),
    "price_review_items": ("Пересмотр цены", "lists.price_review"),
    "price_review_ending_items": ("Запас заканчивается", "lists.price_review[].ending"),
    "sell_off_items": ("Есть остаток к распродаже", "lists.sell_off"),
    "sell_off_qty": ("К распродаже, шт.", "lists.sell_off[].stock"),
    "ask_supplier_items": ("Уточнить у поставщика", "lists.ask_supplier"),
    "ask_supplier_qty": ("Исключено, шт.", "lists.ask_supplier[].qty"),
}
MONTHLY_SOURCE = "прогноз по sales_monthly.csv → qty_sold, qty_returned, month"
DATE_SOURCE = "дата среза из параметра расчёта или описания выгрузки"


def number(value):
    if type(value) is int or (type(value) is float and isfinite(value)):
        return value
    return None


def iso_date(value):
    try:
        return date.fromisoformat(value).isoformat()
    except (ValueError, TypeError):
        return None


def code(value):
    if isinstance(value, str) and len(value) <= 40 and SKU.fullmatch(value):
        return value
    raise ValueError("Ожидался обезличенный код товара.")


def category(value):
    return value if isinstance(value, str) and value in CATEGORIES else "other"


def category_label(value):
    return CATEGORIES.get(value, "Другая категория")


def public_item(row):
    forecast = []
    for point in row.get("monthly_forecast", ()):
        month = point.get("month")
        qty = number(point.get("qty"))
        if isinstance(month, str) and re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", month) and qty is not None:
            forecast.append((month, qty))

    return {
        "sku_id": code(row.get("sku_id")),
        "category": category(row.get("category")),
        **{field: number(row.get(field)) for field in ITEM_NUMBERS},
        **{field: iso_date(row.get(field)) for field in ITEM_DATES},
        **{field: row.get(field) is True for field in (
            "in_auto_purchase", "order_now", "shortage_risk", "overstock_risk",
        )},
        "monthly_forecast": forecast,
    }


def item_rows(facts):
    if facts.get("schema_version") != 1:
        raise ValueError("Ожидался facts.json версии 1.")
    rows = [public_item(row) for row in facts["items"]]
    if len({row["sku_id"] for row in rows}) != len(rows):
        raise ValueError("В расчёте повторяются коды товаров.")
    return rows


def has_order(row):
    return row["order_qty"] is not None and row["order_qty"] > 0


def purchase_order(row):
    return (not row["order_now"], row["next_order_date"] or "9999-12-31", row["sku_id"])
