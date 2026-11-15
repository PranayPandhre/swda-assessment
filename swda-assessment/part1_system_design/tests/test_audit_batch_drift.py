import json

import pytest
from swda_agent_gateway.audit import AuditLog, verify_chain
from swda_agent_gateway.batch import run_batch
from swda_agent_gateway.drift import drift_status, psi
from swda_agent_gateway.evaluation import run_gate


def test_audit_chain_detects_edit_and_deletion(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl", "SWDA_AUDIT_HMAC_KEY")
    for i in range(4):
        log.record({"event": i})
    assert verify_chain(tmp_path / "a.jsonl")[0]
    lines = (tmp_path / "a.jsonl").read_text().splitlines()
    (tmp_path / "b.jsonl").write_text("\n".join(lines[:1] + [lines[1].replace('"event":1', '"event":9')] + lines[2:]) + "\n")
    assert not verify_chain(tmp_path / "b.jsonl")[0]
    (tmp_path / "c.jsonl").write_text("\n".join(lines[:1] + lines[2:]) + "\n")          # deleted record
    assert not verify_chain(tmp_path / "c.jsonl")[0]


def test_audit_chain_resumes_across_restarts(tmp_path):
    AuditLog(tmp_path / "a.jsonl", "SWDA_AUDIT_HMAC_KEY").record({"e": 1})
    AuditLog(tmp_path / "a.jsonl", "SWDA_AUDIT_HMAC_KEY").record({"e": 2})
    assert verify_chain(tmp_path / "a.jsonl") == (True, "2 records verified")


def test_audit_refuses_to_start_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("SWDA_AUDIT_HMAC_KEY")
    with pytest.raises(RuntimeError):
        AuditLog(tmp_path / "a.jsonl", "SWDA_AUDIT_HMAC_KEY")


def test_batch_is_idempotent_and_dead_letters_bad_rows(gw, tmp_path):
    src = tmp_path / "in.jsonl"
    src.write_text("\n".join([
        json.dumps({"request_id": "b1", "text": "need leadership training as a new manager"}),
        json.dumps({"request_id": "b2", "text": "retrenched need resume help", "age": 5}),   # violates schema
        "not json",
    ]) + "\n")
    out, dlq = tmp_path / "out.jsonl", tmp_path / "dlq.jsonl"
    a = run_batch(gw.runtime, "service-recommendation-agent", gw.redactor, src, out, dlq)
    b = run_batch(gw.runtime, "service-recommendation-agent", gw.redactor, src, out, dlq)   # re-run same hour
    assert a == {"processed": 1, "skipped_already_done": 0, "dead_lettered": 2}
    assert b["processed"] == 0 and b["skipped_already_done"] == 1
    assert len(out.read_text().splitlines()) == 1


def test_release_gate_passes_on_golden_set(gw):
    ok, report = run_gate(gw.s, gw.runtime)
    assert ok, report


def test_release_gate_fails_when_quality_regresses(gw, monkeypatch):
    la = gw.runtime.agents["service-recommendation-agent"]
    monkeypatch.setattr(la.agent, "classify_request", lambda text: {"category": "unclassified", "scores": {}, "ambiguous": False})
    la.agent.tools["classify_request"] = la.agent.classify_request
    ok, report = run_gate(gw.s, gw.runtime)
    assert not ok and not report["checks"]["category_accuracy"]


def test_psi():
    ref = {"a": 50, "b": 30, "c": 20}
    assert psi(ref, ref, 1e-4) == pytest.approx(0)
    shifted = {"a": 10, "b": 20, "c": 70}
    v = psi(ref, shifted, 1e-4)
    assert v > 0.25 and drift_status(v, 0.10, 0.25) == "alert" and drift_status(0.05, 0.10, 0.25) == "ok"
    assert psi(ref, {"a": 50, "b": 30, "c": 20, "new": 5}, 1e-4) > 0        # an unseen category is visible
