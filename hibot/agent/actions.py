"""JSON contracts of the LLM <-> orchestrator protocol (structure only; semantic checks happen in executor.py)."""
from __future__ import annotations

from typing import Any, Dict, List

from hibot.llm.jsonio import validate_schema

CLASSIFICATIONS = ("code_defect", "master_data", "access", "data_correction", "integration", "config",
                   "user_error", "unknown")
EVIDENCE_TYPES = ("query", "code", "similar_case", "card")
ACTIONS = ("run_query", "run_sql", "inspect_code", "read_source", "final_report")
CODE_KINDS = ("endpoint", "screen", "db_object", "error_text", "text")

_WHY = {"type": "string", "maxLength": 600}
_REGION = {"type": "string", "enum": ["EU", "APAC"]}

REPORT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["summary", "classification", "root_cause", "confidence", "evidence", "fix_steps"],
    "properties": {
        "summary": {"type": "string", "minLength": 3, "maxLength": 1500},
        "classification": {"type": "string", "enum": list(CLASSIFICATIONS)},
        "root_cause": {"type": "string", "minLength": 3, "maxLength": 3000},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence": {"type": "array", "maxItems": 20, "items": {
            "type": "object", "required": ["type", "ref", "finding"],
            "properties": {"type": {"type": "string", "enum": list(EVIDENCE_TYPES)},
                           "ref": {"type": "string", "maxLength": 300},
                           "finding": {"type": "string", "maxLength": 1500}}}},
        "code_locations": {"type": "array", "maxItems": 20, "items": {
            "type": "object", "required": ["repo", "path", "why"],
            "properties": {"repo": {"type": "string"}, "path": {"type": "string"},
                           "line": {"type": ["integer", "null"], "minimum": 0}, "why": {"type": "string"}}}},
        "fix_steps": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 800}},
        "verification_query_ids": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
        "suggested_card": {"type": "object"},
    },
}

_FINAL = {"type": "object", "required": ["action"] + REPORT_SCHEMA["required"],
          "properties": dict(REPORT_SCHEMA["properties"], action={"type": "string", "enum": ["final_report"]})}

ACTION_SCHEMAS: Dict[str, dict] = {
    "run_query": {"type": "object", "required": ["action", "query_id"], "properties": {
        "action": {"type": "string", "enum": ["run_query"]}, "query_id": {"type": "string", "minLength": 1, "maxLength": 80},
        "params": {"type": "object"}, "region": _REGION, "why": _WHY}},
    "run_sql": {"type": "object", "required": ["action", "sql"], "properties": {
        "action": {"type": "string", "enum": ["run_sql"]}, "sql": {"type": "string", "minLength": 6, "maxLength": 4000},
        "region": _REGION, "why": _WHY}},
    "inspect_code": {"type": "object", "required": ["action", "target"], "properties": {
        "action": {"type": "string", "enum": ["inspect_code"]}, "why": _WHY,
        "target": {"type": "object", "required": ["kind", "value"], "properties": {
            "kind": {"type": "string", "enum": list(CODE_KINDS)}, "value": {"type": "string", "minLength": 1, "maxLength": 300},
            "app": {"type": "string"}}}}},
    "read_source": {"type": "object", "required": ["action", "path"], "properties": {
        "action": {"type": "string", "enum": ["read_source"]}, "path": {"type": "string", "minLength": 1, "maxLength": 400},
        "start_line": {"type": "integer", "minimum": 1}, "end_line": {"type": "integer", "minimum": 1}, "why": _WHY}},
    "final_report": _FINAL,
}

# Schema passed to the LLM client as a hint (and used for the first-level check).
ACTION_ENVELOPE: Dict[str, Any] = {"type": "object", "required": ["action"],
                                   "properties": {"action": {"type": "string", "enum": list(ACTIONS)}}}


def validate_action(obj: Any, only_final: bool = False) -> List[str]:
    """Structure validation of one LLM answer.  Returns errors (empty = ok)."""
    if not isinstance(obj, dict):
        return ["answer must be a JSON object"]
    act = obj.get("action")
    if only_final and act != "final_report":
        return ["the investigation budget is exhausted: answer with action=final_report only"]
    if act not in ACTION_SCHEMAS:
        return ["$.action: must be one of %s (got %r)" % (list(ACTIONS), act)]
    return validate_schema(obj, ACTION_SCHEMAS[act])


def split_report(action: dict) -> dict:
    return {k: v for k, v in action.items() if k != "action"}
