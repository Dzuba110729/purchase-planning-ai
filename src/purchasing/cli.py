"""Точка входа `purchasing`. Команды добавляют этапы."""

import argparse
import sys
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="purchasing",
        description="ИИ-система планирования закупок.",
    )
    sub = parser.add_subparsers(dest="command")
    rep = sub.add_parser("data-report", help="отчёт о качестве данных (только счётчики)")
    rep.add_argument("--data-dir", default=None, help="папка с CSV (иначе DATA_DIR, иначе share/)")
    rep.add_argument("--plan-date", default=None, help="дата плана YYYY-MM-DD")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "data-report":
        from purchasing.data import DataError, format_report, load_dataset, quality_report

        try:
            ds = load_dataset(args.data_dir, args.plan_date)
        except DataError as exc:
            print(f"Ошибка данных: {exc}", file=sys.stderr)
            return 2
        print(format_report(quality_report(ds)))
    return 0
