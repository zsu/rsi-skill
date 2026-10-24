#!/usr/bin/env python3
"""Manage candidate versions of the target project inside a run.

    python -I candidate.py new     --run-dir R [--from v2] [--iteration N] [--mode fix|divergent]
    python -I candidate.py diff    --run-dir R --candidate v3 [--base v2] [--write]
    python -I candidate.py promote --run-dir R --candidate v3
    python -I candidate.py reject  --run-dir R --candidate v3 [--reason "..."]
    python -I candidate.py list    --run-dir R

Candidates are full project copies in <run>/candidates/vN/ (v0 = the untouched snapshot).
`new` copies the champion (or --from) to the next vN, records its parent and sets it as the
pending challenger. The improver edits that copy; `diff --write` stores diff.patch for review.
`promote` makes it the champion (state.json) and archives nothing (every vN is kept as is).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rsilib  # noqa: E402


def next_id(run: rsilib.Run) -> str:
    nums = [int(p.name[1:]) for p in (run.dir / "candidates").glob("v*") if p.is_dir() and p.name[1:].isdigit()]
    return f"v{max(nums) + 1 if nums else 0}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--from", dest="src")
    p.add_argument("--iteration", type=int)
    p.add_argument("--mode", default="fix", choices=["fix", "divergent"])
    p = sub.add_parser("diff")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--candidate", required=True)
    p.add_argument("--base")
    p.add_argument("--write", action="store_true")
    for name in ("promote", "reject"):
        p = sub.add_parser(name)
        p.add_argument("--run-dir", required=True)
        p.add_argument("--candidate", required=True)
        p.add_argument("--reason", default="")
    p = sub.add_parser("list")
    p.add_argument("--run-dir", required=True)
    a = ap.parse_args()
    run = rsilib.Run(a.run_dir)
    state = run.state

    if a.cmd == "new":
        src = a.src or state["champion"]
        cid = next_id(run)
        rsilib.copy_tree(run.candidate(src), run.candidate(cid))
        meta = {"id": cid, "parent": src, "created_at": rsilib.now_iso(),
                "iteration": a.iteration if a.iteration is not None else state.get("iteration"),
                "mode": a.mode, "status": "draft", "targets": []}
        run.save_candidate_meta(cid, meta)
        state["pending_challenger"] = cid
        run.save_state(state)
        rsilib.emit({"candidate": cid, "path": str(run.candidate(cid)), "parent": src,
                     "skill_path": str(run.skill_dir_in(run.candidate(cid))),
                     "meta": str(run.dir / "candidates" / f"{cid}.meta.json")})

    if a.cmd == "diff":
        meta = run.candidate_meta(a.candidate)
        base = a.base or meta.get("parent") or "v0"
        d = rsilib.diff_trees(run.candidate(base), run.candidate(a.candidate))
        if a.write:
            (run.dir / "candidates" / f"{a.candidate}.diff.patch").write_text(d["patch"], encoding="utf-8")
        rsilib.emit({"candidate": a.candidate, "base": base, "changed": d["changed"], "added": d["added"],
                     "deleted": d["deleted"], "added_lines": d["added_line_count"],
                     "patch_file": str(run.dir / "candidates" / f"{a.candidate}.diff.patch") if a.write else None})

    if a.cmd in ("promote", "reject"):
        meta = run.candidate_meta(a.candidate)
        if not meta:
            raise SystemExit(f"unknown candidate {a.candidate}")
        if a.cmd == "promote":
            old = state["champion"]
            old_meta = run.candidate_meta(old)
            old_meta["status"] = "retired"
            run.save_candidate_meta(old, old_meta)
            meta["status"] = "champion"
            meta["promoted_at"] = rsilib.now_iso()
            state["champion"] = a.candidate
        else:
            meta["status"] = "rejected"
            meta["rejected_reason"] = a.reason
            meta["rejected_iteration"] = state.get("iteration")
        if state.get("pending_challenger") == a.candidate:
            state["pending_challenger"] = None
        run.save_candidate_meta(a.candidate, meta)
        run.save_state(state)
        rsilib.emit({"champion": state["champion"], "candidate": a.candidate, "status": meta["status"]})

    if a.cmd == "list":
        rows = []
        for mp in sorted((run.dir / "candidates").glob("v*.meta.json"), key=lambda x: int(x.name[1:].split(".")[0])):
            m = rsilib.read_json(mp)
            rows.append({k: m.get(k) for k in ("id", "parent", "status", "mode", "iteration", "targets")})
        rsilib.emit({"champion": state["champion"], "pending_challenger": state.get("pending_challenger"),
                     "candidates": rows})


if __name__ == "__main__":
    main()
