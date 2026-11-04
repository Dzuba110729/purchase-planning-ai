"""Сборка крошечных придуманных наборов данных для тестов прогноза (без CSV и реальных строк)."""

from datetime import date

import pandas as pd
import pytest

from purchasing.data import Dataset


def _empty(cols):
    return pd.DataFrame({c: [] for c in cols})


@pytest.fixture
def make_dataset():
    """make_dataset(series, plan_date, stock=None): series = {(sku, category): {"YYYY-MM": qty or (qty, returned)}}.

    stock = {sku: {"YYYY-MM": stock_qty}} — остаток на 1-е число; нет строки = снимка нет.
    """

    def build(series, plan_date=date(2025, 8, 13), stock=None, partial=()):
        rows = []
        for (sku, cat), by_month in series.items():
            for month, v in by_month.items():
                qty, ret = v if isinstance(v, tuple) else (v, 0)
                rows.append({
                    "sku_id": sku, "category": cat, "month": pd.Period(month, freq="M"),
                    "qty_sold": qty, "qty_returned": ret, "orders_cnt": 1, "markup_pct": 30.0,
                    "partial_month": int(month in partial),
                })
        sales = pd.DataFrame(rows)
        srows = [
            {"sku_id": sku, "month": pd.Period(m, freq="M"), "stock_qty": q, "restored": False}
            for sku, by_month in (stock or {}).items() for m, q in by_month.items()
        ]
        st = pd.DataFrame(srows) if srows else _empty(["sku_id", "month", "stock_qty", "restored"])
        sku = pd.DataFrame({"sku_id": [k[0] for k in series], "category": [k[1] for k in series]})
        return Dataset(
            sku=sku, sales=sales, stock=st, orders=_empty(["status"]), receipts=_empty(["sku_id"]),
            suppliers=_empty(["supplier_id"]), plan_date=plan_date,
        )

    return build


def months(start: str, end: str):
    return [str(m) for m in pd.period_range(start, end, freq="M")]
