"""Графики «прогноз против факта»: по одному товару на категорию, только коды и числа."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from .backtest import BacktestResult  # noqa: E402
from .model import net_demand_frame  # noqa: E402

# Контрастные цвета, различимы и в чёрно-белой печати (разные линии и маркеры)
C_HIST, C_FACT, C_MODEL, C_BASE = "#6b7280", "#111827", "#1d4ed8", "#dc2626"
HISTORY_MONTHS = 24


def pick_sku(res: BacktestResult, category: str) -> str:
    """Типичный товар категории: медианный по факту среди тех, что продавались в проверяемом периоде."""
    g = res.detail[res.detail["category"] == category].groupby("sku_id")["fact"].sum()
    g = g[g > 0].sort_values(kind="stable")
    return g.index[len(g) // 2]


def plot_sku(res: BacktestResult, dataset, sku_id: str, path: Path) -> None:
    d = res.detail[res.detail["sku_id"] == sku_id].sort_values("month")
    category = d["category"].iloc[0]
    plan_month = pd.Period(res.plan_date, freq="M")
    hist = net_demand_frame(dataset, plan_month, res.params.subtract_returns).loc[sku_id].dropna()
    hist = hist.iloc[-HISTORY_MONTHS:]
    wape_m = (d["forecast"] - d["fact"]).abs().sum() / d["fact"].sum()
    wape_b = (d["base"] - d["fact"]).abs().sum() / d["fact"].sum()

    fig, ax = plt.subplots(figsize=(10, 5.8), dpi=110)
    ax.plot([p.to_timestamp() for p in hist.index], hist.values, color=C_HIST, marker="o", ms=3, lw=1.5, label="История до августа 2025")
    x = [p.to_timestamp() for p in d["month"]]
    ax.plot(x, d["fact"], color=C_FACT, marker="s", ms=4, lw=2.2, label="Факт")
    ax.plot(x, d["forecast"], color=C_MODEL, marker="^", ms=5, lw=2.2, ls="--", label=f"Прогноз модели (WAPE по товару {wape_m:.0%})")
    ax.plot(x, d["base"], color=C_BASE, lw=2, ls=":", label=f"База: среднее за 12 мес. (WAPE по товару {wape_b:.0%})")
    ax.axvline(plan_month.to_timestamp(), color="#9ca3af", lw=1)
    ax.set_title(f"Прогноз против факта. Категория {category}, товар {sku_id}\nПрогноз сделан в августе 2025 по данным до этой даты", fontsize=12)
    ax.set_xlabel("Месяц")
    ax.set_ylabel("Штук в месяц (продажи минус возвраты)")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=2, fontsize=9, frameon=False)
    fig.autofmt_xdate()
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def plot_backtest(res: BacktestResult, dataset, out_dir) -> dict[str, tuple[str, Path]]:
    """По одному графику на категорию: docs/backtest_<категория>.png. Возвращает {категория: (sku, путь)}."""
    out = {}
    for category in res.table.index:
        sku = pick_sku(res, category)
        path = Path(out_dir) / f"backtest_{category}.png"
        plot_sku(res, dataset, sku, path)
        out[category] = (sku, path)
    return out
