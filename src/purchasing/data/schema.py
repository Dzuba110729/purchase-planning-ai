"""Описание шести файлов: обязательные колонки и их типы."""

from dataclasses import dataclass, field

# Типы колонок: str — текст; int — целое; float — число; month — YYYY-MM; date — YYYY-MM-DD
STR, INT, FLOAT, MONTH, DATE = "str", "int", "float", "month", "date"


@dataclass(frozen=True)
class FileSpec:
    name: str  # имя файла
    columns: dict[str, str]  # обязательные колонки -> тип
    nullable: frozenset[str] = frozenset()  # колонки, где пусто — допустимо
    optional: dict[str, str] = field(default_factory=dict)  # проверяются, если колонка есть


FILES: dict[str, FileSpec] = {
    "sku_dim": FileSpec(
        "sku_dim.csv",
        {
            "sku_id": STR, "category": STR, "supply_type": STR,
            "first_sale_month": MONTH, "last_sale_month": MONTH,
            "months_with_sales": INT, "qty_total": INT,
            "avg_markup_pct_12m": FLOAT, "on_order_qty": INT,
        },
        frozenset({"avg_markup_pct_12m"}),
        {"weight_kg": FLOAT, "price_decile": FLOAT, "warranty_months": INT, "target_cover_days": INT},
    ),
    "sales_monthly": FileSpec(
        "sales_monthly.csv",
        {
            "sku_id": STR, "category": STR, "month": MONTH, "qty_sold": INT,
            "qty_returned": INT, "orders_cnt": INT, "markup_pct": FLOAT,
            "partial_month": INT,
        },
        frozenset({"markup_pct"}),
    ),
    "stock_monthly": FileSpec(
        "stock_monthly.csv", {"sku_id": STR, "month": MONTH, "stock_qty": INT}
    ),
    "purchase_orders": FileSpec(
        "purchase_orders.csv",
        {
            "po_id": STR, "sku_id": STR, "supplier_id": STR, "order_date": DATE,
            "promised_date": DATE, "planned_lead_days": INT, "status": STR,
            "qty_ordered": INT, "qty_confirmed": INT, "qty_received": INT,
            "qty_pending": INT,
        },
        frozenset({"po_id"}),
    ),
    "receipts": FileSpec(
        "receipts.csv",
        {"sku_id": STR, "receipt_date": DATE, "qty": INT, "supplier_id": STR},
    ),
    "supplier_dim": FileSpec(
        "supplier_dim.csv",
        {
            "supplier_id": STR, "supply_type": STR, "receipts_cnt": INT,
            "skus": INT, "median_days_between_receipts": FLOAT,
            "median_planned_lead_days": FLOAT,
        },
        frozenset({"median_days_between_receipts", "median_planned_lead_days"}),
    ),
}
