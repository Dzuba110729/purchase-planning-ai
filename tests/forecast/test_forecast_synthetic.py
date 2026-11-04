"""Тесты прогноза на придуманных таблицах с заранее известным ответом."""

from datetime import date

import pandas as pd
import pytest

from purchasing.forecast import Params, backtest, build_forecast, fit, wape

PLAIN = Params(smooth=0.0, cap_mult=None, recent_weight=0.0, correct_lost=False)


def span(start, end, f):
    """{"YYYY-MM": f(calendar_month)} на каждый месяц от start до end."""
    return {str(m): f(m.month) for m in pd.period_range(start, end, freq="M")}


def test_wape_known_value():
    # |10-5| + |10-15| = 10, сумма факта = 20 -> 50%
    assert wape([10, 10], [5, 15]) == pytest.approx(0.5)


def test_flat_sku_forecast_is_its_level(make_dataset):
    ds = make_dataset({("A", "c"): span("2024-08", "2025-07", lambda m: 10)})
    fc = build_forecast(ds, date(2025, 8, 13), PLAIN)
    assert fc.for_sku("A").iloc[:12].tolist() == pytest.approx([10.0] * 12)


def test_returns_are_subtracted(make_dataset):
    # продано 12, вернули 2 каждый месяц -> спрос 10
    ds = make_dataset({("A", "c"): span("2024-08", "2025-07", lambda m: (12, 2))})
    fc = build_forecast(ds, date(2025, 8, 13), PLAIN)
    assert fc.for_sku("A").iloc[0] == pytest.approx(10.0)


def test_returns_never_make_demand_negative(make_dataset):
    ds = make_dataset({("A", "c"): {**span("2024-08", "2025-06", lambda m: 10), "2025-07": (1, 5)}})
    f = fit(ds, date(2025, 8, 13), PLAIN)
    assert (f["demand"].fillna(0) >= 0).all().all()


def test_profile_sums_to_one_and_peak_is_found(make_dataset):
    # три года: в октябре 60, в остальные месяцы 5
    pat = lambda m: 60 if m == 10 else 5  # noqa: E731
    ds = make_dataset({("A", "c"): span("2022-08", "2025-07", pat)})
    prof = build_forecast(ds, date(2025, 8, 13), PLAIN).profile.loc["c"]
    assert prof.sum() == pytest.approx(1.0)
    assert prof.idxmax() == 10
    assert prof[10] == pytest.approx(60 / 115)


def test_seasonal_forecast_follows_profile_times_level(make_dataset):
    pat = lambda m: 60 if m == 10 else 5  # noqa: E731
    ds = make_dataset({("A", "c"): span("2023-08", "2025-07", pat)})
    s = build_forecast(ds, date(2025, 8, 13), PLAIN).for_sku("A")
    assert s[pd.Period("2025-10", "M")] == pytest.approx(60.0)
    assert s[pd.Period("2025-11", "M")] == pytest.approx(5.0)


def test_sku_without_recent_sales_is_not_forecast(make_dataset):
    ds = make_dataset({
        ("A", "c"): span("2024-08", "2025-07", lambda m: 10),
        ("OLD", "c"): span("2023-01", "2024-06", lambda m: 10),
    })
    fc = build_forecast(ds, date(2025, 8, 13), PLAIN)
    assert set(fc.monthly["sku_id"]) == {"A"}


def test_partial_month_is_not_used(make_dataset):
    # июль 2025 помечен неполным: его огромное значение не должно влиять
    series = {("A", "c"): {**span("2024-08", "2025-06", lambda m: 10), "2025-07": 9999}}
    ds = make_dataset(series, plan_date=date(2025, 7, 20), partial=("2025-07",))
    assert build_forecast(ds, date(2025, 7, 20), PLAIN).for_sku("A").iloc[0] == pytest.approx(10.0)


def test_lost_demand_raises_only_zero_stock_months(make_dataset):
    flat = lambda m: 10  # noqa: E731
    series = {(f"S{i}", "c"): span("2024-08", "2025-07", flat) for i in range(4)}
    # у S0 четыре месяца без остатка и без продаж
    out = ["2025-02", "2025-03", "2025-04", "2025-05"]
    series[("S0", "c")] = {m: (0 if m in out else 10) for m in series[("S0", "c")]}
    stock = {"S0": {m: (0 if m in out else 50) for m in series[("S0", "c")]}}
    for i in (1, 2, 3):
        stock[f"S{i}"] = {m: 50 for m in series[(f"S{i}", "c")]}
    ds = make_dataset(series, stock=stock)
    f = fit(ds, date(2025, 8, 13), Params(smooth=0.0, cap_mult=None, recent_weight=0.0, lost_share=1.0))
    diff = f["adjusted"] - f["demand"]
    assert (diff.fillna(0) >= -1e-9).all().all()  # только вверх
    changed = diff.abs() > 1e-9
    zero = f["zero_stock"]
    assert not (changed & ~zero).any().any()  # только месяцы с нулевым остатком
    assert changed.loc["S0", [pd.Period(m, "M") for m in out]].all()
    assert not changed.loc[["S1", "S2", "S3"]].any().any()


def test_lost_demand_value_is_expected_level(make_dataset):
    # плоская категория: ожидаемый спрос в «пустые» месяцы ~ обычные 10, а не 0
    series = {(f"S{i}", "c"): span("2024-08", "2025-07", lambda m: 10) for i in range(6)}
    out = ["2025-04", "2025-05"]
    series[("S0", "c")] = {m: (0 if m in out else 10) for m in series[("S0", "c")]}
    stock = {f"S{i}": {m: 50 for m in series[(f"S{i}", "c")]} for i in range(6)}
    stock["S0"] = {m: (0 if m in out else 50) for m in series[("S0", "c")]}
    ds = make_dataset(series, stock=stock)
    f = fit(ds, date(2025, 8, 13), Params(smooth=0.0, cap_mult=None, recent_weight=0.0, lost_share=1.0))
    got = f["adjusted"].loc["S0", pd.Period("2025-04", "M")]
    assert got == pytest.approx(10.0, abs=0.7)


def test_no_stock_snapshots_means_no_lost_demand_correction(make_dataset):
    ds = make_dataset({("A", "c"): {**span("2024-08", "2025-05", lambda m: 10), "2025-06": 0, "2025-07": 0}})
    f = fit(ds, date(2025, 8, 13), PLAIN)
    assert (f["adjusted"].fillna(0) == f["demand"].fillna(0)).all().all()


def test_no_peeking_into_the_future(make_dataset):
    base = {("A", "c"): span("2023-08", "2026-07", lambda m: 60 if m == 10 else 5),
            ("B", "c"): span("2024-02", "2026-07", lambda m: 7)}
    stock = {"A": span("2024-01", "2026-07", lambda m: 0 if m == 3 else 40)}
    changed = {
        ("A", "c"): {m: (v * 37 + 11 if m >= "2025-08" else v) for m, v in base[("A", "c")].items()},
        ("B", "c"): {m: (v * 3 + 1 if m >= "2025-08" else v) for m, v in base[("B", "c")].items()},
    }
    stock2 = {"A": {m: (7 if m >= "2025-08" else v) for m, v in stock["A"].items()}}
    d = date(2025, 8, 13)
    f1 = build_forecast(make_dataset(base, stock=stock), d).monthly
    f2 = build_forecast(make_dataset(changed, stock=stock2), d).monthly
    pd.testing.assert_frame_equal(f1, f2)


def test_backtest_synthetic_known_answer(make_dataset):
    # сезон одинаков четыре года: октябрь 60, остальные 5
    pat = lambda m: 60 if m == 10 else 5  # noqa: E731
    ds = make_dataset({("A", "c"): span("2022-08", "2026-07", pat)})
    res = backtest(ds, params=PLAIN)
    assert res.wape("c", "model") == pytest.approx(0.0, abs=1e-9)
    # база = 115/12; ошибка за 11 месяцев: |115/12-60| + 10·|115/12-5| = 96.25 при факте 110
    assert res.wape("c", "base") == pytest.approx(96.25 / 110)
    assert res.better()["c"] is True
