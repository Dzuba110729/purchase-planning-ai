"""Расчёт закупки на придуманных товарах: ответ известен заранее, посчитан руками.

Общие условия (мир из conftest): дата плана 2026-01-01; поставщик SUP1: плечо 60 дней
(3 заказа, приход ровно через 60 дней), приходы раз в 90 дней -> покрытие 90 дней;
шаг партии S1 = НОД(50, 100, 150) = 50; продажи ровные (30/мес) -> разброс 0, страховой запас 0;
один товар -> группа A. Спрос за период «плечо + покрытие» = 150 дней.
"""

from datetime import date

import pytest

from purchasing.planning import build_plan

from planning_helpers import PLAN, history_orders, make_forecast, open_order, season_rates


def item(plan, sku="S1"):
    return plan.item(sku)


def test_flat_demand_zero_risk(world):
    # 1 шт/день; остаток 200. Цель = 150 + 0; нужно max(0, 150-200) = 0.
    # Запас 200 дней. Уровень «спрос за плечо» = 60. Остаток 200-d >= 60 до d=140,
    # на d=141 уже 59 < 60 -> заказ на день 140 = 1 января + 140 = 21 мая 2026.
    # Кончится: на конец дня 200 остаток -1 -> день 200. Плечо 60 -> дефицита нет.
    ds, fc = world(200, {"S1": 1.0})
    it = item(build_plan(ds, fc))
    assert (it["lead_time_days"], it["cover_days"], it["batch_step"]) == (60, 90, 50)
    assert it["abc_group"] == "A"
    assert it["demand_lead_cover"] == pytest.approx(150)
    assert it["safety_stock"] == 0
    assert it["target_stock"] == pytest.approx(150)
    assert it["stock_days"] == pytest.approx(200)
    assert it["order_qty"] == 0
    assert it["next_order_date"] == "2026-05-21"
    assert it["runout_days"] == 200
    assert (it["shortage_risk"], it["unavoidable_shortage"], it["overstock_risk"]) == (False, False, False)


def test_flat_demand_low_stock_orders_now(world):
    # остаток 40: нужно 150 - 40 = 110 -> до шага 50 вверх = 150.
    # Кончится через 40 дней, плечо 60 > 40 -> дефицит неизбежен, заказ сегодня.
    ds, fc = world(40, {"S1": 1.0})
    it = item(build_plan(ds, fc))
    assert it["order_qty"] == 150
    assert it["next_order_date"] == "2026-01-01" and it["order_now"] is True
    assert it["runout_days"] == 40 and it["runout_date"] == "2026-02-10"
    assert it["stock_days"] == pytest.approx(40)
    assert it["unavoidable_shortage"] is True and it["shortage_risk"] is True


def test_seasonal_demand_uses_months_with_shares(world):
    # Январь-март 1 шт/день (90 дней), с 1 апреля (день 90) 3 шт/день.
    # Спрос за 150 дней: 90*1 + 60*3 = 270. Остаток 100 -> нужно 170 -> до шага 50 = 200.
    # (по среднему спросу 150/150=1 вышло бы 50->50: сезонность важна.)
    # Порог заказа при d>=31: 2d, остаток 100-d; 100-d < 2d при d=34 -> заказ в день 33 = 3 февраля.
    # Остаток кончится: после 90 дней остаётся 10, затем -3/день: конец дня 93 -> -2 -> день 93 = 4 апреля.
    ds, fc = world(100, {"S1": season_rates()})
    it = item(build_plan(ds, fc))
    assert it["demand_lead_cover"] == pytest.approx(270)
    assert it["order_qty"] == 200
    assert it["next_order_date"] == "2026-02-03"
    assert it["runout_days"] == 93 and it["runout_date"] == "2026-04-04"
    assert it["unavoidable_shortage"] is False


def test_order_in_transit_counts_toward_stock(world):
    # остаток 40, в пути 100 (придёт 20 февраля = день 50). Цель 150 -> нужно 150-40-100 = 10 -> 50.
    # Запас в днях = (40+100)/1 = 140. До прихода остатка хватит на 40 дней < плечо 60 -> дефицит.
    ds, fc = world(40, {"S1": 1.0}, extra_orders=[open_order("PO-1", "S1", "SUP1", date(2026, 2, 20), 100)])
    it = item(build_plan(ds, fc))
    assert it["in_transit_qty"] == 100
    assert it["stock_days"] == pytest.approx(140)
    assert it["order_qty"] == 50
    assert it["unavoidable_shortage"] is True


def test_overdue_up_to_90_days_is_in_transit_shifted_by_usual_delay(world):
    # История поставщика: плановое плечо 60, пришло через 100 -> обычное опоздание 40 дней.
    # Просрочка 17 дней (обещано 15.12.2025), 80 шт -> в пути, придёт 15.12 + 40 = 24.01.2026 (день 23).
    # Остаток 10: кончится в день 10, до прихода -> дефицит, хотя «на бумаге» заказ уже должен был прийти.
    hist = history_orders("SUP1", "S1", lead=100, promised_lead=60)
    ds, fc = world(10, {"S1": 1.0}, history=hist,
                   extra_orders=[open_order("PO-2", "S1", "SUP1", date(2025, 12, 15), 80)])
    plan = build_plan(ds, fc)
    it = item(plan)
    assert it["lead_time_days"] == 60
    assert it["in_transit_qty"] == 80 and it["overdue_included_qty"] == 80 and it["overdue_excluded_qty"] == 0
    assert it["stock_days"] == pytest.approx(90)
    assert it["runout_days"] == 10 and it["unavoidable_shortage"] is True
    assert plan.to_facts()["lists"]["ask_supplier"] == []


def test_overdue_over_90_days_goes_to_ask_supplier_list(world):
    # обещано 01.09.2025: просрочка 122 дня -> не в запасе, в список «уточнить у поставщика».
    ds, fc = world(40, {"S1": 1.0}, extra_orders=[open_order("PO-3", "S1", "SUP1", date(2025, 9, 1), 70)])
    plan = build_plan(ds, fc)
    it = item(plan)
    assert it["in_transit_qty"] == 0 and it["overdue_excluded_qty"] == 70
    assert it["stock_days"] == pytest.approx(40)
    ask = plan.to_facts()["lists"]["ask_supplier"]
    assert ask == [{"sku_id": "S1", "po_id": "PO-3", "supplier_id": "SUP1", "qty": 70,
                    "days_overdue": 122, "promised_date": "2025-09-01"}]
    assert plan.to_facts()["totals"]["ask_supplier_items"] == 1


@pytest.mark.parametrize("promised,included", [(date(2025, 10, 3), True), (date(2025, 10, 2), False)])
def test_overdue_boundary_is_90_days_inclusive(world, promised, included):
    # 03.10.2025 -> ровно 90 дней просрочки (в пути); 02.10.2025 -> 91 день (не считаем).
    ds, fc = world(40, {"S1": 1.0}, extra_orders=[open_order("PO-4", "S1", "SUP1", promised, 25)])
    it = item(build_plan(ds, fc))
    assert it["in_transit_qty"] == (25 if included else 0)
    assert it["overdue_excluded_qty"] == (0 if included else 25)


def test_zero_stock(world):
    # остаток 0: кончился уже сегодня (день 0), заказ сегодня на всю цель: 150 -> кратно 50 = 150.
    ds, fc = world(0, {"S1": 1.0})
    it = item(build_plan(ds, fc))
    assert it["stock_days"] == 0 and it["runout_days"] == 0 and it["runout_date"] == "2026-01-01"
    assert it["order_qty"] == 150 and it["order_now"] is True
    assert it["unavoidable_shortage"] is True


def test_new_item_uses_category_lead_default_step_and_simplified_safety(world):
    # NEW: одна запись продаж (дек. 2025, 30 шт), нет поставщика, supply_type=unknown, нет заказов.
    # Плечо = медиана по категории c = 60; покрытие = медиана по всем поставщикам = 90; шаг по умолчанию 10.
    # Важность S1 = 30*5*40 = 6000, NEW = 30*1*40 = 1200; доля до NEW = 6000/7200 = 0.833 -> B (z=1.2816).
    # Мало истории -> разброс = 50% от 30 = 15; запас = 1.2816*15*sqrt(60/30) = 27.19.
    # Цель 150 + 27.19 = 177.19; остаток 20 -> 157.19 -> до шага 10 вверх = 160.
    ds, fc = world(
        100, {"S1": 1.0, "NEW": 1.0}, monthly_sales={"NEW": [30]},
        extra_skus=[{"sku_id": "NEW", "supplier_id": None, "supply_type": "unknown", "decile": 1.0,
                     "first_sale_month": "2025-12"}],
        extra_stock={"NEW": 20},
    )
    it = item(build_plan(ds, fc), "NEW")
    assert (it["lead_time_days"], it["lead_time_source"]) == (60, "category")
    assert (it["cover_days"], it["cover_source"]) == (90, "all_suppliers")
    assert (it["batch_step"], it["batch_step_source"]) == (10, "default")
    assert it["abc_group"] == "B"
    assert it["safety_stock"] == pytest.approx(27.19, abs=0.02)
    assert "мало истории" in it["safety_note"]
    assert it["order_qty"] == 160
    assert it["supplier_id"] is None


def test_safety_stock_from_demand_spread(world):
    # Продажи за 12 мес.: 6 раз по 20 и 6 раз по 40 -> среднее 30, отклонения ±10,
    # выборочное стд = sqrt(12*100/11) = 10.4447. Группа A, z = 1.6449.
    # Запас = 1.6449 * 10.4447 * sqrt(60/30) = 24.30.
    ds, fc = world(500, {"S1": 1.0}, monthly_sales={"S1": [20] * 6 + [40] * 6})
    it = item(build_plan(ds, fc))
    assert it["service_level"] == 0.95
    assert it["safety_stock"] == pytest.approx(24.30, abs=0.05)
    assert it["target_stock"] == pytest.approx(150 + 24.30, abs=0.05)


def test_abc_groups_by_cumulative_importance(build):
    # важность = среднемесячные продажи * дециль * наценка: X=40*5*40=8000, Y=15*5*20=1500, Z=5*5*20=500; всего 10000.
    # Доля до товара: X 0 -> A; Y 0.80 (не < 0.80) -> B; Z 0.95 (не < 0.95) -> C.
    skus = [
        {"sku_id": "X", "markup": 40.0}, {"sku_id": "Y", "markup": 20.0}, {"sku_id": "Z", "markup": 20.0},
    ]
    ds = build(skus, stock={"X": 10, "Y": 10, "Z": 10},
               monthly_sales={"X": [40] * 12, "Y": [15] * 12, "Z": [5] * 12})
    plan = build_plan(ds, make_forecast({"X": 1.0, "Y": 1.0, "Z": 1.0}))
    assert [plan.item(s)["abc_group"] for s in "XYZ"] == ["A", "B", "C"]
    assert [plan.item(s)["service_level"] for s in "XYZ"] == [0.95, 0.90, 0.80]


def test_cover_is_clamped_to_60_120(world):
    # приходы раз в 200 дней -> 200 зажато до 120; раз в 20 дней -> 60
    for gap, expected in ((200, 120), (20, 60)):
        hist = history_orders("SUP1", "S1", lead=10, promised_lead=10, gap=gap, start=date(2023, 1, 1))
        ds, fc = world(100, {"S1": 1.0}, history=hist)
        assert item(build_plan(ds, fc))["cover_days"] == expected


def test_missing_history_is_explicit_no_data(build):
    # нет ни заказов, ни приходов: плечо и покрытие неизвестны -> null + «нет данных», количество не выдумываем
    ds = build([{"sku_id": "S1", "supplier_id": "SUP1"}], stock={"S1": 40})
    it = build_plan(ds, make_forecast({"S1": 1.0})).item("S1")
    assert it["lead_time_days"] is None and it["lead_time_note"] == "нет данных"
    assert it["cover_days"] is None and it["cover_note"] == "нет данных"
    assert {"lead_time_days", "cover_days"} <= set(it["missing"])
    assert it["order_qty"] is None and it["order_qty_note"] == "нет данных"
    assert it["shortage_risk"] is None and it["batch_step"] == 10


def test_lateness_without_data_is_zero_with_note(world):
    # поставщик SUP9 без истории: просроченный (<=90 дн.) заказ приходит в обещанную дату + 0, с пометкой
    ds, fc = world(40, {"S1": 1.0}, extra_orders=[open_order("PO-5", "S1", "SUP9", date(2025, 12, 20), 30)])
    it = item(build_plan(ds, fc))
    assert it["in_transit_qty"] == 30
    assert any("опоздан" in n for n in it["notes"])


def test_safety_stock_ignores_single_spike(world):
    # 11 месяцев по 30 и один всплеск 300. Медиана 30, потолок 4×30 = 120 -> всплеск становится 120.
    # Среднее (330+120)/12 = 37.5; сумма квадратов: 11×7.5² + 82.5² = 618.75 + 6806.25 = 7425;
    # /11 = 675; σ = 25.98. Группа A: 1.6449 × 25.98 × sqrt(60/30) = 60.44 (без потолка было бы в разы больше).
    ds, fc = world(500, {"S1": 1.0}, monthly_sales={"S1": [30] * 5 + [300] + [30] * 6})
    it = item(build_plan(ds, fc))
    assert it["safety_stock"] == pytest.approx(60.44, abs=0.05)
    assert it["safety_note"] is None


def test_safety_stock_capped_by_demand_over_lead_plus_cover(world):
    # тот же всплеск, но спрос 0.1 шт/день: за 150 дней 15 шт. Расчётный запас 60.44 > 15 -> ровно 15.
    # Цель = 15 + 15 = 30; остаток 0 -> 30 -> до шага 50 = 50.
    ds, fc = world(0, {"S1": 0.1}, monthly_sales={"S1": [30] * 5 + [300] + [30] * 6})
    it = item(build_plan(ds, fc))
    assert it["demand_lead_cover"] == pytest.approx(15)
    assert it["safety_stock"] == pytest.approx(15)
    assert "ограничен" in it["safety_note"] and "safety_capped" in it["flags"]
    assert it["target_stock"] == pytest.approx(30) and it["order_qty"] == 50


def test_lead_time_is_planned_not_first_receipt(world):
    # плановое плечо 120 дней, но приходы фиксировались через 10 дней (частичные/чужие приходы): плечо = 120
    hist = history_orders("SUP1", "S1", lead=10, promised_lead=120)
    ds, fc = world(100, {"S1": 1.0}, history=hist)
    it = item(build_plan(ds, fc))
    assert it["lead_time_days"] == 120 and it["lead_time_source"] == "supplier"
    assert "planned_lead_days" in it["lead_time_note"]


def test_lead_time_drops_junk_planned_values(world):
    # 3 нормальных заказа с плечом 60 и 5 мусорных: плечо -13 (запись задним числом) и 900 (> 400) -> медиана 60
    o, r = history_orders("SUP1", "S1", lead=60, promised_lead=60)
    junk = []
    for i, pl in enumerate([-13, -13, -13, 900, 900]):
        j = dict(o[0]); j.update(po_id=f"J{i}", planned_lead_days=pl); junk.append(j)
    ds, fc = world(100, {"S1": 1.0}, history=([*o, *junk], r))
    assert item(build_plan(ds, fc))["lead_time_days"] == 60


def test_lead_time_category_median_for_unknown_supply(world):
    # поставщика нет в данных: плечо = медиана planned_lead_days заказов категории c (все заказы мира: 60)
    ds, fc = world(10, {"S1": 1.0, "U": 1.0}, extra_skus=[{"sku_id": "U", "supplier_id": None, "supply_type": "unknown"}])
    it = item(build_plan(ds, fc), "U")
    assert (it["lead_time_days"], it["lead_time_source"]) == (60, "category")


def test_safety_stock_uses_demand_net_of_returns(world):
    # продано по 40 в месяц, в 6 месяцах возвращено 10: спрос 40 (6 раз) и 30 (6 раз).
    # Среднее 35, отклонения ±5, σ = sqrt(12*25/11) = 5.2223. Группа A: 1.6449 * 5.2223 * sqrt(2) = 12.15.
    # (по валовым продажам σ = 0 и запас 0.)
    ds, fc = world(500, {"S1": 1.0}, monthly_sales={"S1": [40] * 12}, monthly_returns={"S1": [0, 10] * 6})
    assert item(build_plan(ds, fc))["safety_stock"] == pytest.approx(12.15, abs=0.05)


def test_four_months_of_history_is_enough_for_real_sigma(build):
    # товар продаётся 4 полных месяца (сен-дек 2025): 10, 20, 30, 40 -> среднее 25, выборочная σ = sqrt(500/3) = 12.91.
    # Мало истории — только < 3 месяцев. Группа A: 1.6449 * 12.91 * sqrt(60/30) = 30.03.
    o, r = history_orders("SUP1", "S1", lead=60, promised_lead=60)
    ds = build([{"sku_id": "S1", "supplier_id": "SUP1", "first_sale_month": "2025-09"}], stock={"S1": 500},
               orders=o, receipts=r, monthly_sales={"S1": [10, 20, 30, 40]})
    it = build_plan(ds, make_forecast({"S1": 1.0})).item("S1")
    assert it["safety_stock"] == pytest.approx(30.03, abs=0.05)
    assert "safety_simplified" not in it["flags"] and it["safety_note"] is None
