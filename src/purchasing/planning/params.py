"""Параметры расчёта закупки. Все границы из брифа — здесь, чтобы попасть в facts.json."""

from statistics import NormalDist

COVER_MIN_DAYS = 60
COVER_MAX_DAYS = 120
OVERDUE_MAX_DAYS = 90  # просрочка до этого числа дней включительно — «в пути»; больше — «уточнить у поставщика»
OVERSTOCK_DAYS = 365  # 12 месяцев
DEFAULT_BATCH_STEP = 10
ABC_BOUNDS = {"A": 0.80, "B": 0.95}  # накопленная доля важности; остальное — C
SERVICE_LEVELS = {"A": 0.95, "B": 0.90, "C": 0.80}
Z_VALUES = {g: NormalDist().inv_cdf(p) for g, p in SERVICE_LEVELS.items()}
MIN_ORDERS_FOR_SUPPLIER = 3  # меньше завершённых заказов с приходом — по поставщику плечо не считаем
MIN_RECEIPT_DATES_FOR_COVER = 3  # меньше дат прихода (2 интервала) — ритм поставщика не считаем
LEAD_MAX_DAYS = 400  # приход позже этого после заказа не считаем приходом этого заказа
SIGMA_WINDOW_MONTHS = 12
NEW_ITEM_MONTHS = 6  # меньше полных месяцев истории — разброс спроса упрощённый
NEW_ITEM_CV = 0.5  # упрощённый разброс: 50% от среднемесячного спроса
SIGMA_OUTLIER_CAP_MULT = 4.0  # потолок месячных продаж при расчёте σ: 4 × медиана ненулевых месяцев (как в прогнозе)
SAFETY_CAP = "demand_lead_cover"  # страховой запас не больше спроса за плечо + покрытие
FALLBACK_WINDOW_DAYS = 90  # окно спроса и порог «заканчивается», если плечо или покрытие неизвестны

PARAMS_FOR_FACTS = {
    "cover_min_days": COVER_MIN_DAYS,
    "cover_max_days": COVER_MAX_DAYS,
    "overdue_max_days": OVERDUE_MAX_DAYS,
    "overstock_days": OVERSTOCK_DAYS,
    "overstock_months": 12,
    "default_batch_step": DEFAULT_BATCH_STEP,
    "abc_bounds": ABC_BOUNDS,
    "service_levels": SERVICE_LEVELS,
    "z_values": {g: round(z, 4) for g, z in Z_VALUES.items()},
    "min_markup_pct": 25,
    "min_orders_for_supplier_lead": MIN_ORDERS_FOR_SUPPLIER,
    "min_receipt_dates_for_cover": MIN_RECEIPT_DATES_FOR_COVER,
    "lead_max_days": LEAD_MAX_DAYS,
    "sigma_window_months": SIGMA_WINDOW_MONTHS,
    "new_item_months": NEW_ITEM_MONTHS,
    "new_item_cv": NEW_ITEM_CV,
    "sigma_outlier_cap_mult": SIGMA_OUTLIER_CAP_MULT,
    "safety_cap": SAFETY_CAP,
    "fallback_window_days": FALLBACK_WINDOW_DAYS,
}
