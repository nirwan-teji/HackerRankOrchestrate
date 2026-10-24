"""PNG evidence (application screenshots) -> observations.

Pipeline (no model in the decision path):
  1. OCR the screenshot once (RapidOCR, local/free); the word boxes are cached in vision_cache/<name>.<hash>.json so
     later runs are offline and byte-for-byte deterministic. A VLM can be plugged in via `GHOST_VISION=vlm`
     (see `vlm_transcribe`), but OCR is the default because these screens are clean, machine-rendered UIs.
  2. Boxes are grouped into table rows (same y) and the detail pane (right-hand key/value panel).
  3. Plain regex turns rows/panes into Obs: resource id + the identifiers shown with it + a state hint.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from .model import EMAIL_RX, Obs, norm

CACHE = Path(__file__).resolve().parent / "vision_cache"
RID = re.compile(r"(account|search|analytics|support|export|marketing|audit_record|backup|data_flow|tombstone|legal_hold|object)_(\d+)")
USER = re.compile(r"user_\d+")
PANE_X = 1420  # right-hand detail pane starts here in every console screenshot


def ocr_boxes(path: Path) -> list[dict]:
    CACHE.mkdir(exist_ok=True)
    h = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    f = CACHE / f"{path.stem}.{h}.json"
    if f.exists():
        return json.loads(f.read_text())
    try:
        from rapidocr_onnxruntime import RapidOCR

        res, _ = RapidOCR()(str(path))
    except Exception:  # OCR engine unavailable and nothing cached -> no observations from this image
        return []
    boxes = [{"x": round(min(p[0] for p in b)), "y": round(sum(p[1] for p in b) / 4), "t": t} for b, t, _ in (res or [])]
    f.write_text(json.dumps(boxes, indent=0))
    return boxes


def vlm_transcribe(path: Path) -> str:  # optional alternative backend, used only when GHOST_VISION=vlm
    from orchestrate.vision import image_part
    from orchestrate.llm import ask

    return ask("Transcribe every visible label and value of this admin console screenshot as plain text lines.",
               [{"type": "text", "text": "Screenshot:"}, image_part(path)], vision=True)


def _split_glued(text: str) -> str:
    """OCR glues neighbouring cells: 'user_20-analytics_7438-darwish.omar@x.com', '1data_flow_9846'."""
    text = re.sub(r"(?<=\d)[-·](?=[A-Za-z])", " ", text)
    text = re.sub(r"(?<=[a-z])_(\d+)[-·]", r"_\1 ", text)
    return text


def _find(text: str):
    text = _split_glued(text)
    rids = [m.group(0) for m in RID.finditer(text)]
    emails = {norm(e) for e in EMAIL_RX.findall(text)}
    users = set(USER.findall(text))
    phones = {norm(p) for p in re.findall(r"\+\d[\d ()\-]{6,}\d", text)}
    return rids, emails, users, phones


def _state(rid: str, text: str, has_identity: bool) -> str:
    low = text.lower()
    pre = rid.rsplit("_", 1)[0]
    if pre == "data_flow":
        return "DISABLED" if re.search(r"disabled|paused|stopped|inactive", low) else "ENABLED"
    if pre == "backup":
        return "BLOCKED" if re.search(r"blocked|restore denied|restore disabled", low) else "RESTORABLE"
    if pre in ("analytics", "support", "tombstone"):
        return "EXPOSED" if has_identity else "MASKED"
    return "PRESENT"


def observe_image(path: Path, fid: str, captured_at: str) -> list[Obs]:
    boxes = ocr_boxes(path)
    if not boxes:
        return []
    chrome_x = 240  # left navigation rail
    head = [b for b in boxes if b["x"] > chrome_x and 80 < b["y"] <= 330]
    body = [b for b in boxes if b["x"] > chrome_x and b["y"] > 330]
    pane = [b for b in body if b["x"] >= PANE_X]
    table = [b for b in body if b["x"] < PANE_X]

    def rows(items):
        items = sorted(items, key=lambda b: (b["y"], b["x"]))
        grp, last = [], None
        for b in items:
            if last is None or abs(b["y"] - last) > 9:
                grp.append([]); last = b["y"]
            grp[-1].append(b)
        return [" | ".join(x["t"] for x in sorted(g, key=lambda b: b["x"])) for g in grp]

    hrids, hemails, husers, hphones = _find(" ".join(rows(head)))
    page_ids = frozenset(hemails | husers | hphones)  # the customer/subject the whole page is about (if any)
    found: dict[str, Obs] = {}

    def put(rid, ident, text, doc):
        ident = frozenset(ident)
        st = _state(rid, text, bool(ident))
        if rid not in found or len(ident) > len(found[rid].idents):
            found[rid] = Obs(fid, captured_at, rid, st, idents=ident, kind="record", attrs={"doc": doc})

    for rid in dict.fromkeys(hrids):
        put(rid, page_ids, " ".join(rows(head)), "console-header")
    prow = rows(pane)
    ptxt = "\n".join(prow)
    prids, pemails, pusers, pphones = _find(ptxt)
    sel = _find(prow[0])[0] if prow else []
    for rid in dict.fromkeys(prids):
        own = frozenset(pemails | pusers | pphones) if rid in sel else page_ids
        put(rid, own or page_ids, ptxt, "console-detail")
    for r in rows(table):
        rids, emails, users, phones = _find(r)
        for rid in rids:
            if rid.startswith("audit_record") and not re.search(r"clos|delet|privacy|identity change|subject request", r, re.I):
                continue  # routine security events (logins, password changes) are not deletion-related records
            put(rid, frozenset(emails | users | phones) or page_ids, r, "console-row")
    return list(found.values())
