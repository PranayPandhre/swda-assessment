# Part 2 - Architecture Design (Deliverable A)

## B1. Main data and control flows, actor boundaries, rounds, evaluation harness, trace store, human approval

```mermaid
flowchart TB
  subgraph UNTRUSTED["Untrusted input (treated as DATA, never as instructions)"]
    AA[(Annex A<br/>MO library)]
    AB[(Annex B<br/>social-media excerpts)]
  end

  subgraph CFG["Config (versioned, reviewed) - no behaviour in code"]
    PROF[actors.yaml<br/>goals, capabilities,<br/>visible info, tools]
    TAX[taxonomy.yaml<br/>controlled vocabulary,<br/>MO feature map, evidence rules]
    POL[policy.yaml<br/>rounds, retries, cost,<br/>approval rules]
    SGR[safeguards.yaml<br/>injection / unsafe / fact / PII rules]
  end

  AA --> LOAD
  AB --> LOAD
  LOAD["Annex loader<br/>scan on load; flagged text WITHHELD"] --> LIB[(Source library<br/>id -> text, basis)]

  subgraph ORCH["Orchestrator = deterministic code, not an LLM"]
    direction TB
    RND["Round loop (max_rounds)<br/>delivers messages one round later"]
    GC["Guarded call<br/>cost check -> LLM -> schema + semantic validation<br/>-> retry (max_retries) -> fail turn"]
    SCR["Safeguards on every free-text field<br/>quarantine + trace; halt at budget"]
    LIM{{"Limits: rounds, cost units,<br/>failed turns, quarantine budget"}}
    RND --> GC --> SCR
    LIM -. halts .-> RND
  end

  subgraph ACTORS["Actor agents - each bound to ONE identity (mock LLM scripted, real LLM pluggable)"]
    direction LR
    TR["Trainee Agent<br/>sees S1-S3, MO-03/04/06"]
    TP["Training Provider Agent<br/>sees S1-S5, MO-02/04/05/07/08"]
    EM["Employer Agent<br/>sees S5, MO-01/06/08"]
  end

  subgraph TOOLS["ActorToolBox (least privilege, in code)"]
    LS[lookup_source]
    LM[lookup_mo]
  end

  PROF --> ORCH
  POL --> ORCH
  SGR --> SCR
  LIB --> TOOLS
  ORCH <-- "structured ActorTurn JSON only" --> ACTORS
  ACTORS -- "tool_calls (visibility + grant checked)" --> TOOLS

  ORCH --> ACC[(Accepted contributions<br/>capability tags + capped summary,<br/>basis, refs, flows, benefits)]
  ACC --> RV
  subgraph REVIEWSTAGE["Review stage"]
    RV["Review Agent<br/>(read-only lookups; schema has NO approval field)"]
    DET["Deterministic checks (authoritative)<br/>refs resolve - actor-removal - collusion vs single-actor<br/>MO class (Jaccard) - source support score"]
  end
  ACC --> DET
  TAX --> DET
  LIB --> RV

  RV --> CARD
  DET --> CARD
  CARD["Pattern card builder (code)<br/>source text copied by ID - simulated behaviour labelled -<br/>assumptions separate - blocking issues computed"]
  CARD --> CARDS[(Pattern cards JSON + MD<br/>status = pending_human_review)]

  CARDS --> HUMAN{{"HUMAN approval step<br/>only code path that changes status<br/>reviewer id must match human:*"}}
  HUMAN --> DEC[(approve / reject / request_changes<br/>+ card digest at decision time)]

  ORCH -. every event .-> TRACE[(Trace store<br/>hash-chained JSONL)]
  TOOLS -. tool_call allowed/denied .-> TRACE
  HUMAN -. approval_decision .-> TRACE

  subgraph EVAL["Evaluation harness (CI + on demand)"]
    GOLD["Golden expectations<br/>end-to-end + agent-level"]
    INV["Invariants on EVERY run<br/>no agent approval - prompts only visible info -<br/>cards free of unsafe text - chain valid"]
    REG["Regression vs committed baseline<br/>card fingerprints, metrics, cost"]
    MUT["Meta-tests: disable a control -> evals must fail"]
  end
  TRACE --> EVAL
  CARDS --> EVAL
```

## B2. One actor turn (validation, retries, quarantine)

```mermaid
sequenceDiagram
  autonumber
  participant O as Orchestrator (code)
  participant TB as ActorToolBox
  participant L as LLM (scripted mock / real)
  participant V as Validator
  participant S as Safeguards
  participant T as Trace store
  O->>TB: visible ids for this actor
  O->>O: cost < max? else HALT cost_limit
  O->>L: system + prompt (visible data wrapped as untrusted, inbox, notes)
  L-->>O: raw JSON text
  O->>T: llm_call (hashes, tokens, cumulative cost)
  O->>V: schema + semantic checks (role capabilities, refs visible, flow endpoints, caps)
  alt invalid
    V-->>O: error codes (never echo content)
    O->>T: validation_failed (+ boundary_violation)
    O->>L: retry with error codes (up to max_retries)
    Note over O: exhausted retries means turn_failed, and too many failed turns HALT the run (max_failed_turns)
  else valid
    O->>S: scan summaries, messages, assumptions
    alt hit (injection / operational / fact / PII)
      S-->>O: rule ids
      O->>T: item_quarantined (not delivered, not in cards), HALT at quarantine budget
    else clean
      O->>O: accept contribution / queue message for NEXT round
    end
    O->>TB: execute requested lookups
    TB-->>O: result (wrapped) or ToolDenied
    O->>T: tool_call allowed / denied (boundary_violation)
  end
```

## B3. What the actors may see and do (enforced in code, data in `config/actors.yaml`)

| Agent | Capability tags it may use | Visible sources / MOs | Tools | Can approve? |
|---|---|---|---|---|
| Trainee | claim_without_attendance, course_only_hire_participation | S1-S3 / MO-03, 04, 06 | lookup_source, lookup_mo | No |
| Training Provider | attendance_falsified, cash_inducement, attendance_optional_messaging, overclaimed_training, value_return_via_provider, misleading_course_bundling | S1-S5 / MO-02, 04, 05, 07, 08 | lookup_source, lookup_mo | No |
| Employer | sham_employment, post_course_termination, sponsor_fee_payment, reciprocal_referral, dormant_entity_use | S5 / MO-01, 06, 08 | lookup_source, lookup_mo | No |
| Review | none (comments + opinions only) | all | lookup_source, lookup_mo | **No (schema cannot express it)** |
| Orchestrator | n/a (code) | all (to assemble) | n/a | **No** |
| **Human reviewer** | n/a | cards + trace | approval CLI | **Yes - the only path** |
