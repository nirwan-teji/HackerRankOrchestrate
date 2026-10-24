"""PNG evidence (application screenshots) -> observations.

Pipeline: OCR/VLM text extraction is cached per file in vision_cache/<sha>.txt so a re-run is free and
deterministic; interpretation of the extracted text is plain regex code (no model in the decision path).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .model import EMAIL_RX, RID_RX, USERREF_RX, Obs, norm

CACHE = Path(__file__).resolve().parent / "vision_cache"


def extract_text(path: Path) -> str:
    """Return the screen text of an image: cached transcription, else OCR (rapidocr), else ''."""
    CACHE.mkdir(exist_ok=True)
    h = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    f = CACHE / f"{path.stem}.{h}.txt"
    if f.exists():
        return f.read_text()
    try:
        from rapidocr_onnxruntime import RapidOCR  # optional dependency

        res, _ = RapidOCR()(str(path))
        text = "\n".join(r[1] for r in (res or []))
    except Exception:
        return ""
    f.write_text(text)
    return text


def observe_image(path: Path, fid: str, captured_at: str) -> list[Obs]:
    text = extract_text(path)
    if not text:
        return []
    emails = {norm(e) for e in EMAIL_RX.findall(text)}
    refs = set(USERREF_RX.findall(text))
    out = []
    for rid in sorted({m.group(0) for m in RID_RX.finditer(text)}):
        out.append(Obs(fid, captured_at, rid, "PRESENT", idents=frozenset(emails | refs), kind="record", attrs={"doc": "screenshot"}))
    return out
