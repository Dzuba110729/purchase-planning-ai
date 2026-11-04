"""Тесты на крошечных придуманных CSV (никаких реальных данных)."""

import csv
from datetime import date

import pytest

from purchasing.data import DataError, load_dataset, quality_report

HEADERS = {
    "sku_dim": "sku_id,category,supply_type,first_sale_month,last_sale_month,months_with_sales,qty_total,avg_markup_pct_12m,on_order_qty",
    "sales_monthly": "sku_id,category,month,qty_sold,qty_returned,orders_cnt,markup_pct,partial_month",
    "stock_monthly": "sku_id,month,stock_qty",
    "purchase_orders": "po_id,sku_id,supplier_id,order_date,promised_date,planned_lead_days,status,qty_ordered,qty_confirmed,qty_received,qty_pending",
    "receipts": "sku_id,receipt_date,qty,supplier_id",
    "supplier_dim": "supplier_id,supply_type,receipts_cnt,skus,median_days_between_receipts,median_planned_lead_days",
}
ROWS = {
    "sku_dim": [
        "A,c1,import,2025-01,2025-05,5,50,40,0",    # живой
        "B,c1,import,2025-01,2025-05,5,50,10,0",    # низкая наценка
        "C,c1,local,2024-01,2024-06,3,9,,0",        # мёртвый, остаток есть
        "D,c1,local,2024-01,2024-06,3,9,,0",        # мёртвый, остатка нет
    ],
    "sales_monthly": [
        "A,c1,2025-03,10,0,2,40,0", "A,c1,2025-04,20,1,2,40,0", "A,c1,2025-05,30,0,2,40,1",
        "B,c1,2025-04,5,0,1,10,0",
    ],
    "stock_monthly": ["A,2025-03,7", "C,2025-05,3", "C,2025-04,2"],
    "purchase_orders": [
        "P1,A,S1,2025-01-01,2025-06-01,100,open,10,10,0,10",
        "P2,A,S1,2025-01-01,2025-05-10,100,open,5,5,0,5",
        "P3,B,S1,2025-01-01,2025-04-01,100,completed,5,5,5,0",
    ],
    "receipts": ["A,2025-02-01,5,S1"],
    "supplier_dim": ["S1,import,1,2,30,100"],
}


@pytest.fixture
def make(tmp_path):
    def _make(**override):
        for name, header in HEADERS.items():
            rows = override.get(name, ROWS[name])
            h = override.get(name + "_header", header)
            (tmp_path / f"{name}.csv").write_text("\n".join([h, *rows]) + "\n", encoding="utf-8")
        return tmp_path
    return _make


PLAN = date(2025, 5, 20)


def test_loads_small_dataset(make):
    ds = load_dataset(make(), plan_date=PLAN)
    assert len(ds.sku) == 4 and len(ds.sales) == 4


def test_missing_column(make):
    h = HEADERS["sales_monthly"].replace("qty_sold", "qty_sld")
    with pytest.raises(DataError, match="в файле sales_monthly.csv нет колонки qty_sold"):
        load_dataset(make(sales_monthly_header=h), plan_date=PLAN)


def test_text_instead_of_number(make):
    rows = list(ROWS["stock_monthly"]) + ["B,2025-03,много"]
    with pytest.raises(DataError, match="в файле stock_monthly.csv в колонке stock_qty не целое число"):
        load_dataset(make(stock_monthly=rows), plan_date=PLAN)


def test_bad_date(make):
    rows = ["P1,A,S1,вчера,2025-06-01,100,open,10,10,0,10"]
    with pytest.raises(DataError, match="в колонке order_date"):
        load_dataset(make(purchase_orders=rows), plan_date=PLAN)


def test_missing_file(make):
    d = make()
    (d / "receipts.csv").unlink()
    with pytest.raises(DataError, match="нет файла receipts.csv"):
        load_dataset(d, plan_date=PLAN)


def test_empty_file(make):
    d = make()
    (d / "supplier_dim.csv").write_text("")
    with pytest.raises(DataError, match="supplier_dim.csv пустой"):
        load_dataset(d, plan_date=PLAN)


def test_missing_dir(tmp_path):
    with pytest.raises(DataError):
        load_dataset(tmp_path / "net", plan_date=PLAN)


def test_data_dir_from_env(make, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(make()))
    assert len(load_dataset(plan_date=PLAN).sku) == 4


def test_stock_dense_calendar_zero_filled(make):
    ds = load_dataset(make(), plan_date=PLAN)
    st = ds.stock
    # календарь: с первого месяца снимков (2025-03) по месяц плана (2025-05), для каждого SKU
    assert len(st) == 4 * 3
    assert ds.stock_at("2025-03")["A"] == 7
    assert ds.stock_at("2025-05")["A"] == 0  # нет строки = 0
    assert ds.stock_at()["C"] == 3
    assert ds.stock_at()["D"] == 0
    assert st["restored"].sum() == 12 - 3


def test_marking(make):
    ds = load_dataset(make(), plan_date=PLAN)
    assert ds.live_skus == ["A", "B"]
    assert ds.dead_skus == ["C", "D"]
    assert ds.low_markup_skus == ["B"]
    assert ds.auto_purchase_skus() == ["A"]
    assert ds.price_review_list() == ["B"]
    assert ds.retire_list() == ["C", "D"]
    assert ds.sell_off_list() == ["C"]
    assert ds.zero_stock_live_skus() == ["A", "B"]


def test_open_orders(make):
    ds = load_dataset(make(), plan_date=PLAN)
    assert len(ds.open_orders) == 2
    assert len(ds.in_transit_orders) == 1 and ds.in_transit_qty == 10
    assert len(ds.overdue_orders) == 1 and ds.overdue_qty == 5


def test_partial_month_excluded_from_means(make):
    base = load_dataset(make(), plan_date=PLAN)
    assert base.mean_monthly_sales()["A"] == 7.5  # (10 + 20) / 4 мес. окна 2025-01..2025-04, май не входит
    rows = [r.replace("A,c1,2025-05,30", "A,c1,2025-05,99999") for r in ROWS["sales_monthly"]]
    changed = load_dataset(make(sales_monthly=rows), plan_date=PLAN)
    assert changed.mean_monthly_sales().equals(base.mean_monthly_sales())
    assert "2025-05" not in set(map(str, base.full_months_sales()["month"]))


def test_plan_date_derived_from_partial_month(make):
    ds = load_dataset(make())
    assert ds.plan_date.year == 2025 and ds.plan_date.month == 5


def test_quality_report_counts_only(make):
    rep = quality_report(load_dataset(make(), plan_date=PLAN))
    assert rep["rows"]["sku_dim.csv"] == 4
    assert rep["sku"]["live"] == 2 and rep["sku"]["dead"] == 2 and rep["sku"]["low_markup"] == 1
    assert rep["oddities"]["sales.qty_returned > qty_sold"] == 0
    assert rep["open_orders"]["in_transit_qty"] == 10
    assert not any(isinstance(v, str) and v.startswith("SKU") for v in rep.values())


def test_mean_divides_by_window_months_not_rows(make):
    # у A строки только за 2025-03 и 2025-05; окно 2025-01..2025-04 (май неполный)
    rows = ["A,c1,2025-03,10,0,2,40,0", "A,c1,2025-04,20,1,2,40,0", "A,c1,2025-05,30,0,2,40,1"]
    rows2 = [rows[0], rows[2]]
    ds = load_dataset(make(sales_monthly=rows2), plan_date=PLAN)
    # first_sale_month A = 2025-01, полные месяцы 2025-01..2025-04 = 4, продаж 10
    assert ds.mean_monthly_sales()["A"] == 2.5


def test_empty_required_text(make):
    rows = ["A,c1,import,2025-01,2025-05,5,50,40,0", ",c1,import,2025-01,2025-05,5,50,40,0"]
    with pytest.raises(DataError, match="в файле sku_dim.csv в колонке sku_id пустые значения: 1 стр., строки файла: 3"):
        load_dataset(make(sku_dim=rows), plan_date=PLAN)


def test_inf_is_error(make):
    rows = ["A,c1,import,2025-01,2025-05,5,50,inf,0"]
    with pytest.raises(DataError, match="avg_markup_pct_12m не число"):
        load_dataset(make(sku_dim=rows), plan_date=PLAN)


def test_bom_file_reads(make):
    d = make()
    p = d / "stock_monthly.csv"
    p.write_bytes(b"\xef\xbb\xbf" + p.read_bytes())
    assert len(load_dataset(d, plan_date=PLAN).sku) == 4


def test_bad_plan_date(make):
    with pytest.raises(DataError, match="дата плана"):
        load_dataset(make(), plan_date="13.13.2026")


def test_bad_plan_date_env(make, monkeypatch):
    monkeypatch.setenv("PLAN_DATE", "вчера")
    with pytest.raises(DataError, match="PLAN_DATE"):
        load_dataset(make())


def test_plan_date_env_override(make, monkeypatch):
    monkeypatch.setenv("PLAN_DATE", "2025-05-03")
    ds = load_dataset(make())
    assert ds.plan_date == date(2025, 5, 3) and ds.plan_date_source == "env"


def test_param_beats_env(make, monkeypatch):
    monkeypatch.setenv("PLAN_DATE", "2025-05-03")
    ds = load_dataset(make(), plan_date="2025-05-07")
    assert ds.plan_date == date(2025, 5, 7) and ds.plan_date_source == "param"


def test_plan_date_exact_derived(make):
    # самая поздняя дата в данных 2025-02-01, но не раньше начала неполного месяца 2025-05
    ds = load_dataset(make())
    assert ds.plan_date == date(2025, 5, 1) and ds.plan_date_source == "derived"
    assert "из дат в данных" in quality_report(ds)["plan_date_source"]


def test_no_partial_month_uses_last_day(make):
    rows = [r.replace(",1", ",0") if r.endswith(",1") else r for r in ROWS["sales_monthly"]]
    ds = load_dataset(make(sales_monthly=rows))
    assert ds.plan_date == date(2025, 5, 31) and ds.plan_date_source == "derived"


def test_markup_exactly_25_is_live_not_low(make):
    rows = ["A,c1,import,2025-01,2025-05,5,50,25,0", "B,c1,import,2025-01,2025-05,5,50,24.99,0"]
    ds = load_dataset(make(sku_dim=rows), plan_date=PLAN)
    assert ds.low_markup_skus == ["B"] and ds.live_skus == ["A", "B"]


def test_promised_on_plan_date_is_in_transit(make):
    rows = ["P1,A,S1,2025-01-01,2025-05-20,100,open,10,10,0,10"]
    ds = load_dataset(make(purchase_orders=rows), plan_date=PLAN)
    assert len(ds.in_transit_orders) == 1 and len(ds.overdue_orders) == 0


def test_dead_recent_oddity(make):
    rows = ["C,c1,local,2025-01,2025-04,3,9,,0", "D,c1,local,2020-01,2020-06,3,9,,0"]
    rep = quality_report(load_dataset(make(sku_dim=rows), plan_date=PLAN))
    assert rep["oddities"]["sku_dim.dead_with_last_sale_month_in_last_12m"] == 1


def test_cli_data_report_counts_only(make, capsys):
    from purchasing.cli import main

    assert main(["data-report", "--data-dir", str(make()), "--plan-date", "2025-05-20"]) == 0
    out = capsys.readouterr().out
    assert "live: 2" in out and "2025-05-20" in out
    assert "A,c1" not in out


def test_cli_error_code_2(make, capsys):
    from purchasing.cli import main

    assert main(["data-report", "--data-dir", str(make()), "--plan-date", "xx"]) == 2
    assert "дата плана" in capsys.readouterr().err
    assert main(["data-report", "--data-dir", "/nonexistent-dir-xyz"]) == 2
