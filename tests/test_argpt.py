"""ArGPT (company LLM) adapter and .env loader. No network."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from hibot.envfile import load_env
from hibot.llm.client import HttpChatClient, LLMError, _parse_argpt

URL = "https://argptapi.arcelik.com/api/public/text-completion"
MESSAGES = [{"role": "system", "content": "Sadece JSON."}, {"role": "user", "content": "SM-1 analiz et"}]


class Recorder:
    def __init__(self, status=200, body=None):
        self.status, self.body, self.requests = status, body, []

    def __call__(self, req, timeout):
        self.requests.append(req)
        return self.status, json.dumps(self.body).encode("utf-8")


def make(opener, **kw):
    return HttpChatClient(URL, fmt="argpt", api_key="SECRET-KEY", client_id="SECRET-CID",
                          opener=opener, retries=0, **kw)


class ArGptRequestTests(unittest.TestCase):
    def test_body_and_headers(self):
        rec = Recorder(body={"result": '{"ok": true}'})
        self.assertEqual(make(rec).complete(MESSAGES, max_tokens=900, temperature=0.0), '{"ok": true}')
        req = rec.requests[0]
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(set(body), {"completionSettings", "prompt"})
        self.assertEqual(body["completionSettings"]["maxTokens"], 900)
        self.assertEqual(body["completionSettings"]["temperature"], 0.0)
        self.assertIn("Sadece JSON.", body["prompt"])
        self.assertIn("SM-1 analiz et", body["prompt"])
        self.assertLess(body["prompt"].index("Sadece JSON."), body["prompt"].index("SM-1 analiz et"))
        headers = {k.lower(): v for k, v in req.header_items()}
        self.assertEqual(headers["clientid"], "SECRET-CID")
        self.assertEqual(headers["apikey"], "SECRET-KEY")             # no "Bearer " prefix
        self.assertNotIn("authorization", headers)

    def test_requires_both_credentials(self):
        with self.assertRaises(LLMError):
            HttpChatClient(URL, fmt="argpt", api_key="k", opener=Recorder())
        with self.assertRaises(LLMError):
            HttpChatClient(URL, fmt="argpt", client_id="c", opener=Recorder())

    def test_secrets_never_in_errors_or_repr(self):
        rec = Recorder(status=401, body={"message": "bad key SECRET-KEY for SECRET-CID"})
        with self.assertRaises(LLMError) as cm:
            make(rec).complete(MESSAGES)
        self.assertNotIn("SECRET-KEY", str(cm.exception))
        self.assertNotIn("SECRET-CID", str(cm.exception))
        self.assertNotIn("SECRET", repr(make(rec)))

    def test_from_env(self):
        env = {"HIBOT_LLM_URL": URL, "HIBOT_LLM_FORMAT": "argpt", "HIBOT_LLM_API_KEY": "k", "HIBOT_LLM_CLIENT_ID": "c"}
        c = HttpChatClient.from_env(env, opener=Recorder(body={"text": "x"}))
        self.assertEqual(c.complete(MESSAGES), "x")


class ArGptParseTests(unittest.TestCase):
    def test_shapes(self):
        for data, want in [
            ("plain", "plain"),
            ({"result": "a"}, "a"),
            ({"data": {"completion": "b"}}, "b"),
            ({"Data": {"Text": "c"}}, "c"),
            ({"isSuccess": True, "data": {"choices": [{"text": "d"}]}}, "d"),
            ({"choices": [{"message": {"content": "e"}}]}, "e"),
        ]:
            self.assertEqual(_parse_argpt(data), want, data)

    def test_failure_flag_and_unknown_shape(self):
        with self.assertRaises(LLMError):
            _parse_argpt({"isSuccess": False, "message": "quota"})
        with self.assertRaises(LLMError):
            _parse_argpt({"id": 1, "tokens": 5})


class EnvFileTests(unittest.TestCase):
    def test_load_env_does_not_override_and_strips_quotes(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text('# c\nHIBOT_T_A="x=1;y"\nHIBOT_T_B=keep\n\nnot a line\n', encoding="utf-8")
            os.environ["HIBOT_T_B"] = "real"
            try:
                loaded = load_env(p)
                self.assertEqual(loaded, ["HIBOT_T_A"])
                self.assertEqual(os.environ["HIBOT_T_A"], "x=1;y")
                self.assertEqual(os.environ["HIBOT_T_B"], "real")
            finally:
                os.environ.pop("HIBOT_T_A", None)
                os.environ.pop("HIBOT_T_B", None)

    def test_missing_file(self):
        self.assertEqual(load_env(Path(tempfile.gettempdir()) / "nope-hibot.env"), [])


if __name__ == "__main__":
    unittest.main()


class ArGptChatTests(unittest.TestCase):
    def test_chat_body_roles_and_url(self):
        rec = Recorder(body={"content": "ok", "message": "success"})
        c = HttpChatClient(URL, fmt="argpt_chat", api_key="SECRET-KEY", client_id="SECRET-CID", opener=rec, retries=0)
        self.assertTrue(c.url.endswith("/api/public/chat-completion"))
        self.assertEqual(c.complete(MESSAGES, max_tokens=50), "ok")
        body = json.loads(rec.requests[0].data.decode("utf-8"))
        self.assertEqual(set(body), {"messages", "completionSettings"})
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])
        self.assertEqual(body["completionSettings"]["maxTokens"], 50)
        headers = {k.lower(): v for k, v in rec.requests[0].header_items()}
        self.assertEqual(headers["clientid"], "SECRET-CID")
        self.assertEqual(rec.requests[0].full_url, c.url)


class ChatTo429FallbackTests(unittest.TestCase):
    """argpt_chat -> argpt text-completion on 429 (daily usage limit); the client keeps using text for the rest of the run."""
    LIMIT = (429, json.dumps({"message": "Daily usage limit is exceeded."}).encode())
    OK = (200, json.dumps({"content": "{\"a\": 1}", "message": "success"}).encode())
    MULTI = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "Q1"},
             {"role": "assistant", "content": "A1"}, {"role": "user", "content": "Q2"}]

    def _client(self, *responses):
        urls = []
        seq = list(responses)

        def opener(req, timeout):
            urls.append((req.full_url, json.loads(req.data.decode())))
            return seq.pop(0)
        c = HttpChatClient(URL, fmt="argpt_chat", api_key="SECRET-KEY", client_id="SECRET-CID", opener=opener, sleep=lambda s: None, retries=2)
        return c, urls

    def test_daily_limit_falls_back_without_retrying_and_sticks(self):
        c, urls = self._client(self.LIMIT, self.OK, self.OK)
        self.assertEqual(c.transport, "argpt_chat")
        self.assertEqual(c.complete(self.MULTI), "{\"a\": 1}")
        self.assertEqual([u.rsplit("/", 1)[1] for u, _ in urls], ["chat-completion", "text-completion"])
        self.assertIn("prompt", urls[1][1])                               # flattened multi-turn: one prompt, every turn inside
        for needle in ("SYS", "Q1", "A1", "Q2"):
            self.assertIn(needle, urls[1][1]["prompt"])
        self.assertEqual((c.transport, c.fmt), ("argpt_text_fallback", "argpt_chat"))
        self.assertIn("Daily usage limit", c.fallback_reason)
        c.complete(self.MULTI)                                            # the rest of the run stays on text-completion
        self.assertTrue(urls[2][0].endswith("/text-completion"))

    def test_other_429_is_retried_before_falling_back(self):
        busy = (429, b'{"message":"slow down"}')
        c, urls = self._client(busy, busy, busy, self.OK)
        c.complete(self.MULTI)
        self.assertEqual(len(urls), 4)
        self.assertEqual(c.transport, "argpt_text_fallback")

    def test_plain_text_format_has_no_fallback_and_reports_transport(self):
        c = make(Recorder(429, {"message": "Daily usage limit is exceeded."}))
        self.assertEqual(c.transport, "argpt")
        with self.assertRaises(LLMError):
            c.complete(MESSAGES)
        self.assertEqual(c.transport, "argpt")
