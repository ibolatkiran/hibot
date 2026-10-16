"""Validator / rewriter for SQL that HiBot is about to run on production.

Design goals (safety first, see hibot/db/README.md):

* A real tokenizer (strings, [brackets], "quoted", comments, variables) - no
  substring matching.  Keywords are only recognised as *unquoted words*, so a
  column called ``[UpdateDate]`` or ``UpdateDate`` is never confused with
  ``UPDATE``.
* Exactly one statement, which must be ``SELECT`` or ``WITH ... SELECT``.
* Deny-by-default: anything not understood is rejected (unknown characters,
  unknown functions, unknown objects, hints other than NOLOCK, OPTION other
  than RECOMPILE/MAXRECURSION, variables that are not declared parameters ...).
* Objects must exist in the schema catalog for the target region; only
  tables, views and (side-effect free) functions are readable.
* The outer SELECT(s) get a ``TOP n`` injected / clamped.
* Credentials are never selectable; PII columns are not selectable in ad-hoc
  SQL (templates may select them, results are masked afterwards).
* ``SELECT *`` over tables with sensitive columns is rewritten to an explicit
  safe column list (or rejected when that cannot be done unambiguously).
* The SQL that is executed is *regenerated from the validated tokens*
  (comments dropped) - never the original text.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .catalog import (REGIONS, FREE_TEXT_KIND, ObjectInfo, SchemaCatalog,
                      get_catalog, is_secret_name, is_secret_table)

DEFAULT_ROW_LIMIT = 200
MAX_ROW_LIMIT = 1000
MAX_SQL_CHARS = 20000
MAX_TOKENS = 5000
MAX_DEPTH = 12

# ---------------------------------------------------------------- vocabulary

FORBIDDEN_KEYWORDS = frozenset("""
INSERT UPDATE DELETE MERGE TRUNCATE DROP ALTER CREATE GRANT REVOKE DENY
EXEC EXECUTE OPENROWSET OPENQUERY OPENDATASOURCE OPENXML BULK INTO WAITFOR
SHUTDOWN DBCC BACKUP RESTORE KILL USE SET DECLARE CURSOR OPEN CLOSE FETCH
DEALLOCATE GOTO BEGIN IF WHILE PRINT RAISERROR THROW RECONFIGURE LOAD COMMIT
ROLLBACK SAVE TRAN TRANSACTION OUTPUT OFFSET NEXT READTEXT WRITETEXT
UPDATETEXT DUMP CHECKPOINT REVERT GO RETURN BREAK CONTINUE ENABLE DISABLE
TRIGGER READONLY COMPUTE BROWSE
""".split())

BUILTIN_FUNCTIONS = frozenset("""
AVG COUNT COUNT_BIG SUM MIN MAX STDEV STDEVP VAR VARP STRING_AGG ROW_NUMBER
RANK DENSE_RANK NTILE LAG LEAD FIRST_VALUE LAST_VALUE CUME_DIST COALESCE ISNULL
NULLIF IIF CHOOSE CAST CONVERT TRY_CAST TRY_CONVERT LEN DATALENGTH LEFT RIGHT
SUBSTRING CHARINDEX PATINDEX LTRIM RTRIM TRIM UPPER LOWER REPLACE STUFF CONCAT
CONCAT_WS FORMAT STR REVERSE REPLICATE SPACE CHAR NCHAR ASCII UNICODE QUOTENAME
TRANSLATE ISNUMERIC ISDATE ISJSON JSON_VALUE JSON_QUERY JSON_PATH_EXISTS
GETDATE GETUTCDATE SYSDATETIME SYSUTCDATETIME SYSDATETIMEOFFSET DATEADD
DATEDIFF DATEDIFF_BIG DATEPART DATENAME DAY MONTH YEAR EOMONTH DATEFROMPARTS
DATETIMEFROMPARTS SWITCHOFFSET TODATETIMEOFFSET ABS CEILING FLOOR ROUND POWER
SQRT SIGN SQUARE LOG LOG10 EXP GREATEST LEAST DATETRUNC
""".split())

# Table-valued built-ins that may appear in FROM.
BUILTIN_TVFS = frozenset({"OPENJSON", "STRING_SPLIT"})

TYPE_NAMES = frozenset("""
INT BIGINT SMALLINT TINYINT BIT DECIMAL NUMERIC FLOAT REAL MONEY SMALLMONEY
CHAR VARCHAR NCHAR NVARCHAR BINARY VARBINARY DATETIME DATETIME2 DATE TIME
DATETIMEOFFSET SMALLDATETIME UNIQUEIDENTIFIER
""".split())

# Words that may legitimately be followed by "(" without being a function call.
KEYWORDS_BEFORE_PAREN = frozenset("""
SELECT FROM WHERE AND OR NOT IN EXISTS ANY ALL SOME ON JOIN APPLY UNION
INTERSECT EXCEPT AS OVER BY HAVING WHEN THEN ELSE CASE LIKE BETWEEN IS TOP
DISTINCT VALUES WITH PARTITION ORDER GROUP PIVOT UNPIVOT WITHIN OUTER INNER
LEFT RIGHT CROSS FULL OPTION FOR ROWS RANGE ESCAPE COLLATE
""".split())

SOURCE_TERMINATORS = frozenset({"WHERE", "GROUP", "HAVING", "ORDER", "UNION",
                                "INTERSECT", "EXCEPT", "OPTION", "FOR", "WINDOW"})
NOT_AN_ALIAS = frozenset("""
JOIN INNER LEFT RIGHT FULL CROSS OUTER ON WHERE GROUP HAVING ORDER UNION
INTERSECT EXCEPT WITH OPTION FOR PIVOT UNPIVOT TABLESAMPLE APPLY NATURAL USING
""".split())
ALLOWED_TABLE_HINTS = frozenset({"NOLOCK", "READUNCOMMITTED"})
SYSTEM_QUALIFIERS = frozenset({"sys", "information_schema", "master", "msdb",
                               "model", "tempdb"})
COMPARISON_WORDS = frozenset({"IN", "LIKE", "BETWEEN", "IS"})
COMPARISON_OPS = frozenset({"=", "<>", "!=", "<", ">", "<=", ">=", "!<", "!>"})
ASSIGN_OPS = frozenset({"+=", "-=", "*=", "/=", "%=", "&=", "|=", "^="})

W, Q, S, N, V, O, P = "WORD", "QIDENT", "STRING", "NUMBER", "VAR", "OP", "PUNCT"


# ------------------------------------------------------------------- results

@dataclass
class SafeSqlResult:
    ok: bool
    rewritten_sql: Optional[str] = None       # named params (@Name)
    positional_sql: Optional[str] = None      # '?' params, for pyodbc
    param_order: List[str] = field(default_factory=list)  # names, one per '?'
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    rewrites: List[str] = field(default_factory=list)      # human readable notes
    objects: List[str] = field(default_factory=list)       # catalog objects read
    functions: List[str] = field(default_factory=list)
    pii_columns_used: List[str] = field(default_factory=list)
    row_limit: int = DEFAULT_ROW_LIMIT
    region: Optional[str] = None
    internal_error: bool = False

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "rewritten_sql": self.rewritten_sql,
            "errors": list(self.errors), "warnings": list(self.warnings),
            "rewrites": list(self.rewrites), "objects": list(self.objects),
            "functions": list(self.functions), "row_limit": self.row_limit,
            "region": self.region,
        }


class _Reject(Exception):
    """Internal: abort validation with one error message."""


@dataclass
class Token:
    kind: str
    text: str
    start: int
    end: int
    gap: str = " "           # whitespace that preceded this token (comments -> " ")
    depth: int = 0
    match: int = -1
    skip: bool = False
    post: str = ""           # text injected right after the token
    value: str = ""          # unquoted identifier / variable name (no @)

    @property
    def up(self) -> str:
        return self.text.upper() if self.kind == W else ""

    def is_word(self, *names: str) -> bool:
        return self.kind == W and self.text.upper() in names

    def is_punct(self, ch: str) -> bool:
        return self.kind == P and self.text == ch

    @property
    def is_ident(self) -> bool:
        return self.kind in (W, Q)


# ----------------------------------------------------------------- tokenizer

_IDENT_START = re.compile(r"[A-Za-z_#]")
_IDENT_PART = re.compile(r"[A-Za-z0-9_#$]")
_NUMBER = re.compile(r"0[xX][0-9A-Fa-f]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_TWO_CHAR_OPS = ("<>", "<=", ">=", "!=", "!<", "!>", "+=", "-=", "*=", "/=",
                 "%=", "&=", "|=", "^=")
_ONE_CHAR_OPS = "+-*/%=<>&|^~"


def tokenize(sql: str) -> List[Token]:
    """Tokenize T-SQL.  Raises _Reject on anything unsafe or malformed."""
    for ch in sql:
        if ch in "\t\n\r":
            continue
        cat = unicodedata.category(ch)
        if cat[0] == "C" or cat in ("Zl", "Zp"):
            raise _Reject("control / invisible character U+%04X is not allowed" % ord(ch))
    toks: List[Token] = []
    i, n = 0, len(sql)
    gap_start = 0
    gap_has_comment = False

    def emit(kind, text, start, end, value=""):
        nonlocal gap_start, gap_has_comment
        raw = sql[gap_start:start]
        gap = " " if gap_has_comment else raw
        toks.append(Token(kind, text, start, end, gap=gap, value=value))
        gap_start = end
        gap_has_comment = False

    while i < n:
        ch = sql[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if sql.startswith("--", i):
            j = i
            while j < n and sql[j] not in "\r\n":
                j += 1
            gap_has_comment = True
            i = j
            continue
        if sql.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if sql.startswith("/*", j):
                    depth += 1; j += 2
                elif sql.startswith("*/", j):
                    depth -= 1; j += 2
                else:
                    j += 1
            if depth:
                raise _Reject("unterminated /* comment")
            gap_has_comment = True
            i = j
            continue
        if ch == "'" or (ch in "Nn" and sql.startswith("'", i + 1)):
            j = i + (1 if ch == "'" else 2)
            while True:
                if j >= n:
                    raise _Reject("unterminated string literal")
                if sql[j] == "'":
                    if sql.startswith("''", j):
                        j += 2
                        continue
                    break
                j += 1
            emit(S, sql[i:j + 1], i, j + 1)
            i = j + 1
            continue
        if ord(ch) > 127:
            raise _Reject("non-ASCII character outside a string literal is not allowed")
        if ch in "[\"":
            close = "]" if ch == "[" else '"'
            j = i + 1
            buf = []
            while True:
                if j >= n:
                    raise _Reject("unterminated quoted identifier")
                if sql[j] == close:
                    if sql.startswith(close * 2, j):
                        buf.append(close); j += 2
                        continue
                    break
                buf.append(sql[j]); j += 1
            name = "".join(buf)
            if not name.strip():
                raise _Reject("empty quoted identifier")
            if any(ord(c) > 127 or c in "?{}" for c in name):
                raise _Reject("non-ASCII or ?{} character in quoted identifier is not allowed")
            emit(Q, sql[i:j + 1], i, j + 1, value=name)
            i = j + 1
            continue
        if _IDENT_START.match(ch):
            if ch == "#":
                raise _Reject("temp tables (#name) are not allowed")
            j = i + 1
            while j < n and _IDENT_PART.match(sql[j]):
                j += 1
            if "#" in sql[i:j]:
                raise _Reject("'#' inside an identifier is not allowed")
            emit(W, sql[i:j], i, j, value=sql[i:j])
            i = j
            continue
        if ch.isdigit() or (ch == "." and i + 1 < n and sql[i + 1].isdigit()):
            m = _NUMBER.match(sql, i)
            j = m.end()
            if j < n and (sql[j].isalpha() or sql[j] == "_"):
                raise _Reject("malformed numeric literal near %r" % sql[i:j + 1])
            emit(N, sql[i:j], i, j)
            i = j
            continue
        if ch == "@":
            if sql.startswith("@@", i):
                raise _Reject("system variables (@@...) are not allowed")
            j = i + 1
            while j < n and _IDENT_PART.match(sql[j]):
                j += 1
            if j == i + 1:
                raise _Reject("stray '@'")
            emit(V, sql[i:j], i, j, value=sql[i + 1:j])
            i = j
            continue
        two = sql[i:i + 2]
        if two in _TWO_CHAR_OPS:
            emit(O, two, i, i + 2)
            i += 2
            continue
        if ch in _ONE_CHAR_OPS:
            emit(O, ch, i, i + 1)
            i += 1
            continue
        if ch in "(),.;":
            emit(P, ch, i, i + 1)
            i += 1
            continue
        raise _Reject("character %r is not allowed in SQL" % ch)
    return toks


# ------------------------------------------------------------------ analysis

@dataclass
class _Src:
    kind: str                      # table view function builtin derived cte
    name: str                      # canonical name or raw
    alias: Optional[str]
    info: Optional[ObjectInfo]
    keys: List[str]
    first: int                     # token index of first token of the source
    last: int                      # token index of last token (incl. alias/hints)


@dataclass
class _Scope:
    start: int
    end: int
    depth: int
    parent: Optional["_Scope"] = None
    from_idx: Optional[int] = None
    region_end: int = 0
    head_end: int = 0              # index of first select-list item
    sources: List[_Src] = field(default_factory=list)


class _Analyzer:
    def __init__(self, sql, catalog, region, row_limit, allowed_params, allow_pii):
        self.sql = sql
        self.cat = catalog
        self.region = region
        self.row_limit = row_limit
        self.allowed_params = {p.lstrip("@").lower() for p in allowed_params}
        self.allow_pii = allow_pii
        self.warnings: List[str] = []
        self.rewrites: List[str] = []
        self.objects: List[str] = []
        self.functions: List[str] = []
        self.pii_used: List[str] = []
        self.cte_names: Set[str] = set()
        self.consumed: Set[int] = set()
        self.hint_ok: Set[int] = set()       # WITH tokens validated as hints
        self.scopes: List[_Scope] = []
        self.toks: List[Token] = []
        self.param_order: List[str] = []
        self.cte_explicit: Dict[str, Set[str]] = {}
        self.cte_select: Dict[str, int] = {}

    # --- helpers
    def warn(self, msg):
        if msg not in self.warnings:
            self.warnings.append(msg)

    def lookup(self, parts: List[str]) -> Optional[ObjectInfo]:
        if len(parts) == 1:
            return self.cat.get("dbo." + parts[0])
        return self.cat.get(parts[0] + "." + parts[1])

    # --- phases
    def run(self) -> List[Token]:
        toks = self.toks = tokenize(self.sql)
        if len(toks) > MAX_TOKENS:
            raise _Reject("statement too large (%d tokens)" % len(toks))
        self._strip_semicolon()
        if not toks:
            raise _Reject("empty statement")
        self._depths()
        self._token_rules()
        self._head()
        self._build_scopes()
        for sc in self.scopes:
            self._parse_sources(sc)
        for i, t in enumerate(toks):
            if (t.is_word("WITH") and i > 0 and i not in self.consumed
                    and not (i + 1 < len(toks) and toks[i + 1].is_word("ROLLUP", "CUBE"))):
                raise _Reject("WITH is only allowed at the start of the statement or as a NOLOCK hint")
        self._check_calls_and_dotted()
        self._sensitive_columns()
        self._expand_stars()
        self._inject_top()
        self._tenant_warnings()
        return toks

    def _strip_semicolon(self):
        toks = self.toks
        if toks and toks[-1].is_punct(";"):
            toks.pop()
        for t in toks:
            if t.is_punct(";"):
                raise _Reject("multiple statements / semicolon inside the statement are not allowed")

    def _depths(self):
        stack = []
        d = 0
        for i, t in enumerate(self.toks):
            if t.is_punct("("):
                t.depth = d
                stack.append(i)
                d += 1
                if d > MAX_DEPTH:
                    raise _Reject("nesting too deep")
            elif t.is_punct(")"):
                if not stack:
                    raise _Reject("unbalanced ')'")
                d -= 1
                t.depth = d
                j = stack.pop()
                t.match = j
                self.toks[j].match = i
            else:
                t.depth = d
        if stack:
            raise _Reject("unbalanced '('")

    def _token_rules(self):
        toks = self.toks
        first = toks[0]
        if not first.is_word("SELECT", "WITH"):
            raise _Reject("only SELECT or WITH ... SELECT statements are allowed (found %r)" % first.text)
        for i, t in enumerate(toks):
            prev = toks[i - 1] if i else None
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if t.kind == W:
                up = t.up
                if up in FORBIDDEN_KEYWORDS:
                    raise _Reject("keyword %s is not allowed" % up)
                if up == "OPTION":
                    self._check_option(i)
            if t.is_ident:
                name = t.value
                low = name.lower()
                if low.startswith("sp_") or low.startswith("xp_"):
                    raise _Reject("system procedure / extended procedure name %r is not allowed" % name)
                if is_secret_name(name):
                    raise _Reject("column or object %r looks like a credential and can never be queried" % name)
            if t.kind == V:
                low = t.value.lower()
                if low not in self.allowed_params:
                    raise _Reject("variable @%s is not an allowed parameter (DECLARE/SET are not available)" % t.value)
                if nxt is not None and nxt.kind == O and (nxt.text == "=" or nxt.text in ASSIGN_OPS):
                    if prev is not None and (prev.is_word("SELECT", "DISTINCT", "ALL") or prev.is_punct(",")
                                             or prev.kind == N):
                        raise _Reject("variable assignment in SELECT is not allowed")
            if t.kind == O and t.text in ASSIGN_OPS:
                raise _Reject("compound assignment operator %s is not allowed" % t.text)
            if t.is_punct(".") and not (
                    prev is not None and prev.is_ident and nxt is not None
                    and (nxt.is_ident or (nxt.kind == O and nxt.text == "*"))):
                raise _Reject("unexpected '.'")

    def _check_option(self, i):
        toks = self.toks
        t = toks[i]
        if t.depth != 0 or i + 1 >= len(toks) or not toks[i + 1].is_punct("("):
            raise _Reject("OPTION is only allowed as a trailing clause")
        close = toks[i + 1].match
        if close != len(toks) - 1:
            raise _Reject("OPTION must be the last clause")
        inner = [x.text.upper() for x in toks[i + 2:close]]
        ok = inner == ["RECOMPILE"] or (
            len(inner) == 2 and inner[0] == "MAXRECURSION" and inner[1].isdigit()
            and 0 < int(inner[1]) <= 1000)
        if not ok:
            raise _Reject("only OPTION (RECOMPILE) and OPTION (MAXRECURSION n<=1000) are allowed")
        for k in range(i, len(toks)):
            self.consumed.add(k)

    def _head(self):
        """Parse the WITH (CTE) header, if any."""
        toks = self.toks
        self.main_start = 0
        if not toks[0].is_word("WITH"):
            return
        p = 1
        while True:
            if p >= len(toks) or not toks[p].is_ident:
                raise _Reject("malformed WITH clause")
            cname = toks[p].value.lower()
            self.cte_names.add(cname)
            self.consumed.add(p)
            p += 1
            if p < len(toks) and toks[p].is_punct("("):
                m = toks[p].match
                for k in range(p, m + 1):
                    self.consumed.add(k)
                self.cte_explicit[cname] = {t.value.lower() for t in toks[p + 1:m] if t.is_ident}
                p = m + 1
            if p >= len(toks) or not toks[p].is_word("AS"):
                raise _Reject("malformed WITH clause (expected AS)")
            p += 1
            if p >= len(toks) or not toks[p].is_punct("(") or p + 1 >= len(toks) \
                    or not toks[p + 1].is_word("SELECT"):
                raise _Reject("CTE body must be a parenthesised SELECT")
            self.cte_select[cname] = p + 1
            p = toks[p].match + 1
            if p < len(toks) and toks[p].is_punct(","):
                p += 1
                continue
            break
        if p >= len(toks) or not toks[p].is_word("SELECT"):
            raise _Reject("WITH must be followed by a SELECT statement")
        self.main_start = p

    def _build_scopes(self):
        toks = self.toks
        n = len(toks)
        owner: List[Optional[_Scope]] = [None] * n
        for i, t in enumerate(toks):
            if not t.is_word("SELECT"):
                continue
            d = t.depth
            if d == 0 and i != self.main_start and not toks[i - 1].is_word("UNION", "ALL", "INTERSECT", "EXCEPT"):
                raise _Reject("only one statement is allowed (second SELECT at top level)")
            j = i + 1
            while j < n:
                if toks[j].depth < d:
                    break
                if toks[j].depth == d and toks[j].is_word("UNION", "INTERSECT", "EXCEPT"):
                    break
                j += 1
            sc = _Scope(start=i, end=j, depth=d, parent=owner[i])
            # FROM / head
            k = i + 1
            while k < j:
                if toks[k].depth == d and toks[k].is_word("FROM"):
                    sc.from_idx = k
                    break
                k += 1
            r = (sc.from_idx + 1) if sc.from_idx is not None else j
            while r < j:
                if toks[r].depth == d and toks[r].kind == W and toks[r].up in SOURCE_TERMINATORS:
                    break
                r += 1
            sc.region_end = r
            for x in range(i, j):
                owner[x] = sc
            self.scopes.append(sc)
        self.owner = owner
        for sc in self.scopes:
            sc.head_end = self._head_end(sc)

    def _head_end(self, sc: _Scope) -> int:
        toks = self.toks
        p = sc.start + 1
        if p < len(toks) and toks[p].is_word("DISTINCT", "ALL"):
            p += 1
        if p < len(toks) and toks[p].is_word("TOP"):
            p += 1
            if p >= len(toks):
                raise _Reject("malformed TOP")
            if toks[p].is_punct("("):
                p = toks[p].match + 1
            else:
                p += 1
            if p < len(toks) and toks[p].is_word("PERCENT"):
                raise _Reject("TOP ... PERCENT is not allowed")
            if p < len(toks) and toks[p].is_word("WITH") and p + 1 < len(toks) and toks[p + 1].is_word("TIES"):
                raise _Reject("TOP ... WITH TIES is not allowed")
        return p

    # --- output column names of a SELECT (for CTE / derived-table column checks)
    def _output_columns(self, sc: _Scope) -> Optional[Set[str]]:
        toks = self.toks
        stop = sc.from_idx if sc.from_idx is not None else sc.end
        names: Set[str] = set()
        item: List[Token] = []
        items: List[List[Token]] = []
        for i in range(sc.head_end, stop):
            t = toks[i]
            if t.depth == sc.depth and t.is_punct(","):
                items.append(item)
                item = []
            else:
                item.append(t)
        items.append(item)
        for it in items:
            if not it:
                return None
            if it[-1].kind == O and it[-1].text == "*":
                return None
            if len(it) >= 2 and it[-2].is_word("AS") and it[-1].is_ident:
                names.add(it[-1].value.lower())
            elif len(it) == 1 and it[0].is_ident:
                names.add(it[0].value.lower())
            elif (len(it) >= 3 and it[-1].is_ident and it[-2].is_punct(".") and it[-3].is_ident
                    and all(x.is_ident or x.is_punct(".") for x in it)):
                names.add(it[-1].value.lower())
            elif len(it) >= 2 and it[-1].is_ident and (it[-2].is_punct(")") or it[-2].kind in (S, N)):
                names.add(it[-1].value.lower())
            # else: unnamed expression - no name to check
        return names

    def _source_columns(self, src: "_Src") -> Optional[Set[str]]:
        """Known output columns of a CTE / derived-table source (None = unknown)."""
        sel = None
        if src.kind == "cte":
            if src.name.lower() in self.cte_explicit:
                return self.cte_explicit[src.name.lower()]
            sel = self.cte_select.get(src.name.lower())
        elif src.kind == "derived":
            sel = src.first + 1
        if sel is None:
            return None
        for sc in self.scopes:
            if sc.start == sel:
                return self._output_columns(sc)
        return None

    # --- FROM parsing
    def _parse_sources(self, sc: _Scope):
        toks = self.toks
        if sc.from_idx is None:
            return
        d = sc.depth
        p = sc.from_idx + 1
        end = sc.region_end
        expect = True
        while p < end:
            t = toks[p]
            if expect:
                if t.is_punct("("):
                    m = t.match
                    if not toks[p + 1].is_word("SELECT", "VALUES"):
                        raise _Reject("parenthesised FROM source must be a subquery")
                    # inner tokens stay analysable (they belong to the child scope)
                    self.consumed.add(p)
                    self.consumed.add(m)
                    src = _Src("derived", "(subquery)", None, None, [], p, m)
                    p = m + 1
                    p = self._alias(src, toks, p, end, "derived")
                    sc.sources.append(src)
                    expect = False
                    continue
                if not t.is_ident:
                    raise _Reject("unexpected token %r in FROM clause" % t.text)
                parts, q = self._dotted(p)
                for k in range(p, q):
                    self.consumed.add(k)
                if q < len(toks) and toks[q].is_punct("(") and q < end:
                    src = self._resolve_callable(parts, p, q)
                    if src.kind == "table":
                        # legacy "table (NOLOCK)" hint syntax
                        self._check_hints(q, toks[q].match, legacy=True)
                        p = toks[q].match + 1
                        src.last = p - 1
                    else:
                        m = toks[q].match
                        # only the parens are consumed: arguments are still analysed
                        self.consumed.add(q)
                        self.consumed.add(m)
                        p = m + 1
                        src.last = m
                else:
                    src = self._resolve_table(parts, p)
                    p = q
                    src.last = q - 1
                p = self._alias(src, toks, p, end, src.kind)
                sc.sources.append(src)
                expect = False
                continue
            # not expecting a source
            if t.is_word("JOIN", "APPLY") and t.depth == d:
                expect = True
                p += 1
                continue
            if t.is_punct(",") and t.depth == d:
                expect = True
                p += 1
                continue
            if t.is_punct("("):
                p = t.match + 1
                continue
            p += 1
        if expect:
            raise _Reject("incomplete FROM clause")

    def _dotted(self, p) -> Tuple[List[str], int]:
        toks = self.toks
        parts = [toks[p].value]
        q = p + 1
        while q + 1 < len(toks) and toks[q].is_punct(".") and toks[q + 1].is_ident:
            parts.append(toks[q + 1].value)
            q += 2
        return parts, q

    def _alias(self, src: _Src, toks, p, end, kind) -> int:
        alias = None
        if p < end and toks[p].is_word("AS"):
            p += 1
            if p >= end or not toks[p].is_ident:
                raise _Reject("alias expected after AS")
            alias = toks[p].value
            self.consumed.add(p - 1)
            self.consumed.add(p)
            p += 1
        elif p < end and toks[p].is_ident and not (toks[p].kind == W and toks[p].up in NOT_AN_ALIAS):
            alias = toks[p].value
            self.consumed.add(p)
            p += 1
        src.alias = alias
        # legacy hint "alias (NOLOCK)" / derived-column list "alias (a, b)"
        if p < end and toks[p].is_punct("("):
            m = toks[p].match
            if kind in ("table", "view"):
                self._check_hints(p, m, legacy=True)
            else:
                self._check_column_list(p, m)
            p = m + 1
        if p < end and toks[p].is_word("WITH") and p + 1 < len(toks) and toks[p + 1].is_punct("("):
            m = toks[p + 1].match
            if kind in ("table", "view"):
                self._check_hints(p + 1, m, legacy=False)
                self.consumed.add(p)
            elif kind == "builtin":
                for k in range(p, m + 1):
                    self.consumed.add(k)
                self.hint_ok.add(p)
            else:
                raise _Reject("WITH (...) is not allowed after this source")
            p = m + 1
        src.last = p - 1
        if kind in ("table", "view"):
            bare = src.name
            src.keys = [alias.lower()] if alias else [bare.lower(), bare.split(".")[-1].lower()]
        elif kind in ("cte",):
            src.keys = [(alias or src.name).lower()]
        else:
            src.keys = [alias.lower()] if alias else []
        return p

    def _check_hints(self, open_i, close_i, legacy):
        toks = self.toks
        names = []
        for k in range(open_i + 1, close_i):
            t = toks[k]
            if t.is_punct(","):
                continue
            if t.kind == W and t.up in ALLOWED_TABLE_HINTS:
                names.append(t.up)
                continue
            raise _Reject("table hint %r is not allowed (only NOLOCK / READUNCOMMITTED; "
                          "the runner already uses READ UNCOMMITTED)" % t.text)
        if not names:
            raise _Reject("empty table hint list")
        for k in range(open_i, close_i + 1):
            self.consumed.add(k)

    def _check_column_list(self, open_i, close_i):
        toks = self.toks
        for k in range(open_i + 1, close_i):
            if not (toks[k].is_ident or toks[k].is_punct(",")):
                raise _Reject("unexpected token in alias column list")
        for k in range(open_i, close_i + 1):
            self.consumed.add(k)

    def _resolve_callable(self, parts, p, q) -> _Src:
        """name ( ... ) in FROM: function, built-in TVF, or table + legacy hint."""
        if len(parts) == 1 and parts[0].upper() in BUILTIN_TVFS:
            return _Src("builtin", parts[0].upper(), None, None, [], p, q)
        if len(parts) == 1 and parts[0].lower() in self.cte_names:
            raise _Reject("CTE %r cannot be called like a function" % parts[0])
        if len(parts) > 2:
            raise _Reject("cross-database / linked-server names are not allowed: %s" % ".".join(parts))
        if len(parts) == 1:
            raise _Reject("function call %s( ... ) must be schema-qualified (e.g. dbo.%s)" % (parts[0], parts[0]))
        info = self.lookup(parts)
        if info is not None and info.type in ("table", "view"):
            return self._resolve_table(parts, p)
        return self._resolve_function(parts, info, p)

    def _resolve_function(self, parts, info, p) -> _Src:
        name = ".".join(parts)
        if info is None:
            raise _Reject("unknown object %s (not in the %s catalog)" % (name, self.region))
        if info.type != "function":
            raise _Reject("%s is a %s - only tables, views and functions can be read" % (info.name, info.type))
        self._check_region(info)
        if info.called_procs or info.written_tables:
            raise _Reject("function %s calls procedures or writes tables - not allowed" % info.name)
        if info.name not in self.functions:
            self.functions.append(info.name)
        return _Src("function", info.name, None, info, [], p, p)

    def _check_region(self, info: ObjectInfo):
        if not info.in_region(self.region):
            raise _Reject("%s does not exist in region %s (available in: %s)"
                          % (info.name, self.region, "/".join(info.regions) or "none"))

    def _resolve_table(self, parts, p) -> _Src:
        name = ".".join(parts)
        if len(parts) > 2:
            raise _Reject("cross-database / linked-server names are not allowed: %s" % name)
        if len(parts) == 1 and parts[0].lower() in self.cte_names:
            return _Src("cte", parts[0], None, None, [], p, p)
        info = self.lookup(parts)
        if info is None:
            raise _Reject("unknown object %s (not in the %s catalog)" % (name, self.region))
        if info.type == "function":
            raise _Reject("%s is a function; call it with arguments" % info.name)
        if info.type not in ("table", "view"):
            raise _Reject("%s is a %s - only tables, views and functions can be read" % (info.name, info.type))
        self._check_region(info)
        if is_secret_table(info.name):
            raise _Reject("table %s holds credentials / secrets and can never be queried" % info.name)
        if info.is_backup:
            self.warn("%s looks like a backup/copy table" % info.name)
        if info.name not in self.objects:
            self.objects.append(info.name)
        return _Src(info.type, info.name, None, info, [], p, p)

    # --- calls, dotted names
    def _scope_chain(self, idx):
        sc = self.owner[idx]
        while sc is not None:
            yield sc
            sc = sc.parent

    def _find_source(self, idx, key) -> Optional[_Src]:
        for sc in self._scope_chain(idx):
            for s in sc.sources:
                if key in s.keys:
                    return s
        return None

    def _check_calls_and_dotted(self):
        toks = self.toks
        n = len(toks)
        i = 0
        # CTE-body column lists were consumed in _head
        while i < n:
            t = toks[i]
            if not t.is_ident or i in self.consumed:
                i += 1
                continue
            prev = toks[i - 1] if i else None
            if prev is not None and prev.is_punct("."):
                i += 1
                continue
            # chain
            parts, q = self._dotted(i)
            nxt = toks[q] if q < n else None
            is_call = nxt is not None and nxt.is_punct("(")
            if len(parts) == 1:
                if is_call:
                    self._check_plain_call(i, t)
                i = q
                continue
            if is_call:
                if len(parts) > 2:
                    raise _Reject("cross-database call %s( ... ) is not allowed" % ".".join(parts))
                info = self.lookup(parts)
                if info is None and parts[0].lower() in SYSTEM_QUALIFIERS:
                    raise _Reject("system function %s is not allowed" % ".".join(parts))
                self._resolve_function(parts, info, i)
                i = q
                continue
            self._check_column_ref(i, parts, q)
            i = q

    def _check_plain_call(self, i, t):
        toks = self.toks
        if t.kind == Q:
            raise _Reject("quoted function name %s is not allowed" % t.text)
        up = t.up
        if up in BUILTIN_FUNCTIONS or up in TYPE_NAMES or up in KEYWORDS_BEFORE_PAREN:
            return
        # CTE definition header "name (cols) AS (": consumed in _head
        raise _Reject("function %s() is not on the allowlist" % t.text)

    def _check_column_ref(self, i, parts, q):
        toks = self.toks
        first = parts[0].lower()
        src = self._find_source(i, first)
        col = None
        if src is not None:
            col = parts[1] if len(parts) == 2 else None
            if len(parts) > 2:
                raise _Reject("over-qualified name %s" % ".".join(parts))
        elif len(parts) == 3:
            src = self._find_source(i, (parts[0] + "." + parts[1]).lower())
            if src is None:
                raise _Reject("reference to %s is not allowed (cross-database or unknown object)" % ".".join(parts))
            col = parts[2]
        else:
            if first in SYSTEM_QUALIFIERS:
                raise _Reject("reference to system object %s is not allowed" % ".".join(parts))
            if len(parts) > 3:
                raise _Reject("over-qualified name %s" % ".".join(parts))
            self.warn("could not resolve qualifier %r (column checks skipped)" % parts[0])
            return
        if col is not None and src.kind in ("table",) and src.info is not None and src.info.columns:
            cols = {c.lower() for c in src.info.columns_for(self.region)}
            if col.lower() not in cols:
                raise _Reject("column %s does not exist in %s for region %s"
                              % (col, src.info.name, self.region))
        elif col is not None and src.kind in ("cte", "derived"):
            known = self._source_columns(src)
            if known is not None and col.lower() not in known:
                raise _Reject("column %s is not produced by %s %s (available: %s)"
                              % (col, src.kind, src.alias or src.name, ", ".join(sorted(known))))

    # --- sensitive columns
    def _sensitive_columns(self):
        tables = [s.info for sc in self.scopes for s in sc.sources
                  if s.kind in ("table", "view") and s.info is not None]
        pii: Dict[str, str] = {}
        for info in tables:
            for c, kind in info.pii.items():
                if kind != FREE_TEXT_KIND:
                    pii[c] = kind
        if not pii:
            return
        for t in self.toks:
            if t.is_ident and t.value.lower() in pii:
                if not self.allow_pii:
                    raise _Reject("column %s holds personal data (%s) and cannot be used in ad-hoc SQL; "
                                  "use an approved query template instead" % (t.value, pii[t.value.lower()]))
                if t.value not in self.pii_used:
                    self.pii_used.append(t.value)

    def _unsafe_columns(self, info: ObjectInfo) -> Set[str]:
        bad = set()
        for c in info.columns_for(self.region):
            if is_secret_name(c):
                bad.add(c.lower())
            elif not self.allow_pii and info.pii.get(c.lower(), FREE_TEXT_KIND) != FREE_TEXT_KIND:
                bad.add(c.lower())
        return bad

    def _expand_stars(self):
        toks = self.toks
        for sc in self.scopes:
            stop = sc.from_idx if sc.from_idx is not None else sc.end
            for i in range(sc.head_end, stop):
                t = toks[i]
                if not (t.kind == O and t.text == "*") or t.depth != sc.depth:
                    continue
                prev = toks[i - 1]
                if prev.is_punct("."):
                    self._star_qualified(sc, i)
                elif i == sc.head_end or prev.is_punct(","):
                    self._star_plain(sc, i)

    def _in_exists(self, sc: _Scope) -> bool:
        toks = self.toks
        i = sc.start
        return i >= 2 and toks[i - 1].is_punct("(") and toks[i - 2].is_word("EXISTS")

    def _col_list(self, qualifier: str, info: ObjectInfo) -> str:
        bad = self._unsafe_columns(info)
        cols = [c for c in info.columns_for(self.region) if c.lower() not in bad]
        return ", ".join("%s.[%s]" % (qualifier, c.replace("]", "]]")) for c in cols)

    @staticmethod
    def _qual(src: _Src) -> str:
        if src.alias:
            return "[%s]" % src.alias.replace("]", "]]")
        return ".".join("[%s]" % p for p in src.name.split("."))

    def _star_qualified(self, sc, i):
        toks = self.toks
        a = toks[i - 2]
        if not a.is_ident:
            raise _Reject("malformed qualified *")
        src = self._find_source(i, a.value.lower())
        if src is None:
            raise _Reject("unknown alias %s in %s.*" % (a.value, a.value))
        if self._in_exists(sc):
            return
        self._maybe_expand([(src, a)], toks, i - 2, i, sc)

    def _star_plain(self, sc, i):
        if sc.from_idx is None:
            raise _Reject("SELECT * without FROM")
        if self._in_exists(sc):
            return
        self._maybe_expand([(s, None) for s in sc.sources], self.toks, i, i, sc)

    def _maybe_expand(self, sources, toks, first, last, sc):
        need = False
        for src, _ in sources:
            if src.kind == "view":
                raise _Reject("SELECT * over view %s: list the columns explicitly" % src.name)
            if src.kind == "table" and src.info is not None and self._unsafe_columns(src.info):
                need = True
            if src.kind == "function":
                self.warn("SELECT * over function %s: result columns are not in the catalog; "
                          "output is masked but not column-filtered" % src.name)
        if not need:
            return
        parts = []
        for src, _ in sources:
            if src.kind == "table":
                parts.append(self._col_list(self._qual(src), src.info))
            elif src.kind in ("cte", "derived", "function", "builtin"):
                if len(sources) > 1 and src.kind in ("function", "builtin", "derived") and not src.alias:
                    raise _Reject("SELECT * over a join containing an unaliased %s cannot be rewritten; "
                                  "give it an alias or list columns" % src.kind)
                q = src.alias or src.name
                parts.append("[%s].*" % q.replace("]", "]]"))
        names = ", ".join(s.name for s, _ in sources if s.kind == "table")
        for k in range(first, last + 1):
            toks[k].skip = True
        toks[first].skip = False
        toks[first].text = ", ".join(parts)
        toks[first].kind = "SYNTH"
        self.rewrites.append("expanded SELECT * on %s to explicit non-sensitive columns" % names)

    # --- row limit
    def _inject_top(self):
        toks = self.toks
        lim = self.row_limit
        for sc in self.scopes:
            if sc.depth != 0:
                continue
            p = sc.start + 1
            if toks[p].is_word("DISTINCT", "ALL"):
                p += 1
            if toks[p].is_word("TOP"):
                a = toks[p + 1]
                if a.kind == N:
                    self._clamp_number(a, lim)
                elif a.kind == V:
                    self._clamp_var(a, lim)
                elif a.is_punct("(") and toks[p + 3].is_punct(")") and p + 3 == a.match \
                        and toks[p + 2].kind in (N, V):
                    inner = toks[p + 2]
                    if inner.kind == N:
                        self._clamp_number(inner, lim)
                    else:
                        self._clamp_var(inner, lim)
                else:
                    raise _Reject("TOP must be a literal number or a parameter")
            else:
                toks[p - 1].post += " TOP %d " % lim
                self.rewrites.append("injected TOP %d" % lim)

    def _clamp_number(self, tok, lim):
        if not tok.text.isdigit():
            raise _Reject("TOP must be a positive integer")
        if int(tok.text) > lim:
            self.rewrites.append("clamped TOP %s to %d" % (tok.text, lim))
            tok.text = str(lim)
        elif int(tok.text) == 0:
            raise _Reject("TOP 0 is pointless")

    def _clamp_var(self, tok, lim):
        tok.text = "(CASE WHEN %s > 0 AND %s < %d THEN %s ELSE %d END)" % (
            tok.text, tok.text, lim, tok.text, lim)
        tok.kind = "SYNTH_EXPR"
        self.rewrites.append("clamped TOP parameter to %d" % lim)

    # --- tenant predicate warning
    def _tenant_warnings(self):
        toks = self.toks
        for sc in self.scopes:
            tenant_unqualified = False
            qualified: Set[str] = set()
            pk_eq: Set[Tuple[str, str]] = set()
            for i in range(sc.start, sc.end):
                t = toks[i]
                if not t.is_ident:
                    continue
                prev = toks[i - 1] if i else None
                nxt = toks[i + 1] if i + 1 < len(toks) else None
                cmp_adj = ((prev is not None and ((prev.kind == O and prev.text in COMPARISON_OPS)
                                                  or prev.is_word(*COMPARISON_WORDS)))
                           or (nxt is not None and ((nxt.kind == O and nxt.text in COMPARISON_OPS)
                                                    or nxt.is_word(*COMPARISON_WORDS))))
                if t.value.lower() == "tenantid" and cmp_adj:
                    if prev is not None and prev.is_punct("."):
                        qualified.add(toks[i - 2].value.lower())
                    else:
                        tenant_unqualified = True
            for src in sc.sources:
                if src.kind != "table" or src.info is None or not src.info.has_tenant_id:
                    continue
                if tenant_unqualified or any(k in qualified for k in src.keys):
                    continue
                if self._pk_lookup(sc, src):
                    continue
                self.warn("%s (alias %s) has a TenantId column but no TenantId predicate - "
                          "add one to avoid cross-tenant results" % (src.info.name, src.alias or "-"))

    def _pk_lookup(self, sc, src) -> bool:
        toks = self.toks
        pk = {c.lower() for c in src.info.primary_key}
        if not pk:
            return False
        for i in range(sc.start, sc.end):
            t = toks[i]
            if t.is_ident and t.value.lower() in pk and i >= 2 and toks[i - 1].is_punct(".") \
                    and toks[i - 2].is_ident and toks[i - 2].value.lower() in src.keys:
                prev = toks[i - 3] if i >= 3 else None
                nxt = toks[i + 1] if i + 1 < len(toks) else None
                if (nxt is not None and nxt.kind == O and nxt.text == "=") or \
                        (prev is not None and prev.kind == O and prev.text == "="):
                    return True
        return False

    # --- output
    def build(self, positional: bool) -> Tuple[str, List[str]]:
        out: List[str] = []
        order: List[str] = []
        prev_emitted: Optional[Token] = None
        for t in self.toks:
            if t.skip:
                continue
            text = t.text
            if t.kind == V and positional:
                text = "?"
                order.append(t.value)
            elif t.kind == "SYNTH_EXPR" and positional:
                order.extend(re.findall(r"@(\w+)", t.text))
                text = re.sub(r"@\w+", "?", t.text)
            if prev_emitted is not None:
                gap = t.gap if prev_emitted.post == "" else ""
                if gap == "" and not prev_emitted.post and _needs_space(prev_emitted, t):
                    gap = " "
                out.append(gap)
            out.append(text)
            if t.post:
                out.append(t.post)
            prev_emitted = t
        return "".join(out).strip(), order


def _needs_space(a: Token, b: Token) -> bool:
    return bool(re.match(r"[A-Za-z0-9_@]", a.text[-1:])) and bool(re.match(r"[A-Za-z0-9_@]", b.text[:1]))


# ------------------------------------------------------------------- public

def validate(sql: str, *, region: str, catalog: Optional[SchemaCatalog] = None,
             row_limit: int = DEFAULT_ROW_LIMIT, allowed_params: Iterable[str] = (),
             allow_pii: bool = False) -> SafeSqlResult:
    """Validate and rewrite `sql` for execution on `region` ("EU" or "APAC").

    allowed_params: names (with or without '@') that may appear as variables;
                    used by templates.  Ad-hoc SQL passes none.
    allow_pii:      True for vetted templates (PII columns may be selected and
                    are masked later); False for ad-hoc SQL.
    """
    res = SafeSqlResult(ok=False, region=region)
    warnings: List[str] = []
    try:
        region = (region or "").upper()
        res.region = region
        if region not in REGIONS:
            raise _Reject("unknown region %r (use EU or APAC)" % region)
        if not isinstance(sql, str) or not sql.strip():
            raise _Reject("empty SQL")
        if len(sql) > MAX_SQL_CHARS:
            raise _Reject("SQL too long (%d chars, max %d)" % (len(sql), MAX_SQL_CHARS))
        try:
            limit = int(row_limit)
        except (TypeError, ValueError):
            raise _Reject("row_limit must be an integer")
        if limit < 1:
            raise _Reject("row_limit must be >= 1")
        if limit > MAX_ROW_LIMIT:
            warnings.append("row_limit %d clamped to %d" % (limit, MAX_ROW_LIMIT))
            limit = MAX_ROW_LIMIT
        res.row_limit = limit
        cat = catalog or get_catalog()
        an = _Analyzer(sql, cat, region, limit, allowed_params, allow_pii)
        an.run()
        res.rewritten_sql, _ = an.build(positional=False)
        res.positional_sql, res.param_order = an.build(positional=True)
        res.warnings = warnings + an.warnings
        res.rewrites = an.rewrites
        res.objects = an.objects
        res.functions = an.functions
        res.pii_columns_used = an.pii_used
        res.ok = True
    except _Reject as e:
        res.errors.append(str(e))
        res.warnings = warnings
    except IndexError:
        res.errors.append("malformed statement")
    except RecursionError:
        res.errors.append("statement too complex")
    except Exception as e:  # noqa: BLE001 - fail closed on any validator bug
        res.errors.append("internal validator error (%s) - statement rejected" % e.__class__.__name__)
        res.internal_error = True
    return res
