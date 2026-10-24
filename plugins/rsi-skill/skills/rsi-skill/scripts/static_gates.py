#!/usr/bin/env python3
"""Run the static gates (the target's own tests/linters plus a structure check) on a candidate.

    python -I static_gates.py --run-dir R --candidate v1 [--only id1,id2]

Gates come from config.json "gates" (detected by resolve_target.py; the profile step may edit
them). Each runs with cwd = the candidate copy of the project. Placeholders: {repo} {skill} {python}.
Writes candidates/<id>.gates.json. A failing gate is "blocking" only if it is required and it
passed on v0 (failures already present in the target are recorded but do not block).
Exit 0 when nothing blocks, 1 otherwise.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rsilib  # noqa: E402

LINK_RE = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


def structure_gate(skill_dir: Path, limits: dict) -> dict:
    problems, warnings = [], []
    p = skill_dir / "SKILL.md"
    if not p.exists():
        return {"passed": False, "evidence": "SKILL.md missing"}
    st = rsilib.skill_stats(skill_dir)
    if not st["has_frontmatter"]:
        problems.append("no YAML frontmatter")
    if not st["name"]:
        problems.append("frontmatter has no name")
    elif not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", st["name"]):
        problems.append(f"name {st['name']!r} is not lowercase-hyphen (max 64)")
    if not st["description"]:
        problems.append("frontmatter has no description")
    max_desc = int(limits.get("description_max_chars") or 1024)
    if st["description_chars"] > max_desc:
        problems.append(f"description {st['description_chars']} chars > {max_desc}")
    max_body = limits.get("body_max_lines")
    if max_body and st["body_lines"] > int(max_body):
        problems.append(f"body {st['body_lines']} lines > {max_body}")
    elif st["body_lines"] > 500:
        warnings.append(f"body {st['body_lines']} lines > 500 (guidance)")
    body = rsilib.parse_frontmatter(rsilib.read_text(p))[1]
    for m in LINK_RE.finditer(body):
        link = m.group(1)
        if re.match(r"^[a-z]+:", link) or link.startswith(("/", "$", "{", "<")):
            continue
        if not (skill_dir / link).exists():
            problems.append(f"broken relative link: {link}")
    return {"passed": not problems, "evidence": "; ".join(problems + warnings) or "ok",
            "stats": {k: st[k] for k in ("name", "description_chars", "body_lines")}}


def exec_gates(gates: list, root: Path, skill: Path, limits: dict, only=None) -> list:
    """Run gate commands with cwd = root (a project copy or the target itself)."""
    results = []
    for g in gates:
        if only and g["id"] not in only:
            continue
        if g["cmd"] == "builtin":
            r = structure_gate(skill, limits)
        else:
            cmd = g["cmd"].replace("{python}", f'"{sys.executable}"').replace("{repo}", str(root)).replace(
                "{skill}", str(skill.relative_to(root).as_posix()) if skill != root else ".")
            cwd = skill if g.get("cwd") == "skill" else root
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")  # never litter the project with __pycache__
            try:
                pr = subprocess.run(cmd, shell=True, cwd=str(cwd), capture_output=True, text=True, env=env,
                                    encoding="utf-8", errors="replace", timeout=int(g.get("timeout", 600)))
                tail = (pr.stdout[-2500:] + ("\n[stderr]\n" + pr.stderr[-1500:] if pr.stderr.strip() else "")).strip()
                r = {"passed": pr.returncode == 0, "evidence": f"exit {pr.returncode}. {tail}"}
            except subprocess.TimeoutExpired:
                r = {"passed": False, "evidence": "timed out"}
        r.update({"id": g["id"], "required": bool(g.get("required", True))})
        results.append(r)
    return results


def run_gates(run: rsilib.Run, candidate: str, only=None) -> dict:
    cfg = run.config
    root = run.candidate(candidate)
    results = exec_gates(cfg.get("gates", []), root, run.skill_dir_in(root), cfg["target"].get("limits", {}), only)

    base = None
    if candidate != "v0":
        base_path = run.dir / "candidates" / "v0.gates.json"
        base = rsilib.read_json(base_path)
        if base is None:
            base = run_gates(run, "v0", only)
    base_pass = {r["id"]: r["passed"] for r in (base or {}).get("results", [])} if base else {}
    for r in results:
        pre_existing = candidate != "v0" and base_pass.get(r["id"]) is False
        r["pre_existing_failure"] = (candidate == "v0" and not r["passed"]) or pre_existing
        r["blocking"] = (not r["passed"]) and r["required"] and not r["pre_existing_failure"]
    out = {"candidate": candidate, "at": rsilib.now_iso(), "results": results,
           "blocking": [r["id"] for r in results if r["blocking"]],
           "pre_existing_failures": [r["id"] for r in results if r["pre_existing_failure"]]}
    if not only:
        rsilib.write_json(run.dir / "candidates" / f"{candidate}.gates.json", out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--only")
    a = ap.parse_args()
    run = rsilib.Run(a.run_dir)
    out = run_gates(run, a.candidate, set(a.only.split(",")) if a.only else None)
    brief = {**out, "results": [{k: r.get(k) for k in ("id", "passed", "blocking", "pre_existing_failure")}
                                | {"evidence": r["evidence"][-600:]} for r in out["results"]]}
    rsilib.emit(brief, 1 if out["blocking"] else 0)


if __name__ == "__main__":
    main()
