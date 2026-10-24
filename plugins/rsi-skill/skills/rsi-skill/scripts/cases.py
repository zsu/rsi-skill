#!/usr/bin/env python3
"""Validate and register an iteration's generated cases; query the case bank.

    python -I cases.py register --run-dir R --iteration N
    python -I cases.py bank     --run-dir R [--last 60]
    python -I cases.py failed   --run-dir R --iteration N [--role champion]
    python -I cases.py probes   --run-dir R
    python -I cases.py findings --run-dir R --iteration N [--role champion]
    python -I cases.py keys     --run-dir R [--alias old=canonical ...]

register: checks every iterations/NNN/cases/*/case.json against the schema in
references/schemas.md, makes ids unique across the run (prefix iNNN-, folder renamed to match),
and appends one summary line per case to <workspace>/case-bank/history.jsonl.
Exit 1 if any case is invalid (the list of problems is printed; fix and re-run).
bank: compact summaries of earlier cases (all runs) so the generator can avoid repeats.
probes: failure categories seen so far (no details) for the case generator.
findings: the blocking findings of a role in iteration N, grouped by key, with case and run
paths and attempt counts (input for the improver; stuck keys are flagged).
keys: every finding key graded so far, with titles and counts (give it to graders so they reuse
keys); --alias merges synonyms into <run>/key_aliases.json, which scoring applies.
failed: the cases of iteration N whose champion run had blocking findings (the replay set).
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rsilib  # noqa: E402

CHECK_TYPES = {"file_exists", "file_absent", "contains", "not_contains", "regex", "not_regex", "json_path",
               "number_near", "command", "skill_invoked"}
MAX_ID = 40
REQUIRED_FIELDS = {"file_exists": ["path"], "file_absent": ["path"], "contains": ["text"],
                   "not_contains": ["text"], "regex": ["pattern"], "not_regex": ["pattern"],
                   "json_path": ["path", "expr"], "number_near": ["pattern", "target"], "command": ["cmd"]}


def validate(case: dict, case_dir: Path) -> list:
    errs = []
    for f in ("id", "prompt"):
        if not case.get(f):
            errs.append(f"missing {f}")
    if case.get("invoke", "explicit") not in ("explicit", "natural"):
        errs.append("invoke must be explicit|natural")
    if case.get("interaction", "none") not in ("none", "questions_ok"):
        errs.append("interaction must be none|questions_ok")
    for i, c in enumerate(case.get("deterministic_checks", [])):
        t = c.get("type")
        if t not in CHECK_TYPES:
            errs.append(f"check {i}: unknown type {t!r}")
            continue
        for f in REQUIRED_FIELDS.get(t, []):
            if f not in c:
                errs.append(f"check {i} ({t}): missing {f}")
        if c.get("severity", "major") not in rsilib.SEVERITIES:
            errs.append(f"check {i}: bad severity {c.get('severity')!r}")
    crit = case.get("criteria", [])
    if not crit and not case.get("deterministic_checks"):
        errs.append("no criteria and no deterministic_checks: nothing to grade")
    for i, c in enumerate(crit):
        if not isinstance(c, dict) or not c.get("text"):
            errs.append(f"criterion {i}: needs text")
        elif c.get("severity", "major") not in rsilib.SEVERITIES:
            errs.append(f"criterion {i}: bad severity {c.get('severity')!r}")
    for fn in case.get("files", []):
        if not (case_dir / "files" / fn).exists():
            errs.append(f"listed input file missing: files/{fn}")
    return errs


def case_hash(case: dict) -> str:
    return hashlib.sha256(" ".join(case.get("prompt", "").lower().split()).encode()).hexdigest()[:16]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("register")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--iteration", type=int, required=True)
    p = sub.add_parser("bank")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--last", type=int, default=60)
    p = sub.add_parser("probes")
    p.add_argument("--run-dir", required=True)
    p = sub.add_parser("findings")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--iteration", type=int, required=True)
    p.add_argument("--role", default="champion")
    p = sub.add_parser("keys")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--alias", action="append", default=[])
    p = sub.add_parser("failed")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--iteration", type=int, required=True)
    p.add_argument("--role", default="champion")
    a = ap.parse_args()
    run = rsilib.Run(a.run_dir)
    bank_path = run.workspace / "case-bank" / "history.jsonl"

    if a.cmd == "register":
        prefix = f"i{a.iteration:03d}-"
        root = run.iteration_dir(a.iteration) / "cases"
        problems, registered = {}, []
        known = {r.get("hash") for r in rsilib.read_jsonl(bank_path)}
        for cj in sorted(root.glob("*/case.json")):
            case = rsilib.read_json(cj)
            d = cj.parent
            if case.get("anchor"):
                registered.append(case["id"])
                continue
            cid = case.get("id") or d.name
            if not cid.startswith(prefix):
                cid = prefix + cid
            # short ids keep sandbox paths under Windows' 260-character limit
            cid = re.sub(r"[^a-z0-9-]+", "-", cid.lower()).strip("-")[:MAX_ID]
            if case.get("id") != cid or d.name != cid:
                case["id"] = cid
                rsilib.write_json(cj, case)
                if d.name != cid:
                    new = d.parent / cid
                    if new.exists():
                        shutil.rmtree(new)
                    d.rename(new)
                    d = new
            errs = validate(case, d)
            if errs:
                problems[cid] = errs
                continue
            h = case_hash(case)
            if h in known:
                problems[cid] = ["duplicate of an earlier case (same prompt); write a new one"]
                continue
            rsilib.append_jsonl(bank_path, {
                "run": run.config["run_id"], "iteration": a.iteration, "id": cid, "hash": h,
                "title": case.get("title", ""), "tags": case.get("coverage_tags", []),
                "probes": case.get("probes_failure_category"), "prompt_head": " ".join(case["prompt"].split())[:160]})
            known.add(h)
            registered.append(cid)
        rsilib.emit({"registered": registered, "invalid": problems}, 1 if problems else 0)

    if a.cmd == "bank":
        rows = rsilib.read_jsonl(bank_path)[-a.last:]
        rsilib.emit({"count": len(rows), "cases": [{k: r.get(k) for k in ("id", "title", "tags", "probes", "prompt_head")}
                                                     for r in rows]})

    if a.cmd == "failed":
        import aggregate
        out = []
        for role_dir in sorted((run.iteration_dir(a.iteration) / "runs").glob(f"*/{a.role}")):
            blocking = [f for f in aggregate.run_findings(run, role_dir) if f["blocking"]]
            if blocking:
                out.append({"case": f"{a.iteration:03d}:{role_dir.parent.name}",
                            "keys": sorted({f["key"] for f in blocking})})
        rsilib.emit({"replay": ",".join(o["case"] for o in out), "cases": out})


    if a.cmd == "keys":
        import aggregate
        alias_path = run.dir / "key_aliases.json"
        aliases = rsilib.read_json(alias_path, {}) or {}
        for item in a.alias:
            old_key, canon = item.split("=", 1)
            aliases[old_key.strip()] = canon.strip()
        if a.alias:
            rsilib.write_json(alias_path, aliases)
        keys = {}
        for rd in sorted((run.dir / "iterations").glob("*/runs/*/*")):
            for f in aggregate.run_findings(run, rd):
                k = keys.setdefault(f["key"], {"key": f["key"], "title": f.get("title"), "severity": f["severity"],
                                               "category": f.get("category"), "count": 0})
                k["count"] += 1
        rsilib.emit({"keys": sorted(keys.values(), key=lambda k: -k["count"]), "aliases": aliases})

    if a.cmd == "probes":
        import aggregate
        cats = {}
        for rd in sorted((run.dir / "iterations").glob("*/runs/*/*")):
            for f in aggregate.run_findings(run, rd):
                if f["blocking"] or f["severity"] in ("major", "critical"):
                    c = f.get("category") or "uncategorized"
                    cats[c] = cats.get(c, 0) + 1
        rsilib.emit({"categories": sorted(cats, key=lambda c: -cats[c]), "counts": cats})

    if a.cmd == "findings":
        import aggregate
        hist = run.history
        groups = {}
        for rd in sorted((run.iteration_dir(a.iteration) / "runs").glob(f"*/{a.role}")):
            for f in aggregate.run_findings(run, rd):
                if not f["blocking"]:
                    continue
                g = groups.setdefault(f["key"], {"key": f["key"], "title": f.get("title"), "severity": f["severity"],
                                                 "category": f.get("category"), "suspected_files": set(),
                                                 "attempts": hist.get("attempts", {}).get(f["key"], 0), "occurrences": []})
                if rsilib.severity_rank(f["severity"]) > rsilib.severity_rank(g["severity"]):
                    g["severity"] = f["severity"]
                g["suspected_files"].update(f.get("suspected_files") or [])
                case_path = next(iter(sorted((run.dir / "iterations").glob(f"*/cases/{rd.parent.name}/case.json"))), "")
                g["occurrences"].append({"case": str(case_path), "run": str(rd),
                                         "evidence": str(f.get("evidence", ""))[:400]})
        out = []
        for g in groups.values():
            g["suspected_files"] = sorted(g["suspected_files"])
            g["stuck"] = g["attempts"] >= 2
            out.append(g)
        out.sort(key=lambda g: (g["stuck"], -rsilib.severity_rank(g["severity"]), -len(g["occurrences"])))
        rsilib.emit({"iteration": a.iteration, "role": a.role, "findings": out,
                     "do_not_repeat": hist.get("do_not_repeat", [])[-10:]})


if __name__ == "__main__":
    main()
