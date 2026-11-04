"""Свойства расчёта на наборе придуманных товаров (B, C, D, E)."""

from datetime import date, timedelta

import pandas as pd
import pytest

from purchasing.planning import build_plan

from planning_helpers import PLAN, history_orders, make_forecast, open_order, season_rates

STOCKS = [0, 5, 40, 100, 150, 200, 500, 5000]
PATTERNS = {"flat": 1.0, "season": season_rates(), "tiny": 0.05, "none": 0.0}


@pytest.fixture
def many(build):
    """Много товаров: разные остатки и спрос, часть с заказами в пути, часть без истории."""
    o, r = history_orders("SUP1", "base", lead=60, promised_lead=50)
    skus, stock, rates, orders = [], {}, {}, list(o)
    i = 0
    for st in STOCKS:
        for name, rate in PATTERNS.items():
            sid = f"M{i}"
            i += 1
            sup = "SUP1" if i % 3 else None
            skus.append({"sku_id": sid, "supplier_id": sup, "supply_type": "import" if sup else "unknown",
                         "decile": 1 + i % 10, "markup": 30.0 + i})
            stock[sid] = st
            rates[sid] = rate
            if i % 4 == 0:
                orders.append(open_order(f"PO-{sid}", sid, "SUP1", PLAN + timedelta(days=30 + i), 70 + i))
            if i % 5 == 0:
                orders.append(open_order(f"PO-O{sid}", sid, "SUP1", PLAN - timedelta(days=10 + i), 33))
    skus.append({"sku_id": "base", "supplier_id": "SUP1"})
    stock["base"] = 10
    rates["base"] = 1.0
    ds = build(skus, stock=stock, orders=orders, receipts=r)
    return ds, make_forecast(rates)


def test_quantity_non_negative_and_multiple_of_step(many):
    plan = build_plan(*many)
    assert len(plan.items) > 30
    for it in plan.items:
        q = it["order_qty"]
        if q is None:
            continue
        assert q >= 0 and q % it["batch_step"] == 0, it["sku_id"]


def test_dead_and_low_markup_never_in_auto_purchase(build):
    skus = [
        {"sku_id": "OK", "markup": 40.0}, {"sku_id": "LOW", "markup": 10.0},
        {"sku_id": "DEAD", "markup": None},
    ]
    ds = build(skus, stock={"OK": 0, "LOW": 0, "DEAD": 50}, monthly_sales={"DEAD": [0] * 12})
    plan = build_plan(ds, make_forecast({"OK": 1.0, "LOW": 1.0}))
    facts = plan.to_facts()
    by = {i["sku_id"]: i for i in facts["items"]}
    assert "DEAD" not in by
    assert by["LOW"]["in_auto_purchase"] is False and by["LOW"]["order_qty"] == 0
    assert by["LOW"]["exclusion_reason"] == "low_markup"
    assert by["OK"]["in_auto_purchase"] is True
    assert facts["totals"]["auto_purchase_items"] == 1
    assert [x["sku_id"] for x in facts["lists"]["price_review"]] == ["LOW"]
    assert [x["sku_id"] for x in facts["lists"]["sell_off"]] == ["DEAD"]
    assert facts["totals"]["dead_total"] == 1 and facts["totals"]["live_total"] == 2


def test_auto_flag_equals_dataset_list(many):
    ds, fc = many
    plan = build_plan(ds, fc)
    assert {i["sku_id"] for i in plan.items if i["in_auto_purchase"]} == set(ds.auto_purchase_skus())
    for it in plan.items:
        if not it["in_auto_purchase"]:
            assert it["order_qty"] == 0


def test_low_markup_ending_flag(build):
    # запас в днях = 40 < плечо+покрытие (или 90 без данных) -> «заканчивается»; 400 -> нет
    skus = [{"sku_id": "L1", "markup": 10.0}, {"sku_id": "L2", "markup": 10.0}]
    ds = build(skus, stock={"L1": 40, "L2": 400})
    facts = build_plan(ds, make_forecast({"L1": 1.0, "L2": 1.0})).to_facts()
    flags = {x["sku_id"]: x["ending"] for x in facts["lists"]["price_review"]}
    assert flags == {"L1": True, "L2": False}
    assert facts["totals"]["price_review_ending_items"] == 1


@pytest.mark.parametrize("n,expected", [(0, 150), (50, 100), (100, 50), (150, 0), (300, 0)])
def test_extra_in_transit_reduces_recommendation(world, n, expected):
    # остаток 40, нужно 110. Заказ в пути на n шт.: нужно 110-n -> вверх до шага 50; не меньше 0.
    # n=0 -> 150; 50 -> 60 -> 100; 100 -> 10 -> 50; 150 -> 0; 300 -> 0.
    # (n кратно 50: шаг партии = НОД всех заказов товара, включая этот.)
    extra = [open_order("PO-N", "S1", "SUP1", PLAN + timedelta(days=40), n)] if n else []
    ds, fc = world(40, {"S1": 1.0}, extra_orders=extra)
    assert build_plan(ds, fc).item("S1")["order_qty"] == expected


def test_order_date_plus_lead_not_after_runout(many):
    plan = build_plan(*many)
    checked = 0
    for it in plan.items:
        if it["lead_time_days"] is None:
            continue
        if it["unavoidable_shortage"]:
            assert it["next_order_date"] == PLAN.isoformat(), it["sku_id"]
            continue
        if it["next_order_date"] and it["runout_date"]:
            order = date.fromisoformat(it["next_order_date"])
            runout = date.fromisoformat(it["runout_date"])
            assert order + timedelta(days=it["lead_time_days"]) <= runout, it["sku_id"]
            checked += 1
    assert checked > 5


def test_shortage_risk_equals_unavoidable_and_overstock_over_12_months(world):
    # остаток 400 при 1 шт/день: хватает на 400 дней > 365 -> излишек; 300 -> нет
    for stock, over in ((400, True), (300, False)):
        ds, fc = world(stock, {"S1": 1.0})
        it = build_plan(ds, fc).item("S1")
        assert it["overstock_risk"] is over
        assert it["shortage_risk"] == it["unavoidable_shortage"] is False
