"""UI capability and permission gating (round 2).

Problem class: "I cannot do X", "field is not editable", "please update field Y", "button missing for our users".  The endpoint behind
the feature works; the question is WHO the UI lets use it.  In Sirius that is often decided in the calling screen by a hard-coded list
(user type ids, role ids, tenant ids), a system-config object or a feature flag - sometimes with a TODO next to it.

This module is deterministic (no LLM): given an endpoint it asks the code graph for the CALLING UI screens (`callers`), opens those
screen files through the guarded source reader and extracts compact gating snippets (file:line) near the feature:

  * lines that mention the feature token (taken from the endpoint name, e.g. SetRepairDate -> "repairdate") AND a gate pattern
    (`includes(`, UserType, RoleId, TenantId, SYSTEM_CONFIG, REACT_APP_, TODO, permission ...), plus +-2 lines of context;
  * gate patterns within +-10 lines of the call site;
  * hard-coded integer lists found on those lines (`[1218, 1233]`), labelled with the nearest environment key (Live/Test/Local) so
    the user-type ids can be resolved with the `user_type_lookup` template.

Entry points: `analyze(graph, endpoints, ticket_text)` -> dict (+ `text` for the case file), `endpoints_from_incident(...)` and
`is_capability_ticket(...)` to decide whether to run it automatically, `snippets_for_trace(...)` for an already traced endpoint.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import safety

APP_REPO = {"portal": "SiriusHeadOffice", "crm": "SiriusCallCenter", "partner": "SiriusServiceShopCallCenter",
            "technician": "SiriusTechnician"}

GATE_RE = re.compile(r"\.includes\(|\.indexOf\(|UserType|RoleId|roleId|TenantId|tenantId|SYSTEM_CONFIG|REACT_APP_|\bTODO\b|"
                     r"isAuthori[sz]ed|hasPermission|permission|canEdit|\bisAdmin\b|feature\s*flag|FeatureFlag", re.I)
INT_LIST_RE = re.compile(r"\[\s*(\d{2,6}(?:\s*,\s*\d{2,6})*)\s*,?\s*\]")
ENV_KEY_RE = re.compile(r"\b(Live|Prod(?:uction)?|Test|Local|Dev|Stage|Staging)\b\s*:")
VERBS = {"get", "set", "post", "put", "delete", "update", "insert", "save", "add", "create", "edit", "list", "by", "id", "api", "technician",
         "headoffice", "mobile", "is", "has", "check", "load", "find", "search", "for", "the", "of", "to", "with", "and"}
CAPABILITY_RE = re.compile(
    r"can'?t|cannot|can not|unable|not able|not editable|read[- ]?only|not allowed|no access|missing (?:button|field|option|menu)|"
    r"button|grey|gray|disabled|please (?:update|change|enable|allow|add|make)|(?:update|change|edit|modify|enable|allow|correct) (?:the )?[\w ]{0,25}"
    r"(?:field|date|option|button)|authority|authori[sz]|permission|yetki|düzenle|değiştir|göremi|yapamı|izin|not visible|doesn'?t show|cannot see", re.I)
MAX_FILES = 4
MAX_CHARS = 2600
MAX_LINES_PER_FILE = 30


def _tokens(name: str) -> List[str]:
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name or "")
    return [t.lower() for t in re.findall(r"[A-Za-z0-9]+", parts)]


def feature_tokens(endpoint: str) -> List[str]:
    """Content tokens of the endpoint's last route segment, e.g. 'POST api/Technician/SetRepairDate' -> ['repair', 'date']."""
    seg = [s for s in re.split(r"[/\s]+", endpoint or "") if s and not s.startswith("{")]
    last = seg[-1] if seg else ""
    return [t for t in _tokens(last) if t not in VERBS and len(t) > 2]


def is_capability_ticket(inc: dict) -> bool:
    text = "%s\n%s" % (inc.get("summary") or "", inc.get("desc") or "")
    return bool(CAPABILITY_RE.search(text))


def endpoints_from_incident(inc: dict, max_endpoints: int = 2) -> List[str]:
    """Endpoints of the ticket's screen hints whose name shares >= 2 content tokens with the ticket text (e.g. 'repair date')."""
    text = "%s %s" % (inc.get("summary") or "", inc.get("desc") or "")
    words = set(_tokens(text))
    squashed = re.sub(r"[^a-z0-9]", "", text.lower())
    scored: List[Tuple[int, str]] = []
    seen = set()
    for h in inc.get("screen_hints") or []:
        for ep in h.get("endpoints") or []:
            if ep in seen:
                continue
            seen.add(ep)
            toks = feature_tokens(ep)
            if len(toks) < 2:
                continue
            hit = sum(1 for t in toks if t in words)
            if hit >= 2 or "".join(toks) in squashed:
                scored.append((hit, ep))
    scored.sort(key=lambda x: -x[0])
    return [ep for _, ep in scored[:max_endpoints]]


# ---------------------------------------------------------------------------------------------- file scanning

def _read_lines(app: str, rel_path: str) -> Optional[List[str]]:
    repo = APP_REPO.get(app)
    if not repo or not rel_path:
        return None
    try:
        real = safety.resolve("%s/%s" % (repo, rel_path.replace("\\", "/").lstrip("/")))
        with open(real, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except (safety.SourceError, OSError):
        return None


def scan_lines(lines: Sequence[str], feature: Sequence[str], call_line: Optional[int] = None,
               ctx: int = 2, near_call: int = 10) -> Dict[str, Any]:
    """Gating snippets of one source file.  Returns {"ranges": [(start, end)], "id_lists": [...], "lines": [(no, text)]}."""
    joined = "".join(feature)
    hits: List[int] = []
    for i, ln in enumerate(lines, 1):
        flat = re.sub(r"[^a-z0-9]", "", ln.lower())
        has_feature = bool(joined) and joined in flat
        if has_feature and GATE_RE.search(ln):
            hits.append(i)
        elif call_line and abs(i - call_line) <= near_call and GATE_RE.search(ln) and not ln.lstrip().startswith("import "):
            hits.append(i)
    # TODO lines directly next to a hit belong to the gate
    todo = [i for i, ln in enumerate(lines, 1) if re.search(r"\bTODO\b", ln) and any(abs(i - h) <= 4 for h in hits)]
    marks = sorted(set(hits) | set(todo))
    ranges: List[List[int]] = []
    for m in marks:
        a, b = max(1, m - ctx), min(len(lines), m + ctx)
        if ranges and a <= ranges[-1][1] + 1:
            ranges[-1][1] = max(ranges[-1][1], b)
        else:
            ranges.append([a, b])
    out_lines: List[Tuple[int, str]] = []
    for a, b in ranges:
        for n in range(a, b + 1):
            out_lines.append((n, lines[n - 1]))
        out_lines.append((0, "..."))
    if out_lines:
        out_lines.pop()
    id_lists = []
    in_range = [n for a, b in ranges for n in range(a, b + 1)]
    for h in in_range:
        ln = lines[h - 1]
        if not re.search(r"UserType|RoleId|TenantId|roleId|tenantId", ln):
            continue
        m = INT_LIST_RE.search(ln) or re.search(r"={2,3}\s*(\d{2,6})", ln)        # list, or a single comparison: ut.UserTypeId === 1218
        if not m:
            continue
        env = None
        for back in range(h, max(0, h - 5), -1):                      # nearest environment key above (Live / Test ...)
            e = ENV_KEY_RE.search(lines[back - 1])
            if e:
                env = e.group(1)
                break
        id_lists.append({"line": h, "ids": [int(x) for x in re.findall(r"\d+", m.group(1))], "env": env, "text": ln.strip()[:120]})
    return {"ranges": ranges, "lines": out_lines, "id_lists": id_lists}


def _trim(lines: List[Tuple[int, str]], max_lines: int) -> List[Tuple[int, str]]:
    if len(lines) <= max_lines:
        return lines
    return lines[:max_lines] + [(0, "... (+%d more lines)" % (len(lines) - max_lines))]


def snippets_for_trace(trace: dict, feature: Sequence[str], max_files: int = MAX_FILES) -> List[dict]:
    """Gating snippets for the callers of an already traced endpoint (graph.trace_endpoint result)."""
    out: List[dict] = []
    seen = set()
    for c in (trace.get("callers") or []):
        app = c.get("app")
        for rel, role in ((c.get("screen"), "screen"), (c.get("file"), "call site")):
            if not rel or (app, rel) in seen or len(out) >= max_files:
                continue
            seen.add((app, rel))
            lines = _read_lines(app, rel)
            if lines is None:
                out.append({"app": app, "file": rel, "role": role, "call_line": c.get("line"), "error": "file not readable"})
                continue
            call_line = c.get("line") if rel == c.get("file") else None
            sc = scan_lines(lines, feature, call_line)
            out.append({"app": app, "file": rel, "role": role, "call_line": c.get("line"), "total_lines": len(lines),
                        "lines": _trim(sc["lines"], MAX_LINES_PER_FILE), "id_lists": sc["id_lists"]})
    return out


def _fmt_item(it: dict) -> List[str]:
    head = "[%s] %s%s" % (it["app"], it["file"], (" (%s, call at :%s)" % (it["role"], it["call_line"])) if it.get("call_line") else " (%s)" % it["role"])
    if it.get("error"):
        return [head + ": " + it["error"]]
    if not it.get("lines"):
        return [head + ": no gating pattern (user type / role / tenant / config / TODO) near the feature"]
    L = [head]
    for n, t in it["lines"]:
        L.append("  ..." if n == 0 and t == "..." else ("  %s" % t if n == 0 else "  %d| %s" % (n, t.strip()[:150])))
    return L


def format_text(endpoint: str, items: List[dict], max_chars: int = MAX_CHARS) -> str:
    L = ["## UI GATING (deterministic): screens that call %s and the permission conditions near the feature" % endpoint]
    gated = [i for i in items if i.get("lines")]
    for it in sorted(items, key=lambda i: (not i.get("lines"), i["app"])):
        L += _fmt_item(it)
    lists = gate_id_lists(items)
    if lists:
        L.append("Hard-coded id lists in the UI: " + "; ".join("%s:%d %s=%s" % (l["file"].split("/")[-1], l["line"], l.get("env") or "?", l["ids"]) for l in lists[:6]))
        L.append("-> resolve them with user_type_lookup (Ids=...), then list the ticket tenant's user types (TenantId=...) and check whether the "
                 "reporter's role is in the list. If the screen decides by a hard-coded list the reporter's role is NOT in, the finding is a "
                 "permission-SCOPE limit of the code (root_cause_type change_request / route_to_backlog; options: workaround by an allowed role, "
                 "add the role after business approval, move the list to configuration).")
    elif gated:
        L.append("-> the screen has gating conditions above; read the surrounding code (read_source) to see who they allow.")
    text = "\n".join(L)
    return text if len(text) <= max_chars else text[:max_chars] + "\n...[truncated]"


def gate_id_lists(items: Iterable[dict]) -> List[dict]:
    out = []
    for it in items:
        for l in it.get("id_lists") or []:
            out.append(dict(l, file=it["file"], app=it["app"]))
    return out


def live_ids(items: Iterable[dict]) -> List[int]:
    """Ids to resolve: the lists labelled Live/Prod when any exists, else all lists (de-duplicated, max 12)."""
    lists = gate_id_lists(items)
    pick = [l for l in lists if (l.get("env") or "").lower() in ("live", "prod", "production")] or lists
    ids: List[int] = []
    for l in pick:
        for i in l["ids"]:
            if i not in ids:
                ids.append(i)
    return ids[:12]


def analyze(graph: Any, endpoints: Sequence[str], max_endpoints: int = 2) -> Dict[str, Any]:
    """{"endpoints": [...], "items": [...], "text": str, "ids": [...]}; never raises (graph optional)."""
    res: Dict[str, Any] = {"endpoints": [], "items": [], "text": "", "ids": []}
    if graph is None or not endpoints:
        return res
    texts: List[str] = []
    for ep in list(endpoints)[:max_endpoints]:
        try:
            tr = graph.trace_endpoint(ep)
        except Exception:                                              # noqa: BLE001
            continue
        if not tr or tr.get("error"):
            continue
        items = snippets_for_trace(tr, feature_tokens(tr.get("endpoint") or ep))
        res["endpoints"].append(tr.get("endpoint") or ep)
        res["items"].extend(items)
        if items:
            texts.append(format_text(tr.get("endpoint") or ep, items))
    res["text"] = safety.scrub_source("\n".join(texts)) if texts else ""
    res["ids"] = live_ids(res["items"])
    return res
