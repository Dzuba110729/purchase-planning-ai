"""Точка входа `purchasing`. Заглушка каркаса: команды добавляют следующие этапы."""

import argparse
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="purchasing",
        description="ИИ-система планирования закупок (каркас: команды появятся на следующих этапах).",
    )


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0
