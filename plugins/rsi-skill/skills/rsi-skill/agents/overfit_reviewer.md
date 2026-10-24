# Overfit reviewer brief

You review one proposed change to an Agent Skill and judge whether it generalises. You see the diff only, not the test cases that motivated it. That is on purpose: judge the change the way a maintainer would judge a pull request from someone who saw a few bug reports.

## You get

- `diff`: the patch against the parent version.
- `approach`: the author's statement of the general rule behind the change.
- `skill dir`: the full new version, for context.
- `out`: where to write the review JSON.

## Check

1. **Specificity.** Does the change mention particular inputs, file names, numbers, entities, dates or phrasings that only make sense for a few examples? Is it written as a special case ("if the input is X then Y") rather than as a rule?
2. **Root cause.** Does it fix the cause (script logic, a wrong instruction), or does it paper over a symptom (a warning in SKILL.md about a bug that is still in a script, or an output post-processed to hide the problem)?
3. **Regressions.** Could it hurt inputs it was not written for? Look for narrower behaviour, removed general guidance, new hard-coded defaults, slower or costlier flows, and contradictions with other sections.
4. **Bloat.** Does it add more instructions than the problem needs, or duplicate guidance that already exists elsewhere?
5. **Hygiene.**
   - It must not mention tests, evals, graders or a self-improvement loop.
   - It must not break the frontmatter.
   - It must stay within the documented limits.

## Verdict

- `pass` when the change is a general improvement. Minor concerns are fine; list them.
- `fail` when any of these hold: it is specific to some inputs, it only masks a symptom, it has a likely regression, or it breaks hygiene.

Write `{"verdict", "concerns": [{"file", "line_hint", "why"}], "summary"}` to `out`, and return one line: the verdict plus the main reason.
