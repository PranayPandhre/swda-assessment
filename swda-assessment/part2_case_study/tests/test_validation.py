import json

import pytest
from collusion_sim.tools import ActorToolBox
from collusion_sim.validation import validate_review, validate_turn


def turn(actor="employer", rnd=1, **over):
    base = {"actor": actor, "round": rnd, "contributions": [{"pattern_key": "P1", "capability": "sham_employment", "summary": "x",
            "basis": "source", "source_refs": ["MO-06"]}]}
    base.update(over)
    return json.dumps(base)


@pytest.fixture
def check(settings, lib, sg):
    def _check(raw, actor="employer", rnd=1, known_assumptions=frozenset()):
        spec = settings.actors.actors[actor]
        vis = ActorToolBox(spec, lib, sg).visible_ids()
        return validate_turn(raw, actor_key=actor, round_no=rnd, spec=spec, visible_ids=vis, known_ids=set(lib.ids()),
                             known_assumptions=set(known_assumptions), settings=settings)
    return _check


def test_valid_turn(check):
    t, errs = check(turn())
    assert errs == [] and t.contributions[0].capability == "sham_employment"


@pytest.mark.parametrize("raw,expected", [
    ("not json {", "schema:invalid_json"),
    (turn(extra_field=1), "extra_forbidden"),
    (turn(actor="trainee"), "boundary:actor_identity_mismatch"),
    (turn(rnd=2), None),
    (turn(contributions=[{"pattern_key": "P1", "capability": "attendance_falsified", "summary": "x", "basis": "source", "source_refs": ["MO-06"]}]),
     "capability_outside_actor_role"),
    (turn(contributions=[{"pattern_key": "P1", "capability": "made_up", "summary": "x", "basis": "source", "source_refs": ["MO-06"]}]),
     "capability_not_in_taxonomy"),
    (turn(contributions=[{"pattern_key": "P1", "capability": "sham_employment", "summary": "x", "basis": "source", "source_refs": ["S99"]}]),
     "unknown_reference"),
    (turn(contributions=[{"pattern_key": "P1", "capability": "sham_employment", "summary": "x", "basis": "source", "source_refs": ["S1"]}]),
     "reference_not_visible_to_actor"),
    (turn(contributions=[{"pattern_key": "P1", "capability": "sham_employment", "summary": "x", "basis": "source"}]),
     "basis_source_without_reference"),
    (turn(contributions=[{"pattern_key": "P1", "capability": "sham_employment", "summary": "x", "basis": "assumption", "assumption_ids": ["A9"]}]),
     "undeclared_assumption"),
    (turn(contributions=[{"pattern_key": "bad", "capability": "sham_employment", "summary": "x", "basis": "simulated"}]), "pattern_key_format"),
    (turn(contributions=[{"pattern_key": "P1", "capability": "sham_employment", "summary": "y" * 999, "basis": "simulated"}]), "summary_length"),
    (turn(contributions=[{"pattern_key": "P1", "capability": "sham_employment", "summary": "x", "basis": "simulated",
                          "flows": [{"from": "trainee", "to": "employer", "kind": "cash"}]}]), "flow_endpoints"),
    (turn(contributions=[{"pattern_key": "P1", "capability": "sham_employment", "summary": "x", "basis": "simulated",
                          "flows": [{"from": "employer", "to": "employer", "kind": "cash"}]}]), "flow_endpoints"),
    (turn(contributions=[{"pattern_key": "P1", "capability": "sham_employment", "summary": "x", "basis": "simulated",
                          "flows": [{"from": "employer", "to": "trainee", "kind": "bitcoin"}]}]), "flow_kind"),
    (turn(messages=[{"to": "employer", "content": "hi"}]), "recipient"),
    (turn(messages=[{"to": "trainee", "content": "z" * 999}]), "messages[0].length"),
    (turn(messages=[{"to": "trainee", "content": "a"}] * 5), "too_many_messages"),
])
def test_validation_rejects(check, raw, expected):
    t, errs = check(raw)
    if expected is None:
        assert t is None and errs == ["schema:round_mismatch"]
    else:
        assert t is None and any(expected in e for e in errs), errs


def test_errors_never_echo_submitted_content(check):
    secret = "SUPER-SECRET-VALUE"
    _, errs = check(turn(contributions=[{"pattern_key": "P1", "capability": secret, "summary": "x", "basis": "source", "source_refs": [secret]}]))
    assert errs and secret not in json.dumps(errs)


def test_review_schema_cannot_express_approval(settings, lib):
    good = {"reviews": [{"pattern_key": "P1", "reference_comment": "a", "assumption_comment": "b", "source_support_opinion": "weak",
                         "closest_mo_opinion": None, "recommendation": "needs_more_evidence"}]}
    kw = dict(candidate_keys={"P1"}, known_ids=set(lib.ids()), mo_ids=set(lib.ids("annex_a_mo")), settings=settings)
    assert validate_review(json.dumps(good), **kw)[1] == []
    for forbidden in ({"approved": True}, {"decision": "approve"}):
        assert validate_review(json.dumps({**good, **forbidden}), **kw)[0] is None
    bad_rec = {"reviews": [{**good["reviews"][0], "recommendation": "approve"}]}
    assert validate_review(json.dumps(bad_rec), **kw)[0] is None
    assert any("pattern_key" in e for e in validate_review(json.dumps({"reviews": [{**good["reviews"][0], "pattern_key": "P9"}]}), **kw)[1])
    assert any("closest_mo_unknown" in e for e in validate_review(json.dumps({"reviews": [{**good["reviews"][0], "closest_mo_opinion": "MO-99"}]}), **kw)[1])
