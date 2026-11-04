"""Бэктест: «сейчас август 2025» — прогноз на сент. 2025 – июль 2026 против факта.

Без подглядывания: набор данных урезается по дате (`plan_date`), модель видит
только месяцы строго раньше августа 2025. Факт берётся из той же таблицы продаж
уже после расчёта прогноза. Ошибка — WAPE = сумма |прогноз − факт| / сумма факта
по категории; база — простое среднее спроса за последние 12 месяцев.
"""

from dataclasses import dataclass, replace
from datetime import date

import pandas as pd

from .model import DEFAULT_PARAMS, Params, build_forecast, net_demand_frame

BACKTEST_DATE = date(2025, 8, 13)
BACKTEST_HORIZON = 11  # сент. 2025 … июль 2026


@dataclass
class BacktestResult:
    plan_date: date
    months: list[pd.Period]
    table: pd.DataFrame  # по категориям: skus, fact, wape_model, wape_base, model_better
    detail: pd.DataFrame  # sku_id, category, month, forecast, base, fact
    params: Params

    def wape(self, category: str, which: str = "model") -> float:
        return float(self.table.loc[category, f"wape_{which}"])

    def better(self) -> dict[str, bool]:
        return self.table["model_better"].to_dict()


def wape(forecast, fact) -> float:
    fact_sum = float(sum(fact))
    if fact_sum == 0:
        return float("nan")
    return float(sum(abs(f - a) for f, a in zip(forecast, fact)) / fact_sum)


def backtest(dataset, plan_date: date = BACKTEST_DATE, params: Params = DEFAULT_PARAMS, horizon: int = BACKTEST_HORIZON) -> BacktestResult:
    plan_month = pd.Period(plan_date, freq="M")
    # прогноз строится на копии набора, где «сейчас» — plan_date; модель сама отсекает всё от месяца плана
    ds = replace(dataset, plan_date=plan_date)
    fc = build_forecast(ds, plan_date, params)
    months = list(pd.period_range(plan_month + 1, periods=horizon, freq="M"))

    # база: среднее спроса за последние 12 месяцев до даты (месяцы товара)
    hist = net_demand_frame(ds, plan_month, params.subtract_returns)
    last12 = hist.reindex(columns=[plan_month - k for k in range(12, 0, -1)])
    base = last12.mean(axis=1, skipna=True)

    # факт: тот же показатель (продажи − возвраты) за будущие месяцы
    s = dataset.sales
    s = s[s["month"].isin(months)]
    qty = s["qty_sold"].astype("float64")
    if params.subtract_returns:
        qty = (qty - s["qty_returned"].astype("float64")).clip(lower=0)
    fact = s.assign(fact=qty).pivot_table(index="sku_id", columns="month", values="fact", aggfunc="sum")

    f = fc.monthly[fc.monthly["month"].isin(months)]
    det = f.rename(columns={"qty": "forecast"}).copy()
    det["base"] = det["sku_id"].map(base).fillna(0.0)
    fl = fact.reindex(index=det["sku_id"].unique(), columns=months).fillna(0.0).stack()
    fl.index.names = ["sku_id", "month"]
    det = det.merge(fl.rename("fact").reset_index(), on=["sku_id", "month"], how="left")
    det["fact"] = det["fact"].fillna(0.0)

    rows = {}
    for cat, g in det.groupby("category"):
        fa = g["fact"].sum()
        wm = (g["forecast"] - g["fact"]).abs().sum() / fa
        wb = (g["base"] - g["fact"]).abs().sum() / fa
        rows[cat] = {"skus": g["sku_id"].nunique(), "fact": float(fa), "wape_model": wm, "wape_base": wb, "model_better": bool(wm < wb)}
    table = pd.DataFrame(rows).T.astype({"skus": int, "fact": float, "wape_model": float, "wape_base": float, "model_better": bool})
    return BacktestResult(plan_date=plan_date, months=months, table=table, detail=det, params=params)


def format_backtest(res: BacktestResult) -> str:
    lines = [
        f"Бэктест: прогноз из {res.plan_date:%Y-%m} против факта {res.months[0]} … {res.months[-1]}",
        f"{'категория':<30}{'товаров':>8}{'факт, шт':>12}{'WAPE модели':>13}{'WAPE базы':>11}  лучше?",
    ]
    for cat, r in res.table.iterrows():
        lines.append(
            f"{cat:<30}{int(r['skus']):>8}{r['fact']:>12,.0f}{r['wape_model']:>12.1%}{r['wape_base']:>11.1%}  "
            f"{'да' if r['model_better'] else 'НЕТ'}".replace(",", " ")
        )
    return "\n".join(lines)
