#!/usr/bin/env python3
"""Find the target skill from a path and describe it.

    python -I resolve_target.py <path> [--name <skill-name>] [--out target.json]

<path> may be a SKILL.md, a skill folder, or any project folder that contains skills at
depth: .claude/skills/<x>/, plugins/<p>/skills/<x>/, skills/<x>/, or elsewhere.

Exit codes: 0 found (JSON on stdout), 2 nothing found, 4 ambiguous (several skills; pass --name).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rsilib  # noqa: E402

SEARCH_SKIP = rsilib.ALWAYS_EXCLUDE_DIRS | {"out", "dist", "build", "evals", "fixtures", "rsi-workspace"}


def find_skill_mds(root: Path, max_depth: int = 6) -> list:
    found = []
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        dirnames[:] = [d for d in dirnames if d not in SEARCH_SKIP and not d.endswith("-workspace")]
        if depth >= max_depth:
            dirnames[:] = []
        if "SKILL.md" in filenames:
            found.append(Path(dirpath))
    return sorted(found)


def classify(skill_dir: Path) -> dict:
    parent, grand = skill_dir.parent, skill_dir.parent.parent
    if parent.name == "skills" and (grand / ".claude-plugin" / "plugin.json").exists():
        return {"kind": "plugin_skill", "plugin_dir": grand}
    if parent.name == "skills" and grand.name == ".claude":
        return {"kind": "project_skill", "plugin_dir": None}
    return {"kind": "standalone", "plugin_dir": None}


def detect_gates(repo: Path, skill_dir: Path, kind: str, plugin_dir) -> list:
    """Static gates: cheap commands that must keep passing. Placeholders: {repo} {skill} {python}."""
    gates = [{"id": "rsi:structure", "cmd": "builtin", "required": True,
              "why": "frontmatter, description <= 1024 chars, body <= 500 lines, links resolve"}]
    rel_skill = skill_dir.relative_to(repo).as_posix() if skill_dir != repo else "."
    if (repo / "tests" / "run_all.py").exists():
        gates.append({"id": "tests:run_all", "cmd": "{python} -I tests/run_all.py", "cwd": "repo",
                      "timeout": 1800, "required": True})
    elif any((repo / "tests").glob("test_*.py")) or (repo / "pytest.ini").exists() or (repo / "conftest.py").exists():
        gates.append({"id": "tests:pytest", "cmd": "{python} -m pytest -q", "cwd": "repo", "timeout": 1800,
                      "required": True})
    for p in sorted((skill_dir / "scripts").glob("lint_skill*.py")) if (skill_dir / "scripts").exists() else []:
        gates.append({"id": f"lint:{p.stem}", "cmd": "{python} -I {skill}/scripts/" + p.name, "cwd": "repo",
                      "timeout": 300, "required": True})
    for p in (repo / "tools").glob("gen_*.py") if (repo / "tools").exists() else []:
        if "--check" in rsilib.read_text(p):
            gates.append({"id": f"tools:{p.stem}", "cmd": "{python} tools/" + p.name + " --check", "cwd": "repo",
                          "timeout": 300, "required": True})
    if kind == "plugin_skill" and plugin_dir is not None:
        rel_plugin = Path(plugin_dir).relative_to(repo).as_posix()
        gates.append({"id": "claude:plugin-validate", "cmd": f"claude plugin validate {rel_plugin}", "cwd": "repo",
                      "timeout": 120, "required": False})
    _ = rel_skill
    return gates


def detect_packager(repo: Path) -> dict:
    for rel in ("tools/package_skill.py", "scripts/package_skill.py", "package_skill.py"):
        p = repo / rel
        if p.exists():
            text = rsilib.read_text(p)
            excludes = bool(re.search(r"""["']evals["']""", text))
            return {"path": rel, "mentions_evals": excludes}
    return {"path": None, "mentions_evals": None}


def detect_limits(repo: Path) -> dict:
    hints = {}
    tests = repo / "tests"
    if tests.exists():
        for p in tests.rglob("test_*.py"):
            try:
                t = rsilib.read_text(p)
            except OSError:
                continue
            if "1024" in t and "description" in t.lower():
                hints["description_max_chars"] = 1024
            if re.search(r"\b500\b", t) and "SKILL.md" in t:
                hints["body_max_lines"] = 500
            m = re.search(r"\b(\d{3})\b[^\n]{0,40}files?", t)
            if m and "zip" in t.lower():
                hints.setdefault("zip_file_limit_hint", int(m.group(1)))
    return hints


def anchors_location(repo: Path, skill_dir: Path, name: str, packager: dict) -> dict:
    in_skill = skill_dir / "evals" / "evals.json"
    in_repo = repo / "evals" / name / "evals.json"
    if in_skill.exists():
        return {"path": str(in_skill), "exists": True}
    if in_repo.exists():
        return {"path": str(in_repo), "exists": True}
    # A packager that ships the skill folder and does not mention evals would ship them: keep anchors outside it.
    ships = packager.get("path") and not packager.get("mentions_evals")
    target = in_repo if (ships and repo != skill_dir) else in_skill
    return {"path": str(target), "exists": False}


def resolve(path: str, name: str | None = None):
    """Return (exit_code, info). exit 0 = found, 2 = nothing found, 4 = ambiguous."""
    p = Path(os.path.expanduser(path.strip('"'))).resolve()
    if not p.exists():
        return 2, {"error": "path does not exist", "path": str(p)}
    if p.is_file() and p.name == "SKILL.md":
        candidates = [p.parent]
    elif (p / "SKILL.md").exists():
        candidates = [p]
    else:
        candidates = find_skill_mds(p)
    if name:
        candidates = [c for c in candidates if c.name == name or rsilib.skill_stats(c)["name"] == name]
    if not candidates:
        return 2, {"error": "no SKILL.md found", "path": str(p)}
    if len(candidates) > 1:
        return 4, {"error": "several skills found; pass --name",
                   "candidates": [{"dir": str(c), "name": rsilib.skill_stats(c)["name"]} for c in candidates]}

    skill_dir = candidates[0]
    stats = rsilib.skill_stats(skill_dir)
    cls = classify(skill_dir)
    groot = rsilib.git_root(skill_dir)
    if groot is not None:
        repo = groot
    elif cls["kind"] == "plugin_skill":
        repo = cls["plugin_dir"]
        # a marketplace folder above the plugin folder is the project root
        for up in [cls["plugin_dir"].parent, cls["plugin_dir"].parent.parent]:
            if (up / ".claude-plugin" / "marketplace.json").exists():
                repo = up
    elif cls["kind"] == "project_skill":
        repo = skill_dir.parent.parent.parent
    else:
        repo = p if (p.is_dir() and p != skill_dir and skill_dir.is_relative_to(p)) else skill_dir
    repo = Path(repo).resolve()
    name = stats["name"] or skill_dir.name
    packager = detect_packager(repo)
    plugin_dir = cls["plugin_dir"]
    plugin_name = None
    if plugin_dir is not None:
        pj = rsilib.read_json(Path(plugin_dir) / ".claude-plugin" / "plugin.json", {}) or {}
        plugin_name = pj.get("name") or Path(plugin_dir).name

    validators = []
    if (skill_dir / "scripts").exists():
        for s in sorted((skill_dir / "scripts").rglob("*.py")):
            if re.search(r"(valid|audit|check|lint|verify)", s.stem, re.I):
                validators.append(s.relative_to(skill_dir).as_posix())

    out = {
        "name": name,
        "kind": cls["kind"],
        "repo_root": str(repo),
        "skill_dir": str(skill_dir),
        "skill_rel": skill_dir.relative_to(repo).as_posix() if skill_dir != repo else ".",
        "plugin_dir": str(plugin_dir) if plugin_dir else None,
        "plugin_rel": Path(plugin_dir).relative_to(repo).as_posix() if plugin_dir else None,
        "plugin_name": plugin_name,
        "invoke": f"/{plugin_name}:{name}" if plugin_name else f"/{name}",
        "skill": stats,
        "git": rsilib.git_info(repo),
        "gates": detect_gates(repo, skill_dir, cls["kind"], plugin_dir),
        "validators": validators,
        "packager": packager,
        "limits": detect_limits(repo),
        "anchors": anchors_location(repo, skill_dir, name, packager),
        "docs": [str(d) for d in [repo / "AGENTS.md", repo / "CLAUDE.md", repo / "README.md",
                                  skill_dir / "README.md"] if d.exists()],
        "default_workspace": str(repo.parent / "rsi-workspace" / name),
        "file_count": len(rsilib.list_project_files(repo)),
    }
    return 0, out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("--name", help="pick this skill when the path holds several")
    ap.add_argument("--out", help="also write the JSON here")
    a = ap.parse_args()
    code, out = resolve(a.path, a.name)
    if code == 0 and a.out:
        rsilib.write_json(a.out, out)
    rsilib.emit(out, code)


if __name__ == "__main__":
    main()
