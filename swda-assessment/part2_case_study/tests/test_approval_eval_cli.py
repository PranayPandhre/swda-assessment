import json

import pytest
import yaml
from collusion_sim.approval import ApprovalRefused, apply_decision, load_card, verify_card_integrity
from collusion_sim.cli import main
from collusion_sim.evaluation import evaluate
from collusion_sim.runner import apply_decisions_file, run_scenario
from collusion_sim.trace import verify_trace
from p2_helpers import FIXED


@pytest.fixture
def run_dir(settings, tmp_path):
    out = tmp_path / "run"
    run_scenario("baseline", out, settings, clock=lambda: FIXED, run_id="t")
    return out


def decide(run_dir, settings, card="P1", **kw):
    args = dict(reviewer="human:a.b", decision="approve", notes="reasoned", channel="test", settings=settings, clock=lambda: FIXED,
                trace_path=run_dir / "trace.jsonl")
    args.update(kw)
    return apply_decision(run_dir / "cards" / f"{card}.json", **args)


def test_human_can_approve_and_decision_is_traced(run_dir, settings):
    card = decide(run_dir, settings)
    assert card.status == "approved" and card.approval.reviewer == "human:a.b"
    assert load_card(run_dir / "cards" / "P1.json").status == "approved"
    assert verify_trace(run_dir / "trace.jsonl")[0]
    assert json.loads((run_dir / "trace.jsonl").read_text().splitlines()[-1])["type"] == "approval_decision"


@pytest.mark.parametrize("reviewer", ["agent:review", "agent:trainee", "review", "human:", "HUMAN:x", ""])
def test_agents_and_malformed_identities_cannot_decide(run_dir, settings, reviewer):
    with pytest.raises(ApprovalRefused, match="human identity"):
        decide(run_dir, settings, reviewer=reviewer)
    assert load_card(run_dir / "cards" / "P1.json").status == "pending_human_review"


def test_weak_or_new_patterns_need_written_justification(run_dir, settings):
    with pytest.raises(ApprovalRefused, match="justification"):
        decide(run_dir, settings, card="P4", notes="  ")                       # weak support + new MO
    assert decide(run_dir, settings, card="P4", notes="Approved for SME validation only").status == "approved"
    assert decide(run_dir, settings, card="P1", notes="").status == "approved"    # strong + variation: no note required


def test_blocking_issues_prevent_approval_but_not_rejection(settings, tmp_path):
    out = tmp_path / "adv"
    run_scenario("adversarial", out, settings, clock=lambda: FIXED, run_id="a")
    with pytest.raises(ApprovalRefused, match="not_reviewed"):
        decide(out, settings, card="P2")
    assert decide(out, settings, card="P2", decision="reject", notes="unreviewed").status == "rejected"


def test_invalid_decision_and_double_decision_refused(run_dir, settings):
    with pytest.raises(ApprovalRefused, match="decision must be"):
        decide(run_dir, settings, decision="auto_approve")
    decide(run_dir, settings)
    with pytest.raises(ApprovalRefused, match="already"):
        decide(run_dir, settings, decision="reject")


def test_edit_after_approval_is_detected(run_dir, settings):
    decide(run_dir, settings)
    p = run_dir / "cards" / "P1.json"
    assert verify_card_integrity(load_card(p))
    doc = json.loads(p.read_text())
    doc["title"] = "Quietly edited after sign-off"
    p.write_text(json.dumps(doc))
    assert not verify_card_integrity(load_card(p))
    assert main(["verify", "--run-dir", str(run_dir)]) == 2


def test_decisions_file_reports_refusals(run_dir, settings, tmp_path):
    f = tmp_path / "d.yaml"
    f.write_text(yaml.safe_dump({"decisions": [{"card": "P1", "decision": "approve", "reviewer": "agent:review", "notes": "x"},
                                               {"card": "P3", "decision": "reject", "reviewer": "human:z", "notes": "n"}]}))
    rep = apply_decisions_file(run_dir, f, settings, lambda: FIXED)
    assert "REFUSED" in rep[0] and rep[1].startswith("P3: reject")
    assert (run_dir / "cards" / "P3.md").read_text().count("rejected") >= 1


# ---------------------------------------------------------------- evaluation harness
def test_evaluation_passes_on_committed_baseline(settings):
    rep = evaluate(settings)
    assert rep.ok, [c for c in rep.checks if not c.passed]
    assert len(rep.checks) > 100


def test_regression_check_catches_changed_cards(sandbox):
    b = sandbox.path(sandbox.evaluation["baseline_path"])
    doc = json.loads(b.read_text())
    doc["baseline"]["fingerprints"]["P1"] = "0" * 64
    b.write_text(json.dumps(doc))
    failed = [c.name for c in evaluate(sandbox).checks if not c.passed]
    assert failed == ["regression/baseline/card_fingerprints_unchanged"]


def test_regression_check_catches_metric_and_cost_regressions(sandbox):
    b = sandbox.path(sandbox.evaluation["baseline_path"])
    doc = json.loads(b.read_text())
    doc["adversarial"]["metrics"]["quarantined"] = 9            # safeguards used to catch more than they do now
    doc["baseline"]["metrics"]["cost_units"] = 1.0              # cost has more than doubled
    b.write_text(json.dumps(doc))
    failed = {c.name for c in evaluate(sandbox).checks if not c.passed}
    assert failed == {"regression/adversarial/quarantined_not_lower", "regression/baseline/cost_units_not_higher"}


def test_missing_baseline_fails_closed_and_update_creates_it(sandbox):
    b = sandbox.path(sandbox.evaluation["baseline_path"])
    b.unlink()
    assert not evaluate(sandbox).ok
    assert evaluate(sandbox, update_baseline=True).ok and b.exists()


def test_mutation_disabling_safeguards_is_caught_by_evals(sandbox):
    """Meta-test: the harness must fail when a control is silently removed."""
    off = sandbox.safeguards.model_copy(update={"rules": {k: [] for k in sandbox.safeguards.rules}})
    failed = {c.name for c in evaluate(sandbox.model_copy(update={"safeguards": off})).checks if not c.passed}
    assert "adversarial/count/quarantined" in failed
    assert any(n.startswith("adversarial/inv/cards_free_of_unsafe_text") for n in failed) or "adversarial/count/quarantined" in failed


def test_mutation_letting_actors_exceed_their_role_is_caught(sandbox):
    spec = sandbox.actors.actors["employer"].model_copy(update={"capabilities": sandbox.actors.actors["employer"].capabilities + ["attendance_falsified"]})
    mutated = sandbox.model_copy(update={"actors": sandbox.actors.model_copy(update={"actors": {**sandbox.actors.actors, "employer": spec}})})
    failed = {c.name for c in evaluate(mutated).checks if not c.passed}
    assert "adversarial/count/boundary_violations" in failed


# ---------------------------------------------------------------- CLI
def test_cli_run_and_eval(tmp_path, capsys):
    assert main(["run", "--scenario", "baseline", "--out", str(tmp_path / "o"), "--decisions", "scenarios/human_decisions_demo.yaml"]) == 0
    out = capsys.readouterr().out
    assert "P1: approve by human:j.tan" in out and (tmp_path / "o" / "trace.jsonl").exists()
    assert main(["eval"]) == 0
