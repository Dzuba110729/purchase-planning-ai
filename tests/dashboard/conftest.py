from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def sample_facts():
    first = {
        "sku_id": "SKU-000001", "category": "grain_mills", "stock": 0,
        "in_transit_qty": 4, "order_qty": 30, "next_order_date": "2026-08-13",
        "runout_date": "2026-08-14", "stock_days": 1.3, "lead_time_days": 120,
        "demand_lead_cover": 26.5, "cover_days": 60, "batch_step": 10,
        "daily_demand": 1.203, "safety_stock": 3.5, "target_stock": 30.0,
        "markup_pct": 35.0, "order_now": True, "shortage_risk": True,
        "overstock_risk": False, "in_auto_purchase": True,
        "monthly_forecast": [{"month": "2026-08", "qty": 9.75}, {"month": "2026-09", "qty": 19.5}],
        "supplier_id": "SUP-PRIVATE", "notes": ["PRIVATE NOTES"],
    }
    second = {
        **first, "sku_id": "SKU-000002", "category": "space_heaters",
        "order_qty": 20, "next_order_date": "2026-09-04", "order_now": False,
        "shortage_risk": False, "overstock_risk": True,
    }
    third = {
        **first, "sku_id": "SKU-000003", "category": "space_heaters",
        "order_qty": None, "next_order_date": None, "order_now": False,
        "in_auto_purchase": False, "markup_pct": 12.0,
    }
    return {
        "schema_version": 1, "plan_date": "2026-08-13",
        "generated_from": {"sku_rows": 4},
        "totals": {
            "live_total": 3, "dead_total": 1, "weight_known_items": 2, "order_qty_total": 50,
            "order_now_items": 1, "shortage_risk_items": 2, "overstock_risk_items": 1,
            "price_review_items": 1, "price_review_ending_items": 1, "sell_off_items": 1,
            "sell_off_qty": 5, "ask_supplier_items": 1, "ask_supplier_qty": 17,
            "open_order_items": 3, "overdue_order_items": 2, "overdue_order_qty": 19,
        },
        "items": [first, second, third],
        "lists": {
            "price_review": [{
                "sku_id": third["sku_id"], "category": third["category"], "markup_pct": 12.0,
                "stock": 0, "stock_days": 1.3, "ending": True,
            }],
            "discontinue": [{"sku_id": "SKU-000004", "category": "grain_mills", "stock": 5}],
            "sell_off": [{"sku_id": "SKU-000004", "category": "grain_mills", "stock": 5}],
            "ask_supplier": [{
                "sku_id": first["sku_id"], "qty": 17, "days_overdue": 100,
                "promised_date": "2026-05-05", "po_id": "PO-PRIVATE", "supplier_id": "SUP-PRIVATE",
            }],
        },
        "forecast_accuracy": {"warning": "PRIVATE WARNING"},
    }


@pytest.fixture(scope="session")
def real_facts():
    from purchasing.data import load_dataset
    from purchasing.forecast import build_forecast
    from purchasing.planning import build_plan

    data_dir = ROOT / "share"
    if not data_dir.is_dir():
        pytest.skip("нет локальных данных в share/")
    ds = load_dataset(data_dir)
    return build_plan(ds, build_forecast(ds)).to_facts()
