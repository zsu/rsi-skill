#!/usr/bin/env python3
"""Track spend for a run and check it against the limits in config.json.

    python -I budget.py add    --run-dir R --kind subagent|orchestrator|other --tokens N [--cost-usd X] [--note "..."]
    python -I budget.py status --run-dir R
    python -I budget.py check  --run-dir R [--reserve-usd X] [--reserve-tokens N] [--stop]
    python -I budget.py set-limits --run-dir R [--max-cost-usd X] [--max-tokens N] [--max-hours H]
        [--max-iterations N] [--cases N] [--parallel N] [--timeout S] [--max-cost-per-case-usd X]
        [--patience N] [--max-turns N]

Case runs are recorded automatically by run_case.py (real total_cost_usd from Claude Code).
Subagent and orchestrator usage is recorded as tokens and priced at limits.subagent_usd_per_mtok
(an estimate). `check` exits 3 when a limit is reached (or would be, with the reserve), else 0.
With --stop, a check that exits 3 also records the stop in state.json (status "stopped",
stop_reason "limit:<what>", phase "finish"), so status, report and resume see a finished run.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rsilib  # noqa: E402


def status(run_dir, reserve_usd: float = 0.0, reserve_tokens: int = 0) -> dict:
    run = rsilib.Run(run_dir)
    limits = run.config["limits"]
    state = run.state
    entries = rsilib.read_jsonl(run.dir / "budget.jsonl")
    rate = float(limits.get("subagent_usd_per_mtok") or 0)
    cost_real = sum(float(e.get("cost_usd") or 0) for e in entries if e.get("kind") == "case")
    # Agent entries carry a real cost when the caller knew one; otherwise price their tokens.
    cost_est = sum(float(e.get("cost_usd") or 0) or int(e.get("tokens") or 0) / 1e6 * rate
                   for e in entries if e.get("kind") != "case")
    tokens = sum(int(e.get("tokens") or 0) for e in entries)
    started = rsilib.parse_iso(state.get("started_at") or run.config["created_at"])
    elapsed_h = (rsilib.parse_iso(rsilib.now_iso()) - started).total_seconds() / 3600
    iterations_done = len(run.history.get("iterations", []))
    total_cost = cost_real + cost_est
    exceeded = []
    if limits.get("max_cost_usd") is not None and total_cost + reserve_usd >= float(limits["max_cost_usd"]):
        exceeded.append("cost")
    if limits.get("max_tokens") is not None and tokens + reserve_tokens >= int(limits["max_tokens"]):
        exceeded.append("tokens")
    if limits.get("max_hours") is not None and elapsed_h >= float(limits["max_hours"]):
        exceeded.append("time")
    # iteration 0 is the baseline measurement; max_iterations counts the improvement iterations after it
    if limits.get("max_iterations") is not None and iterations_done - 1 >= int(limits["max_iterations"]):
        exceeded.append("iterations")
    case_costs = [float(e.get("cost_usd") or 0) for e in entries if e.get("kind") == "case"]
    return {
        "cost_usd": round(total_cost, 4), "cost_usd_cases": round(cost_real, 4),
        "cost_usd_estimated_agents": round(cost_est, 4), "tokens": tokens,
        "elapsed_hours": round(elapsed_h, 3), "iterations_done": iterations_done,
        "case_runs": len(case_costs),
        "mean_case_cost_usd": round(sum(case_costs) / len(case_costs), 4) if case_costs else None,
        "limits": {k: limits.get(k) for k in ("max_cost_usd", "max_tokens", "max_hours", "max_iterations")},
        "exceeded": exceeded,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add")
    p_add.add_argument("--run-dir", required=True)
    p_add.add_argument("--kind", default="subagent", choices=["subagent", "orchestrator", "other"])
    p_add.add_argument("--tokens", type=int, default=0)
    p_add.add_argument("--cost-usd", type=float, default=0.0)
    p_add.add_argument("--note", default="")
    p_add.add_argument("--iteration", type=int)
    p_set = sub.add_parser("set-limits")
    p_set.add_argument("--run-dir", required=True)
    for flag, typ in (("max-cost-usd", float), ("max-tokens", int), ("max-hours", float), ("max-iterations", int),
                      ("cases", int), ("parallel", int), ("timeout", int), ("max-cost-per-case-usd", float),
                      ("patience", int), ("max-turns", int)):
        p_set.add_argument(f"--{flag}", type=typ)
    for name in ("status", "check"):
        p = sub.add_parser(name)
        p.add_argument("--run-dir", required=True)
        p.add_argument("--reserve-usd", type=float, default=0.0)
        p.add_argument("--reserve-tokens", type=int, default=0)
        if name == "check":
            p.add_argument("--stop", action="store_true", help="record a limit stop in state.json on exit 3")
    a = ap.parse_args()
    if a.cmd == "add":
        run = rsilib.Run(a.run_dir)
        rec = {"at": rsilib.now_iso(), "kind": a.kind, "tokens": a.tokens, "cost_usd": a.cost_usd,
               "note": a.note, "iteration": a.iteration if a.iteration is not None else run.state.get("iteration")}
        rsilib.append_jsonl(run.dir / "budget.jsonl", rec)
        rsilib.emit(status(a.run_dir))
    if a.cmd == "set-limits":
        run = rsilib.Run(a.run_dir)
        cfg = run.config
        names = {"cases": "cases_per_iteration", "timeout": "timeout_seconds"}
        for k, v in vars(a).items():
            if k in ("cmd", "run_dir") or v is None:
                continue
            cfg["limits"][names.get(k, k)] = v
        rsilib.write_json(run.dir / "config.json", cfg)
        rsilib.emit({"limits": cfg["limits"]})
    s = status(a.run_dir, a.reserve_usd, a.reserve_tokens)
    if a.cmd == "check" and s["exceeded"] and a.stop:
        run = rsilib.Run(a.run_dir)
        state = run.state
        state.update(status="stopped", stop_reason="limit:" + ",".join(s["exceeded"]), phase="finish")
        run.save_state(state)
        s["stop_reason"] = state["stop_reason"]
    rsilib.emit(s, 3 if (a.cmd == "check" and s["exceeded"]) else 0)


if __name__ == "__main__":
    main()
