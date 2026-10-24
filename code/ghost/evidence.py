"""Turn every evidence file into uniform Obs records (see model.Obs).

All parsing is deterministic. PNG screenshots are handled by ghost.vision (OCR / VLM, cached).
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from collections import defaultdict
from pathlib import Path

from .logparse import parse_log
from .model import EMAIL_RX, HASH_RX, USERREF_RX, Obs, category_of, norm, system_of

LOOKUP_STATE = {
    "FOUND": "PRESENT", "NOT_FOUND": "ABSENT", "IDENTIFIABLE": "EXPOSED", "ANONYMIZED": "MASKED",
    "REDACTED": "MASKED", "DISABLED": "DISABLED", "BLOCKED": "BLOCKED",
}
IDENT_KEYS = ("lookup_key", "match_key", "subject_key", "subject_ref", "subject_reference", "correlation_key",
              "matched_value", "email", "customer_email", "owner_email", "requester_email", "requester_phone",
              "phone", "owner_phone", "email_hash", "customer_reference", "user_id", "lookup_value", "full_name",
              "requester_name", "name")
DIRECT_KEYS = ("email", "customer_email", "owner_email", "requester_email", "requester_phone", "phone",
               "owner_phone", "email_hash", "full_name", "requester_name", "name", "matched_value", "display_name")


def _clean(v):
    s = norm(v)
    return "" if s in ("no direct identifier", "[redacted]", "none", "nan") else s


def flat(d: dict) -> dict:
    """Merge one level of nested record dicts (e.g. search `document`, account `profile`) into the parent."""
    out = dict(d)
    for k, v in d.items():
        if isinstance(v, dict) and k in ("document", "profile", "record", "subject"):
            out.update({kk: vv for kk, vv in v.items() if kk not in out or out[kk] in (None, "")})
    return out


def idents_of(d: dict, keys=IDENT_KEYS) -> frozenset:
    d = flat(d)
    return frozenset(x for x in (_clean(d.get(k)) for k in keys) if x) | text_idents(d)


PHONE_RX = re.compile(r"\+\d[\d ()\-]{6,}\d")


def text_idents(d: dict) -> frozenset:
    """Emails/phones embedded in free-text fields (message bodies, excerpts)."""
    out = set()
    for k in ("message_text", "message_excerpt", "excerpt", "body", "note", "notes"):
        v = d.get(k)
        if isinstance(v, str):
            out |= {norm(e) for e in EMAIL_RX.findall(v)} | {norm(p) for p in PHONE_RX.findall(v)}
    return frozenset(out)


def has_direct(d: dict) -> bool:
    d = flat(d)
    return any(_clean(d.get(k)) for k in DIRECT_KEYS) or bool(text_idents(d))


def has_ref_or_direct(d: dict) -> bool:
    d = flat(d)
    return has_direct(d) or bool(_clean(d.get("customer_reference")) or _clean(d.get("subject_reference")))


def _b(v) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------------------------- logs
def parse_log_file(path, fid) -> list[Obs]:
    lines = parse_log(path)
    started: dict[str, dict] = {}  # trace -> started line (carries the lookup key)
    out: list[Obs] = []
    for ln in lines:
        ev, tr = ln.get("event", ""), ln.get("trace", "")
        if ev.endswith("_started") or ev.endswith("_check_started"):
            started[tr] = ln
            continue
        rid = ln.get("resource_id", "")
        key = ""
        if tr in started:
            s = started[tr]
            key = _clean(s.get("lookup_key") or s.get("match_key") or s.get("subject_key") or s.get("subject_ref"))
            if not rid:
                rid = s.get("resource_id", "")
        ids = set(idents_of(ln))
        if key:
            ids.add(key)
        t = ln["ts"]
        a = dict(ln)
        a["_key"] = key

        def add(state, extra_ids=(), rid_=None, **attrs):
            out.append(Obs(fid, t, rid_ or rid, state, idents=frozenset(ids | set(extra_ids)), attrs={**attrs, "event": ev}, kind="lookup"))

        if ev == "record_lookup":  # notification_delivery / search_index_diagnostic style
            o = _lookup_record(fid, {**ln, "recorded_at": t, "subject_reference": ln.get("subject_reference", "")}, "recorded_at")
            if o:
                _fix_result_state(o, ln)
                o.t, o.kind = t, "lookup"
                out.append(o)
        elif ev == "profile_lookup_completed":
            gone = ln.get("http_status") == "404" or ln.get("rows") == "0"
            add("ABSENT" if gone else "PRESENT")
        elif ev == "index_query_completed":
            add("PRESENT" if ln.get("result") == "hit" else "ABSENT")
        elif ev in ("analytics_profile_read_completed", "analytics_profile_read", "contact_lookup_completed"):
            if ev == "contact_lookup_completed":  # marketing contact profile (opt-in record) -> always deleted
                out.append(Obs(fid, t, rid, "PRESENT", cat="MARKETING_PROFILE", idents=frozenset(ids | {_clean(ln.get("correlation_key"))}), kind="lookup", attrs={"event": ev}))
            elif ln.get("direct_identifiers") == "0" or not has_ref_or_direct(ln):
                add("MASKED")
            else:
                add("EXPOSED")
        elif ev == "analytics_identity_scan":
            add("EXPOSED" if ln.get("direct_identifiers", "1") != "0" else "MASKED", extra_ids=[_clean(ln.get("matched_value"))])
        elif ev == "support_ticket_read_completed":
            add("EXPOSED" if has_direct(ln) else "MASKED")
        elif ev == "message_pii_scan":
            add("EXPOSED", extra_ids=[_clean(ln.get("matched_value"))])
        elif ev == "attachment_pii_scan":
            out.append(Obs(fid, t, rid, "EXPOSED", cat="SUPPORT_ATTACHMENT", idents=frozenset(ids | {_clean(ln.get("matched_value"))}),
                           kind="lookup", attrs={"event": ev, "file": ln.get("file", "")}))
        elif ev == "message_read":
            add("MASKED" if "redacted" in ln.get("message_text", "").lower() else "EXPOSED")
        elif ev == "contact_query_completed":  # legacy contact index = secondary identifier (always deleted)
            out.append(Obs(fid, t, rid, "PRESENT", cat="SECONDARY_IDENTIFIER", idents=frozenset(ids), kind="lookup", attrs={"event": ev}))
        elif ev == "deletion_marker_check":
            if ln.get("marker_found", "").lower() == "false":
                out.append(Obs(fid, t, rid, "PRESENT", cat="SECONDARY_IDENTIFIER", idents=frozenset(ids), kind="lookup", attrs={"event": ev, "last_write": ln.get("last_write", "")}))
        elif ev in ("connector_config_read_completed", "connector_config_loaded", "connector_heartbeat"):
            on = _b(ln.get("enabled")) and ln.get("scheduler_registered", "true") != "false"
            add("ENABLED" if on else "DISABLED", connector=ln.get("connector_name", ""))
        elif ev == "restore_policy_read_completed":
            add("RESTORABLE" if _b(ln.get("restore_allowed")) else "BLOCKED")
        elif ev == "restore_candidate_evaluated":
            add("RESTORABLE" if _b(ln.get("eligible")) and not _b(ln.get("deletion_block")) else "BLOCKED")
        elif ev == "restore_authorization_completed":
            add("BLOCKED" if ln.get("decision") == "deny" else "RESTORABLE")
        elif ev in ("audit_record_read_completed",):
            add("PRESENT", retention_until=ln.get("retention_until", ""))
        elif ev in ("export_metadata_read_completed", "export_download_check_completed"):
            add("PRESENT" if _b(ln.get("available", "true")) else "ABSENT")
    return [o for o in out if o.rid]


# ---------------------------------------------------------------------------------------------- SQL
def _bool_state(v, on, off):
    return on if v in (1, "1", True, "true") else off


def parse_sql_file(path, fid, captured_at) -> tuple[list[Obs], dict]:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(Path(path).read_text(errors="ignore"))
    obs: list[Obs] = []
    scope = {"systems": {}, "rids": set()}
    names = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    for tab in names:
        rows = [dict(r) for r in con.execute(f"select * from {tab}")]
        if tab == "snapshot_scope":
            for r in rows:
                scope["systems"][r["origin_system"]] = r["captured_at"]
            continue
        for r in rows:
            rid = r.get("resource_id", "")
            base = {"table": tab}
            if "record_json" in r:
                j = json.loads(r["record_json"])
                col, sysid = r["collection_name"], r["origin_system"]
                scope["systems"][sysid] = captured_at
                obs.extend(_collection_record(fid, captured_at, sysid, col, j, base))
                continue
            ref = _clean(r.get("customer_reference") or r.get("user_id"))
            if tab.startswith("customer_profiles"):
                scope["systems"]["account_service"] = captured_at
                obs.append(Obs(fid, captured_at, rid, "PRESENT", idents=idents_of({**r, "customer_reference": ref}), attrs=base))
            elif tab == "customer_search_documents":
                scope["systems"]["search_service"] = captured_at
                obs.append(Obs(fid, captured_at, rid, "PRESENT", idents=idents_of(r), attrs=base))
            elif tab == "search_documents_primary":
                scope["systems"]["search_service"] = captured_at
                src = r.get("source_resource_id", "")
                obs.append(Obs(fid, captured_at, rid, "PRESENT", idents=idents_of(r) | ({src} if src else set()), attrs={**base, "source_resource_id": src}))
            elif tab == "customer_analytics_profiles":
                scope["systems"]["analytics_platform"] = captured_at
                obs.append(Obs(fid, captured_at, rid, "EXPOSED" if has_ref_or_direct(r) else "MASKED", idents=idents_of(r), attrs=base))
            elif tab == "support_records":
                scope["systems"]["support_platform"] = captured_at
                obs.append(Obs(fid, captured_at, rid, "EXPOSED" if has_direct(r) else "MASKED", idents=idents_of(r), attrs={**base, "excerpt": r.get("message_excerpt", "")}))
            elif tab == "customer_exports":
                scope["systems"]["export_service"] = captured_at
                obs.append(Obs(fid, captured_at, rid, "PRESENT", idents=idents_of(r), attrs=base))
            elif tab == "connector_subscriptions":
                scope["systems"]["integration_service"] = captured_at
                obs.append(Obs(fid, captured_at, rid, _bool_state(r["enabled"], "ENABLED", "DISABLED"), idents=idents_of(r), attrs={**base, "connector": r.get("connector_name", "")}))
            elif tab == "backup_restore_entries":
                scope["systems"]["backup_service"] = captured_at
                obs.append(Obs(fid, captured_at, rid, _bool_state(r["restore_allowed"], "RESTORABLE", "BLOCKED"), idents=idents_of(r), attrs=base))
            elif tab == "security_audit_records":
                scope["systems"]["security_service"] = captured_at
                obs.append(Obs(fid, captured_at, rid, "PRESENT", idents=idents_of(r), attrs={**base, "retention_until": r.get("retention_until", "")}))
    for o in obs:
        scope["rids"].add(o.rid)
    return obs, scope


def _collection_record(fid, t, sysid, col, j, base) -> list[Obs]:
    rid = j.get("resource_id", "")
    ids = idents_of(j)
    if col == "customer_profiles":
        return [Obs(fid, t, rid, "PRESENT", idents=ids, attrs=base)]
    if col == "customer_analytics_reports":
        return [Obs(fid, t, rid, "EXPOSED" if has_ref_or_direct(j) else "MASKED", idents=ids, attrs=base)]
    if col == "customer_search_documents":
        return [Obs(fid, t, rid, "PRESENT", idents=ids, attrs=base)]
    if col == "support_conversations":
        return [Obs(fid, t, rid, "EXPOSED" if has_direct(j) else "MASKED", idents=ids, attrs=base)]
    if col == "ticket_attachments":
        return [Obs(fid, t, rid, "EXPOSED" if has_direct(j) or j.get("contains_pii") else "PRESENT", cat="SUPPORT_ATTACHMENT", idents=ids, attrs={**base, **{k: v for k, v in j.items() if k not in IDENT_KEYS}})]
    if col == "customer_export_jobs":
        return [Obs(fid, t, rid, "PRESENT" if j.get("download_available", True) else "ABSENT", idents=ids, attrs=base)]
    if col == "connector_configurations":
        return [Obs(fid, t, rid, "ENABLED" if _b(j.get("enabled")) else "DISABLED", idents=ids, attrs={**base, "connector": j.get("connector_name", "")})]
    if col == "backup_catalog":
        return [Obs(fid, t, rid, "RESTORABLE" if _b(j.get("restore_allowed")) else "BLOCKED", idents=ids, attrs=base)]
    if col in ("security_audit_events", "retained_order_records"):
        return [Obs(fid, t, rid, "PRESENT", idents=ids, attrs={**base, "retention_until": j.get("retention_until", "")})]
    if col == "deleted_customer_references":
        return [Obs(fid, t, rid, "EXPOSED" if has_direct(j) else "MASKED", idents=ids, attrs=base)]
    return [Obs(fid, t, rid, "PRESENT", idents=ids, attrs=base)] if rid else []


# ---------------------------------------------------------------------------------------------- JSON
def _lookup_record(fid, d, tkey) -> Obs | None:
    rid = d.get("resource_id")
    if not rid:
        return None
    res = d.get("result", "FOUND")
    ids = idents_of(d)
    return Obs(fid, d.get(tkey, ""), rid, LOOKUP_STATE.get(res, "PRESENT" if res != "ACTIVE" else "ENABLED"),
               system=d.get("system", ""), cat=d.get("record_type", ""), idents=ids, kind="lookup",
               attrs={"result": res})


def parse_json_file(path, fid, captured_at) -> tuple[list[Obs], dict]:
    j = json.loads(Path(path).read_text())
    obs: list[Obs] = []
    scope = {"systems": {}, "rids": set()}
    if "snapshots" in j:  # records-assurance bundle
        for s in j["snapshots"]:
            t = s.get("captured_at", captured_at)
            scope["systems"][s["data_store"]] = t
            for r in s["records"]:
                if s["collection"] == "operational_record_history":
                    o = _lookup_record(fid, r, "recorded_at")
                    if o:
                        _fix_result_state(o, r)
                        obs.append(o)
                    continue
                obs.extend(_collection_record(fid, t, s["data_store"], s["collection"], r, {"collection": s["collection"]}))
    elif "service_exports" in j:  # per-service read-back export
        se = j["service_exports"]
        for r in se.get("account_service", {}).get("profile_lookups", []):
            p = r.get("profile")
            ids = idents_of(p) if p else frozenset()
            obs.append(Obs(fid, r["observed_at"], r["resource_id"], "PRESENT" if (p or r.get("status_code") == 200) else "ABSENT", idents=ids, kind="lookup"))
        for r in se.get("analytics_platform", {}).get("profiles", []):
            obs.append(Obs(fid, r["observed_at"], r["resource_id"], "EXPOSED" if has_ref_or_direct(r) else "MASKED", idents=idents_of(r), kind="lookup"))
        for r in se.get("backup_service", {}).get("restore_entries", []):
            obs.append(Obs(fid, r["observed_at"], r["resource_id"], "RESTORABLE" if r.get("restore_allowed") else "BLOCKED", idents=idents_of(r), kind="lookup"))
        for r in se.get("export_service", {}).get("exports", []):
            obs.append(Obs(fid, r["observed_at"], r["resource_id"], "PRESENT", idents=idents_of(r), kind="lookup"))
        for r in se.get("integration_service", {}).get("connectors", []):
            obs.append(Obs(fid, r["observed_at"], r["resource_id"], "ENABLED" if r.get("enabled") else "DISABLED", idents=idents_of(r), kind="lookup", attrs={"connector": r.get("connector_name", "")}))
        for r in se.get("search_service", {}).get("index_queries", []):
            docs = r.get("hits", {}).get("documents", [])
            ids = set(idents_of({"q": r.get("query")}, ("q",)))
            for d in docs:
                ids |= idents_of(d)
            obs.append(Obs(fid, r["observed_at"], r["resource_id"], "PRESENT" if docs or r.get("hits", {}).get("total") else "ABSENT", idents=frozenset(ids), kind="lookup"))
        for r in se.get("security_service", {}).get("audit_records", []):
            obs.append(Obs(fid, r["observed_at"], r["resource_id"], "PRESENT", idents=idents_of(r), kind="lookup", attrs={"retention_until": r.get("retention_until", "")}))
        for r in se.get("support_platform", {}).get("tickets", []):
            obs.append(Obs(fid, r["observed_at"], r["resource_id"], "EXPOSED" if has_direct(r) else "MASKED", idents=idents_of(r), kind="lookup", attrs={"excerpt": r.get("message_excerpt", "")}))
    elif "collection" in j and "records" in j:  # export-jobs / analytics report style
        t = j.get("captured_at", captured_at)
        for r in j["records"]:
            obs.extend(_collection_record(fid, t, "", j["collection"], r, {"collection": j["collection"]}))
    elif "records" in j:  # manifest / ledger style (record_type/result)
        for r in j["records"]:
            o = _lookup_record(fid, r, "recorded_at")
            if o:
                _fix_result_state(o, r)
                obs.append(o)
    for o in obs:
        scope["rids"].add(o.rid)
    return obs, scope


def _fix_result_state(o: Obs, r: dict) -> None:
    res = r.get("result")
    if res == "ACTIVE":
        o.state = "RESTORABLE" if o.cat == "BACKUP_ARCHIVE" else "ENABLED"


# ---------------------------------------------------------------------------------------------- TXT / PDF
_TXT = re.compile(r"^- (\S+) \| (\w+) \| (\S+) \| (\w+) \| (\w+) \| (.*)$")


def parse_txt_file(path, fid, captured_at) -> list[Obs]:
    out = []
    for line in Path(path).read_text().splitlines():
        m = _TXT.match(line.strip())
        if not m:
            continue
        t, system, rid, rtype, result, ident = m.groups()
        d = {"result": result, "record_type": rtype, "system": system, "resource_id": rid, "subject_reference": ident, "recorded_at": t}
        o = _lookup_record(fid, d, "recorded_at")
        _fix_result_state(o, d)
        out.append(o)
    return out


def pdf_text(path) -> str:
    """poppler's pdftotext when installed (best layout fidelity), else the pure-python pypdf fallback."""
    try:
        out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
        if out.strip():
            return out
    except (OSError, subprocess.CalledProcessError):
        pass
    from pypdf import PdfReader

    return "\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)


def parse_pdf_file(path, fid, captured_at) -> list[Obs]:
    """PDFs are business documents: extract resource ids + identifiers they expose (see ghost.documents)."""
    from .documents import observe_document

    return observe_document(pdf_text(path), fid, captured_at)


# ---------------------------------------------------------------------------------------------- driver
def load_all(dataset_dir, manifest: list[dict]) -> tuple[list[Obs], dict[str, dict]]:
    """Returns (observations, scopes) for every evidence file in the manifest."""
    d = Path(dataset_dir)
    obs: list[Obs] = []
    scopes: dict[str, dict] = {}
    for row in manifest:
        p, fid, cap = d / row["file_path"], row["file_id"], row["captured_at"]
        ext = p.suffix.lower()
        if ext == ".log":
            o, sc = parse_log_file(p, fid), {"systems": {}, "rids": set()}
        elif ext == ".sql":
            o, sc = parse_sql_file(p, fid, cap)
        elif ext == ".json":
            o, sc = parse_json_file(p, fid, cap)
        elif ext == ".txt":
            o, sc = parse_txt_file(p, fid, cap), {"systems": {}, "rids": set()}
        elif ext == ".pdf":
            o, sc = parse_pdf_file(p, fid, cap), {"systems": {}, "rids": set()}
        elif ext == ".png":
            from .vision import observe_image

            o, sc = observe_image(p, fid, cap), {"systems": {}, "rids": set()}
        else:
            continue
        for x in o:
            if not x.t:
                x.t = cap
        sc["captured_at"] = cap
        obs.extend(o)
        scopes[fid] = sc
    return obs, scopes
