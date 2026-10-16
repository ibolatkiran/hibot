"""Backend (ASP.NET Core) analysis: controllers->endpoints, manager methods->db objects/external calls/errors."""
import os
import re
from collections import defaultdict

from common import walk
from csparse import CFile

SQL_KW_SKIP = {'select', 'values', 'inserted', 'deleted', 'openjson', 'string_split', 'openquery', 'set', 'where',
               'top', 'table', 'null', 'as', 'on', 'case', 'when', 'then', 'else', 'end', 'distinct', 'dual',
               'openrowset', 'json_value', 'json_query', 'cross', 'outer', 'apply', 'xmltable', 'generate_series'}
DAPPER_CALL = re.compile(r'\.\s*(Query|QueryFirst|QueryFirstOrDefault|QuerySingle|QuerySingleOrDefault|QueryAsync|'
                         r'QueryFirstAsync|QueryFirstOrDefaultAsync|QuerySingleAsync|QuerySingleOrDefaultAsync|QueryMultiple|'
                         r'QueryMultipleAsync|Execute|ExecuteAsync|ExecuteScalar|ExecuteScalarAsync|ExecuteReader|'
                         r'ExecuteReaderAsync)\s*(?:<[^()]*?>)?\s*\(')
SQLCMD = re.compile(r'new\s+SqlCommand\s*\(')
CMDTEXT = re.compile(r'\.CommandText\s*=\s*')
NAME_LIKE = re.compile(r'^\s*(\[?\w+\]?\.)*\[?\w+\]?\s*$')
SQL_OBJ = re.compile(r'\b(FROM|JOIN|INTO|UPDATE|MERGE)\s+((?:\[?[A-Za-z_]\w*\]?\.){0,2}\[?[A-Za-z_#@]\w*\]?)\s*(\()?', re.I)
EXEC_OBJ = re.compile(r'\bEXEC(?:UTE)?\s+((?:\[?[A-Za-z_]\w*\]?\.){0,2}\[?[A-Za-z_]\w*\]?)', re.I)
SQL_TEXT_UP = re.compile(r'\b(SELECT\b.*\bFROM\b|INSERT\s+INTO\b|UPDATE\s+\S+\s+SET\b|DELETE\s+FROM\b|MERGE\s+INTO\b|EXEC(UTE)?\s+\S+|\bFROM\s+\[?\w+\]?[.\s(]|\bJOIN\s+\[?\w+\]?\.)', re.S)
CALL_RE = re.compile(r'(?<![\w.])(?:this\.)?([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*(?:<[^()]*?>)?\s*\(')
LOCAL_CALL_RE = re.compile(r'(?<![\w.])(?:this\.)?([A-Za-z_]\w*)\s*(?:<[^()]*?>)?\s*\(')
ERR_PREFIX = re.compile(r'(ErrorDescription|ErrorMessage|ErrorMsg|ErrorText|ErrorDesc|Description|Message|Error|'
                        r'ResultMessage|ErrDesc|DisplayMessage)\s*[=:]|\bthrow\s+new\s+\w*Exception\s*\(\s*$|'
                        r'new\s+\w*Exception\s*\(\s*$|\bBadRequest\s*\(\s*$|\bErrorResult\s*\(|\bFail(?:ure)?\s*\(\s*$')
EXT_TYPE = re.compile(r'(HttpClient|Soap|Sap|Mule|Salesforce|Twilio|Okta|Graph|OneTrust|RestClient|RestSharp|'
                      r'Gateway|WebRequest|MailOrbit|Payment|Proxy|ServiceClient|Client$|Exchange)', re.I)
HTTP_METHODS = re.compile(r'\.\s*(GetAsync|PostAsync|PutAsync|DeleteAsync|SendAsync|GetStringAsync|PostAsJsonAsync|'
                          r'PutAsJsonAsync|GetFromJsonAsync|GetByteArrayAsync|GetStreamAsync|PatchAsync)\s*\(')
NOISE_CLASSES = {'UserContext', 'IUserContext', 'DapperRepository', 'IDapperRepository'}


def clean_sql_name(n):
    n = re.sub(r'[\[\]]', '', n.strip())
    return n


def parse_sql_objects(sql):
    """Return list of (name, kind) found in a SQL string."""
    objs = []
    seen = set()
    for mm in SQL_OBJ.finditer(sql):
        kw, name, paren = mm.group(1).upper(), clean_sql_name(mm.group(2)), mm.group(3)
        low = name.lower()
        if low in SQL_KW_SKIP or name.startswith('#') or name.startswith('@') or '{' in name:
            continue
        if low.startswith(('sys.', 'information_schema.', 'master.')):
            continue
        if kw == 'UPDATE' and paren:
            continue
        kind = 'function' if (paren and kw in ('FROM', 'JOIN')) else 'table'
        if kind == 'function' and low in SQL_KW_SKIP:
            continue
        if (name.lower(), kind) not in seen:
            seen.add((name.lower(), kind))
            objs.append((name, kind))
    for mm in EXEC_OBJ.finditer(sql):
        name = clean_sql_name(mm.group(1))
        if name.lower() in SQL_KW_SKIP or name.startswith('@'):
            continue
        if (name.lower(), 'sp') not in seen:
            seen.add((name.lower(), 'sp'))
            objs.append((name, 'sp'))
    return objs


class Backend:
    def __init__(self, repo):
        self.repo = repo
        self.files = []
        self.classes = {}       # name -> [CClass]
        self.iface_impl = defaultdict(list)
        self.methods = []       # analysed method dicts
        self.method_idx = defaultdict(list)   # (class, name) -> [method dict]
        self.endpoints = []
        self.external_classes = set()
        self.dbsets = {}        # context class -> {DbSet property -> entity}

    # ---------------------------------------------------------------- load
    def load(self):
        roots = [('Sirius', ()), ('Sirius.Business', ()), ('Sirius.Common', ()),
                 ('Sirius.DataAccess', ('MyModels', 'DbEntity', 'Migrations'))]
        for sub, skip in roots:
            for p in walk(os.path.join(self.repo, sub), ('.cs',), extra_skip_dirs=skip):
                if p.endswith(('.Designer.cs', '.g.cs')):
                    continue
                try:
                    cf = CFile(p, self.repo)
                except Exception as e:
                    print('  [warn] C# parse failed', p, e)
                    continue
                cf.project = sub
                self.files.append(cf)
                for c in cf.classes:
                    self.classes.setdefault(c.name, []).append(c)
        # EF DbContexts (MyModels is skipped for the general scan, read only DbContext files)
        ctx_files = list(walk(os.path.join(self.repo, 'Sirius.DataAccess'), ('.cs',)))
        for p in ctx_files:
            if not re.search(r'Context\.cs$', p):
                continue
            from common import read_text
            txt = read_text(p)
            for cm in re.finditer(r'class\s+(\w+)\s*:\s*DbContext', txt):
                props = {pm.group(2): pm.group(1) for pm in re.finditer(r'DbSet<(\w+)>\s+(\w+)\s*\{', txt)}
                self.dbsets[cm.group(1)] = props
        # external client class names (generated SOAP / service clients)
        for sub in ('Sirius.ServiceClient', 'ServiceReference'):
            for p in walk(os.path.join(self.repo, sub), ('.cs',)):
                try:
                    with open(p, 'rb') as f:
                        txt = f.read().decode('utf-8', 'ignore')
                except OSError:
                    continue
                for mm in re.finditer(r'\bclass\s+(\w*(?:Client|Service|Proxy)\w*)', txt):
                    self.external_classes.add(mm.group(1))
        for name, cs in self.classes.items():
            for c in cs:
                for b in c.bases:
                    self.iface_impl[b].append(c)

    # ---------------------------------------------------------------- resolution helpers
    def resolve_type(self, typ):
        """type name -> list of concrete CClass candidates having this name or implementing it."""
        out = []
        if typ in self.classes:
            for c in self.classes[typ]:
                if c.kind == 'class':
                    out.append(c)
        for c in self.iface_impl.get(typ, []):
            if c.kind == 'class' and c not in out:
                out.append(c)
        return out

    # ---------------------------------------------------------------- analysis
    def analyse(self):
        for cf in self.files:
            for md in cf.methods:
                if md.cls.kind == 'interface':
                    continue
                self._analyse_method(cf, md)
        self._build_endpoints()

    def _lits_in(self, cf, a, b):
        import bisect
        if not hasattr(cf, '_lit_starts'):
            cf._lit_starts = [l[0] for l in cf.lits]
        lo = bisect.bisect_left(cf._lit_starts, a)
        res = []
        for k in range(lo, len(cf.lits)):
            l = cf.lits[k]
            if l[0] >= b:
                break
            res.append(l)
        return res

    def _lit_at(self, cf, pos):
        """literal starting at or just after pos (skipping whitespace)"""
        m = cf.masked
        j = pos
        while j < len(m) and m[j].isspace():
            j += 1
        import bisect
        if not hasattr(cf, '_lit_starts'):
            cf._lit_starts = [l[0] for l in cf.lits]
        k = bisect.bisect_left(cf._lit_starts, j)
        if k < len(cf.lits) and cf.lits[k][0] == j:
            return cf.lits[k]
        return None

    def _first_arg(self, cf, op):
        cl = cf.pair.get(op)
        if cl is None:
            return None, None, None
        m = cf.masked
        i = op + 1
        s = i
        while i < cl:
            c = m[i]
            if c in '([{':
                j = cf.pair.get(i)
                i = (j if j else i) + 1
                continue
            if c == ',':
                break
            i += 1
        return s, i, cl

    def _resolve_arg_string(self, cf, md, a, b):
        """first-arg expression -> (string value or None)"""
        lit = self._lit_at(cf, a)
        if lit and lit[1] <= b + 1:
            # simple literal (possibly concatenated: take first only if nothing else)
            rest = cf.masked[lit[1]:b].strip()
            if not rest or rest.startswith('+'):
                return lit[2]
        expr = cf.masked[a:b].strip()
        if re.fullmatch(r'[A-Za-z_]\w*', expr):
            # last assignment before the call
            best = None
            for mm in re.finditer(r'\b' + re.escape(expr) + r'\s*=\s*(?!=)', cf.masked[md.body_start:a]):
                p = md.body_start + mm.end()
                l = self._lit_at(cf, p)
                if l:
                    best = l[2]
            return best
        if re.fullmatch(r'[\w.]+', expr):
            # constant like Constants.X : try to find a static string with that name
            nm = expr.split('.')[-1]
            for c in self.files:
                pass
        return None

    def _analyse_method(self, cf, md):
        body_a, body_b = md.body_start, md.body_end
        body_m = cf.masked[body_a:body_b + 1]
        cls = md.cls
        db = []
        seen_db = set()

        def add_db(name, kind, via):
            n = clean_sql_name(name)
            key = (n.lower(), kind)
            if key not in seen_db:
                seen_db.add(key)
                db.append({'name': n, 'kind': kind, 'via': via})

        claimed_lits = set()
        # Dapper calls
        for mm in DAPPER_CALL.finditer(body_m):
            op = body_a + mm.end() - 1
            a, b, cl = self._first_arg(cf, op)
            if a is None:
                continue
            argtxt = cf.masked[op:cl + 1]
            is_sp = 'StoredProcedure' in argtxt
            lit = self._lit_at(cf, a)
            val = self._resolve_arg_string(cf, md, a, b)
            if lit:
                claimed_lits.add(lit[0])
            recv = cf.masked[max(0, body_a + mm.start() - 40):body_a + mm.start()]
            via = 'ef' if re.search(r'[Cc]ontext\s*(\.Database)?\s*(\.Connection)?\s*(\.GetDbConnection\(\))?\s*$', recv) else 'dapper'
            if val is None:
                continue
            if is_sp or NAME_LIKE.match(val):
                if NAME_LIKE.match(val) and ' ' not in val.strip():
                    add_db(val, 'sp', via)
                    continue
            for n, k in parse_sql_objects(val):
                add_db(n, k, via)
            if not parse_sql_objects(val) and len(val) > 6:
                db.append({'name': re.sub(r'\s+', ' ', val.strip())[:100], 'kind': 'inline_sql', 'via': via})
        # ADO
        for rx in (SQLCMD, CMDTEXT):
            for mm in rx.finditer(body_m):
                if rx is SQLCMD:
                    op = body_a + mm.end() - 1
                    a, b, cl = self._first_arg(cf, op)
                else:
                    a = body_a + mm.end()
                    b = cf.masked.find(';', a)
                if a is None:
                    continue
                lit = self._lit_at(cf, a)
                if lit:
                    claimed_lits.add(lit[0])
                val = self._resolve_arg_string(cf, md, a, b)
                if not val:
                    continue
                if NAME_LIKE.match(val) and ' ' not in val.strip():
                    add_db(val, 'sp', 'ado')
                else:
                    objs = parse_sql_objects(val)
                    for n, k in objs:
                        add_db(n, k, 'ado')
                    if not objs and len(val) > 6:
                        db.append({'name': re.sub(r'\s+', ' ', val.strip())[:100], 'kind': 'inline_sql', 'via': 'ado'})
        # EF DbSet access  (_siriusContext.Role.ToListAsync())
        for mm in re.finditer(r'(?<![\w.])(?:this\.)?([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\b(?!\s*\()', body_m):
            ctype = cls.fields.get(mm.group(1))
            if ctype in self.dbsets and mm.group(2) in self.dbsets[ctype]:
                add_db(mm.group(2), 'table', 'ef')
        # inline SQL anywhere in remaining literals
        for l in self._lits_in(cf, body_a, body_b):
            if l[0] in claimed_lits:
                continue
            v = l[2]
            if len(v) < 12:
                continue
            if SQL_TEXT_UP.search(v) or (re.search(r'\bdbo\b', v, re.I) and re.search(r'\b(select|from|join|exec|insert|update)\b', v, re.I)):
                for n, k in parse_sql_objects(v):
                    add_db(n, k, 'inline')
        # EF raw sql
        for mm in re.finditer(r'\b(FromSqlRaw|FromSqlInterpolated|ExecuteSqlRaw|ExecuteSqlInterpolated\w*|ExecuteSqlRawAsync)\s*\(', body_m):
            pass  # literal handled above as inline; mark via
        for d in db:
            if d['via'] == 'inline' and re.search(r'FromSql|ExecuteSql', body_m):
                d['via'] = 'ef'

        # calls
        calls = []
        local_calls = []
        ext = []
        seen_calls = set()
        for mm in CALL_RE.finditer(body_m):
            recv, meth = mm.group(1), mm.group(2)
            typ = cls.fields.get(recv)
            targets = []
            if typ:
                targets = self.resolve_type(typ)
            elif recv in self.classes and recv[0].isupper():
                targets = [c for c in self.classes[recv] if c.kind == 'class']
            for t in targets:
                if t.name in NOISE_CLASSES:
                    continue
                if any(m_.name == meth for m_ in t.methods):
                    key = f'{t.name}.{meth}'
                    if key not in seen_calls:
                        seen_calls.add(key)
                        calls.append(key)
            # external
            tname = typ or recv
            if tname and (EXT_TYPE.search(tname) and tname not in self.classes or tname in self.external_classes):
                key = (tname, meth)
                if key not in seen_calls:
                    seen_calls.add(key)
                    ext.append({'kind': 'soap' if re.search(r'Soap|Sap|Service', tname) else 'client', 'target': f'{tname}.{meth}'})
            elif targets:
                for t in targets:
                    if re.search(r'Sap|Soap|MailOrbit|Mule|Salesforce|Twilio|Payment|Exchange', t.name) and any(m_.name == meth for m_ in t.methods):
                        key = ('ext', t.name, meth)
                        if key not in seen_calls:
                            seen_calls.add(key)
                            ext.append({'kind': 'internal-client', 'target': f'{t.name}.{meth}'})
        for mm in LOCAL_CALL_RE.finditer(body_m):
            nm = mm.group(1)
            if nm in ('if', 'for', 'foreach', 'while', 'switch', 'using', 'lock', 'catch', 'return', 'new', 'nameof', 'typeof',
                      'await', 'sizeof', 'default', 'when', 'string', 'int', 'var', 'decimal', 'Task', 'List'):
                continue
            if nm == md.name:
                continue
            if any(m_.name == nm for m_ in cls.methods):
                key = f'{cls.name}.{nm}'
                if key not in seen_calls:
                    seen_calls.add(key)
                    calls.append(key)
        # HttpClient style calls
        for mm in HTTP_METHODS.finditer(body_m):
            op = body_a + mm.end() - 1
            a, b, cl = self._first_arg(cf, op)
            tgt = None
            if a is not None:
                lit = self._lit_at(cf, a)
                if lit:
                    tgt = lit[2].split('?')[0][:120]
            ext.append({'kind': 'http', 'target': tgt or '(dynamic url)', 'call': mm.group(1)})
        for mm in re.finditer(r'\bnew\s+(HttpClient|RestClient|WebClient)\b|WebRequest\.Create\s*\(|CreateClient\s*\(\s*(?:"|)', body_m):
            ext.append({'kind': 'http', 'target': mm.group(0).strip()[:40]})
        for mm in re.finditer(r'\bnew\s+(\w+(?:Client|Proxy|SoapClient)\w*)\s*\(', body_m):
            ext.append({'kind': 'soap' if mm.group(1) in self.external_classes else 'client', 'target': 'new ' + mm.group(1)})
        # dedupe ext
        uniq, s2 = [], set()
        for e in ext:
            k = (e['kind'], e['target'])
            if k not in s2:
                s2.add(k)
                uniq.append(e)
        ext = uniq

        # error messages
        errs = []
        for l in self._lits_in(cf, body_a, body_b):
            v = l[2].strip()
            if len(v) < 3 or not re.search(r'[A-Za-zÇĞİÖŞÜçğıöşü]{3}', v):
                continue
            pre_start = max(body_a, l[0] - 200)
            pre = cf.masked[pre_start:l[0]]
            k = max(pre.rfind(';'), pre.rfind('{'), pre.rfind('}'))
            stmt_pre = pre[k + 1:] if k >= 0 else pre
            if ERR_PREFIX.search(stmt_pre) and l[0] not in claimed_lits:
                if SQL_TEXT_UP.search(v) and len(v) > 30:
                    continue
                if v not in errs:
                    errs.append(v if len(v) <= 300 else v[:300] + '...')
            if len(errs) >= 60:
                break

        rec = {'class': cls.name, 'method': md.name, 'file': cf.rel, 'line_start': md.line_start,
               'line_end': md.line_end, 'db_objects': db, 'calls': calls, 'external_calls': ext,
               'error_messages': errs}
        if md.is_ctor:
            rec['ctor'] = True
        md._rec = rec
        md._attrs_http = [a for a in md.attrs if a[0] in ('HttpGet', 'HttpPost', 'HttpPut', 'HttpDelete', 'HttpPatch')]
        self.methods.append(rec)
        self.method_idx[(cls.name, md.name)].append(rec)

    # ---------------------------------------------------------------- endpoints
    def _build_endpoints(self):
        for cf in self.files:
            for c in cf.classes:
                if not (c.name.endswith('Controller') and c.kind == 'class'):
                    continue
                cname = c.name[:-len('Controller')]
                croutes = [a for a in c.attrs if a[0] == 'Route']
                ctmpl = croutes[-1][1][0] if croutes and croutes[-1][1] else ''
                ctmpl = ctmpl.replace('[controller]', cname)
                for md in c.methods:
                    if md.is_ctor or not hasattr(md, '_rec'):
                        continue
                    https = md._attrs_http
                    mroute = [a for a in md.attrs if a[0] == 'Route']
                    for hname, hargs, hline in https:
                        t = hargs[0] if hargs else (mroute[0][1][0] if mroute and mroute[0][1] else '')
                        t = t.replace('[action]', md.name).replace('[controller]', cname)
                        if t.startswith('~/') or t.startswith('/'):
                            route = t.lstrip('~/')
                        else:
                            ctm = ctmpl.replace('[action]', md.name)
                            route = (ctm.rstrip('/') + '/' + t) if t else ctm
                        rec = md._rec
                        self.endpoints.append({
                            'http_method': hname.replace('Http', '').upper(), 'route': route.strip('/'),
                            'controller': cname, 'action': md.name, 'file': cf.rel, 'line': md.line_start,
                            'attribute_line': hline, 'line_end': md.line_end,
                            'calls': rec['calls'], 'db_objects': rec['db_objects'],
                            'external_calls': rec['external_calls'], 'error_messages': rec['error_messages']})

    # ---------------------------------------------------------------- downstream
    def downstream(self, calls, depth=4):
        """aggregate db_objects / external / errors reachable from a list of 'Class.method' keys."""
        seen = set()
        stack = [(k, 1, [k]) for k in calls]
        dbs, exts, errs, chains = {}, {}, [], []
        out_methods = []
        while stack:
            key, d, path = stack.pop()
            if key in seen:
                continue
            seen.add(key)
            cls, _, meth = key.partition('.')
            for rec in self.method_idx.get((cls, meth), []):
                out_methods.append(key)
                for o in rec['db_objects']:
                    k = (o['name'].lower(), o['kind'])
                    if k not in dbs:
                        dbs[k] = dict(o, via_method=key)
                for e in rec['external_calls']:
                    exts.setdefault((e['kind'], e['target']), dict(e, via_method=key))
                for m_ in rec['error_messages']:
                    if len(errs) < 80 and (m_, key) not in errs:
                        errs.append((m_, key))
                if d < depth:
                    for c in rec['calls']:
                        stack.append((c, d + 1, path + [c]))
        return {'methods': sorted(set(out_methods)), 'db_objects': list(dbs.values()),
                'external_calls': list(exts.values()),
                'error_messages': [{'text': t, 'method': k} for t, k in errs]}
