"""LLM access layer: abstract client, fake client for tests/demo, generic HTTP client, JSON I/O helpers."""
from .client import FakeLLM, HttpChatClient, LLMClient, LLMError, client_from_env
from .jsonio import (JsonExtractError, JsonRepairFailed, complete_json, extract_json, validate_schema)

__all__ = ["FakeLLM", "HttpChatClient", "LLMClient", "LLMError", "client_from_env", "JsonExtractError",
           "JsonRepairFailed", "complete_json", "extract_json", "validate_schema"]
