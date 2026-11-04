"""Проверки этапа 4 на реальных данных из share/ (вне git). Без share/ — пропускаются."""

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from purchasing.cli import main
from purchasing.data import load_dataset
from purchasing.forecast import build_forecast
from purchasing.planning import build_plan

SHARE = Path(__file__).resolve().parents[2] / "share"
pytestmark = pytest.mark.skipif(not (SHARE / "sku_dim.csv").exists(), reason="нет share/ с данными")


@pytest.fixture(scope="module")
def ds():
    return load_dataset(SHARE)


@pytest.fixture(scope="module")
def plan(ds):
    return build_plan(ds, build_forecast(ds, ds.plan_date))


def test_counts_match_dataset(ds, plan):
    t = plan.to_facts()["totals"]
    assert t["live_total"] == len(plan.items) == 413
    assert t["dead_total"] == len(ds.dead_skus)
    assert t["auto_purchase_items"] == len(ds.auto_purchase_skus()) <= 345
    assert t["price_review_items"] == len(ds.price_review_list()) == 68
    assert t["sell_off_items"] == len(ds.sell_off_list())


def test_quantity_non_negative_and_multiple_of_step(plan):
    for it in plan.items:
        if it["order_qty"] is not None:
            assert it["order_qty"] >= 0 and it["order_qty"] % it["batch_step"] == 0, it["sku_id"]


def test_dead_and_low_markup_get_no_quantity(ds, plan):
    auto = set(ds.auto_purchase_skus())
    for it in plan.items:
        if it["sku_id"] not in auto:
            assert it["order_qty"] == 0 and not it["in_auto_purchase"]
    assert not {i["sku_id"] for i in plan.items} & set(ds.dead_skus)


def test_order_date_plus_lead_not_after_runout(ds, plan):
    for it in plan.items:
        if it["lead_time_days"] is None:
            continue
        if it["unavoidable_shortage"]:
            assert it["next_order_date"] == ds.plan_date.isoformat()
        elif it["next_order_date"] and it["runout_date"]:
            order = date.fromisoformat(it["next_order_date"])
            assert order + timedelta(days=it["lead_time_days"]) <= date.fromisoformat(it["runout_date"]), it["sku_id"]


def test_overdue_rule_matches_dataset(ds, plan):
    # просроченные открытые заказы с остатком к поставке (qty_pending > 0): > 90 дней — в список, остальные — в путь
    od = ds.overdue_orders
    od = od[od["qty_pending"] > 0]
    days = (pd.Timestamp(ds.plan_date) - od["promised_date"]).dt.days
    f = plan.to_facts()
    assert f["totals"]["ask_supplier_items"] == int((days > 90).sum()) > 0
    assert f["totals"]["ask_supplier_qty"] == int(od.loc[days > 90, "qty_pending"].sum())
    live = set(ds.live_skus)
    expect_in = int(ds.in_transit_orders.loc[ds.in_transit_orders["sku_id"].isin(live), "qty_pending"].sum()) + int(
        od.loc[(days <= 90) & od["sku_id"].isin(live), "qty_pending"].sum())
    assert sum(i["in_transit_qty"] for i in plan.items) == expect_in
    assert all(x["days_overdue"] > 90 for x in f["lists"]["ask_supplier"])


def test_command_facts_end_to_end(ds, tmp_path):
    out = tmp_path / "out" / "facts.json"
    assert main(["facts", "--data-dir", str(SHARE), "--out", str(out)]) == 0
    f = json.loads(out.read_text(encoding="utf-8"))
    assert f["totals"]["live_total"] == 413
    assert f["totals"]["auto_purchase_items"] == len(ds.auto_purchase_skus())
    acc = f["forecast_accuracy"]
    assert acc["computed"] is True and len(acc["categories"]) == 6
    assert all(0 < c["wape_model"] < 5 for c in acc["categories"])


def test_command_facts_data_error_exit_code(tmp_path, capsys):
    assert main(["facts", "--data-dir", str(tmp_path / "нет"), "--out", str(tmp_path / "f.json")]) == 2
    assert "Ошибка данных" in capsys.readouterr().err


def test_lead_time_not_far_below_planned(ds, plan):
    # плечо не в разы короче планового: у каждой категории медиана плеча товаров не ниже 50% медианы planned_lead_days
    # категории; сверху не проверяем: товары сидят у разных поставщиков (space_heaters: основной поставщик с планом 256 дн.)
    import numpy as np
    o = ds.orders[(ds.orders["planned_lead_days"] >= 1) & (ds.orders["planned_lead_days"] <= 400)]
    cat = ds.sku.set_index("sku_id")["category"]
    planned = o.assign(c=o["sku_id"].map(cat)).groupby("c")["planned_lead_days"].median()
    for c, ref in planned.items():
        leads = [i["lead_time_days"] for i in plan.items if i["category"] == c and i["lead_time_days"]]
        if leads:
            assert np.median(leads) >= 0.5 * ref, (c, np.median(leads), ref)
    gm = [i["lead_time_days"] for i in plan.items if i["category"] == "grain_mills"]
    assert gm and min(gm) > 12


def test_lead_distribution_close_to_readme(plan):
    # README заказчика: плановое плечо p25 = 52, медиана 123, p90 = 205 по всем 660 заказам. У нас мусор
    # (planned <= 0, 74 заказа) отброшен, поэтому p25 выше (101); медиана и p90 те же.
    d = plan.to_facts()["lead_time_distribution"]["orders_planned"]
    assert 90 <= d["p25"] <= 115 and 115 <= d["median"] <= 140 and 190 <= d["p90"] <= 220
