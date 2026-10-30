# rsi-skill

Recursive self-improvement for Agent Skills. Point it at a skill and a goal:

```
/rsi-skill:rsi-skill run against <path-to-target-skill> until no major problems, max 4 iterations, $60
```

The loop:

1. Profiles the skill and turns your goal into a measurable `goal.json`.
2. Writes **new** eval cases in every iteration.
3. Runs the skill headless (`claude -p`) in a sandbox for each case.
4. Grades every run with deterministic checks first, then an evidence-required LLM grader.
5. Lets an improver edit anything in the skill (SKILL.md, scripts, references, assets, tests).
6. Promotes the new version (the challenger) only when it beats the current best (the champion) on the
   **next** iteration's new cases. Nobody, including the improver, has seen those cases.
7. Stops when the goal is met, a limit is hit (iterations, time, tokens, cost), the remaining problems
   are stuck or external, or progress plateaus.

All iterations live outside the target, in `<target repo parent>/rsi-workspace/<skill>/`. The best
version is written back as **uncommitted** edits for you to review with `git diff`.

## Install

rsi-skill is listed in the `zsu` marketplace of the [zsu/agent-plugins](https://github.com/zsu/agent-plugins)
catalog. It installs from this repo on GitHub.

**Claude Code:**

```
/plugin marketplace add zsu/agent-plugins
/plugin install rsi-skill@zsu
```

**Codex:**

```
codex plugin marketplace add zsu/agent-plugins
```

Then install `rsi-skill` from the Plugins directory. The test runs drive the `claude` CLI headless, so Claude
Code must be installed and logged in even when Codex is the orchestrator. A Codex-native runner is not
built yet.

**Updates.** These reach users when `version` is bumped in both `plugins/rsi-skill/.claude-plugin/plugin.json`
and `plugins/rsi-skill/.codex-plugin/plugin.json` and pushed. This repo has no marketplace of its own,
so the plugin can't be registered twice.

To load the plugin from a clone for one session without installing it:

```bash
claude --plugin-dir plugins/rsi-skill
```

(Run that from the root of the clone, or pass the full path to `plugins/rsi-skill`.)

## Usage

```
/rsi-skill:rsi-skill [run|resume|status|report|apply|anchors] <target path> [until <goal>] [limits] [flags]
```

- **Target:** a SKILL.md, a skill folder, or a project folder that contains skills at any depth
  (`.claude/skills/x`, `plugins/p/skills/x`, `skills/x`). If the folder contains several skills, you are asked which one.
- **Goal:** for example "no major problems" (the default), "until clean", "pass rate ≥ 90%", or "focus on unattended mode".
- **Limits:** "max 5 iterations" (counted after the baseline iteration), "4h", "$20", "500k tokens", "3 cases".
- **Flags:**

  | Flag | Effect |
  |---|---|
  | `--unattended` | Ask no questions |
  | `--report-only` | Evaluate the baseline only and change nothing |
  | `--unpaired` | Cheaper but weaker comparison |
  | `--baseline` | Also run once without the skill |
  | `--no-apply` | Don't write the result back to the target |
  | `--model M` | Model for the test runs |
  | `--judge-model M` | Model for graders and comparators |
  | `--workspace DIR` | Where iterations live |
  | `--isolate-user-settings` | Load only project and local settings in test runs |

## Fixed benchmark or fresh cases?

The default is fresh cases only, and fixed sets are not stored. A benchmark that is reused on every run
becomes something the skill gets tuned to. New cases each iteration keep finding new problems and act as
a held-out test.

At the end of a run you may keep up to 3 **anchors**. These are cases with stable external ground truth
(for example a bill CBO has scored) or a cheap deterministic regression check. They go in the target's
git, at `<skill>/evals/evals.json` (Anthropic evals.json format, also read by skill-creator). If the
target's packager would ship that folder, they go in `<repo>/evals/<skill>/` instead.

Later runs execute the anchors as a regression guard only. The full policy is in
[references/anchors.md](plugins/rsi-skill/skills/rsi-skill/references/anchors.md).

## Layout

```
plugins/rsi-skill/skills/rsi-skill/
  SKILL.md       the orchestrator workflow
  agents/        briefs: profiler, case_generator, grader, comparator, improver, overfit_reviewer
  references/    schemas, goals, stopping rules, anchors, target types
  scripts/       Python 3 standard library only, Windows-safe; every script has --help and prints JSON
  assets/        report template
tests/           offline tests (fake claude CLI, toy target with planted defects)
```

Run the tests:

```bash
python -I tests/run_all.py
```

## Compatibility

`evals.json`, `grading.json` and `benchmark.json` use the same field names as Anthropic's skill-creator,
so its tools can read them.
