# SWDA ML/AI Engineer

Two parts, one repo, one command: **`make all`** (creates a venv, installs, runs all tests, both evaluation gates and both demos).

| | Deliverable A: architecture | B: design doc/note | C: hands-on code |
|---|---|---|---|
| **Part 1** System design + MCP agent | [docs/architecture.md](part1_system_design/docs/architecture.md) (6 diagrams) | [docs/design_document.md](part1_system_design/docs/design_document.md) | `part1_system_design/src/swda_agent_gateway` |
| **Part 2** Collusion simulation | [docs/architecture.md](part2_case_study/docs/architecture.md) (2 diagrams) | [docs/design_note.md](part2_case_study/docs/design_note.md) | `part2_case_study/src/collusion_sim` |

Read first: Part 1 design doc sections 1, 10, 12, 15; Part 2 design note sections 2, 4, 9. AI usage declaration: [AI_USAGE.md](AI_USAGE.md). Walkthrough prompts: [WALKTHROUGH.md](WALKTHROUGH.md).

## Quick start
```bash
make all                 # setup + test + gates + demos      (Python >= 3.10)
# or piecewise
make setup && make test
.venv/bin/swda-gateway demo                       # Part 1: MCP over stdio (auth, redaction, audit)
.venv/bin/swda-gateway gate                       # Part 1: release gate, non-zero exit on failure
.venv/bin/swda-gateway serve-http                 # Part 1: HTTP transport (/mcp, /healthz, /readyz, /metrics)
.venv/bin/collusion-sim run --scenario baseline --out outputs/latest      # Part 2: cards stay pending
.venv/bin/collusion-sim review --run-dir outputs/latest                   # Part 2: interactive HUMAN decisions
.venv/bin/collusion-sim eval                      # Part 2: evals + regression vs committed baseline
```
Part 1 needs `SWDA_AUDIT_HMAC_KEY` in the environment (the Makefile sets a demo value; the gateway refuses to start without one). Demo client tokens: `demo-token-portal` (entitled), `demo-token-analyst` (not entitled). Sample Part 2 output: `part2_case_study/outputs/sample/` (cards `.json/.md`, `trace.jsonl`, `run_summary.json`).

## Part 1 - what the code does
A registry-driven **MCP gateway** (JSON-RPC 2.0; stdio + HTTP; verified against the official MCP Python SDK client) exposing one approved **Service Recommendation Agent** (deterministic tools: classify -> search -> rank; synthetic data).
Every call: authenticate -> scope check (unknown == unauthorised) -> rate limit -> size + JSON-Schema validation -> PII redaction -> allow-listed tools with timeout -> output PII check (fail closed) -> hash-chained audit + metrics.
Registry: approval state machine with separation of duties, digest-sealed manifests, exposure re-checked on load. Also: idempotent batch path with DLQ, golden-set release gate, PSI drift.

## Part 2 - what the code does
Orchestrator (code) runs rounds between Trainee / Training Provider / Employer agents (scripted mock LLM), validates every hand-off, enforces least privilege and information asymmetry, screens for hidden instructions and unsafe output, then a Review Agent + deterministic checks feed **pattern cards** (source / simulated / assumptions separated, actor-removal checks, collusion vs single-actor, MO class + data needed). Cards stay `pending_human_review` until a human decides. Eval harness: golden, invariants, regression baseline, meta-tests.

## Assumptions (full lists in the design docs)
Synthetic data only; Part 1 agent is rule-based so it demonstrates the control plane, not model quality; the MO-to-capability map and evidence weights in Part 2 are analyst judgments held in config; "human" approval is modelled by an id pattern (production: SSO). The Dockerfile base-image digest is a placeholder to pin in a real pipeline.

## Layout
```
part1_system_design/  config/ data/ docs/ src/swda_agent_gateway/ tests/
part2_case_study/     config/ data/ docs/ scenarios/ evals/ src/collusion_sim/ tests/ outputs/sample/
.github/workflows/ci.yml   Dockerfile   Makefile   pyproject.toml
```
