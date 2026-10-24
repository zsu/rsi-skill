# File schemas

Every file is JSON (UTF-8). Paths are relative to the run folder `<run>` unless they are absolute.

## Contents

- [goal.json](#goaljson)
- [profile.json](#profilejson)
- [case.json](#casejson)
- [grading.json](#gradingjson)
- [Comparison verdict](#comparison-verdict)
- [Candidate meta](#candidate-meta)
- [Overfit review](#overfit-review)
- [Written by scripts](#written-by-scripts)

## goal.json

The orchestrator writes this in step 1. See [goals.md](goals.md).

```json
{
  "text": "no major problems",
  "blocking_severity": "major",
  "confirm_streak": 2,
  "min_check_pass_rate": null,
  "min_criteria_pass_rate": null,
  "min_quality": null,
  "focus": ["optional areas the user named"],
  "out_of_scope": ["things the user said not to change or not to judge"]
}
```

## profile.json

Written by the profiler. See [agents/profiler.md](../agents/profiler.md).

```json
{
  "summary": "what the skill does, 2-3 sentences",
  "inputs": ["kinds of input it accepts"],
  "output_contract": ["what a correct run must produce: files, sections, formats"],
  "validators": [{"cmd": "{python} {skill}/scripts/validate_report.py {sandbox}/*.html", "checks": "what it proves", "severity": "major"}],
  "unattended": "how to ask for a run with no user (the exact phrasing the skill documents)",
  "network": "none | some | heavy",
  "keys": [{"name": "FRED_API_KEY", "present": true}],
  "est_cost_usd_per_case": 1.5,
  "est_minutes_per_case": 12,
  "cases_per_iteration": 4,
  "coverage_map": {"input_kind": ["bill number", "zip of files"], "difficulty": ["simple", "multi-title"]},
  "ground_truth_sources": ["where answers can be checked: published CBO estimates, BLS tables ..."],
  "risks": ["known fragile areas worth probing"],
  "gates_change": "optional: gates to add/remove, with why"
}
```

## case.json

Written by the case generator to `iterations/NNN/cases/<id>/case.json`. Input files go in `files/` next to it. `cases.py register` checks the schema, prefixes the id with `iNNN-` and refuses duplicates.

```json
{
  "id": "short-kebab-id",
  "title": "one line",
  "prompt": "the user's message, as a real user would write it",
  "invoke": "explicit",
  "interaction": "none",
  "files": ["input.csv"],
  "deterministic_checks": [
    {"id": "report-exists", "type": "file_exists", "path": "**/*-report.html", "severity": "critical"},
    {"id": "validator", "type": "command", "cmd": "{python} {skill}/scripts/validate_report.py {sandbox}/out/report.html", "expect_exit": 0, "severity": "major"},
    {"id": "median", "type": "number_near", "path": "summary.json", "pattern": "\"median\"\\s*:\\s*([-\\d.]+)", "target": 4.5, "abs_tol": 0.001, "severity": "major"}
  ],
  "criteria": [
    {"id": "c1", "text": "States the assumptions it made because no user was available", "severity": "minor"}
  ],
  "ground_truth": {"value": 4.5, "tolerance": 0.001, "source": "computed by hand from files/input.csv", "source_url": null},
  "coverage_tags": ["even-count", "csv"],
  "probes_failure_category": "numeric-correctness",
  "expected_output": "short description of a good answer (for humans and anchors)",
  "timeout_seconds": 900,
  "max_turns": null,
  "max_cost_usd": null
}
```

- `invoke`: `explicit` prefixes the prompt with the skill's slash command. `natural` relies on the description to trigger the skill, and is used to test triggering. Use `natural` for at most 1 case in 4, and add a `skill_invoked` check.
- `interaction`: `none` means no user is available, so the run must finish without asking. `questions_ok` means asking a clarifying question and stopping is a correct outcome (the grader judges whether the question was needed).
- Check types and placeholders are listed in `scripts/check_case.py --help`.

## grading.json

Written by the grader next to `metrics.json`. The field names under `expectations` and `summary` follow skill-creator, so its viewer can read them.

```json
{
  "case_id": "i001-...", "role": "champion", "candidate": "v0",
  "expectations": [{"text": "criterion text", "passed": true, "evidence": "quote or file:line"}],
  "summary": {"passed": 3, "failed": 1, "total": 4, "pass_rate": 0.75},
  "findings": [{
    "key": "median-even-count",
    "title": "Median is wrong for an even number of values",
    "severity": "major",
    "class": "skill_general",
    "category": "numeric-correctness",
    "evidence": "summary.json median=4, expected 4.5 (values 1..8)",
    "suspected_files": ["scripts/summarize.py"],
    "check_id": "median"
  }],
  "overall_quality": 3,
  "notes": "short"
}
```

- `key`: a stable kebab-case slug for the defect, not for the case. The same defect in another case must get the same key, because keys connect findings across iterations (attempts, stuck, fixed).
- `class`:
  - `skill_general`: the skill would fail other inputs too.
  - `case_specific`: the case itself is ambiguous or wrong. Never counts against the skill.
  - `external`: network, outage, missing key, data the source lacks, or model limits.
- `check_id`: set it when the finding explains a failed deterministic check. The check's own finding is then merged into this one, and the grader's class is kept.

## Comparison verdict

Written by the comparator as `iterations/NNN/comparison/<case>.json`. If it is a swap re-judgment, the file is `<case>.swap.json`.

```json
{"winner": "A", "confidence": "high", "scores": {"A": 4, "B": 2}, "reasoning": "short, evidence-based"}
```

`winner` is `A`, `B` or `tie`. Only the order file says which side is which.

## Candidate meta

`candidates/vN.meta.json`. Scripts create and update it. The improver adds:

```json
{
  "targets": ["finding keys this challenger is meant to fix"],
  "approach": "one paragraph: the general rule behind the change",
  "unfixable": ["keys the improver judged cannot be fixed inside the skill, with why in approach"],
  "files_touched": ["SKILL.md", "scripts/summarize.py"]
}
```

## Overfit review

`candidates/vN.overfit.json`, written by the overfit reviewer:

```json
{"verdict": "pass", "concerns": [{"file": "SKILL.md", "line_hint": "…", "why": "names a value from one input"}], "summary": "short"}
```

## Written by scripts

These are for reading only:

| File | Written by |
|---|---|
| `config.json`, `state.json` | `init_workspace.py` (`state.json` is then updated by `candidate.py` and `aggregate.py decide`) |
| `history.json` | `aggregate.py decide` |
| `budget.jsonl` | `run_case.py`, `budget.py add` |
| `metrics.json`, `transcript.jsonl`, `digest.md`, `final.md` | `run_case.py` |
| `checks.json` | `check_case.py` |
| `scoreboard.json`, `benchmark.json` | `aggregate.py score` |
| `decision.json` | `aggregate.py decide` |
| `vN.gates.json` | `static_gates.py` |
| `vN.leak.json` | `leak_scan.py` |
| `vN.diff.patch` | `candidate.py diff --write` |
