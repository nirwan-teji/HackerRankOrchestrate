import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT / "code" / "evaluation"))

from ghost import intent  # noqa: E402
from ghost.logparse import parse_line  # noqa: E402
from ghost.steps import Step, build_steps, to_json  # noqa: E402
from ghost.validate import case_key, validate_rows  # noqa: E402

DIALECTS = {
    "logfmt": '2026-09-15T11:56:57Z level=INFO service=account_service trace_id=tr-1 resource_id=account_9797 event=profile_lookup_completed rows=0 http_status=404',
    "bracket": '2026-07-28T05:51:41Z [WARN] [support_platform] [tr-1] message_pii_scan: resource_id="support_2322" matched_value="a@b.com"',
    "pipe": '2026-07-03T07:51:32Z | DEBUG | security_service | tr-1 | audit_record_read_completed | resource_id="audit_record_4413" rows="1"',
    "syslog": '2026-08-22T10:52:27Z <14>1 node-9 account_service tr-1 - - level=INFO resource_id="account_8656" event="profile_lookup_completed" rows="0"',
    "cloud": '2026-06-08T09:54:59Z requestId=tr-1 level=WARN function=support_platform message="resource_id=\\"support_5234\\" event=\\"attachment_pii_scan\\" matched_value=\\"x@y.com\\""',
    "json": '2026-09-10T08:56:17Z {"severity":"DEBUG","service":"account_service","trace_id":"tr-1","event":"profile_lookup_started","lookup_key":"x@y.com"}',
}


@pytest.mark.parametrize("name", DIALECTS)
def test_every_log_dialect_yields_event_trace_and_service(name):
    d = parse_line(DIALECTS[name])
    assert d["event"] and d["trace"] == "tr-1" and d["service"]


def test_syslog_fields_are_not_shifted():
    d = parse_line(DIALECTS["syslog"])
    assert d["service"] == "account_service" and d["resource_id"] == "account_8656"


def test_intent_rules():
    assert intent.rule_intent("Please erase the account linked to this email address.") == ("DELETE", True)
    assert intent.rule_intent("Je demande la suppression définitive du compte.")[0] == "DELETE"
    assert intent.rule_intent("I'd like to download my data. Please keep the account as it is.")[0] == "OTHER"
    assert intent.rule_intent("Please change the email address used for this account. I want to continue using the service.")[0] == "OTHER"
    assert intent.rule_intent("Can you tell me what personal information you hold about me? This is an information request, not a request to erase it.")[0] == "OTHER"


def test_step_order_phase_operation_system_resource():
    class R:  # minimal resource state
        def __init__(self, rid, system, cat, state, ev="e"):
            self.rid, self.system, self.cat, self.state, self.evidence = rid, system, cat, state, ev

    steps, status = build_steps([R("search_2", "search_service", "SEARCH_INDEX_ENTRY", "PRESENT"), R("search_1", "search_service", "SEARCH_INDEX_ENTRY", "PRESENT"),
                                 R("data_flow_1", "integration_service", "DATA_FLOW", "ENABLED"), R("account_1", "account_service", "USER_PROFILE", "ABSENT")])
    assert status == "INCOMPLETE"
    ops = [(s.operation, s.resource_id) for s in steps]
    assert ops == [("REPORT_REMAINING_RECORD", "search_1"), ("REPORT_REMAINING_RECORD", "search_2"), ("REPORT_ACTIVE_DATA_FLOW", "data_flow_1"),
                   ("DISABLE_DATA_FLOW", "data_flow_1"), ("DELETE_RECORD", "search_1"), ("DELETE_RECORD", "search_2"),
                   ("VERIFY_RECORD_ABSENT", "account_1"), ("VERIFY_RECORD_ABSENT", "search_1"), ("VERIFY_RECORD_ABSENT", "search_2"),
                   ("VERIFY_DATA_FLOW_DISABLED", "data_flow_1")]
    assert [s["step_no"] for s in to_json(steps)] == list(range(1, 11))


def test_natural_case_order():
    assert sorted(["case_100", "case_33", "case_9"], key=case_key) == ["case_9", "case_33", "case_100"]


def test_validator_rejects_bad_rows():
    bad = [{"case_id": "case_1", "deletion_status": "NOT_APPLICABLE", "steps_json": '[{"x":1}]'}]
    assert validate_rows(bad, ["case_1"])


def test_custom_scenarios_pass():
    from custom_cases import run_scenarios

    for r in run_scenarios(verbose=False):
        assert r["status_ok"] and r["steps_ok"], r["name"]


@pytest.mark.skipif(not (ROOT / "dataset" / "sample_output.csv").exists(), reason="dataset not present")
def test_public_sample_regression():
    from ghost.run import run
    from ghost.scoring import load, score

    _, _, rows = run(ROOT / "dataset", "sample_deletion_requests.csv")
    s = score({r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in rows}, load(ROOT / "dataset" / "sample_output.csv"))
    assert s["status_accuracy"] == 1.0 and s["step_f1"] >= 0.98


def test_every_citation_is_a_real_participant_visible_id():
    """Regression: provenance tags ("direct") once leaked into evidence_id, which the grader would reject."""
    import csv

    from ghost.run import run

    ds = ROOT / "dataset"
    if not (ds / "deletion_requests.csv").exists():
        pytest.skip("dataset not present")
    aud, _, rows = run(ds, "deletion_requests.csv")
    known = ({m["file_id"] for m in aud.ds.manifest} | {a["activity_id"] for a in aud.ds.activity}
             | {a["audit_id"] for a in aud.ds.audit_log})
    cited = {s["evidence_id"] for r in rows for s in json.loads(r["steps_json"]) if s["evidence_id"]}
    assert not (cited - known), sorted(cited - known)[:5]
    assert not validate_rows(rows, [r["case_id"] for r in rows], known)


def test_ambiguous_links_are_recorded_not_hidden():
    from ghost.run import run

    ds = ROOT / "dataset"
    if not (ds / "deletion_requests.csv").exists():
        pytest.skip("dataset not present")
    aud, _, _ = run(ds, "deletion_requests.csv")
    assert aud.ambiguities, "positional tie-breaks must be recorded as Ambiguity records"
    for a in aud.ambiguities:  # a group only exists when several cases were interchangeable
        assert len(a.case_ids) > 1 and a.chosen


# ---- structured-output fallback (regression for Groq gpt-oss rejecting function calling) -------------------------------
def _fake_llm(accepts: set[str], plain_reply: str = '{"authorises_deletion": true, "reason": "ok"}'):
    from langchain_core.messages import AIMessage
    from pydantic import BaseModel

    class Bad(Exception):
        status_code = 400

    class Runnable:
        def __init__(self, method):
            self.method = method

        def invoke(self, messages):
            if self.method not in accepts:
                raise Bad("Tool call validation failed: attempted to call tool 'json' which was not in request.tools")
            return {"raw": AIMessage(content="x"), "parsed": self.schema(authorises_deletion=True, reason="ok")}

    class Fake:
        calls = []

        def with_structured_output(self, schema, method="function_calling", include_raw=False):
            Fake.calls.append(method)
            r = Runnable(method); r.schema = schema
            return r

        def invoke(self, messages):
            return AIMessage(content="Sure! " + plain_reply)

    return Fake()


@pytest.mark.parametrize("accepts,expect_method", [({"json_schema"}, "json_schema"), ({"json_mode"}, "json_mode"), ({"function_calling"}, "function_calling"), (set(), "plain_json")])
def test_structured_output_falls_back_until_a_mode_works(accepts, expect_method):
    llm_mod = pytest.importorskip("orchestrate.llm")
    from langchain_core.messages import HumanMessage, SystemMessage
    from pydantic import BaseModel

    class Intent(BaseModel):
        authorises_deletion: bool
        reason: str

    llm_mod._WORKING.clear()
    fake = _fake_llm(accepts)
    out = llm_mod._structured(fake, Intent, [SystemMessage(content="s"), HumanMessage(content="u")], "m")
    assert out.authorises_deletion is True
    assert llm_mod._WORKING["m"] == expect_method


def test_bad_request_errors_are_not_retried():
    llm_mod = pytest.importorskip("orchestrate.llm")

    class E(Exception):
        status_code = 400

    class R429(Exception):
        status_code = 429

    assert not llm_mod._transient(E()) and llm_mod._transient(R429()) and llm_mod._transient(TimeoutError())


def test_every_manifest_file_is_committed_not_gitignored():
    """Regression: `*.log` in .gitignore once kept all 26 log evidence files out of the repo, so a fresh clone gave different answers."""
    import csv
    import subprocess

    man = ROOT / "dataset" / "evidence_files.csv"
    if not man.exists() or not (ROOT / ".git").exists():
        pytest.skip("needs the dataset and a git checkout")
    tracked = set(subprocess.run(["git", "ls-files", "dataset/evidence"], cwd=ROOT, capture_output=True, text=True).stdout.split())
    missing = [r["file_path"] for r in csv.DictReader(open(man)) if f"dataset/{r['file_path']}" not in tracked]
    assert not missing, f"{len(missing)} evidence files are not tracked by git, e.g. {missing[:3]}"


# ---- reviewer: advisory only -------------------------------------------------------------------------------------------
def _run_samples_with_fake_review(monkeypatch, replies):
    from ghost import review as R
    from ghost.run import run

    ds = ROOT / "dataset"
    if not (ds / "sample_deletion_requests.csv").exists():
        pytest.skip("dataset not present")

    class Out:
        def __init__(self, v, issues):
            self.verdict, self.issues = v, issues

    class Iss:
        def __init__(self, kind, rid, expl):
            self.kind, self.resource_id, self.explanation = kind, rid, expl

    def fake_review_case(aud, res):
        kind = replies(res)
        return R.Review(res.case_id, "concerns" if kind else "agree", kind or [])

    monkeypatch.setenv("GHOST_LLM_REVIEW", "1")
    monkeypatch.setenv("GHOST_REVIEW_SCOPE", "all")
    monkeypatch.setattr(R, "review_case", fake_review_case)
    return run(ds, "sample_deletion_requests.csv")


def test_reviewer_flags_never_change_the_answer(monkeypatch):
    from ghost.run import run

    ds = ROOT / "dataset"
    if not (ds / "sample_deletion_requests.csv").exists():
        pytest.skip("dataset not present")
    monkeypatch.delenv("GHOST_LLM_REVIEW", raising=False)
    _, _, base = run(ds, "sample_deletion_requests.csv")
    # a reviewer that objects to everything must still leave the output byte-identical
    aud, results, rows = _run_samples_with_fake_review(
        monkeypatch, lambda res: [{"kind": "other", "resource_id": res.resources[0].rid, "explanation": "x"}])
    assert rows == base
    assert any(r.review and r.review.verdict == "concerns" for r in results.values())


def test_reviewer_discards_flags_about_resources_it_was_never_shown(monkeypatch):
    llm_mod = pytest.importorskip("orchestrate.llm")
    from ghost import review as R
    from ghost.run import run

    ds = ROOT / "dataset"
    if not (ds / "sample_deletion_requests.csv").exists():
        pytest.skip("dataset not present")
    aud, results, _ = run(ds, "sample_deletion_requests.csv")
    res = next(r for r in results.values() if r.status == "INCOMPLETE")
    real = res.resources[0].rid

    class Iss:
        def __init__(self, rid): self.kind, self.resource_id, self.explanation = "other", rid, "x"

    class Out:
        verdict = "concerns"
        issues = [Iss("account_does_not_exist"), Iss(real)]

    monkeypatch.setattr(llm_mod, "ask", lambda *a, **k: Out())
    rv = R.review_case(aud, res)
    assert [i["resource_id"] for i in rv.issues] == [real]


def test_reviewer_scope_only_covers_uncertain_cases(monkeypatch):
    from ghost import review as R
    from ghost.run import run

    ds = ROOT / "dataset"
    if not (ds / "deletion_requests.csv").exists():
        pytest.skip("dataset not present")
    monkeypatch.delenv("GHOST_REVIEW_SCOPE", raising=False)
    aud, results, _ = run(ds, "deletion_requests.csv")
    scoped = [c for c, r in results.items() if R.in_scope(aud, r)]
    assert 0 < len(scoped) < len(results) // 2  # risky subset, not everything
    assert all(any(x.via in ("guess", "convention", "alias") or x.state == "UNKNOWN" for x in results[c].resources) for c in scoped)


def test_reviewer_prompt_names_every_category_the_engine_treats():
    """The reviewer re-derives the plan from the prompt's specification, so it must never drift from steps.py."""
    from ghost import review as R
    from ghost.steps import ANON_CATS, DELETE_CATS, REDACT_CATS, RETAIN_CATS

    for cat in DELETE_CATS | ANON_CATS | REDACT_CATS | RETAIN_CATS | {"BACKUP_ARCHIVE", "DATA_FLOW"}:
        assert cat in R.SYSTEM, cat


# ---- token-per-minute limiter (Groq free tier: ~30 requests/min but only ~8K tokens/min) -------------------------------
class _Clock:
    def __init__(self): self.t = 1000.0
    def now(self): return self.t
    def sleep(self, s): self.t += s


def test_token_window_blocks_until_the_minute_rolls_over():
    llm_mod = pytest.importorskip("orchestrate.llm")
    c = _Clock()
    w = llm_mod.TokenWindow(now=c.now, sleep=c.sleep)
    assert w.wait(3000, 8000) == 0           # empty window: free
    w.add(3000); c.t += 10
    assert w.wait(3000, 8000) == 0           # 6000 <= 8000
    w.add(3000); c.t += 10
    slept = w.wait(3000, 8000)               # 9000 > 8000: must wait for the oldest call (t=1000) to age out
    assert 39.9 < slept < 41                 # 60 s after the first call, 20 s have passed
    assert w.wait(3000, 8000) == 0


def test_token_window_never_deadlocks_on_an_oversized_request():
    llm_mod = pytest.importorskip("orchestrate.llm")
    c = _Clock()
    w = llm_mod.TokenWindow(now=c.now, sleep=c.sleep)
    w.add(500)
    assert w.wait(50_000, 8000) < 61         # a request bigger than the cap is waited for once, then allowed alone
    assert w.wait(10, 0) == 0                # cap 0 disables the limiter


def test_token_estimate_counts_text_images_and_expected_reply():
    llm_mod = pytest.importorskip("orchestrate.llm")
    from langchain_core.messages import HumanMessage, SystemMessage

    out = llm_mod.get_settings().est_out_tokens
    text = llm_mod._estimate_tokens([SystemMessage(content="x" * 3500), HumanMessage(content="y" * 700)])
    assert text == int(4200 / 3.5) + out
    img = llm_mod._estimate_tokens([HumanMessage(content=[{"type": "text", "text": ""}, {"type": "image_url", "image_url": {"url": "d"}}])])
    assert img == 800 + out


def test_intent_memo_is_per_configuration(monkeypatch):
    """Regression: final_run's intent gate (LLM on) leaked its labels into the reviewer gate (LLM off) via the memo."""
    from ghost import intent

    text = "I found an old report saying my account still existed. Please confirm that the profile is gone."
    monkeypatch.setattr(intent, "llm_intent", lambda t: "OTHER")
    monkeypatch.setenv("GHOST_LLM_INTENT", "1")
    assert intent.classify(text) == "OTHER"          # LLM layer on: model says not a deletion
    monkeypatch.setenv("GHOST_LLM_INTENT", "0")
    assert intent.classify(text) == "DELETE"         # LLM layer off: must be the rule answer, not the memoised LLM one


def test_output_uses_lf_line_endings_like_the_template(tmp_path):
    from ghost.run import run

    ds = ROOT / "dataset"
    if not (ds / "sample_deletion_requests.csv").exists():
        pytest.skip("dataset not present")
    out = tmp_path / "o.csv"
    run(ds, "sample_deletion_requests.csv", out)
    assert b"\r\n" not in out.read_bytes()
