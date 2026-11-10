"""The catalogue sync: listing, probing through the proxy, classification, caching, publishing."""
import base64
import datetime
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "catalog"))
import sync  # noqa: E402

NOW = datetime.datetime(2026, 11, 10, 12, 0, tzinfo=datetime.timezone.utc)
ENV = {
    "MISTRAL_API_BASE": "https://m/v1", "OPENCODE_GO_API_BASE": "https://oc/v1", "OPENROUTER_API_BASE": "https://or/v1",
    "LOCAL_LLM_API_BASE": "http://local/v1", "VIBE_API_KEY": "k-vibe", "VIBE_STUDY_API_KEY": "k-study",
    "OPENCODE_GO_API_KEY": "k-oc", "OPENROUTER_API_KEY": "k-or", "LOCAL_LLM_API_KEY": "none",
    "LITELLM_URL": "http://litellm:4000", "LITELLM_MASTER_KEY": "sk-master-secret",
}
LISTS = {
    "https://m/v1/models": {"data": [{"id": "mistral-large", "name": "L", "max_context_length": 1000, "capabilities": {"completion_chat": True}},
                                      {"id": "mistral-embed", "capabilities": {"completion_chat": False}}, {"id": "bare"}, {"nope": 1}, "x"]},
    "https://oc/v1/models": {"data": [{"id": "glm-5.3-flash"}, {"id": "deepseek-v4-pro"}]},
    "https://or/v1/models": {"data": [{"id": "a/b:free", "name": "AB", "context_length": 8, "pricing": {"prompt": "0", "completion": "0"}},
                                       {"id": "c/d", "pricing": {"prompt": "0.1", "completion": "0.1"}}, {"id": "e/f", "pricing": {"prompt": "0", "completion": "0"}}]},
    "http://local/v1/models": {"data": [{"id": "qwen3:32b"}]},
}


class Http:
    """A stub transport. `answers` maps a probed model to (status, body)."""

    def __init__(self, answers=None, lists=None):
        self.answers, self.lists, self.calls, self.puts = answers or {}, LISTS if lists is None else lists, [], []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if method == "POST":
            return self.answers.get(body["model"], (200, {"id": "x", "choices": [{"message": {"content": "SECRET-RESPONSE"}}]}))
        if method == "PUT":
            self.puts.append(body)
            return 201, {}
        if url in self.lists:
            return (200, self.lists[url]) if self.lists[url] is not None else (500, "down")
        return 404, "no"

    def probed(self):
        return [c[3]["model"] for c in self.calls if c[0] == "POST"]


class Classify(unittest.TestCase):
    def test_table(self):
        cases = [
            (200, "", "usable"),
            (400, '{"error":{"type":"budget_exceeded","message":"Budget has been exceeded"}}', "budget-exhausted"),
            (402, "", "budget-exhausted"),
            (429, "You exceeded your current quota", "budget-exhausted"),
            (400, '{"error":{"message":"This Go model is hosted outside Europe and the United States. Select Global"}}', "blocked"),
            (401, "bad key", "blocked"),
            (403, "", "blocked"),
            (404, "", "unknown"),
            (400, '{"error":"The model `x` does not exist"}', "unknown"),
            (429, "slow down", "error"),
            (503, "upstream", "error"),
            (599, "timed out", "error"),
        ]
        for status, text, want in cases:
            self.assertEqual(sync.classify(status, text)[0], want, (status, text))

    def test_reason_text_is_kept_and_keys_are_scrubbed(self):
        _, reason = sync.classify(403, '{"error":{"message":"hosted outside the EU, key sk-abcdefghijkl and Bearer abcdef123456"}}')
        self.assertIn("hosted outside the EU", reason)
        self.assertNotIn("abcdefghijkl", reason)
        self.assertNotIn("abcdef123456", reason)
        self.assertLessEqual(len(sync.classify(500, "x" * 1000)[1]), 240)

    def test_error_message_shapes(self):
        self.assertEqual(sync.error_message(None), "")
        self.assertEqual(sync.error_message("plain"), "plain")
        self.assertEqual(sync.error_message('{"error":"s"}'), "s")
        self.assertEqual(sync.error_message("[1]"), "[1]")
        self.assertEqual(sync.error_message('{"error":{"type":"t","message":"m"}}'), "t m")


class Listing(unittest.TestCase):
    def entry(self, route):
        return next(e for e in sync.ROUTES if e["route"] == route)

    def test_each_kind(self):
        http = Http()
        mistral = {m["id"]: m for m in sync.list_models(self.entry("vibe"), ENV, http)}
        self.assertEqual(set(mistral), {"mistral-large", "mistral-embed", "bare"})
        self.assertIs(mistral["mistral-embed"]["chat"], False)
        self.assertIsNone(mistral["bare"]["chat"])
        self.assertEqual(mistral["mistral-large"]["context_length"], 1000)
        free = {m["id"]: m["free"] for m in sync.list_models(self.entry("openrouter"), ENV, http)}
        self.assertEqual(free, {"a/b:free": True, "c/d": False, "e/f": True})
        self.assertTrue(sync.list_models(self.entry("local"), ENV, http)[0]["free"])
        self.assertFalse(sync.list_models(self.entry("opencode-go"), ENV, http)[0]["free"])
        self.assertEqual(http.calls[0][2], {"Authorization": "Bearer k-vibe"})

    def test_keyless_and_failures(self):
        http = Http()
        sync.list_models(self.entry("local"), dict(ENV, LOCAL_LLM_API_KEY=""), http)
        self.assertEqual(http.calls[-1][2], {})
        with self.assertRaisesRegex(RuntimeError, "not set"):
            sync.list_models(self.entry("local"), {}, http)
        with self.assertRaisesRegex(RuntimeError, "answered 404"):
            sync.list_models(self.entry("local"), dict(ENV, LOCAL_LLM_API_BASE="http://other/v1"), http)

    def test_free_price_garbage(self):
        self.assertFalse(sync.is_free_price({"prompt": "x"}))
        self.assertFalse(sync.is_free_price(None))


class Build(unittest.TestCase):
    def run_build(self, http, previous=None, **kw):
        return sync.build(ENV, http, previous or {}, kw.pop("now", NOW), **kw)

    def test_first_run_probes_everything_probeable_through_the_route(self):
        http = Http(answers={"opencode-go/deepseek-v4-pro": (403, '{"error":{"message":"This Go model is hosted outside Europe"}}')})
        cat, stats = self.run_build(http)
        models = {m["id"]: m["metadata"] for p in cat["providers"].values() for m in p["models"]}
        self.assertEqual(models["opencode-go/glm-5.3-flash"]["status"], "usable")
        self.assertEqual(models["opencode-go/deepseek-v4-pro"]["status"], "blocked")
        self.assertIn("hosted outside", models["opencode-go/deepseek-v4-pro"]["reason"])
        self.assertEqual(models["vibe/mistral-embed"]["status"], "not-chat")
        self.assertEqual(models["openrouter/c/d"]["status"], "unprobed")
        self.assertEqual(models["openrouter/a/b:free"]["free"], True)
        self.assertEqual(models["local/qwen3:32b"]["free"], True)
        self.assertEqual(stats["probed"], len(http.probed()))
        self.assertNotIn("openrouter/c/d", http.probed())
        self.assertNotIn("vibe/mistral-embed", http.probed())
        probe = next(c for c in http.calls if c[0] == "POST")
        self.assertEqual(probe[3]["max_tokens"], 1)
        self.assertEqual(probe[2]["x-litellm-api-key"], "Bearer sk-master-secret")
        self.assertEqual(cat["providers"]["vibe"]["metadata"]["by_status"], {"usable": 2, "not-chat": 1})

    def test_published_catalogue_holds_no_key_and_no_response(self):
        text = json.dumps(self.run_build(Http())[0])
        for secret in ("k-vibe", "k-study", "k-oc", "k-or", "sk-master-secret", "SECRET-RESPONSE"):
            self.assertNotIn(secret, text)

    def test_cache_skips_fresh_models_and_reprobes_stale_errored_and_new(self):
        first, _ = self.run_build(Http())
        # fresh: nothing probed
        http = Http()
        _, stats = self.run_build(http, first, now=NOW + datetime.timedelta(hours=1))
        self.assertEqual(http.probed(), [])
        # a day later: everything probeable again
        http = Http()
        self.run_build(http, first, now=NOW + datetime.timedelta(hours=25))
        self.assertIn("opencode-go/glm-5.3-flash", http.probed())
        # an errored model is retried at once, a new model is probed at once
        first["providers"]["opencode-go"]["models"][0]["metadata"]["status"] = "error"
        lists = dict(LISTS, **{"http://local/v1/models": {"data": [{"id": "qwen3:32b"}, {"id": "new-one"}]}})
        http = Http(lists=lists)
        self.run_build(http, first, now=NOW + datetime.timedelta(hours=1))
        self.assertIn("local/new-one", http.probed())
        self.assertIn("opencode-go/deepseek-v4-pro", http.probed())
        self.assertNotIn("local/qwen3:32b", http.probed())

    def test_a_vanished_model_leaves_the_catalogue(self):
        first, _ = self.run_build(Http())
        lists = dict(LISTS, **{"https://oc/v1/models": {"data": [{"id": "glm-5.3-flash"}]}})
        cat, _ = self.run_build(Http(lists=lists), first, now=NOW + datetime.timedelta(hours=1))
        ids = [m["id"] for m in cat["providers"]["opencode-go"]["models"]]
        self.assertEqual(ids, ["opencode-go/glm-5.3-flash"])

    def test_probe_limits_and_corrupt_cache(self):
        http = Http()
        cat, stats = self.run_build(http, max_probes=3)
        self.assertEqual(stats["probed"], 3)
        http = Http()
        sync.build(ENV, http, {}, NOW, route_limits={"opencode-go": 1}, max_probes=50)
        self.assertEqual(len([m for m in http.probed() if m.startswith("opencode-go/")]), 1)
        old = {"providers": {"local": {"models": [{"id": "local/qwen3:32b", "metadata": {"status": "usable", "probed_at": "garbage"}}]}}}
        http = Http()
        self.run_build(http, old)
        self.assertIn("local/qwen3:32b", http.probed())
        http = Http()
        self.run_build(http, {"providers": {"local": {"models": [{"id": "local/qwen3:32b", "metadata": {"status": "usable"}}]}}})
        self.assertIn("local/qwen3:32b", http.probed())

    def test_openrouter_free_probes_are_capped_per_run(self):
        lists = dict(LISTS, **{"https://or/v1/models": {"data": [{"id": f"v/m{i}:free"} for i in range(40)]}})
        http = Http(lists=lists)
        self.run_build(http)
        self.assertEqual(len([m for m in http.probed() if m.startswith("openrouter/")]), 30)

    def test_a_route_that_cannot_be_listed_keeps_its_last_entries(self):
        first, _ = self.run_build(Http())
        lists = dict(LISTS, **{"http://local/v1/models": None, "https://oc/v1/models": {"data": []}})
        cat, stats = self.run_build(Http(lists=lists), first, now=NOW + datetime.timedelta(hours=1))
        self.assertTrue(cat["providers"]["local"]["metadata"]["stale"])
        self.assertTrue(cat["providers"]["opencode-go"]["metadata"]["stale"])
        self.assertEqual(len(cat["providers"]["local"]["models"]), 1)
        self.assertEqual(len(stats["route_errors"]), 2)
        # no earlier entries: the route is simply absent
        cat, stats = self.run_build(Http(lists=lists))
        self.assertNotIn("local", cat["providers"])
        self.assertIn("opencode-go", cat["providers"])

    def test_no_proxy_configured_lists_without_probing(self):
        http = Http()
        cat, stats = sync.build({k: v for k, v in ENV.items() if k != "LITELLM_URL"}, http, {}, NOW)
        self.assertEqual(stats["probed"], 0)
        self.assertEqual(http.probed(), [])
        self.assertEqual(cat["providers"]["local"]["models"][0]["metadata"]["status"], "unprobed")

    def test_age(self):
        self.assertEqual(sync.parse_age("2d"), datetime.timedelta(days=2))
        self.assertEqual(sync.parse_age("6h"), datetime.timedelta(hours=6))
        with self.assertRaises(ValueError):
            sync.parse_age("soon")

    def test_routes_match_config(self):
        config = (ROOT / "config" / "config.yaml").read_text()
        names = re.findall(r"^ {2}- model_name: (\S+)/\*$", config, re.M)
        self.assertEqual(names, [e["route"] for e in sync.ROUTES])
        for e in sync.ROUTES:
            self.assertIn(f"os.environ/{e['base_env']}", config)
            self.assertIn(f"os.environ/{e['key_env']}", config)


def encoded(catalogue):
    return {"content": base64.b64encode(json.dumps(catalogue).encode()).decode(), "sha": "abc"}


class Main(unittest.TestCase):
    def test_writes_a_local_file_and_reads_it_back_as_the_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "c.json")
            self.assertEqual(sync.main(["--output", out], ENV, Http(), NOW), 0)
            http = Http()
            sync.main(["--output", out], ENV, http, NOW + datetime.timedelta(hours=1))
            self.assertEqual(http.probed(), [])
            self.assertEqual(json.load(open(out))["version"], 1)

    def test_a_run_that_lists_nothing_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(sync.main(["--output", os.path.join(tmp, "c.json")], {}, Http(), NOW), 1)

    def test_publishes_when_changed_and_not_when_unchanged(self):
        env = dict(ENV, CATALOG_REPO="o/r", CATALOG_TOKEN="t")
        cat, _ = sync.build(ENV, Http(), {}, NOW)

        class Gh(Http):
            def __call__(self, method, url, headers, body):
                if url.startswith("https://api.github.com") and method == "GET":
                    return self.existing
                return super().__call__(method, url, headers, body)

        gh = Gh()
        gh.existing = (404, "")
        sync.main([], env, gh, NOW)
        self.assertEqual(len(gh.puts), 1)
        self.assertNotIn("sha", gh.puts[0])
        gh = Gh()
        gh.existing = (200, encoded(cat))
        sync.main([], env, gh, NOW + datetime.timedelta(hours=1))
        self.assertEqual(gh.puts, [])
        gh = Gh()
        gh.existing = (200, encoded(cat))
        sync.main([], env, gh, NOW + datetime.timedelta(hours=25))
        self.assertEqual(gh.puts[0]["sha"], "abc")

    def test_github_errors_surface(self):
        with self.assertRaisesRegex(RuntimeError, "reading"):
            sync.github_get("o/r", "p", "t", lambda *a: (500, "x"))
        with self.assertRaisesRegex(RuntimeError, "publishing"):
            sync.github_put("o/r", "p", "t", {}, None, lambda *a: (422, "x"))


class Transport(unittest.TestCase):
    def test_make_http_against_a_local_server(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                code, body = {"/ok": (200, b'{"a":1}'), "/text": (200, b"plain"), "/bad": (400, b"nope")}[self.path]
                self.send_response(code)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                body = self.rfile.read(int(self.headers["content-length"]))
                self.send_response(200)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}"
        http = sync.make_http(5)
        self.assertEqual(http("GET", base + "/ok", {}, None), (200, {"a": 1}))
        self.assertEqual(http("GET", base + "/text", {}, None), (200, "plain"))
        self.assertEqual(http("GET", base + "/bad", {}, None), (400, "nope"))
        self.assertEqual(http("POST", base + "/p", {}, {"x": 1}), (200, {"x": 1}))
        server.shutdown()
        self.assertEqual(sync.make_http(1)("GET", base + "/ok", {}, None)[0], 599)


if __name__ == "__main__":
    unittest.main()
