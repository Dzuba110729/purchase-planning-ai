"""Чтение и проверка CSV."""

import numpy as np
import pandas as pd

from .errors import DataError
from .schema import DATE, FLOAT, INT, MONTH, FileSpec


def _convert(df: pd.DataFrame, spec: FileSpec, col: str, kind: str) -> pd.Series:
    s = df[col]
    present = s.notna()
    if kind == "str":
        empty = s.isna() | (s.astype("string").str.strip() == "")
        if col not in spec.nullable and empty.any():
            rows = ", ".join(str(i + 2) for i in df.index[empty][:5])
            raise DataError(
                f"в файле {spec.name} в колонке {col} пустые значения: "
                f"{int(empty.sum())} стр., строки файла: {rows}"
            )
        return s
    if kind in (INT, FLOAT):
        out = pd.to_numeric(s, errors="coerce")
        bad = (present & out.isna()) | np.isinf(out.astype("float64"))
        if kind == INT:
            bad |= out.notna() & (out != out.round())
    else:
        fmt = "%Y-%m" if kind == MONTH else "%Y-%m-%d"
        out = pd.to_datetime(s, format=fmt, errors="coerce")
        bad = present & out.isna()
    if bad.any():
        what = {INT: "не целое число", FLOAT: "не число", MONTH: "не месяц YYYY-MM", DATE: "не дата YYYY-MM-DD"}[kind]
        rows = ", ".join(str(i + 2) for i in df.index[bad][:5])
        raise DataError(
            f"в файле {spec.name} в колонке {col} {what}: "
            f"{int(bad.sum())} стр., строки файла: {rows}"
        )
    if col not in spec.nullable and out.isna().any():
        raise DataError(f"в файле {spec.name} в колонке {col} пустые значения: {int(out.isna().sum())} стр.")
    if kind == INT:
        out = out.astype("Int64") if out.isna().any() else out.astype("int64")
    if kind == MONTH:
        out = out.dt.to_period("M")
    return out


def read_file(data_dir, spec: FileSpec) -> pd.DataFrame:
    path = data_dir / spec.name
    if not path.is_file():
        raise DataError(f"нет файла {spec.name} в папке данных")
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=True, encoding="utf-8-sig")
    except pd.errors.EmptyDataError:
        raise DataError(f"файл {spec.name} пустой") from None
    except OSError as exc:
        raise DataError(f"файл {spec.name} не удалось открыть ({type(exc).__name__})") from None
    except (pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise DataError(f"файл {spec.name} не читается как CSV ({type(exc).__name__})") from None
    for col in spec.columns:
        if col not in df.columns:
            raise DataError(f"в файле {spec.name} нет колонки {col}")
    if df.empty:
        raise DataError(f"в файле {spec.name} нет строк данных")
    for col, kind in spec.columns.items():
        df[col] = _convert(df, spec, col, kind)
    for col, kind in spec.optional.items():
        if col in df.columns:
            df[col] = _convert(df, FileSpec(spec.name, {}, spec.nullable | {col}), col, kind)
    return df
