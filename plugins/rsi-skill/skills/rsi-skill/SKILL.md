---
name: rsi-skill
description: Recursive self-improvement loop for any Agent Skill. Give it a target skill folder (any depth: project, plugin or bare skill) and optionally a goal and limits; it profiles the skill, writes a measurable goal, then iterates - fresh eval cases every iteration, real headless runs of the skill in sandboxes, deterministic checks plus evidence-based grading, an improver that edits anything in the skill (SKILL.md, scripts, references, assets, tests), and a champion/challenger gate that promotes a change only when it wins on new cases nobody tuned it to. Stops when the goal is met, a limit (iterations, time, tokens, cost) is reached, the remaining problems are stuck or external, or progress plateaus. All iterations live outside the target; the best version is applied as uncommitted edits. Use whenever the user asks to self-improve, evaluate-and-fix, harden, iterate on, benchmark or run RSI on a skill, e.g. "rsi-skill run against ../my-skill until no major problems", even without naming this skill.
---

# rsi-skill: evaluate and improve a skill until the goal is met

This loop works on any Agent Skill. Each iteration writes **new** test cases, runs the skill headless on
them, grades the results with evidence, and lets an improver write a **challenger**. The challenger
replaces the **champion** only if it does better on the *next* iteration's new cases. Neither the
improver nor anyone else saw those cases, so they work as a held-out test. A fix that only patches the
cases it was shown does not pass this.

Notation:
- `<rsi>` is this skill's folder (`${CLAUDE_SKILL_DIR}`).
- `<run>` is the run folder that step 1 creates.
- `py` stands for `python -I`.
- Every script prints JSON and has `--help`.

All file formats are in [references/schemas.md](references/schemas.md).

## Arguments

`/rsi-skill [run|resume|status|report|apply|anchors] <target path> [until <goal>] [limits] [flags]`

The request is free text. Read it like this:

| Part | How to read it |
|---|---|
| Verb | `run` (the default), `resume [<run or workspace>]`, `status`, `report`, `apply`, `anchors` |
| Target | The first path-like token: a SKILL.md, a skill folder, or a project folder that has skills at any depth. If it is missing or does not exist, ask the user for it with your question tool. |
| Goal | The text after `until` or `goal:`, for example "no major problems". If none is given, use the default from [references/goals.md](references/goals.md). |
| Limits | "max 5 iterations", "4h", "$20", "500k tokens", "3 cases". The defaults are 5 iterations after the baseline, 6 hours, and a cost cap from the step-2 estimate. |
| Flags | `--workspace DIR`, `--model M` (model for test runs), `--judge-model M`, `--unattended` (no questions; also no anchors), `--report-only` (baseline evaluation only, edits nothing), `--unpaired`, `--baseline` (also run with no skill on iteration 0), `--no-apply`, `--isolate-user-settings`, `--name SKILL` |

Never write inside `<rsi>`. Write to the target project only in step 8 (`apply`) and for anchors the
user approved.

## How the work is split

The loop can run for hours. Keep your own context small:
- Scripts handle state. Subagents do the reading-heavy work, following the briefs in `agents/`.
- Long batches run in the background, and you are notified when they finish.
- Read summaries, not transcripts.
- After each subagent finishes, record its token use (the `total_tokens` / subagent token count in its result):
  `py <rsi>/scripts/budget.py add --run-dir <run> --kind subagent --tokens N --note "<role>"`.
- Once per iteration, record roughly 60k tokens for yourself with `--kind orchestrator`.
- Every step is re-entrant: `todo.py` and `state.json` tell you where you stopped.

**Subagents.** Use your agent tool with the `general-purpose` type, giving each one the brief file path plus the inputs listed in that brief. Use the configured `judge_model` for graders and comparators when it is set. Launch independent agents in one message so they run in parallel. Without an agent tool, do the work inline and say in the report that grading was not independent.

## 0. Verbs other than `run`

- **`status`**: run `py <rsi>/scripts/budget.py status --run-dir <run>`, then read `<run>/state.json` and the last `decision.json`. Report the iteration, champion, stop reason and spend in a few lines.
- **`report`**: run `py <rsi>/scripts/report.py --run-dir <run>` and summarize it.
- **`apply`**: do step 8 only.
- **`anchors`**: run `py <rsi>/scripts/anchors.py list --run-dir <run>`. Review or retire anchors with the user ([references/anchors.md](references/anchors.md)).
- **`resume`**: find the run (the given path, or the newest `runs/*` under the target's workspace). Read `state.json` (`phase`, `iteration`, `champion`, `pending_challenger`) and run `todo.py --run-dir <run> --iteration <it>`, then continue at that phase.

For `status`, `report`, `apply` and `anchors`, the default run is the newest one.

## 1. Resolve the target and create the run

```
py <rsi>/scripts/init_workspace.py --target "<path>" [--name SKILL] [--workspace DIR] --goal-text "<goal>"
    [--max-iterations N] [--max-hours H] [--max-cost-usd X] [--max-tokens N] [--cases N]
    [--model M] [--judge-model M] [--unpaired] [--baseline] [--report-only] [--unattended]
```

- **Exit 4:** several skills were found. Ask the user which one (list the candidates), then re-run with `--name`.
- **Exit 2:** nothing was found. Ask the user for the folder.

The script resolves the skill and its kind: project skill, plugin skill (run with `--plugin-dir`), or standalone. It also detects static gates (the project's tests, linters, generators run with `--check`, `claude plugin validate`). The workspace defaults to `<repo parent>/rsi-workspace/<skill-name>/`, outside the target. The script snapshots the project to `<run>/candidates/v0/` and writes `config.json`, `target.json` and `state.json`.

Next, record the starting state:

1. Note `target.json` → `git.dirty`. Pre-existing changes in the target belong to the user; mention them in the report.
2. Run `py <rsi>/scripts/static_gates.py --run-dir <run> --candidate v0`. Failures here are **pre-existing**: they are recorded and never block a challenger.

If a gate fails only because of the environment (for example `claude plugin validate` is not available), remove it from `config.json` and say so in the report.

## 2. Profile, goal, and confirmation

1. **Profile.** Spawn one subagent with [agents/profiler.md](agents/profiler.md). It gets the candidate root `<run>/candidates/v0`, `<run>/target.json`, the goal text, and `out = <run>/profile.json`.
2. **Goal.** Write `<run>/goal.json` from the user's goal, using [references/goals.md](references/goals.md).
3. **Size the run.**
   - Cases per iteration: `profile.cases_per_iteration` unless the user set one.
   - Cost per iteration ≈ cases × cost per case × 2 (paired), plus about 30% for agents.
   - Default cost cap: that per-iteration cost × (max_iterations + 1), rounded up.
   - Apply the numbers with `py <rsi>/scripts/budget.py set-limits --run-dir <run> --cases N --max-cost-usd X [--timeout S]`. Set the timeout to about 2.5 × `est_minutes_per_case`.
4. **Confirm once.** Unless `--unattended` is set, ask one question with your question tool showing: the target and its kind, the goal as compiled, cases per iteration, the limits, the estimated cost and time, and the workspace path. Offer "Start", "Change limits" and "Report only". Apply any changes the user makes. With `--unattended`, state the same facts in one message and start.

## 3. Generate fresh cases (each iteration `N`, starting at 0)

1. **Anchors.** `py <rsi>/scripts/anchors.py export --run-dir <run> --iteration N` copies any persistent anchors into this iteration's cases.
2. **Context for the generator.**
   - `py <rsi>/scripts/cases.py bank --run-dir <run>` gives summaries of earlier cases, to avoid repeats.
   - `py <rsi>/scripts/cases.py probes --run-dir <run>` gives the failure categories seen so far, with no details.
3. **Generate.** Spawn the case generator with [agents/case_generator.md](agents/case_generator.md). Give it `profile.json`, `goal.json`, the cases folder `<run>/iterations/NNN/cases`, N, the bank, the probe categories, and the skill folder of `candidates/v0` (it reads the skill, never another candidate).
   - Do not give it diffs, findings, transcripts or improver notes. Its ignorance is what makes the cases held out.
4. **Register.** `py <rsi>/scripts/cases.py register --run-dir <run> --iteration N` validates the cases, makes their ids unique (`iNNN-` prefix, 40 characters at most) and logs them to the case bank.
   - On exit 1, send the listed problems back to the same generator (or fix trivial ones yourself) and register again.

## 4. Run the cases

Which candidates run:
- **Champion:** `state.champion` always runs on the fresh cases and the anchors.
- **Challenger:** `state.pending_challenger`, if there is one, runs on the same cases.
  - It also replays the previous iteration's failed cases. Get the replay list with `py <rsi>/scripts/cases.py failed --run-dir <run> --iteration <N-1>` (use the `replay` value).
  - With `--unpaired`, run only the challenger, plus its replays.
- **No-skill baseline:** with `--baseline` on iteration 0, add `--role noskill=v0`.

Start the batch in the background and wait for the completion notice:

```
py <rsi>/scripts/run_batch.py --run-dir <run> --iteration N --role champion=<champ>
    [--role challenger=<chall> --replay <list>] [--parallel P]
```

- **What each run does.** Each run happens in its own sandbox, using `claude -p ... --permission-mode bypassPermissions` with hooks off, cwd set to the sandbox and the prompt on stdin. Details and caveats: [references/target_types.md](references/target_types.md).
- **Exit 3:** jobs were skipped because a budget limit was reached. Go to step 6, which records the stop.
- **Exit 4:** an infrastructure failure. Either the CLI is not logged in (its preflight runs `claude auth status`), or runs failed on auth, API or quota errors before doing any work. Nothing is graded. Tell the user the `fix` it prints (usually `claude auth login` in a terminal) and stop. Once it is fixed, `resume` continues from here; those runs are never cached.
- **Failures.** Any run with `status: error` and an empty transcript is an infrastructure failure. Look at its `stderr.txt`, fix the cause (for example a bad `--model`) and re-run with `--force` before grading.
- **Triggering.** If `skill_invoked` is false on explicit-invocation cases, the skill did not load. That is an infrastructure failure too.

Then run the deterministic checks: `py <rsi>/scripts/check_case.py --run-dir <run> --iteration N --all`.

## 5. Grade and compare

1. **List the work.** `py <rsi>/scripts/todo.py --run-dir <run> --iteration N` lists each run still to grade, with every path its grader needs.
2. **Grade.** Spawn graders with [agents/grader.md](agents/grader.md), one per run. For cheap skills, one grader may take up to 3 runs of the same case. Each grader also gets `goal.json` and the skill folder the run used (`metrics.skill_path`).
   - **Never** tell a grader which role or candidate produced a run.
3. **Compare.** When a challenger ran, `py <rsi>/scripts/aggregate.py blind --run-dir <run> --iteration N` copies each champion/challenger pair into `comparison/<case>/{A,B}` in a random order per case.
   - Spawn comparators with [agents/comparator.md](agents/comparator.md), one per pair.
   - If a verdict is `tie` or has low confidence, run one more comparator with A and B swapped (give it A as B and B as A), writing to `comparison/<case>.swap.json`. When the two verdicts disagree, the pair counts as a tie.
4. **Check completeness.** `todo.py` must now show nothing left in `to_grade` or `to_compare`.
5. **Merge synonyms.** `py <rsi>/scripts/cases.py keys --run-dir <run>` lists every finding key. Where two keys name the same defect, merge them with `--alias other-key=canonical-key` (keep the older key). Pass the list to later graders as `known keys`, so attempts, stuck and fixed are tracked across iterations.

## 6. Score and decide

```
py <rsi>/scripts/aggregate.py score  --run-dir <run> --iteration N
py <rsi>/scripts/aggregate.py decide --run-dir <run> --iteration N --dry-run
```

1. **Read the dry run.** Read the dry-run decision (gate results, reasons, `next_action`, `stop_reason`). If it promotes, look at the challenger's diff (`candidates/<id>.diff.patch`). You may **veto** a promotion that the evidence does not support, for example if the grading looks wrong or the change is clearly harmful. You can never force a promotion past the gate. The rules are in [references/stopping.md](references/stopping.md).
2. **Commit the decision.** Run decide again without `--dry-run`, adding `--veto "<reason>"` if you vetoed. It updates `history.json` and `state.json`. Exit 10 means stop: go to step 8.
3. **Report the iteration.** Tell the user in 2–3 lines:
   - iteration N
   - the champion, and whether a challenger was promoted
   - blocking findings on the fresh cases
   - the clean streak
   - spend so far, and the next action

## 7. Improve (when `next_action` is `improve` or `divergent`)

1. **Create the challenger.** `py <rsi>/scripts/candidate.py new --run-dir <run> --iteration N [--mode divergent]` copies the champion to the next `vK`.
2. **Gather findings.** `py <rsi>/scripts/cases.py findings --run-dir <run> --iteration N --role <role that is now champion>`. That role is `challenger` if this iteration promoted, `champion` otherwise.
3. **Improve.** Spawn the improver with [agents/improver.md](agents/improver.md). Give it:
   - the candidate root and the skill dir in it
   - the findings JSON, with stuck keys marked so it leaves them alone
   - the `do_not_repeat` list
   - `goal.json`
   - the mode
   - the `meta` path
   - the target's AGENTS.md / CLAUDE.md paths

   If all non-stuck findings are minor (and the goal blocks on major), skip improving and set up a confirm iteration instead.
4. **Check the challenger:**
   - **Diff:** `py <rsi>/scripts/candidate.py diff --run-dir <run> --candidate vK --write`
   - **Leak scan:** `py <rsi>/scripts/leak_scan.py --run-dir <run> --candidate vK`
   - **Static gates:** `py <rsi>/scripts/static_gates.py --run-dir <run> --candidate vK`
   - **Overfit review:** spawn a reviewer with [agents/overfit_reviewer.md](agents/overfit_reviewer.md), giving it the patch file, the `approach` from the meta, the skill dir, and `out = <run>/candidates/vK.overfit.json`. Give it **no** cases or findings.
5. **One revision if a check fails.** If any check fails, send the failures to the improver (continue the same agent if you can) for one revision, then repeat step 4. If it still fails, run `py <rsi>/scripts/candidate.py reject --run-dir <run> --candidate vK --reason "<why>"`. That counts as a failed attempt, and the next iteration is champion-only.
6. **Move on.** Return to step 3 with N + 1. First run `py <rsi>/scripts/budget.py check --run-dir <run> --reserve-usd <one iteration's cost>`. On exit 3, stop as `limit` and go to step 8.

When `next_action` is `confirm`, skip this step and go straight to step 3 with N + 1, which runs the champion only.

## 8. Finish

1. **Apply** (unless `--report-only` or `--no-apply`, or the champion is still `v0`):

   ```
   py <rsi>/scripts/apply_to_target.py --run-dir <run> --run-gates
   ```

   - It writes the champion's changes into the target as **uncommitted edits** and always writes `<run>/champion.patch`.
   - **Exit 2:** the target changed while the run was going. Nothing was written. Tell the user to apply the patch by hand.
   - **Exit 1:** a gate fails in the target. Report it and do not try to fix it here.
   - Never commit, never switch branches, and never touch files the user changed.
2. **Report.** `py <rsi>/scripts/report.py --run-dir <run>` writes `REPORT.md` and `report.html`:
   - the trajectory
   - what was fixed and what remains (each with its class and evidence)
   - fix attempts and challengers
   - spend
   - the champion's changes
3. **Offer anchors** (skip with `--unattended`). These are cases that qualify under [references/anchors.md](references/anchors.md): a stable external ground truth, or a cheap deterministic regression check for something fixed in this run.
   - Pick at most 3, ask the user to choose with a multi-select question, then run `py <rsi>/scripts/anchors.py add --run-dir <run> --iteration <it> --case <id> --why "..."` for each one chosen.
   - Say plainly when nothing qualifies. That is the normal outcome for skills whose answers drift.
4. **Tell the user:**
   - the stop reason, in words, for example "goal met after 2 clean iterations", "cost limit reached", "remaining findings are external (API outage)", or "no further improvement after a divergent attempt"
   - what was fixed and what remains
   - spend and time
   - where the report is
   - that `git diff` in the target shows the changes, which are left uncommitted for review

## Rules that keep it honest

- **Held-out by construction.**
  - Cases are written before the challenger is evaluated, by an agent that never sees any candidate or diff.
  - The challenger is judged on those cases.
  - Replayed cases only show that the fix works at all; they never decide a promotion on their own.
- **Fresh every iteration.** Generated cases stay in the workspace and are never written into the target. The case bank only stops repeats.
- **Fix causes, not cases.** The improver states each fix as a general rule. The leak scan, the overfit review and the next iteration's new cases all check this.
- **Classify before fixing.** `case_specific` findings (a bad or ambiguous case) and `external` findings (network, keys, data the source lacks, model limits) never count against the skill and are never "fixed" in it. Report them.
- **Evidence or it did not happen.** A criterion passes only with cited evidence. Ground truth comes from the inputs or a public authority, never from the skill's own output.
- **Safety.**
  - Test runs bypass permission prompts inside a sandbox folder, but they can still use the network and reach other paths.
  - Never point a case at real user data or at production systems with side effects.
  - Never print, log or store key values.
  - Any change to files outside the sandbox (other than scratch files in the system temp folder) is a critical finding.
- **Stop honestly.** If the goal cannot be met (stuck, external, plateau, budget), stop and say why, with the evidence. Do not keep spending.
