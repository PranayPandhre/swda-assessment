from collusion_sim.patterns import Accepted
from collusion_sim.schemas import Contribution

FIXED = "2026-01-01T00:00:00+00:00"


def acc(actor, cap, key="P1", essential=True, refs=(), flows=(), basis="source", assum=(), rnd=1, seq=1):
    return Accepted(actor, rnd, seq, Contribution.model_validate({
        "pattern_key": key, "capability": cap, "summary": "s", "basis": basis, "source_refs": list(refs),
        "assumption_ids": list(assum), "essential": essential,
        "flows": [{"from": a, "to": b, "kind": "cash"} for a, b in flows]}))
