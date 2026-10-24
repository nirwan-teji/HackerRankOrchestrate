"""Load the participant-facing CSVs and (lazily) the evidence observations."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from .evidence import load_all
from .model import Obs


def canon(tok) -> str:
    """Canonical identifier token: emails/hashes lowercase, phones digits with leading '+'."""
    s = str(tok or "").strip().lower()
    if not s or s in ("null", "none"):
        return ""
    if "@" in s or re.fullmatch(r"[0-9a-f]{64}", s) or re.match(r"[a-z_]+_\d+$", s):
        return s
    d = re.sub(r"\D", "", s)
    if re.fullmatch(r"[+\d\s()\-.]{7,}", s) and len(d) >= 7:
        return "+" + d
    return s


@dataclass
class Dataset:
    root: Path
    identities: list[dict] = field(default_factory=list)
    audit_log: list[dict] = field(default_factory=list)
    activity: list[dict] = field(default_factory=list)
    rules: list[dict] = field(default_factory=list)
    manifest: list[dict] = field(default_factory=list)
    obs: list[Obs] = field(default_factory=list)
    scopes: dict = field(default_factory=dict)

    @classmethod
    def load(cls, root) -> "Dataset":
        root = Path(root)
        rd = lambda n: list(csv.DictReader(open(root / n, encoding="utf-8")))
        missing = [m["file_path"] for m in rd("evidence_files.csv") if not (root / m["file_path"]).exists()]
        if missing:  # a partial dataset silently changes the answers, so refuse to run
            raise SystemExit(f"{len(missing)} evidence file(s) listed in evidence_files.csv are missing under {root}, e.g. "
                             f"{missing[:3]}.\nRe-copy the full dataset/ folder (the official starter repo has every file).")
        ds = cls(root, rd("user_identities.csv"), rd("user_audit_log.csv"), rd("system_activity.csv"),
                 rd("deletion_rules.csv"), rd("evidence_files.csv"))
        ds.obs, ds.scopes = load_all(root, ds.manifest)
        for o in ds.obs:
            o.idents = frozenset(c for c in (canon(x) for x in o.idents) if c)
        return ds

    def requests(self, name="deletion_requests.csv") -> list[dict]:
        return list(csv.DictReader(open(self.root / name, encoding="utf-8")))
