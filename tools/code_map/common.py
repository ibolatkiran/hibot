"""Shared helpers: safe file walking (security exclusions), line index, bracket pairing."""
import bisect
import fnmatch
import os
import re

SKIP_DIRS = {'node_modules', 'build', 'dist', 'bin', 'obj', '.git', 'git', '.vs', '.vscode',
             '.idea', 'coverage', '__pycache__', '.wt'}
# Files that may hold secrets: NEVER opened, NEVER listed.
SECRET_PATTERNS = ['appsettings*.json', '.env*', '.env-cmdrc.json', 'translate.js', 'web.config',
                   '*.pfx', '*.pem', '*.key', '*.p12', '*.snk', 'secrets*.json', '*.publishsettings',
                   '*.user', 'launchSettings.json']


def is_secret_file(name):
    low = name.lower()
    return any(fnmatch.fnmatch(low, p.lower()) for p in SECRET_PATTERNS)


def walk(root, exts, skip_dirs=None, extra_skip_dirs=()):
    """Yield absolute paths of files with given extensions, skipping noise dirs and secret files."""
    sd = set(SKIP_DIRS) | set(extra_skip_dirs)
    if skip_dirs:
        sd |= set(skip_dirs)
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in sd)
        for fn in sorted(fns):
            if is_secret_file(fn):
                continue
            if fn.lower().endswith(tuple(exts)):
                yield os.path.join(dp, fn)


def read_text(path):
    if is_secret_file(os.path.basename(path)):
        raise PermissionError('refusing to read secret-bearing file: ' + os.path.basename(path))
    with open(path, 'rb') as f:
        data = f.read()
    for enc in ('utf-8-sig', 'cp1254', 'latin-1'):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', 'replace')


class LineIndex:
    def __init__(self, text):
        self.starts = [0] + [m.end() for m in re.finditer(r'\n', text)]

    def line(self, pos):
        return bisect.bisect_right(self.starts, pos)


def pair_brackets(masked):
    """Return (pair dict open->close, close->open) for (), [], {} on masked text."""
    pair = {}
    stack = []
    closer = {')': '(', ']': '[', '}': '{'}
    for m in re.finditer(r'[()\[\]{}]', masked):
        c = m.group()
        p = m.start()
        if c in '([{':
            stack.append((c, p))
        else:
            # pop until matching type (tolerate imbalance)
            for k in range(len(stack) - 1, -1, -1):
                if stack[k][0] == closer[c]:
                    op = stack[k][1]
                    del stack[k:]
                    pair[op] = p
                    break
    return pair


def rel(path, root):
    return os.path.relpath(path, root).replace(os.sep, '/')
