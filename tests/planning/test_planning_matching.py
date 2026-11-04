"""Сопоставление приходов с заказами (нужно для обычного опоздания поставщика): ответы посчитаны руками."""

from datetime import date

import pandas as pd

from planning_helpers import ORDER_COLS
from purchasing.data import Dataset
from purchasing.planning.stats import matched_orders


def _order(po, sku, od, promised, qty, received=None):
    return {"po_id": po, "sku_id": sku, "supplier_id": "S", "order_date": pd.Timestamp(od),
            "promised_date": pd.Timestamp(promised), "planned_lead_days": (pd.Timestamp(promised) - pd.Timestamp(od)).days,
            "status": "completed", "qty_ordered": qty, "qty_confirmed": qty,
            "qty_received": qty if received is None else received, "qty_pending": 0}


def _ds(orders, receipts):
    rec = pd.DataFrame([{"sku_id": s, "receipt_date": pd.Timestamp(d), "qty": q, "supplier_id": "S"} for s, d, q in receipts])
    sku = pd.DataFrame({"sku_id": sorted({o["sku_id"] for o in orders}), "category": "c"})
    return Dataset(sku=sku, sales=pd.DataFrame(), stock=pd.DataFrame(), orders=pd.DataFrame(orders, columns=ORDER_COLS),
                   receipts=rec, suppliers=pd.DataFrame(), plan_date=date(2026, 1, 1))


def _row(m, sku, od):
    return m[(m["sku_id"] == sku) & (m["order_date"] == pd.Timestamp(od))].iloc[0]


def test_partial_receipts_and_old_order_receipts_are_not_given_to_new_order():
    # Заказ A: 01.01.2025, 100 шт., обещан 01.03 (59 дн.). Заказ B: 01.02.2025, 100 шт., обещан 01.04 (59 дн.).
    # Приходы: 10.03 (60), 20.03 (40) — закрывают A целиком; 01.05 (100) — B.
    # A: готов 20.03 -> 78 дн., опоздание 78-59 = 19. B: 01.05 -> 89 дн., опоздание 30.
    # (по «первому приходу после заказа» B получил бы 10.03 = 37 дн. — приход по старому заказу A.)
    o = [_order("A", "X", "2025-01-01", "2025-03-01", 100), _order("B", "X", "2025-02-01", "2025-04-01", 100)]
    m = matched_orders(_ds(o, [("X", "2025-03-10", 60), ("X", "2025-03-20", 40), ("X", "2025-05-01", 100)]), date(2026, 1, 1))
    a, b = _row(m, "X", "2025-01-01"), _row(m, "X", "2025-02-01")
    assert (a["actual_days"], a["lateness"]) == (78, 19)
    assert (b["actual_days"], b["lateness"]) == (89, 30)


def test_one_receipt_for_several_orders():
    # Заказы C (100) и D (200) от 01.01.2025, обещаны 01.03 (59 дн.); один приход 01.04.2025 на 300 шт.
    # Оба закрыты 01.04 -> 90 дн., опоздание 31.
    o = [_order("C", "Y", "2025-01-01", "2025-03-01", 100), _order("D", "Y", "2025-01-01", "2025-03-01", 200)]
    m = matched_orders(_ds(o, [("Y", "2025-04-01", 300)]), date(2026, 1, 1))
    assert len(m) == 2 and set(m["actual_days"]) == {90} and set(m["lateness"]) == {31}


def test_order_without_full_receipt_is_skipped():
    # получено 100, пришло только 60 -> заказ не сопоставлен и в статистику не входит
    o = [_order("E", "Z", "2025-01-01", "2025-03-01", 100)]
    assert matched_orders(_ds(o, [("Z", "2025-03-10", 60)]), date(2026, 1, 1)).empty
