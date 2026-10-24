#!/usr/bin/env python3
"""Bring the champion's changes into the target project as uncommitted edits.

    python -I apply_to_target.py --run-dir R [--candidate vN] [--dry-run] [--patch-only] [--run-gates]

Always writes <run>/champion.patch (v0 -> candidate, git-style). Then, unless --dry-run or
--patch-only, it checks that every file it will touch in the target still matches the snapshot
taken at the start (baseline_manifest.json). If the target changed meanwhile, nothing is
written (exit 2, conflicts listed): apply the patch by hand. Otherwise it writes changed and added
files and deletes removed ones, never commits, and with --run-gates reruns the static gates in
the target. Exit 0 applied (or dry run), 2 conflicts, 1 gates failed after applying.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rsilib  # noqa: E402
import static_gates  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--candidate")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--patch-only", action="store_true")
    ap.add_argument("--run-gates", action="store_true")
    a = ap.parse_args()
    run = rsilib.Run(a.run_dir)
    cand = a.candidate or run.state["champion"]
    src = run.candidate(cand)
    target = Path(run.config["target"]["repo_root"])
    d = rsilib.diff_trees(run.candidate("v0"), src)
    patch_path = run.dir / "champion.patch"
    patch_path.write_text(d["patch"], encoding="utf-8")
    out = {"candidate": cand, "target": str(target), "patch": str(patch_path),
           "changed": d["changed"], "added": d["added"], "deleted": d["deleted"]}
    if cand == "v0" or not (d["changed"] or d["added"] or d["deleted"]):
        rsilib.emit({**out, "applied": False, "note": "champion equals the original; nothing to apply"})
    if a.dry_run or a.patch_only:
        rsilib.emit({**out, "applied": False, "note": "dry run" if a.dry_run else "patch only"})

    base = rsilib.read_json(run.dir / "baseline_manifest.json", {})
    conflicts = []
    for rel in d["changed"] + d["deleted"]:
        p = target / rel
        if not p.exists():
            conflicts.append({"file": rel, "why": "deleted in the target since the snapshot"})
        elif base.get(rel) != rsilib.sha256_file(p):
            conflicts.append({"file": rel, "why": "changed in the target since the snapshot"})
    for rel in d["added"]:
        p = target / rel
        if p.exists() and rsilib.sha256_file(p) != rsilib.sha256_file(src / rel):
            conflicts.append({"file": rel, "why": "a different file now exists at this path"})
    if conflicts:
        rsilib.emit({**out, "applied": False, "conflicts": conflicts,
                     "note": "target changed since the run started; apply champion.patch by hand"}, 2)

    for rel in d["changed"] + d["added"]:
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, target / rel)
    for rel in d["deleted"]:
        (target / rel).unlink(missing_ok=True)
    state = run.state
    state["applied"] = {"candidate": cand, "at": rsilib.now_iso()}
    run.save_state(state)
    out.update({"applied": True, "git": rsilib.git_info(target)})
    code = 0
    if a.run_gates:
        t = run.config["target"]
        results = static_gates.exec_gates(run.config.get("gates", []), target, Path(t["skill_dir"]), t.get("limits", {}))
        base_gates = rsilib.read_json(run.dir / "candidates" / "v0.gates.json", {}) or {}
        pre = {r["id"] for r in base_gates.get("results", []) if not r["passed"]}
        failed = [r["id"] for r in results if not r["passed"] and r["required"] and r["id"] not in pre]
        out["gates_in_target"] = [{"id": r["id"], "passed": r["passed"], "evidence": r["evidence"][-400:]} for r in results]
        out["gates_failed"] = failed
        code = 1 if failed else 0
    rsilib.emit(out, code)


if __name__ == "__main__":
    main()
