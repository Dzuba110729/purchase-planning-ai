"""Набор данных: загрузка, восстановление остатков, разметка, открытые заказы.

Определения (все решены здесь, чтобы следующие этапы не гадали):

* Дата плана (`plan_date`). Порядок: явный аргумент; переменная PLAN_DATE
  (YYYY-MM-DD); иначе 2026-08-13 — ДАТА ВЗЯТА ИЗ README ЗАКАЗЧИКА («данные на
  13-е число»), а не из самих данных: дня выгрузки в CSV нет (максимальная
  дата прихода — 2026-08-01). Это дефолт применяется, если неполный месяц
  выгрузки (`partial_month = 1`) — 2026-08 (`plan_date_source` =
  "readme_default"). При новых данных дата сдвигается сама
  (`plan_date_source` = "derived"): берём самую позднюю дату в
  `receipts`/`purchase_orders.order_date`, но не раньше 1-го числа неполного
  месяца и не позже его последнего дня. Если неполного месяца нет (выгрузка
  закрытым месяцем) — последний день последнего месяца продаж.
  Месяц плана = месяц `plan_date`.
* Неполный месяц: строки `sales_monthly` с `partial_month = 1`. В средние не
  входят: `full_months_sales()` и `mean_monthly_sales()` их отбрасывают.
  Среднее делится на число полных месяцев окна SKU (от `first_sale_month` до
  последнего полного месяца), а не на число имеющихся строк: пропущенный
  месяц считается нулём продаж.
* Остатки: «нет строки в stock_monthly = остаток 0». Плотная таблица
  `Dataset.stock` — каждый SKU из sku_dim на каждый месяц от 2024-01 (первый
  месяц снимков; берётся из данных) по месяц плана включительно; недостающие
  строки = 0, колонка `restored` = True у восстановленных. Для всех SKU, а не
  только живых: нулевой остаток мёртвого товара — тоже факт. На этих данных у
  139 из 413 живых остаток на месяц плана 0.
* Разметка SKU (`Dataset.sku_flags`): `status` = «dead», если
  `avg_markup_pct_12m` пусто (не продавался последние 12 мес), иначе «live».
  `low_markup` = True у живого с наценкой < MIN_MARKUP_PCT (=25). Низкая
  наценка — подмножество живых (live = 413, из них low_markup = 68), мёртвые
  low_markup не получают.
* Списки: `retire_list()` — все мёртвые; `sell_off_list()` — мёртвые с
  остатком > 0 на месяц плана («распродать / вывести»); `price_review_list()`
  — живые с низкой наценкой (в автозакупку не идут);
  `auto_purchase_skus()` — живые без низкой наценки.
* Открытые заказы: `status = open`. «В пути» — promised_date >= plan_date,
  «просрочен» — promised_date < plan_date. `in_transit_qty` = сумма
  qty_pending по заказам в пути; `overdue_qty` — по просроченным.
"""

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from .errors import DataError
from .loader import read_file
from .schema import FILES

MIN_MARKUP_PCT = 25
DEFAULT_PLAN_DATE = date(2026, 8, 13)
DEFAULT_DATA_DIR = "share"


def _parse_date(value, what: str) -> date:
    try:
        ts = pd.Timestamp(value)
        if pd.isna(ts):
            raise ValueError
        return ts.date()
    except (ValueError, TypeError):
        raise DataError(f"{what}: не дата YYYY-MM-DD") from None


def resolve_data_dir(data_dir=None) -> Path:
    return Path(data_dir or os.environ.get("DATA_DIR") or DEFAULT_DATA_DIR)


def _derive_plan_date(sales, orders, receipts) -> tuple[date, str]:
    partial = sales.loc[sales["partial_month"] == 1, "month"]
    if partial.empty:
        return sales["month"].max().end_time.date(), "derived"
    pm = partial.max()
    if pm.start_time.date() <= DEFAULT_PLAN_DATE <= pm.end_time.date():
        return DEFAULT_PLAN_DATE, "readme_default"
    latest = max(receipts["receipt_date"].max(), orders["order_date"].max()).date()
    return min(max(latest, pm.start_time.date()), pm.end_time.date()), "derived"


def _restore_stock(stock: pd.DataFrame, sku_ids, plan_month: pd.Period) -> pd.DataFrame:
    first = stock["month"].min()
    months = pd.period_range(first, plan_month, freq="M")
    grid = pd.MultiIndex.from_product([sorted(sku_ids), months], names=["sku_id", "month"]).to_frame(index=False)
    grid = grid.merge(stock[["sku_id", "month", "stock_qty"]], how="left", on=["sku_id", "month"])
    grid["restored"] = grid["stock_qty"].isna()
    grid["stock_qty"] = grid["stock_qty"].fillna(0).astype("int64")
    return grid


@dataclass
class Dataset:
    sku: pd.DataFrame
    sales: pd.DataFrame
    stock: pd.DataFrame  # плотный календарь, см. докстринг модуля
    orders: pd.DataFrame
    receipts: pd.DataFrame
    suppliers: pd.DataFrame
    plan_date: date
    raw_stock_rows: int = 0
    plan_date_source: str = "param"  # param / env / readme_default / derived

    @property
    def plan_month(self) -> pd.Period:
        return pd.Period(self.plan_date, freq="M")

    # --- разметка SKU ---
    @property
    def sku_flags(self) -> pd.DataFrame:
        f = self.sku[["sku_id", "avg_markup_pct_12m"]].copy()
        f["status"] = f["avg_markup_pct_12m"].isna().map({True: "dead", False: "live"})
        f["low_markup"] = (f["status"] == "live") & (f["avg_markup_pct_12m"] < MIN_MARKUP_PCT)
        return f.drop(columns="avg_markup_pct_12m")

    def _ids(self, mask) -> list[str]:
        return self.sku_flags.loc[mask(self.sku_flags), "sku_id"].tolist()

    @property
    def live_skus(self) -> list[str]:
        return self._ids(lambda f: f["status"] == "live")

    @property
    def dead_skus(self) -> list[str]:
        return self._ids(lambda f: f["status"] == "dead")

    @property
    def low_markup_skus(self) -> list[str]:
        return self._ids(lambda f: f["low_markup"])

    def retire_list(self) -> list[str]:
        """Вывести из ассортимента: все мёртвые."""
        return self.dead_skus

    def sell_off_list(self) -> list[str]:
        """Распродать / вывести: мёртвые с остатком > 0 на месяц плана."""
        return sorted(set(self.dead_skus) & set(self.skus_with_stock()))

    def price_review_list(self) -> list[str]:
        """Пересмотр цены: живые с наценкой < MIN_MARKUP_PCT; в автозакупку не идут."""
        return self.low_markup_skus

    def auto_purchase_skus(self) -> list[str]:
        """Кандидаты в автозакупку: живые без низкой наценки."""
        return self._ids(lambda f: (f["status"] == "live") & ~f["low_markup"])

    # --- остатки ---
    def stock_at(self, month=None) -> pd.Series:
        m = pd.Period(month, freq="M") if month is not None else self.plan_month
        s = self.stock[self.stock["month"] == m]
        return s.set_index("sku_id")["stock_qty"]

    def skus_with_stock(self) -> list[str]:
        s = self.stock_at()
        return s[s > 0].index.tolist()

    def zero_stock_live_skus(self) -> list[str]:
        s = self.stock_at()
        return [k for k in self.live_skus if s.get(k, 0) == 0]

    # --- открытые заказы ---
    @property
    def open_orders(self) -> pd.DataFrame:
        return self.orders[self.orders["status"] == "open"]

    @property
    def in_transit_orders(self) -> pd.DataFrame:
        o = self.open_orders
        return o[o["promised_date"] >= pd.Timestamp(self.plan_date)]

    @property
    def overdue_orders(self) -> pd.DataFrame:
        o = self.open_orders
        return o[o["promised_date"] < pd.Timestamp(self.plan_date)]

    @property
    def in_transit_qty(self) -> int:
        return int(self.in_transit_orders["qty_pending"].sum())

    @property
    def overdue_qty(self) -> int:
        return int(self.overdue_orders["qty_pending"].sum())

    # --- полные месяцы ---
    def full_months_sales(self) -> pd.DataFrame:
        """Продажи без неполного месяца (и без месяцев позже месяца плана)."""
        s = self.sales
        return s[(s["partial_month"] != 1) & (s["month"] < self.plan_month)]

    def mean_monthly_sales(self, last_n_months: int | None = None) -> pd.Series:
        """Среднемесячные продажи по SKU за ПОЛНЫЕ месяцы окна (август не входит).

        Окно SKU — от его first_sale_month (не раньше последних last_n_months)
        до месяца перед месяцем плана. Сумма продаж делится на число месяцев
        окна, а не на число строк: отсутствующая строка = 0 продаж.
        """
        s = self.full_months_sales()
        last = self.plan_month - 1
        first = self.sku.set_index("sku_id")["first_sale_month"]
        if last_n_months:
            first = first.where(first > last - last_n_months, last - last_n_months + 1)
        start = first.rename("start").reset_index()
        m = s.merge(start, on="sku_id")
        m = m[m["month"] >= m["start"]]
        total = m.groupby("sku_id")["qty_sold"].sum()
        n = start.set_index("sku_id")["start"].map(lambda p: max((last - p).n + 1, 0))
        n = n[n > 0]
        return (total.reindex(n.index).fillna(0) / n).rename("mean_qty_sold")


def load_dataset(data_dir=None, plan_date: date | str | None = None) -> Dataset:
    """Читает 6 CSV из папки (аргумент → DATA_DIR → share/); дата плана — см. докстринг модуля."""
    d = resolve_data_dir(data_dir)
    if not d.is_dir():
        raise DataError(f"папка данных не найдена: {d}")
    t = {name: read_file(d, spec) for name, spec in FILES.items()}
    env = os.environ.get("PLAN_DATE")
    if plan_date is not None:
        pdate, source = _parse_date(plan_date, "дата плана (аргумент)"), "param"
    elif env:
        pdate, source = _parse_date(env, "дата плана (переменная PLAN_DATE)"), "env"
    else:
        pdate, source = _derive_plan_date(t["sales_monthly"], t["purchase_orders"], t["receipts"])
    stock = _restore_stock(t["stock_monthly"], t["sku_dim"]["sku_id"].unique(), pd.Period(pdate, freq="M"))
    return Dataset(
        sku=t["sku_dim"], sales=t["sales_monthly"], stock=stock, orders=t["purchase_orders"],
        receipts=t["receipts"], suppliers=t["supplier_dim"], plan_date=pdate,
        raw_stock_rows=len(t["stock_monthly"]), plan_date_source=source,
    )
