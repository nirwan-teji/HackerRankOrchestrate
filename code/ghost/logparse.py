"""Normalise the many log dialects in dataset/evidence/logs into flat dicts.

Dialects seen: logfmt, JSON-after-timestamp, bracketed `[LVL] [svc] [trace] event: k="v"`,
pipe-separated, RFC5424-style syslog, and cloud-runtime lines whose payload is an escaped
`message="..."` string. Every parser returns the same shape: {ts, event, service, trace, **kv}.
"""
from __future__ import annotations

import json
import re

_TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\s+(.*)$")
_KV = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')


def _unq(v: str) -> str:
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return v


def kv_pairs(text: str) -> dict:
    return {k: _unq(v) for k, v in _KV.findall(text)}


def parse_line(line: str) -> dict | None:
    line = line.strip()
    m = _TS.match(line)
    if not m:
        return None
    ts, rest = m.groups()
    d: dict = {"ts": ts}
    if rest.startswith("{"):  # JSON payload
        try:
            obj = json.loads(rest)
        except ValueError:
            return None
        d.update({k: ("" if v is None else str(v)) for k, v in obj.items()})
        d["service"] = obj.get("service") or obj.get("logger") or ""
        d["trace"] = obj.get("trace_id", "")
        return d
    if rest.startswith("<"):  # syslog: <15>1 host app trace - - k=v...
        parts = rest.split(None, 7)
        d.update(kv_pairs(rest))
        d["service"] = parts[3] if len(parts) > 3 else ""
        d["trace"] = parts[4] if len(parts) > 4 else ""
        return d
    if rest.startswith("requestId="):  # cloud runtime export
        outer = kv_pairs(rest.split(" message=", 1)[0])
        msg = rest.split(" message=", 1)[1] if " message=" in rest else ""
        d.update(kv_pairs(_unq(msg.strip())))
        d["service"] = outer.get("function", "")
        d["trace"] = outer.get("requestId", "")
        d.setdefault("level", outer.get("level", ""))
        return d
    if rest.startswith("["):  # [LEVEL] [service] [trace] event: k="v"
        mm = re.match(r"\[(\w+)\]\s+\[([^\]]*)\]\s+\[([^\]]*)\]\s+(\w+):\s*(.*)$", rest)
        if not mm:
            return None
        d.update(kv_pairs(mm.group(5)))
        d.update(level=mm.group(1), service=mm.group(2), trace=mm.group(3), event=mm.group(4))
        return d
    if rest.startswith("|"):  # | LEVEL | service | trace | event | k=v
        f = [x.strip() for x in rest.lstrip("| ").split(" | ", 4)]
        if len(f) == 5:
            d.update(kv_pairs(f[4]))
            d.update(level=f[0], service=f[1], trace=f[2], event=f[3])
            return d
    d.update(kv_pairs(rest))  # logfmt
    d["trace"] = d.get("trace_id", "")
    return d


def parse_log(path) -> list[dict]:
    out = []
    for raw in open(path, encoding="utf-8", errors="ignore"):
        d = parse_line(raw)
        if d is not None:
            d["_raw"] = raw.strip()
            out.append(d)
    return out
