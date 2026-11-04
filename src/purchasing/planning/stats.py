"""Статистики по истории: плечо, опоздание, ритм приходов, шаг партии.

Что считается и как (все определения здесь, чтобы не гадать):

* Фактическое плечо одного заказа — дней от `order_date` до первого прихода того же
  товара от того же поставщика в дату >= `order_date` (поиск вперёд по `receipts`).
  Берутся закрытые заказы (`completed`/`closed`) с `qty_received > 0`; приход дальше
  `LEAD_MAX_DAYS` дней и «приход в день заказа» (0 дней) заказу не приписываются.
  Заказы с обещанной датой не позже даты заказа (запись задним числом, в реальных
  данных 45 заказов одного поставщика с «плечом −13») в статистику не входят.
* Плечо поставщика — медиана фактических плеч его заказов (нужно >= 3 заказов).
  Плечо категории — медиана по всем сопоставленным заказам товаров категории.
* Обычное опоздание поставщика — медиана (фактическое плечо − обещанное
  `promised_date − order_date`), не меньше 0 (нужно >= 3 заказов).
* Период покрытия — медиана интервалов между датами прихода поставщика (разные даты,
  >= 3 дат), зажатая в 60–120 дней. Нет данных у поставщика — медиана этих значений
  по всем поставщикам с данными.
* Шаг партии — наибольший общий делитель количеств всех заказов товара (`qty_ordered`
  любого статуса). Он совпадает с кратностью, в которой товар реально заказывали;
  нет заказов — `DEFAULT_BATCH_STEP` (10).
"""

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .params import (
    COVER_MAX_DAYS, COVER_MIN_DAYS, DEFAULT_BATCH_STEP, LEAD_MAX_DAYS,
    MIN_ORDERS_FOR_SUPPLIER, MIN_RECEIPT_DATES_FOR_COVER,
)


@dataclass
class HistoryStats:
    supplier_lead: dict[str, int] = field(default_factory=dict)
    category_lead: dict[str, int] = field(default_factory=dict)
    supplier_lateness: dict[str, int] = field(default_factory=dict)
    supplier_cover: dict[str, int] = field(default_factory=dict)
    global_cover: int | None = None
    batch_step: dict[str, int] = field(default_factory=dict)


def _ceil_int(x: float) -> int:
    return int(math.ceil(round(float(x), 9)))


def matched_orders(ds, plan_date) -> pd.DataFrame:
    """Закрытые заказы с найденным приходом: sku_id, supplier_id, category, actual_days, lateness."""
    cols = ["sku_id", "supplier_id", "category", "actual_days", "lateness"]
    o = ds.orders
    ts = pd.Timestamp(plan_date)
    o = o[(o["status"] != "open") & (o["qty_received"] > 0) & (o["order_date"] <= ts)].dropna(subset=["supplier_id"])
    if o.empty or ds.receipts.empty:
        return pd.DataFrame(columns=cols)
    left = o[["sku_id", "supplier_id", "order_date", "promised_date"]].copy()
    left["order_date"] = left["order_date"].astype("datetime64[ns]")
    left["promised_date"] = left["promised_date"].astype("datetime64[ns]")
    right = ds.receipts[["sku_id", "supplier_id", "receipt_date"]].copy()
    right["receipt_date"] = right["receipt_date"].astype("datetime64[ns]")
    m = pd.merge_asof(
        left.sort_values("order_date"), right.sort_values("receipt_date"),
        left_on="order_date", right_on="receipt_date", by=["sku_id", "supplier_id"], direction="forward",
    ).dropna(subset=["receipt_date"])
    m["actual_days"] = (m["receipt_date"] - m["order_date"]).dt.days
    planned = (m["promised_date"] - m["order_date"]).dt.days
    m = m[(m["actual_days"] >= 1) & (m["actual_days"] <= LEAD_MAX_DAYS) & (planned >= 1)].copy()
    m["lateness"] = m["actual_days"] - planned[m.index]
    cat = ds.sku.set_index("sku_id")["category"]
    m["category"] = m["sku_id"].map(cat)
    return m[cols]


def clamp_cover(days: float) -> int:
    return int(min(max(round(float(days)), COVER_MIN_DAYS), COVER_MAX_DAYS))


def history_stats(ds, plan_date) -> HistoryStats:
    st = HistoryStats()
    m = matched_orders(ds, plan_date)
    if not m.empty:
        g = m.groupby("supplier_id")
        for sup, grp in g:
            if len(grp) >= MIN_ORDERS_FOR_SUPPLIER:
                st.supplier_lead[sup] = max(_ceil_int(grp["actual_days"].median()), 1)
                st.supplier_lateness[sup] = max(_ceil_int(grp["lateness"].median()), 0)
        for cat, grp in m.dropna(subset=["category"]).groupby("category"):
            st.category_lead[cat] = max(_ceil_int(grp["actual_days"].median()), 1)
    r = ds.receipts
    r = r[r["receipt_date"] <= pd.Timestamp(plan_date)]
    for sup, grp in r.dropna(subset=["supplier_id"]).groupby("supplier_id"):
        dates = np.sort(grp["receipt_date"].drop_duplicates().to_numpy())
        if len(dates) >= MIN_RECEIPT_DATES_FOR_COVER:
            gaps = np.diff(dates).astype("timedelta64[D]").astype(int)
            st.supplier_cover[sup] = clamp_cover(float(np.median(gaps)))
    if st.supplier_cover:
        st.global_cover = clamp_cover(float(np.median(list(st.supplier_cover.values()))))
    o = ds.orders
    o = o[o["qty_ordered"] > 0]
    for sku, grp in o.groupby("sku_id"):
        st.batch_step[sku] = int(np.gcd.reduce(grp["qty_ordered"].astype("int64").to_numpy()))
    return st


def batch_step_for(st: HistoryStats, sku_id: str) -> tuple[int, str]:
    step = st.batch_step.get(sku_id)
    if step and step > 0:
        return step, "history"
    return DEFAULT_BATCH_STEP, "default"
