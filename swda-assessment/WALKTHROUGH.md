# Walkthrough and defence notes

**Part 1 (5 min story):** *prototype -> governed service.* Draw A3 and trace one `tools/call`: `mcp_server._call_tool` -> `registry.exposed` -> `runtime.invoke` -> `toolbox`. Be ready to explain: why unknown and unentitled return the same error; why the agent can't call a tool outside its manifest (`toolbox.py`, test `test_agent_cannot_use_tool_outside_its_allowlist`); why input is HMAC-pseudonymised not hashed; what the registry digest does and does not protect (not a signature); why batch and online share one core.

**Part 2 (5 min story):** *agents propose, code disposes, humans approve.* Trace one turn (`orchestrator._guarded_call` -> `validation.validate_turn` -> `_screen`). Be ready to explain: why the orchestrator is not an LLM; why behaviour is tags + capped summary; how actor-removal differs from "ask the model if it's collusion"; what each of source support / MO class means and what they don't; why the computed rating overrides the Review Agent; what the mock can't prove (design note section 9).

**Likely questions:** How would this fail with a real model? (retries, halts, first-attempt validity metric; section 9 plan.) What is the weakest control? (regex screens; static tokens; thread timeouts.) What would you build next? (classifier screen, signed registry, OAuth, per-agent containers, real-model eval with planted ground truth.) How do you know the evals aren't vacuous? (meta-tests that disable controls.)

**Run these live:** `make all`; `collusion-sim run --scenario adversarial` and read the trace; `collusion-sim eval --verbose`; tamper with a card after approval then `collusion-sim verify`.
