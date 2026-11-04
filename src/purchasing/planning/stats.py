"""Статистики по истории: плечо, опоздание, ритм приходов, шаг партии.

Что считается и как (все определения здесь, чтобы не гадать):

* Плечо поставки — ПЛАНОВОЕ: медиана `planned_lead_days` заказов поставщика (все
  статусы, дата заказа не позже даты плана). Нужно >= 3 заказов; мусор отбрасывается:
  `planned_lead_days <= 0` (записи задним числом) и `> LEAD_MAX_DAYS`. Нет истории у
  поставщика / `supply_type = unknown` / нет поставщика — медиана по категории. Нет и её —
  «нет данных». Фактическое плечо по приходам в плечо НЕ идёт: приходы в выгрузке
  частичные, повторные и общие на несколько заказов, и первый приход после даты заказа
  давал у grain_mills 7–12 дней при плановых 120.
* Обычное опоздание поставщика — только для сдвига просроченных заказов в пути. Приходы
  сопоставляются с закрытыми заказами по очереди и по количеству (см. `matched_orders`);
  опоздание = фактический срок − плановый, медиана, не меньше 0 (нужно >= 3 заказов).
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
    """Закрытые заказы с найденным приходом: sku_id, supplier_id, category, order_date, actual_days, lateness.

    Сопоставление по очереди и по количеству (для каждой пары товар + поставщик): заказы идут
    от старых к новым и забирают из приходов, не раньше своей даты заказа, по порядку дат,
    нужное количество `qty_received`; остаток прихода достаётся следующему заказу (один приход
    на несколько заказов), частичные приходы суммируются. Дата прихода заказа — дата прихода,
    закрывшего его количество. Приход не может достаться двум заказам больше своего количества,
    поэтому приход по старому заказу не засчитывается новому. Не найдено полное количество,
    срок > LEAD_MAX_DAYS или плановый срок <= 0 — заказ в статистику не входит.
    """
    cols = ["sku_id", "supplier_id", "category", "order_date", "actual_days", "lateness"]
    o = ds.orders
    ts = pd.Timestamp(plan_date)
    o = o[(o["status"] != "open") & (o["qty_received"] > 0) & (o["order_date"] <= ts)].dropna(subset=["supplier_id"])
    r = ds.receipts.dropna(subset=["supplier_id"])
    if o.empty or r.empty:
        return pd.DataFrame(columns=cols)
    pools = {k: g.sort_values("receipt_date")[["receipt_date", "qty"]].to_numpy() for k, g in r.groupby(["sku_id", "supplier_id"])}
    cat = ds.sku.set_index("sku_id")["category"]
    rows = []
    for (sku, sup), g in o.groupby(["sku_id", "supplier_id"]):
        pool = pools.get((sku, sup))
        if pool is None:
            continue
        left = [float(q) for _, q in pool]
        for od in g.sort_values("order_date").itertuples():
            need = float(od.qty_received)
            done = None
            for i, (rd, _) in enumerate(pool):
                if need <= 0:
                    break
                if left[i] <= 0 or pd.Timestamp(rd) < od.order_date:
                    continue
                take = min(left[i], need)
                left[i] -= take
                need -= take
                if need <= 0:
                    done = pd.Timestamp(rd)
            if done is None:
                continue
            actual = (done - od.order_date).days
            planned = (od.promised_date - od.order_date).days
            if 1 <= actual <= LEAD_MAX_DAYS and planned >= 1:
                rows.append((sku, sup, cat.get(sku), od.order_date, actual, actual - planned))
    return pd.DataFrame(rows, columns=cols)


def planned_orders(ds, plan_date) -> pd.DataFrame:
    """Заказы с годным плановым плечом (1..LEAD_MAX_DAYS): sku_id, supplier_id, category, planned."""
    o = ds.orders
    o = o[(o["order_date"] <= pd.Timestamp(plan_date)) & (o["planned_lead_days"] >= 1) & (o["planned_lead_days"] <= LEAD_MAX_DAYS)]
    out = o[["sku_id", "supplier_id", "planned_lead_days"]].rename(columns={"planned_lead_days": "planned"}).copy()
    out["category"] = out["sku_id"].map(ds.sku.set_index("sku_id")["category"])
    return out


def clamp_cover(days: float) -> int:
    return int(min(max(round(float(days)), COVER_MIN_DAYS), COVER_MAX_DAYS))


def history_stats(ds, plan_date) -> HistoryStats:
    st = HistoryStats()
    po = planned_orders(ds, plan_date)
    for sup, grp in po.dropna(subset=["supplier_id"]).groupby("supplier_id"):
        if len(grp) >= MIN_ORDERS_FOR_SUPPLIER:
            st.supplier_lead[sup] = _ceil_int(grp["planned"].median())
    for cat, grp in po.dropna(subset=["category"]).groupby("category"):
        st.category_lead[cat] = _ceil_int(grp["planned"].median())
    m = matched_orders(ds, plan_date)
    for sup, grp in m.groupby("supplier_id"):
        if len(grp) >= MIN_ORDERS_FOR_SUPPLIER:
            st.supplier_lateness[sup] = max(_ceil_int(grp["lateness"].median()), 0)
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
