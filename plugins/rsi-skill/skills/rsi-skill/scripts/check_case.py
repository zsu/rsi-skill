#!/usr/bin/env python3
"""Run a case's deterministic checks against one finished run.

    python -I check_case.py --run-dir R --iteration N --case-id ID --role champion
    python -I check_case.py --run-dir R --iteration N --all

Reads the case from iterations/NNN/cases/<id>/case.json (or the replayed case's own iteration)
and writes checks.json next to metrics.json. Check types (each has id, severity, optional desc):

  file_exists     {path}                  glob, relative to the sandbox
  file_absent     {path}
  contains        {path?, text, ignore_case?}       path defaults to the final message
  not_contains    {path?, text, ignore_case?}
  regex           {path?, pattern, flags?}           Python regex, search
  not_regex       {path?, pattern, flags?}
  json_path       {path, expr, equals?|min?|max?|exists?}   expr: a.b.0.c
  number_near     {path?, pattern, target, rel_tol?|abs_tol?}  first capture group is the number
  command         {cmd, expect_exit?=0, timeout?=300}  shell command, cwd = sandbox
  skill_invoked   {}                      the run used the skill (Skill tool or SKILL.md read)

Placeholders in path/cmd: {sandbox} {out} {skill} {final} {case} {python}.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rsilib  # noqa: E402


def _expand(s: str, ctx: dict) -> str:
    for k, v in ctx.items():
        s = s.replace("{" + k + "}", str(v))
    return s


def _resolve(path: str | None, ctx: dict, sandbox: Path) -> list:
    if not path:
        return [Path(ctx["final"])]
    p = _expand(path, ctx)
    pat = p if Path(p).is_absolute() else str(sandbox / p)
    return [Path(x) for x in sorted(glob.glob(pat, recursive=True))]


def _text(paths: list) -> str | None:
    texts = [rsilib.read_text(p) for p in paths if p.is_file()]
    return "\n".join(texts) if texts else None


def _json_get(data, expr: str):
    cur = data
    for part in expr.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


def run_check(chk: dict, ctx: dict, sandbox: Path, metrics: dict) -> dict:
    typ = chk.get("type")
    res = {"id": chk.get("id") or typ, "type": typ, "severity": chk.get("severity", "major"),
           "desc": chk.get("desc", ""), "passed": False, "evidence": ""}
    try:
        if typ in ("file_exists", "file_absent"):
            hits = _resolve(chk["path"], ctx, sandbox)
            res["passed"] = bool(hits) if typ == "file_exists" else not hits
            res["evidence"] = f"{len(hits)} match(es): " + ", ".join(str(h.relative_to(sandbox)) if h.is_relative_to(sandbox) else str(h) for h in hits[:5])
        elif typ in ("contains", "not_contains"):
            text = _text(_resolve(chk.get("path"), ctx, sandbox))
            if text is None:
                res["evidence"] = "file not found"
                res["passed"] = typ == "not_contains" and bool(chk.get("missing_ok"))
            else:
                needle = chk["text"]
                hay = text.lower() if chk.get("ignore_case") else text
                found = (needle.lower() if chk.get("ignore_case") else needle) in hay
                res["passed"] = found if typ == "contains" else not found
                res["evidence"] = ("found" if found else "not found") + f": {needle[:80]!r}"
        elif typ in ("regex", "not_regex"):
            text = _text(_resolve(chk.get("path"), ctx, sandbox))
            if text is None:
                res["evidence"] = "file not found"
            else:
                flags = 0
                for ch in chk.get("flags", ""):
                    flags |= {"i": re.I, "m": re.M, "s": re.S}.get(ch, 0)
                m = re.search(chk["pattern"], text, flags)
                res["passed"] = bool(m) if typ == "regex" else not m
                res["evidence"] = f"match: {m.group(0)[:120]!r}" if m else "no match"
        elif typ == "json_path":
            paths = _resolve(chk["path"], ctx, sandbox)
            if not paths:
                res["evidence"] = "file not found"
            else:
                data = json.loads(rsilib.read_text(paths[0]))
                try:
                    val = _json_get(data, chk["expr"])
                    present = True
                except (KeyError, IndexError, ValueError, TypeError):
                    val, present = None, False
                ok = present
                if "exists" in chk:
                    ok = present == bool(chk["exists"])
                if present and "equals" in chk:
                    ok = ok and val == chk["equals"]
                if present and "min" in chk:
                    ok = ok and float(val) >= float(chk["min"])
                if present and "max" in chk:
                    ok = ok and float(val) <= float(chk["max"])
                res["passed"] = bool(ok)
                res["evidence"] = f"{chk['expr']} = {json.dumps(val)[:120]}" if present else f"{chk['expr']} missing"
        elif typ == "number_near":
            text = _text(_resolve(chk.get("path"), ctx, sandbox))
            m = re.search(chk["pattern"], text or "", re.I | re.S)
            if not m:
                res["evidence"] = "number not found"
            else:
                val = float(m.group(1).replace(",", "").replace("$", ""))
                tgt = float(chk["target"])
                tol = abs(tgt) * float(chk["rel_tol"]) if "rel_tol" in chk else float(chk.get("abs_tol", 0))
                res["passed"] = abs(val - tgt) <= tol
                res["evidence"] = f"found {val}, target {tgt} ± {tol}"
                res["value"] = val
        elif typ == "command":
            cmd = _expand(chk["cmd"], ctx)
            r = subprocess.run(cmd, shell=True, cwd=str(sandbox), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=int(chk.get("timeout", 300)))
            want = int(chk.get("expect_exit", 0))
            res["passed"] = r.returncode == want
            tail = (r.stdout[-1500:] + ("\n[stderr]\n" + r.stderr[-1000:] if r.stderr.strip() else "")).strip()
            res["evidence"] = f"exit {r.returncode} (want {want}). {tail}"
        elif typ == "skill_invoked":
            res["passed"] = bool(metrics.get("skill_invoked"))
            res["evidence"] = ", ".join(metrics.get("skill_invocations") or []) or "skill never invoked"
        else:
            res["evidence"] = f"unknown check type {typ!r}"
    except subprocess.TimeoutExpired:
        res["evidence"] = "command timed out"
    except Exception as e:  # a broken check must not crash the batch
        res["evidence"] = f"check error: {type(e).__name__}: {e}"
    return res


def check_run(run: rsilib.Run, iteration: int, case_id: str, role: str) -> dict:
    role_dir = run.iteration_dir(iteration) / "runs" / case_id / role
    metrics = rsilib.read_json(role_dir / "metrics.json")
    if not metrics:
        return {"case_id": case_id, "role": role, "error": "no metrics.json (run missing)"}
    case_dir = None
    for it_dir in [run.iteration_dir(iteration)] + sorted((run.dir / "iterations").glob("*"), reverse=True):
        if (it_dir / "cases" / case_id / "case.json").exists():
            case_dir = it_dir / "cases" / case_id
            break
    case = rsilib.read_json(case_dir / "case.json") if case_dir else {}
    sandbox = Path(metrics["sandbox"])
    ctx = {"sandbox": sandbox, "out": sandbox, "skill": metrics.get("skill_path") or "",
           "final": role_dir / "final.md", "case": case_dir or "", "python": sys.executable}
    results = []
    if metrics.get("status") in ("timeout", "error"):
        results.append({"id": "run_completed", "type": "run", "severity": "critical", "passed": False,
                        "evidence": f"run status {metrics['status']}; see stderr.txt"})
    for chk in case.get("deterministic_checks", []):
        if role == "noskill" and chk.get("type") == "skill_invoked":
            continue
        results.append(run_check(chk, ctx, sandbox, metrics))
    passed = sum(1 for r in results if r["passed"])
    out = {"case_id": case_id, "role": role, "candidate": metrics.get("candidate"), "checks": results,
           "summary": {"passed": passed, "failed": len(results) - passed, "total": len(results),
                       "pass_rate": round(passed / len(results), 4) if results else None}}
    rsilib.write_json(role_dir / "checks.json", out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--iteration", type=int, required=True)
    ap.add_argument("--case-id")
    ap.add_argument("--role")
    ap.add_argument("--all", action="store_true", help="every run of the iteration")
    a = ap.parse_args()
    run = rsilib.Run(a.run_dir)
    if a.all:
        outs = []
        for role_dir in sorted((run.iteration_dir(a.iteration) / "runs").glob("*/*")):
            if (role_dir / "metrics.json").exists():
                o = check_run(run, a.iteration, role_dir.parent.name, role_dir.name)
                outs.append({"case_id": o["case_id"], "role": o["role"], **(o.get("summary") or {}),
                             "failed_ids": [c["id"] for c in o.get("checks", []) if not c["passed"]]})
        rsilib.emit({"iteration": a.iteration, "runs": outs})
    if not (a.case_id and a.role):
        raise SystemExit("give --case-id and --role, or --all")
    rsilib.emit(check_run(run, a.iteration, a.case_id, a.role))


if __name__ == "__main__":
    main()
