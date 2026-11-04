"""Excel order export from the same public projection as the dashboard."""

from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from .model import category_label, has_order, iso_date, item_rows, number, purchase_order


def build_xlsx(facts, path) -> Path:
    """Write the full order list with numeric quantities and true Excel dates."""
    rows = sorted((row for row in item_rows(facts) if has_order(row)), key=purchase_order)
    book = Workbook()
    order = book.active
    order.title = "К заказу"
    order.append(["Код товара", "Категория", "К заказу, шт.", "Дата заказа"])
    for row in rows:
        when = row["next_order_date"]
        order.append([
            row["sku_id"], category_label(row["category"]), row["order_qty"],
            date.fromisoformat(when) if when else None,
        ])
        order.cell(order.max_row, 4).number_format = "dd.mm.yyyy"
    order.auto_filter.ref = order.dimensions
    order.freeze_panes = "C2"
    for column, width in {"A": 19, "B": 36, "C": 19, "D": 19}.items():
        order.column_dimensions[column].width = width

    summary = book.create_sheet("Сводка")
    summary.append(["Показатель", "Значение"])
    when = iso_date(facts.get("plan_date"))
    summary.append(["Дата плана", date.fromisoformat(when) if when else None])
    summary["B2"].number_format = "dd.mm.yyyy"
    summary.append(["К заказу, шт.", number(facts["totals"].get("order_qty_total"))])
    summary.column_dimensions["A"].width = 29
    summary.column_dimensions["B"].width = 19
    for sheet in (order, summary):
        sheet.row_dimensions[1].height = 25
        for cell in sheet[1]:
            cell.fill = PatternFill("solid", fgColor="143B37")
            cell.font = Font(color="FFFFFF", bold=True)
            cell.alignment = Alignment(vertical="center")

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    book.save(target)
    return target
