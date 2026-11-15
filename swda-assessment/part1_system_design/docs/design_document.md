# Part 1 - Design Document (Deliverable B)

Scope: take a prototype that classifies service requests and recommends programmes to a supportable production service on AWS +
Databricks: hourly batch first, near-real-time API and **governed MCP exposure of approved agents** later. Diagrams: [architecture.md](architecture.md).
Code: a thin vertical slice (MCP gateway + registry + one agent + gates) described in [section 15](#15-what-is-implemented-vs-designed).

## 0. Assumptions (state them, challenge them)

| # | Assumption | If wrong |
|---|---|---|
| A1 | Prototype is a text classifier + programme ranker; it may use an LLM or embeddings for classification later. The PoC uses deterministic rules so it can be tested exactly. | Add LLM-specific gates (prompt registry, guardrails, judge evals) - already designed in 5 and 6. |
| A2 | Inputs contain personal data (names, NRIC-like IDs, contact details) and are classed *restricted*. | Tighter controls (tokenisation at ingest) apply regardless; relax only with data-governance sign-off. |
| A3 | Hourly batch means "process requests received in the previous hour window"; a late or failed window must be re-runnable without duplicates. | If strict ordering is required, add per-key sequencing. |
| A4 | Recommendation errors are *medium* impact (wasted time, misdirected citizen), not safety-critical; a human (caseworker) stays in the loop for sensitive categories. | If outputs trigger entitlements automatically, raise to high-risk tier: mandatory human approval, stricter gates. |
| A5 | Data residency in Singapore (ap-southeast-1) and agency data-classification/IM8-style controls apply; PDPA obligations apply to personal data. | Region/controls change; architecture unchanged. |
| A6 | Team size is small (2-4 engineers): prefer managed services and fewer moving parts over bespoke platforms. | Larger team can afford a custom feature platform / serving stack. |
| A7 | "Agents" exposed over MCP are read-mostly decision-support agents. Agents that can *write* to enterprise systems are out of scope for v1 and would need a separate, stricter tier. | Add per-tool write scopes + human-in-the-loop confirmation. |

## 1. Design drivers (what the architecture optimises for, in order)

1. **Safety of sensitive data and of agent actions** - least privilege everywhere, fail closed.
2. **Auditability** - every decision reproducible: *which data, code, model/prompt version, config, caller* produced this output.
3. **Supportability** - an on-call engineer can diagnose, roll back and explain within minutes.
4. **Cost-consciousness** - hourly batch on right-sized, short-lived compute; budgets and quotas on anything that scales with usage (tokens, calls).
5. **Evolvability** - the same inference core serves batch today and online/MCP later, so the later phase is a new *door*, not a new *building*.

## 2. Component choices and justification

| Concern | Choice | Why this | Main alternative and why not (now) |
|---|---|---|---|
| Storage / tables | **S3 + Delta Lake**, bronze/silver/gold medallion | ACID, time travel (reproducibility + recovery), already the platform | Redshift: another store to govern; no extra benefit for this workload |
| Governance | **Unity Catalog** (catalog/schema ACLs, column masks, lineage, audit tables) | One place for data, features and model permissions + lineage that auditors can read | Lake Formation + hand-rolled lineage: weaker lineage for ML assets |
| Pipelines | **Databricks Workflows / Lakeflow** with data-quality *expectations* | Native retries, dependency graph, expectations quarantine bad rows, same runtime as training | Airflow/MWAA: extra platform to run; consider only for cross-system orchestration |
| Features | **UC feature tables**, point-in-time joins | Same feature code in training and batch -> removes train/serve skew; lineage included | Custom feature store: unjustified effort for v1 |
| Experiments, models | **MLflow** tracking + **UC Model Registry** with *aliases* (`candidate`, `champion`) | Run <-> data version <-> code SHA lineage; promotion/rollback = alias move (no redeploy) | SageMaker Registry: duplicates registry; keep if/when serving moves to SageMaker |
| Prompts / agent configs | **Versioned artefacts in Git + registered in MLflow as content-hashed artefacts**; pinned by hash in the agent manifest | Prompts are code: reviewed, diffed, rolled back; hash pin proves *which* prompt produced an output | Runtime-editable prompt console: fast but unauditable |
| Batch inference | **Databricks job (hourly)** reading `champion` alias; idempotent writes (`MERGE` on `request_id`) | Cheap (short-lived job cluster / serverless), simple failure semantics | Always-on endpoint for batch: pays 24x for 1x work |
| Future online serving | **Databricks Model Serving** for the model; **ECS Fargate** for the MCP gateway and agent runtime | Gateway is a thin stateless service (scale-to-load); model endpoint scales to zero in non-prod | Lambda for gateway: good for spiky traffic, but cold starts + stdio/streaming limits; revisit |
| MCP exposure | **Own thin gateway** (this repo) in front of approved agents; evaluate **Amazon Bedrock AgentCore Gateway** as a managed substitute | Registry-driven exposure, entitlement-filtered discovery, redaction and audit are *policy* that must match agency rules; a thin gateway keeps them testable | Managed gateway: less code to own, but verify it supports registry-digest checks, our audit sink and per-agent scopes before adopting |
| AuthN/Z | **Enterprise IdP via OAuth 2.1/OIDC** for HTTP MCP (MCP's HTTP authorization model); env-injected credential for stdio | Standard, revocable, short-lived tokens; scopes map 1:1 to agents | Static API keys: simplest, acceptable only for local/dev (the PoC uses hashed tokens) |
| Observability | **CloudWatch + OpenTelemetry**, Lakehouse Monitoring for data/model quality | Alerts where on-call already lives; one trace id from MCP call to agent tool | Self-hosted Prometheus/Grafana: more ops burden |
| Audit | Append-only, **hash-chained** records shipped to **S3 Object Lock (WORM)**; CloudTrail for control plane | Tamper-evident + immutable retention | Plain app logs: mutable, not evidence |
| Secrets / keys | **KMS CMKs per data domain**, **Secrets Manager**, short-lived IAM roles (OIDC from CI) | No long-lived keys in repos/CI | - |
| Network | VPC, private subnets, **VPC endpoints / PrivateLink** for S3, Databricks, Bedrock; no public egress from agent runtime | Exfiltration path for a compromised agent is closed by default | - |
| IaC / delivery | **Terraform + Databricks Asset Bundles**, GitHub Actions | Environments reproducible; reviewed infra changes | - |

## 3. Data and feature pipelines

- **Layers.** Bronze (raw, immutable, append-only) -> Silver (typed, deduplicated on `request_id`, **PII tokenised/redacted**, quality-checked) -> Gold (training + inference tables) -> Feature tables.
- **Quality gate between bronze and silver** (expectations): schema match, mandatory fields, value ranges, freshness (< 2h), volume within ±X% of trailing 7-day same-hour. Failing rows go to a *quarantine table* with reason; failing *volume/freshness* checks **fail the run** (no silent partial data). Thresholds are config, per dataset.
- **Point-in-time correctness.** Training joins features "as of" the request timestamp (no leakage); inference uses the same transformation code from the same package version.
- **PII.** Detect at ingest (managed classifier + regex like the PoC's), tokenise with a vault-held key; the model sees tokens/redacted text. Raw PII lives only in a restricted bronze schema with column masks and short retention.
- **Idempotency and late data.** Window = `[h-1, h)` by *event time*; writes are `MERGE` by `request_id`; a rerun of the same window is a no-op. Late arrivals are picked up by a `window + lateness` reprocess job.
- **Lineage.** UC lineage + run metadata (code SHA, data version) written with every batch output.

## 4. Experimentation, evaluation and model development

- **Reproducibility contract** for every run: *code SHA, data snapshot (Delta version/timestamp), feature definitions version, config hash, random seeds, package lock file, environment image digest* logged to MLflow. A run is "reproducible" if re-running it from those pins yields metrics within tolerance (tested in CI for the training smoke path).
- **Evaluation is layered** (cheapest first): (1) unit/property tests on code; (2) **golden set** (curated, versioned, stratified) with accuracy/hit-rate\@k thresholds; (3) **slice metrics** (programme category, age band, language register, channel) to catch regressions hidden by averages; (4) **safety/robustness set** (PII leakage, prompt-injection strings in free text, empty/garbage input -> graceful "unclassified" + human review); (5) offline replay of a recent real-shaped window vs. champion.
- **Thresholds are release gates, not dashboards**: defined in `config/release_gates.yaml` (implemented), changed only by reviewed PR.
- **Baselines first.** The deterministic rules model is kept as the permanent *fallback and challenger*: if an LLM/embedding model is introduced it must beat it on the golden set to be promoted, and the rules path is the degraded-mode answer during incidents.
- **Human labels** from caseworker corrections feed the golden set (after review) - never straight into training (guards against feedback loops).

## 5. Model, prompt and artefact management

| Artefact | Where | Identity | Promotion |
|---|---|---|---|
| Model weights/params | UC Model Registry | version + run id + data version | alias `candidate` -> `champion` via gate + approval |
| Prompts, tool schemas, agent config | Git (source) + hashed artefact in registry | content SHA-256 pinned in agent manifest | PR review + eval gate; manifest digest re-sealed |
| Datasets / golden sets | Delta tables, versioned | Delta version | PR-reviewed changes only |
| Container/wheel | ECR / package repo | digest + SBOM | built once in CI, promoted unchanged through environments |
| Agent manifest | Registry (this repo: `agent_registry.yaml`) | `manifest_sha256` over canonical manifest | approval workflow (A4) |

**Principle: build once, promote the same immutable artefact; config differs by environment, code never does.** Model cards (intended use, data, metrics by slice, limits, owner) are generated from the run and stored with the version.

## 6. CI/CD/CT automation and release gates

See diagram A2. Gates, in order, each failing the pipeline:

1. **CI**: lint, types, unit tests, SAST, dependency audit, secret scan, container scan, config/schema validation, registry digest verification.
2. **Gate 1 (dev)**: data tests + golden-set evaluation against `release_gates.yaml` (implemented as `swda-gateway gate`; non-zero exit blocks).
3. **Gate 2 (staging)**: *shadow* run on a recent window beside champion: quality delta, p95 latency, cost/request, slice fairness deltas. No regression beyond configured tolerance.
4. **Approval**: model owner + security (separation of duties; the PoC enforces owner != approver for agents).
5. **Prod rollout**: batch - alias flip at the next hour boundary; online - 5% canary, automated watch window with auto-rollback on SLO breach.
- **CT (continuous training)** is a *pipeline*, not a cron: trigger (drift alert, labelled-feedback volume, schedule) -> train -> Gate 1 -> Gate 2 -> **human approval** -> promote. Automation proposes; humans promote.
- **Rollback** is a registry alias move (seconds) or `suspend` in the agent registry; both are audited.

## 7. Batch inference now, online serving later

- **One inference core.** `ServiceRecommendationAgent.run()` is used by the hourly batch path (`batch.py`) and the MCP path (`mcp_server.py`) - identical outputs by construction, so shadow-comparing online vs batch is meaningful and phase 2 adds no new logic.
- **Batch.** Idempotent per window and per `request_id`; poison records go to a DLQ table (not the whole batch); output table is append/merge with `model_version`, `config_hash`, `run_id`; completion emits a metric + event so downstream jobs wait on data, not on the clock.
- **Online (later).** Same agent behind the gateway; model endpoint with autoscaling and scale-to-zero outside business hours; **timeouts, rate limits and a circuit breaker** around any model call; **fallback** to the rules path when the model is degraded (flagged `needs_human_review`).
- **Cache** idempotent responses by `(request_id, model_version, config_hash)` to absorb retries without extra cost.

## 8. Monitoring, alerting and incident response

**What is monitored** (each has an owner and a runbook link):

| Layer | Signals | Example alert (config, not code) |
|---|---|---|
| Pipeline | freshness, row counts, expectation failures, DLQ rate, job duration | no gold data for the window by H+20min -> page |
| Model quality | proxy metrics (confidence distribution, `unclassified` rate, `needs_human_review` rate), delayed labels -> accuracy by slice | unclassified rate > gate for 3 windows -> ticket; > 2x -> page |
| Drift | PSI on input features and predicted category distribution (implemented: `drift.py`; thresholds `psi_warn/alert`) | PSI >= alert on 2 consecutive windows -> page + freeze CT promotion |
| Service (online) | request rate, error rate by code, p50/p95/p99 latency, saturation, rate-limit hits | 5xx > 1% for 5 min -> page |
| Security | auth failures, denied-tool attempts, scope denials, output-blocked events, unusual per-client volume | burst of `denied_unknown_or_unauthorised` from one client -> security alert |
| Cost | $/window, tokens/request, per-client quota burn | forecast > budget -> alert before breach |
| Audit integrity | hash-chain verification job (implemented: `verify-audit`) | chain break -> security incident |

**Incident response.** Severity matrix (S1: sensitive-data exposure or wrong output at scale; S2: degraded quality/availability; S3: single-window failure). First action is always **contain**: `suspend` the agent in the registry (gateway stops exposing it on next load), flip alias to previous champion, or switch to rules fallback. Then diagnose using the trace id -> audit record -> run/model/config versions. Post-incident: blameless review, a new regression test in the golden/safety sets, and runbook update. S1 includes the data-protection notification path.

## 9. Drift, feedback and retraining

- **Detect**: input drift (PSI/KS per feature, language/length distribution), **prediction drift** (category mix), **concept drift** (delayed labels vs predictions by slice). PSI is implemented; thresholds in config.
- **Feedback loop**: caseworker accept/override signals stored with `request_id`; labels are sampled and reviewed before entering golden/training sets. Guard against **feedback loops** (the model influencing what is later labelled) by holding out a randomly sampled "unrecommended" review slice.
- **Retraining**: trigger conditions are explicit and config-driven; retraining never auto-promotes. A retrained candidate must beat champion *and* the rules baseline on the golden + slice sets and pass the safety set.
- **Freeze rule**: while a drift alert is active, promotion is blocked (a drifting world makes "better on yesterday's golden set" unreliable).

## 10. Security, governance, auditability and responsible AI

**Threat model for the MCP surface** (STRIDE-flavoured; implemented controls in bold):

| Threat | Control |
|---|---|
| Spoofed client | **Hashed-token auth (PoC) -> OAuth/OIDC short-lived tokens**; mTLS between gateway and services |
| Privilege escalation via tool discovery | **Entitlement-filtered `tools/list`; unknown == unauthorised (same error)** |
| Tampered agent definition | **Manifest digest verified on every load; approval policy re-evaluated; entrypoint allow-list** (production: KMS-signed digest) |
| Over-permissive agent ("excessive agency") | **Allow-listed tools per manifest enforced by the ToolBox; no network/secrets in the agent runtime** |
| Prompt injection via request text / tool output | Input is data: schema validation + size caps; **PII redaction**; deterministic tools in PoC; for LLM agents: delimit untrusted text, no tool receives raw user text as instructions, output schema validation, Bedrock Guardrails as a second layer |
| Data exfiltration through responses | **Output policy check (fail closed)**; responses are structured schemas, not free text; egress closed at network layer |
| Denial of wallet / service | **Per-client rate limits, size caps, timeouts**; token/cost budgets per agent for LLM agents |
| Repudiation | **Hash-chained audit with client id, agent+version, HMAC pseudonym of input**; WORM storage |
| Info leak via logs | **Raw text never logged**; PII counts only; HMAC (keyed) pseudonyms so low-entropy inputs cannot be confirmed by guessing |
| Supply chain | Locked deps, SBOM, `pip-audit`, signed images, pinned base image digests |

**Governance.** Data classification on every table/agent; access via UC groups, not individuals; *separation of duties* (owner cannot approve own agent; three distinct approver roles); agent **risk tiering** drives gate strictness; periodic **re-certification** of approved agents (approval expires; a lapsed approval fails the exposure check).

**Responsible AI.** Purpose limitation and data minimisation (model sees redacted text); **human-in-the-loop** for sensitive or ambiguous outputs (`needs_human_review` is first-class in the response schema); explainability by construction (each recommendation carries `reasons`; every call has a tool trace); fairness - slice metrics in gates (age band, language register, channel) with documented tolerances; contestability - citizens/caseworkers can see why and override, and overrides are logged; model/agent cards published internally; no automated adverse decisions in v1 (A4).

## 11. Reliability, recovery and cost management

- **Reliability.** SLOs (e.g. hourly output available by H+20min 99%; online p95 < X ms, 99.5% availability) -> error budgets drive release pace. Retries with backoff + jitter on idempotent calls; **timeouts everywhere**; **bulkheads** (separate pools per agent); **circuit breaker** + rules fallback for model dependency; DLQ for poison messages.
- **Recovery.** Delta time travel + S3 versioning + cross-region replication of audit and model artefacts; IaC rebuilds environments; **RPO**: <= 1 hour for batch (re-derivable from bronze) and ~0 for audit (WORM, replicated); **RTO**: batch re-run within the next window; gateway < 30 min via multi-AZ + redeploy from the signed image. Quarterly game-day: restore + replay a window; verify the audit chain after restore.
- **Cost.** Job clusters with auto-termination and spot for non-critical stages; serverless/scale-to-zero outside hours; right-size by profiling; **tag everything** (project/env/owner) -> per-agent chargeback; **budgets and alerts** (AWS Budgets, Databricks system tables); for LLM agents: token budgets per request/agent/day, response caching, smaller model first (route up on low confidence), batch API for non-urgent work. Cost per request is a **release-gate metric**, so a change that doubles cost cannot ship silently (Part 2 enforces the same with `max_cost_increase_ratio`).

## 12. Agent approval, registry and controlled exposure

1. **Register** - owner submits a manifest: id, version, owner, risk tier, data classification, entrypoint, **allowed tools**, required scopes, prompt/config hashes, eval-gate result.
2. **Review** - security (threat model + tools), data governance (data classification/PII), product owner (purpose); each is a distinct approver; owner cannot approve; eval gate must have passed. (`registry.transition` + `policy_violations`.)
3. **Seal** - on approval the manifest digest is computed and stored (production: KMS signature). Any later edit invalidates it.
4. **Expose** - the gateway builds its tool list from `exposed()` only: approved + digest valid + policy still satisfied + entrypoint allow-listed. Discovery is scoped to the caller's entitlements.
5. **Operate** - every call is authenticated, authorised, rate-limited, validated, redacted, executed with allow-listed tools under a timeout, output-checked and audited.
6. **Change/withdraw** - new version = new approval; `suspend` removes exposure on next load (immediately in a hot-reload design); `retire` is terminal. Approvals expire and require re-certification.
- **MCP clients** are registered too (id, token digest/IdP client, scopes, quotas) - the client registry is the other half of "who may call what".

## 13. ML lifecycle and reproducibility (end to end)

`data version + feature code version + training code SHA + config hash + seed + environment digest -> MLflow run -> model version (+ model card) -> evaluation report -> registry alias -> batch/online output stamped with model_version, config_hash, agent version, run id`.
Given an output record, an auditor can reconstruct the exact model, config and data snapshot; given a model version, they can list every output it produced.

## 14. Key trade-offs

| Decision | We chose | Cost of that choice | When we would revisit |
|---|---|---|---|
| Build a thin MCP gateway vs buy managed | Thin custom gateway | We own ~1,000 lines of security-relevant code | A managed gateway meets registry-digest, audit and scope requirements |
| Deterministic tools inside agents | Yes (rules/ranking/search as plain code) | Less "magic"; recall limited by rules | Evidence that an ML ranker beats rules on the golden + slice sets |
| Agent runtime in-process (threads) with timeout | Simple, fast to test | A hung thread can't be killed; shared process | Move to per-agent container/Lambda for strong isolation as risk tier rises |
| Hash-chain audit in app + WORM store | Tamper-evident and immutable | Extra moving part; sequencing under multi-writer needs a single-writer or per-shard chains | Volume requires sharded chains/Kinesis -> S3 |
| Batch-first, one inference core | Cheaper and simpler now | Online latency work deferred | Business requires near-real-time; core is already reusable |
| Rules fallback permanently kept | Safe degraded mode | Two code paths to maintain | Never removed for medium-risk tier |
| Digest (sha256) vs signature for registry | Digest in PoC | Anyone with write access to the file can re-seal it | Production: KMS signature + registry in an access-controlled store |
| Strict fail-closed output check | Safe | Possible false blocks (e.g. a programme title that looks like an email) | Tune patterns with metrics on `output_blocked` |
| Redaction by regex (PoC) | Transparent and testable | Misses names/addresses | Managed PII service + NER in production, regex retained as a backstop |

## 15. What is implemented vs designed

| Requirement | Implemented in `swda_agent_gateway` (tested) | Designed only (above) |
|---|---|---|
| 1 Data/feature pipelines | Synthetic catalogue + input schema validation, batch idempotency/DLQ (`batch.py`) | Delta/UC pipelines, expectations |
| 2 Experimentation/evaluation | Golden set + thresholds as config (`evaluation.py`) | MLflow runs, slices, shadow |
| 3 Model/prompt/artefact mgmt | Registry manifest + digest sealing (`registry.py`) | MLflow/UC registry, prompt hashing |
| 4 CI/CD/CT gates | `swda-gateway gate` (non-zero exit), CI workflow in `.github/workflows/ci.yml` | Shadow/canary, CT triggers |
| 5 Batch + online | One agent core used by batch and MCP; stdio + HTTP transports | Model Serving, autoscaling |
| 6 Monitoring/alerting | Metrics (`/metrics`), health/readiness, audit-chain verification, PSI drift | CloudWatch/OTel wiring, runbooks |
| 7 Drift/feedback/retraining | PSI + status thresholds | Feedback capture, CT pipeline |
| 8 Security/governance/audit | AuthN/Z, scopes, PII redaction, output policy, hash-chained audit | OAuth/OIDC, KMS, WORM, Guardrails |
| 9 Reliability/cost | Timeouts, rate limits, size caps, fail-closed | Multi-AZ, DR, budgets |
| 10 Agent approval/registry/MCP | State machine with separation of duties, exposure predicate, entitlement-filtered discovery, tool allow-list | Registry service, hot-reload, re-certification |
| 11 Hands-on coding | **Whole of `swda_agent_gateway` + 37 tests (incl. official MCP SDK interop)** | - |

## 16. Known limitations of the PoC (honest list)

- Static hashed tokens, not OAuth; single-process audit chain; threads for timeouts; regex PII; the registry digest is not a signature; no hot-reload of the registry; no persistent rate-limit state across replicas; the "agent" is rules-based so it demonstrates the *control plane*, not model quality.
