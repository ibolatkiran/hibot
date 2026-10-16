"""Lightweight, regex based T-SQL (SSDT project) parser used by build.py.

Python 3 standard library only.  Not a real T-SQL parser: see knowledge/schema/README.md
for the known limitations.
"""
import os
import re
import hashlib

# ----------------------------------------------------------------------------
# File reading / text cleaning
# ----------------------------------------------------------------------------


def read_sql(path):
    with open(path, 'rb') as fh:
        raw = fh.read()
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw.decode('utf-16', errors='replace')
    if raw[:3] == b'\xef\xbb\xbf':
        return raw[3:].decode('utf-8', errors='replace')
    # BOM-less UTF-16 heuristic: lots of NUL bytes
    if raw[:200].count(b'\x00') > 20:
        enc = 'utf-16-le' if raw[1:2] == b'\x00' else 'utf-16-be'
        return raw.decode(enc, errors='replace')
    return raw.decode('utf-8', errors='replace')


def strip_text(text):
    """Return (nocomment, clean).

    nocomment : comments replaced with spaces (newlines kept), string literals kept.
    clean     : nocomment with the *interior* of string literals blanked.
    Both are the same length as the input so offsets line up.
    """
    n = len(text)
    nc = list(text)
    cl = list(text)
    i = 0
    depth = 0
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ''
        if c == '-' and nxt == '-':
            j = i
            while j < n and text[j] not in '\r\n':
                nc[j] = ' '
                cl[j] = ' '
                j += 1
            i = j
        elif c == '/' and nxt == '*':
            depth = 1
            j = i + 2
            nc[i] = nc[i + 1] = cl[i] = cl[i + 1] = ' '
            while j < n and depth:
                if text[j] == '/' and j + 1 < n and text[j + 1] == '*':
                    depth += 1
                    nc[j] = nc[j + 1] = cl[j] = cl[j + 1] = ' '
                    j += 2
                    continue
                if text[j] == '*' and j + 1 < n and text[j + 1] == '/':
                    depth -= 1
                    nc[j] = nc[j + 1] = cl[j] = cl[j + 1] = ' '
                    j += 2
                    continue
                if text[j] not in '\r\n':
                    nc[j] = ' '
                    cl[j] = ' '
                j += 1
            i = j
        elif c == "'":
            j = i + 1
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        cl[j] = cl[j + 1] = ' '
                        j += 2
                        continue
                    break
                if text[j] not in '\r\n':
                    cl[j] = ' '
                j += 1
            i = j + 1
        elif c == '[':
            j = text.find(']', i)
            i = (j + 1) if j != -1 else n
        elif c == '"':
            j = text.find('"', i + 1)
            i = (j + 1) if j != -1 else n
        else:
            i += 1
    return ''.join(nc), ''.join(cl)


GO_RE = re.compile(r'^[ \t]*GO[ \t]*(?:\d+)?[ \t]*;?[ \t\r]*$', re.I | re.M)


def split_batches(clean, nocomment):
    """Split on GO lines.  Returns list of (clean_batch, nocomment_batch)."""
    out = []
    pos = 0
    for m in GO_RE.finditer(clean):
        out.append((clean[pos:m.start()], nocomment[pos:m.start()]))
        pos = m.end()
    out.append((clean[pos:], nocomment[pos:]))
    return [(c, n) for c, n in out if c.strip()]


def norm_hash(clean):
    s = re.sub(r'\s+', '', clean).lower()
    return hashlib.md5(s.encode('utf-8', 'replace')).hexdigest()


# ----------------------------------------------------------------------------
# Identifier helpers
# ----------------------------------------------------------------------------
IDENT = r'(?:\[[^\]]+\]|"[^"]+"|[A-Za-z_#@][\w$#@]*)'
NAME2 = r'(?:(%s)\s*\.\s*)?(%s)' % (IDENT, IDENT)


def unq(s):
    if s is None:
        return None
    s = s.strip()
    if s.startswith('[') and s.endswith(']'):
        return s[1:-1]
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    return s


def split_top(s, sep=','):
    """Split on sep at paren depth 0 (bracketed identifiers are opaque)."""
    parts, depth, cur, inbr = [], 0, [], False
    for ch in s:
        if inbr:
            if ch == ']':
                inbr = False
        elif ch == '[':
            inbr = True
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if ch == sep and depth == 0 and not inbr:
            parts.append(''.join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append(''.join(cur))
    return parts


def balanced(s, start):
    """s[start] == '('.  Return index just after matching ')' (or len(s))."""
    depth, inbr = 0, False
    for i in range(start, len(s)):
        ch = s[i]
        if inbr:
            if ch == ']':
                inbr = False
        elif ch == '[':
            inbr = True
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return i + 1
    return len(s)


# ----------------------------------------------------------------------------
# Reference extraction
# ----------------------------------------------------------------------------
TOK2 = re.compile(r'(?<![\w@#\]\.\$])(%s)\s*\.\s*(%s)(?:\s*\.\s*(%s))?' % (IDENT, IDENT, IDENT))
UNQ_REF = re.compile(
    r'\b(?:FROM|JOIN|INTO|UPDATE|APPLY|EXEC(?:UTE)?|MERGE|TABLE)\s+(?:@\w+\s*=\s*)?(%s)(?![\w\.\[\(@])' % IDENT, re.I)
CTE_RE = re.compile(r'(?:\bWITH|,)\s*(%s)\s*(?:\([^)]*\))?\s*AS\s*\(' % IDENT, re.I)
EXEC_RE = re.compile(r'\bEXEC(?:UTE)?\s+(?:\(\s*)?(?:@\w+\s*=\s*)?((?:%s\s*\.\s*){0,3}%s)' % (IDENT, IDENT), re.I)
WRITE_RES = [
    re.compile(r'\bINSERT\s+(?:INTO\s+)?((?:%s\s*\.\s*){0,3}%s)' % (IDENT, IDENT), re.I),
    re.compile(r'\bUPDATE\s+(?!STATISTICS)((?:%s\s*\.\s*){0,3}%s)' % (IDENT, IDENT), re.I),
    re.compile(r'\bDELETE\s+(?:FROM\s+)?((?:%s\s*\.\s*){0,3}%s)' % (IDENT, IDENT), re.I),
    re.compile(r'\bMERGE\s+(?:INTO\s+)?((?:%s\s*\.\s*){0,3}%s)' % (IDENT, IDENT), re.I),
    re.compile(r'\bTRUNCATE\s+TABLE\s+((?:%s\s*\.\s*){0,3}%s)' % (IDENT, IDENT), re.I),
]
SKIP_WORDS = {'set', 'top', 'select', 'from', 'where', 'values', 'output', 'with', 'as', 'the', 'case', 'when'}


class Index:
    """Known object index: lower 'schema.name' -> canonical 'Schema.Name'."""

    def __init__(self, keys):
        self.canon = {k.lower(): k for k in keys}

    def lookup(self, schema, name):
        return self.canon.get(('%s.%s' % (schema, name)).lower())


def _name_parts(token):
    parts = re.findall(r'\[[^\]]+\]|"[^"]+"|[^\s\.]+', token)
    return [unq(p) for p in parts]


def _resolve_token(idx, token):
    p = _name_parts(token)
    if len(p) >= 2:
        k = idx.lookup(p[-2], p[-1])
        if k:
            return k
        return None
    if len(p) == 1:
        return idx.lookup('dbo', p[0])
    return None


def extract_refs(clean, idx, self_key=None):
    """Return dict(referenced, written, called)."""
    refs = set()
    ctes = {unq(m.group(1)).lower() for m in CTE_RE.finditer(clean)}
    for m in TOK2.finditer(clean):
        a, b, c = m.group(1), m.group(2), m.group(3)
        k = None
        if c:
            k = idx.lookup(unq(b), unq(c)) or idx.lookup(unq(a), unq(b))
        else:
            k = idx.lookup(unq(a), unq(b))
        if k:
            refs.add(k)
    for m in UNQ_REF.finditer(clean):
        w = unq(m.group(1))
        if w.lower() in ctes or w.lower() in SKIP_WORDS or w.startswith(('#', '@')):
            continue
        k = idx.lookup('dbo', w)
        if k:
            refs.add(k)
    called = set()
    for m in EXEC_RE.finditer(clean):
        k = _resolve_token(idx, m.group(1))
        if k:
            called.add(k)
            refs.add(k)
    # aliases for UPDATE alias / DELETE alias ... FROM table alias
    alias_re = re.compile(r'\b(?:FROM|JOIN)\s+((?:%s\s*\.\s*){0,3}%s)\s+(?:AS\s+)?(%s)(?=\s|$|,|\))' % (IDENT, IDENT, IDENT), re.I)
    aliases = {}
    for m in alias_re.finditer(clean):
        al = unq(m.group(2)).lower()
        if al in ('where', 'on', 'inner', 'left', 'right', 'join', 'cross', 'outer', 'full', 'group', 'order',
                  'union', 'set', 'with', 'having', 'option', 'for', 'when', 'where'):
            continue
        k = _resolve_token(idx, m.group(1))
        if k and al not in aliases:
            aliases[al] = k
    written = set()
    for rx in WRITE_RES:
        for m in rx.finditer(clean):
            tok = m.group(1)
            parts = _name_parts(tok)
            if not parts:
                continue
            if parts[-1].lower() in SKIP_WORDS and len(parts) == 1:
                continue
            k = _resolve_token(idx, tok)
            if not k and len(parts) == 1:
                k = aliases.get(parts[0].lower())
            if k:
                written.add(k)
                refs.add(k)
    if self_key:
        refs.discard(self_key)
        written.discard(self_key)
        called.discard(self_key)
    return {'referenced': sorted(refs), 'written': sorted(written), 'called': sorted(called)}


def dynamic_refs(nocomment, idx, self_key=None):
    """Known objects mentioned inside string literals (dynamic SQL)."""
    found = set()
    for m in re.finditer(r"N?'((?:[^']|'')*)'", nocomment):
        s = m.group(1)
        if len(s) < 15 or '.' not in s:
            continue
        for t in TOK2.finditer(s):
            a, b, c = t.group(1), t.group(2), t.group(3)
            k = (idx.lookup(unq(b), unq(c)) if c else None) or idx.lookup(unq(a), unq(b))
            if k:
                found.add(k)
    if self_key:
        found.discard(self_key)
    return sorted(found)


# ----------------------------------------------------------------------------
# RAISERROR / THROW
# ----------------------------------------------------------------------------
LIT = r"N?'((?:[^']|'')*)'"


def _expr_to_text(expr):
    out = []
    for m in re.finditer(LIT + r'|(@\w+)|([A-Za-z_]\w*(?:\s*\([^()]*\))?)', expr):
        if m.group(1) is not None:
            out.append(m.group(1).replace("''", "'"))
        elif m.group(2):
            out.append('{%s}' % m.group(2))
        elif m.group(3):
            out.append('{%s}' % re.sub(r'\s+', ' ', m.group(3)))
    return ''.join(out).strip()


def extract_raises(nocomment):
    res = []
    for m in re.finditer(r'\bRAISERROR\s*\(', nocomment, re.I):
        end = balanced(nocomment, m.end() - 1)
        args = split_top(nocomment[m.end():end - 1])
        if args:
            t = _expr_to_text(args[0])
            res.append({'kind': 'RAISERROR', 'message': t or args[0].strip(),
                        'severity': args[1].strip() if len(args) > 1 else None})
    for m in re.finditer(r'\bTHROW\s+([\w@]+)\s*,\s*', nocomment, re.I):
        rest = nocomment[m.end():m.end() + 600]
        # message expression ends at ',' at depth 0 followed by state, or ';'
        args = split_top(re.split(r';|\bEND\b|\n\s*\n', rest)[0])
        if args:
            t = _expr_to_text(args[0])
            res.append({'kind': 'THROW', 'number': m.group(1), 'message': t or args[0].strip()})
    return res


# ----------------------------------------------------------------------------
# Type / table parsing
# ----------------------------------------------------------------------------
TYPE_RE = re.compile(r'\s*((?:%s\s*\.\s*)?%s)\s*(\([^)]*\))?' % (IDENT, IDENT))


def parse_type(s):
    m = TYPE_RE.match(s)
    if not m:
        return None, s
    t = unq(m.group(1)).lower() if '.' not in m.group(1) else m.group(1)
    arg = re.sub(r'\s+', '', m.group(2)) if m.group(2) else ''
    return (t + arg).replace(',', ', ') if False else (t + arg), s[m.end():]


CONSTR_START = re.compile(r'^\s*(CONSTRAINT|PRIMARY\s+KEY|UNIQUE|FOREIGN\s+KEY|CHECK|INDEX|PERIOD)\b', re.I)


def parse_cols_list(s):
    cols = []
    for p in split_top(s):
        p = p.strip()
        if not p:
            continue
        m = re.match(r'(%s)' % IDENT, p)
        if m:
            cols.append(unq(m.group(1)))
    return cols


def parse_constraint(item, table_cols_hint=None):
    """Parse a table-level constraint item. Return dict or None."""
    m = re.match(r'\s*(?:CONSTRAINT\s+(%s)\s+)?(.*)$' % IDENT, item, re.I | re.S)
    cname = unq(m.group(1)) if m and m.group(1) else None
    rest = m.group(2) if m else item
    pk = re.match(r'PRIMARY\s+KEY\s*(CLUSTERED|NONCLUSTERED)?\s*\(([^)]*)\)', rest, re.I)
    if pk:
        return {'kind': 'pk', 'name': cname, 'columns': parse_cols_list(pk.group(2))}
    uq = re.match(r'UNIQUE\s*(CLUSTERED|NONCLUSTERED)?\s*\(([^)]*)\)', rest, re.I)
    if uq:
        return {'kind': 'unique', 'name': cname, 'columns': parse_cols_list(uq.group(1 + 1))}
    fk = re.match(r'FOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+((?:%s\s*\.\s*)?%s)\s*(?:\(([^)]*)\))?' % (IDENT, IDENT), rest, re.I)
    if fk:
        return {'kind': 'fk', 'name': cname, 'columns': parse_cols_list(fk.group(1)),
                'ref_table': '.'.join(_name_parts(fk.group(2))[-2:]) if '.' in fk.group(2) else 'dbo.' + unq(fk.group(2)),
                'ref_columns': parse_cols_list(fk.group(3) or '')}
    df = re.match(r'DEFAULT\s*(.*?)\s+FOR\s+(%s)\s*$' % IDENT, rest, re.I | re.S)
    if df:
        return {'kind': 'default', 'name': cname, 'column': unq(df.group(2)), 'value': df.group(1).strip()}
    return None


def parse_column(item):
    m = re.match(r'\s*(%s)\s*(.*)$' % IDENT, item, re.S)
    if not m:
        return None
    name = unq(m.group(1))
    rest = m.group(2)
    if re.match(r'AS\b', rest, re.I):
        return {'name': name, 'sql_type': 'computed', 'nullable': None, 'identity': False,
                'default': None, 'computed': re.sub(r'\s+', ' ', rest[2:].strip())[:200]}
    ty, rest2 = parse_type(rest)
    col = {'name': name, 'sql_type': ty}
    ident = re.search(r'\bIDENTITY\s*(\(\s*\d+\s*,\s*\d+\s*\))?', rest2, re.I)
    col['identity'] = bool(ident)
    if re.search(r'\bNOT\s+NULL\b', rest2, re.I):
        col['nullable'] = False
    elif re.search(r'\bNULL\b', rest2, re.I):
        col['nullable'] = True
    else:
        col['nullable'] = None if ty == 'timestamp' else True
    d = re.search(r'\bDEFAULT\s*(.*?)(?=\s+(?:NOT\s+NULL|NULL|CONSTRAINT|PRIMARY|UNIQUE|REFERENCES|CHECK|IDENTITY|ROWGUIDCOL|SPARSE|MASKED)\b|$)', rest2, re.I | re.S)
    col['default'] = d.group(1).strip() if d else None
    if re.search(r'\bROWVERSION\b', ty or '', re.I) is None and re.search(r'\bPRIMARY\s+KEY\b', rest2, re.I):
        col['inline_pk'] = True
    return col


def parse_create_table(clean, key):
    m = re.search(r'\bCREATE\s+TABLE\s+(%s)\s*\(' % NAME2, clean, re.I)
    if not m:
        return None
    start = m.end() - 1
    end = balanced(clean, start)
    body = clean[start + 1:end - 1]
    cols, pk, uniques, fks, defaults = [], [], [], [], {}
    for item in split_top(body):
        if not item.strip():
            continue
        if CONSTR_START.match(item):
            c = parse_constraint(item)
            if c:
                if c['kind'] == 'pk':
                    pk = c['columns']
                elif c['kind'] == 'unique':
                    uniques.append({'name': c['name'], 'columns': c['columns'], 'unique': True})
                elif c['kind'] == 'fk':
                    fks.append(c)
            continue
        col = parse_column(item)
        if col:
            if col.pop('inline_pk', False):
                pk = [col['name']]
            cols.append(col)
    return {'columns': cols, 'primary_key': pk, 'indexes': uniques, 'foreign_keys': fks}


INDEX_RE = re.compile(
    r'\bCREATE\s+(UNIQUE\s+)?(CLUSTERED\s+|NONCLUSTERED\s+)?(?:COLUMNSTORE\s+)?INDEX\s+(%s)\s+ON\s+((?:%s\s*\.\s*)?%s)\s*(\()?' % (IDENT, IDENT, IDENT), re.I)


def parse_index(batch):
    m = INDEX_RE.search(batch)
    if not m:
        return None
    cols = []
    if m.group(5):
        s = m.end() - 1
        e = balanced(batch, s)
        cols = parse_cols_list(batch[s + 1:e - 1])
        tail = batch[e:]
    else:
        tail = batch[m.end():]
    inc = re.search(r'\bINCLUDE\s*\(([^)]*)\)', tail, re.I)
    flt = re.search(r'\bWHERE\s+(.*?)(?:\bWITH\s*\(|$)', tail, re.I | re.S)
    d = {'name': unq(m.group(3)), 'columns': cols, 'unique': bool(m.group(1)),
         'clustered': bool(m.group(2) and m.group(2).strip().upper() == 'CLUSTERED')}
    if inc:
        d['include'] = parse_cols_list(inc.group(1))
    if flt:
        d['filter'] = re.sub(r'\s+', ' ', flt.group(1).strip())[:120]
    return d, _qualify(m.group(4))


def _qualify(tok):
    p = _name_parts(tok)
    return (p[-2] if len(p) > 1 else 'dbo') + '.' + p[-1]


def parse_trigger(clean, nocomment, idx):
    m = re.search(r'\bCREATE\s+(?:OR\s+ALTER\s+)?TRIGGER\s+((?:%s\s*\.\s*)?%s)\s+ON\s+((?:%s\s*\.\s*)?%s)' % (IDENT, IDENT, IDENT, IDENT), clean, re.I)
    if not m:
        return None
    tname = unq(_name_parts(m.group(1))[-1])
    ev = re.search(r'\b(?:FOR|AFTER|INSTEAD\s+OF)\s+((?:INSERT|UPDATE|DELETE)(?:\s*,\s*(?:INSERT|UPDATE|DELETE))*)', clean[m.end():m.end() + 300], re.I)
    events = [e.strip().upper() for e in ev.group(1).split(',')] if ev else []
    kind = re.search(r'\b(FOR|AFTER|INSTEAD\s+OF)\b', clean[m.end():m.end() + 300], re.I)
    body_start = m.end()
    refs = extract_refs(clean[body_start:], idx, self_key=_qualify(m.group(2)))
    # remove table itself from refs, keep everything else
    t = {'name': tname, 'events': events, 'timing': re.sub(r'\s+', ' ', kind.group(1).upper()) if kind else None,
         'called_objects': refs['referenced'], 'written_tables': refs['written'], 'called_procs': refs['called'],
         'raises_errors': [r['message'] for r in extract_raises(nocomment[body_start:])]}
    return t, _qualify(m.group(2))


# ----------------------------------------------------------------------------
# Routines (procedures / functions / views)
# ----------------------------------------------------------------------------
ROUTINE_RE = re.compile(r'\b(?:CREATE|ALTER)\s+(?:OR\s+ALTER\s+)?(PROC(?:EDURE)?|FUNCTION|VIEW)(?:\s+|(?=\[))((?:%s\s*\.\s*)?%s)' % (IDENT, IDENT), re.I)


def parse_params(ptext, ptext_nc):
    params = []
    for p, pn in zip(split_top(ptext), split_top(ptext_nc)):
        p = p.strip()
        if not p:
            continue
        m = re.match(r'(@\w+)\s*(?:AS\s+)?(.*)$', p, re.S | re.I)
        if not m:
            continue
        name = m.group(1)
        ty, rest = parse_type(m.group(2))
        # default from the comment-stripped (string-preserving) text
        mn = re.match(r'(@\w+)\s*(?:AS\s+)?(.*)$', pn.strip(), re.S | re.I)
        _, restn = parse_type(mn.group(2)) if mn else (None, rest)
        d = re.match(r'\s*=\s*(.*?)\s*(?:\b(?:OUTPUT|OUT|READONLY)\b\s*)*$', restn, re.S | re.I)
        entry = {'name': name, 'sql_type': ty, 'default': re.sub(r'\s+', ' ', d.group(1)).strip() if d else None}
        if re.search(r'\b(OUTPUT|OUT)\b', restn, re.I):
            entry['output'] = True
        params.append(entry)
    return params


def parse_routine(clean, nocomment, idx):
    m = ROUTINE_RE.search(clean)
    if not m:
        return None
    kind = m.group(1).upper()
    kind = 'procedure' if kind.startswith('PROC') else kind.lower()
    pos = m.end()
    ptext = ptext_nc = ''
    body_start = pos
    rest = clean[pos:]
    stripped = rest.lstrip()
    off = pos + (len(rest) - len(stripped))
    if kind == 'view':
        params = []
    elif stripped.startswith('('):
        e = balanced(clean, off)
        ptext, ptext_nc = clean[off + 1:e - 1], nocomment[off + 1:e - 1]
        body_start = e
        params = parse_params(ptext, ptext_nc)
    elif kind == 'procedure':
        am = re.search(r'\bAS\b', clean[pos:], re.I)
        if am:
            ptext = clean[pos:pos + am.start()]
            ptext_nc = nocomment[pos:pos + am.start()]
            # drop WITH options
            wm = re.search(r'\bWITH\b', ptext, re.I)
            if wm:
                ptext, ptext_nc = ptext[:wm.start()], ptext_nc[:wm.start()]
            body_start = pos + am.end()
        params = parse_params(ptext, ptext_nc)
    else:
        params = []
    full = clean[m.start():]
    key = _qualify(m.group(2))
    r = extract_refs(clean[body_start:], idx, self_key=key)
    raises = extract_raises(nocomment[body_start:])
    dyn = [k for k in dynamic_refs(nocomment[body_start:], idx, key) if k not in r['referenced']]
    out = {'type': kind, 'name_in_file': key, 'parameters': params,
           'referenced_objects': r['referenced'], 'written_tables': r['written'], 'called_procs': r['called'],
           'raises_errors': raises,
           'writes_error_log': ('Util.AddErrorLog' in r['referenced'] or 'Log.DB_Errors' in r['written'])}
    if dyn:
        out['dynamic_sql_refs'] = dyn
    if kind == 'function':
        rt = re.search(r'\bRETURNS\s+(@\w+\s+TABLE|TABLE|[\w\[\]]+(?:\s*\([^)]*\))?)', clean[body_start:body_start + 400], re.I)
        if rt:
            out['returns'] = re.sub(r'\s+', ' ', rt.group(1)).strip()
    return out
