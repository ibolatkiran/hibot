"""code_search (ripgrep or Python fallback) and read_file (delegates to hibot.agent.safety.read_source)."""
from __future__ import annotations

import fnmatch
import itertools
import json
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional

from hibot.agent import config, safety

from ._common import ToolError, err, finish, scrub, select_roots, to_int

EXTRA_EXCLUDED_DIRS = {"build", "dist", "git", "coverage", ".next", ".idea", ".vscode", ".nuget", "packages"}
SKIP_FILE_RE = re.compile(r"(?i)(\.min\.(js|css)|\.map|package-lock\.json|yarn\.lock)$")
RG_EXCLUDE_FILE_GLOBS = ("appsettings*.json", ".env*", ".env-cmdrc.json", "translate.js", "web.config", "*.pfx",
                         "*.pem", "*.key", "*.p12", "*.snk", "secret.json", "secrets.json", "id_rsa*",
                         "*.publishsettings", "*.user", "*.min.js", "*.min.css", "*.map", "package-lock.json",
                         "yarn.lock")
MAX_FILE_BYTES = 1_500_000
MAX_LINE_CHARS = 220
SEARCH_SECONDS = 40
READ_MAX_LINES = 250
READ_MAX_CHARS = 16000


def _excluded_dirs() -> set:
    return {d.casefold() for d in config.DENIED_DIRS} | EXTRA_EXCLUDED_DIRS


def file_allowed(name: str) -> bool:
    """Deny-list for one file NAME (secrets, noise, unreadable types)."""
    if config.SECRET_NAME_RE.match(name) or SKIP_FILE_RE.search(name):
        return False
    return Path(name).suffix.lower() in config.READABLE_SUFFIXES


def rel_allowed(rel: str) -> bool:
    parts = re.split(r"[\\/]+", rel)
    ex = _excluded_dirs()
    return all(p.casefold() not in ex for p in parts[:-1]) and file_allowed(parts[-1])


def iter_files(roots: List[Path]):
    ex = _excluded_dirs()
    for root in roots:
        for dp, dns, fns in os.walk(str(root), followlinks=False):
            dns[:] = sorted(d for d in dns if d.casefold() not in ex)
            for fn in sorted(fns):
                if not file_allowed(fn):
                    continue
                full = os.path.join(dp, fn)
                rel = os.path.relpath(full, str(root)).replace("\\", "/")
                yield root, full, rel


def _glob_ok(glob: Optional[str], rel: str) -> bool:
    if not glob:
        return True
    g = glob.replace("\\", "/")
    return fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(rel, "*/" + g) or fnmatch.fnmatch(rel.rsplit("/", 1)[-1], g)


def _hit(root: Path, rel: str, line: int, text: str) -> dict:
    t = scrub(text.strip())
    if len(t) > MAX_LINE_CHARS:
        t = t[:MAX_LINE_CHARS] + "..."
    return {"file": "%s/%s" % (root.name, rel), "line": line, "text": t}


def _ctx(lines, j):
    return "%d| %s" % (j + 1, scrub(lines[j].strip())[:MAX_LINE_CHARS])


def _load(item):
    root, full, rel = item
    try:
        if os.path.getsize(full) > MAX_FILE_BYTES:
            return item, None
        with open(full, "rb") as f:
            raw = f.read()
    except OSError:
        return item, None
    if b"\x00" in raw[:2000]:
        return item, None
    return item, raw.decode("utf-8", errors="replace")


def _python_search(rx, roots, glob, max_results, context, deadline):
    """Threaded (file reads are I/O bound, esp. on Windows with AV scanning), results in deterministic path order."""
    hits, files_scanned, timed_out = [], 0, False
    items = (it for it in iter_files(roots) if _glob_ok(glob, it[2]))
    with ThreadPoolExecutor(max_workers=12) as pool:
        while True:
            batch = list(itertools.islice(items, 96))
            if not batch:
                break
            if time.time() > deadline:
                timed_out = True
                break
            for (root, full, rel), text in pool.map(_load, batch):
                if text is None:
                    continue
                files_scanned += 1
                if not rx.search(text):
                    continue
                lines = text.splitlines()
                for i, ln in enumerate(lines):
                    if rx.search(ln):
                        h = _hit(root, rel, i + 1, ln)
                        if context:
                            h["context"] = [_ctx(lines, j) for j in range(max(0, i - context),
                                                                           min(len(lines), i + context + 1)) if j != i]
                        hits.append(h)
                        if len(hits) >= max_results:
                            return hits, files_scanned, timed_out, True
    return hits, files_scanned, timed_out, False


def rg_path() -> Optional[str]:
    return shutil.which("rg")


def parse_rg_json(stream, roots, glob, max_results):
    """Parse `rg --json` lines.  Every path is re-checked against the deny-list (defence in depth)."""
    hits = []
    for raw in stream:
        try:
            m = json.loads(raw)
        except ValueError:
            continue
        if m.get("type") != "match":
            continue
        d = m["data"]
        p = (d.get("path") or {}).get("text")
        text = (d.get("lines") or {}).get("text")
        if not p or text is None:
            continue
        pn = os.path.normcase(os.path.realpath(p))
        root = next((r for r in roots if pn.startswith(os.path.normcase(os.path.realpath(str(r))) + os.sep)), None)
        if root is None:
            continue
        rel = os.path.relpath(p, str(root)).replace("\\", "/") if os.path.isabs(p) else p.replace("\\", "/")
        if not rel_allowed(rel) or not _glob_ok(glob, rel):
            continue
        hits.append(_hit(root, rel, d.get("line_number") or 0, text.rstrip("\r\n")))
        if len(hits) >= max_results:
            break
    return hits


def _rg_search(pattern, literal, roots, glob, max_results):
    exe = rg_path()
    if not exe:
        return None
    cmd = [exe, "--json", "--no-config", "--sort", "path", "--max-columns", "400", "--max-filesize", "1500K"]
    if literal:
        cmd.append("-F")
    for d in sorted(_excluded_dirs()):
        cmd += ["-g", "!**/%s/**" % d]
    for g in RG_EXCLUDE_FILE_GLOBS:
        cmd += ["-g", "!" + g]
    for suf in sorted(config.READABLE_SUFFIXES):
        cmd += ["-g", "*" + suf]
    cmd += ["-e", pattern, "--"] + [str(r) for r in roots]
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                             errors="replace")
    except OSError:
        return None
    try:
        return parse_rg_json(p.stdout, roots, glob, max_results)
    finally:
        p.kill()
        p.wait()


def code_search(pattern: str, repos=None, glob: Optional[str] = None, max_results: int = 50, context: int = 0,
                literal: bool = False) -> dict:
    """Search the allowed source repos (regex by default, `literal=True` for plain text)."""
    try:
        if not isinstance(pattern, str) or not pattern.strip():
            raise ToolError("pattern must be a non-empty string")
        if len(pattern) > 300 or "\x00" in pattern:
            raise ToolError("pattern too long (max 300 chars)")
        max_results = to_int(max_results, "max_results", 1, 200, 50)
        context = to_int(context, "context", 0, 5, 0)
        if glob is not None and (not isinstance(glob, str) or len(glob) > 120):
            raise ToolError("glob must be a short string like '*.cs' or 'src/**/*.js'")
        roots = select_roots(repos)
        if not roots:
            return err("no allowed repository folder exists on this machine")
        try:
            rx = re.compile(re.escape(pattern) if literal else pattern, re.M)
        except re.error as e:
            raise ToolError("invalid regex: %s" % e)
    except ToolError as e:
        return err(e)
    engine, hits, scanned, timed_out, capped = "python", None, None, False, False
    if context == 0 and rg_path():
        try:
            hits = _rg_search(pattern, literal, roots, glob, max_results)
        except Exception:
            hits = None
        if hits is not None:
            engine, capped = "rg", len(hits) >= max_results
    if hits is None:
        hits, scanned, timed_out, capped = _python_search(rx, roots, glob, max_results, context,
                                                          time.time() + SEARCH_SECONDS)
    res = {"ok": True, "engine": engine, "pattern": pattern, "count": len(hits), "hits": hits,
           "repos": [r.name for r in roots]}
    if scanned is not None:
        res["files_scanned"] = scanned
    if capped:
        res["note"] = "stopped at max_results=%d; narrow the pattern/glob/repos for more" % max_results
    if timed_out:
        res["note"] = "search time limit reached; narrow repos/glob"
    out = finish(res)
    if out.get("truncated") and "note" not in out:
        out["note"] = "output cut to fit the size cap; narrow the pattern/glob/repos"
    out["count"] = len(out["hits"])
    return out


def read_file(path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> dict:
    """Read a line window (<=250 lines) of a source file via the guarded hibot.agent.safety.read_source."""
    r = safety.read_source(path, start_line, end_line, max_lines=READ_MAX_LINES)
    if not r.get("ok"):
        return r
    text = r["text"]
    if len(text) > READ_MAX_CHARS:
        cut = text[:READ_MAX_CHARS]
        cut = cut[:cut.rfind("\n")] if "\n" in cut else cut
        r["text"] = cut
        r["end_line"] = r["start_line"] + cut.count("\n")
        r["truncated"] = True
    r.setdefault("truncated", False)
    if r["truncated"]:
        r["note"] = "window cut; continue with start_line=%d" % (r["end_line"] + 1)
    return r
