"""CSV I/O with strict schema validation (wrong columns/rows = silent score loss)."""
from pathlib import Path

import pandas as pd


def read_csv(path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def write_output(df: pd.DataFrame, path, columns: list[str], expected_rows: int | None = None) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"missing output columns: {missing}")
    if expected_rows is not None and len(df) != expected_rows:
        raise ValueError(f"row count {len(df)} != expected {expected_rows}")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df[columns].to_csv(path, index=False)


def check_enum(df: pd.DataFrame, col: str, allowed: set[str]) -> list[int]:
    """Return row indexes whose value is outside the allowed enum."""
    return [i for i, v in df[col].items() if v not in allowed]
