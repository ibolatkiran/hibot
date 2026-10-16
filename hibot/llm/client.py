"""LLM clients.

The company endpoint has NO tool calling (DECISIONS D8): the model only returns text, which we parse as JSON.
So the whole contract is `complete(messages) -> str`.  Everything provider specific lives in the small
`_FORMATS` adapter table below - adapt it when the endpoint documentation arrives.

Environment (HttpChatClient.from_env / client_from_env("http")):
    HIBOT_LLM_URL          full endpoint URL                       (required)
    HIBOT_LLM_API_KEY      credential, never logged / put in errors (optional)
    HIBOT_LLM_MODEL        model / deployment name                 (optional, sent when set)
    HIBOT_LLM_FORMAT       openai | anthropic | raw | argpt        (default openai; company endpoint = argpt)
    HIBOT_LLM_CLIENT_ID    ArGPT ClientId header (argpt only; secret, never logged)
    HIBOT_LLM_AUTH_HEADER  header name for the key                 (default: Authorization / x-api-key)
    HIBOT_LLM_AUTH_SCHEME  prefix put before the key               (default: "Bearer " for openai/raw, "" for anthropic)
    HIBOT_LLM_JSON_MODE    "1" adds response_format=json_object (openai format only, opt-in)
    HIBOT_LLM_TIMEOUT      seconds per request                     (default 120)
    HIBOT_LLM_RETRIES      retries on 429/5xx/network errors       (default 2)
"""
from __future__ import annotations

import abc
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union


class LLMError(RuntimeError):
    """Transport / protocol problem. Messages never contain credentials."""


class LLMClient(abc.ABC):
    name = "llm"

    @abc.abstractmethod
    def complete(self, messages: List[Dict[str, str]], *, json_schema: Optional[dict] = None,
                 max_tokens: int = 2000, temperature: float = 0.0) -> str:
        """Return the raw model text.  `json_schema` is a hint (some endpoints can enforce it)."""


# ------------------------------------------------------------------ FakeLLM

Scripted = Union[str, dict, list, Callable[[List[Dict[str, str]]], Any]]


class FakeLLM(LLMClient):
    """Scripted responses, consumed in order.

    Each item may be: a str (returned as is), a dict/list (json.dumps'ed), or a callable
    `fn(messages) -> str|dict` for dynamic answers / prompt assertions (raise AssertionError inside).
    `calls` records every call (messages deep-copied) for assertions.
    When the script is exhausted: raise LLMError (or repeat `default` when given).
    """
    name = "fake"

    def __init__(self, responses: Sequence[Scripted] = (), default: Optional[Scripted] = None):
        self._queue = list(responses)
        self._default = default
        self.calls: List[Dict[str, Any]] = []

    def complete(self, messages, *, json_schema=None, max_tokens=2000, temperature=0.0) -> str:
        self.calls.append({"messages": json.loads(json.dumps(messages)), "json_schema": json_schema,
                           "max_tokens": max_tokens, "temperature": temperature})
        if self._queue:
            item = self._queue.pop(0)
        elif self._default is not None:
            item = self._default
        else:
            raise LLMError("FakeLLM script exhausted after %d calls" % len(self.calls))
        if callable(item):
            item = item(messages)
        return item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)

    # -- assertion helpers
    def prompt_text(self, call: int = -1) -> str:
        return "\n".join(m["content"] for m in self.calls[call]["messages"])

    def assert_prompt_contains(self, needle: str, call: int = -1) -> None:
        if needle not in self.prompt_text(call):
            raise AssertionError("%r not found in prompt of call %d" % (needle, call))

    @property
    def remaining(self) -> int:
        return len(self._queue)


# ----------------------------------------------------------- HTTP adapters
# Each format = (request builder, response parser, default auth header, default auth scheme).

def _flatten_openai(messages, model, max_tokens, temperature, json_schema, env):
    body: Dict[str, Any] = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    if model:
        body["model"] = model
    if env.get("HIBOT_LLM_JSON_MODE") == "1":          # opt-in: not every gateway supports it
        body["response_format"] = {"type": "json_object"}
    return body


def _parse_openai(data: Any) -> str:
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise LLMError("unexpected openai-style response shape")


def _flatten_anthropic(messages, model, max_tokens, temperature, json_schema, env):
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    rest = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
    body: Dict[str, Any] = {"messages": rest, "max_tokens": max_tokens, "temperature": temperature}
    if system:
        body["system"] = system
    if model:
        body["model"] = model
    return body


def _parse_anthropic(data: Any) -> str:
    try:
        return "".join(b.get("text", "") for b in data["content"] if isinstance(b, dict))
    except (KeyError, TypeError):
        raise LLMError("unexpected anthropic-style response shape")


def _flatten_raw(messages, model, max_tokens, temperature, json_schema, env):
    """Unknown gateway: send the conversation as one 'prompt' plus the structured messages."""
    prompt = "\n\n".join("[%s]\n%s" % (m["role"], m["content"]) for m in messages)
    body: Dict[str, Any] = {"prompt": prompt, "messages": messages, "max_tokens": max_tokens,
                            "temperature": temperature}
    if model:
        body["model"] = model
    return body


def _parse_raw(data: Any) -> str:
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for k in ("text", "response", "output", "content", "answer", "completion", "result"):
            if isinstance(data.get(k), str):
                return data[k]
        try:
            return _parse_openai(data)
        except LLMError:
            pass
    raise LLMError("cannot find text in raw response; adapt hibot/llm/client.py::_parse_raw")


def _flatten_argpt(messages, model, max_tokens, temperature, json_schema, env):
    """Arçelik ArGPT text-completion: one prompt string, no chat roles, no tool calling.

    POST https://argptapi.arcelik.com/api/public/text-completion
    headers: ClientId, ApiKey (see HttpChatClient._headers)
    """
    labels = {"system": "TALİMATLAR", "user": "GİRDİ", "assistant": "ÖNCEKİ YANITIN"}
    parts = ["### %s\n%s" % (labels.get(m["role"], m["role"].upper()), m["content"]) for m in messages]
    parts.append("### YANIT (yalnızca geçerli JSON)\n")
    return {
        "completionSettings": {"maxTokens": max_tokens, "temperature": temperature, "topP": 1,
                               "presencePenalty": 0, "frequencyPenalty": 0},
        "prompt": "\n\n".join(parts),
    }


_TEXT_KEYS = ("completion", "text", "content", "result", "response", "output", "answer", "message", "data",
              "choices", "value")


def _parse_argpt(data: Any) -> str:
    """Response shape is undocumented: find the completion text wherever it is (verified by `python -m hibot.llm.probe`)."""
    def find(node: Any, depth: int = 0) -> Optional[str]:
        if depth > 6:
            return None
        if isinstance(node, str):
            return node
        if isinstance(node, list):
            for item in node:
                hit = find(item, depth + 1)
                if hit is not None:
                    return hit
        if isinstance(node, dict):
            for k in _TEXT_KEYS:
                for key in (k, k.capitalize()):
                    if key in node:
                        hit = find(node[key], depth + 1)
                        if hit is not None:
                            return hit
        return None

    if isinstance(data, dict) and data.get("isSuccess") is False:
        raise LLMError("ArGPT returned isSuccess=false: %s" % str(data.get("message") or data.get("errors"))[:300])
    text = find(data)
    if text is None:
        raise LLMError("cannot find completion text in ArGPT response; run `python -m hibot.llm.probe` and adapt _parse_argpt")
    return text


_FORMATS = {
    "openai": (_flatten_openai, _parse_openai, "Authorization", "Bearer "),
    "anthropic": (_flatten_anthropic, _parse_anthropic, "x-api-key", ""),
    "raw": (_flatten_raw, _parse_raw, "Authorization", "Bearer "),
    "argpt": (_flatten_argpt, _parse_argpt, "ApiKey", ""),
}

Opener = Callable[[urllib.request.Request, float], Tuple[int, bytes]]


def _default_opener(req: urllib.request.Request, timeout: float) -> Tuple[int, bytes]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:      # noqa: S310 - URL is operator-configured
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b""


class HttpChatClient(LLMClient):
    name = "http"

    def __init__(self, url: str, *, api_key: Optional[str] = None, model: Optional[str] = None,
                 fmt: str = "openai", timeout: float = 120.0, retries: int = 2, backoff: float = 1.5,
                 auth_header: Optional[str] = None, auth_scheme: Optional[str] = None,
                 extra_headers: Optional[Mapping[str, str]] = None, opener: Optional[Opener] = None,
                 sleep: Callable[[float], None] = time.sleep, env: Optional[Mapping[str, str]] = None,
                 client_id: Optional[str] = None):
        if fmt not in _FORMATS:
            raise LLMError("HIBOT_LLM_FORMAT must be one of %s" % ", ".join(sorted(_FORMATS)))
        if not url or not re.match(r"^https?://", url):
            raise LLMError("HIBOT_LLM_URL must be an http(s) URL")
        self.url, self.model, self.fmt = url, model, fmt
        self._key = api_key or None
        self.timeout, self.retries, self.backoff = float(timeout), int(retries), float(backoff)
        flat, parse, hdr, scheme = _FORMATS[fmt]
        self._flat, self._parse = flat, parse
        self._auth_header = auth_header or hdr
        self._auth_scheme = scheme if auth_scheme is None else auth_scheme
        self._extra = dict(extra_headers or {})
        if fmt == "anthropic":
            self._extra.setdefault("anthropic-version", "2023-06-01")
        self._client_id = client_id or None
        if fmt == "argpt":
            if not (self._client_id and self._key):
                raise LLMError("argpt format needs HIBOT_LLM_CLIENT_ID and HIBOT_LLM_API_KEY")
            self._extra["ClientId"] = self._client_id
        self._opener = opener or _default_opener
        self._sleep = sleep
        self._env = dict(os.environ if env is None else env)

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None, **kw) -> "HttpChatClient":
        e = os.environ if env is None else env
        url = e.get("HIBOT_LLM_URL", "")
        if not url:
            raise LLMError("HIBOT_LLM_URL is not set (see hibot/llm/README.md)")
        return cls(url, api_key=e.get("HIBOT_LLM_API_KEY"), model=e.get("HIBOT_LLM_MODEL"),
                   fmt=e.get("HIBOT_LLM_FORMAT", "openai").lower(),
                   timeout=float(e.get("HIBOT_LLM_TIMEOUT", "120")), retries=int(e.get("HIBOT_LLM_RETRIES", "2")),
                   auth_header=e.get("HIBOT_LLM_AUTH_HEADER"), auth_scheme=e.get("HIBOT_LLM_AUTH_SCHEME"),
                   client_id=e.get("HIBOT_LLM_CLIENT_ID"), env=e, **kw)

    def __repr__(self) -> str:                    # never leak the key
        return "HttpChatClient(url=%r, fmt=%r, model=%r)" % (self.url, self.fmt, self.model)

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        h.update(self._extra)
        if self._key:
            h[self._auth_header] = self._auth_scheme + self._key
        return h

    def _scrub(self, s: str) -> str:
        for secret in (self._key, self._client_id):
            if secret:
                s = s.replace(secret, "***")
        return s[:300]

    def complete(self, messages, *, json_schema=None, max_tokens=2000, temperature=0.0) -> str:
        body = self._flat(messages, self.model, max_tokens, temperature, json_schema, self._env)
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        last = "unknown error"
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(self.url, data=payload, headers=self._headers(), method="POST")
            try:
                status, raw = self._opener(req, self.timeout)
            except Exception as e:                # noqa: BLE001 - network errors are retried
                last = "%s: %s" % (e.__class__.__name__, self._scrub(str(e)))
            else:
                if status == 200:
                    text = raw.decode("utf-8", "replace")
                    try:
                        data: Any = json.loads(text)
                    except ValueError:
                        data = text                # plain-text gateway
                    return self._parse(data)
                last = "HTTP %s: %s" % (status, self._scrub(raw.decode("utf-8", "replace")))
                if status not in (408, 425, 429) and not (500 <= status < 600):
                    raise LLMError(last)           # other 4xx: retrying will not help
            if attempt < self.retries:
                self._sleep(self.backoff * (2 ** attempt))
        raise LLMError("LLM request failed after %d attempts: %s" % (self.retries + 1, last))


def client_from_env(kind: str, **kw) -> LLMClient:
    if kind == "http":
        return HttpChatClient.from_env(**kw)
    raise LLMError("unknown LLM kind %r (the fake client is built by the demo scenario)" % kind)
