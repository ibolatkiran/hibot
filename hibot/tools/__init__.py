"""HiBot tool layer: investigation tools shared by HiBot's own LLM loop and the MCP server (hibot.mcp_server).

Every tool is a plain function returning a compact JSON-serializable dict (size-capped, secrets/PII scrubbed, read-only).
`call_tool(name, args)` validates and dispatches; `tool_catalog()` renders the ArGPT prompt text.
"""
from .code import code_search, read_file
from .data import (cards_list, cards_match, column_writers, graph_search_error_text, graph_search_text,
                   graph_trace_db_object, graph_trace_endpoint, graph_trace_screen, list_templates, run_template,
                   set_runner_factory, similar_tickets, sql, ticket, ticket_window)
from .git import git_blame, git_log
from .registry import BY_NAME, TOOLS, call_tool, tool_catalog, validate_args

__all__ = [t.name for t in TOOLS] + ["call_tool", "tool_catalog", "TOOLS", "BY_NAME", "validate_args", "set_runner_factory"]
