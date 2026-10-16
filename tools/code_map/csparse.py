"""C# structural parser: masking (comments/strings), brace matching, classes, methods, attributes."""
import re
from common import read_text, LineIndex, pair_brackets

CS_TOKEN = re.compile(r'//|/\*|\$@"|@\$"|@"|\$"|"|\'')
CHAR_RE = re.compile(r"'(?:\\[^']+|[^'\\])'")


def mask_cs(s):
    """Return (masked, literals). masked has same length; comments and string/char contents blanked.
    literals: list of (start, end, value, interpolated) where start includes prefix chars ($,@) and end is
    exclusive (after closing quote)."""
    out = []
    lits = []
    n = len(s)
    i = last = 0
    blank = re.compile(r'[^\n]')
    while True:
        m = CS_TOKEN.search(s, i)
        if not m:
            out.append(s[last:])
            break
        i = m.start()
        t = m.group()
        if t == '//':
            out.append(s[last:i])
            j = s.find('\n', i)
            j = n if j < 0 else j
            out.append(' ' * (j - i))
            i = last = j
        elif t == '/*':
            out.append(s[last:i])
            j = s.find('*/', i + 2)
            j = n if j < 0 else j + 2
            out.append(blank.sub(' ', s[i:j]))
            i = last = j
        elif t == "'":
            mc = CHAR_RE.match(s, i)
            if mc:
                out.append(s[last:i + 1])
                out.append(' ' * (mc.end() - i - 2))
                last = mc.end() - 1
                i = mc.end()
            else:
                i += 1
        else:
            verbatim = '@' in t
            interp = '$' in t
            q = i + len(t)           # index after opening quote
            j = q
            if verbatim:
                while True:
                    j = s.find('"', j)
                    if j < 0:
                        j = n
                        break
                    if j + 1 < n and s[j + 1] == '"':
                        j += 2
                        continue
                    break
                val = s[q:j].replace('""', '"')
            else:
                while j < n:
                    c = s[j]
                    if c == '\\':
                        j += 2
                        continue
                    if c == '"' or c == '\n':
                        break
                    j += 1
                val = s[q:j]
            out.append(s[last:q])
            out.append(blank.sub(' ', s[q:j]))
            lits.append((i, min(j + 1, n), val, interp))
            last = j
            i = min(j + 1, n)
    res = ''.join(out)
    if len(res) != n:
        res = res[:n].ljust(n)
    return res, lits


KEYWORDS_NOT_METHOD = {'if', 'for', 'foreach', 'while', 'switch', 'using', 'lock', 'catch', 'fixed', 'else',
                       'return', 'new', 'nameof', 'typeof', 'sizeof', 'checked', 'unchecked', 'when', 'do',
                       'try', 'finally', 'throw', 'await', 'var', 'get', 'set', 'add', 'remove', 'base', 'this'}
TYPE_DECL = re.compile(r'\b(class|interface|struct|enum|record)\s+([A-Za-z_]\w*)')


class CClass:
    def __init__(self, name, kind, start, open_pos, close_pos, header):
        self.name, self.kind = name, kind
        self.start, self.open, self.close = start, open_pos, close_pos
        self.header = header
        self.bases = []
        self.fields = {}      # var name -> type
        self.methods = []
        self.attrs = []       # (name, argtext)
        self.file = None


class CMethod:
    def __init__(self):
        self.cls = None
        self.name = None
        self.sig_pos = 0
        self.hdr_start = 0
        self.body_start = 0
        self.body_end = 0
        self.line_start = 0
        self.line_end = 0
        self.attrs = []     # list of (attrname, [string args], line)
        self.params = ''
        self.is_ctor = False


class CFile:
    def __init__(self, path, root):
        import os
        self.path = path
        self.rel = os.path.relpath(path, root).replace(os.sep, '/')
        self.text = read_text(path)
        self.masked, self.lits = mask_cs(self.text)
        self.li = LineIndex(self.text)
        self.pair = pair_brackets(self.masked)
        self.classes = []
        self.methods = []
        self._parse()

    def line(self, pos):
        return self.li.line(pos)

    # ------------------------------------------------------------------
    def _parse(self):
        m = self.masked
        pr = self.pair
        pat = re.compile(r'\b(class|interface|struct|record)\s+([A-Za-z_]\w*)([^;{}]*?)\{')
        for mm in pat.finditer(m):
            op = mm.end() - 1
            if op not in pr:
                continue
            header = mm.group(3)
            if mm.group(1) == 'record' and '(' in header and ')' not in header:
                continue
            c = CClass(mm.group(2), mm.group(1), mm.start(), op, pr[op], header)
            c.file = self
            hdr = header
            # strip where-constraints
            hdr = re.split(r'\bwhere\b', hdr)[0]
            if ':' in hdr:
                bl = hdr.split(':', 1)[1]
                depth, cur, parts = 0, '', []
                for ch in bl:
                    if ch == '<':
                        depth += 1
                    elif ch == '>':
                        depth -= 1
                    if ch == ',' and depth == 0:
                        parts.append(cur)
                        cur = ''
                    else:
                        cur += ch
                parts.append(cur)
                for p in parts:
                    mi = re.match(r'\s*([\w.]+)', p)
                    if mi:
                        c.bases.append(mi.group(1).split('.')[-1])
            self.classes.append(c)
        # attributes before class (Route)
        for c in self.classes:
            pre = m[max(0, c.start - 600):c.start]
            # find attribute blocks directly above: take segment after last '}' or ';'
            k = max(pre.rfind('}'), pre.rfind(';'))
            seg_start = max(0, c.start - 600) + k + 1
            c.attrs = self._attrs(seg_start, c.start)
        for c in self.classes:
            self._members(c)

    def _attrs(self, a, b):
        """attributes in masked[a:b] -> [(name, [string args], line)]"""
        res = []
        m = self.masked
        for mm in re.finditer(r'\[\s*([A-Za-z_][\w.]*)\s*(\()?', m[a:b]):
            pos = a + mm.start()
            # skip array-index-like usage: attributes only at member headers; accept all
            args = []
            if mm.group(2):
                op = a + mm.end() - 1
                cl = self.pair.get(op)
                if cl:
                    for lit in self.lits:
                        if op < lit[0] < cl:
                            args.append(lit[2])
                        elif lit[0] >= cl:
                            break
            res.append((mm.group(1).split('.')[-1], args, self.line(pos)))
        return res

    def _members(self, c):
        m, pr = self.masked, self.pair
        i = c.open + 1
        hs = i
        close = c.close
        nested_spans = [(x.open, x.close) for x in self.classes if x is not c and c.open < x.open < c.close]
        while i < close:
            ch = m[i]
            if ch == '{':
                j = pr.get(i)
                if j is None:
                    i += 1
                    continue
                header = m[hs:i]
                if any(a == i for a, _ in nested_spans):
                    pass
                else:
                    self._classify(c, hs, i, j, header)
                i = j + 1
                hs = i
                continue
            if ch == ';':
                header = m[hs:i]
                if '=>' in header:
                    self._classify(c, hs, i, i, header, expr_body=True)
                else:
                    self._field(c, header)
                hs = i + 1
            elif ch in '([':
                j = pr.get(i)
                if j:
                    i = j
            i += 1

    def _strip_attrs(self, hdr):
        out = []
        depth = 0
        for ch in hdr:
            if ch == '[':
                depth += 1
                out.append(' ')
            elif ch == ']':
                depth -= 1
                out.append(' ')
            else:
                out.append(' ' if depth else ch)
        return ''.join(out)

    def _field(self, c, header):
        h = self._strip_attrs(header)
        h = h.split('=')[0]
        if '(' in h:
            return
        mm = re.search(r'([A-Za-z_][\w.<>,\[\]?\s]*?)\s+([A-Za-z_]\w*)\s*$', h.strip())
        if mm:
            raw_t = re.sub(r'\b(?:private|public|protected|internal|static|readonly|const|virtual|override|sealed|volatile|required|new|unsafe)\b', ' ', mm.group(1))
            typ = re.sub(r'\s+', '', raw_t).split('.')[-1]
            if typ not in ('return', 'var', 'new', 'static', 'readonly', 'private', 'public', 'protected', 'internal'):
                c.fields[mm.group(2)] = re.sub(r'<.*', '', typ)

    def _classify(self, c, hs, open_pos, close_pos, header, expr_body=False):
        h = self._strip_attrs(header)
        if expr_body:
            h = h.split('=>')[0]
        if TYPE_DECL.search(h) and not expr_body:
            return
        # property with accessor block: no '(' before '{' (or initializer)
        pm = re.search(r'([A-Za-z_]\w*)\s*(<[^()]*>)?\s*\(', h)
        if not pm or '=' in h[:pm.start()].replace('==', ''):
            # property: Type Name {  -> record field type
            self._field(c, header)
            return
        name = pm.group(1)
        if name in KEYWORDS_NOT_METHOD:
            return
        before = h[:pm.start()].strip()
        if name != c.name and not before:
            return  # call-like statement, not a declaration
        # verify closing paren followed by allowed tail
        op = hs + (len(header) - len(header.lstrip()))  # approx; find real open paren pos
        pos_open = hs + pm.end() - 1
        cl = self.pair.get(pos_open)
        if cl is None:
            return
        tail = self.masked[cl + 1: open_pos if not expr_body else open_pos].strip()
        if tail and not re.match(r'^(where\b|:\s*(base|this)\b|=>)', tail) and not tail.startswith('where'):
            if expr_body and '=>' in tail:
                pass
            else:
                return
        md = CMethod()
        md.cls = c
        md.name = name
        md.is_ctor = (name == c.name)
        md.sig_pos = hs + pm.start()
        md.hdr_start = hs
        md.body_start = open_pos if not expr_body else (cl + 1)
        md.body_end = close_pos if not expr_body else open_pos
        md.params = self.text[pos_open + 1:cl]
        md.line_start = self.line(md.sig_pos)
        md.line_end = self.line(md.body_end)
        md.attrs = self._attrs(hs, md.sig_pos)
        # record ctor param types -> fields
        if md.is_ctor:
            self._ctor_fields(c, md)
        c.methods.append(md)
        self.methods.append(md)

    def _ctor_fields(self, c, md):
        params = {}
        depth, cur, parts = 0, '', []
        for ch in md.params:
            if ch in '<([':
                depth += 1
            elif ch in '>)]':
                depth -= 1
            if ch == ',' and depth == 0:
                parts.append(cur)
                cur = ''
            else:
                cur += ch
        parts.append(cur)
        for p in parts:
            p = re.sub(r'\[[^\]]*\]', '', p).strip()
            mm = re.match(r'(?:this\s+)?([\w.<>,\[\]?]+)\s+(\w+)(?:\s*=.*)?$', p, re.S)
            if mm:
                typ = re.sub(r'<.*', '', mm.group(1)).split('.')[-1]
                params[mm.group(2)] = typ
        c.fields.update({k: v for k, v in params.items() if k not in c.fields})
        body = self.masked[md.body_start:md.body_end]
        for mm in re.finditer(r'\b(?:this\.)?(_?\w+)\s*=\s*(\w+)\s*;', body):
            if mm.group(2) in params:
                c.fields[mm.group(1)] = params[mm.group(2)]
