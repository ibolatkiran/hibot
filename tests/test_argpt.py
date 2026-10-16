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
