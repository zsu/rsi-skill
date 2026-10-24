# Target types and running them

## Where the skill sits

| Kind | Layout | How a test run loads it |
|---|---|---|
| `project_skill` | `<repo>/.claude/skills/<name>/SKILL.md` | The skill folder is copied to `sandbox/.claude/skills/<name>/`, and the run's cwd is the sandbox. |
| `plugin_skill` | `<repo>/plugins/<p>/skills/<name>/SKILL.md`, with `<repo>/plugins/<p>/.claude-plugin/plugin.json` | The plugin folder is copied next to the sandbox and loaded with `--plugin-dir`. The slash command is `/<p>:<name>`. |
| `standalone` | Any folder with SKILL.md | Same as `project_skill`. If the folder is also the repo root, its `tests/` is not copied in. |

A candidate is a **full project copy** (tracked plus untracked-unignored files), so the improver can also change tests, tools and docs outside the skill folder. Only the skill (or plugin) folder goes into a sandbox, and never its `evals/`.

## Headless runs

`run_case.py` runs:

```
claude -p --output-format stream-json --verbose --permission-mode bypassPermissions
  [--no-session-persistence] [--settings {"disableAllHooks":true}] [--model M]
  [--max-turns N] [--max-budget-usd X] [--plugin-dir P] [--setting-sources project,local]
```

- **Prompt.** It goes in on stdin. The cwd is the sandbox, and `CLAUDECODE` is removed from the environment so a nested run is allowed.
- **Optional flags.** Each is used only when `claude --help` lists it (cached per run in `claude_caps.json`).
- **Windows.** Output is read by threads and a timeout kills the whole process tree.
- **What `bypassPermissions` means.** The run cannot stop to ask for approval, but it can still use the network and reach files outside the sandbox. That is why every write a skill makes should stay in the sandbox. Report any run that creates or changes files elsewhere as a critical finding. Scratch files in the system temp folder are the exception: at most minor.
- **`--isolate-user-settings`.** Loads only project and local settings. This drops the user's plugins, hooks and `env` from test runs. Use it when user-level plugins would interfere. Don't use it when the skill reads keys from the user's settings `env`.

## No user available

A test run cannot answer questions. Most skills that ask questions document an unattended mode (for example "the request says not to ask questions", or a `no_user` reason code). The profiler finds that phrasing, and the case generator writes it naturally into `interaction: none` prompts. A case with `interaction: questions_ok` tests whether the skill asks when it should: a short clarifying question that ends the run is then a correct outcome.

## Expensive or networked skills

- **Cost.** Use the profiler's `est_cost_usd_per_case` to size each iteration. Use 2–3 cases for skills that cost several dollars a case, and 5–6 for cheap ones. Paired evaluation doubles the cost; use `--unpaired` only when the budget forces it.
- **Network.** Network failures are `external` findings, not skill defects. When an agency or API is down, the run should stop as `unreachable:external` rather than "fix" working code.
- **Keys.** Check only whether a key is present. Never print, log or store a key value.
- **Run in parallel.** Use `parallel` at 2–3 so local CPU, rate limits and quotas are not exhausted, then wait for the batch to finish.
