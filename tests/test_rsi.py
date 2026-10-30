"""Offline tests for the rsi-skill scripts (no network, no real Claude Code runs)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "plugins" / "rsi-skill" / "skills" / "rsi-skill"
SCRIPTS = SKILL / "scripts"
FIX = Path(__file__).resolve().parent / "fixtures"
TOY = FIX / "toy-skill"
FAKE = [sys.executable, str(FIX / "fake_claude.py")]
sys.path.insert(0, str(SCRIPTS))

import aggregate  # noqa: E402
import rsilib  # noqa: E402


def sh(script: str, *args, env=None, check=None):
    e = dict(os.environ)
    e.update(env or {})
    r = subprocess.run([sys.executable, "-I", str(SCRIPTS / script), *map(str, args)], capture_output=True,
                       text=True, encoding="utf-8", env=e)
    try:
        out = json.loads(r.stdout) if r.stdout.strip() else {}
    except json.JSONDecodeError:
        out = {"_raw": r.stdout}
    if check is not None and r.returncode != check:
        raise AssertionError(f"{script} exit {r.returncode} (want {check}): {r.stdout[-2000:]} {r.stderr[-2000:]}")
    return r.returncode, out


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, check=True)


def make_git_copy(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst)
    git(dst, "init", "-q", "-b", "main")
    git(dst, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(dst, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    return dst


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rsi-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def new_run(self, **kw) -> Path:
        repo = make_git_copy(TOY, self.tmp / "toy")
        args = ["--target", repo, "--workspace", self.tmp / "ws", "--goal-text", "no major problems", "--run-id", "r1"]
        for k, v in kw.items():
            args += [f"--{k.replace('_', '-')}", v] if v is not True else [f"--{k.replace('_', '-')}"]
        _, out = sh("init_workspace.py", *args, check=0)
        run = Path(out["run_dir"])
        rsilib.write_json(run / "goal.json", {"text": "no major problems", "blocking_severity": "major", "confirm_streak": 2})
        return run


class TestResolve(TmpCase):
    def test_project_skill(self):
        repo = make_git_copy(TOY, self.tmp / "toy")
        code, out = sh("resolve_target.py", repo)
        self.assertEqual(code, 0)
        self.assertEqual(out["kind"], "project_skill")
        self.assertEqual(out["name"], "csv-summary")
        self.assertEqual(out["skill_rel"], ".claude/skills/csv-summary")
        self.assertEqual(out["invoke"], "/csv-summary")
        self.assertEqual(Path(out["repo_root"]), repo.resolve())
        ids = [g["id"] for g in out["gates"]]
        self.assertIn("rsi:structure", ids)
        self.assertIn("tests:run_all", ids)
        self.assertEqual(Path(out["default_workspace"]), (self.tmp / "rsi-workspace" / "csv-summary").resolve())

    def test_plugin_skill_and_anchor_location(self):
        repo = self.tmp / "mkt"
        sk = repo / "plugins" / "pp" / "skills" / "alpha"
        sk.mkdir(parents=True)
        (sk / "SKILL.md").write_text("---\nname: alpha\ndescription: d\n---\nbody\n", encoding="utf-8")
        (repo / "plugins" / "pp" / ".claude-plugin").mkdir(parents=True)
        (repo / "plugins" / "pp" / ".claude-plugin" / "plugin.json").write_text('{"name": "pp"}', encoding="utf-8")
        (repo / "tools").mkdir()
        (repo / "tools" / "package_skill.py").write_text("# zips the skill folder\n", encoding="utf-8")
        git(repo, "init", "-q")
        code, out = sh("resolve_target.py", repo)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["kind"], "plugin_skill")
        self.assertEqual(out["plugin_rel"], "plugins/pp")
        self.assertEqual(out["invoke"], "/pp:alpha")
        # the packager does not mention evals -> anchors go to the repo, not the shipped skill folder
        self.assertTrue(out["anchors"]["path"].replace("\\", "/").endswith("evals/alpha/evals.json"))

    def test_ambiguous_and_missing(self):
        for n in ("one", "two"):
            d = self.tmp / "p" / "skills" / n
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"---\nname: {n}\ndescription: x\n---\n", encoding="utf-8")
        code, out = sh("resolve_target.py", self.tmp / "p")
        self.assertEqual(code, 4)
        self.assertEqual(len(out["candidates"]), 2)
        code, out = sh("resolve_target.py", self.tmp / "p", "--name", "two")
        self.assertEqual(code, 0)
        self.assertEqual(out["kind"], "standalone")
        code, _ = sh("resolve_target.py", self.tmp / "nope")
        self.assertEqual(code, 2)


class TestWorkspace(TmpCase):
    def test_snapshot_and_refuse_inside(self):
        run = self.new_run()
        v0 = run / "candidates" / "v0"
        self.assertTrue((v0 / ".claude/skills/csv-summary/SKILL.md").exists())
        self.assertFalse((v0 / ".git").exists())
        self.assertTrue((run / "baseline_manifest.json").exists())
        st = rsilib.read_json(run / "state.json")
        self.assertEqual(st["champion"], "v0")
        code, _ = sh("init_workspace.py", "--target", self.tmp / "toy", "--workspace", self.tmp / "toy" / "ws")
        self.assertNotEqual(code, 0)

    def test_static_gates_v0(self):
        run = self.new_run()
        code, out = sh("static_gates.py", "--run-dir", run, "--candidate", "v0")
        self.assertEqual(code, 0, out)
        self.assertTrue(all(r["passed"] for r in out["results"]), out)


def write_case(run: Path, it: int, cid: str, **extra) -> Path:
    d = run / "iterations" / f"{it:03d}" / "cases" / cid
    (d / "files").mkdir(parents=True, exist_ok=True)
    (d / "files" / "data.csv").write_text("a\n1\n2\n3\n4\n", encoding="utf-8")
    case = {"id": cid, "title": "t", "prompt": extra.pop("prompt", f"Summarize data.csv for me please ({cid})."),
            "files": ["data.csv"],
            "deterministic_checks": [{"id": "out", "type": "file_exists", "path": "out.txt", "severity": "critical"}],
            "criteria": [{"id": "c1", "text": "gives the median", "severity": "major"}]}
    case.update(extra)
    rsilib.write_json(d / "case.json", case)
    return d


class TestRunCase(TmpCase):
    def test_fake_run_metrics_and_sandbox(self):
        run = self.new_run(max_cost_usd=5)
        v0_evals = run / "candidates/v0/.claude/skills/csv-summary/evals"
        v0_evals.mkdir(parents=True)
        (v0_evals / "evals.json").write_text("{}", encoding="utf-8")
        cdir = write_case(run, 0, "i000-a")
        code, m = sh("run_case.py", "--run-dir", run, "--iteration", 0, "--case", cdir, "--candidate", "v0",
                     "--role", "champion", "--claude-cmd", json.dumps(FAKE), check=0)
        self.assertEqual(m["status"], "ok")
        self.assertTrue(m["skill_invoked"])
        self.assertAlmostEqual(m["cost_usd"], 0.0123)
        self.assertEqual(m["tokens_billable"], 160)
        sandbox = Path(m["sandbox"])
        self.assertEqual((sandbox / "out.txt").read_text(encoding="utf-8").split()[0], "/csv-summary")
        self.assertTrue((sandbox / ".claude/skills/csv-summary/SKILL.md").exists())
        self.assertFalse((sandbox / ".claude/skills/csv-summary/evals").exists(), "evals must never reach the sandbox")
        self.assertIn("out.txt", m["files_created"])
        argv = json.loads((sandbox / "argv.json").read_text(encoding="utf-8"))
        self.assertIn("bypassPermissions", argv)
        self.assertIn("--no-session-persistence", argv)
        role_dir = sandbox.parent
        self.assertIn("Median is 4.5", (role_dir / "final.md").read_text(encoding="utf-8"))
        self.assertIn("TOOL Skill", (role_dir / "digest.md").read_text(encoding="utf-8"))
        b = rsilib.read_jsonl(run / "budget.jsonl")
        self.assertEqual(len(b), 1)
        # checks
        _, ch = sh("check_case.py", "--run-dir", run, "--iteration", 0, "--case-id", "i000-a", "--role", "champion", check=0)
        self.assertEqual(ch["summary"]["passed"], 1)

    def test_timeout_and_budget_stop(self):
        run = self.new_run(timeout=2)
        cdir = write_case(run, 0, "i000-slow")
        code, m = sh("run_case.py", "--run-dir", run, "--iteration", 0, "--case", cdir, "--candidate", "v0",
                     "--role", "champion", "--claude-cmd", json.dumps(FAKE), env={"FAKE_CLAUDE_SLEEP": "20"})
        self.assertEqual(code, 1)
        self.assertEqual(m["status"], "timeout")
        # budget: a tiny cap is exceeded after one real-cost run, and run_batch skips the rest
        run2 = self.new_run_second(max_cost_usd=0.01)
        for c in ("i000-x", "i000-y", "i000-z"):
            write_case(run2, 0, c)
        code, out = sh("run_batch.py", "--run-dir", run2, "--iteration", 0, "--role", "champion=v0",
                       "--parallel", 1, "--claude-cmd", json.dumps(FAKE))
        self.assertEqual(code, 3, out)
        self.assertEqual(out["ok"], 1)
        self.assertEqual(out["skipped_budget"], 2)

    def test_not_logged_in_stops_batch(self):
        run = self.new_run()
        write_case(run, 0, "i000-auth")
        code, out = sh("run_batch.py", "--run-dir", run, "--iteration", 0, "--role", "champion=v0",
                       "--claude-cmd", json.dumps(FAKE), env={"FAKE_CLAUDE_LOGGED_OUT": "1", "ANTHROPIC_API_KEY": ""})
        self.assertEqual(code, 4, out)
        self.assertIn("not logged in", out["error"])

    def test_infra_failure_detected(self):
        import run_case
        lines = [json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Failed to authenticate: OAuth session expired"}]}}),
                 json.dumps({"type": "result", "subtype": "success", "result": "Failed to authenticate",
                             "total_cost_usd": 0, "usage": {}})]
        parsed = run_case.parse_stream(lines, "x")
        self.assertTrue(run_case.is_infra_failure(parsed, lines))
        ok = [json.dumps({"type": "result", "subtype": "success", "result": "done", "usage": {"output_tokens": 5}})]
        self.assertFalse(run_case.is_infra_failure(run_case.parse_stream(ok, "x"), ok))

    def test_inline_skill_use_detected(self):
        # an explicit /skill prompt is expanded inline: no Skill tool call, but the skill's files are used
        import run_case
        lines = [json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "python C:\\x\\sandbox\\.claude\\skills\\csv-summary\\scripts\\summarize.py a.csv"}}]}})]
        self.assertEqual(run_case.parse_stream(lines, "csv-summary")["skill_invocations"], ["Bash:skill-files"])
        self.assertEqual(run_case.parse_stream(lines, "other")["skill_invocations"], [])
        # the skill folder with no trailing slash counts; a sibling folder sharing the prefix does not
        cd = [json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "cd C:/x/plugin/data-analyst/skills/data-analyst && python scripts/fetch.py"}}]}})]
        self.assertEqual(run_case.parse_stream(cd, "data-analyst")["skill_invocations"], ["Bash:skill-files"])
        self.assertEqual(run_case.parse_stream(cd, "data")["skill_invocations"], [])
        sib = [json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "C:/x/skills/csv-summary-v2/SKILL.md"}}]}})]
        self.assertEqual(run_case.parse_stream(sib, "csv-summary")["skill_invocations"], [])

    def new_run_second(self, **kw):
        repo = make_git_copy(TOY, self.tmp / "toy2")
        args = ["--target", repo, "--workspace", self.tmp / "ws2", "--run-id", "r2"]
        for k, v in kw.items():
            args += [f"--{k.replace('_', '-')}", v]
        _, out = sh("init_workspace.py", *args, check=0)
        return Path(out["run_dir"])

    def test_plugin_dir_used(self):
        repo = self.tmp / "mkt"
        sk = repo / "plugins" / "pp" / "skills" / "alpha"
        sk.mkdir(parents=True)
        (sk / "SKILL.md").write_text("---\nname: alpha\ndescription: d\n---\nbody\n", encoding="utf-8")
        (repo / "plugins/pp/.claude-plugin").mkdir(parents=True)
        (repo / "plugins/pp/.claude-plugin/plugin.json").write_text('{"name": "pp"}', encoding="utf-8")
        _, out = sh("init_workspace.py", "--target", repo, "--workspace", self.tmp / "ws", "--run-id", "p", check=0)
        run = Path(out["run_dir"])
        cdir = write_case(run, 0, "i000-p")
        _, m = sh("run_case.py", "--run-dir", run, "--iteration", 0, "--case", cdir, "--candidate", "v0",
                  "--role", "champion", "--claude-cmd", json.dumps(FAKE), check=0)
        argv = json.loads((Path(m["sandbox"]) / "argv.json").read_text(encoding="utf-8"))
        self.assertIn("--plugin-dir", argv)
        self.assertTrue(Path(argv[argv.index("--plugin-dir") + 1], "skills", "alpha", "SKILL.md").exists())
        self.assertTrue((Path(m["sandbox"]) / "out.txt").read_text(encoding="utf-8").startswith("/pp:alpha "))


class TestChecks(TmpCase):
    def test_check_types(self):
        sb = self.tmp / "sb"
        sb.mkdir()
        (sb / "r.json").write_text(json.dumps({"a": {"b": [1, 2.5]}}), encoding="utf-8")
        (sb / "report.html").write_text("<h1>Summary</h1> total $1,234.5", encoding="utf-8")
        final = self.tmp / "final.md"
        final.write_text("The median is 4.5", encoding="utf-8")
        import check_case as cc
        ctx = {"sandbox": sb, "out": sb, "skill": "", "final": final, "case": "", "python": sys.executable}
        cases = [
            ({"type": "file_exists", "path": "**/*.html"}, True),
            ({"type": "file_absent", "path": "*.tmp"}, True),
            ({"type": "contains", "text": "median"}, True),
            ({"type": "not_contains", "path": "report.html", "text": "TODO"}, True),
            ({"type": "regex", "path": "report.html", "pattern": "<h1>summary", "flags": "i"}, True),
            ({"type": "json_path", "path": "r.json", "expr": "a.b.1", "min": 2, "max": 3}, True),
            ({"type": "json_path", "path": "r.json", "expr": "a.c", "exists": False}, True),
            ({"type": "number_near", "path": "report.html", "pattern": r"total \$([\d,.]+)", "target": 1234, "rel_tol": 0.01}, True),
            ({"type": "number_near", "pattern": r"median is ([\d.]+)", "target": 4, "abs_tol": 0.1}, False),
            ({"type": "command", "cmd": '{python} -c "import sys; sys.exit(3)"', "expect_exit": 3}, True),
            ({"type": "skill_invoked"}, False),
        ]
        for chk, want in cases:
            r = cc.run_check(chk, ctx, sb, {"skill_invoked": False})
            self.assertEqual(r["passed"], want, (chk, r))


def fabricate(run: Path, it: int, role: str, cand: str, findings: dict, quality=4):
    """Create finished runs with gradings: findings = {case_id: [(key, severity, class), ...]}."""
    for cid, fs in findings.items():
        if not (run / "iterations" / f"{it:03d}" / "cases" / cid / "case.json").exists() and role != "replay":
            write_case(run, it, cid)
        rd = run / "iterations" / f"{it:03d}" / "runs" / cid / role
        (rd / "sandbox").mkdir(parents=True, exist_ok=True)
        (rd / "final.md").write_text(f"{role} answer", encoding="utf-8")
        rsilib.write_json(rd / "metrics.json", {"case_id": cid, "role": role, "candidate": cand, "status": "ok",
                                                "sandbox": str(rd / "sandbox"), "cost_usd": 0.1, "duration_seconds": 1,
                                                "skill_invoked": True, "files_created": []})
        rsilib.write_json(rd / "checks.json", {"checks": [], "summary": {"passed": 1, "failed": 0, "total": 1}})
        rsilib.write_json(rd / "grading.json", {
            "case_id": cid, "role": role, "expectations": [{"text": "x", "passed": not fs, "evidence": "e"}],
            "summary": {"passed": 0 if fs else 1, "failed": 1 if fs else 0, "total": 1},
            "findings": [{"key": k, "title": k, "severity": s, "class": c, "category": "cat-" + k, "evidence": "ev"}
                         for k, s, c in fs], "overall_quality": quality})


def approve(run: Path, cand: str, leak=True, overfit=True, gates_ok=True, targets=(), mode="fix"):
    m = rsilib.read_json(run / "candidates" / f"{cand}.meta.json")
    m.update({"targets": list(targets), "mode": mode, "approach": "general rule"})
    rsilib.write_json(run / "candidates" / f"{cand}.meta.json", m)
    rsilib.write_json(run / "candidates" / f"{cand}.leak.json", {"passed": leak})
    rsilib.write_json(run / "candidates" / f"{cand}.overfit.json", {"verdict": "pass" if overfit else "fail"})
    rsilib.write_json(run / "candidates" / f"{cand}.gates.json", {"blocking": [] if gates_ok else ["tests"]})


class TestDecide(TmpCase):
    def setUp(self):
        super().setUp()
        self.run = self.new_run()
        sh("static_gates.py", "--run-dir", self.run, "--candidate", "v0", check=0)

    def it(self, n, champ, champ_f, chall=None, chall_f=None, **appr):
        fabricate(self.run, n, "champion", champ, champ_f)
        if chall:
            fabricate(self.run, n, "challenger", chall, chall_f)
            aggregate.blind(rsilib.Run(self.run), n)
            # comparator: challenger wins wherever it has fewer findings
            comp = self.run / "iterations" / f"{n:03d}" / "comparison"
            for op in comp.glob("*.order.json"):
                cid = op.name[:-11]
                order = rsilib.read_json(op)
                better = "challenger" if len(chall_f[cid]) < len(champ_f[cid]) else (
                    "champion" if len(chall_f[cid]) > len(champ_f[cid]) else None)
                w = next((k for k, v in order.items() if v == better), "tie")
                rsilib.write_json(comp / f"{cid}.json", {"winner": w, "confidence": "high"})
        sh("aggregate.py", "score", "--run-dir", self.run, "--iteration", n, check=0)
        code, d = sh("aggregate.py", "decide", "--run-dir", self.run, "--iteration", n)
        return code, d

    def new_challenger(self, it, **appr):
        _, c = sh("candidate.py", "new", "--run-dir", self.run, "--iteration", it, check=0)
        approve(self.run, c["candidate"], **appr)
        return c["candidate"]

    def test_full_trajectory_promote_then_goal(self):
        bug = [("median-even", "major", "skill_general")]
        code, d = self.it(0, "v0", {"i000-a": bug, "i000-b": bug, "i000-c": []})
        self.assertEqual((code, d["next_action"], d["clean"]), (0, "improve", False))
        v1 = self.new_challenger(0, targets=["median-even"])
        code, d = self.it(1, "v0", {"i001-a": bug, "i001-b": []}, v1, {"i001-a": [], "i001-b": []})
        self.assertTrue(d["promoted"], d)
        self.assertEqual(d["champion_after"], v1)
        self.assertTrue(d["clean"])
        self.assertEqual(d["clean_streak"], 1)
        self.assertEqual(d["next_action"], "confirm")
        code, d = self.it(2, v1, {"i002-a": [], "i002-b": [], "i002-c": []})
        self.assertEqual(code, 10)
        self.assertEqual(d["stop_reason"], "goal_met")
        st = rsilib.read_json(self.run / "state.json")
        self.assertEqual(st["champion"], v1)
        self.assertEqual(st["status"], "stopped")
        _, rep = sh("report.py", "--run-dir", self.run, check=0)
        self.assertIn("median-even", rep["fixed"])
        self.assertTrue((self.run / "report.html").exists())

    def test_gate_failures_block_promotion(self):
        bug = [("b1", "major", "skill_general")]
        self.it(0, "v0", {"i000-a": bug, "i000-b": bug})
        v1 = self.new_challenger(0, leak=False, targets=["b1"])
        _, d = self.it(1, "v0", {"i001-a": bug, "i001-b": bug}, v1, {"i001-a": [], "i001-b": []})
        self.assertFalse(d["promoted"])
        self.assertIn("gate failed: leak_scan", d["reasons"])
        # new critical in challenger also blocks even with fewer findings overall
        v2 = self.new_challenger(1, targets=["b1"])
        _, d = self.it(2, "v0", {"i002-a": bug, "i002-b": bug},
                       v2, {"i002-a": [("crash", "critical", "skill_general")], "i002-b": []})
        self.assertFalse(d["promoted"])
        self.assertFalse(d["gate"]["no_new_critical"])

    def test_case_specific_and_external_do_not_block(self):
        _, d = self.it(0, "v0", {"i000-a": [("ambiguous", "major", "case_specific")],
                                 "i000-b": [("api-down", "major", "external")], "i000-c": []})
        self.assertTrue(d["clean"], d)
        self.assertEqual(d["remaining_blocking_keys"], [])

    def test_plateau_then_divergent_then_stop(self):
        bug = [("b1", "major", "skill_general")]
        same = {"x": bug, "y": bug}
        self.it(0, "v0", {f"i000-{k}": v for k, v in same.items()})
        v1 = self.new_challenger(0, targets=["b1"])
        _, d = self.it(1, "v0", {f"i001-{k}": v for k, v in same.items()}, v1, {f"i001-{k}": v for k, v in same.items()})
        self.assertEqual(d["next_action"], "improve")
        v2 = self.new_challenger(1, targets=["b2-other"])
        _, d = self.it(2, "v0", {f"i002-{k}": v for k, v in same.items()}, v2, {f"i002-{k}": v for k, v in same.items()})
        # b1 attempted twice? only once (v1); v2 targeted another key -> patience reached -> divergent
        self.assertEqual(d["next_action"], "divergent")
        v3 = self.new_challenger(2, targets=["b3-other"], mode="divergent")
        code, d = self.it(3, "v0", {f"i003-{k}": v for k, v in same.items()}, v3, {f"i003-{k}": v for k, v in same.items()})
        self.assertEqual(code, 10)
        self.assertEqual(d["stop_reason"], "plateau")

    def test_stuck_is_unreachable(self):
        bug = [("b1", "major", "skill_general")]
        self.it(0, "v0", {"i000-a": bug, "i000-b": bug})
        v1 = self.new_challenger(0, targets=["b1"])
        _, d = self.it(1, "v0", {"i001-a": bug, "i001-b": bug}, v1, {"i001-a": bug, "i001-b": bug})
        v2 = self.new_challenger(1, targets=["b1"])
        code, d = self.it(2, "v0", {"i002-a": bug, "i002-b": bug}, v2, {"i002-a": bug, "i002-b": bug})
        self.assertEqual(code, 10, d)
        self.assertEqual(d["stop_reason"], "unreachable:stuck")

    def test_iteration_limit_and_veto(self):
        rsilib.Run(self.run)
        sh("budget.py", "set-limits", "--run-dir", self.run, "--max-iterations", 1, check=0)
        bug = [("b1", "major", "skill_general")]
        self.it(0, "v0", {"i000-a": bug, "i000-b": bug})
        v1 = self.new_challenger(0, targets=["b1"])
        fabricate(self.run, 1, "champion", "v0", {"i001-a": bug, "i001-b": bug})
        fabricate(self.run, 1, "challenger", v1, {"i001-a": [], "i001-b": []})
        sh("aggregate.py", "score", "--run-dir", self.run, "--iteration", 1, check=0)
        code, d = sh("aggregate.py", "decide", "--run-dir", self.run, "--iteration", 1, "--veto", "diff looks wrong")
        self.assertFalse(d["promoted"])
        self.assertEqual(code, 10)
        self.assertEqual(d["stop_reason"], "limit:iterations")


class TestLeakAndCases(TmpCase):
    def test_leak_scan(self):
        run = self.new_run()
        write_case(run, 0, "i000-even-median-case", prompt="Please summarise the quarterly widget sales in widgets_q3_2025.csv now",
                   ground_truth={"value": 1234.75})
        _, c = sh("candidate.py", "new", "--run-dir", run, check=0)
        skill_md = Path(c["skill_path"]) / "SKILL.md"
        code, out = sh("leak_scan.py", "--run-dir", run, "--candidate", c["candidate"])
        self.assertEqual(code, 0, out)
        skill_md.write_text(skill_md.read_text(encoding="utf-8") +
                            "\nAveraging the two middle values gives the median of an even count.\n", encoding="utf-8")
        code, out = sh("leak_scan.py", "--run-dir", run, "--candidate", c["candidate"])
        self.assertEqual(code, 0, out)
        skill_md.write_text(skill_md.read_text(encoding="utf-8") +
                            "\nIf asked to summarise the quarterly widget sales in widgets_q3, the answer is 1234.75.\n", encoding="utf-8")
        code, out = sh("leak_scan.py", "--run-dir", run, "--candidate", c["candidate"])
        self.assertEqual(code, 1)
        kinds = {h["kind"] for h in out["blocking"]}
        self.assertIn("prompt_text", kinds)
        self.assertIn("ground_truth", kinds)

    def test_register_and_bank(self):
        run = self.new_run()
        write_case(run, 0, "good")
        bad = write_case(run, 0, "bad", criteria=[], deterministic_checks=[{"type": "nope"}])
        code, out = sh("cases.py", "register", "--run-dir", run, "--iteration", 0)
        self.assertEqual(code, 1)
        self.assertIn("i000-good", out["registered"])
        self.assertIn("i000-bad", out["invalid"])
        shutil.rmtree(bad.parent / "i000-bad")
        write_case(run, 1, "again", prompt="Summarize  data.csv for me please (good).")
        code, out = sh("cases.py", "register", "--run-dir", run, "--iteration", 1)
        self.assertEqual(code, 1)
        self.assertIn("duplicate", json.dumps(out))
        _, bank = sh("cases.py", "bank", "--run-dir", run, check=0)
        self.assertEqual(bank["count"], 1)

    def test_anchor_roundtrip(self):
        run = self.new_run()
        write_case(run, 0, "i000-gt", ground_truth={"value": 2.5})
        _, out = sh("anchors.py", "add", "--run-dir", run, "--iteration", 0, "--case", "i000-gt", "--why", "stable", check=0)
        path = Path(out["path"])
        self.assertTrue(path.exists())
        data = rsilib.read_json(path)
        self.assertEqual(data["evals"][0]["id"], "gt")
        self.assertTrue((path.parent / data["evals"][0]["files"][0]).exists())
        _, ex = sh("anchors.py", "export", "--run-dir", run, "--iteration", 1, check=0)
        self.assertEqual(ex["exported"], ["anchor-gt"])
        c = rsilib.read_json(run / "iterations/001/cases/anchor-gt/case.json")
        self.assertTrue(c["anchor"])
        self.assertTrue((run / "iterations/001/cases/anchor-gt/files/data.csv").exists())
        _, v = sh("anchors.py", "validate", "--run-dir", run, check=0)
        self.assertEqual(v["problems"], [])

    def test_anchor_keeps_case_helpers(self):
        run = self.new_run()
        verify = [{"id": "verify", "type": "command", "cmd": "{python} -I {case}/verify.py {sandbox}/out.txt",
                   "expect_exit": 0, "severity": "critical"}]
        cdir = write_case(run, 0, "i000-hv", deterministic_checks=verify)
        (cdir / "verify.py").write_text("import sys\nsys.exit(0 if 'Summarize' in open(sys.argv[1]).read() else 1)\n",
                                        encoding="utf-8")
        (cdir / "__pycache__").mkdir()
        (cdir / "__pycache__" / "verify.cpython-313.pyc").write_bytes(b"x")
        # a check that needs a helper the case does not have is refused
        write_case(run, 0, "i000-nohelper", deterministic_checks=verify)
        code, _ = sh("anchors.py", "add", "--run-dir", run, "--iteration", 0, "--case", "i000-nohelper")
        self.assertNotEqual(code, 0)

        _, out = sh("anchors.py", "add", "--run-dir", run, "--iteration", 0, "--case", "i000-hv", "--why", "gt", check=0)
        base = Path(out["path"]).parent
        ev = rsilib.read_json(Path(out["path"]))["evals"][0]
        self.assertEqual(ev["rsi"]["case_files"], ["case/hv/verify.py"])
        self.assertEqual(ev["files"], ["files/hv/data.csv"])
        self.assertTrue((base / "case/hv/verify.py").exists())
        self.assertFalse((base / "files/hv/verify.py").exists())

        _, ex = sh("anchors.py", "export", "--run-dir", run, "--iteration", 1, check=0)
        self.assertEqual(ex["exported"], ["anchor-hv"])
        adir = run / "iterations/001/cases/anchor-hv"
        self.assertTrue((adir / "verify.py").exists())
        self.assertFalse((adir / "files/verify.py").exists(), "helpers must never reach the sandbox")
        _, m = sh("run_case.py", "--run-dir", run, "--iteration", 1, "--case", adir, "--candidate", "v0",
                  "--role", "champion", "--claude-cmd", json.dumps(FAKE), check=0)
        self.assertFalse((Path(m["sandbox"]) / "verify.py").exists())
        _, ch = sh("check_case.py", "--run-dir", run, "--iteration", 1, "--case-id", "anchor-hv", "--role", "champion",
                   check=0)
        self.assertEqual(ch["summary"]["passed"], 1, ch)
        _, v = sh("anchors.py", "validate", "--run-dir", run, check=0)
        self.assertEqual(v["problems"], [])

        # an older entry without rsi.case_files still exports; validate names the missing helper
        data = rsilib.read_json(Path(out["path"]))
        del data["evals"][0]["rsi"]["case_files"]
        rsilib.write_json(Path(out["path"]), data)
        _, ex = sh("anchors.py", "export", "--run-dir", run, "--iteration", 2, check=0)
        self.assertTrue((run / "iterations/002/cases/anchor-hv/files/data.csv").exists())
        code, v = sh("anchors.py", "validate", "--run-dir", run)
        self.assertEqual(code, 1)
        self.assertIn("{case}/verify.py", json.dumps(v["problems"]))


class TestApply(TmpCase):
    def test_apply_and_conflict(self):
        run = self.new_run()
        _, c = sh("candidate.py", "new", "--run-dir", run, check=0)
        script = Path(c["skill_path"]) / "scripts" / "summarize.py"
        script.write_text(script.read_text(encoding="utf-8").replace("return s[len(s) // 2]",
                                                                       "n = len(s)\n    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2"),
                          encoding="utf-8")
        sh("candidate.py", "promote", "--run-dir", run, "--candidate", c["candidate"], check=0)
        target = self.tmp / "toy" / ".claude/skills/csv-summary/scripts/summarize.py"
        code, out = sh("apply_to_target.py", "--run-dir", run, "--dry-run", check=0)
        self.assertEqual(out["changed"], [".claude/skills/csv-summary/scripts/summarize.py"])
        self.assertNotIn("n % 2", target.read_text(encoding="utf-8"))
        # conflict: the user edited the same file meanwhile
        orig = target.read_bytes()
        target.write_bytes(orig + b"\n# user edit\n")
        code, out = sh("apply_to_target.py", "--run-dir", run)
        self.assertEqual(code, 2)
        target.write_bytes(orig)
        code, out = sh("apply_to_target.py", "--run-dir", run, "--run-gates", check=0)
        self.assertTrue(out["applied"])
        self.assertIn("n % 2", target.read_text(encoding="utf-8"))
        self.assertTrue((run / "champion.patch").read_text(encoding="utf-8").startswith("diff --git"))
        st = subprocess.run(["git", "status", "--porcelain"], cwd=self.tmp / "toy", capture_output=True, text=True).stdout
        self.assertIn("summarize.py", st)  # uncommitted


class TestBudget(TmpCase):
    def test_add_status_check(self):
        run = self.new_run(max_tokens=1000)
        code, _ = sh("budget.py", "check", "--run-dir", run)
        self.assertEqual(code, 0)
        sh("budget.py", "add", "--run-dir", run, "--tokens", 1500, "--note", "grader", check=0)
        code, out = sh("budget.py", "check", "--run-dir", run)
        self.assertEqual(code, 3)
        self.assertIn("tokens", out["exceeded"])
        self.assertGreater(out["cost_usd_estimated_agents"], 0)

    def test_check_stop_records_limit_in_state(self):
        # a reserve that cannot fit stops the run: without --stop state.json still read "running"
        run = self.new_run(max_tokens=1000)
        code, _ = sh("budget.py", "check", "--run-dir", run, "--reserve-tokens", 10, "--stop")
        self.assertEqual(code, 0)
        self.assertNotEqual(rsilib.read_json(Path(run) / "state.json")["status"], "stopped")
        code, _ = sh("budget.py", "check", "--run-dir", run, "--reserve-tokens", 5000)
        self.assertEqual(code, 3)
        self.assertNotEqual(rsilib.read_json(Path(run) / "state.json")["status"], "stopped")
        code, out = sh("budget.py", "check", "--run-dir", run, "--reserve-tokens", 5000, "--stop")
        self.assertEqual(code, 3)
        st = rsilib.read_json(Path(run) / "state.json")
        self.assertEqual((st["status"], st["stop_reason"], st["phase"]), ("stopped", "limit:tokens", "finish"))
        self.assertEqual(out["stop_reason"], "limit:tokens")


class TestStructure(unittest.TestCase):
    def test_own_skill_structure(self):
        import static_gates
        r = static_gates.structure_gate(SKILL, {"body_max_lines": 500})
        self.assertTrue(r["passed"], r)

    def test_briefs_and_references_exist(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        import re
        for rel in re.findall(r"\]\(((?:agents|references)/[^)#]+)", text):
            self.assertTrue((SKILL / rel).exists(), rel)
        for f in ("profiler", "case_generator", "grader", "comparator", "improver", "overfit_reviewer"):
            self.assertTrue((SKILL / "agents" / f"{f}.md").exists(), f)

    def test_scripts_have_help(self):
        for p in sorted(SCRIPTS.glob("*.py")):
            if p.name == "rsilib.py":
                continue
            r = subprocess.run([sys.executable, "-I", str(p), "--help"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{p.name}: {r.stderr[-500:]}")

    def test_manifests(self):
        # installed only through the zsu-dev catalog (zsu/agent-plugins@dev): no marketplace here, and the
        # Claude and Codex manifests of the one plugin cannot drift
        self.assertFalse((ROOT / ".claude-plugin").exists(), "the catalog lives in zsu/agent-plugins")
        self.assertFalse((ROOT / ".agents" / "plugins" / "marketplace.json").exists())
        pdir = ROOT / "plugins" / "rsi-skill"
        claude = json.loads((pdir / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        codex = json.loads((pdir / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(codex["name"], claude["name"])
        self.assertEqual(codex["version"], claude["version"], "bump both plugin.json files together")
        self.assertRegex(claude["version"], r"^\d+\.\d+\.\d+$")
        self.assertTrue(codex["skills"].startswith("./"))
        self.assertTrue((pdir / codex["skills"] / claude["name"] / "SKILL.md").is_file())
        self.assertTrue(codex["interface"]["displayName"])

    def test_no_local_absolute_paths(self):
        # shipped files must work on any machine: no drive letters or home folders baked in
        import re
        pat = re.compile(r"(?<![A-Za-z])[A-Za-z]:\\[A-Za-z]|/Users/[a-z]|/home/[a-z]|\\Users\\")
        files = [p for p in (ROOT / "plugins").rglob("*") if p.is_file() and p.suffix in (".md", ".py", ".json", ".html")]
        files += [ROOT / n for n in ("README.md", "AGENTS.md")]
        hits = [f"{p.relative_to(ROOT)}:{i}" for p in files
                for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if pat.search(line)]
        self.assertEqual(hits, [])

    def test_no_select_on_pipes(self):
        # select() on pipes breaks on Windows; the runner must use threads
        self.assertNotIn("select.select", (SCRIPTS / "run_case.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
