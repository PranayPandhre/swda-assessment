# AI usage declaration

**Tool:** Claude (Anthropic) in agent mode, with a code sandbox, file tools and a shell. AI wrote the first version of the code, tests and documents in this repo in a single working session; the candidate is expected to review, run and be able to defend all of it (see WALKTHROUGH.md). *Candidate: edit this file to reflect your own review and any changes you made.*

## How it was prompted
1. Prompt: the assessment PDF attached, plus "Solve this coding assignment". No other steering.
2. The assistant read the PDF, split the work into Part 1 and Part 2, set explicit design rules from the brief (thin vertical slice, defensible over broad, state assumptions, config not code, untrusted annex content), and built in stages: config/data -> core modules -> tests -> evals -> diagrams -> docs.

## What the AI returned and how it was applied
- Code for both parts, 119 tests, eval harness, diagrams (Mermaid, rendered and visually checked), design docs.
- It was applied only after execution: all tests, both release/eval gates, both demos, and an interoperability test with the official MCP SDK client were run.

## Where the AI was wrong, and how that was caught
| Issue | Caught by | Fix |
|---|---|---|
| Injection regex `act as` flagged MO-01 ("act as employers") and withheld it | Inspecting the first sample trace (`flagged_sources`) | Narrowed rule; added a regression test |
| Several test expectations were wrong (assumption-penalty arithmetic, per-role validity fractions, a guessed category in the SDK test) | Failing tests; reasoned which side was wrong | Fixed the tests, not the code, after checking the code's behaviour was the intended one |
| YAML typo in taxonomy; two `conftest.py` helper imports colliding | Loader error; collection error | Fixed; helpers moved to uniquely named modules |
| Mock review script inconsistent with a changed candidate set (`no_provider`) | Cards showed `not_reviewed` | Scenario-specific review script |
| Unneeded duplicate source retrieval in the actor turn | Reading the code | Removed |

## Verification beyond "tests pass"
Mutation-style meta-tests (disable safeguards / widen an actor's role -> evals must fail), tamper tests (audit chain, registry digest, post-approval card edit), a context-minimisation invariant (prompts contain only permitted information).

## What the AI cannot vouch for
No real LLM was used; the Part 2 mock cannot show model behaviour (design note section 9). Cloud components in Part 1 are designed, not deployed. Product capabilities named in the design (e.g. managed MCP gateways, Lakehouse Monitoring) should be re-verified against current documentation before adoption.
