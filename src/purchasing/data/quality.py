"""Отчёт о качестве данных: только счётчики и имена колонок, без значений строк."""

from .dataset import Dataset
from .schema import FILES


def quality_report(ds: Dataset) -> dict:
    tables = {
        "sku_dim": ds.sku, "sales_monthly": ds.sales, "purchase_orders": ds.orders,
        "receipts": ds.receipts, "supplier_dim": ds.suppliers,
    }
    rows = {FILES[k].name: len(v) for k, v in tables.items()}
    rows[FILES["stock_monthly"].name] = ds.raw_stock_rows
    gaps = {
        FILES[k].name: {c: int(n) for c, n in v.isna().sum().items() if n > 0}
        for k, v in tables.items()
    }
    keys = {
        "sku_dim": ["sku_id"], "sales_monthly": ["sku_id", "month"],
        "purchase_orders": ["po_id", "sku_id"], "supplier_dim": ["supplier_id"],
    }
    dups = {}
    for k, cols in keys.items():
        t = tables[k]
        if k == "purchase_orders":  # пустые po_id считаются отдельно, не дублями
            t = t[t["po_id"].notna()]
        dups[FILES[k].name] = int(t.duplicated(cols).sum())
    dups[FILES["receipts"].name] = int(ds.receipts.duplicated().sum())

    s, known = ds.sales, set(ds.sku["sku_id"])
    flags = ds.sku_flags
    o = ds.orders
    odd = {
        "sales_sku_not_in_sku_dim": int((~s["sku_id"].isin(known)).sum()),
        "orders_sku_not_in_sku_dim": int((~o["sku_id"].isin(known)).sum()),
        "receipts_sku_not_in_sku_dim": int((~ds.receipts["sku_id"].isin(known)).sum()),
        "sku_dim_without_sales": int((~ds.sku["sku_id"].isin(set(s["sku_id"]))).sum()),
        "sales.qty_returned > qty_sold": int((s["qty_returned"] > s["qty_sold"]).sum()),
        "sales.negative_qty": int(((s["qty_sold"] < 0) | (s["qty_returned"] < 0)).sum()),
        "sales.markup_pct_empty_with_sales": int(((s["qty_sold"] > 0) & s["markup_pct"].isna()).sum()),
        "stock.negative_stock_qty": int((ds.stock["stock_qty"] < 0).sum()),
        "orders.negative_qty": int((o[["qty_ordered", "qty_confirmed", "qty_received", "qty_pending"]] < 0).any(axis=1).sum()),
        "orders.promised_before_order": int((o["promised_date"] < o["order_date"]).sum()),
        "sku_dim.dead_with_last_sale_month_in_last_12m": int(
            ((ds.sku["avg_markup_pct_12m"].isna()) & (ds.sku["last_sale_month"] > ds.plan_month - 12)).sum()
        ),
        "orders.open_overdue": len(ds.overdue_orders),
        "sku_dim.live_without_stock_row_in_plan_month": len(ds.zero_stock_live_skus()),
        "sku_dim.weight_kg_empty": int(ds.sku["weight_kg"].isna().sum()) if "weight_kg" in ds.sku else 0,
        "sku_dim.target_cover_days_zero": int((ds.sku["target_cover_days"] == 0).sum()) if "target_cover_days" in ds.sku else 0,
    }
    return {
        "plan_date": ds.plan_date.isoformat(),
        "plan_date_source": {
            "param": "задана параметром", "env": "задана переменной PLAN_DATE",
            "readme_default": "взята по README заказчика (13-е число), а не из данных: дня выгрузки в CSV нет",
            "derived": "выведена из дат в данных",
        }[ds.plan_date_source],
        "rows": rows,
        "missing_values": gaps,
        "duplicates": dups,
        "stock_rows_restored": int(ds.stock["restored"].sum()),
        "sku": {
            "total": len(ds.sku), "live": len(ds.live_skus), "dead": len(ds.dead_skus),
            "low_markup": len(ds.low_markup_skus), "auto_purchase": len(ds.auto_purchase_skus()),
            "retire_list": len(ds.retire_list()), "sell_off_list": len(ds.sell_off_list()),
            "price_review_list": len(ds.price_review_list()),
            "live_zero_stock_plan_month": len(ds.zero_stock_live_skus()),
        },
        "open_orders": {
            "total": len(ds.open_orders), "in_transit": len(ds.in_transit_orders),
            "overdue": len(ds.overdue_orders), "in_transit_qty": ds.in_transit_qty,
            "overdue_qty": ds.overdue_qty,
        },
        "partial_month_rows": int((s["partial_month"] == 1).sum()),
        "oddities": odd,
    }


def format_report(rep: dict) -> str:
    lines = [f"Отчёт о качестве данных (дата плана {rep['plan_date']})"]

    def walk(d, indent=0):
        for k, v in d.items():
            if isinstance(v, dict):
                lines.append("  " * indent + f"{k}:")
                walk(v, indent + 1)
            elif k != "plan_date":
                lines.append("  " * indent + f"{k}: {v}")

    walk({k: v for k, v in rep.items() if k != "plan_date"})
    return "\n".join(lines)
