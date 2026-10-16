"""Lightweight JS/JSX analysis: masking, imports, top-level decls, url templates."""
import bisect
import os
import re
from common import read_text, LineIndex, pair_brackets

_BLANK = re.compile(r'[^\n]')
SPEC = re.compile(r"//|/\*|['\"`]")
SPEC_EXPR = re.compile(r"//|/\*|['\"`{}]")
TPL = re.compile(r"\\.|`|\$\{", re.S)
STR = {"'": re.compile(r"'(?:\\.|[^'\\\n])*'"), '"': re.compile(r'"(?:\\.|[^"\\\n])*"')}


def mask_js(s):
    """Blank comments and string contents (keeps quotes, newlines, ${expr} code). Same length."""
    pieces = []
    n = len(s)

    def bl(a, b):
        pieces.append(_BLANK.sub(' ', s[a:b]))

    def template(i):
        seg = i
        while True:
            m = TPL.search(s, i)
            if not m:
                bl(seg, n)
                return n
            t = m.group()
            if t == '`':
                bl(seg, m.start())
                pieces.append('`')
                return m.end()
            if t == '${':
                bl(seg, m.start())
                pieces.append('${')
                e = code(m.end(), True)
                if e >= n:
                    return n
                pieces.append('}')
                i = seg = e + 1
                continue
            i = m.end()

    def code(i, in_expr):
        depth = 0
        last = i
        rx = SPEC_EXPR if in_expr else SPEC
        while True:
            m = rx.search(s, i)
            if not m:
                pieces.append(s[last:])
                return n
            i = m.start()
            t = m.group()
            if t == '{':
                depth += 1
                i += 1
            elif t == '}':
                if depth == 0:
                    pieces.append(s[last:i])
                    return i
                depth -= 1
                i += 1
            elif t == '//':
                pieces.append(s[last:i])
                j = s.find('\n', i)
                j = n if j < 0 else j
                bl(i, j)
                i = last = j
            elif t == '/*':
                pieces.append(s[last:i])
                j = s.find('*/', i + 2)
                j = n if j < 0 else j + 2
                bl(i, j)
                i = last = j
            elif t in ("'", '"'):
                m2 = STR[t].match(s, i)
                if m2:
                    pieces.append(s[last:i + 1])
                    bl(i + 1, m2.end() - 1)
                    last = m2.end() - 1
                    i = m2.end()
                else:
                    i += 1
            else:  # backtick
                pieces.append(s[last:i + 1])
                i = last = template(i + 1)

    code(0, False)
    out = ''.join(pieces)
    return out if len(out) == n else out[:n].ljust(n)


IDENT = re.compile(r'[A-Za-z_$][\w$]*')
DECL_RE = re.compile(r'^([ \t]*)(export\s+)?(default\s+)?(?:async\s+)?(const|let|var|function\s*\*?|class)\s*([A-Za-z_$][\w$]*)', re.M)
DEFAULT_RE = re.compile(r'^([ \t]*)export\s+default\s+(?!(?:async\s+)?function\b|class\b)', re.M)
IMPORT_RE = re.compile(r'\bimport\s+([^;\'"`]*?)\s*from\s*([\'"])')


class Decl:
    __slots__ = ('name', 'start', 'end', 'exported', 'calls')

    def __init__(self, name, start, end, exported):
        self.name, self.start, self.end, self.exported = name, start, end, exported
        self.calls = []


class JsFile:
    def __init__(self, path, root):
        self.path = path
        self.root = root
        self.rel = os.path.relpath(path, root).replace('\\', '/')
        self.text = read_text(path)
        self.masked = mask_js(self.text)
        self.li = LineIndex(self.text)
        self.pair = None
        self.imports = {}      # local -> (raw_src, imported_name)  imported_name: 'default' | '*' | name
        self.import_spans = []
        self.dyn_imports = []
        self.decls = {}
        self._idents = {}
        self._parse_imports()
        self._parse_decls()

    def _parse_imports(self):
        t, m = self.text, self.masked
        for mm in IMPORT_RE.finditer(m):
            q = mm.end() - 1
            e = t.find(t[q], q + 1)
            if e < 0:
                continue
            self.import_spans.append((mm.start(), e + 1))
            self._parse_clause(mm.group(1).strip(), t[q + 1:e])
        for mm in re.finditer(r'\bimport\s*\(\s*([\'"])', m):
            q = mm.end() - 1
            e = t.find(t[q], q + 1)
            if e > 0:
                self.dyn_imports.append(t[q + 1:e])

    def _parse_clause(self, clause, src):
        clause = re.sub(r'\s+', ' ', clause)
        mb = re.search(r'\{(.*?)\}', clause)
        if mb:
            for part in mb.group(1).split(','):
                part = part.strip()
                if not part:
                    continue
                mm = re.match(r'(\w+)\s+as\s+(\w+)$', part)
                if mm:
                    self.imports[mm.group(2)] = (src, mm.group(1))
                elif re.match(r'\w+$', part):
                    self.imports[part] = (src, part)
            clause = clause.replace(mb.group(0), '')
        mns = re.search(r'\*\s*as\s+(\w+)', clause)
        if mns:
            self.imports[mns.group(1)] = (src, '*')
            clause = clause.replace(mns.group(0), '')
        for part in clause.split(','):
            part = part.strip()
            if re.match(r'\w+$', part):
                self.imports[part] = (src, 'default')

    def brackets(self):
        if self.pair is None:
            self.pair = pair_brackets(self.masked)
            ev = [m.start() for m in re.finditer(r'[()\[\]{}]', self.masked)]
            self._ev = ev
            depth, ds = 0, []
            for p in ev:
                if self.masked[p] in '([{':
                    depth += 1
                else:
                    depth -= 1
                ds.append(depth)
            self._ds = ds
        return self.pair

    def depth_at(self, pos):
        self.brackets()
        k = bisect.bisect_left(self._ev, pos)
        return 0 if k == 0 else self._ds[k - 1]

    def _parse_decls(self):
        m = self.masked
        starts = []
        for mm in DECL_RE.finditer(m):
            p = mm.start() + len(mm.group(1))
            if self.depth_at(p) != 0:
                continue
            starts.append((p, mm.group(5), bool(mm.group(2)), bool(mm.group(3))))
        for mm in DEFAULT_RE.finditer(m):
            p = mm.start() + len(mm.group(1))
            if self.depth_at(p) == 0:
                starts.append((p, 'default', True, True))
        starts.sort()
        for k, (p, name, exp, dflt) in enumerate(starts):
            end = starts[k + 1][0] if k + 1 < len(starts) else len(m)
            if name not in self.decls:
                self.decls[name] = Decl(name, p, end, exp)
            if dflt and name != 'default':
                self.decls.setdefault('default', Decl('default', p, end, True))
        self._by_start = sorted(self.decls.values(), key=lambda x: x.start)
        self._starts = [d.start for d in self._by_start]

    def decl_at(self, pos):
        k = bisect.bisect_right(self._starts, pos) - 1
        # walk back to find a decl that contains pos (aliases share spans)
        while k >= 0:
            d = self._by_start[k]
            if pos < d.end:
                return d
            if d.name != 'default':
                break
            k -= 1
        return None

    def idents(self, decl=None):
        key = decl.name if decl else None
        if key not in self._idents:
            if decl is None:
                seg = self.masked
                for a, b in self.import_spans:
                    seg = seg[:a] + _BLANK.sub(' ', seg[a:b]) + seg[b:]
            else:
                seg = self.masked[decl.start:decl.end]
            self._idents[key] = (set(IDENT.findall(seg)), seg)
        return self._idents[key]

    def line(self, pos):
        return self.li.line(pos)

    def arg_spans(self, open_pos):
        pr = self.brackets()
        close = pr.get(open_pos)
        if close is None:
            return []
        args, s, i, m = [], open_pos + 1, open_pos + 1, self.masked
        while i < close:
            c = m[i]
            if c in '([{':
                j = pr.get(i)
                i = (j if j else i) + 1
                continue
            if c == ',':
                args.append((s, i))
                s = i + 1
            i += 1
        if m[s:close].strip():
            args.append((s, close))
        return args


def _tpl(s):
    res, i, n = [], 0, len(s)
    while i < n:
        if s.startswith('${', i):
            d, j = 1, i + 2
            while j < n and d:
                if s[j] == '{':
                    d += 1
                elif s[j] == '}':
                    d -= 1
                j += 1
            res.append('{' + re.sub(r'\s+', '', s[i + 2:j - 1])[:40] + '}')
            i = j
        else:
            res.append(s[i])
            i += 1
    return ''.join(res)


def url_template(jf, a, b, consts=None):
    """Convert expression jf.text[a:b] into a url template with {param} placeholders."""
    t, m = jf.text[a:b], jf.masked[a:b]
    parts, depth, s = [], 0, 0
    for i, c in enumerate(m):
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
        elif c == '+' and depth == 0:
            parts.append(t[s:i])
            s = i + 1
    parts.append(t[s:])
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) >= 2 and p[0] in '\'"' and p[-1] == p[0]:
            out.append(p[1:-1])
        elif len(p) >= 2 and p[0] == '`' and p[-1] == '`':
            out.append(_tpl(p[1:-1]))
        elif consts and p in consts:
            out.append(consts[p])
        else:
            out.append('{' + re.sub(r'\s+', '', p)[:40] + '}')
    return ''.join(out)


def file_consts(jf):
    consts = {}
    for mm in re.finditer(r'\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*([\'"`])', jf.masked):
        q = mm.end() - 1
        qc = jf.text[q]
        e = jf.text.find(qc, q + 1)
        if e > 0 and (qc == '`' or '\n' not in jf.text[q:e]):
            v = jf.text[q + 1:e]
            consts[mm.group(1)] = _tpl(v) if qc == '`' else v
    return consts
