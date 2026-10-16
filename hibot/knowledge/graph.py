"""Runtime query API over knowledge/graph/graph.json (stdlib only).

Built by tools/graph/build.py. Joins: screen -> endpoint -> manager method -> SP/function/view -> table (+ triggers).
All public methods return plain JSON-serializable dicts/lists; every item carries a short `evidence` string and
`file`/`line` where known. Backup/copy DB objects are excluded unless include_backups=True (objects explicitly asked for
by name, and objects the code directly invokes, are always shown but flagged `is_backup_or_copy`).
"""
import json
import math
import os
import re
import unicodedata
from collections import Counter, defaultdict, deque

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                            "knowledge", "graph", "graph.json")
DB_TYPES = ("proc", "func", "view", "table", "trigger")
ROUTINES = ("proc", "func", "view")
_TR = str.maketrans({"ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g", "ü": "u", "Ü": "u", "ö": "o",
                     "Ö": "o", "ç": "c", "Ç": "c"})
STOP = set("""a an the and or but if of to in on at by for with from as is are was were be been it its this that these those i we
you he she they not no do does did can could will would should have has had there here what which who when where why how all any
some other more such than then so too very just also into over about after before again up down out off only own same both few
per via please thanks get set see show shown api index view views src js jsx ts tsx com component components use used using cannot cant
can't doesn't dont don't unable error errors issue problem ve veya ile bir bu icin gibi olarak da de mi mu""".split())


def fold(s):
    s = (s or "").translate(_TR)
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def tokenize(text, split_camel=True):
    text = text or ""
    if split_camel:
        text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    out = []
    for w in re.findall(r"[^\W_]+", fold(text)):
        if len(w) < 2 or w in STOP or w.isdigit():
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def _norm_text(s):
    return re.sub(r"\s+", " ", fold(s)).strip(" .:!?\"'")


class CodeGraph:
    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, path=None):
        path = path or DEFAULT_PATH
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls(data, path)

    def __init__(self, data, path=None):
        self.path = path
        self.meta = data["meta"]
        self.nodes = data["nodes"]
        self.errors = data["errors"]
        self.i18n = data.get("i18n", {})
        ids = list(self.nodes)
        self.out = defaultdict(lambda: defaultdict(list))
        self.inn = defaultdict(lambda: defaultdict(list))
        for s, t, ty, *rest in data["edges"]:
            at = rest[0] if rest else None
            self.out[ids[s]][ty].append((ids[t], at))
            self.inn[ids[t]][ty].append((ids[s], at))
        self.by_type = defaultdict(list)
        for nid, n in self.nodes.items():
            n["id"] = nid
            self.by_type[n["type"]].append(nid)
        self._lower = {}
        for nid in self.by_type["proc"] + self.by_type["func"] + self.by_type["view"] + self.by_type["table"]:
            self._lower.setdefault(self.nodes[nid]["name"].lower(), []).append(nid)
        self._ep_index = None
        self._cache = {}
        self._bm = None

    # ------------------------------------------------------------------ small helpers
    def _n(self, nid):
        return self.nodes[nid]

    def _ok(self, nid, include_backups):
        return include_backups or not self.nodes[nid].get("is_backup_or_copy")

    def _item(self, nid, evidence="", **extra):
        n = self.nodes[nid]
        d = {"id": nid, "name": n.get("name") or n.get("file") or nid, "type": n["type"]}
        if n.get("regions") is not None and n["type"] in DB_TYPES:
            d["regions"] = n["regions"]
            if n.get("is_backup_or_copy"):
                d["is_backup_or_copy"] = True
            if n.get("unresolved"):
                d["unresolved"] = True
        if n.get("file"):
            d["file"] = n["file"]
            d["line"] = n.get("line")
        if n["type"] in ("screen", "route"):
            d["app"] = n["app"]
        d["evidence"] = evidence
        d.update(extra)
        return d

    def _path_str(self, parent, nid, limit=6):
        chain = []
        while nid is not None:
            chain.append(self.nodes[nid].get("name") or nid)
            nid = parent.get(nid)
        chain.reverse()
        if len(chain) > limit:
            chain = chain[:2] + ["..."] + chain[-(limit - 3):]
        return " -> ".join(chain)

    # ------------------------------------------------------------------ endpoint resolution
    @staticmethod
    def _route_key(route):
        r = route.split("?")[0].strip().strip("/")
        r = re.sub(r"^api/", "", r, flags=re.I)
        segs = [s for s in r.split("/") if s and not (s.startswith("{") or s.startswith(":"))]
        return "/".join(segs).lower()

    def _build_ep_index(self):
        idx = defaultdict(list)
        for eid in self.by_type["endpoint"]:
            n = self.nodes[eid]
            idx[self._route_key(n["route"])].append(eid)
        self._ep_index = idx

    def find_endpoints(self, endpoint):
        if self._ep_index is None:
            self._build_ep_index()
        q = (endpoint or "").strip()
        if q.startswith("endpoint:"):
            q = q[9:]
        if q in self.nodes and q.startswith("endpoint:") is False and ("endpoint:" + q) in self.nodes:
            return ["endpoint:" + q]
        if "endpoint:" + q in self.nodes:
            return ["endpoint:" + q]
        verb = None
        m = re.match(r"^(GET|POST|PUT|DELETE|PATCH)\s+(.*)$", q, re.I)
        if m:
            verb, q = m.group(1).upper(), m.group(2)
        key = self._route_key(q)
        hits = list(self._ep_index.get(key, []))
        if not hits and key:
            hits = [e for k, v in self._ep_index.items() if k.endswith("/" + key) for e in v]
        if not hits:
            parts = key.split("/")
            if len(parts) >= 2:
                hits = list(self._ep_index.get("/".join(parts[:2]), [])) if len(parts) > 2 else []
            if not hits and parts and parts[-1]:
                act = parts[-1]
                hits = [e for e in self.by_type["endpoint"] if self.nodes[e]["action"].lower() == act]
        if verb:
            hits = [h for h in hits if self.nodes[h]["http_method"] == verb] or hits
        return hits

    # ------------------------------------------------------------------ forward core
    def _closure(self, starts, include_backups=False):
        """BFS over db objects from `starts` (always shown). Returns routines/reads/writes/triggers with parents."""
        parent, depth, order = {}, {}, []
        reads, writes, trigs = {}, {}, {}
        dq = deque()
        for s in starts:
            if s not in depth:
                depth[s] = 0
                parent[s] = None
                dq.append(s)
        while dq:
            cur = dq.popleft()
            n = self.nodes[cur]
            if n["type"] in ROUTINES or n["type"] == "trigger":
                order.append(cur)
                for ty in ("calls", "uses"):
                    for t, _ in self.out[cur].get(ty, []):
                        if t not in depth and self._ok(t, include_backups):
                            depth[t] = depth[cur] + 1
                            parent[t] = cur
                            dq.append(t)
                for t, _ in self.out[cur].get("reads", []):
                    if self._ok(t, include_backups) and t not in reads:
                        reads[t] = cur
                for t, _ in self.out[cur].get("writes", []):
                    if not self._ok(t, include_backups):
                        continue
                    if t not in writes:
                        writes[t] = cur
                        for tr, _ in self.out[t].get("fires", []):
                            if tr not in depth and not self.nodes[tr].get("disabled"):
                                trigs[tr] = t
                                depth[tr] = depth[cur] + 1
                                parent[tr] = cur
                                dq.append(tr)
        return {"order": order, "parent": parent, "depth": depth, "reads": reads, "writes": writes, "trigs": trigs}

    def _method_closure(self, start):
        seen, order, parent = {start}, [], {start: None}
        dq = deque([start])
        while dq:
            cur = dq.popleft()
            order.append(cur)
            for t, _ in self.out[cur].get("calls", []):
                if t not in seen and self.nodes[t]["type"] == "method":
                    seen.add(t)
                    parent[t] = cur
                    dq.append(t)
        return order, parent

    def _trace_one(self, eid, include_backups, with_callers=True):
        ck = ("ep", eid, include_backups, with_callers)
        if ck in self._cache:
            return self._cache[ck]
        n = self.nodes[eid]
        order, mparent = self._method_closure(eid)
        starts, code_tables, code_rw = [], {}, {"reads": {}, "writes": {}}
        methods = []
        for cur in order:
            cn = self.nodes[cur]
            if cn["type"] == "method":
                methods.append(self._item(cur, "controller -> manager call chain: " + self._path_str(mparent, cur)))
            for t, at in self.out[cur].get("invokes", []):
                if t not in starts:
                    starts.append(t)
            for t, at in self.out[cur].get("touches", []):
                code_tables.setdefault(t, cur)
            for ty in ("reads", "writes"):
                for t, at in self.out[cur].get(ty, []):
                    code_rw[ty].setdefault(t, cur)
        cl = self._closure(starts, include_backups)
        res = {"endpoint": n["name"], "endpoint_id": eid, "http_method": n["http_method"], "route": n["route"],
               "file": n["file"], "line": n["line"]}
        if with_callers:
            callers, by_app = [], defaultdict(list)
            for s, at in self.inn[eid].get("invokes", []):
                sn = self.nodes[s]
                it = {"app": sn["app"], "screen": sn["file"], "routes": sn["routes"][:5],
                      "file": (at or {}).get("file", sn["file"]), "line": (at or {}).get("line"),
                      "evidence": f"{sn['app']} screen {sn['file']} calls this endpoint"
                                  + (f" at {at['file']}:{at['line']}" if at and at.get("line") else "")}
                callers.append(it)
                by_app[sn["app"]].append(sn["file"])
            callers.sort(key=lambda x: (x["app"], x["screen"]))
            res["callers"] = callers
            res["callers_by_app"] = {a: sorted(set(v)) for a, v in sorted(by_app.items())}
        res["methods"] = methods
        dbo = {"procs": [], "funcs": [], "views": []}
        key = {"proc": "procs", "func": "funcs", "view": "views"}
        for nid in cl["order"]:
            t = self.nodes[nid]["type"]
            if t in key:
                direct = cl["depth"][nid] == 0
                ev = ("invoked directly by code" if direct else "reached via " + self._path_str(cl["parent"], nid))
                dbo[key[t]].append(self._item(nid, ev, depth=cl["depth"][nid]))
        res["db_objects"] = dbo

        def tab(tid, via_from, kind):
            tn = self.nodes[tid]
            return self._item(tid, f"{kind} by {self._path_str(cl['parent'], via_from)}" if via_from in cl["parent"]
                              else f"{kind} by {self.nodes[via_from].get('name')}")
        res["tables_read"] = [tab(t, v, "read") for t, v in sorted(cl["reads"].items())]
        res["tables_written"] = [tab(t, v, "written") for t, v in sorted(cl["writes"].items())]
        for ty, lst in (("reads", res["tables_read"]), ("writes", res["tables_written"])):
            have = {x["id"] for x in lst}
            for t, src in code_rw[ty].items():
                if t not in have and self._ok(t, include_backups):
                    lst.append(self._item(t, f"{ty[:-1]} by inline SQL in {self.nodes[src]['name']}"))
        res["tables_code_access"] = [
            self._item(t, f"accessed via ORM/ADO in {self.nodes[m]['name']} (read/write not determined)")
            for t, m in sorted(code_tables.items()) if self._ok(t, include_backups)]
        res["triggers"] = []
        for tr, tbl in sorted(cl["trigs"].items()):
            tn = self.nodes[tr]
            res["triggers"].append(self._item(
                tr, f"fires on {self.nodes[tbl]['name']} (written by {self.nodes[cl['parent'][tr]]['name']}; "
                    f"chain {self._path_str(cl['parent'], cl['parent'][tr])})",
                table=self.nodes[tbl]["name"], events=tn.get("events"), disabled=tn.get("disabled"),
                writes=[self.nodes[t]["name"] for t, _ in self.out[tr].get("writes", [])],
                calls=[self.nodes[t]["name"] for t, _ in self.out[tr].get("calls", [])]))
        files = [{"path": n["file"], "line": n["line"], "what": "controller action"}]
        for m in methods:
            if m.get("file"):
                files.append({"path": m["file"], "line": m.get("line"), "what": "manager method " + m["name"]})
        for grp in dbo.values():
            for x in grp:
                if x.get("file"):
                    files.append({"path": x["file"], "line": x.get("line"), "what": x["name"]})
        res["files"] = files
        res["error_messages"] = [e["text"] for e in self.errors if e["node"] in set(order)]
        self._cache[ck] = res
        return res

    # ------------------------------------------------------------------ public: forward
    def trace_endpoint(self, endpoint, include_backups=False):
        hits = self.find_endpoints(endpoint)
        if not hits:
            toks = tokenize(endpoint)
            sug = [self.nodes[e]["name"] for e in self.by_type["endpoint"]
                   if toks and any(t in fold(self.nodes[e]["route"]) for t in toks)][:8]
            return {"endpoint": endpoint, "error": "endpoint not found", "suggestions": sug}
        # prefer the endpoint that has callers
        hits.sort(key=lambda e: (-len(self.inn[e].get("invokes", [])), e))
        res = dict(self._trace_one(hits[0], include_backups))
        if len(hits) > 1:
            res["other_matches"] = [self.nodes[h]["name"] for h in hits[1:]]
        return res

    def trace_screen(self, app, screen_or_route, include_backups=False):
        q = screen_or_route.strip()
        ql = q.lower()
        screens = [s for s in self.by_type["screen"] if self.nodes[s]["app"] == app]
        routes = [r for r in self.by_type["route"] if self.nodes[r]["app"] == app]
        found, how = [], ""
        for s in screens:
            if self.nodes[s]["file"].lower() == ql:
                found.append(s)
        if found:
            how = "exact screen file"
        if not found:
            for r in routes:
                rp = (self.nodes[r]["path"] or "").lower()
                if rp == ql or rp == "/" + ql.strip("/"):
                    for t, _ in self.out[r].get("renders", []):
                        found.append(t)
                    found += []
                    how = "route " + self.nodes[r]["path"]
                    rr = r
                    break
        if not found:
            sub = [s for s in screens if ql in self.nodes[s]["file"].lower()]
            sub.sort(key=lambda s: (len(self.nodes[s]["file"]), s))
            found, how = sub[:5], "file substring"
        if not found:
            for s in screens:
                if ql in " ".join(self.nodes[s]["routes"]).lower():
                    found.append(s)
            how = "screen route substring"
            found = found[:5]
        res = {"app": app, "query": q, "matched_by": how, "screens": [], "endpoints": []}
        if not found:
            res["error"] = "no screen/route matched"
            return res
        seen, tabs_r, tabs_w, trigs = set(), {}, {}, {}
        dbo = {"procs": {}, "funcs": {}, "views": {}}
        for s in found:
            sn = self.nodes[s]
            res["screens"].append(self._item(s, f"screen in {app}", routes=sn["routes"],
                                             reachable_from_routes=sn["reachable_from_routes"][:10]))
            for e, at in self.out[s].get("invokes", []):
                if e in seen:
                    continue
                seen.add(e)
                tr = self._trace_one(e, include_backups, with_callers=False)
                res["endpoints"].append({"endpoint": tr["endpoint"], "call_site": at, "file": tr["file"],
                                         "line": tr["line"], "trace": tr,
                                         "evidence": f"{sn['file']} -> {tr['endpoint']}"})
                for x in tr["tables_read"]:
                    tabs_r.setdefault(x["name"], x)
                for x in tr["tables_written"]:
                    tabs_w.setdefault(x["name"], x)
                for x in tr["triggers"]:
                    trigs.setdefault(x["name"], x)
                for k in dbo:
                    for x in tr["db_objects"][k]:
                        dbo[k].setdefault(x["name"], x)
        res["tables_read"] = sorted(tabs_r.values(), key=lambda x: x["name"])
        res["tables_written"] = sorted(tabs_w.values(), key=lambda x: x["name"])
        res["triggers"] = sorted(trigs.values(), key=lambda x: x["name"])
        res["db_objects"] = {k: sorted(v.values(), key=lambda x: x["name"]) for k, v in dbo.items()}
        return res

    # ------------------------------------------------------------------ db object resolution
    def resolve_db(self, name):
        q = name.strip().replace("[", "").replace("]", "")
        if q in self.nodes and self.nodes[q]["type"] in DB_TYPES:
            return q, None
        m = re.match(r"^(proc|func|view|table|trigger):(.*)$", q)
        if m and q in self.nodes:
            return q, None
        ql = q.lower()
        cands = self._lower.get(ql)
        if not cands and "." not in ql:
            cands = self._lower.get("dbo." + ql)
        if cands:
            cands = sorted(cands, key=lambda c: (self.nodes[c].get("is_backup_or_copy", False), c))
            return cands[0], None
        # table.column
        if "." in ql:
            head, col = ql.rsplit(".", 1)
            c2 = self._lower.get(head) or self._lower.get("dbo." + head)
            if c2:
                t = c2[0]
                cols = [c for c in self.nodes[t].get("columns", [])]
                if col in [c.lower() for c in cols]:
                    return t, [c for c in cols if c.lower() == col][0]
                return t, "?" + col
        # trigger name
        for tid in self.by_type["trigger"]:
            if tid.lower().endswith("." + ql) or tid.lower() == "trigger:" + ql:
                return tid, None
        # unqualified match in any schema
        c3 = [nid for k, v in self._lower.items() if k.endswith("." + ql) for nid in v]
        if c3:
            c3.sort(key=lambda c: (self.nodes[c].get("is_backup_or_copy", False), c))
            return c3[0], None
        return None, None

    def _reverse(self, start, include_backups=False):
        """Reverse BFS from a DB object. Returns parent map + collected sets."""
        ck = ("rev", start, include_backups)
        if ck in self._cache:
            return self._cache[ck]
        parent = {start: None}
        first = {start: None}
        kind = {start: "self"}
        dq = deque([(start, False)])
        seen_w = {start}
        res = {"endpoints": [], "screens": [], "methods": [], "db": []}
        while dq:
            cur, write_only = dq.popleft()
            cn = self.nodes[cur]
            t = cn["type"]
            ins = []
            if t == "table":
                if not write_only:
                    ins += [(s, "reads") for s, _ in self.inn[cur].get("reads", [])]
                ins += [(s, "writes") for s, _ in self.inn[cur].get("writes", [])]
                ins += [(s, "touches") for s, _ in self.inn[cur].get("touches", [])]
            elif t in ROUTINES:
                for ty in ("calls", "uses", "invokes"):
                    ins += [(s, ty) for s, _ in self.inn[cur].get(ty, [])]
            elif t == "trigger":
                ins += [(s, "fires") for s, _ in self.inn[cur].get("fires", [])]
                ins += [(s, "calls") for s, _ in self.inn[cur].get("calls", [])]
            elif t == "method":
                ins += [(s, "calls") for s, _ in self.inn[cur].get("calls", [])]
            elif t == "endpoint":
                ins += [(s, "invokes") for s, _ in self.inn[cur].get("invokes", [])]
            for s, ty in ins:
                if s in parent:
                    continue
                if not self._ok(s, include_backups) and self.nodes[s]["type"] in DB_TYPES:
                    continue
                parent[s] = cur
                kind[s] = ty
                first[s] = s if cur == start or first.get(cur) is None else first[cur]
                st = self.nodes[s]["type"]
                if st == "table":      # arrived via fires: only writers of that table matter
                    dq.append((s, True))
                else:
                    dq.append((s, False))
                    if st == "endpoint":
                        res["endpoints"].append(s)
                    elif st == "screen":
                        res["screens"].append(s)
                    elif st == "method":
                        res["methods"].append(s)
                    else:
                        res["db"].append(s)
        res["parent"], res["first"], res["kind"] = parent, first, kind
        self._cache[ck] = res
        return res

    def _rev_path(self, parent, nid):
        chain, cur = [], nid
        while cur is not None:
            chain.append(self.nodes[cur].get("name") or cur)
            cur = parent[cur]
        return " -> ".join(chain)

    def _mentions(self, nid, col):
        n = self.nodes[nid]
        p = n.get("file")
        if not p or n["type"] not in ROUTINES + ("trigger",) and n["type"] != "table":
            return None
        try:
            ck = ("src", p)
            if ck not in self._cache:
                with open(p, encoding="utf-8", errors="replace") as f:
                    self._cache[ck] = f.read().lower()
            return re.search(r"(?<![\w])\[?" + re.escape(col.lower()) + r"\]?(?![\w])", self._cache[ck]) is not None
        except OSError:
            return None

    def trace_db_object(self, name, include_backups=False, limit=60):
        nid, col = self.resolve_db(name)
        if not nid:
            return {"name": name, "error": "db object not found"}
        n = self.nodes[nid]
        colname = col[1:] if col and col.startswith("?") else col
        rev = self._reverse(nid, include_backups or bool(n.get("is_backup_or_copy")))
        res = {"name": n["name"], "id": nid, "type": n["type"], "regions": n.get("regions"),
               "is_backup_or_copy": n.get("is_backup_or_copy", False), "file": n.get("file"), "line": n.get("line")}
        if n["type"] == "table":
            res["domain"] = n.get("domain")
            res["columns"] = n.get("columns", [])
        if col:
            res["column"] = {"name": colname, "exists": not col.startswith("?"),
                             "note": "column-level usage is approximated by a text search in each object's source"}

        def lst(ids, kind):
            out = []
            for s in ids:
                it = self._item(s, f"{kind} {n['name']}" if kind else "")
                if colname:
                    it["mentions_column"] = self._mentions(s, colname)
                out.append(it)
            if colname:
                out.sort(key=lambda x: (x["mentions_column"] is not True, x["name"]))
            else:
                out.sort(key=lambda x: x["name"])
            return out
        bk = lambda ids: [s for s in ids if self._ok(s, include_backups)]
        if n["type"] == "table":
            res["readers"] = lst(bk([s for s, _ in self.inn[nid].get("reads", [])]), "reads")
            res["writers"] = lst(bk([s for s, _ in self.inn[nid].get("writes", [])]), "writes")
            res["code_access"] = [self._item(s, "ORM/ADO access (read/write unknown)")
                                  for s, _ in self.inn[nid].get("touches", [])]
            res["triggers_on_table"] = [self._item(t, "trigger on this table", events=self.nodes[t].get("events"),
                                                   disabled=self.nodes[t].get("disabled"),
                                                   writes=[self.nodes[x]["name"] for x, _ in self.out[t].get("writes", [])])
                                        for t, _ in self.out[nid].get("fires", [])]
        elif n["type"] == "trigger":
            res["table"] = n["table"]
            res["writes"] = [self._item(t, "written by trigger") for t, _ in self.out[nid].get("writes", [])]
            res["calls"] = [self._item(t, "called by trigger") for t, _ in self.out[nid].get("calls", [])]
        else:
            res["called_by"] = lst(bk([s for s, ty in
                                       [(a, "calls") for a, _ in self.inn[nid].get("calls", [])] +
                                       [(a, "uses") for a, _ in self.inn[nid].get("uses", [])]]), "calls")
            res["invoked_by_code"] = [self._item(s, "code invokes this object") for s, _ in self.inn[nid].get("invokes", [])]
            cl = self._closure([nid], include_backups)
            res["tables_read"] = [self._item(t, "read by " + self._path_str(cl["parent"], v)) for t, v in sorted(cl["reads"].items())]
            res["tables_written"] = [self._item(t, "written by " + self._path_str(cl["parent"], v)) for t, v in sorted(cl["writes"].items())]
            res["triggers"] = [self._item(t, "fires on " + self.nodes[b]["name"]) for t, b in sorted(cl["trigs"].items())]
        eps = []
        for e in rev["endpoints"]:
            it = {"endpoint": self.nodes[e]["name"], "id": e, "file": self.nodes[e]["file"], "line": self.nodes[e]["line"],
                  "evidence": self._rev_path(rev["parent"], e) + f" ({n['name']})"}
            cur, via_trig = e, False
            while cur is not None:
                if self.nodes[cur]["type"] == "trigger":
                    via_trig = True
                cur = rev["parent"][cur]
            it["via_trigger"] = via_trig
            if colname:
                fm = rev["first"].get(e)
                it["mentions_column"] = self._mentions(fm, colname) if fm else None
            eps.append(it)
        if colname:
            eps.sort(key=lambda x: (x.get("mentions_column") is not True, x["endpoint"]))
        else:
            eps.sort(key=lambda x: (x["via_trigger"], len(x["evidence"]), x["endpoint"]))
        res["n_endpoints"] = len(eps)
        res["n_endpoints_not_via_trigger"] = sum(1 for x in eps if not x["via_trigger"])
        res["endpoints"] = eps[:limit]
        by_app, scr = defaultdict(list), []
        for s in rev["screens"]:
            sn = self.nodes[s]
            by_app[sn["app"]].append(sn["file"])
            scr.append({"app": sn["app"], "screen": sn["file"], "file": sn["file"], "line": 1, "routes": sn["routes"][:3],
                        "evidence": self._rev_path(rev["parent"], s) + f" ({n['name']})"})
        scr.sort(key=lambda x: (x["app"], x["screen"]))
        res["n_screens"] = len(scr)
        res["screens_by_app"] = {a: len(set(v)) for a, v in sorted(by_app.items())}
        res["apps"] = sorted(by_app)
        res["screens"] = scr[:limit * 3]
        res["methods"] = [self._item(m, "") for m in rev["methods"][:limit]]
        return res

    # ------------------------------------------------------------------ error search
    def _error_index(self):
        if "errs" in self._cache:
            return self._cache["errs"]
        recs = []
        for e in self.errors:
            raw = e["text"]
            pieces = [fold(p) for p in re.split(r"\{[^}]*\}|%[sdi]|\{\d+\}", raw)]
            pieces = [re.sub(r"\s+", " ", p).strip(" .:!?\"'") for p in pieces]
            core = max(pieces, key=len) if pieces else ""
            norm = " ".join(p for p in pieces if p)
            recs.append({"src": "err", "e": e, "norm": norm, "core": core, "toks": set(tokenize(norm, False)),
                         "pat": re.compile(".*?".join(re.escape(p) for p in pieces), re.S) if len(pieces) > 1 else None})
        i18n = []
        for app, d in self.i18n.items():
            ku = d.get("key_usage", {})
            for k, (en, tr) in d["texts"].items():
                for lang, txt in (("en", en), ("tr", tr)):
                    if not txt or len(txt) < 3:
                        continue
                    pieces = [re.sub(r"\s+", " ", fold(p)).strip(" .:!?\"'") for p in re.split(r"\{\{[^}]*\}\}|\{\d+\}|%[sd]|@\w+", txt)]
                    norm = " ".join(p for p in pieces if p)
                    i18n.append({"src": "i18n", "app": app, "key": k, "lang": lang, "text": txt, "norm": norm,
                                 "core": max(pieces, key=len) if pieces else "", "toks": set(tokenize(norm, False)),
                                 "pat": None, "screens": ku.get(k, [])})
        self._cache["errs"] = (recs, i18n)
        return recs, i18n

    @staticmethod
    def _score_text(qn, qtoks, r):
        if not r["norm"]:
            return 0.0
        if r["norm"] == qn:
            return 1.0
        if r["pat"] is not None and r["pat"].fullmatch(qn):
            return 0.97
        core = r["core"]
        if len(qn) >= 10 and qn in r["norm"]:
            return 0.9
        if len(core) >= 16 and core in qn:
            return 0.88 * min(1.0, 0.5 + len(core) / max(len(qn), 1))
        if qtoks and r["toks"]:
            inter = len(qtoks & r["toks"])
            if inter >= 2:
                j = inter / len(qtoks | r["toks"])
                if j >= 0.45:
                    return 0.8 * j
        return 0.0

    def search_error_text(self, text, limit=10, include_backups=False):
        qn = _norm_text(re.sub(r"\b\d{3,}\b", " ", text) if len(text) > 200 else text)
        qn = re.sub(r"\s+", " ", qn)
        qtoks = set(tokenize(qn, False))
        recs, i18n = self._error_index()
        hits = []
        for r in recs:
            sc = self._score_text(qn, qtoks, r)
            if sc <= 0:
                continue
            e = r["e"]
            nid = e["node"]
            if not self._ok(nid, include_backups) and self.nodes[nid]["type"] in DB_TYPES:
                continue
            hits.append((sc, 0, r))
        seen_i = set()
        for r in i18n:
            sc = self._score_text(qn, qtoks, r)
            if sc <= 0:
                continue
            hits.append((sc * 0.97, 1, r))
        hits.sort(key=lambda x: (-x[0], x[1]))
        out, used = [], set()
        for sc, _, r in hits:
            if len(out) >= limit:
                break
            if r["src"] == "err":
                e = r["e"]
                nid = e["node"]
                n = self.nodes[nid]
                key = (nid, r["norm"])
                if key in used:
                    continue
                used.add(key)
                rev = self._reverse(nid, include_backups) if n["type"] != "endpoint" else None
                if n["type"] == "endpoint":
                    eps, scr = [nid], [s for s, _ in self.inn[nid].get("invokes", [])]
                    parent = {nid: None}
                else:
                    eps, scr, parent = rev["endpoints"], rev["screens"], rev["parent"]
                what = {"db_procedure": "stored procedure", "db_function": "function", "db_view": "view",
                        "db_trigger": "trigger", "backend_method": "backend method", "backend_endpoint": "controller action"}[e["kind"]]
                out.append({"kind": e["kind"], "score": round(sc, 3), "text": e["text"], "origin": n["name"], "origin_id": nid,
                            "file": e["file"], "line": e["line"],
                            "evidence": f"{what} {n['name']} {e['how']}: \"{e['text'][:120]}\"",
                            "endpoints": [{"endpoint": self.nodes[x]["name"], "file": self.nodes[x]["file"], "line": self.nodes[x]["line"]}
                                          for x in sorted(eps, key=lambda x: len(self._rev_path(parent, x)))[:8]],
                            "n_endpoints": len(eps),
                            "screens": [{"app": self.nodes[s]["app"], "screen": self.nodes[s]["file"]} for s in sorted(scr)[:10]],
                            "n_screens": len(scr)})
            else:
                key = ("i18n", r["norm"])
                if key in used:
                    continue
                used.add(key)
                group = [x for (s2, _, x) in hits if x["src"] == "i18n" and x["norm"] == r["norm"]]
                scr, nkeys, eps = [], [], []
                for x in group:
                    ns, k = x["key"].split("::", 1)
                    nkeys.append(f"{x['app']}/{x['key']}[{x['lang']}]")
                    for sf in x["screens"][:10]:
                        sid = f"screen:{x['app']}:{sf}"
                        line = None
                        if sid in self.nodes:
                            for a, b, c in self.nodes[sid]["i18n_keys"]:
                                if a == ns and b == k:
                                    line = c
                                    break
                        ep = [self.nodes[e]["name"] for e, _ in self.out.get(sid, {}).get("invokes", [])][:5]
                        scr.append({"app": x["app"], "screen": sf, "file": sf, "line": line, "endpoints": ep})
                seen_s, uniq = set(), []
                for d in scr:
                    if (d["app"], d["screen"]) not in seen_s:
                        seen_s.add((d["app"], d["screen"]))
                        uniq.append(d)
                scr = uniq
                out.append({"kind": "i18n", "score": round(sc, 3), "text": r["text"],
                            "origin": "i18n " + ", ".join(nkeys[:3]), "origin_id": f"i18n:{r['app']}:{r['key']}",
                            "file": None, "line": None,
                            "evidence": f"UI text \"{r['text'][:120]}\" is i18n key(s) {', '.join(nkeys[:4])}; used by {len(scr)} screen(s)",
                            "endpoints": [], "n_endpoints": 0, "screens": scr[:12], "n_screens": len(scr)})
        return out

    # ------------------------------------------------------------------ free text
    def _bm25(self):
        if self._bm is not None:
            return self._bm
        docs = {}   # id -> Counter

        def add(did, parts):
            c = Counter()
            for w, text in parts:
                for t in tokenize(text):
                    c[t] += w
            docs[did] = c
        for sid in self.by_type["screen"]:
            n = self.nodes[sid]
            parts = [(3, n["file"]), (2, " ".join(n["routes"]))]
            texts = self.i18n.get(n["app"], {}).get("texts", {})
            for ns, k, _ in n["i18n_keys"][:150]:
                t = texts.get(f"{ns}::{k}")
                if t:
                    parts.append((1, t[0]))
                    parts.append((0.6, t[1]))
                parts.append((0.5, k))
            for e, _ in self.out[sid].get("invokes", []):
                parts.append((1.5, self.nodes[e]["route"]))
            add(sid, parts)
        for eid in self.by_type["endpoint"]:
            n = self.nodes[eid]
            parts = [(3, n["route"]), (1, " ".join(m for m, _ in self.out[eid].get("calls", [])))]
            for s, _ in self.inn[eid].get("invokes", []):
                parts.append((0.5, self.nodes[s]["file"]))
            for e in self.errors:
                pass
            add(eid, parts)
        errs = defaultdict(list)
        for e in self.errors:
            errs[e["node"]].append(e["text"])
        for eid, texts in errs.items():
            if eid in docs:
                for t in texts:
                    for tok in tokenize(t):
                        docs[eid][tok] += 1
            else:
                add(eid, [(1, " ".join(texts))])
        for nid in self.by_type["proc"] + self.by_type["func"] + self.by_type["view"] + self.by_type["table"]:
            if nid in docs:
                continue
            n = self.nodes[nid]
            if n.get("is_backup_or_copy") or n.get("unresolved"):
                continue
            parts = [(3, n["name"])]
            if n["type"] == "table":
                parts.append((0.5, n.get("description", "")))
                parts.append((0.3, " ".join(n.get("columns", [])[:60])))
            add(nid, parts)
        df = Counter()
        for c in docs.values():
            df.update(c.keys())
        N = len(docs)
        avg = sum(sum(c.values()) for c in docs.values()) / max(N, 1)
        inv = defaultdict(list)
        for did, c in docs.items():
            for t in c:
                inv[t].append(did)
        self._bm = {"docs": docs, "df": df, "N": N, "avg": avg, "inv": inv,
                    "len": {d: sum(c.values()) for d, c in docs.items()}}
        return self._bm

    def search_text(self, text, app=None, limit=10, include_backups=False, types=None):
        bm = self._bm25()
        qt = tokenize(text)
        if not qt:
            return []
        scores = defaultdict(float)
        matched = defaultdict(list)
        for t in set(qt):
            if t not in bm["inv"]:
                continue
            idf = math.log(1 + (bm["N"] - bm["df"][t] + 0.5) / (bm["df"][t] + 0.5))
            for did in bm["inv"][t]:
                tf = bm["docs"][did][t]
                dl = bm["len"][did]
                s = idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * dl / bm["avg"]))
                scores[did] += s
                matched[did].append(t)
        cands = sorted(scores, key=lambda d: -scores[d])[:limit * 8 + 40]
        out = []
        top = scores[cands[0]] if cands else 1
        for did in cands:
            n = self.nodes[did]
            t = n["type"]
            if types and t not in types:
                continue
            if t in DB_TYPES and not self._ok(did, include_backups):
                continue
            apps = None
            if app:
                if t == "screen":
                    if n["app"] != app:
                        continue
                elif t == "endpoint":
                    apps = {self.nodes[s]["app"] for s, _ in self.inn[did].get("invokes", [])}
                    if app not in apps:
                        continue
                else:
                    r = self._reverse(did, include_backups)
                    if not any(self.nodes[s]["app"] == app for s in r["screens"]):
                        continue
            it = self._item(did, "matched terms: " + ", ".join(sorted(set(matched[did]))[:8]),
                            score=round(scores[did] / top, 3), raw_score=round(scores[did], 3))
            if t == "screen":
                it["app"] = n["app"]
                it["routes"] = n["routes"][:3]
                it["endpoints"] = [self.nodes[e]["name"] for e, _ in self.out[did].get("invokes", [])][:6]
            elif t == "endpoint":
                it["apps"] = sorted(apps if apps is not None else {self.nodes[s]["app"] for s, _ in self.inn[did].get("invokes", [])})
            out.append(it)
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------------ stats
    def stats(self):
        st = {"nodes": dict(Counter(n["type"] for n in self.nodes.values())),
              "edges": dict(Counter(ty for o in self.out.values() for ty, l in o.items() for _ in l))}
        eps = self.by_type["endpoint"]
        reach = reach_rw = with_callers = with_db = 0
        for e in eps:
            tr = self._trace_one(e, False)
            if tr["tables_read"] or tr["tables_written"] or tr["tables_code_access"]:
                reach += 1
            if tr["tables_read"] or tr["tables_written"]:
                reach_rw += 1
            if tr["db_objects"]["procs"] or tr["db_objects"]["funcs"] or tr["db_objects"]["views"]:
                with_db += 1
            if tr["callers"]:
                with_callers += 1
        st["endpoints"] = {"total": len(eps), "reach_ge1_table_any": reach, "reach_ge1_table_via_sql_objects": reach_rw,
                           "with_sp_func_view": with_db, "with_screen_callers": with_callers,
                           "pct_reach_table_any": round(100 * reach / len(eps), 1),
                           "pct_reach_table_via_sql": round(100 * reach_rw / len(eps), 1)}
        procs = [p for p in self.by_type["proc"] if not self.nodes[p].get("unresolved") and not self.nodes[p].get("is_backup_or_copy")]
        res = sum(1 for p in procs if self.out[p].get("reads") or self.out[p].get("writes"))
        res_tr = sum(1 for p in procs if self._closure([p])["reads"] or self._closure([p])["writes"])
        st["procs"] = {"non_backup": len(procs), "with_direct_table_edges": res,
                       "pct_direct": round(100 * res / len(procs), 1),
                       "with_table_edges_transitive": res_tr, "pct_transitive": round(100 * res_tr / len(procs), 1)}
        scr = self.by_type["screen"]
        st["screens"] = {"total": len(scr), "with_endpoint": sum(1 for s in scr if self.out[s].get("invokes"))}
        st["unresolved_db_refs"] = {t: sum(1 for n in self.nodes.values() if n.get("unresolved") and n["type"] == t)
                                    for t in ("proc", "func", "table")}
        st["error_records"] = dict(Counter(e["kind"] for e in self.errors))
        return st
