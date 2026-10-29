#!/usr/bin/env python3
"""Persistent anchor cases: a small, opt-in benchmark kept in the target's git.

    python -I anchors.py list     --run-dir R
    python -I anchors.py export   --run-dir R --iteration N
    python -I anchors.py add      --run-dir R --iteration N --case CASE_ID --why "..." [--force]
    python -I anchors.py validate --run-dir R

Location comes from resolve_target.py ("anchors.path"): <skill>/evals/evals.json, or
<repo>/evals/<skill-name>/evals.json when a packager would ship the skill's evals/ folder.
Format: Anthropic evals.json (as skill-creator reads it) with an "rsi" block per eval;
"files" are relative to the folder that holds evals.json.

Helper files a case keeps beside case.json (expected-value scripts such as verify.py, small data) are
the case's own and must never reach the sandbox. add stores them under case/<id>/ and lists them in
"rsi.case_files"; export puts them back beside the exported case.json, so checks that call
{case}/verify.py still resolve. Entries without "rsi.case_files" export as before.

export copies every anchor into iterations/NNN/cases/anchor-<id>/ (case.anchor = true) so it runs
beside the fresh cases as a regression guard. add copies a case from this run into the anchor
file (in the target, uncommitted). An anchor needs a ground truth or at least one deterministic
check, at most 8 anchors, at most 2 MB of input files and 512 KB of helper files each, and every
{case}/... path a check uses must be a helper file (--force overrides the last three).
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rsilib  # noqa: E402

MAX_ANCHORS = 8
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_CASE_BYTES = 512 * 1024
CASE_REF = re.compile(r"\{case\}[/\\]+([^\s\"']+)")
SKIP_PARTS = {"__pycache__", ".pytest_cache"}


def load(run: rsilib.Run):
    path = Path(run.config["target"]["anchors"]["path"])
    data = rsilib.read_json(path) or {"skill_name": run.config["target"]["name"], "evals": []}
    return path, data


def helper_files(cdir: Path) -> list[Path]:
    """Files in a case folder that are neither case.json nor sandbox inputs under files/."""
    out = []
    for p in sorted(cdir.rglob("*")):
        rel = p.relative_to(cdir)
        if not p.is_file() or rel.parts[0] == "files" or rel.as_posix() == "case.json":
            continue
        if SKIP_PARTS.intersection(rel.parts) or p.suffix == ".pyc":
            continue
        out.append(p)
    return out


def case_refs(checks: list) -> set[str]:
    """Paths under {case}/ that the deterministic checks reference."""
    refs = set()
    for chk in checks or []:
        for k in ("cmd", "path"):
            for m in CASE_REF.finditer(str(chk.get(k) or "")):
                refs.add(m.group(1).replace("\\", "/"))
    return refs


def helper_rel(ev: dict, f: str) -> Path | None:
    """Where an rsi.case_files entry goes inside the exported case folder (None: refuse to place it)."""
    parts = Path(f).parts
    rel = Path(*parts[2:]) if len(parts) > 2 and parts[:2] == ("case", str(ev["id"])) else Path(Path(f).name)
    if rel.is_absolute() or ".." in rel.parts or rel.parts[0] == "files" or rel.as_posix() == "case.json":
        return None
    return rel


def to_case(ev: dict, base: Path) -> dict:
    r = ev.get("rsi") or {}
    crit = r.get("criteria") or [{"id": f"e{i}", "text": t, "severity": "major"}
                                 for i, t in enumerate(ev.get("expectations", []))]
    return {"id": f"anchor-{ev['id']}", "title": r.get("title") or ev.get("expected_output", "")[:80],
            "prompt": ev["prompt"], "invoke": r.get("invoke", "explicit"), "interaction": r.get("interaction", "none"),
            "files": [Path(f).name for f in ev.get("files", [])], "deterministic_checks": r.get("deterministic_checks", []),
            "criteria": crit, "ground_truth": r.get("ground_truth"), "coverage_tags": r.get("coverage_tags", []),
            "expected_output": ev.get("expected_output", ""), "anchor": True,
            "timeout_seconds": r.get("timeout_seconds"), "max_turns": r.get("max_turns")}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["list", "export", "add", "validate"])
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--iteration", type=int)
    ap.add_argument("--case")
    ap.add_argument("--why", default="")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    run = rsilib.Run(a.run_dir)
    path, data = load(run)
    base = path.parent

    if a.cmd == "list":
        rsilib.emit({"path": str(path), "exists": path.exists(), "count": len(data["evals"]),
                     "anchors": [{"id": e["id"], "prompt_head": " ".join(e["prompt"].split())[:120],
                                  "why": (e.get("rsi") or {}).get("why")} for e in data["evals"]]})

    if a.cmd == "validate":
        problems = []
        for e in data["evals"]:
            for f in e.get("files", []):
                if not (base / f).exists():
                    problems.append(f"{e['id']}: missing file {f}")
            r = e.get("rsi") or {}
            helpers = set()
            for f in r.get("case_files", []):
                rel = helper_rel(e, f)
                if rel is None:
                    problems.append(f"{e['id']}: case file {f} cannot be placed beside case.json")
                elif not (base / f).exists():
                    problems.append(f"{e['id']}: missing case file {f}")
                else:
                    helpers.add(rel.as_posix())
            for ref in sorted(case_refs(r.get("deterministic_checks")) - helpers):
                problems.append(f"{e['id']}: a check uses {{case}}/{ref}, which is not in rsi.case_files")
            if not (r.get("ground_truth") or r.get("deterministic_checks")):
                problems.append(f"{e['id']}: no ground truth and no deterministic checks")
        if len(data["evals"]) > MAX_ANCHORS:
            problems.append(f"{len(data['evals'])} anchors > {MAX_ANCHORS}")
        rsilib.emit({"path": str(path), "problems": problems}, 1 if problems else 0)

    if a.iteration is None:
        raise SystemExit("--iteration is required")
    it = run.iteration_dir(a.iteration)

    if a.cmd == "export":
        out = []
        for e in data["evals"]:
            c = to_case(e, base)
            d = it / "cases" / c["id"]
            if d.exists():
                shutil.rmtree(d)
            (d / "files").mkdir(parents=True, exist_ok=True)
            for f in e.get("files", []):
                if (base / f).exists():
                    shutil.copy2(base / f, d / "files" / Path(f).name)
            for f in (e.get("rsi") or {}).get("case_files", []):
                rel = helper_rel(e, f)
                if rel is not None and (base / f).exists():
                    (d / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(base / f, d / rel)
            rsilib.write_json(d / "case.json", c)
            out.append(c["id"])
        rsilib.emit({"exported": out, "from": str(path)})

    if a.cmd == "add":
        cdir = it / "cases" / a.case
        case = rsilib.read_json(cdir / "case.json")
        if not case:
            raise SystemExit(f"no case {a.case} in iteration {a.iteration}")
        if not (case.get("ground_truth") or case.get("deterministic_checks")):
            raise SystemExit("an anchor needs a ground truth or a deterministic check")
        if len(data["evals"]) >= MAX_ANCHORS and not a.force:
            raise SystemExit(f"already {MAX_ANCHORS} anchors; retire one first (or --force)")
        files = [p for p in (cdir / "files").rglob("*") if p.is_file()] if (cdir / "files").exists() else []
        size = sum(p.stat().st_size for p in files)
        if size > MAX_FILE_BYTES and not a.force:
            raise SystemExit(f"input files are {size} bytes > {MAX_FILE_BYTES}; trim them (or --force)")
        helpers = helper_files(cdir)
        hsize = sum(p.stat().st_size for p in helpers)
        if hsize > MAX_CASE_BYTES and not a.force:
            raise SystemExit(f"helper files beside case.json are {hsize} bytes > {MAX_CASE_BYTES}; trim them (or --force)")
        missing = sorted(case_refs(case.get("deterministic_checks")) - {p.relative_to(cdir).as_posix() for p in helpers})
        if missing and not a.force:
            raise SystemExit(f"checks use {{case}}/ paths that are not beside case.json: {missing} (or --force)")
        ids = {str(e["id"]) for e in data["evals"]}
        aid = a.case.split("-", 1)[1] if a.case.startswith("i") and "-" in a.case else a.case
        while aid in ids:
            aid += "-2"
        rel_files = []
        for p in files:
            rel = Path("files") / aid / p.relative_to(cdir / "files")
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, base / rel)
            rel_files.append(rel.as_posix())
        rel_helpers = []
        for p in helpers:
            rel = Path("case") / aid / p.relative_to(cdir)
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, base / rel)
            rel_helpers.append(rel.as_posix())
        data["evals"].append({
            "id": aid, "prompt": case["prompt"], "expected_output": case.get("expected_output") or case.get("title", ""),
            "files": rel_files, "expectations": [c["text"] for c in case.get("criteria", [])],
            "rsi": {"title": case.get("title"), "invoke": case.get("invoke", "explicit"),
                    "interaction": case.get("interaction", "none"), "criteria": case.get("criteria", []),
                    "deterministic_checks": case.get("deterministic_checks", []), "ground_truth": case.get("ground_truth"),
                    "case_files": rel_helpers, "coverage_tags": case.get("coverage_tags", []), "why": a.why,
                    "origin": {"run": run.config["run_id"], "iteration": a.iteration, "case": a.case},
                    "added_at": rsilib.now_iso()}})
        rsilib.write_json(path, data)
        rsilib.emit({"added": aid, "path": str(path), "count": len(data["evals"]), "files": rel_files,
                     "case_files": rel_helpers})


if __name__ == "__main__":
    main()
