"""PDF business documents -> observations (statements, support attachments, retention registers, journals)."""
from __future__ import annotations

import re
from datetime import datetime

from .model import EMAIL_RX, RID_RX, USERREF_RX, Obs, norm

PHONE_RX = re.compile(r"\+\d[\d ()\-]{6,}\d")
_MON = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}


def _date(s: str) -> str:
    m = re.search(r"(\d{1,2}) (\w{3}) (\d{4})", s)
    return f"{m.group(3)}-{_MON[m.group(2)]:02d}-{int(m.group(1)):02d}T23:59:59Z" if m else ""


def observe_document(text: str, fid: str, captured_at: str) -> list[Obs]:
    out: list[Obs] = []
    emails = {norm(e) for e in EMAIL_RX.findall(text)}
    phones = {norm(p) for p in PHONE_RX.findall(text)}
    refs = set(USERREF_RX.findall(text))
    ids = frozenset(emails | phones | refs)

    # retention registers list orders and user refs but carry no resource ids -> nothing citable, skip
    if re.search(r"ORD-\d{8}\s*\|\s*user_\d+", text):
        return out

    # compliance journal: audit event reference with subject + retain-until
    m = re.search(r"Event reference (audit_record_\d+)", text)
    if m:
        until = re.search(r"retain until ([^\n]+?)(?:\s{3,}|\n)", text)
        out.append(Obs(fid, captured_at, m.group(1), "PRESENT", idents=frozenset(refs), kind="record",
                       attrs={"retention_until": _date(until.group(1)) if until else "", "doc": "compliance_journal"}))
        return out

    # customer statement (user export document)
    m = re.search(r"Statement NST-\d{4}-\d{4}-(\d+)", text)
    if m:
        out.append(Obs(fid, captured_at, f"export_{int(m.group(1))}", "PRESENT", idents=ids, kind="record",
                       attrs={"doc": "statement"}))
        return out

    # support attachment: "reference CS-4827 / support_001"
    for rid in sorted({m.group(0) for m in RID_RX.finditer(text)}):
        cat = "SUPPORT_ATTACHMENT" if rid.startswith("support") and re.search(r"attachment|file ", text, re.I) else ""
        out.append(Obs(fid, captured_at, rid, "EXPOSED" if emails | phones else "PRESENT", cat=cat, idents=ids,
                       kind="record", attrs={"doc": "attachment"}))
    return out
