"""Живая проверка на двадцати товарах с закрытым отчётом для ручного чтения."""

import argparse
import json
import os
import sys
from collections import defaultdict, deque
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from tempfile import mkdtemp

from . import AIConfig, _generate, _write_texts
from .client import DEFAULT_JOURNAL, FLASH, PRO, DeepSeek, load_json, private_write
from .passport import SKU, totals_passport

SAMPLE_SIZE = 20
MIN_PASSED = 18
PRICING_URL = "https://api-docs.deepseek.com/quick_start/pricing/"
PEAK_RATES = {
    FLASH: tuple(map(Decimal, ("0.006", "0.30", "1.20"))),
    PRO: tuple(map(Decimal, ("0.044", "1.32", "3.96"))),
}


def _sample(items):
    categories = defaultdict(deque)
    seen = set()
    for item in sorted(items, key=lambda row: (row["category"], row["sku_id"])):
        code = item["sku_id"]
        if code not in seen and SKU.fullmatch(code) and len(code) <= 40:
            categories[item["category"]].append(item)
            seen.add(code)
    if len(seen) < SAMPLE_SIZE:
        raise ValueError("Недостаточно товаров для проверки.")

    sample = []
    queues = deque(categories.values())
    for _ in range(SAMPLE_SIZE):
        queue = queues.popleft()
        sample.append(queue.popleft())
        if queue:
            queues.append(queue)
    return sample


def _cost(usage, off_peak):
    cost = sum(
        sum(count * rate for count, rate in zip(
            (entry.cache_hit, entry.cache_miss, entry.completion), PEAK_RATES[entry.model], strict=True,
        ))
        for entry in usage
    )
    return format(cost / Decimal(2_000_000 if off_peak else 1_000_000), "f")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts", type=Path, default=Path("out/facts.json"))
    parser.add_argument("--off-peak", action="store_true", help="Оценить стоимость по тарифу вне пика.")
    args = parser.parse_args(argv)

    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        print("Для живой проверки требуется DEEPSEEK_API_KEY в окружении запуска.", file=sys.stderr)
        return 2

    try:
        facts = load_json(args.facts.read_text(encoding="utf-8"))
        if facts["schema_version"] != 1:
            raise ValueError("Неподдерживаемая схема.")
        sample = _sample(facts["items"])
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        print("Для проверки нужен facts.json схемы 1 минимум с двадцатью уникальными товарами.", file=sys.stderr)
        return 2

    try:
        base = DEFAULT_JOURNAL.parent
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory = Path(mkdtemp(prefix="acceptance-", dir=base))
        client = DeepSeek(AIConfig(max_requests=2 * (SAMPLE_SIZE + 3)), directory / DEFAULT_JOURNAL.name)
        texts = _write_texts({**facts, "items": sample}, client)
        probe = _generate(client, "probe", totals_passport(facts, ()))
        probe_passed = (probe.source == "ai" and probe.insufficient_data
                        and probe.text.strip().casefold().rstrip(".") == "в данных нет")
        passed_items = sum(entry.source == "ai" for entry in texts.items.values())
        has_usage = bool(client.usage) and not client.unaccounted_requests
        report = {
            "sample_size": SAMPLE_SIZE,
            "passed_items": passed_items,
            "pass_rate": passed_items / SAMPLE_SIZE,
            "probe_passed": probe_passed,
            "probe": asdict(probe),
            "estimated_cost_usd": _cost(client.usage, args.off_peak) if client.usage else None,
            "price_band": "off-peak" if args.off_peak else "peak",
            "pricing_url": PRICING_URL,
            "requests": client.requests,
            "unaccounted_requests": client.unaccounted_requests,
            "errors": dict(client.errors),
            "summary": asdict(texts.summary),
            "anomalies": asdict(texts.anomalies),
            "items": {code: asdict(entry) for code, entry in texts.items.items()},
        }
        path = directory / "report.json"
        private_write(path, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
    except OSError:
        print("Не удалось сохранить закрытый отчёт приёмки.", file=sys.stderr)
        return 2

    print(f"Закрытый отчёт для ручной проверки — {path}")
    if "authentication" in report["errors"]:
        print("DeepSeek отклонил ключ.", file=sys.stderr)
    elif "balance" in report["errors"]:
        print("DeepSeek сообщил о недостаточном балансе.", file=sys.stderr)
    return 0 if passed_items >= MIN_PASSED and probe_passed and has_usage else 1


if __name__ == "__main__":
    raise SystemExit(main())
