"""Проверенные пояснения к facts.json с запасным режимом без сети."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from .client import AIConfig, DEFAULT_JOURNAL, FLASH, DeepSeek, TokenUsage
from .passport import ANOMALY_NUMBERS, NO_DATA, SUMMARY_NUMBERS, answer_error, item_passport, totals_passport

__all__ = ["AIConfig", "Text", "Texts", "write_texts"]


@dataclass(frozen=True)
class Text:
    text: str
    used_facts: tuple[str, ...]
    insufficient_data: bool
    source: str
    model: str | None = None


@dataclass(frozen=True)
class Texts:
    summary: Text
    anomalies: Text
    items: dict[str, Text]
    requests: int
    token_usage: tuple[TokenUsage, ...]
    unaccounted_requests: int
    errors: dict[str, int]


FORECAST_WARNING = "Точность прогноза по товару низкая. Ориентир — сумма за период по категории."

TEMPLATE_FIELDS = {
    "summary": (
        ("order_now_items", "Позиций к заказу сейчас"),
        ("order_qty_total", "Всего к заказу, шт."),
        ("shortage_risk_items", "Позиций с риском дефицита"),
    ),
    "anomalies": (
        ("shortage_risk_items", "Позиций с риском дефицита"),
        ("overstock_risk_items", "Позиций с риском излишка"),
        ("ask_supplier_items", "Заказов для уточнения у поставщика"),
    ),
    "item": (
        ("stock", "Остаток, шт."),
        ("in_transit_qty", "В пути, шт."),
        ("order_qty", "К заказу, шт."),
        ("next_order_date", "Дата заказа"),
    ),
}


def _template(kind, passport):
    fields = TEMPLATE_FIELDS.get(kind, ())
    sentences = [f"{label}: {passport[field]}." for field, label in fields]
    return Text(
        " ".join(sentences) if sentences else "В данных нет.",
        tuple(field for field, _ in fields),
        not fields or any(passport[field] == NO_DATA for field, _ in fields),
        "template",
    )


def _generate(client, kind, passport):
    first_model = client.config.item_model if kind == "item" else client.config.summary_model
    models = (first_model, FLASH)
    error = None
    for model in models:
        answer, error = client.ask(kind, passport, model, error)
        if error is None:
            error = answer_error(answer, passport)
            if error is None:
                return Text(
                    answer["text"].strip(), tuple(answer["used_facts"]), answer["insufficient_data"], "ai", model,
                )
            client.errors[error] += 1
        if client.blocked or error in ("key_missing", "request_limit"):
            break
    return _template(kind, passport)


def _write_texts(facts, client):
    summary = _generate(client, "summary", totals_passport(facts, SUMMARY_NUMBERS))
    if facts.get("forecast_accuracy", {}).get("warning"):
        summary = replace(
            summary,
            text=f"{summary.text} {FORECAST_WARNING}",
            used_facts=(*summary.used_facts, "forecast_accuracy.warning"),
        )
    anomalies = _generate(client, "anomalies", totals_passport(facts, ANOMALY_NUMBERS))
    items = {
        row["sku_id"]: _generate(client, "item", item_passport(row, facts.get("plan_date")))
        for row in facts.get("items", ())
    }
    return Texts(
        summary, anomalies, items, client.requests, tuple(client.usage),
        client.unaccounted_requests, dict(client.errors),
    )


def write_texts(
    facts: Mapping,
    *,
    config: AIConfig = AIConfig(),
    journal_path: str | Path = DEFAULT_JOURNAL,
) -> Texts:
    """Формирует тексты по facts.json. Журнал содержит тела запросов и хранится локально."""
    return _write_texts(facts, DeepSeek(config, journal_path))
