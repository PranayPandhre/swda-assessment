"""Human-readable markdown view of a card (generated from the JSON; the JSON is the record)."""
from __future__ import annotations

from .schemas import PatternCard


def card_markdown(c: PatternCard) -> str:
    L = [f"# {c.pattern_key}: {c.title}", "", f"**Status:** `{c.status}`  |  **Classification:** `{c.collusion_classification}` "
         f"({c.classification_basis})", "", f"> {c.disclaimer}", ""]
    if c.blocking_issues:
        L += [f"**Blocking issues (approval refused until resolved):** {', '.join(c.blocking_issues)}", ""]
    L += ["## Actors, contributions, benefits, flows", "| Actor | Round | Capability | Basis | Essential | Refs |", "|---|---|---|---|---|---|"]
    L += [f"| {i.actor} | {i.round} | {i.capability_label} | {i.basis} | {i.essential} | {', '.join(i.source_refs + i.assumption_ids) or '-'} |"
          for i in c.simulated_behaviour]
    L += ["", "Benefits: " + (", ".join(f"{b.to}←{b.kind}" for b in c.benefits) or "none declared"),
          "Flows: " + (", ".join(f"{f.from_actor}→{f.to} ({f.kind})" for f in c.flows) or "none declared"), ""]
    L += ["## Actor-removal checks", "| Removed | Pattern survives | Missing |", "|---|---|---|"]
    L += [f"| {r.removed_actor} | {r.pattern_survives} | {', '.join(r.missing_capabilities) or '-'} |" for r in c.removal_checks]
    if c.sufficient_single_actors:
        L.append(f"\nSingle actor(s) sufficient on their own: {', '.join(c.sufficient_single_actors)}")
    m = c.mo_classification
    L += ["", f"## MO classification: **{m.mo_class}** (closest {m.closest_mo}, similarity {m.similarity}; matched {m.matched_mos}; "
          f"novel elements {m.novel_capabilities})", "", "## Data needed for testing"]
    L += [f"- {d.dataset} -> {d.test}  _({d.because})_" for d in c.data_needed_for_testing]
    L += ["", "## Source material (copied from annexes by code)"] + [f"- **{s.id}** ({s.basis}): {s.text}" for s in c.source_material]
    L += ["", "## Simulated behaviour (NOT findings of fact)"] + [f"- [{i.actor}] {i.summary}" for i in c.simulated_behaviour]
    L += ["", "## Assumptions"] + ([f"- **{a.id}**: {a.text}" for a in c.assumptions] or ["- none"])
    r = c.review
    L += ["", "## Review", f"- Source support: **{r.source_support}** (score {r.source_support_score}); review agent opinion: "
          f"{r.agent_source_support_opinion} (disagreement: {r.support_disagreement})",
          f"- Review agent recommendation: {r.agent_recommendation}; MO opinion disagreement: {r.mo_disagreement}",
          f"- Counter-evidence refs: {r.counter_evidence_refs or 'none'}; concerns: {r.concerns or 'none'}"]
    L += ["", "## Human approval", f"- {c.approval.decision} by {c.approval.reviewer} ({c.approval.channel}): {c.approval.notes}"
          if c.approval else "- **Pending**: only a human reviewer can approve, reject or request changes."]
    return "\n".join(L) + "\n"
