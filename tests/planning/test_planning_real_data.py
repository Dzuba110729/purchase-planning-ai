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
