#!/usr/bin/env python3
"""Create a run folder for one rsi loop and snapshot the target project.

    python -I init_workspace.py --target <path|target.json> [--name SKILL] [--workspace DIR] [--goal-text "..."]
        [--max-iterations 5] [--max-hours 6] [--max-cost-usd X] [--max-tokens N]
        [--cases 4] [--parallel 3] [--model M] [--judge-model M] [--timeout 1200]
        [--unpaired] [--baseline] [--report-only] [--unattended] [--run-id ID]

--target is the target path (resolved like resolve_target.py; exit 2/4 as there) or that
script's JSON output. Creates
<workspace>/runs/<run-id>/ with config.json, state.json, candidates/v0/ (the pristine snapshot)
and baseline_manifest.json (hashes of the target files, used by apply_to_target.py).
Prints {"run_dir": ...}.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rsilib  # noqa: E402

DEFAULTS = {
    "max_iterations": 5,
    "max_hours": 6.0,
    "max_cost_usd": None,
    "max_tokens": None,
    "cases_per_iteration": 4,
    "parallel": 3,
    "timeout_seconds": 1200,
    "max_turns": None,
    "patience": 2,
    "promote_margin": 1,
    "replay_fix_min": 0.5,
    # subagent token counts include cache reads, so a blended rate well under list input price
    "subagent_usd_per_mtok": 4.0,
    "orchestrator_tokens_per_iteration": 60000,
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", required=True, help="the target path (any form resolve_target.py takes) or its JSON output")
    ap.add_argument("--name", help="skill name when the target path holds several skills")
    ap.add_argument("--workspace")
    ap.add_argument("--goal-text", default="")
    ap.add_argument("--max-iterations", type=int)
    ap.add_argument("--max-hours", type=float)
    ap.add_argument("--max-cost-usd", type=float)
    ap.add_argument("--max-tokens", type=int)
    ap.add_argument("--cases", type=int, dest="cases_per_iteration")
    ap.add_argument("--parallel", type=int)
    ap.add_argument("--timeout", type=int, dest="timeout_seconds")
    ap.add_argument("--max-turns", type=int)
    ap.add_argument("--patience", type=int)
    ap.add_argument("--model", help="model for the target-skill test runs (default: CLI default)")
    ap.add_argument("--judge-model", help="model for grader/comparator subagents (default: session model)")
    ap.add_argument("--unpaired", action="store_true")
    ap.add_argument("--baseline", action="store_true", help="also run a no-skill baseline on iteration 0")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--unattended", action="store_true")
    ap.add_argument("--isolate-user-settings", action="store_true",
                    help="test runs load only project/local settings (skips user plugins, hooks and env)")
    ap.add_argument("--run-id")
    a = ap.parse_args()

    target = rsilib.read_json(a.target) if a.target.lower().endswith(".json") and Path(a.target).is_file() else None
    if not target or "repo_root" not in target:
        import resolve_target
        code, target = resolve_target.resolve(a.target, a.name)
        if code != 0:
            rsilib.emit(target, code)
    repo = Path(target["repo_root"])
    workspace = Path(a.workspace or target["default_workspace"]).resolve()
    if workspace == repo or workspace.is_relative_to(repo):
        raise SystemExit(f"workspace must be outside the target project: {workspace}")
    run_id = a.run_id or dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = workspace / "runs" / run_id
    if run_dir.exists():
        raise SystemExit(f"run folder exists: {run_dir}")

    limits = dict(DEFAULTS)
    for k in DEFAULTS:
        v = getattr(a, k, None)
        if v is not None:
            limits[k] = v

    files = rsilib.list_project_files(repo)
    v0 = run_dir / "candidates" / "v0"
    rsilib.copy_files(repo, v0, files)
    rsilib.write_json(run_dir / "baseline_manifest.json", rsilib.manifest(repo, files))
    rsilib.write_json(run_dir / "candidates" / "v0.meta.json",
                      {"id": "v0", "parent": None, "created_at": rsilib.now_iso(), "origin": "target snapshot",
                       "targets": [], "status": "champion"})
    (workspace / "case-bank").mkdir(parents=True, exist_ok=True)

    config = {
        "run_id": run_id,
        "created_at": rsilib.now_iso(),
        "target": target,
        "workspace": str(workspace),
        "goal_text": a.goal_text,
        "limits": limits,
        "model": a.model,
        "judge_model": a.judge_model,
        "unpaired": a.unpaired,
        "baseline": a.baseline,
        "report_only": a.report_only,
        "unattended": a.unattended,
        "isolate_user_settings": a.isolate_user_settings,
        "gates": target.get("gates", []),
        "snapshot_files": len(files),
    }
    rsilib.write_json(run_dir / "config.json", config)
    rsilib.write_json(run_dir / "target.json", target)
    rsilib.write_json(run_dir / "state.json", {
        "status": "running", "phase": "profile", "iteration": 0, "champion": "v0",
        "pending_challenger": None, "started_at": rsilib.now_iso(), "stop_reason": None,
    })
    rsilib.write_json(run_dir / "history.json", {"iterations": [], "attempts": {}, "do_not_repeat": []})
    rsilib.emit({"run_dir": str(run_dir), "workspace": str(workspace), "snapshot_files": len(files),
                 "limits": limits})


if __name__ == "__main__":
    main()
