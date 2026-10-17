"""Read-only git history tools (log, blame) on the allowed repositories.  No emails, authors reduced to initials."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from hibot.agent import config, safety

from ._common import ToolError, err, finish, scrub, select_roots, to_int

GIT_TIMEOUT = 30
_SINCE_RE = re.compile(r"^[A-Za-z0-9 :.,\-/+]{1,40}$")


def _initials(name: str) -> str:
    parts = re.findall(r"[^\W\d_]+", name or "", re.U)
    return "".join(p[0].upper() for p in parts[:3]) or "?"


def _repo(repo: str) -> Path:
    if not isinstance(repo, str) or not repo.strip():
        raise ToolError("repo must be a repo folder name such as SiriusApi")
    roots = select_roots([repo])
    if len(roots) != 1:
        raise ToolError("repo %r is ambiguous: %s" % (repo, ", ".join(r.name for r in roots)))
    root = roots[0]
    if not (root / ".git").exists():
        raise ToolError("%s has no git history on this machine (sqlproj folders are not git clones)" % root.name)
    return root


def _check_path(path: str) -> str:
    """Relative path inside a repo; never '..', never a denied dir or a secret file name."""
    p = path.strip().replace("\\", "/")
    parts = [x for x in p.split("/") if x]
    if not parts or len(p) > 300 or ".." in parts or os.path.isabs(p) or re.match(r"^[A-Za-z]:", p) or "\x00" in p:
        raise ToolError("path must be repo-relative without '..'")
    if p.startswith("-"):
        raise ToolError("invalid path")
    if any(x.casefold() in config.DENIED_DIRS for x in parts[:-1]):
        raise ToolError("path inside a denied directory")
    if config.SECRET_NAME_RE.match(parts[-1]):
        raise ToolError("secret file names are never queried")
    return "/".join(parts)


def _git(root: Path, args: list) -> str:
    cmd = ["git", "--no-pager", "-c", "core.quotepath=false", "-c", "core.pager=cat", "-C", str(root)] + args
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=GIT_TIMEOUT, env=env)
    except FileNotFoundError:
        raise ToolError("git executable not found")
    except subprocess.TimeoutExpired:
        raise ToolError("git timed out")
    if p.returncode != 0:
        raise ToolError("git failed: %s" % p.stderr.decode("utf-8", "replace").strip()[:200])
    return p.stdout.decode("utf-8", "replace")


def git_log(repo: str, path: Optional[str] = None, since: Optional[str] = None, grep: Optional[str] = None,
            max_count: int = 30) -> dict:
    """Commit history: sha, date, author initials, subject.  Optional path, since ('2 weeks ago', '2026-09-01'), grep on message."""
    try:
        root = _repo(repo)
        n = to_int(max_count, "max_count", 1, 100, 30)
        args = ["log", "-n", str(n), "--date=short", "--no-color", "--no-ext-diff",
                "--format=%h%x1f%ad%x1f%an%x1f%s"]
        if since:
            if not isinstance(since, str) or not _SINCE_RE.match(since):
                raise ToolError("since must look like '2026-09-01' or '3 weeks ago'")
            args.append("--since=" + since)
        if grep:
            if not isinstance(grep, str) or len(grep) > 100 or "\x00" in grep:
                raise ToolError("grep must be a short string")
            args += ["-i", "--fixed-strings", "--grep=" + grep]
        if path:
            args += ["--", _check_path(path)]
        out = _git(root, args)
    except ToolError as e:
        return err(e)
    commits = []
    for ln in out.splitlines():
        f = ln.split("\x1f")
        if len(f) == 4:
            commits.append({"sha": f[0], "date": f[1], "author": _initials(f[2]), "subject": scrub(f[3])[:160]})
    return finish({"ok": True, "repo": root.name, "path": path, "count": len(commits), "commits": commits})


def git_blame(repo: str, path: str, start_line: int, end_line: Optional[int] = None) -> dict:
    """Who/when/which commit last changed each line (max 80 lines)."""
    try:
        root = _repo(repo)
        rel = _check_path(path)
        full = safety.resolve("%s/%s" % (root.name, rel))          # existence, suffix, secret and allowed-root rules
        s = to_int(start_line, "start_line", 1, 10_000_000)
        e = to_int(end_line, "end_line", 1, 10_000_000, s + 39)
        if e < s:
            raise ToolError("end_line must be >= start_line")
        e = min(e, s + 79)
        out = _git(root, ["blame", "--line-porcelain", "-w", "-L", "%d,%d" % (s, e), "--",
                          os.path.relpath(str(full), os.path.realpath(str(root))).replace("\\", "/")])
    except (ToolError, safety.SourceError) as ex:
        return err(ex)
    lines, cur, subjects = [], None, {}
    for ln in out.splitlines():
        if ln.startswith("\t"):
            if cur:
                cur["text"] = scrub(ln[1:].strip())[:160]
                lines.append(cur)
            cur = None
            continue
        m = re.match(r"^([0-9a-f]{40}) \d+ (\d+)", ln)
        if m:
            cur = {"line": int(m.group(2)), "sha": m.group(1)[:8]}
            continue
        if cur is None:
            continue
        if ln.startswith("author "):
            cur["author"] = _initials(ln[7:])
        elif ln.startswith("author-time "):
            import datetime as dt
            cur["date"] = dt.datetime.fromtimestamp(int(ln[12:]), dt.timezone.utc).strftime("%Y-%m-%d")
        elif ln.startswith("summary "):
            cur["summary"] = scrub(ln[8:])[:120]
    return finish({"ok": True, "repo": root.name, "path": rel, "lines": lines})
