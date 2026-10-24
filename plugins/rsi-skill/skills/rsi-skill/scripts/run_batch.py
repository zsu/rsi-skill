#!/usr/bin/env python3
"""Run every case of an iteration against one or more candidates, in parallel, within budget.

    python -I run_batch.py --run-dir R --iteration N --role champion=v2 [--role challenger=v3]
        [--cases id1,id2] [--replay 003:caseA,003:caseB] [--parallel 3] [--force] [--claude-cmd CMD]

Cases are read from <run>/iterations/NNN/cases/*/case.json (fresh cases plus exported anchors).
--replay re-runs earlier cases (iteration:case-id) with the challenger candidate, as role "replay".
Before each job the budget is checked (cost, tokens, time); jobs past a limit are skipped.
Prints a JSON summary; the per-run details are in each metrics.json. Exit 0 ok, 3 jobs skipped
for budget, 4 infrastructure failure (CLI not logged in, auth/API errors): fix it and rerun.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import budget  # noqa: E402
import rsilib  # noqa: E402
from run_case import run_case  # noqa: E402

RUN_LIMITS = {"cost", "tokens", "time"}  # the iteration limit is checked between iterations, not here


def preflight(claude_cmd: list) -> dict:
    """Ask the CLI whether it is authenticated (best effort; unknown builds return {})."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return {"logged_in": True, "via": "ANTHROPIC_API_KEY"}
    try:
        r = subprocess.run(claude_cmd + ["auth", "status"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60, stdin=subprocess.DEVNULL)
        data = json.loads(r.stdout)
        return {"logged_in": bool(data.get("loggedIn")), "method": data.get("authMethod")}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--iteration", type=int, required=True)
    ap.add_argument("--role", action="append", default=[], help="role=candidate, e.g. champion=v0")
    ap.add_argument("--cases", help="comma-separated case ids (default: all in the iteration)")
    ap.add_argument("--replay", default="", help="comma-separated iteration:case-id to re-run with the challenger")
    ap.add_argument("--parallel", type=int)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--claude-cmd")
    a = ap.parse_args()

    run = rsilib.Run(a.run_dir)
    roles = dict(r.split("=", 1) for r in a.role)
    if not roles:
        raise SystemExit("give at least one --role role=candidate")
    cases_root = run.iteration_dir(a.iteration) / "cases"
    case_dirs = sorted(p.parent for p in cases_root.glob("*/case.json"))
    if a.cases:
        wanted = set(a.cases.split(","))
        case_dirs = [c for c in case_dirs if c.name in wanted]
    jobs = [(c, cand, role) for c in case_dirs for role, cand in roles.items()]
    if a.replay:
        chall = roles.get("challenger")
        if not chall:
            raise SystemExit("--replay needs --role challenger=<candidate>")
        for item in a.replay.split(","):
            it, cid = item.split(":", 1)
            d = run.iteration_dir(int(it)) / "cases" / cid
            if (d / "case.json").exists():
                jobs.append((d, chall, "replay"))
    claude_cmd = rsilib.find_claude_cmd(a.claude_cmd)
    parallel = a.parallel or run.config["limits"].get("parallel") or 3
    auth = preflight(claude_cmd)
    if auth.get("logged_in") is False:
        rsilib.emit({"error": "Claude Code CLI is not logged in; headless runs cannot start",
                     "fix": "run `claude auth login` (or `claude` then /login) in a terminal, or set ANTHROPIC_API_KEY",
                     "auth": auth}, 4)

    def job(spec):
        case_dir, cand, role = spec
        over = [x for x in budget.status(run.dir)["exceeded"] if x in RUN_LIMITS]
        if over:
            return {"case_id": case_dir.name, "role": role, "candidate": cand, "status": "skipped_budget",
                    "exceeded": over}
        try:
            return run_case(run.dir, a.iteration, case_dir, cand, role, force=a.force, claude_cmd=claude_cmd)
        except SystemExit as e:
            return {"case_id": case_dir.name, "role": role, "candidate": cand, "status": "error", "error": str(e)}

    results = []
    with cf.ThreadPoolExecutor(max_workers=parallel) as ex:
        for m in ex.map(job, jobs):
            results.append({k: m.get(k) for k in ("case_id", "role", "candidate", "status", "cost_usd",
                                                   "duration_seconds", "skill_invoked", "skipped", "exceeded",
                                                   "error")})
    summary = {
        "iteration": a.iteration, "jobs": len(jobs),
        "ok": sum(1 for r in results if r["status"] in ("ok", "error_result")),
        "failed": [r for r in results if r["status"] in ("error", "timeout")],
        "infra_errors": [r for r in results if r["status"] == "infra_error"],
        "skipped_budget": sum(1 for r in results if r["status"] == "skipped_budget"),
        "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in results), 4),
        "results": results,
        "budget": budget.status(run.dir),
    }
    code = 4 if summary["infra_errors"] else (3 if summary["skipped_budget"] else 0)
    rsilib.emit(summary, code)


if __name__ == "__main__":
    main()
