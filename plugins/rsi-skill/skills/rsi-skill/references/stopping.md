# Promotion gate and stop rules

`aggregate.py decide` applies these rules, so they are deterministic. You may override a promotion **to rejected** if the challenger's diff or results look wrong to you. Record why with `candidate.py reject`. Never promote a challenger that the script rejected.

## Promotion gate

A challenger becomes champion only if **all** of these hold:

| Gate | Meaning |
|---|---|
| `static_gates` | `vN.gates.json` exists and no required gate fails that passed on v0. |
| `leak_scan` | `vN.leak.json` passed. The diff has no case ids, case prompt text, ground-truth values or rsi terms. |
| `overfit_review` | `vN.overfit.json` has verdict `pass`. |
| `no_new_critical` | No critical finding key on the fresh cases that the champion did not also have. |
| `not_worse` | Blocking findings per case on the fresh cases is no higher than the champion's. |
| `improved` | At least one of: fewer blocking findings per case; pairwise wins minus losses ≥ `promote_margin` (default 1); or the replay fix rate ≥ `replay_fix_min` with no fresh regression and net wins ≥ 0. |
| `replay_fixed` | Of the earlier cases whose failures the challenger was meant to fix, at least `replay_fix_min` (default 50%) now have no blocking findings. |
| `anchors_no_regression` | The challenger has no more anchor failures than the champion. |

**Paired evaluation.** Champion and challenger run on the same new cases. No one, including the improver, saw those cases when the challenger was written. This is the held-out test.

**Replay cases.** Replay cases are the ones the improver saw, so they only show whether the fix works at all. They can never be the only reason to promote.

**Unpaired runs.** With `--unpaired` the champion is not re-run, and the challenger's rate is compared with the champion's last fresh rate. The report flags this as weaker evidence.

## After the gate

- `targets` are the finding keys the challenger was meant to fix. Each target still present afterwards counts one attempt, and a rejected challenger counts one attempt for every target. A key with 2 or more attempts is **stuck**. A key listed in `unfixable` is stuck at once.
- `do_not_repeat` keeps the last 30 approaches and their outcomes. The improver reads it and must not try a rejected approach again.
- A new champion starts its clean streak again from that iteration.

## Stop rules (first match wins)

1. `report_only`: the run was started with `--report-only` (iteration 0 only).
2. `goal_met`: the clean streak has reached `confirm_streak`.
3. `limit:<cost|tokens|time|iterations>`: a limit in `budget.py status` is reached. Iteration 0 is the baseline. `max_iterations` counts the iterations after it.
4. `unreachable:stuck`: every remaining blocking finding key is stuck.
5. `unreachable:external`: for two iterations in a row, at least half of the fresh cases were inconclusive for external reasons.
6. `plateau`: `patience` (default 2) evaluations in a row without a promotion, after the one divergent attempt was already spent.

## Next action when not stopping

- `confirm`: the champion is clean but the streak is short. The next iteration runs the champion on new cases with no challenger.
- `divergent`: `patience` non-promotions in a row. The next challenger is a structurally different approach (`candidate.py new --mode divergent`), tried once per champion.
- `improve`: the normal case. Write a challenger that targets the blocking findings that are not stuck.
