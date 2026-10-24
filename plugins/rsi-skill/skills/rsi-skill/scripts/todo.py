#!/usr/bin/env python3
"""List what is still to do in an iteration (for grading fan-out and for resuming).

    python -I todo.py --run-dir R --iteration N

Prints the runs that have no checks.json or grading.json yet (with every path a grader needs),
the blind pairs that have no verdict yet, and the run state. Nothing is changed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aggregate  # noqa: E402
import rsilib  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--iteration", type=int, required=True)
    a = ap.parse_args()
    run = rsilib.Run(a.run_dir)
    it = run.iteration_dir(a.iteration)
    cases = sorted(p.parent.name for p in (it / "cases").glob("*/case.json"))
    to_check, to_grade = [], []
    for rd in sorted((it / "runs").glob("*/*")):
        if not (rd / "metrics.json").exists():
            continue
        if not (rd / "checks.json").exists():
            to_check.append(f"{rd.parent.name}/{rd.name}")
        if not (rd / "grading.json").exists():
            m = rsilib.read_json(rd / "metrics.json") or {}
            cm = aggregate.case_meta(run, a.iteration, rd.parent.name)
            case_path = next((str(p) for p in [it / "cases" / rd.parent.name / "case.json"] if p.exists()), None) or \
                next((str(q) for q in sorted((run.dir / "iterations").glob(f"*/cases/{rd.parent.name}/case.json"))), None)
            to_grade.append({"case_id": rd.parent.name, "role": rd.name, "case": case_path,
                             "final": str(rd / "final.md"), "digest": str(rd / "digest.md"),
                             "transcript": str(rd / "transcript.jsonl"), "checks": str(rd / "checks.json"),
                             "metrics": str(rd / "metrics.json"), "sandbox": m.get("sandbox"),
                             "files_created": m.get("files_created", [])[:50], "status": m.get("status"),
                             "anchor": bool(cm.get("anchor")), "out": str(rd / "grading.json")})
    comp = it / "comparison"
    to_compare = []
    for op in sorted(comp.glob("*.order.json")) if comp.exists() else []:
        cid = op.name[:-len(".order.json")]
        if not (comp / f"{cid}.json").exists():
            to_compare.append({"case_id": cid, "A": str(comp / cid / "A"), "B": str(comp / cid / "B"),
                               "case": str(it / "cases" / cid / "case.json"), "out": str(comp / f"{cid}.json")})
    rsilib.emit({"iteration": a.iteration, "state": run.state, "cases": cases, "to_check": to_check,
                 "to_grade": to_grade, "to_compare": to_compare,
                 "scored": (it / "scoreboard.json").exists(), "decided": (it / "decision.json").exists()})


if __name__ == "__main__":
    main()
