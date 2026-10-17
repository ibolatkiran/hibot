"""Skills as checklists (round 3): the orchestrator, not the model, drives the investigation method.

Each symptom skill (knowledge/skills/*.md) starts with a small machine-readable header:

    ---
    skill: notification_failure
    problem_types: notification_failure            (comma list; +3 when the rule-based problem_type is one of them)
    detector: capability                           (optional named detector, +3: capability | status)
    keywords: \\bsms\\b|\\bnps\\b|notification        (regex over summary + description; +keyword_weight (default 1) per distinct hit, max 3 hits)
    required_evidence:
    - id: trigger_sp_read | when: wo | description: free text | satisfied_by: read_file:REGEX ; template:a,b ; sql:REGEX ; tool:name
    ---

`satisfied_by` is an OR of atoms:  read_file:<regex over the normalized path of a successful read_file>,  template:<id1,id2> (an executed,
successful template query, preflight / baseline queries count),  sql:<regex over an executed ad-hoc query>,  tool:<name> (a successful call).
`when` (optional, AND of atoms joined by `&`):  wo (a work order was resolved),  no_wo,  user_code (the ticket carries a user code),
text~<regex> (ticket text).  Items whose `when` is false are not required.

`select(inc)` picks the matching skill(s) deterministically (max 2); `Checklist` tracks the required evidence; the v2 loop refuses a
final_report (twice at most) while required evidence is missing, unless the model waives an item with a concrete reason
(`checklist_waivers: [{"id", "reason"}]`), which is recorded in the report.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from hibot.tools import skills as skills_lib

MAX_SKILLS = 2
SELECT_MIN_SCORE = 3
SELECT_REL_MARGIN = 2
MAX_REFUSALS = 2
MIN_WAIVER_CHARS = 12
VAGUE_WAIVER = re.compile(r"^\W*(n/?a|none|no|not needed|not necessary|skip|skipped|unnecessary|gerek yok|yok)\W*$", re.I)


STATUS_CHANGE_RE = re.compile(r"cancel|reject|re-?open|iptal|reddet|(?:change|set|move|put|revert|update)\W+(?:\w+\W+){0,4}?status|status\W+(?:change|update)|"
                              r"statü\w*\W+değiş|durum\w*\W+değiş", re.I)


# ----------------------------------------------------------------------------- parsing
@dataclass
class Item:
    id: str
    description: str
    satisfied_by: List[str]
    when: str = ""


@dataclass
class SkillSpec:
    name: str
    problem_types: List[str] = field(default_factory=list)
    detector: str = ""
    keywords: str = ""
    keyword_weight: int = 1
    items: List[Item] = field(default_factory=list)
    body: str = ""

    def keyword_hits(self, text: str) -> int:
        if not self.keywords:
            return 0
        try:
            rx = re.compile(self.keywords, re.I)
        except re.error:
            return 0
        return min(3, len({m.group(0).lower() for m in rx.finditer(text)}))


def split_front_matter(text: str) -> Tuple[Dict[str, Any], str]:
    """({header fields}, body).  `required_evidence:` lines starting with '- ' are collected under that key; a file without a header gives ({}, text)."""
    if not text.startswith("---"):
        return {}, text
    m = re.match(r"---\s*\n(.*?)\n---\s*\n?(.*)\Z", text, re.S)
    if not m:
        return {}, text
    head: Dict[str, Any] = {}
    cur: Optional[str] = None
    for line in m.group(1).splitlines():
        if not line.strip():
            continue
        if line.startswith("- ") and cur:
            head.setdefault(cur, []).append(line[2:].strip())
            continue
        k, _, v = line.partition(":")
        cur = k.strip()
        head[cur] = v.strip() if v.strip() else []
    return head, m.group(2)


def _parse_item(line: str) -> Optional[Item]:
    fields: Dict[str, str] = {}
    for part in line.split(" | "):
        k, _, v = part.partition(":")
        fields[k.strip()] = v.strip()
    if not fields.get("id") or not fields.get("satisfied_by"):
        return None
    return Item(id=fields["id"], description=fields.get("description", ""), when=fields.get("when", ""),
                satisfied_by=[a.strip() for a in fields["satisfied_by"].split(" ; ") if a.strip()])


def parse_skill(name: str, text: str) -> SkillSpec:
    head, body = split_front_matter(text)
    spec = SkillSpec(name=name, body=body.strip())
    spec.problem_types = [p.strip() for p in str(head.get("problem_types") or "").split(",") if p.strip()]
    spec.detector = str(head.get("detector") or "")
    spec.keywords = str(head.get("keywords") or "")
    try:
        spec.keyword_weight = max(1, int(str(head.get("keyword_weight") or "1")))
    except ValueError:
        spec.keyword_weight = 1
    for line in head.get("required_evidence") or []:
        it = _parse_item(line)
        if it:
            spec.items.append(it)
    return spec


def load_specs() -> List[SkillSpec]:
    out = []
    for n in skills_lib.skill_names():
        if n in ("investigation_protocol",):
            continue
        p = skills_lib.skills_dir() / (n + ".md")
        spec = parse_skill(n, p.read_text(encoding="utf-8"))
        if spec.items:
            out.append(spec)
    return out


# --------------------------------------------------------------------------- selection
def _ticket_text(inc: dict) -> str:
    return "%s\n%s" % (inc.get("summary") or "", inc.get("desc") or "")


def _detector(name: str, inc: dict) -> bool:
    if name == "capability":
        from . import ui_gating
        return ui_gating.is_capability_ticket(inc)
    if name == "status":                              # a request to CHANGE / CANCEL / REJECT a status, not any mention of the word "status"
        return inc.get("problem_type") == "data_correction" or bool(STATUS_CHANGE_RE.search(_ticket_text(inc)))
    return False


def score(spec: SkillSpec, inc: dict) -> int:
    s = 0
    if inc.get("problem_type") in spec.problem_types:
        s += 3
    if spec.detector and _detector(spec.detector, inc):
        s += 3
    s += spec.keyword_hits(_ticket_text(inc)) * spec.keyword_weight
    return s


def select(inc: dict, specs: Optional[List[SkillSpec]] = None) -> List[SkillSpec]:
    """Deterministic: highest score first, ties by name, at most MAX_SKILLS with score >= SELECT_MIN_SCORE."""
    specs = load_specs() if specs is None else specs
    ranked = sorted(((score(s, inc), s.name, s) for s in specs), key=lambda t: (-t[0], t[1]))
    if not ranked:
        return []
    floor = max(SELECT_MIN_SCORE, ranked[0][0] - SELECT_REL_MARGIN)          # a weak runner-up (e.g. the capability detector on "I cannot cancel") is dropped
    return [s for sc, _n, s in ranked if sc >= floor][:MAX_SKILLS]


# ----------------------------------------------------------------------------- tracking
def _norm(p: Any) -> str:
    return str(p or "").replace("\\", "/").lower()


def _query_ok(q: dict, live: bool) -> bool:
    return bool(q.get("executed") and q.get("ok") is not False) if live else True


class Checklist:
    """Required evidence of the selected skills, evaluated against the executed queries and the successful tool calls."""

    def __init__(self, specs: List[SkillSpec], inc: dict):
        self.specs = specs
        self.inc = inc
        self.items: List[Tuple[str, Item]] = [(s.name, it) for s in specs for it in s.items]
        self.waived: Dict[str, str] = {}
        self.refusals = 0

    # ---- conditions
    def _cond(self, when: str, queries: List[dict], live: bool) -> bool:
        if not when.strip():
            return True
        wo = any(q.get("query_id") == "wo_locate" and _query_ok(q, live) and (q.get("row_count") or not live) for q in queries)
        for atom in [a.strip() for a in when.split("&") if a.strip()]:
            if atom == "wo":
                ok = wo or bool((self.inc.get("entities") or {}).get("work_order_ids_strong")) and not live
            elif atom == "no_wo":
                ok = not wo
            elif atom == "user_code":
                ok = bool((self.inc.get("entities") or {}).get("user_codes"))
            elif atom.startswith("text~"):
                try:
                    ok = bool(re.search(atom[5:], _ticket_text(self.inc), re.I))
                except re.error:
                    ok = False
            else:
                ok = False
            if not ok:
                return False
        return True

    # ---- atoms
    @staticmethod
    def _atom(atom: str, queries: List[dict], calls: List[dict], live: bool) -> bool:
        kind, _, arg = atom.partition(":")
        if kind == "tool":
            return any(c["tool"] == arg and c.get("ok") for c in calls)
        if kind == "read_file":
            try:
                rx = re.compile(arg, re.I)
            except re.error:
                return False
            return any(c["tool"] == "read_file" and c.get("ok") and rx.search(_norm((c.get("args") or {}).get("path"))) for c in calls)
        if kind == "template":
            ids = {x.strip() for x in arg.split(",") if x.strip()}
            return any(q.get("query_id") in ids and _query_ok(q, live) for q in queries)
        if kind == "sql":
            try:
                rx = re.compile(arg, re.I)
            except re.error:
                return False
            return any(q.get("query_id") == "adhoc" and _query_ok(q, live) and rx.search(q.get("sql_display") or q.get("sql") or "") for q in queries)
        return False

    def satisfied(self, it: Item, queries: List[dict], calls: List[dict], live: bool) -> bool:
        return any(self._atom(a, queries, calls, live) for a in it.satisfied_by)

    # ---- state
    def required(self, queries: List[dict], live: bool) -> List[Tuple[str, Item]]:
        return [(n, it) for n, it in self.items if self._cond(it.when, queries, live)]

    def missing(self, queries: List[dict], calls: List[dict], live: bool) -> List[Tuple[str, Item]]:
        return [(n, it) for n, it in self.required(queries, live)
                if it.id not in self.waived and not self.satisfied(it, queries, calls, live)]

    def apply_waivers(self, waivers: Any, queries: List[dict], calls: List[dict], live: bool) -> None:
        """Accept waivers for missing items with a concrete reason (not 'n/a'); unknown ids and vague reasons are ignored."""
        if not isinstance(waivers, list):
            return
        names = {it.id for _n, it in self.items}
        for w in waivers:
            if not isinstance(w, dict):
                continue
            i, reason = str(w.get("id") or "").strip(), str(w.get("reason") or "").strip()
            if i in names and len(reason) >= MIN_WAIVER_CHARS and not VAGUE_WAIVER.match(reason):
                self.waived[i] = reason[:300]

    # ---- text
    @staticmethod
    def hint(it: Item) -> str:
        hs = []
        for a in it.satisfied_by:
            kind, _, arg = a.partition(":")
            hs.append({"tool": "call tool %s" % arg,
                       "read_file": "read_file on a source file whose path matches /%s/ (find it with code_search / graph_*)" % arg,
                       "template": "run_template with query_id %s" % " or ".join(x.strip() for x in arg.split(",")),
                       "sql": "a sql query whose text matches /%s/" % arg}.get(kind, a))
        return " OR ".join(hs)

    def section(self) -> str:
        """Case-file block: the selected skills (header stripped) + the required-evidence checklist."""
        if not self.specs:
            return ""
        L = ["## METHOD FOR THIS CASE (skill%s selected by the program from the ticket: %s)" % (
            "s" if len(self.specs) > 1 else "", ", ".join(s.name for s in self.specs)),
            "Follow it. Your first tool call is the first step of this method (you do NOT need read_skill for it)."]
        for s in self.specs:
            L.append("\n### skill: %s\n%s" % (s.name, s.body))
        L.append("\n### REQUIRED EVIDENCE CHECKLIST (tracked by the program)")
        L.append("A final_report is REFUSED (up to %d times) while required evidence is missing. Baseline queries already run count. "
                 "If an item is truly impossible, say so in final_report.checklist_waivers: [{\"id\": \"...\", \"reason\": \"<concrete reason, e.g. "
                 "no work order in the ticket>\"}]; the waiver is recorded." % MAX_REFUSALS)
        for n, it in self.items:
            cond = " (only if: %s)" % it.when if it.when else ""
            L.append("- [%s] %s: %s%s. Satisfied by: %s" % (n, it.id, it.description, cond, self.hint(it)))
        return "\n".join(L)

    def refusal_text(self, missing: List[Tuple[str, Item]]) -> str:
        L = ["REQUIRED EVIDENCE MISSING (refusal %d of %d): the method for this case demands the following before you may conclude. Do exactly this now "
             "(up to 3 independent tools per turn), then resend your final_report (refined by what you learned):" % (self.refusals, MAX_REFUSALS)]
        for _n, it in missing:
            L.append("- %s: %s -> %s" % (it.id, it.description, self.hint(it)))
        L.append("Only if an item is impossible, resend the final_report with checklist_waivers [{\"id\", \"reason\"}] giving the concrete reason.")
        return "\n".join(L)

    def report_block(self, queries: List[dict], calls: List[dict], live: bool) -> Dict[str, Any]:
        req = self.required(queries, live)
        items = []
        for n, it in req:
            sat = self.satisfied(it, queries, calls, live)
            items.append({"skill": n, "id": it.id, "satisfied": sat, "waived": (self.waived.get(it.id) if not sat else None)})
        return {"skills": [s.name for s in self.specs], "items": items, "refusals": self.refusals,
                "missing": [i["id"] for i in items if not i["satisfied"] and not i["waived"]],
                "waivers": dict(self.waived)}
