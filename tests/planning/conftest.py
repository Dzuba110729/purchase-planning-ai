"""Фикстуры для тестов расчёта закупки (хелперы — в planning_helpers.py)."""

import pandas as pd
import pytest

from planning_helpers import ORDER_COLS, PLAN, history_orders, make_forecast
from purchasing.data import Dataset


@pytest.fixture
def build():
    """build(skus, stock, orders, receipts, monthly_sales, plan_date) -> Dataset.

    skus: список словарей {sku_id, category, supplier_id, supply_type, markup, decile, first_sale_month}.
    monthly_sales: {sku: [qty за каждый полный месяц от first_sale_month до месяца перед планом]}.
    """

    def _build(skus, stock=None, orders=(), receipts=(), monthly_sales=None, plan_date=PLAN, monthly_returns=None):
        pm = pd.Period(plan_date, freq="M")
        sku_rows, sales_rows = [], []
        for s in skus:
            fm = pd.Period(s.get("first_sale_month", "2025-01"), freq="M")
            sku_rows.append({
                "sku_id": s["sku_id"], "category": s.get("category", "c"),
                "supplier_id": s.get("supplier_id"), "supply_type": s.get("supply_type", "import"),
                "price_decile": s.get("decile", 5.0),
                "first_sale_month": fm, "last_sale_month": pm - 1,
                "avg_markup_pct_12m": s.get("markup", 40.0),
            })
            vals = (monthly_sales or {}).get(s["sku_id"])
            months = pd.period_range(fm, pm - 1, freq="M")
            if vals is None:
                vals = [30] * len(months)
            assert len(vals) == len(months), (s["sku_id"], len(vals), len(months))
            base = len(sales_rows)
            for m, q in zip(months, vals):
                sales_rows.append({"sku_id": s["sku_id"], "category": s.get("category", "c"), "month": m,
                                   "qty_sold": q, "qty_returned": ((monthly_returns or {}).get(s["sku_id"]) or [0] * len(months))[len(sales_rows) - base], "orders_cnt": 1, "markup_pct": 40.0,
                                   "partial_month": 0})
        stock_rows = [{"sku_id": k, "month": pm, "stock_qty": v, "restored": False} for k, v in (stock or {}).items()]
        for s in skus:  # нет строки = остаток 0 (как после восстановления в модуле данных)
            if s["sku_id"] not in (stock or {}):
                stock_rows.append({"sku_id": s["sku_id"], "month": pm, "stock_qty": 0, "restored": True})
        return Dataset(
            sku=pd.DataFrame(sku_rows), sales=pd.DataFrame(sales_rows),
            stock=pd.DataFrame(stock_rows),
            orders=pd.DataFrame(list(orders), columns=ORDER_COLS),
            receipts=pd.DataFrame(list(receipts), columns=["sku_id", "receipt_date", "qty", "supplier_id"]),
            suppliers=pd.DataFrame({"supplier_id": []}), plan_date=plan_date,
        )

    return _build


@pytest.fixture
def world(build):
    """Базовый мир: S1 у поставщика SUP1 (3 заказа, приход через 60 дней, интервал приходов 90 дней).

    Плечо SUP1 = 60, покрытие SUP1 = 90, шаг партии S1 = НОД(50,100,150) = 50.
    """
    orders, receipts = history_orders("SUP1", "S1", lead=60, promised_lead=60)

    def _w(stock, rates, extra_orders=(), extra_skus=(), extra_stock=None, monthly_sales=None, history=None, monthly_returns=None):
        o, r = (orders, receipts) if history is None else history
        ds = build(
            [{"sku_id": "S1", "supplier_id": "SUP1"}, *extra_skus],
            stock={"S1": stock, **(extra_stock or {})}, orders=[*o, *extra_orders], receipts=r,
            monthly_sales=monthly_sales, monthly_returns=monthly_returns,
        )
        return ds, make_forecast(rates)

    return _w
