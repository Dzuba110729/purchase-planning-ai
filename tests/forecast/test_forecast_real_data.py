"""Проверки этапа 3 на реальных данных из share/ (вне git). Без share/ — пропускаются."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from purchasing.cli import main
from purchasing.data import load_dataset
from purchasing.forecast import BACKTEST_DATE, backtest, build_forecast, fit

SHARE = Path(__file__).resolve().parents[2] / "share"
pytestmark = pytest.mark.skipif(not (SHARE / "sku_dim.csv").exists(), reason="нет share/ с данными")

CATEGORIES = [
    "concrete_mixers", "grain_mills", "sanitary_faucets",
    "space_heaters", "string_trimmers", "trimmer_blades_accessories",
]


@pytest.fixture(scope="module")
def ds():
    return load_dataset(SHARE)


@pytest.fixture(scope="module")
def fc(ds):
    return build_forecast(ds, ds.plan_date)


@pytest.fixture(scope="module")
def bt(ds):
    return backtest(ds)


def test_forecast_covers_exactly_live_skus(ds, fc):
    assert set(fc.monthly["sku_id"]) == set(ds.live_skus)
    assert len(fc.level) == 413
    assert (fc.monthly["qty"] >= 0).all()


def test_forecast_starts_at_plan_month_and_is_monthly(ds, fc):
    one = fc.for_sku(ds.live_skus[0])
    assert one.index[0] == ds.plan_month
    assert len(one) == 18


def test_profile_of_every_category_sums_to_100_percent(fc):
    assert sorted(fc.profile.index) == CATEGORIES
    assert fc.profile.sum(axis=1).tolist() == pytest.approx([1.0] * 6)
    assert (fc.profile >= 0).all().all()


def test_heaters_peak_in_october(fc):
    assert fc.profile.loc["space_heaters"].idxmax() == 10


def test_trimmers_peak_in_may(fc):
    assert fc.profile.loc["string_trimmers"].idxmax() == 5


def test_faucets_have_no_month_above_15_percent(fc):
    assert fc.profile.loc["sanitary_faucets"].max() < 0.15


def test_lost_demand_changes_only_zero_stock_months_and_only_up(ds):
    f = fit(ds, ds.plan_date)
    diff = (f["adjusted"] - f["demand"]).fillna(0)
    assert (diff >= -1e-9).all().all()
    changed = diff.abs() > 1e-9
    assert changed.any().any()  # на реальных данных упущенный спрос найден
    assert not (changed & ~f["zero_stock"]).any().any()


@pytest.mark.parametrize("category", CATEGORIES)
def test_model_beats_12_month_average(bt, category):
    assert bt.wape(category, "model") < bt.wape(category, "base")


def test_backtest_date_and_months(bt):
    assert bt.plan_date == BACKTEST_DATE
    assert str(bt.months[0]) == "2025-09" and str(bt.months[-1]) == "2026-07" and len(bt.months) == 11


def test_backtest_does_not_peek_at_the_future(ds):
    """Подменяем всё от августа 2025 и позже случайными числами: прогноз из августа 2025 не меняется."""
    rng = np.random.default_rng(0)
    cut = pd.Period("2025-08", "M")
    sales = ds.sales.copy()
    fut = sales["month"] >= cut
    sales.loc[fut, "qty_sold"] = rng.integers(0, 500, fut.sum())
    sales.loc[fut, "qty_returned"] = rng.integers(0, 5, fut.sum())
    stock = ds.stock.copy()
    sfut = stock["month"] >= cut
    stock.loc[sfut, "stock_qty"] = rng.integers(0, 5, sfut.sum())
    other = replace(ds, sales=sales, stock=stock)
    a = build_forecast(replace(ds, plan_date=BACKTEST_DATE), BACKTEST_DATE).monthly
    b = build_forecast(replace(other, plan_date=BACKTEST_DATE), BACKTEST_DATE).monthly
    pd.testing.assert_frame_equal(a, b)


def test_backtest_command_prints_table_for_six_categories(capsys):
    assert main(["backtest", "--data-dir", str(SHARE)]) == 0
    out = capsys.readouterr().out
    for c in CATEGORIES:
        assert c in out
    assert "WAPE" in out
