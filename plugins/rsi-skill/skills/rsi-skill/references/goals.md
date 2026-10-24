# Turning the user's goal into goal.json

The user writes the goal in their own words, usually after "until". Turn it into the fields in [schemas.md](schemas.md#goaljson). Keep the user's text in `text`. If no goal is given, use the default in row 1 and say so in the confirmation question.

| User says | goal.json |
|---|---|
| (nothing), "no major problems", "until it works well" | `blocking_severity: major`, `confirm_streak: 2` |
| "no problems at all", "polish it", "until clean" | `blocking_severity: minor`, `confirm_streak: 2` |
| "no critical bugs", "until it doesn't crash" | `blocking_severity: critical`, `confirm_streak: 2` |
| "until all checks pass" | `blocking_severity: major`, `min_check_pass_rate: 1.0` |
| "pass rate ≥ 90%" | `min_criteria_pass_rate: 0.9` |
| "quality 4/5", "until reports are excellent" | `min_quality: 4`, `blocking_severity: major` |
| "focus on X", "especially X" | add X to `focus`. The case generator then puts about half the cases in X. |
| "don't touch Y", "ignore Y" | add Y to `out_of_scope`. The improver must not edit it, and graders must not judge it. |
| "until confident", "thoroughly" | `confirm_streak: 3` |

## Rules

- **Clean.** An iteration is clean when the champion has no blocking findings on its fresh cases, at least two-thirds of those cases are conclusive, every anchor passes, no static gate blocks, and any thresholds are met. The goal is met after `confirm_streak` clean iterations in a row. Each iteration uses new cases, so a streak of 2 means the skill was clean on two independent samples.
- **Severity.** Every case and grader uses this scale:
  - **critical**: wrong or harmful result, a crash, no deliverable, lost data, or an exposed secret.
  - **major**: a wrong number or a wrong conclusion, a missing required section, a broken output contract, a failed validator, or the skill ignoring its own documented rules.
  - **minor**: unclear wording, weak structure, missing nice-to-haves, or inefficiency.
  - **nit**: style.
- **Not "major".** A finding the grader classes as `case_specific` or `external` never blocks the goal. The report still lists it. If most cases are inconclusive for external reasons twice in a row, the run stops as `unreachable:external`.
- **Infeasible goals.** If the goal cannot be measured with this skill (for example "no bugs ever"), restate it as the nearest measurable goal and tell the user in the confirmation question.
