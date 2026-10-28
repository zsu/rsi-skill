#!/usr/bin/env python3
"""Run one eval case against one candidate of the target skill in a fresh sandbox.

    python -I run_case.py --run-dir R --iteration N --case <case-dir> --candidate v1
        --role champion|challenger|replay|noskill [--force] [--dry-run] [--claude-cmd CMD]

The sandbox is <run>/iterations/NNN/runs/<case-id>/<role>/sandbox/:
  * case input files are copied in;
  * project/standalone skills are copied to sandbox/.claude/skills/<name>/;
  * plugin skills are copied to <role>/plugin/<plugin>/ and loaded with --plugin-dir;
  * the skill's own evals/ folder is never copied (it could leak expected answers).
Claude Code runs headless: claude -p --output-format stream-json --verbose
--permission-mode bypassPermissions, prompt on stdin, cwd = sandbox, hooks disabled.

Writes transcript.jsonl, stderr.txt, final.md, metrics.json next to the sandbox and appends the
cost to <run>/budget.jsonl. Exit 0 when the run finished (whatever its quality), 1 on error/timeout.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rsilib  # noqa: E402

DROP_ENV = {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"}
WANTED_FLAGS = ["--no-session-persistence", "--max-budget-usd", "--max-turns", "--settings",
                "--setting-sources", "--permission-mode", "--plugin-dir"]


_CAPS_LOCK = threading.Lock()


def claude_caps(run: rsilib.Run, claude_cmd: list) -> dict:
    """Which optional CLI flags this Claude Code build has (cached per run)."""
    with _CAPS_LOCK:
        return _claude_caps(run, claude_cmd)


def _claude_caps(run: rsilib.Run, claude_cmd: list) -> dict:
    cache = run.dir / "claude_caps.json"
    caps = rsilib.read_json(cache)
    if caps and caps.get("cmd") == claude_cmd:
        return caps
    try:
        r = subprocess.run(claude_cmd + ["--help"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        text = r.stdout + r.stderr
    except (OSError, subprocess.TimeoutExpired):
        text = ""
    caps = {"cmd": claude_cmd, "flags": {f: (f in text) for f in WANTED_FLAGS}}
    rsilib.write_json(cache, caps)
    return caps


def kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def place_skill(run: rsilib.Run, candidate_root: Path, role_dir: Path, sandbox: Path):
    """Copy the candidate skill where Claude Code will find it. Returns (skill_path, extra_args)."""
    t = run.config["target"]
    ignore = {"evals"}
    if t["kind"] == "plugin_skill":
        plugin_src = candidate_root / t["plugin_rel"]
        plugin_dst = role_dir / "plugin" / (t.get("plugin_name") or Path(t["plugin_rel"]).name)
        rsilib.copy_tree(plugin_src, plugin_dst, ignore_dirs=ignore)
        rel_in_plugin = Path(t["skill_rel"]).relative_to(Path(t["plugin_rel"]))
        return plugin_dst / rel_in_plugin, ["--plugin-dir", str(plugin_dst)]
    src = candidate_root / t["skill_rel"]
    dst = sandbox / ".claude" / "skills" / t["name"]
    rsilib.copy_tree(src, dst, ignore_dirs=ignore | ({"tests"} if t["skill_rel"] == "." else set()))
    return dst, []


def parse_stream(lines: list, skill_name: str) -> dict:
    m = {"tool_calls": {}, "total_tool_calls": 0, "skill_invocations": [], "result": None,
         "cost_usd": None, "usage": {}, "num_turns": None, "is_error": None, "result_subtype": None,
         "skills_listed": None, "api_duration_ms": None}
    # the skill folder itself counts too ("cd .../skills/x && ..."), but not a sibling like skills/x-extra
    skill_dir = re.compile(re.escape(f"skills/{skill_name}") + r"(?![\w.-])")
    for line in lines:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        typ = ev.get("type")
        if typ == "system" and ev.get("subtype") == "init":
            sk = ev.get("skills") or ev.get("slash_commands")
            if isinstance(sk, list):
                m["skills_listed"] = [s if isinstance(s, str) else s.get("name") for s in sk][:200]
        elif typ == "assistant":
            for block in (ev.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name", "?")
                    m["tool_calls"][name] = m["tool_calls"].get(name, 0) + 1
                    m["total_tool_calls"] += 1
                    inp = block.get("input") or {}
                    if name == "Skill":
                        m["skill_invocations"].append(str(inp.get("skill") or inp.get("command") or inp))
                    else:
                        # an explicit /skill prompt is expanded inline (no Skill tool call), so also count
                        # any tool call that touches the skill's own files
                        blob = json.dumps(inp, ensure_ascii=False).replace("\\\\", "/").replace("\\", "/")
                        if skill_dir.search(blob) and f"{name}:skill-files" not in m["skill_invocations"]:
                            m["skill_invocations"].append(f"{name}:skill-files")
        elif typ == "result":
            m["result"] = ev.get("result")
            m["cost_usd"] = ev.get("total_cost_usd")
            m["usage"] = ev.get("usage") or {}
            m["num_turns"] = ev.get("num_turns")
            m["is_error"] = ev.get("is_error")
            m["result_subtype"] = ev.get("subtype")
            m["api_duration_ms"] = ev.get("duration_api_ms")
    u = m["usage"]
    m["tokens"] = sum(int(u.get(k) or 0) for k in ("input_tokens", "output_tokens",
                                                     "cache_creation_input_tokens", "cache_read_input_tokens"))
    m["tokens_billable"] = sum(int(u.get(k) or 0) for k in ("input_tokens", "output_tokens",
                                                              "cache_creation_input_tokens"))
    return m


INFRA_PATTERNS = ("failed to authenticate", "oauth session expired", "invalid api key", "please run /login",
                  "credit balance is too low", "overloaded_error")


def is_infra_failure(parsed: dict, lines: list, status: str = "ok") -> bool:
    """True when the CLI failed before the model did any work (auth, API, quota)."""
    if parsed.get("tokens") or status == "timeout":
        return False
    text = " ".join(lines[-20:]).lower()
    crashed_at_start = status == "error" and parsed.get("result") is None and not parsed["tool_calls"]
    return any(p in text for p in INFRA_PATTERNS) or crashed_at_start


def digest(lines: list, limit_chars: int = 60000) -> str:
    """A readable, bounded summary of the transcript for graders (full transcript stays on disk)."""
    out = []
    for line in lines:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        typ = ev.get("type")
        content = (ev.get("message") or {}).get("content")
        if typ == "assistant" and isinstance(content, list):
            for b in content:
                if b.get("type") == "text" and b.get("text", "").strip():
                    out.append("ASSISTANT: " + b["text"].strip()[:1500])
                elif b.get("type") == "tool_use":
                    inp = json.dumps(b.get("input"), ensure_ascii=False)
                    out.append(f"TOOL {b.get('name')}: {inp[:600]}")
        elif typ == "user" and isinstance(content, list):
            for b in content:
                if b.get("type") == "tool_result":
                    c = b.get("content")
                    text = c if isinstance(c, str) else " ".join(
                        x.get("text", "") for x in (c or []) if isinstance(x, dict))
                    flag = "ERROR " if b.get("is_error") else ""
                    out.append(f"  {flag}RESULT: {text.strip()[:500]}")
        elif typ == "result":
            out.append(f"FINAL ({ev.get('subtype')}, turns={ev.get('num_turns')}, cost=${ev.get('total_cost_usd')}): "
                       + str(ev.get("result") or "")[:3000])
    text = "\n".join(out)
    if len(text) > limit_chars:
        half = limit_chars // 2
        text = text[:half] + "\n\n[... middle of transcript omitted; see transcript.jsonl ...]\n\n" + text[-half:]
    return text


def run_case(run_dir, iteration: int, case_dir, candidate: str, role: str, force: bool = False,
             dry_run: bool = False, claude_cmd=None) -> dict:
    run = rsilib.Run(run_dir)
    cfg, t = run.config, run.config["target"]
    case_dir = Path(case_dir).resolve()
    case = rsilib.read_json(case_dir / "case.json")
    if not case:
        raise SystemExit(f"no case.json in {case_dir}")
    cid = case["id"]
    role_dir = run.iteration_dir(iteration) / "runs" / cid / role
    metrics_path = role_dir / "metrics.json"
    if metrics_path.exists() and not force:
        prev = rsilib.read_json(metrics_path)
        if prev.get("status") in ("ok", "error_result") and prev.get("candidate") == candidate:
            prev["skipped"] = "already ran (use --force to rerun)"
            return prev
    if role_dir.exists():
        shutil.rmtree(role_dir, ignore_errors=True)
    sandbox = role_dir / "sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)
    files_dir = case_dir / "files"
    if files_dir.exists():
        rsilib.copy_tree(files_dir, sandbox)

    candidate_root = run.candidate(candidate)
    if not candidate_root.exists():
        raise SystemExit(f"candidate not found: {candidate_root}")
    extra, skill_path = [], None
    if role != "noskill":
        skill_path, extra = place_skill(run, candidate_root, role_dir, sandbox)
    before = rsilib.manifest(sandbox)

    prompt = case["prompt"]
    if role != "noskill" and case.get("invoke", "explicit") == "explicit" and not prompt.lstrip().startswith("/"):
        prompt = f"{t['invoke']} {prompt}"
    elif role == "noskill" and prompt.lstrip().startswith(t["invoke"]):
        prompt = prompt.lstrip()[len(t["invoke"]):].lstrip()  # a no-skill baseline must not name the skill

    claude_cmd = claude_cmd or rsilib.find_claude_cmd()
    caps = claude_caps(run, claude_cmd)["flags"]
    cmd = list(claude_cmd) + ["-p", "--output-format", "stream-json", "--verbose",
                              "--permission-mode", "bypassPermissions"]
    if caps.get("--no-session-persistence"):
        cmd.append("--no-session-persistence")
    if caps.get("--settings"):
        cmd += ["--settings", json.dumps({"disableAllHooks": True})]
    if cfg.get("isolate_user_settings") and caps.get("--setting-sources"):
        cmd += ["--setting-sources", "project,local"]
    if cfg.get("model"):
        cmd += ["--model", cfg["model"]]
    max_turns = case.get("max_turns") or cfg["limits"].get("max_turns")
    if max_turns and caps.get("--max-turns"):
        cmd += ["--max-turns", str(max_turns)]
    case_budget = case.get("max_cost_usd") or cfg["limits"].get("max_cost_per_case_usd")
    if case_budget and caps.get("--max-budget-usd"):
        cmd += ["--max-budget-usd", str(case_budget)]
    cmd += extra
    timeout = int(case.get("timeout_seconds") or cfg["limits"].get("timeout_seconds") or 1200)

    (role_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    if dry_run:
        return {"dry_run": True, "cmd": cmd, "cwd": str(sandbox), "prompt": prompt, "timeout": timeout}

    env = {k: v for k, v in os.environ.items() if k not in DROP_ENV}
    started = time.time()
    started_iso = rsilib.now_iso()
    popen_kw = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    out_lines, err_chunks = [], []
    status, exit_code = "ok", None
    try:
        proc = subprocess.Popen(cmd, cwd=str(sandbox), env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, **popen_kw)
    except OSError as e:
        status, proc = "error", None
        err_chunks.append(f"failed to start claude: {e}")
    if proc is not None:
        def pump_out():
            for raw in iter(proc.stdout.readline, b""):
                out_lines.append(raw.decode("utf-8", errors="replace").rstrip("\r\n"))

        def pump_err():
            for raw in iter(proc.stderr.readline, b""):
                err_chunks.append(raw.decode("utf-8", errors="replace"))

        th = [threading.Thread(target=pump_out, daemon=True), threading.Thread(target=pump_err, daemon=True)]
        for x in th:
            x.start()
        try:
            proc.stdin.write(prompt.encode("utf-8"))
            proc.stdin.close()
        except OSError:
            pass
        try:
            exit_code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            status = "timeout"
            kill_tree(proc)
            try:
                exit_code = proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                exit_code = None
        for x in th:
            x.join(timeout=10)
        if status == "ok" and exit_code not in (0, None):
            status = "error"
    duration = round(time.time() - started, 1)

    (role_dir / "transcript.jsonl").write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    (role_dir / "stderr.txt").write_text("".join(err_chunks)[-200000:], encoding="utf-8")
    (role_dir / "digest.md").write_text(digest(out_lines), encoding="utf-8")
    parsed = parse_stream(out_lines, t["name"])
    if parsed["result"] is not None and status == "error":
        status = "error_result"  # the CLI finished with an error result (e.g. budget/turn cap)
    if parsed.get("result_subtype") and parsed["result_subtype"] != "success" and status == "ok":
        status = "error_result"
    if is_infra_failure(parsed, out_lines, status):
        status = "infra_error"  # auth/API failure before any work: not the skill's fault, never graded
    final = parsed["result"] if isinstance(parsed["result"], str) else ""
    (role_dir / "final.md").write_text(final or "", encoding="utf-8")

    after = rsilib.manifest(sandbox)
    created = sorted(k for k, h in after.items()
                     if before.get(k) != h and not k.startswith(".claude/skills/"))
    metrics = {
        "case_id": cid, "iteration": iteration, "role": role, "candidate": candidate, "status": status,
        "exit_code": exit_code, "started_at": started_iso, "ended_at": rsilib.now_iso(),
        "duration_seconds": duration, "timeout_seconds": timeout,
        "cost_usd": parsed["cost_usd"], "tokens": parsed["tokens"], "tokens_billable": parsed["tokens_billable"],
        "usage": parsed["usage"], "num_turns": parsed["num_turns"], "result_subtype": parsed["result_subtype"],
        "tool_calls": parsed["tool_calls"], "total_tool_calls": parsed["total_tool_calls"],
        "skill_invoked": bool(parsed["skill_invocations"]) if role != "noskill" else None,
        "skill_invocations": parsed["skill_invocations"],
        "files_created": created[:500], "output_chars": len(final or ""),
        "transcript_events": len(out_lines),
        "sandbox": str(sandbox), "skill_path": str(skill_path) if skill_path else None,
        "cmd": [c if len(c) < 300 else c[:300] + "…" for c in cmd],
    }
    rsilib.write_json(metrics_path, metrics)
    rsilib.append_jsonl(run.dir / "budget.jsonl", {
        "at": rsilib.now_iso(), "kind": "case", "iteration": iteration, "case": cid, "role": role,
        "candidate": candidate, "cost_usd": parsed["cost_usd"] or 0.0, "tokens": parsed["tokens_billable"],
        "duration_seconds": duration})
    return metrics


def reparse(role_dir) -> dict:
    """Recompute the transcript-derived metrics of a finished run (after a parser change)."""
    role_dir = Path(role_dir)
    m = rsilib.read_json(role_dir / "metrics.json")
    lines = (role_dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    name = Path(m["skill_path"]).name if m.get("skill_path") else ""
    parsed = parse_stream(lines, name)
    m.update({"tool_calls": parsed["tool_calls"], "total_tool_calls": parsed["total_tool_calls"],
              "skill_invocations": parsed["skill_invocations"],
              "skill_invoked": bool(parsed["skill_invocations"]) if m.get("role") != "noskill" else None})
    rsilib.write_json(role_dir / "metrics.json", m)
    (role_dir / "digest.md").write_text(digest(lines), encoding="utf-8")
    return m


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--iteration", type=int, required=True)
    ap.add_argument("--case", required=True, help="case folder containing case.json")
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--role", required=True, choices=["champion", "challenger", "replay", "noskill"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--claude-cmd")
    ap.add_argument("--reparse", action="store_true", help="only re-derive metrics from the saved transcript")
    a = ap.parse_args()
    if a.reparse:
        run = rsilib.Run(a.run_dir)
        cid = rsilib.read_json(Path(a.case) / "case.json")["id"]
        rsilib.emit(reparse(run.iteration_dir(a.iteration) / "runs" / cid / a.role))
    cmd = rsilib.find_claude_cmd(a.claude_cmd)
    m = run_case(a.run_dir, a.iteration, a.case, a.candidate, a.role, a.force, a.dry_run, cmd)
    rsilib.emit(m, 0 if m.get("dry_run") or m.get("status") in ("ok", "error_result") or m.get("skipped") else 1)


if __name__ == "__main__":
    main()
