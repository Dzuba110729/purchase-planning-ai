"""Recalculate and stage both dashboard files before replacing a local preview."""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from purchasing.ai import AIConfig, write_texts
from purchasing.dashboard import build_site, build_xlsx
from purchasing.data import DataError, load_dataset
from purchasing.data.schema import FILES
from purchasing.forecast import backtest, build_forecast
from purchasing.planning import build_plan


def run(data_dir, plan_date, out, journal, *, use_ai=False, public_logs=False):
    """Return 2 for input errors and 1 for calculation or filesystem errors."""
    try:
        dataset = load_dataset(data_dir, plan_date)
    except DataError as exc:
        if public_logs:
            filename = next((spec.name for spec in FILES.values() if spec.name in str(exc)), None)
            detail = f"проверьте формат и обязательные поля {filename}" if filename else "проверьте комплект CSV и дату среза"
        else:
            detail = str(exc)
        print(f"Ошибка данных: {detail}.", file=sys.stderr)
        return 2

    try:
        forecast = build_forecast(dataset, dataset.plan_date)
        plan = build_plan(dataset, forecast, dataset.plan_date, accuracy=backtest(dataset))
        facts = plan.to_facts()
        texts = write_texts(facts, config=AIConfig(max_requests=900 if use_ai else 0), journal_path=journal)
        target = Path(out)
        target.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=target.parent) as stage:
            index = build_site(facts, texts, stage)
            workbook = build_xlsx(facts, index.with_name("order.xlsx"))
            target.mkdir(exist_ok=True)
            index.replace(target / index.name)
            workbook.replace(target / workbook.name)
    except (OSError, KeyError, TypeError, ValueError):
        print("Ошибка сборки: проверьте расчёт и доступ к папке вывода.", file=sys.stderr)
        return 1
    print("Дашборд и Excel собраны.")
    return 0
