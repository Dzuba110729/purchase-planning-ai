"""facts.json: схема, пустые значения, git (F)."""

import json
import math
import subprocess
from pathlib import Path

import pytest

from purchasing.planning import FACTS_SCHEMA_VERSION, build_plan, write_facts

from planning_helpers import PLAN, make_forecast, open_order, history_orders

ROOT = Path(__file__).resolve().parents[2]

TOP_KEYS = {"schema_version", "plan_date", "plan_date_source", "generated_from", "params", "totals",
            "items", "lists", "forecast_accuracy", "lead_time_distribution"}
GENERATED_KEYS = {"sku_rows", "sales_rows", "stock_rows", "order_rows", "receipt_rows", "supplier_rows"}
TOTALS_KEYS = {"live_total", "dead_total", "auto_purchase_items", "order_qty_total", "order_now_items",
               "shortage_risk_items", "shortage_risk_auto_items", "overstock_risk_items",
               "overstock_risk_auto_items", "price_review_items", "price_review_ending_items",
               "sell_off_items", "sell_off_qty", "ask_supplier_items", "ask_supplier_qty"}
ITEM_KEYS = {
    "sku_id", "category", "supplier_id", "supply_type", "markup_pct", "abc_group", "importance",
    "in_auto_purchase", "exclusion_reason", "stock", "in_transit_qty", "overdue_included_qty",
    "overdue_excluded_qty", "daily_demand", "stock_days", "stock_days_note", "runout_days", "runout_date",
    "lead_time_days", "lead_time_source", "lead_time_note", "cover_days", "cover_source", "cover_note",
    "demand_lead_cover", "service_level", "safety_stock", "safety_note", "target_stock", "batch_step",
    "batch_step_source", "order_qty", "order_qty_note", "next_order_date", "next_order_note", "order_now",
    "shortage_risk", "unavoidable_shortage", "overstock_risk", "flags", "notes", "missing",
}
LIST_KEYS = {"price_review": {"sku_id", "category", "markup_pct", "stock", "stock_days", "ending"},
             "sell_off": {"sku_id", "category", "stock"},
             "ask_supplier": {"sku_id", "po_id", "supplier_id", "qty", "days_overdue", "promised_date"}}
ACCURACY_KEYS = {"warning", "computed", "backtest_plan_date", "horizon_months", "categories"}
PARAMS_KEYS = {"cover_min_days", "cover_max_days", "overdue_max_days", "overstock_days", "overstock_months",
               "default_batch_step", "abc_bounds", "service_levels", "z_values", "min_markup_pct",
               "min_orders_for_supplier_lead", "min_receipt_dates_for_cover", "lead_max_days",
               "sigma_window_months", "new_item_months", "new_item_cv", "fallback_window_days",
               "sigma_outlier_cap_mult", "safety_cap"}


def _no_bad_numbers(x):
    if isinstance(x, float):
        assert math.isfinite(x)
    elif isinstance(x, dict):
        for v in x.values():
            _no_bad_numbers(v)
    elif isinstance(x, list):
        for v in x:
            _no_bad_numbers(v)


@pytest.fixture
def facts_file(world, tmp_path):
    ds, fc = world(
        40, {"S1": 1.0, "L": 1.0, "NODATA": 0.0},
        extra_skus=[{"sku_id": "L", "supplier_id": "SUP1", "markup": 10.0},
                    {"sku_id": "NODATA", "supplier_id": None, "supply_type": "unknown", "first_sale_month": "2025-12"},
                    {"sku_id": "D", "markup": None}],
        extra_stock={"D": 5}, monthly_sales={"NODATA": [0], "D": [0] * 12},
        extra_orders=[open_order("PO-X", "S1", "SUP1", PLAN.replace(year=2025, month=8, day=1), 9)],
    )
    path = tmp_path / "out" / "facts.json"
    write_facts(build_plan(ds, fc), path)
    return path


def test_facts_schema_is_stable(facts_file):
    f = json.loads(facts_file.read_text(encoding="utf-8"))
    assert set(f) == TOP_KEYS
    assert f["schema_version"] == FACTS_SCHEMA_VERSION == 1
    assert f["plan_date"] == "2026-01-01"
    assert set(f["generated_from"]) == GENERATED_KEYS
    assert set(f["totals"]) == TOTALS_KEYS
    assert set(f["params"]) == PARAMS_KEYS
    assert f["params"]["cover_min_days"] == 60 and f["params"]["cover_max_days"] == 120
    assert f["params"]["service_levels"] == {"A": 0.95, "B": 0.90, "C": 0.80}
    assert f["params"]["overdue_max_days"] == 90 and f["params"]["overstock_months"] == 12
    assert f["params"]["default_batch_step"] == 10
    assert f["items"] and all(set(i) == ITEM_KEYS for i in f["items"])
    for name, keys in LIST_KEYS.items():
        assert f["lists"][name], name
        assert all(set(x) == keys for x in f["lists"][name]), name
    ld = f["lead_time_distribution"]
    assert set(ld) == {"items", "orders_planned", "by_source", "by_supply_type", "by_category"}
    assert set(ld["items"]) == {"n", "p25", "median", "p90"} and ld["items"]["n"] == 3
    assert set(f["forecast_accuracy"]) == ACCURACY_KEYS
    assert "ориентир по сумме за период" in f["forecast_accuracy"]["warning"]
    assert f["forecast_accuracy"]["computed"] is False and f["forecast_accuracy"]["categories"] == []


def test_facts_has_no_nan_or_inf_and_explicit_no_data(facts_file):
    text = facts_file.read_text(encoding="utf-8")
    assert "NaN" not in text and "Infinity" not in text
    f = json.loads(text, parse_constant=lambda c: pytest.fail(f"запрещённая константа {c}"))
    _no_bad_numbers(f)
    nodata = next(i for i in f["items"] if i["sku_id"] == "NODATA")
    assert nodata["stock_days"] is None and nodata["stock_days_note"] == "нет спроса"
    assert nodata["supplier_id"] is None
    for it in f["items"]:  # пустое значение — всегда с пояснением
        for key, note in (("lead_time_days", "lead_time_note"), ("cover_days", "cover_note"),
                          ("order_qty", "order_qty_note"), ("stock_days", "stock_days_note")):
            if it[key] is None:
                assert it[note], (it["sku_id"], key)


def test_totals_match_items_and_lists(facts_file):
    f = json.loads(facts_file.read_text(encoding="utf-8"))
    t, items = f["totals"], f["items"]
    assert t["live_total"] == len(items) == 3 and t["dead_total"] == 1
    assert t["auto_purchase_items"] == sum(i["in_auto_purchase"] for i in items)
    assert t["order_qty_total"] == sum(i["order_qty"] or 0 for i in items)
    assert t["price_review_items"] == len(f["lists"]["price_review"])
    assert t["sell_off_items"] == len(f["lists"]["sell_off"]) == 1 and t["sell_off_qty"] == 5
    assert t["ask_supplier_items"] == len(f["lists"]["ask_supplier"]) == 1 and t["ask_supplier_qty"] == 9
    assert t["shortage_risk_items"] == sum(bool(i["shortage_risk"]) for i in items)


def test_facts_json_is_ignored_by_git():
    for p in ("out/facts.json", "facts.json"):
        r = subprocess.run(["git", "check-ignore", "-q", p], cwd=ROOT)
        assert r.returncode == 0, f"{p} не игнорируется git"
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split("\n")
    assert not [p for p in tracked if p.endswith("facts.json")]
