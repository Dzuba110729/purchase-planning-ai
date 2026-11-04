"""Хелперы: сборка крошечных придуманных наборов для тестов расчёта закупки (без CSV и реальных строк).

Прогноз задаётся руками (готовый объект Forecast), чтобы ожидаемые значения
считались вручную и не зависели от модели этапа 3.
"""

from datetime import date, timedelta

import pandas as pd

from purchasing.data import Dataset
from purchasing.forecast import DEFAULT_PARAMS, Forecast

PLAN = date(2026, 1, 1)


def _month_days(m: pd.Period) -> int:
    return m.days_in_month


def make_forecast(rates, plan_date=PLAN, n_months=18):
    """rates = {sku: число/день на все месяцы | {"YYYY-MM": число/день}} -> Forecast (qty месяца = ставка × дни)."""
    pm = pd.Period(plan_date, freq="M")
    rows = []
    for sku, r in rates.items():
        for m in pd.period_range(pm, periods=n_months, freq="M"):
            rate = r.get(str(m), 0.0) if isinstance(r, dict) else r
            if isinstance(r, dict) and "default" in r and str(m) not in r:
                rate = r["default"]
            rows.append({"sku_id": sku, "category": "c", "month": m, "qty": rate * _month_days(m)})
    monthly = pd.DataFrame(rows, columns=["sku_id", "category", "month", "qty"])
    return Forecast(
        plan_date=plan_date, plan_month=pm, monthly=monthly, profile=pd.DataFrame(),
        level=pd.Series(dtype=float), adjusted_history=pd.DataFrame(), lost_months=pd.DataFrame(),
        params=DEFAULT_PARAMS,
    )


def season_rates(first=1.0, later=3.0, switch="2026-04"):
    """Ставка `first` до месяца switch (не включая), далее `later`."""
    out = {}
    for m in pd.period_range("2026-01", periods=18, freq="M"):
        out[str(m)] = first if m < pd.Period(switch, freq="M") else later
    return out


def history_orders(supplier, sku, n=3, lead=60, promised_lead=60, qtys=(50, 100, 150), gap=90, start=date(2025, 1, 1)):
    """n завершённых заказов поставщику; приход ровно через `lead` дней, обещано через `promised_lead`.

    Интервал между приходами = gap дней. Возвращает (orders, receipts) — списки строк.
    """
    orders, receipts = [], []
    for i in range(n):
        od = start + timedelta(days=i * gap)
        orders.append({
            "po_id": f"{sku}-H{i}", "sku_id": sku, "supplier_id": supplier, "order_date": pd.Timestamp(od),
            "promised_date": pd.Timestamp(od + timedelta(days=promised_lead)), "planned_lead_days": promised_lead,
            "status": "completed", "qty_ordered": qtys[i % len(qtys)], "qty_confirmed": qtys[i % len(qtys)],
            "qty_received": qtys[i % len(qtys)], "qty_pending": 0,
        })
        receipts.append({
            "sku_id": sku, "receipt_date": pd.Timestamp(od + timedelta(days=lead)),
            "qty": qtys[i % len(qtys)], "supplier_id": supplier,
        })
    return orders, receipts


def open_order(po_id, sku, supplier, promised, qty, plan_date=PLAN):
    return {
        "po_id": po_id, "sku_id": sku, "supplier_id": supplier,
        "order_date": pd.Timestamp(plan_date - timedelta(days=200)), "promised_date": pd.Timestamp(promised),
        "planned_lead_days": 100, "status": "open", "qty_ordered": qty, "qty_confirmed": qty,
        "qty_received": 0, "qty_pending": qty,
    }


ORDER_COLS = ["po_id", "sku_id", "supplier_id", "order_date", "promised_date", "planned_lead_days", "status",
              "qty_ordered", "qty_confirmed", "qty_received", "qty_pending"]


