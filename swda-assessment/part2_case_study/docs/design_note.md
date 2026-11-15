# Part 2 - Design Note (Deliverable B)

Diagrams: [architecture.md](architecture.md). Run it: `make all` from the repo root (or `collusion-sim run --scenario baseline`).

## 1. What this is, and is not
A thin, testable slice of a multi-agent *simulation* that turns Annex A (MO library) and Annex B (social excerpts) into **structured, hypothetical collusion-pattern cards for human review**. It is not a detector, not evidence about any real party, and not a fraud-simulation platform. Everything an agent says is labelled `SIMULATED`; source text is copied from the annexes by code; assumptions are listed separately; only a human can approve.

## 2. Design choices
1. **Orchestrator is code, not an LLM.** Rounds, routing, limits, retries, cost, quarantine and hand-off are deterministic and unit-tested; an LLM orchestrator could be talked out of its limits.
2. **Agents emit a typed `ActorTurn`, not prose.** Behaviour is described with a **controlled vocabulary** (`taxonomy.yaml` capability tags) plus a capped one-line summary. This keeps output at risk-pattern level, makes classification and removal checks computable, and makes evaluation exact.
3. **Boundaries live in code.** An actor may only use capability tags in its profile, cite only sources it can see, send flows from itself, and call only `lookup_source`/`lookup_mo` on visible IDs (`validation.py`, `tools.py`). Violations are rejected, traced as `boundary_violation`, and retried with an error code (never echoing content).
4. **Cards are assembled by code** (`patterns.py`) from validated fields. The Review Agent adds commentary, but the **authoritative checks are deterministic**: references resolve, actor-removal, MO similarity, source-support score. Where the Review Agent disagrees with the computed value, the conservative computed value stands and the disagreement is shown to the human (`support_disagreement`).
5. **Approval is a separate code path** (`approval.py`): reviewer id must match `human:*` (agent ids can't), weak or new patterns need a written justification, cards with blocking issues (unreviewed, halted run, bad refs) can't be approved, and the card digest at decision time detects later edits.
6. **Config over code.** Profiles, vocabulary, thresholds, safeguard rules, limits, evidence weights, approval rules and eval tolerances are YAML.

## 3. Actor profiles
Goals, incentives, capability tags and visible information per actor are in `config/actors.yaml` (summary table in architecture B3). Information asymmetry is real, not cosmetic: the prompt for an actor contains **only** what its toolbox allows (invariant test `prompts_contain_only_visible_information`). Example: the Employer never sees S1-S4, so it cannot "know" about the provider-side complaints.

## 4. Controls mapped to the brief
| # | Requirement | Where / how |
|---|---|---|
| 1 | Actor profiles | `actors.yaml` |
| 2 | Controlled agent-to-agent messages | `messages` in the schema, length-capped, screened, delivered **one round later**, wrapped as `<peer_message>` data |
| 3 | Boundaries + least privilege in code | `validation.py`, `tools.py` (no approval/write/network tool exists) |
| 4 | Pattern with actors, contributions, benefits, flows | `Contribution.benefits/flows` -> card |
| 5 | Actor-removal + collusion vs single-actor | `patterns.removal_checks/classify_collusion`; system-level removal scenario `no_provider` |
| 6 | Source vs simulated vs assumption | three separate card sections; `basis` enum; cite-or-assume rule |
| 7 | MO classification + data needed | Jaccard of capability tags vs analyst-derived `mo_features` (thresholds in config) -> new / variation / known; data needs from capability + class |
| 8 | Schema handoffs, retries, round/cost limits | pydantic `extra="forbid"`, `max_retries`, `max_rounds`, `max_cost_units`, `max_failed_turns`, `max_quarantined_items` |
| 9 | Hidden instructions, unsafe outputs, human approval | annex scanned on load (poisoned text withheld), untrusted wrappers, regex screens for injection / operational detail / fact assertion / PII, quarantine, human gate |
| 10 | Traces, evals, regressions | hash-chained trace; evaluation harness (section 6) |

**Collusion vs single-actor logic.** A pattern's *essential* capabilities must be provided by contributors. If one actor alone covers them -> `single_actor_misconduct` (others redundant). If >=2 actors are each necessary **and** a flow links *contributing* actors -> `collusion`. Necessary-but-unlinked -> `parallel_misconduct` (no evidence of coordination). Else `redundant`. Flows to non-contributing actors don't count.

**Source support** (not "truth"): sum of source weights by annex basis (Observed 3, overseas 2, Policy risk 1, unverified social 1) minus a penalty per assumption; capped at *weak* if every source is unverified. Weights are judgment calls, hence config and human-visible.

## 5. Safeguards and limiting unrealistic or unsafe output
Structural first (vocabulary, caps, cite-or-assume, role boundaries, code-built cards, labels), then screens (rules in `safeguards.yaml`), then review (Review Agent + deterministic checks), then a **human**. Flagged content is quarantined (kept in the trace for investigators, never delivered or carded), and repeated hits halt the run. Honest limits: regexes miss paraphrase and can false-positive - the first run withheld MO-01 because "act as employers" matched an injection rule; it is now a regression test (`test_safeguards_do_not_flag_the_clean_annexes`). Production adds a classifier-based screen as a second layer.

## 6. Evaluation approach
- **Agent-level**: per role, first-attempt and final schema validity, boundary violations, tool denials (golden `roles:` expectations).
- **End-to-end**: per scenario, expected cards (classification, MO class, closest MO, support, necessary actors, blocking issues) and counters. Scenarios: `baseline`, `adversarial` (malformed JSON, hallucinated ID, out-of-role capability, injection in output and message, step-by-step output, fact assertion, out-of-visibility and ungranted tool calls, a review that tries to approve), `limits_cost` (halt + incomplete hand-off), `no_provider` (actor removal at system level).
- **Invariants on every run**: no agent approval, trace chain valid, limits respected, cards schema-valid and free of unsafe text, refs resolve and source text equals annex text, classification consistent with removal checks, prompts contain only visible info.
- **Regression**: card fingerprints and metrics vs `evals/baselines/baseline.json`; any change fails until a human runs `eval --update-baseline` and reviews the diff; cost may not rise > configured ratio; detection counters may not fall.
- **Meta-tests**: disable the safeguards, or widen an actor's capabilities -> the evals must fail (they do).

## 7. Observability
Hash-chained trace of every LLM call (prompt/response hashes, tokens, cumulative cost), validation failure, tool call, quarantine, delivery, review, card and human decision; `verify` detects tampering and post-approval card edits; `run_summary.json` carries per-role and run metrics (the hooks for dashboards/alerts in a real deployment).

## 8. Trade-offs: actor-based vs single-agent
| Aspect | Actor-based (chosen) | Single agent |
|---|---|---|
| Information asymmetry | Enforced per actor | One context sees all; can't test what each party could know |
| Attribution / removal test | Every contribution has an owner -> removal checks are meaningful | Contributions unattributed; "collusion" is just narrative |
| Injection blast radius | Contained per actor/tool set | One compromised context holds everything |
| Testability | Small role prompts, per-role metrics | One large prompt, hard to isolate failures |
| Cost / latency | N calls per round (bounded by caps) | 1 call; cheaper |
| Realism risk | LLM actors tend to cooperate/converge (mode collapse, sycophancy); "necessary" != "realistic" | Coherent but can invent tidy stories |
| Complexity | More code and more failure modes | Simple |
Use a single-agent run as the **baseline** to show the actor design adds value (section 9).

## 9. What the mock LLM cannot prove, and how to test quality with a real model
**Cannot prove** (the mock ignores prompts): that prompts elicit useful or role-faithful behaviour; real schema-adherence rates; resistance to injection (we prove the *screens catch* scripted hostile strings, not that a model wouldn't obey them, or that paraphrases evade regexes); plausibility, novelty, diversity; stability across samples; review-agent calibration; emergent multi-agent behaviour; real cost/latency. **Can prove**: orchestration, limits, validation/retry, permission enforcement, deterministic analysis, approval gating, trace integrity, harness mechanics.

**With a real model (same interface; pin model + params; record/replay cassettes keep CI deterministic):**
1. **Seeded ground truth.** Build synthetic worlds with a *planted* collusion pattern and negative controls (none planted). Measure pattern recovery (precision/recall vs plant) and false-pattern rate; withhold one MO from the library and measure **rediscovery**.
2. **Groundedness.** Every claim vs its cited IDs: LLM-judge + human audit of a stratified sample; track unsupported-claim and over-assertion rates.
3. **Plausibility and usefulness.** SME rubric (1-5) on a blinded sample, actor-based vs single-agent baseline vs random pattern generator; report with confidence intervals (>=30 runs per scenario).
4. **Stability and diversity.** N seeds at the production temperature: pattern agreement (Jaccard of tag sets), mode-collapse (distinct-pattern count), flake budget.
5. **Role adherence and leakage.** Canary strings in one actor's private sources must never surface in another's output; judge checks actors act on their incentives.
6. **Safety red-team.** Injection corpus (direct, indirect via annex, paraphrased, multilingual) and operational-detail elicitation; report leak rate and screen recall/precision; add a classifier screen.
7. **Review-agent calibration.** Agreement and kappa vs SME decisions; false-approve-recommendation rate is the key number.
8. **Operational.** First-attempt validity, retries, cost/latency distributions vs limits.
Thresholds are pre-registered in config; a model/prompt change must clear them (and the regression harness) before use.

## 10. Known limitations
Hand-written `mo_features` map and evidence weights are analyst judgments; regex screens are brittle; removal checks are structural, not behavioural; the mock review is scripted; reviewer identity is a string pattern (production: SSO claim); no concurrency/persistence beyond files.
