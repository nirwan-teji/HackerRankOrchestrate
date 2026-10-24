"""Dev helper: python scripts/explore_case.py case_05 -> identity, expected steps, matching evidence lines."""
import csv, glob, json, subprocess, sys
from pathlib import Path

D = Path(__file__).resolve().parents[1] / "dataset"
cid = sys.argv[1]
width = int(sys.argv[2]) if len(sys.argv) > 2 else 230
req = {r["case_id"]: r for r in csv.DictReader(open(D / "sample_deletion_requests.csv"))}[cid]
ids = list(csv.DictReader(open(D / "user_identities.csv")))
aud = list(csv.DictReader(open(D / "user_audit_log.csv")))
em = req["email"].lower()
u = next((r for r in ids if r["email"].lower() == em), None)
if not u:
    a = next(a for a in aud if a["old_value"].lower() == em or a["new_value"].lower() == em)
    u = next(r for r in ids if r["user_id"] == a["user_id"])
keys = {u["user_id"], u["email"].lower(), u["phone"], u["email_hash"], em}
for a in aud:
    if a["user_id"] == u["user_id"]:
        keys |= {a["old_value"].lower(), a["new_value"].lower()}
print(req)
print("USER", u["user_id"], u["name"], sorted(keys - {u["email_hash"]}))
out = {r["case_id"]: r for r in csv.DictReader(open(D / "sample_output.csv"))}[cid]
steps = json.loads(out["steps_json"])
print("EXPECTED", out["deletion_status"])
res = set()
for s in steps:
    print("  ", s["step_no"], s["phase"][:4], s["operation"], s["system_id"], s["resource_id"], s["state"], s["evidence_id"])
    res.add(s["resource_id"])
evf = {r["file_id"]: r for r in csv.DictReader(open(D / "evidence_files.csv"))}
needles = {k.lower() for k in keys} | res
for fid, r in evf.items():
    p = D / r["file_path"]
    if p.suffix in (".png",):
        continue
    t = subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True, text=True).stdout if p.suffix == ".pdf" else p.read_text(errors="ignore")
    hits = [l.strip() for l in t.splitlines() if any(n in l.lower() for n in needles)]
    if hits:
        print(f"-- {fid} {r['captured_at'][:16]} {p.name} ({len(hits)} lines)")
        for h in hits[:14]:
            print("     ", h[:width])
for r in csv.DictReader(open(D / "system_activity.csv")):
    if any(n in (r["resource_id"] + " " + r["details"]).lower() for n in needles):
        print("ACT", r["activity_id"], r["event_time"][:19], r["event_type"], r["target_system"], r["resource_id"], r["reported_result"], r["details"][:60])
