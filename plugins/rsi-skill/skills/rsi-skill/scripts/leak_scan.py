#!/usr/bin/env python3
"""Scan a challenger's diff for eval-case content (overfitting / answer leakage).

    python -I leak_scan.py --run-dir R --candidate v3 [--base v2] [--through-iteration N]

Looks only at lines the challenger added versus its parent. Hits:
  block  case id; any 40-char run of a case prompt; a ground-truth value; rsi machinery names
  warn   a distinctive input file name or number from a case that the parent never mentioned;
         eval words (challenger, grader, held-out ...) new to the project
Writes candidates/<id>.leak.json. Exit 1 when any blocking hit is found.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rsilib  # noqa: E402

BLOCK_TERMS = ["rsi-skill", "rsi-workspace", "rsi_skill", "case.json", "eval case", "eval-case"]
WARN_TERMS = ["challenger", "champion", "grader", "held-out", "holdout", "test case", "benchmark case"]
WINDOW = 40


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def windows(text: str, size: int = WINDOW, step: int = 1):
    t = norm(text)
    for i in range(0, max(len(t) - size + 1, 1), step):
        w = t[i:i + size]
        if len(w) == size:
            yield w


NUM_RE = re.compile(r"(?<![\w.])(\d[\d,]*\.\d+|\d{5,}|\d{1,3}(?:,\d{3})+)(?![\w.])")


def distinctive_numbers(text: str) -> set:
    out = set()
    for m in NUM_RE.finditer(text or ""):
        n = m.group(1)
        digits = re.sub(r"\D", "", n)
        if len(digits.strip("0")) >= 3:
            out.add(n)
    return out


def scan(run: rsilib.Run, cand: str, base: str | None = None, through: int | None = None) -> dict:
    meta = run.candidate_meta(cand)
    base = base or meta.get("parent") or "v0"
    d = rsilib.diff_trees(run.candidate(base), run.candidate(cand))
    added = {rel: "\n".join(lines) for rel, lines in d["added_lines"].items() if lines}
    added_norm = {rel: norm(t) for rel, t in added.items()}
    parent_text = norm(" ".join(rsilib.read_text(run.candidate(base) / rel)
                                for rel in rsilib.tree_files(run.candidate(base))
                                if rsilib.is_text_file(run.candidate(base) / rel)))
    hits = []

    def hit(level, kind, value, rel):
        hits.append({"level": level, "kind": kind, "value": value[:120], "file": rel})

    cases = []
    for it_dir in sorted((run.dir / "iterations").glob("*")):
        if through is not None and int(it_dir.name) > through:
            continue
        for cj in it_dir.glob("cases/*/case.json"):
            c = rsilib.read_json(cj) or {}
            c["_files"] = [p.name for p in (cj.parent / "files").rglob("*") if p.is_file()] \
                if (cj.parent / "files").exists() else []
            cases.append(c)
    for c in cases:
        cid = str(c.get("id", ""))
        prompt = c.get("prompt", "")
        wins = set(windows(prompt))
        gt = c.get("ground_truth") or {}
        gt_vals = {str(v) for k, v in gt.items() if k in ("value", "answer") and v not in (None, "")}
        nums = distinctive_numbers(prompt) | distinctive_numbers(" ".join(str(v) for v in gt.values()))
        for rel, t in added_norm.items():
            if cid and len(cid) >= 6 and cid.lower() in t:
                hit("block", "case_id", cid, rel)
            for w in wins:
                if w in t:
                    hit("block", "prompt_text", w, rel)
                    break
            for v in gt_vals:
                if len(v) >= 3 and norm(v) in t and norm(v) not in parent_text:
                    hit("block", "ground_truth", v, rel)
            for fn in c["_files"]:
                stem = Path(fn).stem.lower()
                generic = len(stem) < 6 or stem in {"data", "input", "sample", "example", "test", "report", "readme"}
                if not generic and stem in t and stem not in parent_text:
                    hit("warn", "input_file_name", fn, rel)
            for n in nums:
                if n.lower() in t and n.lower() not in parent_text:
                    hit("warn", "case_number", n, rel)
    for rel, t in added_norm.items():
        for term in BLOCK_TERMS:
            if term in t and term not in parent_text:
                hit("block", "rsi_term", term, rel)
        for term in WARN_TERMS:
            if term in t and term not in parent_text:
                hit("warn", "eval_term", term, rel)
    # one hit per (kind, value, file)
    seen, uniq = set(), []
    for h in hits:
        k = (h["kind"], h["value"], h["file"])
        if k not in seen:
            seen.add(k)
            uniq.append(h)
    out = {"candidate": cand, "base": base, "cases_scanned": len(cases), "files_changed": len(added),
           "blocking": [h for h in uniq if h["level"] == "block"], "warnings": [h for h in uniq if h["level"] == "warn"],
           "passed": not any(h["level"] == "block" for h in uniq)}
    rsilib.write_json(run.dir / "candidates" / f"{cand}.leak.json", out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--base")
    ap.add_argument("--through-iteration", type=int)
    a = ap.parse_args()
    run = rsilib.Run(a.run_dir)
    out = scan(run, a.candidate, a.base, a.through_iteration)
    rsilib.emit(out, 0 if out["passed"] else 1)


if __name__ == "__main__":
    main()
