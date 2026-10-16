import json
import unittest
import urllib.request

from hibot.llm.client import FakeLLM, HttpChatClient, LLMError
from hibot.llm.jsonio import JsonExtractError, JsonRepairFailed, complete_json, extract_json, validate_schema


class ExtractJsonTests(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_code_fence(self):
        self.assertEqual(extract_json('Here:\n```json\n{"a": [1, 2]}\n```\nbye'), {"a": [1, 2]})

    def test_leading_and_trailing_prose(self):
        self.assertEqual(extract_json('Sure! I will run {"action": "x", "n": {"k": 1}} now.'),
                         {"action": "x", "n": {"k": 1}})

    def test_trailing_commas(self):
        self.assertEqual(extract_json('{"a": [1, 2,], "b": {"c": 3,},}'), {"a": [1, 2], "b": {"c": 3}})

    def test_trailing_comma_inside_string_kept(self):
        self.assertEqual(extract_json('{"a": "x,}"}'), {"a": "x,}"})

    def test_braces_inside_strings(self):
        self.assertEqual(extract_json('text {"s": "a } b { c"} tail'), {"s": "a } b { c"})

    def test_skips_invalid_candidate_then_finds_valid(self):
        self.assertEqual(extract_json('{not json} then {"ok": true}'), {"ok": True})

    def test_array(self):
        self.assertEqual(extract_json("result: [1, 2, 3]"), [1, 2, 3])

    def test_failure(self):
        for bad in ("", "no json here", "{broken"):
            with self.assertRaises(JsonExtractError):
                extract_json(bad)


class SchemaTests(unittest.TestCase):
    S = {"type": "object", "required": ["a", "k"], "additionalProperties": False, "properties": {
        "a": {"type": "integer", "minimum": 1, "maximum": 5},
        "k": {"type": "string", "enum": ["x", "y"]},
        "l": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
        "n": {"type": ["number", "null"]}}}

    def test_valid(self):
        self.assertEqual(validate_schema({"a": 2, "k": "x", "l": ["s"], "n": None}, self.S), [])

    def test_errors(self):
        errs = validate_schema({"a": 9, "k": "z", "l": [1, "a", "b"], "extra": 1}, self.S)
        text = " ".join(errs)
        for needle in ("<= 5", "must be one of", "$.l[0]", "at most 2", "unexpected property 'extra'"):
            self.assertIn(needle, text)

    def test_missing_required_and_types(self):
        self.assertTrue(any("missing required property 'a'" in e for e in validate_schema({"k": "x"}, self.S)))
        self.assertTrue(validate_schema({"a": True, "k": "x"}, self.S))           # bool is not an integer
        self.assertTrue(validate_schema([], self.S))


class RepairLoopTests(unittest.TestCase):
    SCHEMA = {"type": "object", "required": ["action"], "properties": {"action": {"type": "string", "enum": ["go"]}}}

    def test_repair_after_invalid_json_and_schema_error(self):
        llm = FakeLLM(["I think we should go", '{"action": "stop"}', '```json\n{"action": "go",}\n```'])
        obj, raw, attempts = complete_json(llm, [{"role": "user", "content": "hi"}], self.SCHEMA)
        self.assertEqual((obj, attempts), ({"action": "go"}, 3))
        llm.assert_prompt_contains("previous answer was invalid", call=1)
        llm.assert_prompt_contains("must be one of", call=2)

    def test_gives_up_after_max_retries(self):
        llm = FakeLLM(["nope"] * 5)
        with self.assertRaises(JsonRepairFailed) as cm:
            complete_json(llm, [{"role": "user", "content": "hi"}], self.SCHEMA, max_retries=2)
        self.assertEqual(len(llm.calls), 3)
        self.assertEqual(cm.exception.last_text, "nope")

    def test_custom_validator_and_messages_not_mutated(self):
        msgs = [{"role": "user", "content": "hi"}]
        llm = FakeLLM(['{"action": "go", "n": 1}', '{"action": "go", "n": 2}'])
        obj, _, attempts = complete_json(llm, msgs, self.SCHEMA, validator=lambda o: [] if o.get("n") == 2 else ["n must be 2"])
        self.assertEqual((obj["n"], attempts), (2, 2))
        self.assertEqual(len(msgs), 1)


class FakeLLMTests(unittest.TestCase):
    def test_script_and_exhaustion(self):
        llm = FakeLLM([{"a": 1}, "text"])
        self.assertEqual(json.loads(llm.complete([{"role": "user", "content": "x"}])), {"a": 1})
        self.assertEqual(llm.complete([]), "text")
        with self.assertRaises(LLMError):
            llm.complete([])

    def test_callable_can_assert_on_prompt(self):
        def check(messages):
            assert "needle" in messages[-1]["content"]
            return {"ok": True}
        llm = FakeLLM([check])
        self.assertTrue(json.loads(llm.complete([{"role": "user", "content": "a needle b"}]))["ok"])
        with self.assertRaises(AssertionError):
            FakeLLM([check]).complete([{"role": "user", "content": "nothing"}])

    def test_default_repeats(self):
        llm = FakeLLM(default={"x": 1})
        llm.complete([])
        llm.complete([])
        self.assertEqual(len(llm.calls), 2)


class _Opener:
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def __call__(self, req: urllib.request.Request, timeout):
        self.requests.append(req)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


MSGS = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]


class HttpClientTests(unittest.TestCase):
    def test_openai_format(self):
        op = _Opener((200, json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode()))
        c = HttpChatClient("https://llm.example/v1/chat", api_key="SECRETKEY", model="m1", opener=op, env={})
        self.assertEqual(c.complete(MSGS, max_tokens=50, temperature=0.1), "hello")
        req = op.requests[0]
        body = json.loads(req.data)
        self.assertEqual((body["model"], body["max_tokens"], body["messages"][0]["role"]), ("m1", 50, "system"))
        self.assertEqual(req.get_header("Authorization"), "Bearer SECRETKEY")

    def test_anthropic_format_splits_system(self):
        op = _Opener((200, json.dumps({"content": [{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]}).encode()))
        c = HttpChatClient("https://llm.example/messages", api_key="K", fmt="anthropic", opener=op, env={})
        self.assertEqual(c.complete(MSGS), "AB")
        body = json.loads(op.requests[0].data)
        self.assertEqual(body["system"], "SYS")
        self.assertTrue(all(m["role"] != "system" for m in body["messages"]))
        self.assertEqual(op.requests[0].get_header("X-api-key"), "K")

    def test_raw_format_and_plain_text(self):
        op = _Opener((200, b'{"response": "raw answer"}'), (200, b"plain text answer"))
        c = HttpChatClient("http://gw/x", fmt="raw", opener=op, env={})
        self.assertEqual(c.complete(MSGS), "raw answer")
        self.assertEqual(c.complete(MSGS), "plain text answer")
        self.assertIn("prompt", json.loads(op.requests[0].data))

    def test_retries_with_backoff_then_success(self):
        sleeps = []
        op = _Opener((503, b"busy"), OSError("conn reset"), (200, json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()))
        c = HttpChatClient("https://x/y", opener=op, retries=2, backoff=1.0, sleep=sleeps.append, env={})
        self.assertEqual(c.complete(MSGS), "ok")
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_client_error_is_not_retried_and_key_never_leaks(self):
        op = _Opener((401, b"bad key SECRETKEY"))
        c = HttpChatClient("https://x/y", api_key="SECRETKEY", opener=op, sleep=lambda s: None, env={})
        with self.assertRaises(LLMError) as cm:
            c.complete(MSGS)
        self.assertEqual(len(op.requests), 1)
        self.assertNotIn("SECRETKEY", str(cm.exception))
        self.assertNotIn("SECRETKEY", repr(c))

    def test_gives_up_after_retries(self):
        op = _Opener(*[(500, b"x")] * 3)
        c = HttpChatClient("https://x/y", opener=op, retries=2, sleep=lambda s: None, env={})
        with self.assertRaises(LLMError):
            c.complete(MSGS)
        self.assertEqual(len(op.requests), 3)

    def test_from_env_and_validation(self):
        with self.assertRaises(LLMError):
            HttpChatClient.from_env({})
        with self.assertRaises(LLMError):
            HttpChatClient("ftp://x")
        with self.assertRaises(LLMError):
            HttpChatClient("https://x", fmt="weird")
        c = HttpChatClient.from_env({"HIBOT_LLM_URL": "https://x/y", "HIBOT_LLM_FORMAT": "ANTHROPIC",
                                      "HIBOT_LLM_MODEL": "m", "HIBOT_LLM_API_KEY": "k"})
        self.assertEqual((c.fmt, c.model), ("anthropic", "m"))


if __name__ == "__main__":
    unittest.main()
