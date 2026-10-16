"""Join the code map (knowledge/codemap) with the DB schema catalog (knowledge/schema) into one graph.

Output: knowledge/graph/graph.json (+ stats printed). Python 3 stdlib only, deterministic.
Usage: PYTHONIOENCODING=utf-8 python tools/graph/build.py [--codemap DIR] [--schema DIR] [--out DIR]
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APPS = ["portal", "crm", "partner", "technician"]
SCHEMA_PRIORITY = ["Operation", "Catalog", "dbo", "Integration", "Communication", "Log", "Inventory", "Email2Case",
                   "Gateway", "Work", "Security", "Payment"]
TYPE_PREFIX = {"procedure": "proc", "function": "func", "view": "view", "table": "table"}


def jload(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class Builder:
    def __init__(self, codemap, schema):
        self.cm, self.sc = codemap, schema
        self.nodes = {}          # id -> attrs
        self.edges = []          # (src, dst, type, attrs|None)
        self._edge_seen = set()
        self.errors = []         # error origin records
        self.objects = jload(os.path.join(schema, "objects.json"))
        self.domains = jload(os.path.join(schema, "domains.json")).get("tables", {})
        self.lower = {k.lower(): k for k in self.objects}
        self.tables_by_name = defaultdict(list)
        for k, r in self.objects.items():
            if r["type"] == "table":
                self.tables_by_name[k.split(".", 1)[1].lower()].append(k)
        self.stats = Counter()
        self._filecache = {}

    # ---- helpers
    def add_node(self, nid, **attrs):
        if nid not in self.nodes:
            self.nodes[nid] = attrs
        else:
            self.nodes[nid].update(attrs)
        return nid

    def add_edge(self, s, t, typ, **attrs):
        key = (s, t, typ)
        if key in self._edge_seen:
            return
        self._edge_seen.add(key)
        self.edges.append((s, t, typ, attrs or None))

    def src_file(self, rec):
        sf = rec.get("source_files") or {}
        return sf.get("EU") or sf.get("APAC")

    def _lines(self, path):
        if path not in self._filecache:
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    self._filecache[path] = f.read().lower().split("\n")
            except OSError:
                self._filecache[path] = []
        return self._filecache[path]

    def find_line(self, path, text):
        if not path:
            return None
        chunk = re.split(r"\{[^}]*\}", text)
        chunk = max(chunk, key=len).strip().rstrip(".").lower()[:40]
        if len(chunk) < 4:
            return None
        for i, ln in enumerate(self._lines(path), 1):
            if chunk in ln:
                return i
        return None

    # ---- DB side
    def db_node_id(self, key):
        r = self.objects[key]
        p = TYPE_PREFIX.get(r["type"])
        return f"{p}:{key}" if p else None

    def build_db(self):
        for key, r in sorted(self.objects.items()):
            nid = self.db_node_id(key)
            if not nid:
                continue
            a = {"name": key, "type": TYPE_PREFIX[r["type"]], "regions": r.get("regions", []),
                 "is_backup_or_copy": bool(r.get("is_backup_or_copy")),
                 "file": self.src_file(r), "line": 1}
            if r["type"] == "table":
                a["columns"] = [c["name"] for c in r.get("columns", [])]
                d = self.domains.get(key)
                if d:
                    a["domain"] = d.get("domain")
                if r.get("description"):
                    a["description"] = r["description"][:200]
            self.add_node(nid, **a)
        for key, r in sorted(self.objects.items()):
            nid = self.db_node_id(key)
            if not nid:
                continue
            if r["type"] in ("procedure", "function", "view"):
                self.link_routine(nid, key, r)
            if r["type"] == "table":
                self.link_triggers(nid, key, r)

    def resolve_known(self, name):
        k = self.lower.get(name.lower())
        return k

    def link_routine(self, nid, key, r):
        written = set(r.get("written_tables") or [])
        called = set(r.get("called_procs") or [])
        for t in sorted(written):
            tid = self._tid(t)
            if tid:
                self.add_edge(nid, tid, "writes")
        for t in sorted(called):
            tid = self._tid(t)
            if tid:
                self.add_edge(nid, tid, "calls")
        for t in sorted(r.get("referenced_objects") or []):
            if t == key or t in written or t in called:
                continue
            tid = self._tid(t)
            if not tid:
                continue
            typ = self.nodes[tid]["type"]
            if typ == "table":
                self.add_edge(nid, tid, "reads")
            elif typ in ("func", "view"):
                self.add_edge(nid, tid, "uses")
        path = self.src_file(r)
        for e in r.get("raises_errors") or []:
            msg = e.get("message") if isinstance(e, dict) else str(e)
            if not msg:
                continue
            self.errors.append({"kind": "db_" + r["type"], "node": nid, "text": msg, "file": path,
                                "line": self.find_line(path, msg), "how": (e.get("kind") if isinstance(e, dict) else "RAISE")})

    def _tid(self, key):
        rk = self.lower.get(key.lower()) if key not in self.objects else key
        if not rk:
            return None
        return self.db_node_id(rk)

    def link_triggers(self, tnid, tkey, r):
        path = self.src_file(r)
        for tr in r.get("triggers") or []:
            trid = f"trigger:{tkey}.{tr['name']}"
            self.add_node(trid, name=tr["name"], table=tkey, type="trigger", events=tr.get("events"),
                          timing=tr.get("timing"), disabled=bool(tr.get("disabled")),
                          regions=r.get("regions", []), is_backup_or_copy=bool(r.get("is_backup_or_copy")),
                          file=path, line=self.find_line(path, "trigger " + tr["name"]) or 1)
            self.add_edge(tnid, trid, "fires")
            wr = set(tr.get("written_tables") or [])
            cp = set(tr.get("called_procs") or [])
            for t in sorted(wr):
                x = self._tid(t)
                if x:
                    self.add_edge(trid, x, "writes")
            for t in sorted(cp):
                x = self._tid(t)
                if x:
                    self.add_edge(trid, x, "calls")
            for t in sorted(tr.get("called_objects") or []):
                if t in wr or t in cp or t == tkey:
                    continue
                x = self._tid(t)
                if x and self.nodes[x]["type"] == "table":
                    self.add_edge(trid, x, "reads")
                elif x and self.nodes[x]["type"] in ("func", "view"):
                    self.add_edge(trid, x, "uses")
            for msg in tr.get("raises_errors") or []:
                m = msg if isinstance(msg, str) else msg.get("message", "")
                if m:
                    self.errors.append({"kind": "db_trigger", "node": trid, "text": m, "file": path,
                                        "line": self.find_line(path, m), "how": "RAISERROR"})

    # ---- table-name resolution for EF / ADO names
    def resolve_table(self, name):
        name = name.strip("[]` ")
        if "." in name:
            k = self.lower.get(name.lower().replace("[", "").replace("]", ""))
            return (k, False) if k else (None, False)
        cands = []
        base = name.lower()
        variants = [base]
        if base.endswith("entity"):
            variants.append(base[:-6])
        for v in list(variants):
            v2 = re.sub(r"\d+$", "", v)
            if v2 != v:
                variants.append(v2)
        for v in list(variants):
            if v.endswith("ies"):
                variants.append(v[:-3] + "y")
            if v.endswith("es"):
                variants.append(v[:-2])
            if v.endswith("s"):
                variants.append(v[:-1])
        for v in variants:
            c = [k for k in self.tables_by_name.get(v, []) if not self.objects[k].get("is_backup_or_copy")]
            if c:
                cands = c
                break
        if not cands:
            return None, False
        if len(cands) == 1:
            return cands[0], False

        def rank(k):
            s = k.split(".")[0]
            return SCHEMA_PRIORITY.index(s) if s in SCHEMA_PRIORITY else 99
        cands.sort(key=lambda k: (rank(k), k))
        return cands[0], True

    # ---- code side
    def build_backend(self):
        be = jload(os.path.join(self.cm, "backend.json"))
        self.be = be
        methods = {}
        for m in be["methods"]:
            methods.setdefault(f"{m['class']}.{m['method']}", []).append(m)
        self.methods = methods
        for name, rows in sorted(methods.items()):
            mid = f"method:{name}"
            r0 = rows[0]
            self.add_node(mid, type="method", name=name, file=r0["file"], line=r0["line_start"],
                          line_end=r0["line_end"],
                          overloads=[{"file": r["file"], "line": r["line_start"]} for r in rows] if len(rows) > 1 else None)
        for name, rows in sorted(methods.items()):
            mid = f"method:{name}"
            for r in rows:
                for c in r.get("calls") or []:
                    if c == name:
                        continue
                    cid = self.method_node(c)
                    self.add_edge(mid, cid, "calls")
                for d in r.get("db_objects") or []:
                    self.link_code_db(mid, d, r["file"], r["line_start"])
                for em in r.get("error_messages") or []:
                    self.errors.append({"kind": "backend_method", "node": mid, "text": em, "file": r["file"],
                                        "line": r["line_start"], "how": "message literal"})
        for e in be["endpoints"]:
            eid = f"endpoint:{e['http_method']} {e['route']}"
            if eid in self.nodes:
                eid += f"@{e['line']}"
            self.add_node(eid, type="endpoint", name=f"{e['http_method']} {e['route']}", http_method=e["http_method"],
                          route=e["route"], controller=e["controller"], action=e["action"], file=e["file"],
                          line=e["line"], line_end=e.get("line_end"))
            e["_id"] = eid
            for c in e.get("calls") or []:
                self.add_edge(eid, self.method_node(c), "calls")
            for d in e.get("db_objects") or []:
                self.link_code_db(eid, d, e["file"], e["line"])
            for em in e.get("error_messages") or []:
                self.errors.append({"kind": "backend_endpoint", "node": eid, "text": em, "file": e["file"],
                                    "line": e["line"], "how": "message literal"})
        self.ep_by_key = {f"{e['http_method']} {e['route']}": e["_id"] for e in be["endpoints"]}

    def method_node(self, name):
        mid = f"method:{name}"
        if mid not in self.nodes:
            self.add_node(mid, type="method", name=name, unlisted=True)
        return mid

    def unresolved(self, typ, name, how):
        nid = f"{typ}:{name}"
        self.add_node(nid, type=typ, name=name, regions=[], is_backup_or_copy=False, unresolved=True)
        self.stats["unresolved_" + typ] += 1
        return nid

    def link_code_db(self, src, d, file, line):
        kind, name = d["kind"], (d.get("name") or "").strip()
        via = d.get("via")
        if not name:
            return
        if kind in ("sp", "function"):
            if not re.fullmatch(r"[\w\[\]]+(\.[\w\[\]]+)?", name) or name.lower().startswith("sp_"):
                self.stats["skipped_names"] += 1
                return
            nm = name.replace("[", "").replace("]", "")
            k = self.lower.get(nm.lower()) or self.lower.get(("dbo." + nm).lower())
            if k and self.objects[k]["type"] in TYPE_PREFIX:
                tid = self.db_node_id(k)
            else:
                tid = self.unresolved("proc" if kind == "sp" else "func", nm if "." in nm else "dbo." + nm, via)
            self.add_edge(src, tid, "invokes", via=via, file=file, line=line)
        elif kind == "table":
            k, amb = self.resolve_table(name)
            if not k:
                tid = self.unresolved("table", name, via)
            else:
                tid = self.db_node_id(k)
                if amb:
                    self.nodes[tid]["ambiguous_via_code"] = True
            self.add_edge(src, tid, "touches", via=via, file=file, line=line)
        elif kind == "inline_sql":
            for m in re.finditer(r"\[?(\w+)\]?\.\[?(\w+)\]?", name):
                k = self.lower.get(f"{m.group(1)}.{m.group(2)}".lower())
                if not k or self.objects[k]["type"] not in TYPE_PREFIX:
                    continue
                tid = self.db_node_id(k)
                if self.objects[k]["type"] == "table":
                    before = name[max(0, m.start() - 30):m.start()].upper()
                    w = re.search(r"(UPDATE|INSERT\s+INTO|DELETE|MERGE)\b[^;]*$", before)
                    self.add_edge(src, tid, "writes" if w else "reads", via="inline", file=file, line=line)
                else:
                    self.add_edge(src, tid, "invokes", via="inline", file=file, line=line)

    # ---- frontend
    def build_frontend(self):
        trace = jload(os.path.join(self.cm, "trace_index.json"))
        site = defaultdict(list)   # (app, screen, endpoint_key) -> [(file,line)]
        for fc in trace["frontend_calls"]:
            b = fc.get("backend")
            if not b:
                continue
            ek = f"{b['http_method']} {b['route']}"
            for c in fc["callers"]:
                for s in c.get("screens") or []:
                    site[(fc["app"], s, ek)].append((c["file"], c["line"]))
        self.i18n = {}
        for app in APPS:
            fe = jload(os.path.join(self.cm, f"frontend_{app}.json"))
            i18n = jload(os.path.join(self.cm, f"i18n_{app}.json"))
            texts = {}
            for lang in ("en", "tr"):
                for ns, d in (i18n["translations"].get(lang) or {}).items():
                    for k, v in d.items():
                        if isinstance(v, str):
                            texts.setdefault(f"{ns}::{k}", ["", ""])[0 if lang == "en" else 1] = v
            used = set()
            for s in fe["screens"]:
                for k in s["i18n"]["keys"]:
                    used.add(f"{k['ns']}::{k['key']}")
            for ku in i18n.get("key_usage", {}):
                used.add(ku)
            self.i18n[app] = {"texts": {k: v for k, v in texts.items()}, "key_usage": i18n.get("key_usage", {})}
            sid_of = {}
            for s in fe["screens"]:
                sid = f"screen:{app}:{s['file']}"
                sid_of[s["file"]] = sid
                self.add_node(sid, type="screen", app=app, file=s["file"], line=1, kind=s["kind"],
                              routes=s["routes"], reachable_from_routes=s["reachable_from_routes"][:40],
                              n_reachable_routes=len(s["reachable_from_routes"]),
                              i18n_keys=[[k["ns"], k["key"], k["line"]] for k in s["i18n"]["keys"]])
            for r in fe["routes"]:
                rid = f"route:{app}:{r['path']}"
                api = []
                for x in r.get("api_endpoints") or []:
                    xk = x if isinstance(x, str) else None
                    if xk and xk in self.ep_by_key:
                        api.append(self.ep_by_key[xk])
                self.add_node(rid, type="route", app=app, path=r["path"], component=r.get("component"),
                              file=r.get("file"), line=r.get("line"), layout=r.get("layout"),
                              closure_endpoints=api)
                if r.get("file") and r["file"] in sid_of:
                    self.add_edge(rid, sid_of[r["file"]], "renders", file=r.get("file"), line=r.get("line"))
            for sfile, eks in (trace["by_screen"].get(app) or {}).items():
                sid = sid_of.get(sfile)
                if not sid:
                    sid = f"screen:{app}:{sfile}"
                    self.add_node(sid, type="screen", app=app, file=sfile, line=1, kind="other", routes=[],
                                  reachable_from_routes=[], n_reachable_routes=0, i18n_keys=[])
                for ek in eks:
                    eid = self.ep_by_key.get(ek)
                    if not eid:
                        continue
                    sites = site.get((app, sfile, ek))
                    if sites:
                        f_, l_ = sorted(set(sites))[0]
                        self.add_edge(sid, eid, "invokes", file=f_, line=l_)
                    else:
                        self.add_edge(sid, eid, "invokes")

    def write(self, out):
        os.makedirs(out, exist_ok=True)
        ids = list(self.nodes)
        idx = {n: i for i, n in enumerate(ids)}
        edges = []
        for s, t, ty, at in self.edges:
            e = [idx[s], idx[t], ty]
            if at:
                e.append(at)
            edges.append(e)
        data = {"meta": {"format": 1, "n_nodes": len(ids), "n_edges": len(edges),
                         "edge_types": ["calls", "uses", "reads", "writes", "touches", "invokes", "fires", "renders"]},
                "nodes": self.nodes, "edges": edges, "errors": self.errors, "i18n": self.i18n}
        p = os.path.join(out, "graph.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codemap", default=os.path.join(ROOT, "knowledge", "codemap"))
    ap.add_argument("--schema", default=os.path.join(ROOT, "knowledge", "schema"))
    ap.add_argument("--out", default=os.path.join(ROOT, "knowledge", "graph"))
    a = ap.parse_args()
    b = Builder(a.codemap, a.schema)
    b.build_db()
    b.build_backend()
    b.build_frontend()
    p = b.write(a.out)
    print("wrote", p, round(os.path.getsize(p) / 1e6, 1), "MB")
    print("nodes", len(b.nodes), dict(Counter(n["type"] for n in b.nodes.values())))
    print("edges", len(b.edges), dict(Counter(e[2] for e in b.edges)))
    print("errors", len(b.errors), dict(Counter(e["kind"] for e in b.errors)))
    print("other", dict(b.stats))
    # stats via the runtime API
    sys.path.insert(0, ROOT)
    from hibot.knowledge.graph import CodeGraph
    g = CodeGraph.load(p)
    st = g.stats()
    with open(os.path.join(a.out, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1, ensure_ascii=False)
    print(json.dumps(st, indent=1))


if __name__ == "__main__":
    main()
