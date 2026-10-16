"""Robust JSON extraction + tiny JSON-schema validator + repair loop.

Supported schema keywords: type (str|list; null/boolean/integer/number/string/array/object), enum, required,
properties, additionalProperties (False only), items, minimum, maximum, minLength, maxLength, minItems, maxItems.
Unknown keywords are ignored.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

_FENCE = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)```", re.S)


class JsonExtractError(ValueError):
    pass


class JsonRepairFailed(RuntimeError):
    def __init__(self, message: str, last_text: str = "", errors: Sequence[str] = ()):
        super().__init__(message)
        self.last_text = last_text
        self.errors = list(errors)


def _strip_trailing_commas(s: str) -> str:
    """Remove ',' directly before '}' or ']' outside of string literals."""
    out, in_str, esc, i = [], False, False, 0
    while i < len(s):
        c = s[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif c == ",":
            j = i + 1
            while j < len(s) and s[j] in " \t\r\n":
                j += 1
            if not (j < len(s) and s[j] in "}]"):
                out.append(c)
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _balanced_candidates(text: str):
    """Yield substrings that start at '{' or '[' and end at the matching bracket (string aware)."""
    n = len(text)
    for start in range(n):
        if text[start] not in "{[":
            continue
        depth, in_str, esc = 0, False, False
        for i in range(start, n):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    yield text[start:i + 1]
                    break


def _try_load(s: str) -> Tuple[bool, Any]:
    for cand in (s, _strip_trailing_commas(s)):
        try:
            return True, json.loads(cand)
        except ValueError:
            continue
    return False, None


def extract_json(text: str) -> Any:
    """Find the first JSON object/array in `text` (fences, prose before/after, trailing commas tolerated)."""
    if not isinstance(text, str) or not text.strip():
        raise JsonExtractError("empty model output")
    t = text.strip().lstrip("﻿")
    ok, val = _try_load(t)
    if ok and isinstance(val, (dict, list)):
        return val
    for m in _FENCE.finditer(t):
        body = m.group(1).strip()
        ok, val = _try_load(body)
        if ok and isinstance(val, (dict, list)):
            return val
        for cand in _balanced_candidates(body):
            ok, val = _try_load(cand)
            if ok and isinstance(val, (dict, list)):
                return val
    for cand in _balanced_candidates(t):
        ok, val = _try_load(cand)
        if ok and isinstance(val, (dict, list)):
            return val
    raise JsonExtractError("no valid JSON object found in model output")


# --------------------------------------------------------------- validation

def _type_ok(v: Any, t: str) -> bool:
    if t == "null":
        return v is None
    if t == "boolean":
        return isinstance(v, bool)
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "string":
        return isinstance(v, str)
    if t == "array":
        return isinstance(v, list)
    if t == "object":
        return isinstance(v, dict)
    return True


def validate_schema(value: Any, schema: dict, path: str = "$") -> List[str]:
    """Return a list of human readable errors (empty = valid)."""
    errs: List[str] = []
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_type_ok(value, x) for x in types):
            return ["%s: expected %s, got %s" % (path, "/".join(types), type(value).__name__)]
    if "enum" in schema and value not in schema["enum"]:
        errs.append("%s: must be one of %s (got %r)" % (path, schema["enum"], value))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append("%s: must be >= %s" % (path, schema["minimum"]))
        if "maximum" in schema and value > schema["maximum"]:
            errs.append("%s: must be <= %s" % (path, schema["maximum"]))
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errs.append("%s: shorter than %d characters" % (path, schema["minLength"]))
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errs.append("%s: longer than %d characters" % (path, schema["maxLength"]))
    if isinstance(value, dict):
        for r in schema.get("required", []):
            if r not in value:
                errs.append("%s: missing required property %r" % (path, r))
        props = schema.get("properties", {})
        for k, sub in props.items():
            if k in value:
                errs.extend(validate_schema(value[k], sub, "%s.%s" % (path, k)))
        if schema.get("additionalProperties") is False:
            for k in value:
                if k not in props:
                    errs.append("%s: unexpected property %r" % (path, k))
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errs.append("%s: needs at least %d items" % (path, schema["minItems"]))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errs.append("%s: at most %d items" % (path, schema["maxItems"]))
        if "items" in schema:
            for i, it in enumerate(value):
                errs.extend(validate_schema(it, schema["items"], "%s[%d]" % (path, i)))
    return errs


# -------------------------------------------------------------- repair loop

def complete_json(client, messages: List[Dict[str, str]], schema: Optional[dict] = None, *,
                  validator: Optional[Callable[[Any], List[str]]] = None, max_retries: int = 2,
                  max_tokens: int = 2000, temperature: float = 0.0) -> Tuple[Any, str, int]:
    """Call the model, parse + validate; on failure re-prompt with the error (max `max_retries` times).

    `validator(obj) -> [errors]` adds checks beyond `schema` (e.g. per-action schemas).
    Returns (obj, raw_text, attempts).  The caller's `messages` list is not modified.
    Raises JsonRepairFailed when all attempts are invalid.  LLMError from the client propagates.
    """
    convo = list(messages)
    last_text, last_errors = "", []
    for attempt in range(1, max_retries + 2):
        text = client.complete(convo, json_schema=schema, max_tokens=max_tokens, temperature=temperature)
        last_text = text
        try:
            obj = extract_json(text)
            errors = validate_schema(obj, schema) if schema else []
            if not errors and validator:
                errors = validator(obj)
        except JsonExtractError as e:
            obj, errors = None, [str(e)]
        if not errors:
            return obj, text, attempt
        last_errors = errors
        if attempt > max_retries:
            break
        convo = convo + [
            {"role": "assistant", "content": text[:4000]},
            {"role": "user", "content": "Your previous answer was invalid:\n- " + "\n- ".join(errors[:8]) +
             "\nReply again with ONLY the corrected JSON object (no prose, no code fence)."}]
    raise JsonRepairFailed("model output still invalid after %d attempts: %s" % (max_retries + 1, "; ".join(last_errors[:3])),
                           last_text, last_errors)
