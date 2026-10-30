# rsi-skill (maintainers)

This repo holds one skill, `plugins/rsi-skill/skills/rsi-skill/`, shipped as the `rsi-skill` plugin.

- **Manifests:** one per harness, sharing the same `skills/` folder.
  - Claude: `plugins/rsi-skill/.claude-plugin/plugin.json`
  - Codex: `plugins/rsi-skill/.codex-plugin/plugin.json`, with `"skills": "./skills/"`
- **Marketplace:** none in this repo. The plugin is listed in the `zsu` marketplace of `zsu/agent-plugins`, which installs it from this repo on GitHub with a `git-subdir` source.
- **Local development:** use `claude --plugin-dir plugins/rsi-skill`.

## Checks before a change lands

- **Tests:** `python -I tests/run_all.py`. They are offline: a fake `claude` CLI stands in at `tests/fixtures/fake_claude.py`, and the toy target lives in `tests/fixtures/toy-skill/`.
- **Manifests:** `claude plugin validate .` and `claude plugin validate plugins/rsi-skill`.
- **Limits (enforced by the tests):**
  - SKILL.md description ≤ 1024 characters.
  - SKILL.md body ≤ 500 lines.
  - Every `agents/` and `references/` link must resolve.
  - Every script must answer `--help`.

## Rules for scripts

- **Standard library only.** They must run on Windows and macOS/Linux.
- **Never** use `select` on pipes; read with threads.
- **Long paths:** Windows long paths may be off, so keep run-folder paths short. Case ids are capped at 40 characters.
- **Output:** every script prints JSON and signals with its exit code. `aggregate.py decide` exits 10 to mean "stop".
- **State lives in files** in the run folder, so any step can be resumed. Do not hold state in the orchestrator's context.

## Rules for the loop

- **Generated cases stay in the workspace, never in the target.** Only anchors the user approves are written to the target.
- **The case generator never sees** candidates, diffs or findings. Only failure categories reach it.
- **The overfit reviewer never sees** cases.
- **The promotion gate is deterministic** (`aggregate.py decide`). The orchestrator may veto a promotion, but it never forces one.
- **The target is written to only by `apply_to_target.py`**, as uncommitted edits, and only after a hash check against the snapshot.

## Releases

Bump `version` in **both** `plugin.json` files: the tests fail if the two differ. The catalog in `zsu/agent-plugins` never holds a version, so a release is only the bump, pushed to `main`.
