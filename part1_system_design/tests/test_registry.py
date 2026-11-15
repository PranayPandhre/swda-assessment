import pytest
import yaml
from p1_helpers import edit_registry, seal
from swda_agent_gateway.models import Approval
from swda_agent_gateway.registry import AgentRegistry, RegistryError, transition


def reg(path, settings):
    return AgentRegistry(path, settings.limits.allowed_entrypoint_prefix)


def test_only_approved_and_valid_agents_are_exposed(settings):
    r = reg(settings.path("registry"), settings)
    assert [a.agent_id for a in r.exposed()] == ["service-recommendation-agent"]
    assert "status is draft" in "; ".join(r.exposure_problems(r.get("experimental-triage-agent")))


def test_tampered_record_is_not_exposed(registry_copy, settings):
    edit_registry(registry_copy, lambda a, raw: a.update(allowed_tools=["classify_request", "search_programmes", "rank_programmes", "delete_everything"]))
    r = reg(registry_copy, settings)                      # NOT re-sealed: digest no longer matches
    assert r.exposed() == []
    assert any("digest mismatch" in p for p in r.exposure_problems(r.records[0]))


def test_approved_record_that_violates_policy_is_not_exposed(registry_copy, settings):
    edit_registry(registry_copy, lambda a, raw: a.update(owner="sec-reviewer-1"))   # owner is also an approver
    seal(registry_copy, settings)
    r = reg(registry_copy, settings)
    assert r.exposed() == []
    assert any("separation of duties" in p for p in r.exposure_problems(r.records[0]))


def test_entrypoint_outside_prefix_refused(registry_copy, settings):
    edit_registry(registry_copy, lambda a, raw: a.update(entrypoint="os:system"))
    seal(registry_copy, settings)
    assert reg(registry_copy, settings).exposed() == []


def test_lifecycle_requires_full_approvals_and_eval_gate(settings):
    r = reg(settings.path("registry"), settings)
    draft = r.get("experimental-triage-agent")
    in_review = transition(draft, "in_review", r.policy)
    with pytest.raises(RegistryError, match="missing approvals"):
        transition(in_review, "approved", r.policy)
    approvals = [Approval(role=x, approver=f"{x}-person", at="2026-11-05T00:00:00Z", evidence="T-1")
                 for x in r.policy.required_roles]
    ready = in_review.model_copy(update={"approvals": approvals})
    with pytest.raises(RegistryError, match="evaluation gate"):
        transition(ready, "approved", r.policy)
    ready.eval_gate.passed = True
    assert transition(ready, "approved", r.policy).status == "approved"


def test_illegal_transition_rejected(settings):
    r = reg(settings.path("registry"), settings)
    with pytest.raises(RegistryError, match="illegal transition"):
        transition(r.get("experimental-triage-agent"), "approved", r.policy)    # draft -> approved skips review


def test_suspended_agent_is_withdrawn(registry_copy, settings):
    r = reg(registry_copy, settings)
    sus = transition(r.get("service-recommendation-agent"), "suspended", r.policy)
    raw = yaml.safe_load(open(registry_copy))
    raw["agents"][0] = yaml.safe_load(sus.model_dump_json())
    yaml.safe_dump(raw, open(registry_copy, "w"), sort_keys=False)
    assert reg(registry_copy, settings).exposed() == []
