"""facts.json — единый источник цифр для README, ИИ, дашборда и видео.

Структура (ключи английские, русский — только в текстовых полях):
schema_version, plan_date, plan_date_source, generated_from{счётчики строк}, params{границы},
totals{итоги}, lead_time_distribution{items, orders_planned, by_source, by_supply_type, by_category: n/p25/median/p90},
items[по живому товару, включая monthly_forecast], lists{price_review, discontinue, sell_off, ask_supplier},
forecast_accuracy{warning, computed, backtest_plan_date, horizon_months, categories[WAPE]}.
Пустое значение — `null`; причина — в `*_note` или в `missing[]` товара. NaN/inf не пишутся.
"""

import json
import math
from datetime import date
from pathlib import Path

import numpy as np

FACTS_SCHEMA_VERSION = 1


def _clean(x):
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        v = float(x)
        return v if math.isfinite(v) else None
    if isinstance(x, date):
        return x.isoformat()
    return x


def plan_to_facts(plan) -> dict:
    return _clean({
        "schema_version": FACTS_SCHEMA_VERSION,
        "plan_date": plan.plan_date,
        "plan_date_source": plan.plan_date_source,
        "generated_from": plan.generated_from,
        "params": plan.params,
        "totals": plan.totals,
        "items": plan.items,
        "lists": plan.lists,
        "lead_time_distribution": plan.lead_time_distribution,
        "forecast_accuracy": plan.forecast_accuracy,
    })


def write_facts(plan, path) -> Path:
    """Пишет facts.json (UTF-8, без NaN); создаёт папку. Возвращает путь."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(plan_to_facts(plan), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return p
