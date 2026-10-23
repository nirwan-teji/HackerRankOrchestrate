"""Builds submission/: code.zip (no venv/data/csv/.env/cache), output.csv, log.txt."""
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".venv", "venv", "node_modules", "__pycache__", ".cache", "data", "dataset", "support_tickets", ".pytest_cache"}
SKIP_SUFFIX = {".pyc", ".csv"}
out = ROOT / "submission"
out.mkdir(exist_ok=True)

with zipfile.ZipFile(out / "code.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for p in (ROOT / "code").rglob("*"):
        if p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS) and p.suffix not in SKIP_SUFFIX and p.name != ".env":
            z.write(p, p.relative_to(ROOT / "code"))

if len(sys.argv) > 1:
    shutil.copy(sys.argv[1], out / "output.csv")
log = Path.home() / "hackerrank_orchestrate" / "log.txt"
if log.exists():
    shutil.copy(log, out / "log.txt")
else:
    print(f"WARNING: transcript not found at {log} (check AGENTS.md for this edition's path)")
print("built", out, [f.name for f in out.iterdir()])
