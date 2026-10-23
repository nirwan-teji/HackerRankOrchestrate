"""python scripts/validate_output.py output.csv input.csv col1,col2,..."""
import sys

import pandas as pd

out, inp = pd.read_csv(sys.argv[1], dtype=str), pd.read_csv(sys.argv[2], dtype=str)
cols = sys.argv[3].split(",") if len(sys.argv) > 3 else None
assert len(out) == len(inp), f"rows {len(out)} != {len(inp)}"
if cols:
    assert list(out.columns) == cols, f"columns {list(out.columns)} != {cols}"
print("nulls per column:\n", out.isna().sum()[out.isna().sum() > 0])
print("OK")
