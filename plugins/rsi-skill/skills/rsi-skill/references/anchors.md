# Anchors: the optional persistent benchmark

## Default: no persistent set

Fresh cases do the main work. Each iteration writes new cases, so a fix is always judged on inputs nobody tuned it to. A fixed benchmark that is reused on every run becomes a target the skill is tuned to over time, which is the overfitting this skill exists to avoid.

## When to keep an anchor

Keep an anchor only when it adds something fresh cases cannot. Offer a case as an anchor (at most 3 per run) only if it meets at least one of:

1. **It has stable external ground truth.** The correct answer comes from an authority and will not drift.
   - Example: a bill with a published CBO cost estimate (a 10-year total and its sign).
   - Example: a closed historical statistic from an official table.
   - Data that agencies revise (recent months, preliminary releases) does not qualify.
2. **It guards a regression this run fixed.** It must be cheap to run (well under the median case cost) and have a deterministic check that fails on the old champion and passes on the new one.

Every anchor needs a ground truth or at least one deterministic check. Anchors whose only checks are judged criteria are not allowed. The cap is 8 anchors per skill: retire one before adding another.

## Rules

- **Who decides.** The user approves each anchor in step 7. With `--unattended`, no anchors are added.
- **Where they live.** `resolve_target.py` sets `anchors.path`:
  - Default: `<skill>/evals/evals.json` plus `evals/files/<id>/` (sandbox inputs) and `evals/case/<id>/` (helper files), which matches skill-creator's `evals/`. skill-creator's packager leaves out a root `evals/`.
  - When the target's own packager ships the skill folder without excluding `evals/`: `<repo>/evals/<skill>/`, so anchors never reach users.
  - Either way the files sit in the target's git and are found on the next run.
- **Format.** Anthropic `evals.json` (`skill_name`, `evals[{id, prompt, expected_output, files, expectations}]`), with an `rsi` block per eval that holds the checks, the ground truth and the origin. skill-creator can also read the file.
  - `files` are the case's sandbox inputs (`files/<id>/...`).
  - `rsi.case_files` are the helper files the case kept beside `case.json`, such as a `verify.py` that a check calls as `{case}/verify.py` (`case/<id>/...`). They never go to the sandbox. `export` puts them back beside the exported `case.json`. Older entries without the field still export.
  - `add` refuses a case whose checks use a `{case}/...` path that is not beside `case.json`, or whose helper files exceed 512 KB (`--force` overrides). `validate` reports the same problems for anchors already in the file.
- **How they are used.**
  - `anchors.py export` copies every anchor into each iteration's cases.
  - Champion and challenger both run them.
  - The gate uses them only as "no regression". They are never a reason to promote.
  - The case generator and the improver may see anchor files. That is acceptable because anchors only guard against regressions.
- **Sandboxes.** `run_case.py` never copies the skill's `evals/` folder into a sandbox, so a run under test cannot read an anchor's expected answer.

## Per-skill expectations

- **Skills with stable ground truth** (cost estimates of scored bills, closed historical figures): 1–3 anchors are usually worth keeping.
- **Skills with a large offline test suite and live data that drifts:** usually none. The suite already guards regressions, and pinned answers go stale.
