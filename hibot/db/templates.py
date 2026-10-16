"""Parameterised query template library (knowledge/queries/*.sql).

A template file is ``<id>.sql``: a leading ``/* ... */`` header in a simple
``key: value`` format followed by one SELECT / WITH...SELECT using named
parameters (``@WorkOrderId``).  Parameters are bound as real ODBC parameters
(``?``) by the runner - values are never formatted into the SQL text.

Header keys (one per line, continuation lines are indented):

    id:          unique id == file name without .sql
    title:       short title
    purpose:     what it diagnoses / which incident symptoms
    param:       Name | type | required|optional [default=V] [like] [max=N] | description   (repeatable)
    require_any: Param1, Param2   (at least one must be supplied)
    regions:     EU, APAC
    tables:      Schema.Object, ...  (must equal what the SQL really reads)
    pii:         ResultColumn:kind, ...   (kinds: email phone contact name address identity iban_bank free_text_body)
    pii_sources: SourceColumn, ...        (PII-flagged catalog columns used under another alias)
    interpret:   how to read the result
    next:        follow-up query ids
    row_limit:   optional cap (default 200)
"""
from __future__ import annotations

import datetime as _dt
import decimal
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import safe_sql
from .catalog import REGIONS, SchemaCatalog, get_catalog

PARAM_TYPES = ("int", "bigint", "string", "datetime", "date", "bool", "decimal")
PII_KINDS = ("email", "phone", "contact", "name", "address", "identity", "iban_bank", "free_text_body")
_HEADER_KEY = re.compile(r"^([a-z_]+):\s?(.*)$")
_DEFAULT_STR_MAX = 200


class TemplateError(ValueError):
    """Bad template definition or bad parameters supplied by the caller."""


@dataclass
class Param:
    name: str
    type: str
    required: bool
    description: str = ""
    default: Optional[str] = None
    like: bool = False
    max_len: int = _DEFAULT_STR_MAX
    min_value: Optional[int] = None
    max_value: Optional[int] = None

    def brief(self) -> str:
        s = "%s:%s%s" % (self.name, self.type, "!" if self.required else "?")
        if self.default is not None:
            s += "=" + self.default
        return s


@dataclass
class Template:
    id: str
    title: str
    purpose: str
    params: List[Param]
    regions: List[str]
    tables: List[str]
    sql: str
    pii: Dict[str, str] = field(default_factory=dict)
    pii_sources: List[str] = field(default_factory=list)
    interpret: str = ""
    next: List[str] = field(default_factory=list)
    require_any: List[str] = field(default_factory=list)
    row_limit: int = safe_sql.DEFAULT_ROW_LIMIT
    path: str = ""

    def param(self, name: str) -> Optional[Param]:
        for p in self.params:
            if p.name.lower() == name.lower():
                return p
        return None


@dataclass
class PreparedQuery:
    template_id: str
    region: str
    sql: str                  # positional ('?') SQL, already validated + rewritten
    args: List[Any]
    row_limit: int
    pii: Dict[str, str]
    params: Dict[str, Any]    # coerced, name -> value (for the audit log)
    warnings: List[str]


# ----------------------------------------------------------------- parsing

def _split_header(text: str) -> Tuple[str, str]:
    m = re.match(r"\s*/\*(.*?)\*/(.*)$", text, re.S)
    if not m:
        raise TemplateError("template must start with a /* ... */ header block")
    return m.group(1), m.group(2).strip()


def _parse_header(block: str) -> List[Tuple[str, str]]:
    items: List[List[str]] = []
    for line in block.splitlines():
        if not line.strip():
            continue
        if line[:1].isspace() and items:
            items[-1][1] += " " + line.strip()
            continue
        m = _HEADER_KEY.match(line.strip())
        if m:
            items.append([m.group(1), m.group(2).strip()])
        elif items:
            items[-1][1] += " " + line.strip()
        else:
            raise TemplateError("header line without key: %r" % line)
    return [(k, v) for k, v in items]


def _csv(v: str) -> List[str]:
    return [x.strip() for x in v.split(",") if x.strip()]


def _parse_param(v: str) -> Param:
    parts = [x.strip() for x in v.split("|")]
    if len(parts) < 3:
        raise TemplateError("param needs 'Name | type | required|optional [opts] | description': %r" % v)
    name, typ, flags = parts[0], parts[1].lower(), parts[2].lower().split()
    desc = " | ".join(parts[3:])
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
        raise TemplateError("bad parameter name %r" % name)
    if typ not in PARAM_TYPES:
        raise TemplateError("param %s: unknown type %r" % (name, typ))
    if not flags or flags[0] not in ("required", "optional"):
        raise TemplateError("param %s: third field must start with required|optional" % name)
    p = Param(name=name, type=typ, required=flags[0] == "required", description=desc)
    for f in flags[1:]:
        if f.startswith("default="):
            p.default = f.split("=", 1)[1]
        elif f == "like":
            p.like = True
        elif f.startswith("max="):
            p.max_len = int(f.split("=", 1)[1]) if typ == "string" else p.max_len
            if typ != "string":
                p.max_value = int(f.split("=", 1)[1])
        elif f.startswith("min="):
            p.min_value = int(f.split("=", 1)[1])
        else:
            raise TemplateError("param %s: unknown option %r" % (name, f))
    return p


def parse_template(text: str, path: str = "") -> Template:
    block, sql = _split_header(text)
    sql = sql.strip().rstrip(";").strip()
    data: Dict[str, List[str]] = {}
    for k, v in _parse_header(block):
        data.setdefault(k, []).append(v)
    allowed = {"id", "title", "purpose", "param", "require_any", "regions", "tables", "pii",
               "pii_sources", "interpret", "next", "row_limit"}
    unknown = set(data) - allowed
    if unknown:
        raise TemplateError("unknown header keys: %s" % ", ".join(sorted(unknown)))
    for req in ("id", "title", "purpose", "regions", "tables", "interpret"):
        if req not in data:
            raise TemplateError("missing header key %r" % req)
    tid = data["id"][0]
    if not re.fullmatch(r"[a-z][a-z0-9_]*", tid):
        raise TemplateError("bad template id %r" % tid)
    params = [_parse_param(v) for v in data.get("param", [])]
    names = [p.name.lower() for p in params]
    if len(set(names)) != len(names):
        raise TemplateError("%s: duplicate parameter names" % tid)
    regions = [r.upper() for r in _csv(data["regions"][0])]
    if not regions or any(r not in REGIONS for r in regions):
        raise TemplateError("%s: regions must be a subset of %s" % (tid, REGIONS))
    pii: Dict[str, str] = {}
    for item in _csv(data.get("pii", [""])[0]):
        if ":" not in item:
            raise TemplateError("%s: pii entries are 'Column:kind' (got %r)" % (tid, item))
        col, kind = [x.strip() for x in item.split(":", 1)]
        if kind not in PII_KINDS:
            raise TemplateError("%s: unknown pii kind %r" % (tid, kind))
        pii[col] = kind
    require_any = _csv(data.get("require_any", [""])[0])
    for r in require_any:
        if r.lower() not in names:
            raise TemplateError("%s: require_any names unknown param %s" % (tid, r))
    row_limit = int(data["row_limit"][0]) if "row_limit" in data else safe_sql.DEFAULT_ROW_LIMIT
    return Template(
        id=tid, title=data["title"][0], purpose=data["purpose"][0], params=params,
        regions=regions, tables=_csv(data["tables"][0]), sql=sql, pii=pii,
        pii_sources=_csv(data.get("pii_sources", [""])[0]),
        interpret=data["interpret"][0], next=_csv(data.get("next", [""])[0]),
        require_any=require_any, row_limit=row_limit, path=path)


# ------------------------------------------------------------- param binding

_INT_RANGES = {"int": (-2 ** 31, 2 ** 31 - 1), "bigint": (-2 ** 63, 2 ** 63 - 1)}
_DT = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?)?$")


def _escape_like(s: str) -> str:
    return re.sub(r"([\[%_])", r"[\1]", s)


def coerce(p: Param, value: Any) -> Any:
    """Validate/convert one caller-supplied value.  Raises TemplateError."""
    if value is None:
        return None
    t = p.type
    try:
        if t in ("int", "bigint"):
            if isinstance(value, bool):
                raise ValueError("bool")
            if isinstance(value, float):
                if value != int(value):
                    raise ValueError("float")
                value = int(value)
            if isinstance(value, str):
                if not re.fullmatch(r"\s*-?\d{1,19}\s*", value):
                    raise ValueError("not an integer")
                value = int(value)
            if not isinstance(value, int):
                raise ValueError("type")
            lo, hi = _INT_RANGES[t]
            if p.min_value is not None:
                lo = max(lo, p.min_value)
            if p.max_value is not None:
                hi = min(hi, p.max_value)
            if not lo <= value <= hi:
                raise ValueError("out of range %d..%d" % (lo, hi))
            return value
        if t == "bool":
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, (int,)) and value in (0, 1):
                return int(value)
            if isinstance(value, str) and value.strip().lower() in ("1", "true", "yes", "0", "false", "no"):
                return 1 if value.strip().lower() in ("1", "true", "yes") else 0
            raise ValueError("expected true/false")
        if t == "decimal":
            if isinstance(value, bool):
                raise ValueError("bool")
            d = decimal.Decimal(str(value).strip())
            if not d.is_finite():
                raise ValueError("not finite")
            return d
        if t in ("datetime", "date"):
            if isinstance(value, _dt.datetime):
                return value.replace(microsecond=0) if t == "date" else value
            if isinstance(value, _dt.date):
                return _dt.datetime(value.year, value.month, value.day)
            if isinstance(value, str) and _DT.match(value.strip()):
                s = value.strip().replace("T", " ")
                fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in s else (
                    "%Y-%m-%d %H:%M:%S" if s.count(":") == 2 else (
                        "%Y-%m-%d %H:%M" if ":" in s else "%Y-%m-%d"))
                return _dt.datetime.strptime(s, fmt)
            raise ValueError("expected ISO date/datetime, e.g. 2025-01-31 or 2025-01-31T10:00:00")
        if t == "string":
            if isinstance(value, (bytes, bytearray)) or isinstance(value, bool):
                raise ValueError("expected text")
            s = str(value)
            if len(s) > p.max_len:
                raise ValueError("longer than %d characters" % p.max_len)
            if any(ord(c) < 32 or ord(c) == 127 for c in s):
                raise ValueError("control characters are not allowed")
            return _escape_like(s) if p.like else s
    except (ValueError, decimal.InvalidOperation) as e:
        raise TemplateError("parameter %s (%s): %s" % (p.name, t, e))
    raise TemplateError("unsupported type %s" % t)


def bind_params(t: Template, supplied: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Return {ParamName: value} for every declared parameter."""
    supplied = dict(supplied or {})
    lookup = {p.name.lower(): p for p in t.params}
    snake = {re.sub(r"_", "", k).lower(): p for k, p in ((p.name, p) for p in t.params)}
    out: Dict[str, Any] = {}
    for k, v in supplied.items():
        key = k.lstrip("@")
        p = lookup.get(key.lower()) or snake.get(key.replace("_", "").lower())
        if p is None:
            raise TemplateError("unknown parameter %r for %s (expected: %s)" % (
                k, t.id, ", ".join(x.name for x in t.params) or "none"))
        if p.name in out:
            raise TemplateError("parameter %s given twice" % p.name)
        out[p.name] = coerce(p, v)
    for p in t.params:
        if p.name not in out or out[p.name] is None:
            if p.default is not None and p.name not in out:
                out[p.name] = coerce(p, p.default)
            else:
                out[p.name] = None
        if p.required and out[p.name] is None:
            raise TemplateError("missing required parameter %s for %s" % (p.name, t.id))
    if t.require_any:
        canon = {p.name.lower(): p.name for p in t.params}
        if all(out.get(canon[n.lower()]) is None for n in t.require_any):
            raise TemplateError("%s needs at least one of: %s" % (t.id, ", ".join(t.require_any)))
    return out


# ----------------------------------------------------------------- registry

def default_queries_dir() -> Path:
    env = os.environ.get("HIBOT_QUERIES_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "knowledge" / "queries"


@dataclass
class TemplateReport:
    template_id: str
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class TemplateRegistry:
    def __init__(self, templates: Dict[str, Template], catalog: Optional[SchemaCatalog] = None):
        self._t = templates
        self._catalog = catalog
        self._prepared: Dict[Tuple[str, str], safe_sql.SafeSqlResult] = {}

    @classmethod
    def load(cls, directory: Optional[os.PathLike] = None,
             catalog: Optional[SchemaCatalog] = None) -> "TemplateRegistry":
        d = Path(directory) if directory else default_queries_dir()
        out: Dict[str, Template] = {}
        for f in sorted(d.glob("*.sql")):
            t = parse_template(f.read_text(encoding="utf-8"), str(f))
            if t.id != f.stem:
                raise TemplateError("%s: header id %r does not match file name" % (f.name, t.id))
            if t.id in out:
                raise TemplateError("duplicate template id %s" % t.id)
            out[t.id] = t
        return cls(out, catalog)

    @property
    def catalog(self) -> SchemaCatalog:
        if self._catalog is None:
            self._catalog = get_catalog()
        return self._catalog

    def ids(self) -> List[str]:
        return sorted(self._t)

    def get(self, template_id: str) -> Template:
        try:
            return self._t[template_id]
        except KeyError:
            raise TemplateError("unknown query_id %r (known: %s)" % (template_id, ", ".join(self.ids())))

    def __iter__(self):
        return iter(self._t[i] for i in self.ids())

    def __len__(self):
        return len(self._t)

    # --- validation
    def validate_sql(self, t: Template, region: str) -> safe_sql.SafeSqlResult:
        key = (t.id, region)
        if key not in self._prepared:
            self._prepared[key] = safe_sql.validate(
                t.sql, region=region, catalog=self.catalog, row_limit=t.row_limit,
                allowed_params=[p.name for p in t.params], allow_pii=True)
        return self._prepared[key]

    def validate_template(self, t: Template) -> TemplateReport:
        rep = TemplateReport(t.id)
        declared_ok_regions = []
        all_objects = set()
        for region in REGIONS:
            r = self.validate_sql(t, region)
            if region in t.regions:
                if not r.ok:
                    rep.errors.append("%s: %s" % (region, "; ".join(r.errors)))
                    continue
                declared_ok_regions.append(region)
                rep.warnings.extend("%s: %s" % (region, w) for w in r.warnings)
                all_objects.update(r.objects)
                all_objects.update(r.functions)
                used_unknown = [c for c in r.pii_columns_used
                                if c not in t.pii and c not in t.pii_sources
                                and c.lower() not in {x.lower() for x in t.pii}
                                and c.lower() not in {x.lower() for x in t.pii_sources}]
                if used_unknown:
                    rep.errors.append("%s: PII columns used but not declared in pii/pii_sources: %s"
                                      % (region, ", ".join(used_unknown)))
            elif r.ok:
                rep.errors.append("template validates in %s but header regions=%s (declare it)"
                                  % (region, ",".join(t.regions)))
        if all_objects and {o.lower() for o in all_objects} != {x.lower() for x in t.tables}:
            rep.errors.append("header tables %s != tables/functions actually read %s"
                              % (sorted(t.tables), sorted(all_objects)))
        used = set(re.findall(r"@(\w+)", re.sub(r"'(?:[^']|'')*'", "''", t.sql)))
        used_l = {u.lower() for u in used}
        for p in t.params:
            if p.name.lower() not in used_l:
                rep.errors.append("declared parameter %s is never used" % p.name)
        for u in used:
            if t.param(u) is None:
                rep.errors.append("SQL uses undeclared parameter @%s" % u)
        for n in t.next:
            if n not in self._t:
                rep.errors.append("next: unknown template id %s" % n)
        return rep

    def validate_all(self) -> List[TemplateReport]:
        return [self.validate_template(t) for t in self]

    # --- execution preparation
    def prepare(self, template_id: str, params: Optional[Mapping[str, Any]], region: str) -> PreparedQuery:
        region = region.upper()
        t = self.get(template_id)
        if region not in t.regions:
            raise TemplateError("query %s is not available in region %s (available: %s)"
                                % (t.id, region, ", ".join(t.regions)))
        bound = bind_params(t, params)
        r = self.validate_sql(t, region)
        if not r.ok:
            raise TemplateError("template %s failed validation for %s: %s" % (t.id, region, "; ".join(r.errors)))
        args = [bound[n] for n in _canon(t, r.param_order)]
        return PreparedQuery(template_id=t.id, region=region, sql=r.positional_sql, args=args,
                             row_limit=r.row_limit, pii=dict(t.pii), params=bound, warnings=list(r.warnings))

    # --- prompt catalog
    def catalog_text(self, region: Optional[str] = None, with_purpose: bool = True) -> str:
        """Compact text listing for the LLM prompt.  `!` required, `?` optional."""
        lines = []
        for t in self:
            if region and region.upper() not in t.regions:
                continue
            ps = ", ".join(p.brief() for p in t.params)
            any_of = (" [at least one of: %s]" % "/".join(t.require_any)) if t.require_any else ""
            reg = "" if len(t.regions) == len(REGIONS) else " (%s only)" % "/".join(t.regions)
            line = "- %s%s: %s" % (t.id, reg, t.title)
            if with_purpose:
                line += " - " + t.purpose
            lines.append(line)
            lines.append("    params: %s%s" % (ps, any_of))
        return "\n".join(lines)

    def describe(self, template_id: str) -> str:
        """Full text for one template (interpretation guide + follow-ups)."""
        t = self.get(template_id)
        return "\n".join([
            "%s - %s" % (t.id, t.title), "purpose: " + t.purpose,
            "params: " + ", ".join("%s (%s) %s" % (p.name, p.type, p.description) for p in t.params),
            "interpret: " + t.interpret, "next: " + ", ".join(t.next)])


def _canon(t: Template, order: Sequence[str]) -> List[str]:
    by_lower = {p.name.lower(): p.name for p in t.params}
    return [by_lower[n.lower()] for n in order]
