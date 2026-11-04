"""Проверки этапа 2 на реальных данных из share/ (вне git)."""

from datetime import date
from pathlib import Path

import pytest

from purchasing.data import load_dataset, quality_report

SHARE = Path(__file__).resolve().parents[2] / "share"
pytestmark = pytest.mark.skipif(not (SHARE / "sku_dim.csv").exists(), reason="нет share/ с данными")


@pytest.fixture(scope="module")
def ds():
    return load_dataset(SHARE)


def test_plan_date_default(ds):
    assert ds.plan_date == date(2026, 8, 13)


def test_row_counts(ds):
    rep = quality_report(ds)["rows"]
    assert rep == {
        "sku_dim.csv": 791, "sales_monthly.csv": 37414, "stock_monthly.csv": 9627,
        "purchase_orders.csv": 660, "receipts.csv": 2394, "supplier_dim.csv": 39,
    }


def test_marking_counts(ds):
    assert len(ds.live_skus) == 413
    assert len(ds.dead_skus) == 378
    assert len(ds.low_markup_skus) == 68
    assert set(ds.low_markup_skus) <= set(ds.live_skus)


def test_open_orders(ds):
    assert len(ds.open_orders) == 57
    assert len(ds.in_transit_orders) == 22
    assert ds.in_transit_qty == 17127
    assert len(ds.overdue_orders) == 35


def test_zero_stock_live(ds):
    assert len(ds.zero_stock_live_skus()) == 139
    assert str(ds.plan_month) == "2026-08"


def test_august_not_in_means(ds):
    months = set(map(str, ds.full_months_sales()["month"]))
    assert "2026-08" not in months and "2026-07" in months
