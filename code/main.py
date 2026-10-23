"""Entry point. Usage: python code/main.py [--input X.csv] [--output Y.csv] [--sample]

TODO per task: implement `process_row` (use orchestrate.graph / llm / retrieval / rules).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tqdm import tqdm

from orchestrate import io_utils
from orchestrate.graph import build
from orchestrate.llm import USAGE

OUTPUT_COLUMNS: list[str] = []  # TODO: exact order from problem_statement.md


def process_row(app, row: dict) -> dict:
    return app.invoke({"row": row}).get("output", {})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", default="output.csv")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()

    df = io_utils.read_csv(a.input)
    if a.limit:
        df = df.head(a.limit)
    app = build()
    rows = [process_row(app, r) for r in tqdm(df.to_dict("records"))]
    out = df.assign(**{k: [r.get(k, "") for r in rows] for k in OUTPUT_COLUMNS})
    io_utils.write_output(out, a.output, OUTPUT_COLUMNS or list(out.columns), expected_rows=len(df))
    print(json.dumps(USAGE.report(), indent=2))  # ops analysis: calls, tokens, runtime


if __name__ == "__main__":
    main()
