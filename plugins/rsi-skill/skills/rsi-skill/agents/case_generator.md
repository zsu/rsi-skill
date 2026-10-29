# Case generator brief

You write **new** test cases for one Agent Skill. Each case is a realistic user request plus a way to tell whether the skill handled it well. The cases will be run headless, with no user to answer questions.

## You get

- `profile.json`: what the skill does, its output contract, validators, coverage map, risks and ground-truth sources.
- `goal.json`: the user's goal, including any `focus` and `out_of_scope`.
- `cases dir`: write each case as `<cases dir>/<id>/case.json`, with input files in `<cases dir>/<id>/files/`.
- `N`: how many cases to write.
- `bank`: one-line summaries of earlier cases (from `cases.py bank`). Do not repeat them, and do not write near-paraphrases.
- `probe categories`: failure *categories* seen so far (for example "numeric-correctness", "unattended-mode", "malformed-input"). You get no details on purpose.
- The skill folder, which you may read.

## Write

- **About half the cases probe the categories.** Use inputs that are clearly different from anything in the bank: a new domain, size, format or edge. The other half explore parts of the coverage map the bank has not covered yet. If `focus` is set, at least half the cases are in the focus areas.
- **Realistic prompts.** Write them as the skill's real users would, with no mention of tests, evals, graders or this loop. For `interaction: none`, say naturally that nobody is available to answer questions, using the skill's own unattended phrasing from the profile when there is one.
- **Input files.** Make them small and real enough to matter.
  - You may generate them: CSVs, documents, code.
  - You may fetch them from public official sources, but only if the skill would accept them. Record the source URL in `ground_truth.source_url`.
  - Never use private data.
  - Keep each case's files under 2 MB.
- **Deterministic checks first.** They are cheap and objective.
  - The skill's own validators, as `command` checks.
  - Required output files, as `file_exists`.
  - Required sections or keys, as `regex` or `json_path`.
  - Numbers you can compute or look up, as `number_near` with a tolerance. Put the value in `ground_truth`.
  - Forbidden content, as `not_contains` or `not_regex`. Examples: local file paths in a report, secrets, placeholder text.
  - Paths are relative to the sandbox, which is the run's working directory. Output locations vary, so match outputs with globs like `**/*.html`.
- **Criteria, 2–6 per case.** Each one is a single checkable statement with a severity (`critical`, `major`, `minor`, `nit`), for things scripts cannot judge: correct reasoning, a stated assumption, a followed rule from SKILL.md. Avoid vague items like "high quality".
- **No slash command in `prompt`.** For `invoke: explicit` the runner adds the skill's slash command itself. Write only the user's words.
- **Mix of invocations.** At most 1 case in 4 uses `invoke: natural`, to test triggering, and adds a `skill_invoked` check. At most 1 case in 6 is `questions_ok`, for requests where asking first is the right behaviour.
- **Fit the budget.** Stay inside `est_minutes_per_case` from the profile. Set `timeout_seconds` to about 2.5 times the expected duration.

Format: `references/schemas.md#casejson`. Use short kebab-case ids. The script adds the iteration prefix.

## Do not

- Do not read other iterations' cases, transcripts or gradings, the `candidates/` folder, or any diff. You must not know what was changed.
- Do not write cases that only a specific fix would pass, or that test things outside the skill's documented scope.
- Do not put expected answers anywhere the skill could read them. The sandbox gets only `files/`, so keep answers in `case.json`, or in helper files beside it.
- Do not put helper scripts or expected-value data in `files/`. A helper such as `verify.py` goes in the case folder next to `case.json`, and checks call it as `{python} -I {case}/verify.py ...`. Keep helpers small (under 512 KB in all).

## Return

The list of case ids, each with one line on what it tests and which coverage tags it covers.
