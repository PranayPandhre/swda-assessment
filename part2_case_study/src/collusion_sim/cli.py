"""collusion-sim CLI: run | review | eval | verify-trace."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .approval import ApprovalRefused, apply_decision, load_card, verify_card_integrity
from .config import load_settings
from .evaluation import evaluate
from .runner import _rewrite_markdown, apply_decisions_file, run_scenario
from .trace import verify_trace


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cmd_run(a, s) -> int:
    out = s.root / a.out if not Path(a.out).is_absolute() else Path(a.out)
    res = run_scenario(a.scenario, out, s)
    sm = res.summary
    print(f"run {sm['run_id']}: {sm['status']} | rounds {sm['rounds_executed']} | cost {sm['cost_units']}/{sm['max_cost_units']} | "
          f"quarantined {sm['quarantined']} | boundary violations {sm['boundary_violations']}")
    for c in res.cards:
        print(f"  {c.pattern_key} {c.collusion_classification:24} MO:{c.mo_classification.mo_class:9} support:{c.review.source_support:9} "
              f"{c.title}  blocking={c.blocking_issues or '-'}")
    if a.decisions:
        print("human decisions (from file):")
        for line in apply_decisions_file(out, s.root / a.decisions, s, _now):
            print("  " + line)
    print(f"artefacts: {out}")
    return 0


def cmd_review(a, s) -> int:
    run_dir = Path(a.run_dir) if Path(a.run_dir).is_absolute() else s.root / a.run_dir
    reviewer = input("your reviewer id (e.g. human:j.tan): ").strip()
    for p in sorted((run_dir / "cards").glob("*.json")):
        card = load_card(p)
        if card.status != "pending_human_review":
            continue
        print(f"\n{(p.with_suffix('.md')).read_text()}")
        decision = input(f"[{card.pattern_key}] decision {s.policy.approval.decisions} or Enter to skip: ").strip()
        if not decision:
            continue
        notes = input("notes: ").strip()
        try:
            apply_decision(p, reviewer=reviewer, decision=decision, notes=notes, channel="interactive", settings=s, clock=_now,
                           trace_path=run_dir / "trace.jsonl")
        except ApprovalRefused as exc:
            print(f"REFUSED: {exc}")
    _rewrite_markdown(run_dir)
    return 0


def cmd_eval(a, s) -> int:
    rep = evaluate(s, update_baseline=a.update_baseline)
    failed = [c for c in rep.checks if not c.passed]
    for c in rep.checks:
        if a.verbose or not c.passed:
            print(f"{'PASS' if c.passed else 'FAIL'}  {c.name}  {c.detail}")
    for name, m in rep.metrics.items():
        print(f"{name:12} {json.dumps(m)}")
    print(f"\nEVAL: {len(rep.checks) - len(failed)}/{len(rep.checks)} checks passed" + ("  (baseline updated)" if a.update_baseline else ""))
    return 0 if rep.ok else 1


def cmd_verify(a, s) -> int:
    run_dir = Path(a.run_dir) if Path(a.run_dir).is_absolute() else s.root / a.run_dir
    ok, msg = verify_trace(run_dir / "trace.jsonl")
    bad = [p.name for p in (run_dir / "cards").glob("*.json") if not verify_card_integrity(load_card(p))]
    print(("trace OK: " if ok else "trace TAMPERED: ") + msg + (f" | cards changed after decision: {bad}" if bad else " | card digests OK"))
    return 0 if ok and not bad else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="collusion-sim")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--scenario", default="baseline")
    r.add_argument("--out", default="outputs/latest")
    r.add_argument("--decisions", help="YAML file of human decisions (non-interactive stand-in for the review step)")
    r.set_defaults(fn=cmd_run)
    v = sub.add_parser("review")
    v.add_argument("--run-dir", required=True)
    v.set_defaults(fn=cmd_review)
    e = sub.add_parser("eval")
    e.add_argument("--update-baseline", action="store_true")
    e.add_argument("--verbose", action="store_true")
    e.set_defaults(fn=cmd_eval)
    t = sub.add_parser("verify")
    t.add_argument("--run-dir", required=True)
    t.set_defaults(fn=cmd_verify)
    args = ap.parse_args(argv)
    return args.fn(args, load_settings())


if __name__ == "__main__":
    sys.exit(main())
