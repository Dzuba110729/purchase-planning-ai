"""Расчёт закупки: `build_plan(dataset, forecast) -> PurchasePlan`.

Правила (из брифа, не менять):

1. Плечо — плановое (`planned_lead_days`): медиана по поставщику (>= 3 заказов, мусор <= 0
   и > 400 отброшен); у `supply_type = unknown` / без поставщика / без истории — медиана по
   категории; нет и её — `null` + «нет данных». Фактическое опоздание в плечо не добавляется,
   оно только сдвигает просроченные заказы в пути.
2. Покрытие — по ритму поставщика, 60–120 дней; нет данных — медиана по поставщикам.
3. ABC: важность = среднемесячные продажи за полные месяцы × дециль цены × наценка;
   товары по убыванию важности, группа по накопленной доле ДО товара: < 80% — A,
   < 95% — B, иначе C (товар, пересекающий границу, остаётся в старшей группе).
   Страховой запас = z(уровень сервиса) × σ месячного спроса × sqrt(плечо / 30),
   z: A 0,95 / B 0,90 / C 0,80; σ — выборочное стандартное отклонение продаж по
   полным месяцам за последние 12 (пропуск = 0), выбросы обрезаны потолком прогноза
   (4 × медиана ненулевых месяцев товара). Спрос = продажи − возвраты по месяцам.
   «Мало истории» — меньше NEW_ITEM_MONTHS (3) полных месяцев от первой продажи товара
   до месяца плана (пропуск внутри окна = 0, это не «мало»). Запас не больше спроса за (плечо + покрытие)
   товара; сработал потолок — флаг safety_capped и пометка в safety_note. Меньше 3 месяцев истории (новый
   товар) — σ = 50% среднемесячного спроса, с пометкой.
4. Просроченные заказы (обещано < даты плана): просрочка <= 90 дней — в пути, приход =
   обещанная дата + обычное опоздание поставщика (не раньше даты плана; нет данных —
   0 дней и пометка); > 90 — не считаются, идут в список «уточнить у поставщика».
5. Дневной спрос — из прогноза: спрос за «плечо + покрытие» / число этих дней
   (месячный прогноз делится на дни месяца, для месяца плана — со дня плана).
   Запас в днях = (остаток + заказы в пути) / дневной спрос.
6. Цель = спрос за (плечо + покрытие) + страховой запас; количество =
   max(0, цель − остаток − в пути), вверх до шага партии.
7. Дата заказа и дефицит — по дневной симуляции остатка: остаток уменьшается по прогнозу,
   приходы в пути добавляются в день прихода. Заказ нужен в последний день, когда
   остаток ещё не ниже «спрос за плечо + страховой»; если уже ниже — сегодня.
   Дефицит неизбежен: остаток кончится раньше, чем придёт заказ, размещённый сегодня
   (день окончания < плечо). Это же — «риск дефицита».
8. Излишек: остатка с учётом прихода в пути хватает дольше 365 дней.
9. В автозакупку — только `dataset.auto_purchase_skus()`; остальные живые получают
   количество 0 (низкая наценка), мёртвые в позиции не попадают вовсе.
"""

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .params import (
    ABC_BOUNDS, FALLBACK_WINDOW_DAYS, NEW_ITEM_CV, NEW_ITEM_MONTHS, OVERDUE_MAX_DAYS, OVERSTOCK_DAYS,
    PARAMS_FOR_FACTS, SIGMA_WINDOW_MONTHS, SERVICE_LEVELS, Z_VALUES,
)
from purchasing.forecast.model import DEFAULT_PARAMS as FORECAST_PARAMS, _winsorize

from .stats import batch_step_for, history_stats, planned_orders

EPS = 1e-9
NO_DATA = "нет данных"
ACCURACY_WARNING = "Точность прогноза по товару низкая, ориентир по сумме за период (по категории), а не по месяцу одного товара."


@dataclass
class PurchasePlan:
    plan_date: date
    plan_date_source: str
    generated_from: dict
    items: list[dict]
    lists: dict
    totals: dict
    forecast_accuracy: dict
    params: dict = field(default_factory=lambda: dict(PARAMS_FOR_FACTS))
    lead_time_distribution: dict = field(default_factory=dict)

    def item(self, sku_id: str) -> dict:
        for it in self.items:
            if it["sku_id"] == sku_id:
                return it
        raise KeyError(sku_id)

    def to_facts(self) -> dict:
        from .facts import plan_to_facts

        return plan_to_facts(self)


# --- вспомогательное ---------------------------------------------------------

def _none(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) or x is pd.NA else x


def _ceil_to_step(x: float, step: int) -> int:
    if x <= EPS:
        return 0
    return int(math.ceil(x / step - 1e-9)) * step


def _to_date(x) -> date:
    return pd.Timestamp(x).date()


def _round(x, n=2):
    return None if x is None else round(float(x), n)


def assign_abc(importance: pd.Series) -> pd.Series:
    """Группа A/B/C по накопленной доле важности ДО товара (< 80% — A, < 95% — B, иначе C)."""
    total = float(importance.sum())
    if total <= 0:
        return pd.Series("C", index=importance.index)
    order = importance.reset_index().rename(columns={importance.index.name or "index": "sku_id", 0: "imp"})
    order.columns = ["sku_id", "imp"]
    order = order.sort_values(["imp", "sku_id"], ascending=[False, True])
    before = (order["imp"].cumsum() - order["imp"]) / total
    grp = np.where(before < ABC_BOUNDS["A"] - 1e-12, "A", np.where(before < ABC_BOUNDS["B"] - 1e-12, "B", "C"))
    grp = np.where(order["imp"].to_numpy() <= 0, "C", grp)
    return pd.Series(grp, index=order["sku_id"].to_numpy()).reindex(importance.index)


class _Calendar:
    """Дни от даты плана до конца последнего месяца прогноза."""

    def __init__(self, plan_date: date, months: list[pd.Period]):
        last = max(months)
        end = last.end_time.date()
        self.n = (end - plan_date).days + 1
        self.start = plan_date
        days = [plan_date + timedelta(days=i) for i in range(self.n)]
        self.month_index = {}
        for i, d in enumerate(days):
            self.month_index.setdefault(pd.Period(d, freq="M"), []).append(i)
        self.days_in_month = {m: m.days_in_month for m in self.month_index}

    def rates(self, monthly: pd.Series) -> np.ndarray:
        r = np.zeros(self.n)
        for m, idx in self.month_index.items():
            r[idx] = float(monthly.get(m, 0.0)) / self.days_in_month[m]
        return r


def _demand_sigma(ds, sku_id, first_month, plan_month, sales_by_sku, forecast_mean):
    """(σ месячного спроса, среднемесячный спрос по истории, число месяцев, упрощённо?)."""
    start = max(pd.Period(first_month, freq="M"), plan_month - SIGMA_WINDOW_MONTHS)
    months = pd.period_range(start, plan_month - 1, freq="M") if start <= plan_month - 1 else []
    n = len(months)
    s = sales_by_sku.get(sku_id)
    vals = (s.reindex(months).fillna(0.0).to_numpy(float) if s is not None and n else np.zeros(n))
    if n >= NEW_ITEM_MONTHS:
        # потолок выбросов — как в прогнозе: cap_mult × медиана ненулевых месяцев товара
        capped = _winsorize(pd.DataFrame([vals]), FORECAST_PARAMS.cap_mult).iloc[0].to_numpy(float)
        return float(np.std(capped, ddof=1)), float(capped.mean()), n, False
    mean = float(vals.mean()) if n else float(forecast_mean)
    return NEW_ITEM_CV * mean, mean, n, True


# --- основной расчёт ----------------------------------------------------------

def build_plan(dataset, forecast, plan_date=None, accuracy=None) -> PurchasePlan:
    """Рассчитывает закупку по каждому живому товару. `accuracy` — результат `backtest()` (необязательно)."""
    ds = dataset
    pdate = _to_date(plan_date) if plan_date is not None else ds.plan_date
    pmonth = pd.Period(pdate, freq="M")
    ts = pd.Timestamp(pdate)
    st = history_stats(ds, pdate)

    fm = forecast.monthly
    months = sorted(fm["month"].unique())
    cal = _Calendar(pdate, months)
    fc_by_sku = {k: g.set_index("month")["qty"] for k, g in fm.groupby("sku_id")}

    sku = ds.sku.set_index("sku_id")
    flags = ds.sku_flags.set_index("sku_id")
    live = sorted(ds.live_skus)
    auto = set(ds.auto_purchase_skus())
    stock_now = ds.stock_at(pmonth)

    # ABC
    mean_sales = ds.mean_monthly_sales().reindex(live).fillna(0.0)
    decile = sku["price_decile"].reindex(live) if "price_decile" in sku.columns else pd.Series(np.nan, index=live)
    decile_missing = decile.isna()
    importance = (mean_sales * decile.fillna(5.0) * sku["avg_markup_pct_12m"].reindex(live)).rename_axis("sku_id")
    abc = assign_abc(importance)

    full = ds.full_months_sales()
    full = full[full["month"] < pmonth]
    net = (full["qty_sold"].astype(float) - full["qty_returned"].astype(float)).clip(lower=0)  # возвраты вычтены
    sales_by_sku = {k: g.set_index("month")["net"] for k, g in full.assign(net=net).groupby("sku_id")}

    # заказы в пути / просроченные
    oo = ds.open_orders
    oo = oo[oo["qty_pending"] > 0].copy()
    oo["promised_date"] = pd.to_datetime(oo["promised_date"])
    oo["days_overdue"] = (ts - oo["promised_date"]).dt.days
    transit_by_sku: dict[str, list[dict]] = {}
    ask_rows = []
    for r in oo.itertuples(index=False):
        base = {"sku": r.sku_id, "qty": int(r.qty_pending), "supplier": _none(r.supplier_id)}
        if r.days_overdue <= 0:
            transit_by_sku.setdefault(r.sku_id, []).append({**base, "arrival": _to_date(r.promised_date), "kind": "transit"})
        elif r.days_overdue <= OVERDUE_MAX_DAYS:
            late = st.supplier_lateness.get(base["supplier"])
            arrival = max(_to_date(r.promised_date) + timedelta(days=late or 0), pdate)
            transit_by_sku.setdefault(r.sku_id, []).append(
                {**base, "arrival": arrival, "kind": "overdue_in", "late_known": late is not None}
            )
        else:
            transit_by_sku.setdefault(r.sku_id, []).append({**base, "kind": "overdue_out"})
            ask_rows.append({
                "sku_id": r.sku_id, "po_id": _none(r.po_id), "supplier_id": base["supplier"], "qty": int(r.qty_pending),
                "days_overdue": int(r.days_overdue), "promised_date": _to_date(r.promised_date).isoformat(),
            })

    items = []
    for sid in live:
        row = sku.loc[sid]
        supplier = _none(row.get("supplier_id"))
        supply_type = _none(row.get("supply_type"))
        category = row["category"]
        notes, flg, missing = [], [], []
        stock = int(stock_now.get(sid, 0))
        monthly = fc_by_sku.get(sid, pd.Series(dtype=float))
        rates = cal.rates(monthly)
        cum0 = np.concatenate([[0.0], np.cumsum(rates)])

        def demand(a, b):
            b = min(b, cal.n)
            return float(cum0[b] - cum0[a]) if b > a else 0.0

        # плечо
        by_supplier = supplier is not None and supply_type != "unknown"
        lead, lead_src, lead_note = None, None, NO_DATA
        if by_supplier and supplier in st.supplier_lead:
            lead, lead_src = st.supplier_lead[supplier], "supplier"
            lead_note = "плановое плечо: медиана planned_lead_days заказов поставщика"
        elif category in st.category_lead:
            lead, lead_src = st.category_lead[category], "category"
            lead_note = "плановое плечо: медиана planned_lead_days по категории"
            flg.append("lead_by_category")
            notes.append("плечо взято как медиана по категории: у поставщика нет своей истории")
        if lead is None:
            missing.append("lead_time_days")
        # покрытие
        cover, cover_src = None, None
        if by_supplier and supplier in st.supplier_cover:
            cover, cover_src = st.supplier_cover[supplier], "supplier"
        elif st.global_cover is not None:
            cover, cover_src = st.global_cover, "all_suppliers"
            flg.append("cover_by_all_suppliers")
            notes.append("период покрытия взят как медиана по всем поставщикам: у поставщика нет своего ритма")
        if cover is None:
            missing.append("cover_days")
        if supply_type == "unknown":
            flg.append("unknown_supply")

        # заказы в пути
        tr = transit_by_sku.get(sid, [])
        in_transit = sum(t["qty"] for t in tr if t["kind"] != "overdue_out")
        overdue_in = sum(t["qty"] for t in tr if t["kind"] == "overdue_in")
        overdue_out = sum(t["qty"] for t in tr if t["kind"] == "overdue_out")
        if overdue_in:
            flg.append("overdue_in_transit")
            if any(not t["late_known"] for t in tr if t["kind"] == "overdue_in"):
                notes.append("просроченный заказ (до 90 дней): обычное опоздание поставщика неизвестно (нет данных), принято 0 дней")
            else:
                notes.append("просроченный заказ (до 90 дней) считается в пути: дата сдвинута на обычное опоздание поставщика")
        if overdue_out:
            flg.append("overdue_excluded")
            notes.append("заказ просрочен больше 90 дней: в запас не входит, уточнить у поставщика")

        # симуляция остатка
        arr = np.zeros(cal.n)
        for t in tr:
            if t["kind"] != "overdue_out":
                off = (t["arrival"] - pdate).days
                if 0 <= off < cal.n:
                    arr[off] += t["qty"]
        s_start = stock + np.cumsum(arr) - cum0[: cal.n]
        s_end = s_start - rates
        out = np.flatnonzero(s_end < -EPS)
        k = int(out[0]) if len(out) else None

        # спрос и запас в днях
        window = (lead + cover) if (lead is not None and cover is not None) else FALLBACK_WINDOW_DAYS
        d_window = demand(0, window)
        daily = d_window / window
        if daily <= EPS:
            stock_days, sd_note = None, "нет спроса"
        else:
            stock_days, sd_note = (stock + in_transit) / daily, None

        # страховой запас
        fc_mean = float(monthly.head(12).mean()) if len(monthly) else 0.0
        sigma, hist_mean, n_hist, simplified = _demand_sigma(
            ds, sid, row["first_sale_month"], pmonth, sales_by_sku, fc_mean
        )
        group = abc[sid]
        z = Z_VALUES[group]
        safety = z * sigma * math.sqrt(lead / 30.0) if lead is not None else None
        safety_note = None
        if simplified:
            flg.append("safety_simplified")
            safety_note = f"мало истории ({n_hist} полных мес.): разброс спроса принят {int(NEW_ITEM_CV * 100)}% от среднемесячного"
        if safety is None:
            safety_note = NO_DATA
            missing.append("safety_stock")
        elif safety > d_window + EPS:
            safety = d_window  # потолок: не больше спроса за плечо + покрытие
            flg.append("safety_capped")
            note = "страховой запас ограничен спросом за плечо + покрытие"
            safety_note = f"{safety_note}; {note}" if safety_note else note

        # цель и количество
        step, step_src = batch_step_for(st, sid)
        target = (d_window + safety) if (lead is not None and cover is not None) else None
        is_auto = sid in auto
        if not is_auto:
            order_qty, oq_note = 0, "не в автозакупке: низкая наценка"
            if target is None:
                missing.append("target_stock")
        elif target is None:
            order_qty, oq_note = None, NO_DATA
            missing += ["target_stock", "order_qty"]
        else:
            order_qty, oq_note = _ceil_to_step(target - stock - in_transit, step), None

        # дата заказа и риски
        next_date, no_note, order_now = None, None, None
        unavoidable = overstock = None
        if lead is None:
            no_note = NO_DATA
            missing += ["next_order_date", "unavoidable_shortage", "shortage_risk"]
        else:
            unavoidable = bool(k is not None and k < lead)
            if lead <= cal.n:
                d = np.arange(0, cal.n - lead + 1)
                rop = cum0[d + lead] - cum0[d] + safety
                viol = np.flatnonzero(s_start[d] < rop - EPS)
                if len(viol):
                    d_o = max(int(viol[0]) - 1, 0)
                    next_date = (pdate + timedelta(days=d_o)).isoformat()
                    order_now = d_o == 0
                else:
                    no_note = "в горизонте прогноза заказ не понадобится"
            else:
                no_note = "плечо длиннее горизонта прогноза"
        if stock + in_transit > 0:
            overstock = bool((k is not None and k > OVERSTOCK_DAYS) or (k is None and cal.n >= OVERSTOCK_DAYS))
        else:
            overstock = False

        low = bool(flags.loc[sid, "low_markup"])
        if low:
            flg.append("low_markup")
        if decile_missing[sid]:
            notes.append("дециль цены не задан: в важности принято 5")
        if sd_note:
            flg.append("no_demand")

        items.append({
            "sku_id": sid, "category": category, "supplier_id": supplier, "supply_type": supply_type,
            "markup_pct": _round(row["avg_markup_pct_12m"], 1), "abc_group": group,
            "importance": _round(importance[sid], 2), "in_auto_purchase": is_auto,
            "exclusion_reason": "low_markup" if low else None,
            "stock": stock, "in_transit_qty": int(in_transit), "overdue_included_qty": int(overdue_in),
            "overdue_excluded_qty": int(overdue_out),
            "daily_demand": _round(daily, 3), "stock_days": _round(stock_days, 1), "stock_days_note": sd_note,
            "runout_days": k, "runout_date": (pdate + timedelta(days=k)).isoformat() if k is not None else None,
            "lead_time_days": lead, "lead_time_source": lead_src, "lead_time_note": lead_note,
            "cover_days": cover, "cover_source": cover_src, "cover_note": NO_DATA if cover is None else None,
            "demand_lead_cover": _round(d_window, 2) if target is not None else None,
            "service_level": SERVICE_LEVELS[group], "safety_stock": _round(safety, 2), "safety_note": safety_note,
            "target_stock": _round(target, 2), "batch_step": step, "batch_step_source": step_src,
            "order_qty": order_qty, "order_qty_note": oq_note,
            "next_order_date": next_date, "next_order_note": no_note, "order_now": order_now,
            "shortage_risk": unavoidable, "unavoidable_shortage": unavoidable, "overstock_risk": overstock,
            "flags": flg, "notes": notes, "missing": missing,
        })

    by = {i["sku_id"]: i for i in items}
    lists = _build_lists(ds, by, ask_rows, pdate)
    totals = _totals(ds, items, lists)
    return PurchasePlan(
        lead_time_distribution=_lead_distribution(items, planned_orders(ds, pdate)),
        plan_date=pdate, plan_date_source=ds.plan_date_source,
        generated_from={
            "sku_rows": len(ds.sku), "sales_rows": len(ds.sales), "stock_rows": int(ds.raw_stock_rows),
            "order_rows": len(ds.orders), "receipt_rows": len(ds.receipts), "supplier_rows": len(ds.suppliers),
        },
        items=items, lists=lists, totals=totals, forecast_accuracy=_accuracy(accuracy),
    )


def _build_lists(ds, by, ask_rows, pdate):
    sku = ds.sku.set_index("sku_id")
    stock_now = ds.stock_at(pd.Period(pdate, freq="M"))
    review = []
    for sid in sorted(ds.price_review_list()):
        it = by.get(sid)
        if it is None:
            continue
        sd = it["stock_days"]
        thr = (it["lead_time_days"] + it["cover_days"]) if (it["lead_time_days"] is not None and it["cover_days"] is not None) else FALLBACK_WINDOW_DAYS
        review.append({
            "sku_id": sid, "category": it["category"], "markup_pct": it["markup_pct"], "stock": it["stock"],
            "stock_days": sd, "ending": bool(sd is not None and sd < thr),
        })
    sell = [
        {"sku_id": sid, "category": sku.loc[sid, "category"], "stock": int(stock_now.get(sid, 0))}
        for sid in sorted(ds.sell_off_list())
    ]
    ask = sorted(ask_rows, key=lambda x: (-x["days_overdue"], x["sku_id"], str(x["po_id"])))
    return {"price_review": review, "sell_off": sell, "ask_supplier": ask}


def _totals(ds, items, lists):
    auto = [i for i in items if i["in_auto_purchase"]]
    return {
        "live_total": len(items), "dead_total": len(ds.dead_skus),
        "auto_purchase_items": len(auto),
        "order_qty_total": int(sum(i["order_qty"] or 0 for i in items)),
        "order_now_items": sum(1 for i in items if (i["order_qty"] or 0) > 0 and i["order_now"]),
        "shortage_risk_items": sum(1 for i in items if i["shortage_risk"]),
        "shortage_risk_auto_items": sum(1 for i in auto if i["shortage_risk"]),
        "overstock_risk_items": sum(1 for i in items if i["overstock_risk"]),
        "overstock_risk_auto_items": sum(1 for i in auto if i["overstock_risk"]),
        "price_review_items": len(lists["price_review"]),
        "price_review_ending_items": sum(1 for x in lists["price_review"] if x["ending"]),
        "sell_off_items": len(lists["sell_off"]), "sell_off_qty": int(sum(x["stock"] for x in lists["sell_off"])),
        "ask_supplier_items": len(lists["ask_supplier"]), "ask_supplier_qty": int(sum(x["qty"] for x in lists["ask_supplier"])),
    }


def _pct(values) -> dict:
    v = np.asarray([x for x in values if x is not None], dtype=float)
    if not len(v):
        return {"n": 0, "p25": None, "median": None, "p90": None}
    q = np.percentile(v, [25, 50, 90])
    return {"n": int(len(v)), "p25": round(float(q[0]), 1), "median": round(float(q[1]), 1), "p90": round(float(q[2]), 1)}


def _lead_distribution(items, po) -> dict:
    """Распределение плеча по товарам (p25/медиана/p90) и по плановым заказам — для сверки с README."""
    def by(key):
        groups = {}
        for i in items:
            groups.setdefault(i[key] if i[key] is not None else "none", []).append(i["lead_time_days"])
        return {k: _pct(v) for k, v in sorted(groups.items())}

    return {
        "items": _pct([i["lead_time_days"] for i in items]),
        "orders_planned": _pct(po["planned"].tolist()),
        "by_source": by("lead_time_source"),
        "by_supply_type": by("supply_type"),
        "by_category": by("category"),
    }


def _accuracy(bt) -> dict:
    out = {"warning": ACCURACY_WARNING, "computed": bt is not None, "backtest_plan_date": None,
           "horizon_months": None, "categories": []}
    if bt is None:
        return out
    out["backtest_plan_date"] = bt.plan_date.isoformat()
    out["horizon_months"] = len(bt.months)
    for cat, r in bt.table.iterrows():
        def f(x):
            return None if not math.isfinite(float(x)) else round(float(x), 4)
        out["categories"].append({
            "category": cat, "skus": int(r["skus"]), "fact_units": round(float(r["fact"]), 1),
            "wape_model": f(r["wape_model"]), "wape_base": f(r["wape_base"]), "model_better": bool(r["model_better"]),
        })
    return out
