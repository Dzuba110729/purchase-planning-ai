"""Модель прогноза: сезонный профиль категории × уровень товара за 12 месяцев.

Правила (все считаются ТОЛЬКО по месяцам строго раньше месяца плана — никакого
подглядывания в будущее; неполный месяц `partial_month = 1` тоже не берётся):

* Спрос = продажи минус возвраты (не ниже нуля).
* Упущенный спрос: если на 1-е число месяца остаток 0 (снимки есть с 2024-01),
  продажи этого месяца занижены. Месяц поднимается до «ожидаемого» уровня товара
  (годовой уровень по месяцам с остатком × доля месяца в профиле) × `lost_share`.
  Вниз не опускается, месяцы с остатком не трогаются.
* Сезонный профиль категории: доли 12 календарных месяцев в годовых окнах (окно
  заканчивается последним полным месяцем), усреднены по окнам с весом по объёму, слегка сглажены
  по кольцу; сумма = 100%.
* Уровень товара: сумма спроса за последние 12 месяцев (или за те, что есть)
  делится на сумму долей профиля за те же месяцы — это «годовой объём».
* Прогноз на месяц = годовой объём × доля месяца в профиле категории.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

HORIZON_MONTHS = 18  # от месяца плана: плечо до 180 дней + покрытие


@dataclass(frozen=True)
class Params:
    """Параметры модели. Выбраны по бэктесту, см. docs/backtest.md и отчёт таска."""

    window: int = 12  # месяцев в окне уровня
    lost_share: float = 0.5  # какую долю разрыва «ожидаемое − продано» считать упущенной
    smooth: float = 0.25 # сила сглаживания профиля по кольцу (0 — без сглаживания)
    cap_mult: float | None = 4.0  # ограничение выбросов: потолок = cap_mult × медиана ненулевых месяцев
    recency: float = 1.0  # вес окна профиля на год старше: recency ** (возраст в годах)
    correct_lost: bool = True
    min_instock_share: float = 0.3  # упущенный спрос считаем, только если месяцы с остатком покрывают >= этой доли сезона
    cap_lift_to_max: bool = True  # «ожидаемое» не выше лучшего реального месяца товара в окне
    recent_months: int = 3  # окно «свежих» продаж для поправки на затухание
    recent_weight: float = 1.0  # 0 — без поправки; w — уровень = (1-w)·L12 + w·min(L12, L_свежий)
    subtract_returns: bool = True


DEFAULT_PARAMS = Params()


def history_cutoff(dataset, plan_date=None) -> pd.Period:
    """Первый месяц, который НЕЛЬЗЯ использовать: месяц плана."""
    return pd.Period(plan_date if plan_date is not None else dataset.plan_date, freq="M")


def net_demand_frame(dataset, plan_month: pd.Period, subtract_returns: bool = True) -> pd.DataFrame:
    """Таблица sku × месяц: спрос (продажи − возвраты) за полные месяцы строго до месяца плана."""
    s = dataset.sales
    s = s[(s["partial_month"] != 1) & (s["month"] < plan_month)]
    qty = s["qty_sold"].astype("float64")
    if subtract_returns:
        qty = (qty - s["qty_returned"].astype("float64")).clip(lower=0)
    d = s.assign(demand=qty)
    return d.pivot_table(index="sku_id", columns="month", values="demand", aggfunc="sum")


def _category_of(dataset, skus) -> pd.Series:
    cat = dataset.sales.drop_duplicates("sku_id").set_index("sku_id")["category"]
    return cat.reindex(skus)


def _month_index(plan_month: pd.Period, n_back: int) -> list[pd.Period]:
    return [plan_month - k for k in range(n_back, 0, -1)]


def _profile_from_totals(total: pd.Series, last: pd.Period, p: Params) -> pd.Series:
    """Профиль 12 календарных месяцев по ряду месячных сумм категории."""
    shares, weights = [], []
    age = 0
    while True:
        months = [last - k for k in range(11 + 12 * age, -1 + 12 * age, -1)]
        if months[0] < total.index.min():
            break
        block = total.reindex(months).fillna(0.0)
        if block.sum() > 0:
            sh = pd.Series(block.values / block.sum(), index=[m.month for m in months])
            shares.append(sh.sort_index())
            weights.append(float(block.sum()) * p.recency**age)  # вес окна = его объём: крошечные годы не шумят
        age += 1
    if not shares:
        return pd.Series(1 / 12, index=range(1, 13))
    prof = sum(w * s for w, s in zip(weights, shares)) / sum(weights)
    if p.smooth > 0:
        arr = prof.sort_index().values
        sm = (np.roll(arr, 1) + 2 * arr + np.roll(arr, -1)) / 4
        arr = (1 - p.smooth) * arr + p.smooth * sm
        prof = pd.Series(arr, index=range(1, 13))
    return prof / prof.sum()


def _winsorize(D: pd.DataFrame, cap_mult: float | None) -> pd.DataFrame:
    if cap_mult is None:
        return D
    med = D.where(D > 0).median(axis=1)
    cap = (med * cap_mult).fillna(np.inf)
    return D.clip(upper=cap, axis=0)


def _levels(D: pd.DataFrame, profile_by_sku: pd.DataFrame, months: list[pd.Period], mask=None, with_den: bool = False):
    """Годовой объём: сумма спроса / сумма долей профиля по месяцам окна (только месяцы товара)."""
    cols = [m for m in months if m in D.columns]
    vals = D.reindex(columns=cols)
    share = profile_by_sku.reindex(columns=[m.month for m in cols])
    share.columns = cols
    present = vals.notna()
    if mask is not None:
        present &= mask.reindex(index=vals.index, columns=cols).fillna(True)
    num = vals.where(present).sum(axis=1)
    den = share.where(present).sum(axis=1)
    lvl = (num / den.where(den > 0)).fillna(0.0)
    return (lvl, den) if with_den else lvl


def fit(dataset, plan_date=None, params: Params = DEFAULT_PARAMS):
    """Считает всё, что нужно прогнозу. Возвращает dict с профилем, уровнями и исправленным спросом."""
    plan_month = history_cutoff(dataset, plan_date)
    D = net_demand_frame(dataset, plan_month, params.subtract_returns)
    if D.empty:
        raise ValueError("нет истории продаж до даты плана")
    last = plan_month - 1
    window = [last - k for k in range(params.window - 1, -1, -1)]
    cat = _category_of(dataset, D.index)

    # «живой на дату»: в окне были продажи с проставленной наценкой (как в справочнике: пусто = не продавался);
    # считается по самим продажам до даты — справочник не нужен
    sl = dataset.sales
    sl = sl[(sl["partial_month"] != 1) & sl["month"].isin(window) & (sl["qty_sold"] > 0) & sl["markup_pct"].notna()]
    live = pd.Series(D.index.isin(sl["sku_id"].unique()), index=D.index)

    # остатки: известны с первого снимка; True = на 1-е число остаток 0
    st = dataset.stock
    st = st[st["month"] < plan_month]
    if st.empty:
        zero = pd.DataFrame(False, index=D.index, columns=D.columns)
    else:
        zero = st.assign(z=st["stock_qty"] == 0).pivot_table(index="sku_id", columns="month", values="z", aggfunc="max")
        zero = zero.reindex(index=D.index, columns=D.columns).astype("float64").fillna(0.0).astype(bool)
    zero &= D.notna()

    def profiles(Dx):
        out = {}
        for c in cat.dropna().unique():
            tot = Dx.loc[cat == c].sum(axis=0)
            out[c] = _profile_from_totals(tot, last, params)
        return pd.DataFrame(out).T  # категория × календарный месяц 1..12

    prof = profiles(D)
    adjusted = D.copy()
    lost_flag = pd.DataFrame(False, index=D.index, columns=D.columns)
    if params.correct_lost and zero.any().any():
        for _ in range(2):  # профиль ↔ коррекция: две итерации
            sku_prof = prof.reindex(cat.values).set_index(D.index)
            lvl_stock, den_stock = _levels(D, sku_prof, window, mask=~zero, with_den=True)
            lvl_stock = lvl_stock.where(den_stock >= params.min_instock_share, 0.0)
            expected = _expected(lvl_stock, sku_prof, D.columns)
            if params.cap_lift_to_max:
                expected = expected.clip(upper=in_window_max(D, window), axis=0)
            zero_in_window = zero.copy()
            zero_in_window.loc[:, [c for c in D.columns if c not in window]] = False
            lift = (expected - D).clip(lower=0) * params.lost_share
            ok = zero_in_window & (lvl_stock > 0).values[:, None]
            adjusted = D + lift.where(ok, 0.0)
            lost_flag = ok & (lift > 0)
            prof = profiles(adjusted)
    sku_prof = prof.reindex(cat.values).set_index(D.index)
    capped = _winsorize(adjusted, params.cap_mult)  # потолок — только для уровня, профиль считается по живым пикам
    level = _levels(capped, sku_prof, window)
    if params.recent_weight > 0:
        recent = window[-params.recent_months:]
        lvl_recent = _levels(capped, sku_prof, recent)
        level = (1 - params.recent_weight) * level + params.recent_weight * np.minimum(level, lvl_recent)
    return {
        "plan_month": plan_month, "profile": prof, "level": level, "category": cat, "live": live,
        "demand": D, "adjusted": adjusted, "lost_flag": lost_flag, "zero_stock": zero,
    }


def in_window_max(D: pd.DataFrame, window) -> pd.Series:
    return D.reindex(columns=[m for m in window if m in D.columns]).max(axis=1).fillna(0.0)


def _expected(level: pd.Series, sku_prof: pd.DataFrame, months) -> pd.DataFrame:
    sh = sku_prof.reindex(columns=[m.month for m in months])
    sh.columns = list(months)
    return sh.mul(level, axis=0)


@dataclass
class Forecast:
    """Прогноз по живым товарам: `monthly` — sku_id, category, month, qty (шт., не округлено)."""

    plan_date: object
    plan_month: pd.Period
    monthly: pd.DataFrame
    profile: pd.DataFrame  # категория × месяц 1..12, строки в сумме = 1
    level: pd.Series  # годовой объём по товару
    adjusted_history: pd.DataFrame  # sku × месяц: спрос после исправления
    lost_months: pd.DataFrame  # sku × месяц: True там, где добавлен упущенный спрос
    params: Params

    def for_sku(self, sku_id: str) -> pd.Series:
        m = self.monthly[self.monthly["sku_id"] == sku_id]
        return m.set_index("month")["qty"]

    def sum_months(self, sku_id: str, start: pd.Period, n: int) -> float:
        s = self.for_sku(sku_id)
        return float(s.reindex(pd.period_range(start, periods=n, freq="M")).fillna(0).sum())


def build_forecast(dataset, plan_date=None, params: Params = DEFAULT_PARAMS, horizon: int = HORIZON_MONTHS) -> Forecast:
    """Прогноз спроса по месяцам для каждого живого товара, начиная с месяца плана."""
    pdate = plan_date if plan_date is not None else dataset.plan_date
    f = fit(dataset, pdate, params)
    pm = f["plan_month"]
    months = pd.period_range(pm, periods=horizon, freq="M")
    skus = f["level"].index[f["live"].values]
    prof = f["profile"]
    cat = f["category"].loc[skus]
    rows = []
    for m in months:
        qty = f["level"].loc[skus].values * prof.reindex(cat.values)[m.month].values
        rows.append(pd.DataFrame({"sku_id": skus, "category": cat.values, "month": m, "qty": qty}))
    monthly = pd.concat(rows, ignore_index=True)
    return Forecast(
        plan_date=pdate, plan_month=pm, monthly=monthly, profile=prof, level=f["level"].loc[skus],
        adjusted_history=f["adjusted"], lost_months=f["lost_flag"], params=params,
    )
