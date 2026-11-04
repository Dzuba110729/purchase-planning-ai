"""Read the small subset of an already-public dashboard needed for notifications."""

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from purchasing.dashboard.model import code, iso_date

MAX_PAGE_BYTES = 20_000_000
TOTALS = ("live_total", "order_qty_total", "order_now_items", "shortage_risk_items", "overstock_risk_items")


@dataclass(frozen=True)
class Position:
    qty: int
    date: str | None
    now: bool
    shortage: bool
    overstock: bool


@dataclass(frozen=True)
class PublishedPlan:
    date: str
    totals: dict[str, int]
    positions: dict[str, Position]
    templates: bool


def _int(value):
    if not re.fullmatch(r"[0-9]{1,18}", value or ""):
        raise ValueError("Ожидалось целое число из дашборда.")
    return int(value)


class _PlanParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}
        self.modes = {}
        self.in_provenance = False
        self.templates = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr" and "data-sku" in attrs:
            sku = code(attrs["data-sku"])
            if sku in self.modes:
                raise ValueError("Повторяется строка дашборда.")
            self.modes[sku] = frozenset((attrs.get("data-modes") or "").split())
        if tag == "span":
            self.in_provenance = attrs.get("class") == "provenance"
            if "data-fact" in attrs:
                self.values.setdefault(attrs["data-fact"], attrs.get("data-value"))

    def handle_data(self, data):
        if self.in_provenance and data.strip() == "Шаблон":
            self.templates = True

    def handle_endtag(self, tag):
        if tag == "span":
            self.in_provenance = False


def read_public_plan(page: bytes) -> PublishedPlan:
    if len(page) > MAX_PAGE_BYTES:
        raise ValueError("Дашборд превышает предел размера.")
    parser = _PlanParser()
    parser.feed(page.decode("utf-8"))
    parser.close()
    date = iso_date(parser.values.get("plan_date"))
    totals = {key: _int(parser.values.get(f"totals.{key}")) for key in TOTALS}
    if date is None or totals["live_total"] != len(parser.modes):
        raise ValueError("Дашборд неполон.")
    positions = {}
    for sku, modes in parser.modes.items():
        qty = parser.values[f"items.{sku}.order_qty"]
        next_date = parser.values[f"items.{sku}.next_order_date"]
        positions[sku] = Position(
            _int(qty) if qty else 0, iso_date(next_date), "now" in modes,
            "shortage" in modes, "overstock" in modes,
        )
    return PublishedPlan(date, totals, positions, parser.templates)
