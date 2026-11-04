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
    bt = sub.add_parser("backtest", help="бэктест прогноза: из августа 2025 против факта (WAPE по категориям)")
    bt.add_argument("--data-dir", default=None, help="папка с CSV (иначе DATA_DIR, иначе share/)")
    bt.add_argument("--plots-dir", default=None, help="сохранить графики «прогноз против факта» в эту папку")
    fc = sub.add_parser("facts", help="расчёт закупки: загрузка, прогноз, план, файл facts.json")
    fc.add_argument("--data-dir", default=None, help="папка с CSV (иначе DATA_DIR, иначе share/)")
    fc.add_argument("--plan-date", default=None, help="дата плана YYYY-MM-DD")
    fc.add_argument("--out", default="out/facts.json", help="куда записать facts.json (по умолчанию out/facts.json)")
    dash = sub.add_parser("dashboard", help="собрать статичную страницу и Excel с шаблонными пояснениями")
    dash.add_argument("--facts", default="out/facts.json", help="готовый файл расчёта")
    dash.add_argument("--out", default="out/dashboard", help="папка для index.html и order.xlsx")
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
    elif args.command == "backtest":
        from purchasing.data import DataError, load_dataset
        from purchasing.forecast import backtest, format_backtest

        try:
            ds = load_dataset(args.data_dir)
        except DataError as exc:
            print(f"Ошибка данных: {exc}", file=sys.stderr)
            return 2
        res = backtest(ds)
        print(format_backtest(res))
        if args.plots_dir:
            from purchasing.forecast.plots import plot_backtest

            for category, (sku, path) in plot_backtest(res, ds, args.plots_dir).items():
                print(f"график: {path} ({category}, {sku})")
    elif args.command == "facts":
        from purchasing.data import DataError, load_dataset
        from purchasing.forecast import backtest, build_forecast
        from purchasing.planning import build_plan, write_facts

        try:
            ds = load_dataset(args.data_dir, args.plan_date)
            forecast = build_forecast(ds, ds.plan_date)
        except DataError as exc:
            print(f"Ошибка данных: {exc}", file=sys.stderr)
            return 2
        except ValueError as exc:
            print(f"Ошибка расчёта: {exc}", file=sys.stderr)
            return 2
        plan = build_plan(ds, forecast, ds.plan_date, accuracy=backtest(ds))
        path = write_facts(plan, args.out)
        t = plan.totals
        print(f"facts.json записан: {path} (дата плана {plan.plan_date}, живых {t['live_total']}, "
              f"в автозакупке {t['auto_purchase_items']}, к заказу {t['order_qty_total']} шт.)")
    elif args.command == "dashboard":
        import json
        from pathlib import Path

        from purchasing.ai import AIConfig, write_texts
        from purchasing.dashboard import build_site, build_xlsx

        try:
            facts = json.loads(Path(args.facts).read_text(encoding="utf-8"))
            if not isinstance(facts, dict):
                raise ValueError
            texts = write_texts(facts, config=AIConfig(max_requests=0))
            path = build_site(facts, texts, args.out)
            build_xlsx(facts, path.with_name("order.xlsx"))
        except (OSError, KeyError, TypeError, ValueError):
            print("Ошибка дашборда: проверьте файл facts.json и доступ к папке вывода.", file=sys.stderr)
            return 2
        print(f"Дашборд и Excel собраны: {path.parent}")
    return 0
