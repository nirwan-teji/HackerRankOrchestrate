"""Hybrid local retrieval (BM25 + optional LLM-free rerank) for corpus/RAG tasks."""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi

_TOK = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOK.findall(text.lower())


@dataclass
class Chunk:
    source: str
    text: str
    meta: dict


def load_chunks(root, exts=(".md", ".txt", ".html", ".json"), size=900, overlap=150) -> list[Chunk]:
    """Heading-aware chunking; folder names become metadata (e.g. product/domain)."""
    chunks = []
    for p in sorted(Path(root).rglob("*")):
        if not p.is_file() or p.suffix.lower() not in exts:
            continue
        text = p.read_text(errors="ignore")
        title = next((l.lstrip("# ").strip() for l in text.splitlines() if l.strip()), p.stem)
        step = size - overlap
        for i in range(0, max(len(text), 1), step):
            piece = text[i : i + size]
            if piece.strip():
                chunks.append(Chunk(str(p), f"{title}\n{piece}", {"domain": p.relative_to(root).parts[0], "title": title}))
    return chunks


class Index:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.bm25 = BM25Okapi([tokenize(c.text) for c in chunks])

    def search(self, query: str, k: int = 5, domain: str | None = None) -> list[tuple[Chunk, float]]:
        scores = self.bm25.get_scores(tokenize(query))
        order = sorted(range(len(scores)), key=lambda i: -scores[i])
        out = []
        for i in order:
            if domain and self.chunks[i].meta.get("domain") != domain:
                continue
            out.append((self.chunks[i], float(scores[i])))
            if len(out) == k:
                break
        return out
