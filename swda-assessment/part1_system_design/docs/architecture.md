# Part 1 - Architecture Design (Deliverable A)

Diagrams are Mermaid (rendered natively by GitHub; PNG exports are in `img/`). Colour/shape key: **rectangles** = services/jobs,
**cylinders** = stores, **hexagons** = human/governance gates. "Control plane" = what decides *what may run*; "data plane" = what *moves data*.

## A1. End-to-end MLOps / AIOps platform (data and control flows)

```mermaid
flowchart LR
  subgraph SRC["Sources (AWS)"]
    S1[(Service request<br/>system extracts)]
    S2[(Programme catalogue)]
    S3[(Feedback / outcomes<br/>caseworker labels)]
  end

  subgraph LAKE["Databricks lakehouse on S3 - governed by Unity Catalog"]
    direction TB
    B[(Bronze<br/>raw, immutable, KMS-encrypted)]
    SV[(Silver<br/>validated, PII-tokenised)]
    G[(Gold<br/>training + inference tables)]
    FS[(Feature tables<br/>point-in-time correct)]
    DQ{{"Data-quality gate<br/>(expectations: schema, nulls,<br/>ranges, freshness)"}}
    B --> DQ --> SV --> G --> FS
  end

  subgraph ML["Experimentation and model lifecycle"]
    EXP[Training / eval jobs<br/>MLflow tracking]
    EV{{"Evaluation gate<br/>golden set + slices + safety"}}
    REG[(UC Model Registry<br/>aliases: candidate / champion)]
    PR[(Prompt + config registry<br/>versioned, hashed)]
    EXP --> EV --> REG
    PR --> EV
  end

  subgraph SERVE["Inference"]
    BATCH[Hourly batch job<br/>Databricks Workflow<br/>idempotent per hour window]
    OUT[(Predictions table<br/>+ DLQ table)]
    ONLINE["Later: online API / MCP gateway<br/>(see A3)"]
    BATCH --> OUT
  end

  subgraph OBS["Observability and operations"]
    MON[Lakehouse Monitoring<br/>drift, quality, slices]
    CW[CloudWatch / OpenTelemetry<br/>metrics, logs, traces]
    AL[Alerting<br/>SNS / PagerDuty]
    AUD[(Audit store<br/>S3 Object Lock + CloudTrail)]
    CT{{"Retrain trigger<br/>drift / feedback volume / schedule"}}
  end

  S1 --> B
  S2 --> B
  S3 --> B
  FS --> EXP
  REG -- "champion alias" --> BATCH
  FS --> BATCH
  PR --> BATCH
  OUT --> MON
  BATCH --> CW
  ONLINE --> CW
  MON --> AL
  CW --> AL
  BATCH --> AUD
  ONLINE --> AUD
  MON --> CT
  S3 -- labels --> CT
  CT -- "triggers CT pipeline (human approves promotion)" --> EXP
  OUT -- "consumers: caseworker UI / downstream" --> DOWN[Downstream consumers]
  DOWN -- feedback --> S3
```

**Control flows:** model promotion is an *alias move* in the registry,
gated by the evaluation gate and a human approver; the batch job reads the `champion` alias at start, so a rollback is another alias move
(no redeploy). Retraining is *triggered* automatically but *promoted* only through the same gate.

## A2. CI/CD/CT pipeline and release gates

```mermaid
flowchart LR
  DEV[Developer PR] --> CI
  subgraph CI["CI (GitHub Actions, OIDC to AWS - no long-lived keys)"]
    L[Lint + type + unit tests]
    SEC[SAST bandit, dependency audit,<br/>secret scan, container scan]
    CFG[Config + schema validation<br/>registry digests verified]
    L --> SEC --> CFG
  end
  CI --> BUILD[Build versioned artefacts<br/>wheel + container, SBOM, signed]
  BUILD --> DEVENV[Deploy to dev<br/>Databricks Asset Bundle + IaC]
  DEVENV --> GATE1{{"Gate 1: data tests +<br/>golden-set evaluation<br/>thresholds in config"}}
  GATE1 --> STG[Staging: shadow run on<br/>last N hours of real-shaped data]
  STG --> GATE2{{"Gate 2: shadow vs champion<br/>quality, latency, cost,<br/>fairness slices"}}
  GATE2 --> APPROVE{{"Human approval<br/>model owner + security<br/>(separation of duties)"}}
  APPROVE --> PROD[Prod: canary 5% hours / alias flip]
  PROD --> WATCH[Automated watch window<br/>auto-rollback on SLO breach]
  CT[Continuous training<br/>on drift / feedback / schedule] --> GATE1
```

## A3. Online serving and governed MCP exposure of agents

```mermaid
flowchart LR
  subgraph CLIENTS["MCP clients (each has an identity and scopes)"]
    C1[Caseworker portal<br/>MCP client]
    C2[Internal AI assistant<br/>MCP client]
    C3[Other approved agent]
  end

  subgraph EDGE["Edge (private)"]
    WAF[API Gateway / ALB + WAF<br/>TLS, request size cap]
    IDP[Enterprise IdP<br/>OAuth 2.1 / OIDC tokens]
  end

  subgraph GW["MCP gateway service (ECS Fargate, private subnets)  - implemented in this repo"]
    direction TB
    AUTHN[1. Authenticate caller]
    AUTHZ[2. Authorise: scope per agent<br/>discovery is entitlement-filtered]
    RL[3. Rate limit + quota per client]
    VAL[4. JSON-Schema validation<br/>size + type limits]
    PII[5. PII redaction in]
    EXE[6. Invoke agent with<br/>allow-listed tools + timeout]
    OUTP[7. Output policy check<br/>PII, schema; fail closed]
    AUDL[8. Audit + metrics]
    AUTHN --> AUTHZ --> RL --> VAL --> PII --> EXE --> OUTP --> AUDL
  end

  subgraph CP["Control plane: agent registry and approval"]
    GIT[(Registry manifests in Git<br/>PR = approval record)]
    REGS[(Agent registry<br/>immutable, digest-sealed)]
    APPR{{"Approval workflow<br/>security + data governance +<br/>product owner; owner cannot approve"}}
    EVALG{{Eval gate result<br/>attached to manifest}}
    GIT --> APPR
    EVALG --> APPR
    APPR --> REGS
  end

  subgraph AGENTS["Approved agents (versioned) and their tools"]
    A1[Service Recommendation Agent v1]
    T1[classify_request]
    T2[search_programmes]
    T3[rank_programmes]
    A1 --> T1
    A1 --> T2
    A1 --> T3
  end

  subgraph SINKS["Sinks"]
    AUDS[(Audit log<br/>hash-chained, WORM)]
    MET[Metrics / traces<br/>CloudWatch + OTel]
  end

  C1 --> WAF
  C2 --> WAF
  C3 --> WAF
  WAF --> AUTHN
  IDP -. token introspection .-> AUTHN
  REGS -- "only status=approved AND digest valid<br/>AND policy still satisfied" --> AUTHZ
  EXE --> A1
  AUDL --> AUDS
  AUDL --> MET
```

Agents **never** receive credentials, network access or tools beyond their manifest; the gateway is the only path to them, and it only
advertises agents the *caller* is entitled to (an unentitled caller cannot even enumerate them).

## A4. Agent approval workflow (state machine)

```mermaid
stateDiagram-v2
  [*] --> draft
  draft --> in_review: owner submits manifest + eval report
  in_review --> draft: changes requested
  in_review --> approved: security + data_governance + product_owner approve,<br/>eval gate passed, owner not an approver
  approved --> suspended: incident / drift alert / policy change (any of security, ops)
  suspended --> in_review: remediation + re-approval
  approved --> retired
  suspended --> retired
  retired --> [*]
  note right of approved
    Exposure is re-checked at every load:
    digest valid, entrypoint allow-listed,
    approvals still satisfy policy
  end note
```

## A5. One `tools/call` (sequence, with the failure exits)

```mermaid
sequenceDiagram
  autonumber
  participant C as MCP client
  participant G as Gateway
  participant R as Registry
  participant A as Agent (allow-listed tools)
  participant L as Audit log
  C->>G: tools/call {name, arguments} + credential
  G->>G: authenticate (unknown token -> 401 / -32001)
  G->>R: resolve agent (approved + digest valid?)
  alt unknown OR not entitled
    G->>L: denied (same error for both: no enumeration)
    G-->>C: error -32602 unknown tool
  else entitled
    G->>G: rate limit, size + schema validate
    G->>G: redact PII (NRIC / email / phone)
    G->>A: invoke(redacted args) with timeout
    A->>A: classify -> search -> rank (only permitted tools)
    A-->>G: structured response
    G->>G: output policy (PII, schema) fail-closed
    G->>L: outcome, client, agent+version, HMAC pseudonym of input
    G-->>C: result {structuredContent, isError}
  end
```

## A6. Trust boundaries

```mermaid
flowchart TB
  subgraph Z0["Zone 0 - untrusted: callers, user text, annex/web content, model output"]
  end
  subgraph Z1["Zone 1 - gateway: authN/Z, validation, redaction, quotas, audit"]
  end
  subgraph Z2["Zone 2 - agent runtime: no network egress except VPC endpoints, no secrets, allow-listed tools only"]
  end
  subgraph Z3["Zone 3 - data platform: Unity Catalog ACLs, row/column masks, KMS keys per domain"]
  end
  Z0 -- "schema-validated, size-capped, redacted" --> Z1
  Z1 -- "least-privilege tool calls only" --> Z2
  Z2 -- "service principal, read-only views" --> Z3
```
