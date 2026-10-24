#!/usr/bin/env python3
"""Write the run report: <run>/REPORT.md and <run>/report.html.

    python -I report.py --run-dir R

Built only from files in the run folder (history.json, scoreboards, gradings, budget, diff),
so it can be regenerated at any time, also for a run that stopped early.
"""
from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aggregate  # noqa: E402
import budget  # noqa: E402
import rsilib  # noqa: E402

ASSET = Path(__file__).resolve().parent.parent / "assets" / "report_template.html"


def findings_for(run, iteration: int, role: str) -> dict:
    agg = {}
    for rd in aggregate.role_dirs(run, iteration, role):
        for f in aggregate.run_findings(run, rd):
            k = f["key"]
            e = agg.setdefault(k, {"key": k, "title": f.get("title", k), "severity": f["severity"],
                                   "class": f["class"], "blocking": f["blocking"], "cases": [], "evidence": f.get("evidence", "")})
            e["cases"].append(rd.parent.name)
            if rsilib.severity_rank(f["severity"]) > rsilib.severity_rank(e["severity"]):
                e["severity"] = f["severity"]
    return agg


def build(run) -> dict:
    cfg, state, hist = run.config, run.state, run.history
    iters = hist.get("iterations", [])
    champ = state.get("champion", "v0")
    first = findings_for(run, iters[0]["iteration"], "champion") if iters else {}
    last_it = iters[-1] if iters else None
    last_role = "challenger" if last_it and last_it.get("promoted") else "champion"
    last = findings_for(run, last_it["iteration"], last_role) if last_it else {}
    first_block = {k for k, v in first.items() if v["blocking"]}
    last_block = {k for k, v in last.items() if v["blocking"]}
    diff = rsilib.diff_trees(run.candidate("v0"), run.candidate(champ)) if champ != "v0" else None
    return {"cfg": cfg, "state": state, "hist": hist, "iters": iters, "champion": champ,
            "fixed": sorted(first_block - last_block), "first": first, "remaining": [last[k] for k in sorted(last)],
            "diff": diff, "budget": budget.status(run.dir), "goal": run.goal}


def md(r) -> str:
    cfg, st, b = r["cfg"], r["state"], r["budget"]
    t = cfg["target"]
    L = [f"# rsi report: {t['name']}", "",
         f"- Target: `{t['skill_dir']}` ({t['kind']})",
         f"- Run: `{cfg['run_id']}`, status **{st.get('status')}**, stop reason **{st.get('stop_reason') or 'n/a'}**",
         f"- Goal: {cfg.get('goal_text') or r['goal'].get('text') or '(default)'}"
         f" → blocking severity `{r['goal'].get('blocking_severity', 'major')}`, confirm streak {r['goal'].get('confirm_streak', 2)}",
         f"- Champion: `{r['champion']}`" + (" (unchanged original)" if r["champion"] == "v0" else ""),
         f"- Spend: ${b['cost_usd']} (cases ${b['cost_usd_cases']}, agents ≈${b['cost_usd_estimated_agents']}), "
         f"{b['tokens']:,} tokens, {b['elapsed_hours']} h, {b['case_runs']} case runs",
         "- Applied to target: " + (f"`{st['applied']['candidate']}` as uncommitted edits, {st['applied']['at']}"
                                    if st.get("applied") else "not yet"), "",
         "## Trajectory", "",
         "| it | champion | challenger | promoted | blocking (cases) | conclusive | pairwise W-L-T | replay fixed | clean | next |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for h in r["iters"]:
        cf = h.get("champion_fresh") or {}
        pw = h.get("pairwise") or {}
        pws = f"{pw.get('challenger_wins', 0)}-{pw.get('champion_wins', 0)}-{pw.get('ties', 0)}" if h.get("challenger") else ""
        L.append(f"| {h['iteration']} | {h['champion_before']}→{h['champion_after']} | {h.get('challenger') or ''} | "
                 f"{'yes' if h.get('promoted') else ''} | {cf.get('blocking_findings', '')} ({cf.get('blocking_cases', '')}) | "
                 f"{cf.get('conclusive', '')}/{cf.get('cases', '')} | {pws} | {h.get('replay_fix_rate') if h.get('replay_fix_rate') is not None else ''} | "
                 f"{'yes' if h.get('clean') else ''} | {h.get('next_action', '')} |")
    L += ["", "## Fixed (blocking at the start, gone at the end)", ""]
    L += [f"- `{k}` — {r['first'][k]['title']} ({r['first'][k]['severity']})" for k in r["fixed"]] or ["- none"]
    L += ["", "## Remaining findings (last evaluation of the champion)", ""]
    rem = sorted(r["remaining"], key=lambda f: -rsilib.severity_rank(f["severity"]))
    L += [f"- **{f['severity']}** `{f['key']}` [{f['class']}{', blocking' if f['blocking'] else ''}] {f['title']} "
          f"— cases: {', '.join(f['cases'])}. Evidence: {str(f['evidence'])[:240]}" for f in rem] or ["- none"]
    att = r["hist"].get("attempts", {})
    if att:
        L += ["", "## Fix attempts per finding", ""]
        L += [f"- `{k}`: {v if v < 99 else 'declared unfixable'}{' (stuck)' if v >= 2 else ''}" for k, v in sorted(att.items())]
    dnr = r["hist"].get("do_not_repeat", [])
    if dnr:
        L += ["", "## Challengers", ""]
        L += [f"- `{d['candidate']}` ({d.get('mode')}) → {d['outcome']}: {d.get('approach', '')[:200]} {('— ' + d['why']) if d.get('why') else ''}"
              for d in dnr]
    if r["diff"]:
        d = r["diff"]
        L += ["", "## Champion changes vs the original", "",
              f"{len(d['changed'])} changed, {len(d['added'])} added, {len(d['deleted'])} deleted, "
              f"{d['added_line_count']} lines added. Full patch: `champion.patch` (after apply_to_target.py).", ""]
        L += [f"- M `{x}`" for x in d["changed"]] + [f"- A `{x}`" for x in d["added"]] + [f"- D `{x}`" for x in d["deleted"]]
    return "\n".join(L) + "\n"


def to_html(markdown: str, title: str) -> str:
    """Tiny Markdown → HTML for the report's own subset (headings, lists, tables, code, bold)."""
    import re
    out, in_list, in_table = [], False, False

    def inline(s):
        s = html.escape(s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)

    for line in markdown.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= {"-"} for c in cells):
                continue
            if not in_table:
                out.append("<div class='tw'><table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in cells) + "</tr></thead><tbody>")
                in_table = True
            else:
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</tbody></table></div>")
            in_table = False
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(line[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if line.startswith("# "):
            out.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.strip():
            out.append(f"<p>{inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    if in_table:
        out.append("</tbody></table></div>")
    tpl = rsilib.read_text(ASSET) if ASSET.exists() else "<html><body>{{BODY}}</body></html>"
    return tpl.replace("{{TITLE}}", html.escape(title)).replace("{{BODY}}", "\n".join(out))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    a = ap.parse_args()
    run = rsilib.Run(a.run_dir)
    r = build(run)
    text = md(r)
    (run.dir / "REPORT.md").write_text(text, encoding="utf-8")
    (run.dir / "report.html").write_text(to_html(text, f"rsi: {run.config['target']['name']}"), encoding="utf-8")
    rsilib.emit({"report_md": str(run.dir / "REPORT.md"), "report_html": str(run.dir / "report.html"),
                 "fixed": r["fixed"], "remaining_blocking": [f["key"] for f in r["remaining"] if f["blocking"]],
                 "stop_reason": r["state"].get("stop_reason"), "champion": r["champion"]})


if __name__ == "__main__":
    main()
