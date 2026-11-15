from collusion_sim import patterns as P
from p2_helpers import acc


def test_collusion_needs_two_necessary_actors_and_a_flow_between_them():
    items = [acc("trainee", "claim_without_attendance"), acc("training_provider", "attendance_falsified", flows=[("training_provider", "trainee")])]
    label, _, suff = P.classify_collusion(items)
    assert label == "collusion" and suff == []


def test_parallel_misconduct_when_no_flow_links_actors():
    items = [acc("trainee", "claim_without_attendance"), acc("training_provider", "attendance_falsified")]
    assert P.classify_collusion(items)[0] == "parallel_misconduct"


def test_flow_to_a_non_contributing_actor_does_not_prove_coordination():
    items = [acc("trainee", "claim_without_attendance"), acc("training_provider", "attendance_falsified", flows=[("training_provider", "employer")])]
    assert P.classify_collusion(items)[0] == "parallel_misconduct"


def test_single_actor_misconduct_when_one_actor_covers_everything():
    items = [acc("training_provider", "overclaimed_training")]
    assert P.classify_collusion(items)[0] == "single_actor_misconduct"
    # a second actor with only a NON-essential contribution does not turn it into collusion
    items.append(acc("employer", "reciprocal_referral", essential=False, flows=[("employer", "training_provider")]))
    label, basis, suff = P.classify_collusion(items)
    assert label == "single_actor_misconduct" and suff == ["training_provider"] and "alone" in basis


def test_redundant_when_capability_is_provided_by_several_actors():
    items = [acc("trainee", "claim_without_attendance"), acc("training_provider", "claim_without_attendance")]
    checks = P.removal_checks(items)
    assert all(c.pattern_survives for c in checks)
    assert P.classify_collusion(items)[0] == "single_actor_misconduct"       # each one alone suffices


def test_removal_checks_report_missing_capabilities():
    items = [acc("trainee", "claim_without_attendance"), acc("training_provider", "attendance_falsified")]
    by = {c.removed_actor: c for c in P.removal_checks(items)}
    assert by["trainee"].missing_capabilities == ["claim_without_attendance"] and not by["trainee"].pattern_survives


def test_mo_classification_known_variation_new(settings):
    known = P.classify_mo([acc("training_provider", "overclaimed_training")], settings)
    var = P.classify_mo([acc("trainee", "claim_without_attendance"), acc("training_provider", "attendance_falsified"),
                         acc("training_provider", "cash_inducement")], settings)
    new = P.classify_mo([acc("training_provider", "misleading_course_bundling")], settings)
    assert (known.mo_class, known.closest_mo) == ("known", "MO-07")
    assert var.mo_class == "variation" and {"MO-03", "MO-04", "MO-05"} <= set(var.matched_mos)
    assert new.mo_class == "new" and new.closest_mo is None and new.novel_capabilities == ["misleading_course_bundling"]


def test_mo_thresholds_are_config_not_code(settings):
    tax = settings.taxonomy.model_copy(update={"mo_classification": {**settings.taxonomy.mo_classification, "known_threshold": 0.4}})
    s2 = settings.model_copy(update={"taxonomy": tax})
    items = [acc("trainee", "claim_without_attendance"), acc("training_provider", "attendance_falsified")]
    assert P.classify_mo(items, settings).mo_class == "variation" and P.classify_mo(items, s2).mo_class == "known"


def test_source_support_penalises_assumptions_and_caps_unverified(settings, lib):
    strong = [acc("trainee", "claim_without_attendance", refs=["MO-03", "MO-04", "MO-05"])]
    assert P.source_support(strong, 0, lib, settings)[0] == "strong"
    assert P.source_support(strong, 7, lib, settings)[0] == "weak"                      # many assumptions erode support
    social = [acc("trainee", "claim_without_attendance", refs=["S1", "S2", "S3", "S4", "S5"])]
    assert P.source_support(social, 0, lib, settings)[0] == "weak"                       # unverified-only cap
    assert P.source_support([acc("trainee", "claim_without_attendance")], 0, lib, settings)[0] == "weak"   # no refs


def test_data_needs_include_capability_and_class_specific_items(settings):
    needs = P.data_needs([acc("training_provider", "misleading_course_bundling")], "new", settings)
    assert {n.because for n in needs} == {"capability:misleading_course_bundling", "mo_class:new"}
