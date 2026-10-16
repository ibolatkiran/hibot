# hibot.llm - LLM access

The company endpoint has **no tool calling** (DECISIONS D8): the model only returns text and our code parses it as JSON.
The whole contract is one method:

```python
LLMClient.complete(messages: list[dict], *, json_schema: dict | None, max_tokens: int, temperature: float) -> str
```

| Module | Purpose |
|---|---|
| `client.py` | `LLMClient` (abstract), `FakeLLM` (scripted, for tests and the demo), `HttpChatClient` (generic urllib client), `client_from_env` |
| `jsonio.py` | `extract_json` (fences, leading prose, trailing commas), `validate_schema` (small JSON-schema subset), `complete_json` (repair loop) |

## Flow of one model call

```
messages ─► client.complete ─► raw text ─► extract_json ─► validate_schema (+ custom validator)
                 ▲                                              │ invalid (max 2 retries)
                 └── assistant text + "your answer was invalid: <errors>" ◄┘
```

`complete_json(client, messages, schema, validator=..., max_retries=2)` returns `(obj, raw_text, attempts)` or raises
`JsonRepairFailed`. Transport errors raise `LLMError` (the orchestrator turns both into a fallback report, never a crash).

Schema subset: `type` (string or list), `enum`, `required`, `properties`, `additionalProperties: false`, `items`,
`minimum/maximum`, `minLength/maxLength`, `minItems/maxItems`. Unknown keywords are ignored.

## Plugging in the company endpoint

The endpoint format is not known yet. `HttpChatClient` keeps the provider specifics in one small table,
`_FORMATS` in `client.py`: `(build request body, parse response text, default auth header, default auth scheme)`.

| env | meaning | default |
|---|---|---|
| `HIBOT_LLM_URL` | full URL (required) | - |
| `HIBOT_LLM_API_KEY` | credential. Never logged, never in errors/`repr` | none |
| `HIBOT_LLM_MODEL` | `model` field when set | none |
| `HIBOT_LLM_FORMAT` | `openai` (chat completions), `anthropic` (messages API), `raw` | `openai` |
| `HIBOT_LLM_AUTH_HEADER` / `HIBOT_LLM_AUTH_SCHEME` | e.g. `api-key` / `` (Azure style), `Authorization` / `Bearer ` | per format |
| `HIBOT_LLM_JSON_MODE` | `1` adds `response_format: json_object` (openai format) | off |
| `HIBOT_LLM_TIMEOUT`, `HIBOT_LLM_RETRIES` | seconds per request, retries on 408/425/429/5xx/network error (backoff 1.5 s * 2^n) | 120, 2 |

* `openai`: body `{model, messages, max_tokens, temperature}`, answer `choices[0].message.content`.
* `anthropic`: system messages are moved to the top-level `system` field, answer = concatenated `content[].text`.
* `raw`: body `{prompt, messages, max_tokens, temperature}` (prompt = all messages flattened), answer = plain text or the first of
  `text|response|output|content|answer|completion|result`.

If the real endpoint differs: add a new entry to `_FORMATS` (two small functions) or extend `_parse_raw`; nothing else changes.
Nothing here depends on the model supporting JSON mode: the repair loop covers prose, fences and trailing commas.

```
set HIBOT_LLM_URL=https://llm.example.corp/v1/chat/completions
set HIBOT_LLM_API_KEY=...            (do not commit)
set HIBOT_LLM_MODEL=...
python -m hibot analyze SM-1777386 --llm http
```

## FakeLLM

```python
llm = FakeLLM([{"action": "inspect_code", ...}, lambda messages: {...}, "raw text"], default=None)
llm.calls[0]["messages"]; llm.assert_prompt_contains("needle", call=1)
```
Items are returned in order (dict -> JSON, callable -> called with the messages, so it can assert on the prompt); an exhausted
script raises `LLMError` unless `default` is given.

## Safety

* The key only lives in the request header; `repr()`, exception messages and retries scrub it.
* Tickets and observations sent to the model are already PII-scrubbed (tools/incidents) and masked (hibot.db.masking).
* Only 4xx other than 408/425/429 fail immediately; no request is ever replayed with different content.
