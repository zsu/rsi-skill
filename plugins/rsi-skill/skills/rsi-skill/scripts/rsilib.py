"""Shared helpers for the rsi-skill scripts (standard library only, Windows-safe)."""
from __future__ import annotations

import datetime as _dt
import difflib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

SEVERITIES = ["nit", "minor", "major", "critical"]
# Never copied into a candidate or the sandbox, whatever git says.
ALWAYS_EXCLUDE_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv"}
# Excluded when the target is not a git repo (git's ignore rules do this job otherwise).
NON_GIT_EXCLUDE_DIRS = {"out", "dist", "build", ".mypy_cache", ".tox"}
_LOCK = threading.RLock()  # run_batch runs cases in threads; serialise shared-file writes
TEXT_EXTS = {".md", ".txt", ".py", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".html", ".htm",
             ".css", ".js", ".ts", ".mjs", ".sh", ".ps1", ".bat", ".csv", ".tsv", ".xml", ".sql", ".r", ".tmpl"}


# --------------------------------------------------------------------------- io

def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(s: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(s)


def read_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    with p.open(encoding="utf-8-sig") as f:  # tolerate a BOM (PowerShell writes one)
        return json.load(f)


def write_json(path, data) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    with _LOCK:
        os.replace(tmp, p)


def append_jsonl(path, record) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, p.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path) -> list:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def emit(data, code: int = 0):
    """Print JSON to stdout (always UTF-8: a redirected Windows console defaults to cp1252) and exit."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.stdout.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    sys.exit(code)


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def is_text_file(path) -> bool:
    p = Path(path)
    if p.suffix.lower() in TEXT_EXTS or p.name in {"SKILL.md", "LICENSE", "Makefile", "Dockerfile"}:
        return True
    try:
        with open(p, "rb") as f:
            chunk = f.read(4096)
        if b"\0" in chunk:
            return False
        chunk.decode("utf-8")
        return True
    except (OSError, UnicodeDecodeError):
        return False


def read_text(path) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------- skill files

def parse_frontmatter(text: str):
    """Return (meta: dict, body: str). Handles simple `key: value` and `>`/`|` blocks."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    meta, key, block, buf = {}, None, None, []

    def flush():
        if key is not None and block is not None:
            joiner = " " if block == ">" else "\n"
            meta[key] = joiner.join(s.strip() for s in buf).strip()

    for raw in lines[1:end]:
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", raw)
        if m and not raw.startswith((" ", "\t")):
            flush()
            key, val = m.group(1), m.group(2).strip()
            block, buf = None, []
            if val in (">", "|", ">-", "|-"):
                block = val[0]
            else:
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                meta[key] = val
        elif block is not None:
            buf.append(raw)
    flush()
    body = "\n".join(lines[end + 1:])
    return meta, body


def skill_stats(skill_dir) -> dict:
    p = Path(skill_dir) / "SKILL.md"
    text = read_text(p)
    meta, body = parse_frontmatter(text)
    return {
        "name": meta.get("name"),
        "description": meta.get("description", ""),
        "description_chars": len(meta.get("description", "")),
        "body_lines": len(body.strip("\n").splitlines()),
        "has_frontmatter": bool(meta),
    }


# --------------------------------------------------------------------------- file sets and copies

def _git(args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def git_root(path):
    p = Path(path).resolve()
    if p.is_file():
        p = p.parent
    try:
        r = _git(["rev-parse", "--show-toplevel"], p)
    except FileNotFoundError:
        return None
    if r.returncode != 0:
        return None
    return Path(r.stdout.strip()).resolve()


def git_info(repo) -> dict:
    if git_root(repo) is None:
        return {"is_repo": False}
    head = _git(["rev-parse", "--short", "HEAD"], repo)
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
    status = _git(["status", "--porcelain"], repo)
    return {
        "is_repo": True,
        "head": head.stdout.strip() if head.returncode == 0 else None,
        "branch": branch.stdout.strip() if branch.returncode == 0 else None,
        "dirty": [l for l in status.stdout.splitlines() if l.strip()],
    }


def _excluded(rel: str, extra_globs) -> bool:
    parts = rel.replace("\\", "/").split("/")
    if any(part in ALWAYS_EXCLUDE_DIRS for part in parts):
        return True
    return any(fnmatch.fnmatch(rel, g) for g in extra_globs or [])


def list_project_files(root, extra_exclude_globs=None) -> list:
    """Relative POSIX paths of the files that make up the project.

    In a git repo: tracked plus untracked-but-not-ignored files (what a reviewer would see).
    Otherwise: a walk that skips caches and usual build/output folders.
    """
    root = Path(root).resolve()
    files = []
    groot = git_root(root)
    if groot is not None:
        r = _git(["ls-files", "-co", "--exclude-standard", "-z"], root)
        if r.returncode == 0:
            for rel in r.stdout.split("\0"):
                if rel and (root / rel).is_file():
                    files.append(rel.replace("\\", "/"))
    if groot is None or not files:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in ALWAYS_EXCLUDE_DIRS | NON_GIT_EXCLUDE_DIRS]
            for fn in filenames:
                rel = os.path.relpath(os.path.join(dirpath, fn), root).replace("\\", "/")
                files.append(rel)
    return sorted(f for f in set(files) if not _excluded(f, extra_exclude_globs) and not f.endswith(".pyc"))


def copy_files(src_root, dst_root, rel_files) -> int:
    src_root, dst_root = Path(src_root), Path(dst_root)
    n = 0
    for rel in rel_files:
        s = src_root / rel
        d = dst_root / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
        n += 1
    return n


def copy_tree(src, dst, ignore_dirs=None) -> None:
    ignore = set(ALWAYS_EXCLUDE_DIRS) | set(ignore_dirs or [])
    shutil.copytree(src, dst, dirs_exist_ok=True,
                    ignore=lambda d, names: [n for n in names if n in ignore or n.endswith(".pyc")])


def tree_files(root) -> list:
    root = Path(root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ALWAYS_EXCLUDE_DIRS]
        for fn in filenames:
            if fn.endswith(".pyc"):
                continue
            out.append(os.path.relpath(os.path.join(dirpath, fn), root).replace("\\", "/"))
    return sorted(out)


def manifest(root, rel_files=None) -> dict:
    root = Path(root)
    rel_files = rel_files if rel_files is not None else tree_files(root)
    return {rel: sha256_file(root / rel) for rel in rel_files}


def diff_trees(old_root, new_root, context: int = 3) -> dict:
    """Compare two project copies. Returns changed/added/deleted lists and a unified patch."""
    old_m, new_m = manifest(old_root), manifest(new_root)
    added = sorted(set(new_m) - set(old_m))
    deleted = sorted(set(old_m) - set(new_m))
    changed = sorted(k for k in set(old_m) & set(new_m) if old_m[k] != new_m[k])
    chunks, added_lines = [], {}
    for rel in changed + added + deleted:
        o, n = Path(old_root) / rel, Path(new_root) / rel
        o_text = read_text(o) if o.exists() and is_text_file(o) else None
        n_text = read_text(n) if n.exists() and is_text_file(n) else None
        if (o.exists() and o_text is None) or (n.exists() and n_text is None):
            chunks.append(f"Binary file {rel} differs\n")
            continue
        a = (o_text or "").splitlines(keepends=True)
        b = (n_text or "").splitlines(keepends=True)
        diff = list(difflib.unified_diff(a, b, fromfile=f"a/{rel}" if o.exists() else "/dev/null",
                                         tofile=f"b/{rel}" if n.exists() else "/dev/null", n=context))
        if diff:
            if not diff[-1].endswith("\n"):
                diff[-1] += "\n"
            chunks.append(f"diff --git a/{rel} b/{rel}\n" + "".join(
                l if l.endswith("\n") else l + "\n\\ No newline at end of file\n" for l in diff))
        added_lines[rel] = [l[1:].rstrip("\n") for l in diff if l.startswith("+") and not l.startswith("+++")]
    stat_lines = sum(len(v) for v in added_lines.values())
    return {"changed": changed, "added": added, "deleted": deleted, "patch": "".join(chunks),
            "added_lines": added_lines, "added_line_count": stat_lines}


# --------------------------------------------------------------------------- run folder

class Run:
    """Accessor for a run folder: <workspace>/runs/<run-id>/."""

    def __init__(self, run_dir):
        self.dir = Path(run_dir).resolve()
        if not (self.dir / "config.json").exists():
            raise SystemExit(f"not a run folder (no config.json): {self.dir}")

    @property
    def config(self) -> dict:
        return read_json(self.dir / "config.json")

    @property
    def state(self) -> dict:
        return read_json(self.dir / "state.json", {})

    def save_state(self, state) -> None:
        state["updated_at"] = now_iso()
        write_json(self.dir / "state.json", state)

    @property
    def goal(self) -> dict:
        return read_json(self.dir / "goal.json", {}) or {}

    @property
    def history(self) -> dict:
        return read_json(self.dir / "history.json", {"iterations": [], "attempts": {}, "do_not_repeat": []})

    def save_history(self, h) -> None:
        write_json(self.dir / "history.json", h)

    @property
    def workspace(self) -> Path:
        return self.dir.parent.parent

    def candidate(self, cid: str) -> Path:
        return self.dir / "candidates" / cid

    def candidate_meta(self, cid: str) -> dict:
        return read_json(self.dir / "candidates" / f"{cid}.meta.json", {}) or {}

    def save_candidate_meta(self, cid: str, meta: dict) -> None:
        write_json(self.dir / "candidates" / f"{cid}.meta.json", meta)

    def iteration_dir(self, n: int) -> Path:
        return self.dir / "iterations" / f"{int(n):03d}"

    def skill_dir_in(self, root) -> Path:
        return Path(root) / self.config["target"]["skill_rel"]


def severity_rank(sev: str) -> int:
    try:
        return SEVERITIES.index((sev or "major").lower())
    except ValueError:
        return SEVERITIES.index("major")


def find_claude_cmd(explicit=None) -> list:
    """The command that starts Claude Code. --claude-cmd / RSI_CLAUDE_CMD may be a JSON list."""
    raw = explicit or os.environ.get("RSI_CLAUDE_CMD")
    if raw:
        raw = raw.strip()
        if raw.startswith("["):
            return json.loads(raw)
        return [raw]
    exe = shutil.which("claude")
    if not exe:
        raise SystemExit("claude CLI not found on PATH (set RSI_CLAUDE_CMD or --claude-cmd)")
    return [exe]
