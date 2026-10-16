"""Frontend (React) analysis: routes, screens, i18n usage, axios api calls, action->saga->api chains."""
import json
import os
import re
from collections import deque, defaultdict

from common import walk, read_text, rel
from jsparse import JsFile, url_template, file_consts, IDENT

UNIT_DIRS = ('src/views/', 'src/components/', 'src/layouts/')
HTTP_RE = re.compile(r'(?<![\w$.])([A-Za-z_$][\w$]*)\s*\.\s*(get|post|put|patch|delete|head)\s*\(')
CFG_RE = re.compile(r'(?<![\w$.])([A-Za-z_$][\w$]*)\s*(?:\.\s*request\s*)?\(\s*\{')
TAKE_RE = re.compile(r'\btake(?:Every|Latest|Leading)?\s*\(\s*([\w.]+)\s*,\s*([\w.]+)')
TYPE_RE = re.compile(r'\btype\s*:\s*([\w.]+)')
REEXPORT_RE = re.compile(r'export\s*(?:\{([^}]*)\}|\*)\s*from\s*[\'"]([^\'"]+)[\'"]')
T_CALL_RE = re.compile(r'(?<![\w$.])t\s*\(|\bi18n(?:ext)?\s*\.\s*t\s*\(|\bprops\s*\.\s*t\s*\(')
USE_T_RE = re.compile(r'\b(useTranslation|withTranslation)\s*\(')


class Frontend:
    def __init__(self, app_id, root, ns_prefix=''):
        self.app_id = app_id
        self.root = root
        self.src = os.path.join(root, 'src')
        self.files = {}   # rel -> JsFile
        self.instances = {}
        self._res_cache = {}
        self.typeedges = defaultdict(list)
        self.sites = []   # all api call sites
        self.stats = {}

    # ------------------------------------------------------------ loading
    def load(self):
        for p in walk(self.src, ('.js', '.jsx')):
            try:
                jf = JsFile(p, self.root)
            except Exception as e:  # keep going
                print('  [warn] parse failed', p, e)
                continue
            self.files[jf.rel] = jf
        self._parse_instances()
        for jf in self.files.values():
            self._find_calls(jf)
        self._build_type_edges()

    def resolve(self, jf, raw):
        key = (jf.rel, raw)
        if key in self._res_cache:
            return self._res_cache[key]
        r = None
        base = None
        if raw.startswith('src/'):
            base = os.path.join(self.root, raw)
        elif raw.startswith('.'):
            base = os.path.normpath(os.path.join(os.path.dirname(jf.path), raw))
        if base:
            for cand in (base, base + '.js', base + '.jsx', os.path.join(base, 'index.js'), os.path.join(base, 'index.jsx')):
                rp = rel(cand, self.root)
                if rp in self.files:
                    r = rp
                    break
        self._res_cache[key] = r
        return r

    # ------------------------------------------------------------ axios instances
    def _parse_instances(self):
        for r, jf in self.files.items():
            if 'axios.create(' not in jf.masked:
                continue
            m = re.search(r'baseURL\s*:\s*([^\n]+)', jf.text)
            expr = m.group(1).strip().rstrip(',}').strip() if m else ''
            kind, external = 'api', False
            if 'BACKEND_URL' in expr:
                kind = 'backend'
            elif 'CUSTOM' in expr:
                kind, external = 'custom', True
            elif 'NOTIFICATION' in expr:
                kind, external = 'notification', True
            elif expr.startswith('http'):
                kind, external = 'absolute', True
            suffix = re.sub(r'\$\{[^}]*\}|process\.env\.\w+|[`\'"]|apiURL', '', expr).strip(' +});,')
            icp = re.search(r'function\s+add\w*ToURL\s*\([^)]*\)\s*\{\s*return\s*([\'"`])([^\'"`]*)\1\s*\+', jf.text)
            prefix = (suffix + '/' + icp.group(2) if icp else suffix)
            segs = [s for s in prefix.split('/') if s]
            if segs and segs[0].lower() == 'api':
                segs = segs[1:]
            self.instances[r] = {'file': r, 'base_expr_kind': kind, 'prefix': '/'.join(segs),
                                 'external': external, 'interceptor_prefix': icp.group(2) if icp else None}

    # ------------------------------------------------------------ api call sites
    def _axios_receivers(self, jf):
        out = {}
        for local, (raw, name) in jf.imports.items():
            if raw == 'axios':
                out[local] = '(axios npm)'
                continue
            tgt = self.resolve(jf, raw)
            if tgt in self.instances and name in ('default', '*'):
                out[local] = tgt
        return out

    def _find_calls(self, jf):
        recv = self._axios_receivers(jf)
        if not recv:
            return
        consts = None
        raw_hits = []
        for mm in HTTP_RE.finditer(jf.masked):
            if mm.group(1) in recv:
                raw_hits.append((mm.start(), mm.group(1), mm.group(2).upper(), mm.end() - 1))
        for mm in CFG_RE.finditer(jf.masked):
            if mm.group(1) in recv:
                raw_hits.append((mm.start(), mm.group(1), None, mm.end() - mm.group(0)[::-1].index('(') - 1 if False else jf.masked.index('(', mm.start() + len(mm.group(1)))))
        for pos, name, method, op in raw_hits:
            if consts is None:
                consts = file_consts(jf)
            args = jf.arg_spans(op)
            if not args:
                continue
            if method is None:  # config object form
                a, b = args[0]
                obj = jf.text[a:b]
                mu = re.search(r'\burl\s*:\s*([^,}\n]+)', obj)
                mm_ = re.search(r'\bmethod\s*:\s*[\'"`](\w+)', obj)
                if not mu:
                    continue
                method = (mm_.group(1) if mm_ else 'GET').upper()
                ua = a + mu.start(1)
                ub = a + mu.end(1)
                tmpl = url_template(jf, ua, ub, consts)
            else:
                a, b = args[0]
                tmpl = url_template(jf, a, b, consts)
            inst_key = recv[name]
            inst = self.instances.get(inst_key, {'prefix': '', 'external': False})
            d = jf.decl_at(pos)
            site = {
                'method': method, 'url_template': tmpl, 'axios_instance': inst_key,
                'file': jf.rel, 'line': jf.line(op), 'decl': d.name if d else None,
            }
            site.update(self._resolve_endpoint(tmpl, inst))
            site['id'] = f"{jf.rel}:{site['line']}:{len(self.sites)}"
            self.sites.append(site)
            if d:
                d.calls.append(site)

    @staticmethod
    def _resolve_endpoint(tmpl, inst):
        t = tmpl.strip()
        res = {'resolved_endpoint': None, 'path_template': None, 'external': False, 'unresolved': False}
        if re.match(r'https?://', t) or (inst.get('external') and True):
            res['external'] = True
            res['path_template'] = t.split('?')[0]
            return res
        t = t.split('?')[0]
        segs = [s for s in t.split('/') if s != '']
        if not segs or (len(segs) == 1 and segs[0].startswith('{')):
            res['unresolved'] = True
            res['path_template'] = t
            return res
        pre = [s for s in inst.get('prefix', '').split('/') if s]
        full = pre + segs
        if full and full[0].lower() == 'api':
            full = full[1:]
        res['path_template'] = '/'.join(full)
        norm = ['{}' if '{' in s else s for s in full]
        res['path_norm'] = '/'.join(norm)
        if len(full) >= 2 and '{' not in full[0] and '{' not in full[1]:
            res['resolved_endpoint'] = full[0] + '/' + full[1]
        elif len(full) >= 1 and '{' not in full[0]:
            res['resolved_endpoint'] = full[0]
        else:
            res['unresolved'] = True
        return res

    # ------------------------------------------------------------ symbol graph
    def symbol(self, jf, local, depth=0):
        """Resolve imported local name to (rel, declname) or None."""
        imp = jf.imports.get(local)
        if not imp:
            return None
        raw, name = imp
        tgt = self.resolve(jf, raw)
        if not tgt:
            return None
        return self._follow(tgt, name, depth)

    def _follow(self, tgt, name, depth=0):
        tj = self.files[tgt]
        if name in tj.decls:
            return (tgt, name)
        if depth > 4:
            return None
        for mm in REEXPORT_RE.finditer(tj.text):
            names, src = mm.group(1), mm.group(2)
            nxt = self.resolve(tj, src)
            if not nxt:
                continue
            if names is None:
                r = self._follow(nxt, name, depth + 1)
                if r:
                    return r
            else:
                for part in names.split(','):
                    part = part.strip()
                    m2 = re.match(r'(\w+)(?:\s+as\s+(\w+))?$', part)
                    if m2 and (m2.group(2) or m2.group(1)) == name:
                        return self._follow(nxt, m2.group(1), depth + 1)
        return (tgt, name) if name == 'default' else None

    def _type_node(self, jf, expr):
        """expr like actions.Types.X | Types.X | X -> ('TYPE:<file>', X)"""
        parts = expr.split('.')
        x = parts[-1]
        if len(parts) >= 3:
            ns = parts[0]
            imp = jf.imports.get(ns)
            if imp:
                tgt = self.resolve(jf, imp[0])
                if tgt:
                    return ('TYPE:' + tgt, x)
        elif len(parts) == 2:
            holder = parts[0]
            if holder in jf.decls:
                return ('TYPE:' + jf.rel, x)
            imp = jf.imports.get(holder)
            if imp:
                tgt = self.resolve(jf, imp[0])
                if tgt:
                    return ('TYPE:' + tgt, x)
            if imp is None and holder in jf.imports:
                return None
        else:
            imp = jf.imports.get(x)
            if imp:
                tgt = self.resolve(jf, imp[0])
                if tgt:
                    return ('TYPE:' + tgt, imp[1])
            return ('TYPE:' + jf.rel, x)
        return None

    def _build_type_edges(self):
        for r, jf in self.files.items():
            if '/sagas/' not in r and 'saga' not in r.lower():
                continue
            for mm in TAKE_RE.finditer(jf.masked):
                tn = self._type_node(jf, mm.group(1))
                h = mm.group(2)
                if not tn:
                    continue
                if h in jf.decls:
                    self.typeedges[tn].append((r, h))
                elif h in jf.imports:
                    s = self.symbol(jf, h)
                    if s:
                        self.typeedges[tn].append(s)

    _EDGE_CACHE = None

    def edges(self, node):
        if self._EDGE_CACHE is None:
            self._EDGE_CACHE = {}
        if node in self._EDGE_CACHE:
            return self._EDGE_CACHE[node]
        out = []
        r, name = node
        if r.startswith('TYPE:'):
            out = list(self.typeedges.get(node, []))
        else:
            jf = self.files.get(r)
            d = jf.decls.get(name) if jf else None
            if d is not None:
                idents, seg = jf.idents(d)
                if 'createSlice(' in seg or 'combineReducers' in seg or 'configureStore' in seg:
                    out = []
                else:
                    out = self._ident_edges(jf, idents, seg)
                    for mm in TYPE_RE.finditer(seg):
                        tn = self._type_node(jf, mm.group(1))
                        if tn and tn[1].isupper() or (tn and '_' in tn[1]):
                            out.append(tn)
        self._EDGE_CACHE[node] = out
        return out

    def _ident_edges(self, jf, idents, seg):
        out = []
        for ident in sorted(idents):
            if ident in jf.imports:
                raw, name = jf.imports[ident]
                tgt = self.resolve(jf, raw)
                if not tgt:
                    continue
                if name == '*':
                    for mm in re.finditer(r'\b' + re.escape(ident) + r'\s*\.\s*([A-Za-z_$][\w$]*)', seg):
                        s = self._follow(tgt, mm.group(1))
                        if s and s[1] != 'default':
                            out.append(s)
                else:
                    s = self._follow(tgt, name)
                    if s:
                        if name == 'default':
                            # object-of-methods default export: narrow by member when possible
                            mem = set(re.findall(r'\b' + re.escape(ident) + r'\s*\.\s*([A-Za-z_$][\w$]*)', seg))
                            narrowed = [(tgt, m) for m in sorted(mem) if m in self.files[tgt].decls]
                            out.extend(narrowed if narrowed else [s])
                        else:
                            out.append(s)
            elif ident in jf.decls:
                out.append((jf.rel, ident))
        return out

    def is_unit(self, r):
        return r.startswith(UNIT_DIRS)

    def unit_start_nodes(self, jf):
        idents, seg = jf.idents(None)
        return self._ident_edges(jf, idents, seg)

    def reach_calls(self, jf):
        """BFS from a unit file through store/util code; return list of (site, chain)."""
        found = {}
        # raw calls inside the file
        for d in jf.decls.values():
            for s in d.calls:
                found.setdefault(s['id'], (s, [jf.rel + '::' + (s['decl'] or '(top-level)')]))
        seen = set()
        q = deque()
        for n in self.unit_start_nodes(jf):
            if n[0] != jf.rel and not self.is_unit(n[0]):
                q.append((n, [jf.rel, n[0] + '::' + n[1]]))
        while q:
            node, chain = q.popleft()
            if node in seen or len(chain) > 9:
                continue
            seen.add(node)
            if not node[0].startswith('TYPE:'):
                tj = self.files.get(node[0])
                d = tj.decls.get(node[1]) if tj else None
                if d:
                    for s in d.calls:
                        found.setdefault(s['id'], (s, chain))
            for nx in self.edges(node):
                if nx in seen:
                    continue
                if not nx[0].startswith('TYPE:') and self.is_unit(nx[0]):
                    continue
                label = (nx[0].replace('TYPE:', 'action-type ') + '::' + nx[1])
                q.append((nx, chain + [label]))
        return list(found.values())

    @staticmethod
    def classify_via(site, chain):
        f = site['file']
        if any('/sagas/' in c or '/saga' in c.lower() for c in chain):
            return 'saga'
        if '/store/slices/' in f:
            return 'slice'
        if '/store/api/' in f:
            return 'api'
        if f.startswith(UNIT_DIRS):
            return 'raw'
        return 'other'

    # ------------------------------------------------------------ routes
    def parse_routes(self):
        rf = self.files.get('src/routes.js')
        if not rf:
            return []
        m, t = rf.masked, rf.text
        pr = rf.brackets()
        routes = []

        def top_props(open_pos):
            close = pr[open_pos]
            d0 = rf.depth_at(open_pos) + 1
            props = {}
            for mm in re.finditer(r'\b(path|component|routes|exact|public|layout)\s*:', m[open_pos + 1:close]):
                p = open_pos + 1 + mm.start()
                if rf.depth_at(p) == d0:
                    props[mm.group(1)] = open_pos + 1 + mm.end()
            return close, props

        def prop_expr(start, close):
            i, depth = start, 0
            while i < close:
                c = m[i]
                if c in '([{':
                    j = pr.get(i)
                    i = (j if j else i) + 1
                    continue
                if c == ',':
                    break
                i += 1
            return start, i

        def walk_obj(open_pos, parent_layout):
            close, props = top_props(open_pos)
            path = None
            if 'path' in props:
                a, b = prop_expr(props['path'], close)
                path = url_template(rf, a, b)
            comp_name, comp_file = None, None
            if 'component' in props:
                a, b = prop_expr(props['component'], close)
                expr = t[a:b].strip()
                di = re.search(r'import\(\s*[\'"]([^\'"]+)', expr)
                if di:
                    comp_file = self.resolve(rf, di.group(1))
                    comp_name = os.path.basename(di.group(1).rstrip('/'))
                elif re.fullmatch(r'[A-Za-z_$][\w$]*', expr):
                    comp_name = expr
                    if expr in rf.imports:
                        comp_file = self.resolve(rf, rf.imports[expr][0])
                else:
                    comp_name = '(inline)'
            if comp_name is not None or path is not None:
                routes.append({'path': path, 'component': comp_name, 'file': comp_file,
                               'layout': parent_layout, 'exact': 'exact' in props,
                               'line': rf.line(open_pos)})
            if 'routes' in props:
                a, b = prop_expr(props['routes'], close)
                k = m.find('[', a, b)
                if k >= 0 and k in pr:
                    for mm in re.finditer(r'\{', m[k + 1:pr[k]]):
                        p = k + 1 + mm.start()
                        if rf.depth_at(p) == rf.depth_at(k) + 1:
                            walk_obj(p, comp_name or parent_layout)

        # top-level array
        k = m.find('[', m.find('export default'))
        if k >= 0 and k in pr:
            for mm in re.finditer(r'\{', m[k + 1:pr[k]]):
                p = k + 1 + mm.start()
                if rf.depth_at(p) == rf.depth_at(k) + 1:
                    walk_obj(p, None)
        return routes

    # ------------------------------------------------------------ i18n usage
    def i18n_usage(self, jf):
        t, m = jf.text, jf.masked
        nss = []
        for mm in USE_T_RE.finditer(m):
            op = mm.end() - 1
            args = jf.arg_spans(op)
            if args:
                a, b = args[0]
                nss += re.findall(r'[\'"]([^\'"]+)[\'"]', t[a:b])
        # i18n.loadNamespaces / getFixedT
        for mm in re.finditer(r'getFixedT\s*\(\s*[^,]*,\s*[\'"]([^\'"]+)', t):
            nss.append(mm.group(1))
        nss = list(dict.fromkeys(nss))
        keys = []
        dyn = 0
        for mm in T_CALL_RE.finditer(m):
            op = mm.end() - 1
            args = jf.arg_spans(op)
            if not args:
                continue
            a, b = args[0]
            raw = t[a:b].strip()
            if len(raw) >= 2 and raw[0] in '\'"`' and raw[-1] == raw[0] and (raw[0] != '`' or '${' not in raw):
                keys.append({'key': raw[1:-1], 'line': jf.line(mm.start())})
            elif re.match(r'^[\'"`]', raw):
                k = url_template(jf, a, b)
                keys.append({'key': k, 'dynamic': True, 'line': jf.line(mm.start())})
            else:
                dyn += 1
        for mm in re.finditer(r'\bi18nKey\s*=\s*\{?\s*[\'"]([^\'"]+)[\'"]', t):
            keys.append({'key': mm.group(1), 'line': jf.line(mm.start())})
        return nss, keys, dyn


def closure_files(fe, start_rel):
    """Files reachable via imports restricted to unit dirs (views/components/layouts)."""
    seen = {start_rel}
    q = deque([start_rel])
    while q:
        r = q.popleft()
        jf = fe.files.get(r)
        if not jf:
            continue
        srcs = [raw for raw, _ in jf.imports.values()] + jf.dyn_imports
        for raw in srcs:
            tgt = fe.resolve(jf, raw)
            if tgt and tgt not in seen and fe.is_unit(tgt):
                seen.add(tgt)
                q.append(tgt)
        for mm in REEXPORT_RE.finditer(jf.text):
            tgt = fe.resolve(jf, mm.group(2))
            if tgt and tgt not in seen and fe.is_unit(tgt):
                seen.add(tgt)
                q.append(tgt)
    return seen


def build_app(app_id, root, ns_prefix, locale_ns_en=None):
    fe = Frontend(app_id, root, ns_prefix)
    fe.load()
    routes = fe.parse_routes()
    # per-unit analysis
    screens = {}
    site_users = defaultdict(set)
    for r, jf in fe.files.items():
        if not (r.startswith('src/views/') or r.startswith('src/components/')):
            continue
        reach = fe.reach_calls(jf)
        calls = []
        for s, chain in sorted(reach, key=lambda x: (x[0]['file'], x[0]['line'])):
            c = {k: s[k] for k in ('method', 'url_template', 'resolved_endpoint', 'axios_instance', 'file', 'line')}
            c['via'] = fe.classify_via(s, chain)
            c['chain'] = chain
            c['site_id'] = s['id']
            if s['external']:
                c['external'] = True
            if s['unresolved']:
                c['unresolved'] = True
            calls.append(c)
            site_users[s['id']].add(r)
        nss, keys, dyn = fe.i18n_usage(jf)
        screens[r] = {'file': r, 'kind': 'view' if r.startswith('src/views/') else 'component',
                      'routes': [], 'reachable_from_routes': [],
                      'i18n': {'namespaces': nss, 'keys': keys, 'dynamic_t_calls': dyn},
                      'api_calls': calls}
    # route -> files
    route_out = []
    for ro in routes:
        entry = dict(ro)
        if ro['file'] and ro['file'] in fe.files:
            cl = closure_files(fe, ro['file'])
            entry['closure_files'] = len(cl)
            eps = set()
            for f in cl:
                sc = screens.get(f)
                if sc:
                    for c in sc['api_calls']:
                        if c['resolved_endpoint'] and not c.get('external'):
                            eps.add(c['method'] + ' ' + c['resolved_endpoint'])
                    if f == ro['file'] and ro['path']:
                        sc['routes'].append(ro['path'])
                    elif ro['path']:
                        sc['reachable_from_routes'].append(ro['path'])
            entry['api_endpoints'] = sorted(eps)
        route_out.append(entry)
    for sc in screens.values():
        sc['routes'] = sorted(set(sc['routes']))
        rr = sorted(set(sc['reachable_from_routes']))
        sc['reachable_from_routes'] = rr[:25]
        if len(rr) > 25:
            sc['reachable_route_count'] = len(rr)
    # resolve ns for keys
    kept = {}
    for r, sc in screens.items():
        has = sc['api_calls'] or sc['i18n']['keys'] or sc['routes'] or sc['kind'] == 'view'
        if not has:
            continue
        for k in sc['i18n']['keys']:
            key = k['key']
            if ':' in key and key.split(':')[0] in (locale_ns_en or {}):
                k['ns'], k['key'] = key.split(':', 1)
                continue
            nss = sc['i18n']['namespaces']
            if len(nss) == 1:
                k['ns'] = nss[0]
            elif nss and locale_ns_en:
                hit = [n for n in nss if key in locale_ns_en.get(n, {})]
                k['ns'] = hit[0] if len(hit) >= 1 else None
                if not hit:
                    k['ns_candidates'] = nss
            elif not nss:
                k['ns'] = None
        kept[r] = sc
    sites_unique = []
    for s in fe.sites:
        o = {k: s[k] for k in ('id', 'method', 'url_template', 'resolved_endpoint', 'path_norm', 'axios_instance', 'file', 'line', 'decl', 'external', 'unresolved') if k in s}
        o['used_by_screens'] = sorted(site_users.get(s['id'], []))
        sites_unique.append(o)
    result = {
        'app': app_id, 'root_name': os.path.basename(root), 'ns_prefix': ns_prefix,
        'axios_instances': fe.instances,
        'routes': route_out,
        'screens': list(kept.values()),
        'api_call_sites': sites_unique,
    }
    fe.stats = {
        'files_parsed': len(fe.files), 'routes': len(route_out),
        'routes_with_file': sum(1 for r in route_out if r.get('file')),
        'screens': len(kept), 'views': sum(1 for s in kept.values() if s['kind'] == 'view'),
        'api_call_sites': len(sites_unique),
        'api_call_sites_reachable_from_screen': sum(1 for s in sites_unique if s['used_by_screens']),
        'screen_api_call_links': sum(len(s['api_calls']) for s in kept.values()),
        'external_or_unresolved_sites': sum(1 for s in sites_unique if s.get('external') or s.get('unresolved')),
    }
    return fe, result
