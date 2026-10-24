#!/usr/bin/env python3
"""Score an iteration, prepare blind pairs, and decide promotion / next action / stop.

    python -I aggregate.py blind  --run-dir R --iteration N [--seed S]
    python -I aggregate.py score  --run-dir R --iteration N
    python -I aggregate.py decide --run-dir R --iteration N [--dry-run] [--veto "reason"]

blind   for each case run by both champion and challenger, copies the two outputs to
        iterations/NNN/comparison/<case>/{A,B}/ in a random order per case and records the
        order in <case>.order.json (the comparator never sees which is which).
score   merges checks.json + grading.json (+ comparison verdicts) into scoreboard.json and a
        skill-creator-compatible benchmark.json.
decide  applies the deterministic promotion gate and stop rules (references/stopping.md),
        updates history.json and state.json, and writes decision.json. Exit 0 continue, 10 stop.
"""
from __future__ import annotations

import argparse
import math
import random
import re
import shutil
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import budget  # noqa: E402
import rsilib  # noqa: E402

MAX_COPY_BYTES = 20 * 1024 * 1024


# --------------------------------------------------------------------------- findings

def goal_threshold(run: rsilib.Run) -> int:
    return rsilib.severity_rank(run.goal.get("blocking_severity", "major"))


def run_findings(run: rsilib.Run, role_dir: Path) -> list:
    """Findings for one run: failed deterministic checks plus grader findings, deduplicated."""
    thr = goal_threshold(run)
    aliases = rsilib.read_json(run.dir / "key_aliases.json", {}) or {}
    checks = rsilib.read_json(role_dir / "checks.json", {}) or {}
    grading = rsilib.read_json(role_dir / "grading.json", {}) or {}
    out, by_check = [], {}
    for f in grading.get("findings", []):
        f = dict(f)
        f.setdefault("key", re.sub(r"[^a-z0-9]+", "-", (f.get("title") or "finding").lower()).strip("-")[:60])
        f.setdefault("severity", "major")
        f.setdefault("class", "skill_general")
        f["key"] = aliases.get(f["key"], f["key"])  # graders may name one defect differently
        f["source"] = "grader"
        if f.get("check_id"):
            by_check[f["check_id"]] = f
        out.append(f)
    for c in checks.get("checks", []):
        if c.get("passed"):
            continue
        if c["id"] in by_check:  # the grader explained this failure; keep its class and key
            g = by_check[c["id"]]
            if rsilib.severity_rank(c.get("severity")) > rsilib.severity_rank(g.get("severity")):
                g["severity"] = c["severity"]
            continue
        out.append({"key": f"check-{c['id']}", "title": c.get("desc") or c["id"], "severity": c.get("severity", "major"),
                    "class": "skill_general",
                    "evidence": c.get("evidence", "")[:500], "source": "check", "check_id": c["id"]})
    for f in out:
        sev_ok = rsilib.severity_rank(f["severity"]) >= thr
        f["blocking"] = sev_ok and f["class"] not in ("case_specific", "external")
        f["external_blocking"] = sev_ok and f["class"] == "external"
    return out


def case_meta(run: rsilib.Run, iteration: int, case_id: str) -> dict:
    p = run.iteration_dir(iteration) / "cases" / case_id / "case.json"
    if p.exists():
        return rsilib.read_json(p) or {}
    for it_dir in sorted((run.dir / "iterations").glob("*"), reverse=True):
        q = it_dir / "cases" / case_id / "case.json"
        if q.exists():
            return rsilib.read_json(q) or {}
    return {}


def summarize(run: rsilib.Run, role_dirs: list) -> dict:
    n = len(role_dirs)
    by_sev = {s: 0 for s in rsilib.SEVERITIES}
    keys, blocking, blocking_cases, ext_cases, crit_keys = {}, 0, 0, 0, set()
    check_pass, check_total, exp_pass, exp_total, quality, costs, durs, invoked = 0, 0, 0, 0, [], [], [], []
    per_case = {}
    for rd in role_dirs:
        fs = run_findings(run, rd)
        b = [f for f in fs if f["blocking"]]
        e = [f for f in fs if f["external_blocking"]]
        for f in fs:
            by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
            if f["blocking"]:
                keys[f["key"]] = keys.get(f["key"], 0) + 1
                if f["severity"] == "critical":
                    crit_keys.add(f["key"])
        blocking += len(b)
        blocking_cases += 1 if b else 0
        m = rsilib.read_json(rd / "metrics.json", {}) or {}
        inconclusive = bool(e) or m.get("status") in ("timeout", "error") and not b
        ext_cases += 1 if inconclusive else 0
        ch = (rsilib.read_json(rd / "checks.json", {}) or {}).get("summary") or {}
        check_pass += ch.get("passed") or 0
        check_total += ch.get("total") or 0
        g = rsilib.read_json(rd / "grading.json", {}) or {}
        gs = g.get("summary") or {}
        exp_pass += gs.get("passed") or 0
        exp_total += gs.get("total") or 0
        if isinstance(g.get("overall_quality"), (int, float)):
            quality.append(g["overall_quality"])
        if m.get("cost_usd") is not None:
            costs.append(float(m["cost_usd"]))
        if m.get("duration_seconds") is not None:
            durs.append(float(m["duration_seconds"]))
        if m.get("skill_invoked") is not None:
            invoked.append(1 if m["skill_invoked"] else 0)
        per_case[rd.parent.name] = {"blocking": len(b), "keys": sorted({f["key"] for f in b}),
                                    "inconclusive": inconclusive, "graded": bool(g), "status": m.get("status")}
    return {
        "cases": n, "conclusive": n - ext_cases, "inconclusive": ext_cases,
        "blocking_findings": blocking, "blocking_cases": blocking_cases, "by_severity": by_sev,
        "blocking_keys": keys, "critical_keys": sorted(crit_keys),
        "check_pass_rate": round(check_pass / check_total, 4) if check_total else None,
        "criteria_pass_rate": round(exp_pass / exp_total, 4) if exp_total else None,
        "quality_mean": round(statistics.mean(quality), 3) if quality else None,
        "cost_usd": round(sum(costs), 4), "duration_mean_s": round(statistics.mean(durs), 1) if durs else None,
        "skill_invoked_rate": round(sum(invoked) / len(invoked), 3) if invoked else None,
        "ungraded": [k for k, v in per_case.items() if not v["graded"]],
        "per_case": per_case,
    }


def role_dirs(run: rsilib.Run, iteration: int, role: str, anchor=None) -> list:
    out = []
    for rd in sorted((run.iteration_dir(iteration) / "runs").glob(f"*/{role}")):
        if not (rd / "metrics.json").exists():
            continue
        if anchor is not None and bool(case_meta(run, iteration, rd.parent.name).get("anchor")) != anchor:
            continue
        out.append(rd)
    return out


# --------------------------------------------------------------------------- blind pairs

def blind(run: rsilib.Run, iteration: int, seed=None) -> dict:
    it = run.iteration_dir(iteration)
    comp = it / "comparison"
    pairs = []
    for case_dir in sorted((it / "runs").glob("*")):
        ch, cl = case_dir / "champion", case_dir / "challenger"
        if not ((ch / "metrics.json").exists() and (cl / "metrics.json").exists()):
            continue
        rng = random.Random(f"{run.config['run_id']}:{iteration}:{case_dir.name}:{seed}")
        order = {"A": "challenger", "B": "champion"} if rng.random() < 0.5 else {"A": "champion", "B": "challenger"}
        dst = comp / case_dir.name
        if dst.exists():
            shutil.rmtree(dst)
        for label, role in order.items():
            src = case_dir / role
            side = dst / label
            side.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / "final.md", side / "final.md")
            m = rsilib.read_json(src / "metrics.json") or {}
            sandbox, total = Path(m.get("sandbox", "")), 0
            for rel in m.get("files_created", []):
                p = sandbox / rel
                if p.is_file() and total + p.stat().st_size <= MAX_COPY_BYTES:
                    (side / "files" / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, side / "files" / rel)
                    total += p.stat().st_size
            (side / "run.txt").write_text(
                f"status: {m.get('status')}\nturns: {m.get('num_turns')}\ntool calls: {m.get('total_tool_calls')}\n"
                f"duration_s: {m.get('duration_seconds')}\n", encoding="utf-8")
        rsilib.write_json(comp / f"{case_dir.name}.order.json", order)
        pairs.append({"case_id": case_dir.name, "A": str(dst / "A"), "B": str(dst / "B"),
                      "case": str(it / "cases" / case_dir.name / "case.json"),
                      "verdict_path": str(comp / f"{case_dir.name}.json")})
    return {"iteration": iteration, "pairs": pairs}


def pairwise(run: rsilib.Run, iteration: int) -> dict:
    comp = run.iteration_dir(iteration) / "comparison"
    wins = losses = ties = 0
    rows = []
    for order_p in sorted(comp.glob("*.order.json")):
        cid = order_p.name[:-len(".order.json")]
        order = rsilib.read_json(order_p)
        v = rsilib.read_json(comp / f"{cid}.json")
        if not v:
            continue
        w = v.get("winner")
        verdict = order.get(w, "tie") if w in ("A", "B") else "tie"
        sw = rsilib.read_json(comp / f"{cid}.swap.json")
        if sw:  # second judgment with the order reversed: disagreement → tie
            sw_order = {"A": order["B"], "B": order["A"]}
            w2 = sw.get("winner")
            v2 = sw_order.get(w2, "tie") if w2 in ("A", "B") else "tie"
            if v2 != verdict:
                verdict = "tie"
        wins += verdict == "challenger"
        losses += verdict == "champion"
        ties += verdict == "tie"
        rows.append({"case_id": cid, "winner": verdict, "confidence": v.get("confidence")})
    return {"challenger_wins": wins, "champion_wins": losses, "ties": ties, "net": wins - losses, "cases": rows}


# --------------------------------------------------------------------------- score

def score(run: rsilib.Run, iteration: int) -> dict:
    sb = {"iteration": iteration, "at": rsilib.now_iso(), "roles": {}}
    for role in ("champion", "challenger", "noskill"):
        fresh = role_dirs(run, iteration, role, anchor=False)
        anch = role_dirs(run, iteration, role, anchor=True)
        if fresh or anch:
            sb["roles"][role] = {"candidate": (rsilib.read_json((fresh or anch)[0] / "metrics.json") or {}).get("candidate"),
                                 "fresh": summarize(run, fresh), "anchors": summarize(run, anch) if anch else None}
    rep = role_dirs(run, iteration, "replay")
    if rep:
        s = summarize(run, rep)
        fixed = sum(1 for v in s["per_case"].values() if v["blocking"] == 0 and not v["inconclusive"])
        s["fixed"] = fixed
        s["fix_rate"] = round(fixed / len(rep), 3)
        sb["replay"] = s
    sb["pairwise"] = pairwise(run, iteration)
    rsilib.write_json(run.iteration_dir(iteration) / "scoreboard.json", sb)
    # skill-creator-style benchmark.json (viewer-compatible field names)
    runs = []
    for role in sb["roles"]:
        for rd in role_dirs(run, iteration, role):
            g = rsilib.read_json(rd / "grading.json", {}) or {}
            m = rsilib.read_json(rd / "metrics.json", {}) or {}
            gs = g.get("summary") or {}
            runs.append({"eval_id": rd.parent.name, "eval_name": rd.parent.name, "configuration": role, "run_number": 1,
                         "result": {"pass_rate": gs.get("pass_rate"), "passed": gs.get("passed"), "failed": gs.get("failed"),
                                    "total": gs.get("total"), "time_seconds": m.get("duration_seconds"),
                                    "tokens": m.get("tokens"), "tool_calls": m.get("total_tool_calls"),
                                    "errors": 0 if m.get("status") == "ok" else 1},
                         "expectations": g.get("expectations", [])})
    summary = {}
    for role in sb["roles"]:
        rates = [r["result"]["pass_rate"] for r in runs if r["configuration"] == role and r["result"]["pass_rate"] is not None]
        if rates:
            summary[role] = {"pass_rate": {"mean": round(statistics.mean(rates), 4),
                                           "stddev": round(statistics.pstdev(rates), 4),
                                           "min": min(rates), "max": max(rates)}}
    rsilib.write_json(run.iteration_dir(iteration) / "benchmark.json", {
        "metadata": {"skill_name": run.config["target"]["name"], "skill_path": run.config["target"]["skill_dir"],
                     "timestamp": rsilib.now_iso(), "evals_run": sorted({r["eval_id"] for r in runs}),
                     "runs_per_configuration": 1},
        "runs": runs, "run_summary": summary, "notes": []})
    return sb


# --------------------------------------------------------------------------- decide

def decide(run: rsilib.Run, iteration: int, dry_run: bool = False, veto: str | None = None) -> dict:
    cfg, goal, limits = run.config, run.goal, run.config["limits"]
    state, hist = run.state, run.history
    sb = rsilib.read_json(run.iteration_dir(iteration) / "scoreboard.json") or score(run, iteration)
    roles = sb.get("roles", {})
    champ_id = state["champion"]
    champ = roles.get("champion")
    chall = roles.get("challenger")
    chall_id = chall["candidate"] if chall else None
    reasons, gate = [], {}
    promoted = False
    prior_iters = hist.get("iterations", [])

    if chall:
        cmeta = run.candidate_meta(chall_id)
        gates = rsilib.read_json(run.dir / "candidates" / f"{chall_id}.gates.json")
        leak = rsilib.read_json(run.dir / "candidates" / f"{chall_id}.leak.json")
        over = rsilib.read_json(run.dir / "candidates" / f"{chall_id}.overfit.json")
        cf, af = chall["fresh"], (champ or {}).get("fresh")
        if af is None:  # unpaired: compare with the champion's last fresh score
            last = next((h for h in reversed(prior_iters) if h.get("champion_fresh")), None)
            af = (last or {}).get("champion_fresh") or {"blocking_findings": math.inf, "critical_keys": [],
                                                         "blocking_cases": math.inf, "cases": 1}
            reasons.append("unpaired comparison (champion not re-run on these cases)")
        gate["static_gates"] = bool(gates) and not gates.get("blocking")
        gate["leak_scan"] = bool(leak) and leak.get("passed", False)
        gate["overfit_review"] = bool(over) and over.get("verdict") == "pass"
        new_crit = sorted(set(cf["critical_keys"]) - set(af.get("critical_keys", [])))
        gate["no_new_critical"] = not new_crit
        # normalise by case count so unpaired comparisons stay fair
        c_rate = cf["blocking_findings"] / max(cf["cases"], 1)
        a_rate = af["blocking_findings"] / max(af.get("cases", 1), 1)
        gate["not_worse"] = c_rate <= a_rate
        pw = sb.get("pairwise", {})
        rep = sb.get("replay")
        rep_ok = rep is None or rep["fix_rate"] >= float(limits.get("replay_fix_min", 0.5))
        gate["replay_fixed"] = rep_ok
        improved = (c_rate < a_rate) or (pw.get("net", 0) >= int(limits.get("promote_margin", 1))) or \
                   (rep is not None and rep["fix_rate"] >= float(limits.get("replay_fix_min", 0.5))
                    and c_rate <= a_rate and pw.get("net", 0) >= 0)
        gate["improved"] = improved
        ca, aa = chall.get("anchors"), (champ or {}).get("anchors")
        gate["anchors_no_regression"] = not (ca and aa and ca["blocking_findings"] > aa["blocking_findings"])
        if veto:
            gate["orchestrator_review"] = False
            reasons.append(f"vetoed: {veto}")
        promoted = all(gate.values())
        if new_crit:
            reasons.append(f"new critical findings: {', '.join(new_crit)}")
        for k, v in gate.items():
            if not v:
                reasons.append(f"gate failed: {k}")
        # attempts per targeted finding key
        found_after = set(cf["blocking_keys"]) | set((rep or {}).get("blocking_keys", {}))
        for key in cmeta.get("targets", []):
            if key in found_after or not promoted:
                hist["attempts"][key] = hist["attempts"].get(key, 0) + 1
        for key in cmeta.get("unfixable", []):
            hist["attempts"][key] = max(hist["attempts"].get(key, 0), 99)
        hist.setdefault("do_not_repeat", []).append({
            "candidate": chall_id, "iteration": iteration, "mode": cmeta.get("mode"), "targets": cmeta.get("targets", []),
            "approach": cmeta.get("approach", ""), "outcome": "promoted" if promoted else "rejected",
            "why": "; ".join(reasons)[:400]})
        hist["do_not_repeat"] = hist["do_not_repeat"][-30:]

    # a challenger rejected before it could be evaluated (leak/overfit/gates twice) counts as a failed attempt
    pre_rejected = []
    for mp in (run.dir / "candidates").glob("v*.meta.json"):
        m = rsilib.read_json(mp) or {}
        if m.get("status") == "rejected" and m.get("rejected_iteration") == iteration - 1 and not m.get("evaluated"):
            pre_rejected.append(m["id"])
            for key in m.get("targets", []):
                hist["attempts"][key] = hist["attempts"].get(key, 0) + 1

    after_id = chall_id if promoted else champ_id
    after = chall["fresh"] if promoted else (champ or {}).get("fresh")
    after_anchor = (chall if promoted else (champ or {})).get("anchors") if (chall or champ) else None
    after_gates = rsilib.read_json(run.dir / "candidates" / f"{after_id}.gates.json") or {}

    # goal check on fresh cases
    clean, why_not_clean = False, []
    if after:
        min_conclusive = max(2, math.ceil(after["cases"] * 2 / 3)) if after["cases"] >= 2 else after["cases"]
        if after["blocking_findings"]:
            why_not_clean.append(f"{after['blocking_findings']} blocking finding(s)")
        if after["conclusive"] < min_conclusive:
            why_not_clean.append(f"only {after['conclusive']}/{after['cases']} conclusive cases")
        if after.get("ungraded"):
            why_not_clean.append(f"ungraded cases: {', '.join(after['ungraded'])}")
        if after_anchor and after_anchor["blocking_findings"]:
            why_not_clean.append("anchor failures")
        if after_gates.get("blocking"):
            why_not_clean.append("static gates blocking")
        if goal.get("min_check_pass_rate") is not None and (after["check_pass_rate"] or 0) < goal["min_check_pass_rate"]:
            why_not_clean.append("check pass rate below goal")
        if goal.get("min_criteria_pass_rate") is not None and (after["criteria_pass_rate"] or 0) < goal["min_criteria_pass_rate"]:
            why_not_clean.append("criteria pass rate below goal")
        if goal.get("min_quality") is not None and (after["quality_mean"] or 0) < goal["min_quality"]:
            why_not_clean.append("quality below goal")
        clean = not why_not_clean
    prev_streak = prior_iters[-1]["clean_streak"] if prior_iters else 0
    clean_streak = prev_streak + 1 if clean else 0
    if promoted:  # a new champion must earn its own streak
        clean_streak = 1 if clean else 0
    confirm = int(goal.get("confirm_streak", 2))
    goal_met = clean_streak >= confirm

    evaluated = bool(chall) or bool(pre_rejected)
    prev_np = prior_iters[-1]["non_promotion_streak"] if prior_iters else 0
    non_promo = 0 if promoted else (prev_np + 1 if evaluated else prev_np)
    divergent_tried = bool(prior_iters[-1].get("divergent_tried")) if prior_iters else False
    if promoted:
        divergent_tried = False
    last_mode = run.candidate_meta(chall_id).get("mode") if chall_id else None
    if chall and last_mode == "divergent":
        divergent_tried = True

    stuck = sorted(k for k, v in hist["attempts"].items() if v >= 2)
    remaining = set((after or {}).get("blocking_keys", {})) | set((after_anchor or {}).get("blocking_keys", {}))
    record = {
        "iteration": iteration, "at": rsilib.now_iso(), "champion_before": champ_id, "challenger": chall_id,
        "promoted": promoted, "champion_after": after_id, "gate": gate, "reasons": reasons,
        "pre_rejected": pre_rejected, "champion_fresh": after and {k: after[k] for k in (
            "cases", "conclusive", "blocking_findings", "blocking_cases", "critical_keys", "blocking_keys",
            "check_pass_rate", "criteria_pass_rate", "quality_mean")},
        "pairwise": sb.get("pairwise"), "replay_fix_rate": (sb.get("replay") or {}).get("fix_rate"),
        "clean": clean, "why_not_clean": why_not_clean, "clean_streak": clean_streak,
        "non_promotion_streak": non_promo, "divergent_tried": divergent_tried,
        "remaining_blocking_keys": sorted(remaining), "stuck_keys": stuck,
    }
    hist_iters = prior_iters + [record]

    # stop rules, in order
    stop, stop_reason, next_action = False, None, "improve"
    if cfg.get("report_only"):
        stop, stop_reason = True, "report_only"
    elif goal_met:
        stop, stop_reason = True, "goal_met"
    else:
        hist_tmp = dict(hist, iterations=hist_iters)
        if not dry_run:
            run.save_history(hist_tmp)
        over = budget.status(run.dir)["exceeded"]
        if over:
            stop, stop_reason = True, "limit:" + ",".join(over)
        elif remaining and all(k in stuck for k in remaining):
            stop, stop_reason = True, "unreachable:stuck"
        elif after and after["conclusive"] < 2 and len(hist_iters) >= 2 and \
                (hist_iters[-2].get("champion_fresh") or {}).get("conclusive", 99) < 2:
            stop, stop_reason = True, "unreachable:external"
        elif non_promo >= int(limits.get("patience", 2)) and divergent_tried and not promoted:
            stop, stop_reason = True, "plateau"
        elif clean:
            next_action = "confirm"
        elif non_promo >= int(limits.get("patience", 2)):
            next_action = "divergent"
        elif not remaining and not clean:
            next_action = "confirm"  # not clean for other reasons (inconclusive/ungraded): re-sample
    if stop:
        next_action = "stop"
    record.update({"stop": stop, "stop_reason": stop_reason, "next_action": next_action})
    hist["iterations"] = hist_iters
    decision = {**record, "goal_met": goal_met, "budget": budget.status(run.dir) if not dry_run else None}
    if not dry_run:
        run.save_history(hist)
        if chall_id:
            m = run.candidate_meta(chall_id)
            m["evaluated"] = True
            m["evaluated_iteration"] = iteration
            m["status"] = "champion" if promoted else "rejected"
            if not promoted:
                m["rejected_reason"] = "; ".join(reasons)[:400]
            run.save_candidate_meta(chall_id, m)
            if promoted:
                old = run.candidate_meta(champ_id)
                old["status"] = "retired"
                run.save_candidate_meta(champ_id, old)
        state["champion"] = after_id
        state["pending_challenger"] = None
        state["iteration"] = iteration
        state["phase"] = "finish" if stop else ("improve" if next_action in ("improve", "divergent") else "cases")
        state["next_action"] = next_action
        if stop:
            state["stop_reason"] = stop_reason
            state["status"] = "stopped"
        run.save_state(state)
        rsilib.write_json(run.iteration_dir(iteration) / "decision.json", decision)
    return decision


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["blind", "score", "decide"])
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--iteration", type=int, required=True)
    ap.add_argument("--seed")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--veto", help="decide: reject the challenger even if the gate passes (give the reason)")
    a = ap.parse_args()
    run = rsilib.Run(a.run_dir)
    if a.cmd == "blind":
        rsilib.emit(blind(run, a.iteration, a.seed))
    if a.cmd == "score":
        sb = score(run, a.iteration)
        brief = {"iteration": sb["iteration"], "pairwise": {k: v for k, v in sb["pairwise"].items() if k != "cases"},
                 "replay": {k: sb["replay"][k] for k in ("cases", "fixed", "fix_rate")} if sb.get("replay") else None,
                 "roles": {r: {k: v["fresh"][k] for k in ("cases", "conclusive", "blocking_findings", "blocking_keys",
                                                           "critical_keys", "check_pass_rate", "criteria_pass_rate",
                                                           "quality_mean", "cost_usd", "skill_invoked_rate", "ungraded")}
                           for r, v in sb["roles"].items()}}
        rsilib.emit(brief)
    d = decide(run, a.iteration, a.dry_run, a.veto)
    rsilib.emit(d, 10 if d["stop"] else 0)


if __name__ == "__main__":
    main()
