"""The catalogue sync: listing, probing at the provider, classification, caching, publishing."""
import base64
import contextlib
import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "catalog"))
import sync  # noqa: E402
import overview  # noqa: E402

NOW = datetime.datetime(2026, 11, 10, 12, 0, tzinfo=datetime.timezone.utc)
ENV = {
    "MISTRAL_API_BASE": "https://m/v1", "OPENCODE_GO_API_BASE": "https://oc/v1", "OPENROUTER_API_BASE": "https://or/v1",
    "LOCAL_LLM_API_BASE": "http://local/v1", "VIBE_API_KEY": "k-vibe", "VIBE_STUDY_API_KEY": "k-study",
    "VIBE_API_GENERAL_KEY": "k-general", "VIBE_STUDY_API_GENERAL_KEY": "k-study-general",
    "OPENCODE_GO_API_KEY": "k-oc", "OPENROUTER_API_KEY": "k-or", "LOCAL_LLM_API_KEY": "none",
}
LISTS = {
    "https://m/v1/models": {"data": [{"id": "mistral-large", "name": "L", "max_context_length": 1000, "capabilities": {"completion_chat": True}},
                                      {"id": "mistral-embed", "capabilities": {"completion_chat": False}}, {"id": "bare"}, {"nope": 1}, "x"]},
    "https://oc/v1/models": {"data": [{"id": "glm-5.3-flash"}, {"id": "deepseek-v4-pro"}]},
    "https://or/v1/models": {"data": [{"id": "a/b:free", "name": "AB", "context_length": 8, "pricing": {"prompt": "0", "completion": "0"}},
                                       {"id": "c/d", "pricing": {"prompt": "0.1", "completion": "0.1"}}, {"id": "e/f", "pricing": {"prompt": "0", "completion": "0"}}]},
    "http://local/v1/models": {"data": [{"id": "qwen3:32b"}]},
}
# The rules a real catalog/routing.json holds, inline: the tests must not depend on that file.
RULES = {"tiers": [[["vibe", 1]], [["vibe-api", 1]]], "cooldowns": {"vibe": 5}}


class Http:
    """A stub transport: provider lists, probe answers and GitHub.

    `answers` maps (provider url, upstream model id) to (status, body), or to a list of those
    consumed in order, so a retried probe sees the next answer.
    """

    def __init__(self, answers=None, lists=None):
        self.answers = answers or {}
        self.lists = LISTS if lists is None else lists
        self.calls, self.puts = [], []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if method == "POST" and url.endswith(("/chat/completions", "/messages")):
            answer = self.answers.get((url, body["model"]), (200, {"choices": [{"message": {"content": "SECRET-RESPONSE"}}]}))
            return answer.pop(0) if isinstance(answer, list) else answer
        if method == "PUT":
            self.puts.append((url, body))
            return 201, {}
        if method == "GET" and url in self.lists:
            return (200, self.lists[url]) if self.lists[url] is not None else (500, "down")
        return 404, "no"

    def probed(self):
        """(provider url, upstream id) of every probe: the provider is asked, never the proxy."""
        return [(c[1], c[3]["model"]) for c in self.calls if c[0] == "POST" and c[1].endswith(("/chat/completions", "/messages"))]

    def probed_ids(self):
        return [model for _, model in self.probed()]


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
        self.assertEqual(sync.error_message("[1]"), "")
        self.assertEqual(sync.error_message("<html>secret</html>"), "<html>secret</html>")
        self.assertEqual(sync.error_message('{"message":"not allowed here"}'), "not allowed here")
        self.assertEqual(sync.error_message('{"other":1}'), "")
        self.assertEqual(sync.error_message('{"error":{"type":"t","message":"m"}}'), "t m")


class Listing(unittest.TestCase):
    def entry(self, route):
        return next(e for e in sync.ROUTES if e["route"] == route)

    def test_an_opencode_go_id_ending_in_free_is_free(self):
        class Go:
            calls = []
            def __call__(self, method, url, headers, body):
                return 200, {"data": [{"id": "beam-free"}, {"id": "glm-5.3"}, {"id": "freezer"}]}
        found = {m["id"]: m["free"] for m in sync.list_models(self.entry("opencode-go"), ENV, Go())}
        self.assertEqual(found, {"beam-free": True, "glm-5.3": False, "freezer": False})

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

    def test_modality_per_kind(self):
        http = Http()
        mistral = {m["id"]: m for m in sync.list_models(self.entry("vibe"), ENV, http)}
        self.assertEqual(mistral["mistral-large"]["modality"], "chat")
        self.assertEqual(mistral["mistral-embed"]["modality"], "embedding")
        self.assertEqual(mistral["bare"]["modality"], "chat")
        self.assertIs(mistral["mistral-embed"]["chat"], False)
        self.assertIsNone(mistral["bare"]["chat"])

    def test_the_four_mistral_accounts_read_their_own_keys(self):
        http = Http()
        for route, key in (("vibe", "k-vibe"), ("vibe-study", "k-study"),
                           ("vibe-api", "k-general"), ("vibe-study-api", "k-study-general")):
            sync.list_models(self.entry(route), ENV, http)
            self.assertEqual(http.calls[-1][2], {"Authorization": f"Bearer {key}"}, route)

    def test_opencode_go_canonical_id_is_kept(self):
        lists = dict(LISTS, **{"https://oc/v1/models": {"data": [{"id": "glm-5.3-flash", "canonical_id": "zhipuai/glm-5.3"},
                                                           {"id": "plain"}]}})
        found = {m["id"]: m for m in sync.list_models(self.entry("opencode-go"), ENV, Http(lists=lists))}
        self.assertEqual(found["glm-5.3-flash"]["canonical_id"], "zhipuai/glm-5.3")
        self.assertNotIn("canonical_id", found["plain"])

    def test_opencode_go_records_the_family_and_base_each_model_is_served_on(self):
        lists = dict(LISTS, **{"https://oc/v1/models": {"data": [
            {"id": "glm-5.3-flash", "api": {"chat-completions": {"base_url": "https://oc/openai/v1"}}},
            {"id": "gpt-6-luna", "api": {"responses": {"base_url": "https://oc/openai/v1"}}},
            {"id": "claude-haiku-5-5", "api": {"messages": {"base_url": "https://oc/anthropic/v1"}}},
            {"id": "plain"},
        ]}})
        found = {m["id"]: m for m in sync.list_models(self.entry("opencode-go"), ENV, Http(lists=lists))}
        self.assertEqual((found["glm-5.3-flash"]["api"], found["glm-5.3-flash"]["api_base"]), ("chat-completions", "https://oc/openai/v1"))
        self.assertEqual(found["gpt-6-luna"]["api"], "responses")
        self.assertEqual((found["claude-haiku-5-5"]["api"], found["claude-haiku-5-5"]["api_base"]), ("messages", "https://oc/anthropic/v1"))
        self.assertNotIn("api", found["plain"])


class Rules(unittest.TestCase):
    def test_load_rules_reads_a_file_and_the_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "routing.json")
            with open(path, "w") as out:
                json.dump({"tiers": [[["vibe", 1]]]}, out)
            self.assertEqual(sync.load_rules(path), {"tiers": [[["vibe", 1]]]})
            self.assertEqual(sync.load_rules(os.path.join(tmp, "missing.json")), {})
            old = os.environ.get("CATALOG_RULES")
            os.environ["CATALOG_RULES"] = path
            try:
                self.assertEqual(sync.load_rules(), {"tiers": [[["vibe", 1]]]})
            finally:
                if old is None:
                    os.environ.pop("CATALOG_RULES", None)
                else:
                    os.environ["CATALOG_RULES"] = old
            with open(path, "w") as out:
                out.write("{")
            with self.assertRaises(ValueError):
                sync.load_rules(path)
        self.assertEqual(sync.RULES_PATH, str(ROOT / "catalog" / "routing.json"))


class Build(unittest.TestCase):
    def run_build(self, http, previous=None, **kw):
        return sync.build(ENV, http, previous or {}, kw.pop("now", NOW), **kw)

    def test_first_run_probes_each_model_at_its_provider(self):
        http = Http(answers={("https://oc/v1/chat/completions", "deepseek-v4-pro"): (403, '{"error":{"message":"This Go model is hosted outside Europe"}}')})
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
        self.assertNotIn("c/d", http.probed_ids())
        self.assertNotIn("mistral-embed", http.probed_ids())
        self.assertEqual(cat["providers"]["vibe"]["metadata"]["by_status"], {"usable": 2, "not-chat": 1})

    def test_a_probe_goes_to_the_provider_with_the_route_key_and_16_tokens(self):
        http = Http()
        self.run_build(http)
        call = next(c for c in http.calls if c[0] == "POST" and c[1] == "https://oc/v1/chat/completions" and c[3]["model"] == "glm-5.3-flash")
        self.assertEqual(call[3]["model"], "glm-5.3-flash")  # the upstream id, no route prefix
        self.assertEqual(call[3]["max_tokens"], 16)
        self.assertEqual(call[3]["messages"], [{"role": "user", "content": "ping"}])
        self.assertEqual(call[2]["Authorization"], "Bearer k-oc")
        self.assertEqual(call[2]["x-opencode-session"], "litellm-catalog-probe")
        vibe = next(c for c in http.calls if c[0] == "POST" and c[1] == "https://m/v1/chat/completions")
        self.assertEqual(vibe[2]["Authorization"], "Bearer k-vibe")
        self.assertNotIn("x-opencode-session", vibe[2])
        # the provider is asked, never a proxy: every call goes to a provider base
        bases = ("https://m/v1", "https://oc/v1", "https://or/v1", "http://local/v1")
        self.assertEqual([c[1] for c in http.calls if not c[1].startswith(bases)], [])

    def test_an_opencode_go_model_is_probed_in_its_own_family_at_its_listed_base(self):
        lists = dict(LISTS, **{"https://oc/v1/models": {"data": [
            {"id": "glm-5.3-flash", "api": {"chat-completions": {"base_url": "https://oc/openai/v1"}}},
            {"id": "gpt-6-luna", "api": {"responses": {"base_url": "https://oc/openai/v1"}}},
            {"id": "claude-haiku-5-5", "api": {"messages": {"base_url": "https://oc/anthropic/v1"}}},
            {"id": "gemini-4-argon", "api": {"generate-content": {"base_url": "https://oc/google/v1beta"}}},
        ]}})
        http = Http(lists=lists)
        cat, _ = self.run_build(http)
        posts = {c[1]: c for c in http.calls if c[0] == "POST" and c[1].startswith("https://oc/")}
        self.assertEqual(sorted(posts), [
            "https://oc/anthropic/v1/messages", "https://oc/google/v1beta/models/gemini-4-argon:generateContent",
            "https://oc/openai/v1/chat/completions", "https://oc/openai/v1/responses",
        ])
        for _, url, headers, _ in posts.values():
            self.assertEqual(headers["Authorization"], "Bearer k-oc", url)
            self.assertEqual(headers["x-opencode-session"], "litellm-catalog-probe", url)
        self.assertEqual(posts["https://oc/openai/v1/responses"][3]["max_output_tokens"], 16)
        self.assertEqual(posts["https://oc/anthropic/v1/messages"][2]["anthropic-version"], sync.ANTHROPIC_VERSION)
        self.assertEqual(posts["https://oc/anthropic/v1/messages"][3]["max_tokens"], 16)
        meta = {m["id"]: m["metadata"] for m in cat["providers"]["opencode-go"]["models"]}
        self.assertEqual(meta["opencode-go/claude-haiku-5-5"]["api"], "messages")
        self.assertEqual(meta["opencode-go/claude-haiku-5-5"]["status"], "usable")

    def test_an_opencode_go_deployment_uses_litellms_own_provider(self):
        params = sync.litellm_params(next(e for e in sync.ROUTES if e["route"] == "opencode-go"), "glm-5.3-flash")
        self.assertEqual(params["model"], "opencode_go/glm-5.3-flash")
        self.assertEqual(params["litellm_credential_name"], "opencode-go")

    def test_a_probe_retries_once_at_the_providers_minimum(self):
        url = "https://oc/v1/chat/completions"
        http = Http(answers={(url, "glm-5.3-flash"): [(400, '{"error":{"message":"Expected a value >= 32, but got 16"}}'), (200, {})]})
        cat, _ = self.run_build(http)
        calls = [c for c in http.calls if c[0] == "POST" and c[1] == url and c[3]["model"] == "glm-5.3-flash"]
        self.assertEqual([c[3]["max_tokens"] for c in calls], [16, 32])
        status = {m["id"]: m["metadata"]["status"] for p in cat["providers"].values() for m in p["models"]}
        self.assertEqual(status["opencode-go/glm-5.3-flash"], "usable")

    def test_a_probe_does_not_retry_above_the_ceiling_or_below_the_floor(self):
        url = "https://oc/v1/chat/completions"
        for message in ("Expected a value >= 4096, but got 16", "Expected a value >= 8, but got 16", "bad request"):
            http = Http(answers={(url, "glm-5.3-flash"): (400, json.dumps({"error": {"message": message}}))})
            self.run_build(http)
            calls = [c for c in http.calls if c[0] == "POST" and c[1] == url and c[3]["model"] == "glm-5.3-flash"]
            self.assertEqual(len(calls), 1, message)

    def test_published_catalogue_holds_no_key_and_no_response(self):
        text = json.dumps(self.run_build(Http())[0])
        for secret in ("k-vibe", "k-study", "k-general", "k-study-general", "k-oc", "k-or", "SECRET-RESPONSE"):
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
        self.assertIn("glm-5.3-flash", http.probed_ids())
        # an errored model is retried at once, a new model is probed at once
        first["providers"]["opencode-go"]["models"][0]["metadata"]["status"] = "error"
        lists = dict(LISTS, **{"http://local/v1/models": {"data": [{"id": "qwen3:32b"}, {"id": "new-one"}]}})
        http = Http(lists=lists)
        self.run_build(http, first, now=NOW + datetime.timedelta(hours=1))
        self.assertIn("new-one", http.probed_ids())
        self.assertIn("deepseek-v4-pro", http.probed_ids())
        self.assertNotIn("qwen3:32b", http.probed_ids())

    def test_a_failing_model_backs_off_instead_of_spending_a_probe_every_run(self):
        url = "https://oc/v1/chat/completions"
        always = {(url, "deepseek-v4-pro"): [(429, "")] * 10}
        cat, _ = self.run_build(Http(answers=dict(always)))
        meta = lambda c: {m["id"]: m["metadata"] for m in c["providers"]["opencode-go"]["models"]}["opencode-go/deepseek-v4-pro"]
        self.assertEqual((meta(cat)["status"], meta(cat)["error_probes"]), ("error", 1))
        probed_at, runs = [], 0
        for hour in range(1, 25):
            http = Http(answers=dict(always))
            cat, _ = self.run_build(http, cat, now=NOW + datetime.timedelta(hours=hour))
            if "deepseek-v4-pro" in http.probed_ids():
                runs += 1
                probed_at.append(hour)
        # waits of 1h, 2h, 4h, 8h, then capped at the 24h max age: 1, 3, 7, 15, not 24 probes
        self.assertEqual(probed_at, [1, 3, 7, 15])
        self.assertEqual(meta(cat)["error_probes"], 5)
        # the first answer that is not an error ends the back-off
        cat, _ = self.run_build(Http(), cat, now=NOW + datetime.timedelta(hours=48))
        self.assertEqual(meta(cat)["status"], "usable")
        self.assertNotIn("error_probes", meta(cat))

    def test_due_orders_by_error_wait(self):
        iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")
        prev = {"metadata": {"status": "error", "error_probes": 3, "probed_at": iso(NOW)}}
        day = datetime.timedelta(hours=24)
        self.assertIsNone(sync.due(prev, NOW + datetime.timedelta(hours=3), day))
        self.assertEqual(sync.due(prev, NOW + datetime.timedelta(hours=3, minutes=56), day), 1)
        # a model that was never probed (a pending one) is always due, a fresh usable one is not
        self.assertEqual(sync.due({"metadata": {}}, NOW, day), 1)
        self.assertIsNone(sync.due({"metadata": {"status": "usable", "probed_at": iso(NOW)}}, NOW, day))

    def test_a_binding_global_budget_is_shared_between_routes(self):
        lists = dict(LISTS, **{"https://or/v1/models": {"data": [{"id": f"v/m{i}:free"} for i in range(10)]}})
        http = Http(lists=lists)
        self.run_build(http, max_probes=4)
        urls = [u for u, _ in http.probed()]
        self.assertEqual(len(urls), 4)
        # openrouter lists first and has 10 due; without turn-taking it would take all 4
        self.assertLess(urls.count("https://or/v1/chat/completions"), 4)

    def test_never_probed_unorouter_ids_are_probed_six_a_run_and_cover_the_backlog(self):
        lists = {"https://uno/v1/models": {"data": [{"id": f"m{i}:free"} for i in range(20)]}}
        env = {"UNOROUTER_API_BASE": "https://uno/v1"}
        cat = {}
        for hour in range(4):
            http = Http(lists=lists)
            cat, stats = sync.build(env, http, cat, NOW + datetime.timedelta(hours=hour))
            self.assertEqual(stats["probed"], 6 if hour < 3 else 2)
        counts = cat["providers"]["unorouter"]["metadata"]["by_status"]
        self.assertEqual(counts, {"usable": 20})

    def test_a_vanished_model_is_kept_once_and_dropped_on_the_second_run(self):
        first, _ = self.run_build(Http())
        lists = dict(LISTS, **{"https://oc/v1/models": {"data": [{"id": "glm-5.3-flash"}]}})
        second, _ = self.run_build(Http(lists=lists), first, now=NOW + datetime.timedelta(hours=1))
        kept = {m["id"]: m["metadata"] for m in second["providers"]["opencode-go"]["models"]}
        self.assertEqual(sorted(kept), ["opencode-go/deepseek-v4-pro", "opencode-go/glm-5.3-flash"])
        self.assertEqual(kept["opencode-go/deepseek-v4-pro"]["absent_runs"], 1)
        self.assertEqual(kept["opencode-go/deepseek-v4-pro"]["status"], "usable")  # the rest is unchanged
        self.assertNotIn("absent_runs", kept["opencode-go/glm-5.3-flash"])
        third, _ = self.run_build(Http(lists=lists), second, now=NOW + datetime.timedelta(hours=2))
        self.assertEqual([m["id"] for m in third["providers"]["opencode-go"]["models"]], ["opencode-go/glm-5.3-flash"])

    def test_a_reappearing_model_resets_its_absence(self):
        first, _ = self.run_build(Http())
        lists = dict(LISTS, **{"https://oc/v1/models": {"data": [{"id": "glm-5.3-flash"}]}})
        second, _ = self.run_build(Http(lists=lists), first, now=NOW + datetime.timedelta(hours=1))
        third, _ = self.run_build(Http(), second, now=NOW + datetime.timedelta(hours=2))
        kept = {m["id"]: m["metadata"] for m in third["providers"]["opencode-go"]["models"]}
        self.assertNotIn("absent_runs", kept["opencode-go/deepseek-v4-pro"])

    def test_failing_probes_count_consecutive_blocked_probes(self):
        url = "https://oc/v1/chat/completions"
        blocked = (403, '{"error":{"message":"hosted outside Europe"}}')
        first, _ = self.run_build(Http(answers={(url, "glm-5.3-flash"): blocked}))
        meta = {m["id"]: m["metadata"] for p in first["providers"].values() for m in p["models"]}
        self.assertEqual(meta["opencode-go/glm-5.3-flash"]["failing_probes"], 1)
        self.assertEqual(meta["opencode-go/deepseek-v4-pro"]["failing_probes"], 0)
        # carried over while the model is fresh
        fresh, _ = self.run_build(Http(), first, now=NOW + datetime.timedelta(hours=1))
        meta = {m["id"]: m["metadata"] for p in fresh["providers"].values() for m in p["models"]}
        self.assertEqual(meta["opencode-go/glm-5.3-flash"]["failing_probes"], 1)
        # a second blocked probe a day later
        again, _ = self.run_build(Http(answers={(url, "glm-5.3-flash"): blocked}), first, now=NOW + datetime.timedelta(hours=25))
        meta = {m["id"]: m["metadata"] for p in again["providers"].values() for m in p["models"]}
        self.assertEqual(meta["opencode-go/glm-5.3-flash"]["failing_probes"], 2)
        # a usable answer clears it
        ok, _ = self.run_build(Http(), again, now=NOW + datetime.timedelta(hours=50))
        meta = {m["id"]: m["metadata"] for p in ok["providers"].values() for m in p["models"]}
        self.assertEqual(meta["opencode-go/glm-5.3-flash"]["failing_probes"], 0)

    def test_probe_limits_and_corrupt_cache(self):
        http = Http()
        cat, stats = self.run_build(http, max_probes=3)
        self.assertEqual(stats["probed"], 3)
        self.assertIn("pending", {m["metadata"]["status"] for p in cat["providers"].values() for m in p["models"]})
        http = Http()
        sync.build(ENV, http, {}, NOW, route_limits={"opencode-go": 1}, max_probes=50)
        self.assertEqual(len([m for m in http.probed() if m[0] == "https://oc/v1/chat/completions"]), 1)
        old = {"providers": {"local": {"models": [{"id": "local/qwen3:32b", "metadata": {"status": "usable", "probed_at": "garbage"}}]}}}
        http = Http()
        self.run_build(http, old)
        self.assertIn("qwen3:32b", http.probed_ids())
        http = Http()
        self.run_build(http, {"providers": {"local": {"models": [{"id": "local/qwen3:32b", "metadata": {"status": "usable"}}]}}})
        self.assertIn("qwen3:32b", http.probed_ids())

    def test_openrouter_free_probes_are_capped_per_run(self):
        lists = dict(LISTS, **{"https://or/v1/models": {"data": [{"id": f"v/m{i}:free"} for i in range(40)]}})
        http = Http(lists=lists)
        self.run_build(http)
        self.assertEqual(len([m for m in http.probed() if m[0] == "https://or/v1/chat/completions"]), 30)

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

    def test_opencode_probe_carries_a_session_header_and_hints_are_flagged(self):
        http = Http()
        cat, _ = self.run_build(http, route_limits={})
        call = next(c for c in http.calls if c[0] == "POST" and c[1] == "https://oc/v1/chat/completions")
        self.assertEqual(call[2]["x-opencode-session"], "litellm-catalog-probe")
        hinted = {m["id"]: m["metadata"].get("needs_org_setting_hint") for m in cat["providers"]["opencode-go"]["models"]}
        self.assertEqual(hinted, {"opencode-go/deepseek-v4-pro": True, "opencode-go/glm-5.3-flash": None})

    def test_archived_models_are_dropped_and_openrouter_prefers_the_account_list(self):
        lists = dict(LISTS, **{"https://m/v1/models": {"data": [{"id": "old", "archived": True}, {"id": "new"}]},
                               "https://or/v1/models/user": {"data": [{"id": "only/mine:free"}]}})
        ids = [m["id"] for m in sync.list_models(next(e for e in sync.ROUTES if e["route"] == "vibe"), ENV, Http(lists=lists))]
        self.assertEqual(ids, ["new"])
        mine = sync.list_models(next(e for e in sync.ROUTES if e["route"] == "openrouter"), ENV, Http(lists=lists))
        self.assertEqual([m["id"] for m in mine], ["only/mine:free"])

    def test_possible_duplicates_are_reported_and_never_merged(self):
        lists = dict(LISTS, **{"https://m/v1/models": {"data": [{"id": "glm-5.3"}]},
                               "https://oc/v1/models": {"data": [{"id": "zai-glm-5.3"}]}})
        cat, _ = self.run_build(Http(lists=lists))
        self.assertEqual(cat["possible_duplicates"], [[
            "opencode-go/zai-glm-5.3", "vibe-api/glm-5.3", "vibe-study-api/glm-5.3", "vibe-study/glm-5.3", "vibe/glm-5.3",
        ]])
        self.assertEqual(self.run_build(Http())[0]["possible_duplicates"], [])

    def test_age(self):
        self.assertEqual(sync.parse_age("2d"), datetime.timedelta(days=2))
        self.assertEqual(sync.parse_age("6h"), datetime.timedelta(hours=6))
        with self.assertRaises(ValueError):
            sync.parse_age("soon")


class Grace(unittest.TestCase):
    """A model that was usable keeps its place through a transient state, and through one absence."""

    def meta(self, **kw):
        return dict({"status": "usable", "was_usable": True}, **kw)

    def test_a_transient_state_keeps_the_model_a_candidate(self):
        for status in ("error", "budget-exhausted", "pending", "blocked", "unknown"):
            self.assertTrue(sync.candidate(self.meta(status=status, failing_probes=1), "chat", "vibe", set()), status)
        # two blocked or unknown probes in a row are what drops it
        for status in ("blocked", "unknown"):
            self.assertFalse(sync.candidate(self.meta(status=status, failing_probes=2), "chat", "vibe", set()), status)
        # a model that was never usable is not a candidate
        self.assertFalse(sync.candidate({"status": "error"}, "chat", "vibe", set()))
        self.assertFalse(sync.candidate({"status": "blocked", "failing_probes": 1}, "chat", "vibe", set()))

    def test_one_absence_keeps_the_model_and_two_drop_it(self):
        self.assertTrue(sync.candidate(self.meta(absent_runs=1), "chat", "vibe", set()))
        self.assertFalse(sync.candidate(self.meta(absent_runs=2), "chat", "vibe", set()))

    def test_a_non_chat_model_is_a_candidate_only_on_an_api_only_route(self):
        meta = {"status": "not-chat", "modality": "embedding"}
        self.assertTrue(sync.candidate(meta, "embedding", "vibe-api", {"vibe-api"}))
        self.assertFalse(sync.candidate(meta, "embedding", "vibe", {"vibe-api"}))
        self.assertFalse(sync.candidate({"status": "not-chat"}, "moderation", "vibe-api", {"vibe-api"}))

    def test_a_paid_openrouter_model_is_never_a_candidate(self):
        self.assertFalse(sync.candidate({"status": "usable", "free": False}, "chat", "openrouter", set()))
        self.assertTrue(sync.candidate({"status": "usable", "free": True}, "chat", "openrouter", set()))

    def test_build_marks_a_usable_model_and_carries_the_mark(self):
        cat, _ = sync.build(ENV, Http(), {}, NOW)
        meta = {m["id"]: m["metadata"] for p in cat["providers"].values() for m in p["models"]}
        self.assertIs(meta["opencode-go/glm-5.3-flash"]["was_usable"], True)
        self.assertNotIn("was_usable", meta["openrouter/c/d"])  # unprobed, never usable
        fresh, _ = sync.build(ENV, Http(), cat, NOW + datetime.timedelta(hours=1))
        meta = {m["id"]: m["metadata"] for p in fresh["providers"].values() for m in p["models"]}
        self.assertIs(meta["opencode-go/glm-5.3-flash"]["was_usable"], True)

    def test_build_clears_the_mark_after_two_blocked_probes(self):
        url = "https://oc/v1/chat/completions"
        blocked = (403, '{"error":{"message":"hosted outside Europe"}}')
        first, _ = sync.build(ENV, Http(), {}, NOW)
        meta = {m["id"]: m["metadata"] for p in first["providers"].values() for m in p["models"]}
        self.assertIs(meta["opencode-go/glm-5.3-flash"]["was_usable"], True)
        second, _ = sync.build(ENV, Http(answers={(url, "glm-5.3-flash"): blocked}), first, NOW + datetime.timedelta(hours=25))
        meta = {m["id"]: m["metadata"] for p in second["providers"].values() for m in p["models"]}
        self.assertEqual(meta["opencode-go/glm-5.3-flash"]["failing_probes"], 1)
        self.assertIs(meta["opencode-go/glm-5.3-flash"]["was_usable"], True)  # one bad probe keeps its place
        third, _ = sync.build(ENV, Http(answers={(url, "glm-5.3-flash"): blocked}), second, NOW + datetime.timedelta(hours=50))
        meta = {m["id"]: m["metadata"] for p in third["providers"].values() for m in p["models"]}
        self.assertEqual(meta["opencode-go/glm-5.3-flash"]["failing_probes"], 2)
        self.assertNotIn("was_usable", meta["opencode-go/glm-5.3-flash"])


class Render(unittest.TestCase):
    def test_the_file_is_a_header_then_a_json_document(self):
        cat = {"providers": {
            "vibe": {"metadata": {}, "models": [{"id": "vibe/mistral-large", "metadata": {"status": "usable"}}]},
            "vibe-api": {"metadata": {}, "models": [{"id": "vibe-api/mistral-large", "metadata": {"status": "usable"}}]},
        }}
        text = sync.render_models(sync.plan_groups(cat, RULES))
        lines = text.splitlines()
        self.assertEqual(lines[0], "# Generated by catalog/sync.py from the probed catalogue. Do not edit by hand.")
        self.assertTrue(lines[1].startswith("#"))
        document = json.loads("\n".join(line for line in lines if not line.startswith("#")))
        self.assertEqual([item["model_name"] for item in document["model_list"]], ["mistral-large", "mistral-large"])
        self.assertEqual(list(document["model_list"][0]), ["model_name", "litellm_params", "model_info"])
        self.assertTrue(text.endswith("\n"))

    def test_an_empty_plan_is_an_empty_model_list(self):
        document = json.loads("\n".join(line for line in sync.render_models([]).splitlines() if not line.startswith("#")))
        self.assertEqual(document, {"model_list": []})


class Gh(Http):
    """GitHub's contents, refs, pulls and labels APIs over a dict of path -> text on main.

    `fail` maps (method, url suffix) to the status that call answers, so one step can be broken.
    """

    def __init__(self, files=None, pulls=None, fail=None, **kw):
        super().__init__(**kw)
        self.files = dict(files or {})
        self.refs = {"main": "sha-main"}
        self.pulls = list(pulls or [])
        self.labels = []
        self.requests = []
        self.fail = dict(fail or {})

    def __call__(self, method, url, headers, body):
        if not url.startswith("https://api.github.com/"):
            return super().__call__(method, url, headers, body)
        self.calls.append((method, url, headers, body))
        self.requests.append((method, url, body))
        for (fail_method, suffix), status in self.fail.items():
            if method == fail_method and url.endswith(suffix):
                return status, "down"
        base = "https://api.github.com/repos/o/r"
        if url.startswith(base + "/contents/"):
            path = url[len(base + "/contents/"):].split("?")[0]
            if method == "GET":
                if path not in self.files:
                    return 404, ""
                return 200, {"content": base64.b64encode(self.files[path].encode()).decode(), "sha": "sha-" + path}
            self.puts.append((path, body))
            self.files[path] = base64.b64decode(body["content"]).decode()
            return 201, {}
        if url == base + "/git/ref/heads/main":
            if "main" not in self.refs:
                return 500, "down"
            return 200, {"object": {"sha": self.refs["main"]}}
        if url == base + "/git/refs" and method == "POST":
            branch = body["ref"].removeprefix("refs/heads/")
            if branch in self.refs:
                return 422, {"message": "Reference already exists"}
            self.refs[branch] = body["sha"]
            return 201, {}
        if url.startswith(base + "/git/refs/heads/") and method == "PATCH":
            self.refs[url.rsplit("/", 1)[1]] = body["sha"]
            return 200, {}
        if url.startswith(base + "/pulls/") and method == "PATCH" and "body" in body:
            return 200, {}
        if url.startswith(base + "/pulls/") and method == "PATCH":
            number = int(url.rsplit("/", 1)[1])
            self.pulls = [pull for pull in self.pulls if pull["number"] != number]
            return 200, {}
        if url.startswith(base + "/pulls"):
            if method == "GET":
                return 200, self.pulls
            self.pulls.append({"number": len(self.pulls) + 1, "title": body["title"], "body": body["body"], "head": body["head"]})
            return 201, self.pulls[-1]
        if url.startswith(base + "/issues/") and url.endswith("/labels"):
            self.labels.append((url, body["labels"]))
            return 200, []
        return super().__call__(method, url, headers, body)


class Main(unittest.TestCase):
    def catalogue_of(self, env, now=NOW):
        """The catalogue main() publishes: the build, plus the groups it planned."""
        cat, _ = sync.build(env, Http(), {}, now)
        cat["groups"] = sync.record_groups(sync.plan_groups(cat, sync.load_rules()))
        return cat

    def test_writes_a_local_file_and_reads_it_back_as_the_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "c.json")
            self.assertEqual(sync.main(["--output", out], ENV, Http(), NOW), 0)
            http = Http()
            sync.main(["--output", out], ENV, http, NOW + datetime.timedelta(hours=1))
            self.assertEqual(http.probed(), [])
            self.assertEqual(json.load(open(out))["version"], 1)
            self.assertTrue(os.path.exists(os.path.join(tmp, "model-overview.md")))
            models = open(os.path.join(tmp, "models.generated.yaml")).read()
            self.assertTrue(models.startswith(sync.MODELS_HEADER))
            self.assertIn("model_list", json.loads("\n".join(line for line in models.splitlines() if not line.startswith("#"))))

    def test_a_stale_pull_request_is_closed_when_main_already_holds_the_text(self):
        gh = Gh(files={"config/models.generated.yaml": "same"}, pulls=[{"number": 7, "head": "o:bot/model-catalog"}])
        self.assertFalse(sync.publish_models("o/r", "t", "same", gh))
        self.assertEqual(gh.pulls, [])
        self.assertEqual(gh.puts, [])

    def test_a_run_that_lists_nothing_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(sync.main(["--output", os.path.join(tmp, "c.json")], {}, Http(), NOW), 1)

    def test_publishes_the_catalogue_when_changed_and_not_when_unchanged(self):
        env = dict(ENV, CATALOG_REPO="o/r", CATALOG_TOKEN="t")
        cat = self.catalogue_of(env)
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "c.json")
            gh = Gh({"model-catalog.json": json.dumps(cat)})
            sync.main(["--output", out], env, gh, NOW + datetime.timedelta(hours=1))
            self.assertEqual([p for p, _ in gh.puts if p == "model-catalog.json"], [])
            gh = Gh({"model-catalog.json": json.dumps(cat)})
            sync.main(["--output", out], env, gh, NOW + datetime.timedelta(hours=25))
            puts = [b for p, b in gh.puts if p == "model-catalog.json"]
            self.assertEqual(len(puts), 1)
            self.assertEqual(puts[0]["sha"], "sha-model-catalog.json")
            self.assertEqual(puts[0]["message"], "chore: refresh model catalogue")

    def test_publishes_the_overview_only_when_it_changed_ignoring_the_stamp(self):
        env = dict(ENV, CATALOG_REPO="o/r", CATALOG_TOKEN="t")
        cat = self.catalogue_of(env)
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "c.json")
            # a different run time alone is not a change
            gh = Gh({"model-catalog.json": json.dumps(cat), "README.md": overview.render(dict(cat, updated_at="1999-01-01T00:00:00Z"))})
            sync.main(["--output", out], env, gh, NOW + datetime.timedelta(hours=1))
            self.assertEqual([p for p, _ in gh.puts if p == "README.md"], [])
            # a real change is
            changed = json.loads(json.dumps(cat))
            changed["providers"]["local"]["models"][0]["metadata"]["status"] = "blocked"
            gh = Gh({"model-catalog.json": json.dumps(cat), "README.md": overview.render(changed)})
            sync.main(["--output", out], env, gh, NOW + datetime.timedelta(hours=1))
            puts = [b for p, b in gh.puts if p == "README.md"]
            self.assertEqual(len(puts), 1)
            self.assertEqual(puts[0]["message"], "chore: refresh model overview")
            self.assertEqual(puts[0]["sha"], "sha-README.md")

    def test_a_catalogue_publish_failure_fails_the_run(self):
        env = dict(ENV, CATALOG_REPO="o/r", CATALOG_TOKEN="t")
        with tempfile.TemporaryDirectory() as tmp:
            gh = Gh(fail={("PUT", "model-catalog.json"): 500})
            buf, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                self.assertEqual(sync.main(["--output", os.path.join(tmp, "c.json")], env, gh, NOW), 1)
            self.assertIn("publishing to o/r/model-catalog.json answered 500", err.getvalue())

    def test_a_catalogue_read_failure_fails_the_run(self):
        env = dict(ENV, CATALOG_REPO="o/r", CATALOG_TOKEN="t")
        with tempfile.TemporaryDirectory() as tmp:
            gh = Gh(fail={("GET", "model-catalog.json"): 500})
            buf, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                self.assertEqual(sync.main(["--output", os.path.join(tmp, "c.json")], env, gh, NOW), 1)
            self.assertIn("reading o/r/model-catalog.json answered 500", err.getvalue())

    def test_no_key_reaches_a_published_file_or_a_reason(self):
        env = dict(ENV, CATALOG_REPO="o/r", CATALOG_TOKEN="t")
        with tempfile.TemporaryDirectory() as tmp:
            gh = Gh()
            sync.main(["--output", os.path.join(tmp, "c.json")], env, gh, NOW)
            published = json.dumps(gh.files)
            for secret in ("k-vibe", "k-study", "k-general", "k-oc", "k-or", "SECRET-RESPONSE"):
                self.assertNotIn(secret, published)

    def test_github_errors_surface(self):
        with self.assertRaisesRegex(RuntimeError, "reading"):
            sync.github_get("o/r", "p", "t", lambda *a: (500, "x"))
        with self.assertRaisesRegex(RuntimeError, "publishing"):
            sync.github_put("o/r", "p", "t", {}, None, lambda *a: (422, "x"))
        self.assertEqual(sync.github_get_text("o/r", "p", "t", lambda *a: (404, "")), (None, None))

    def test_github_reads_a_file_over_one_megabyte_through_the_blob_api(self):
        text = '{"big": true}'
        answers = {
            "contents": (200, {"sha": "s1", "encoding": "none", "content": ""}),
            "blobs": (200, {"sha": "s1", "content": base64.b64encode(text.encode()).decode()}),
        }
        http = lambda method, url, *a: answers["blobs" if "/git/blobs/s1" in url else "contents"]
        self.assertEqual(sync.github_get("o/r", "p", "t", http), ({"big": True}, "s1"))
        answers["blobs"] = (404, "x")
        with self.assertRaisesRegex(RuntimeError, "as a blob"):
            sync.github_get("o/r", "p", "t", http)


class Models(unittest.TestCase):
    """The generated deployments: a bot branch and a pull request in the litellm repository."""

    def env(self, **extra):
        return dict(ENV, MODELS_REPO="o/r", MODELS_TOKEN="t-models", **extra)

    def test_opens_a_branch_a_file_and_a_pull_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            gh = Gh()
            self.assertEqual(sync.main(["--output", os.path.join(tmp, "c.json")], self.env(), gh, NOW), 0)
            self.assertEqual(gh.refs[sync.MODELS_BRANCH], "sha-main")
            put = next(b for p, b in gh.puts if p == sync.MODELS_PATH)
            self.assertEqual(put["branch"], sync.MODELS_BRANCH)
            self.assertNotIn("sha", put)  # the file is new on main
            text = base64.b64decode(put["content"]).decode()
            self.assertTrue(text.startswith(sync.MODELS_HEADER))
            self.assertEqual(len(gh.pulls), 1)
            self.assertEqual(gh.pulls[0]["title"], "fix(models): refresh the generated model deployments")
            self.assertEqual(gh.pulls[0]["head"], sync.MODELS_BRANCH)
            self.assertIn("deployments added:", gh.pulls[0]["body"])
            self.assertIn("deployments removed:", gh.pulls[0]["body"])
            self.assertEqual(gh.labels, [("https://api.github.com/repos/o/r/issues/1/labels", ["type: chore", "area: platform"])])

    def test_a_second_run_reuses_the_open_pull_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "c.json")
            sync.main(["--output", out], self.env(), Gh(), NOW)
            gh = Gh(pulls=[{"number": 7}])
            self.assertEqual(sync.main(["--output", out], self.env(), gh, NOW + datetime.timedelta(hours=1)), 0)
            self.assertEqual([p for p, _ in gh.puts], [sync.MODELS_PATH])
            self.assertEqual(len(gh.pulls), 1)  # the open pull request is reused, not duplicated
            self.assertEqual(gh.labels[0][0], "https://api.github.com/repos/o/r/issues/7/labels")

    def test_a_repository_that_already_holds_the_file_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "c.json")
            gh = Gh()
            sync.main(["--output", out], self.env(), gh, NOW)
            text = base64.b64decode(next(b for p, b in gh.puts if p == sync.MODELS_PATH)["content"]).decode()
            # main holds the file (the pull request was merged, or it is stale): nothing is written
            gh = Gh({sync.MODELS_PATH: text}, pulls=[{"number": 7}])
            self.assertEqual(sync.main(["--output", out], self.env(), gh, NOW + datetime.timedelta(hours=1)), 0)
            self.assertEqual(gh.puts, [])
            self.assertEqual(gh.refs, {"main": "sha-main"})
            self.assertEqual(gh.pulls, [])  # a stale open pull request is closed

    def test_the_body_counts_the_deployments_against_main(self):
        before = sync.render_models([])
        after = sync.render_models([{"name": "mistral-large", "litellm_params": {}, "model_info": {"id": "vibe/mistral-large"}}])
        self.assertEqual(sync.models_body(before, after), (
            "The catalogue sync regenerated `config/models.generated.yaml` from the probed catalogue.\n\n"
            "- deployments added: 1\n- deployments removed: 0\n"
        ))
        self.assertEqual(sync.models_body(after, before).count("deployments removed: 1"), 1)
        self.assertEqual(sync.models_body(None, after).count("deployments added: 1"), 1)

    def test_no_token_reaches_a_request_body_or_the_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            gh = Gh()
            buf, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                sync.main(["--output", os.path.join(tmp, "c.json")], self.env(), gh, NOW)
            self.assertNotIn("t-models", json.dumps([body for _, _, body in gh.requests]))
            self.assertNotIn("t-models", buf.getvalue() + err.getvalue())
            self.assertNotIn("t-models", json.dumps(gh.files))
            github = [c for c in gh.calls if c[1].startswith("https://api.github.com")]
            self.assertTrue(github)
            for call in github:
                self.assertEqual(call[2]["Authorization"], "Bearer t-models")

    def test_a_github_failure_fails_the_run_and_names_the_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            gh = Gh()
            gh.refs = {}  # main cannot be read
            buf, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                self.assertEqual(sync.main(["--output", os.path.join(tmp, "c.json")], self.env(), gh, NOW), 1)
            self.assertIn("reading o/r's main answered 500", err.getvalue())
            self.assertNotIn("t-models", err.getvalue())

    def test_a_label_failure_does_not_fail_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            gh = Gh(fail={("POST", "/labels"): 422})
            self.assertEqual(sync.main(["--output", os.path.join(tmp, "c.json")], self.env(), gh, NOW), 0)
            self.assertEqual(len(gh.pulls), 1)


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


class FreeSignals(unittest.TestCase):
    """One free signal per kind, and the anonymous probe, for the first-wave free-only routes."""

    def listed(self, route, data, rules=None, env=None):
        env = dict(env or {}, **{e["base_env"]: f"https://{route}/v1" for e in sync.ROUTES if e["route"] == route})
        http = Http(lists={f"https://{route}/v1/models": {"data": data}})
        found = sync.list_models(next(e for e in sync.ROUTES if e["route"] == route), env, http, rules)
        return {m["id"]: m["free"] for m in found}, http

    def test_kilo_is_free_when_it_says_isFree(self):
        free, _ = self.listed("kilo", [{"id": "a:free", "isFree": True}, {"id": "b", "isFree": False}, {"id": "c"}])
        self.assertEqual(free, {"a:free": True, "b": False, "c": False})

    def test_requesty_is_free_at_zero_price_and_a_missing_price_is_not_free(self):
        free, _ = self.listed("requesty", [{"id": "a", "input_price": 0, "output_price": 0}, {"id": "b", "input_price": 0, "output_price": 1e-6},
                                           {"id": "c"}, {"id": "d", "input_price": "0", "output_price": "0"}])
        self.assertEqual(free, {"a": True, "b": False, "c": False, "d": True})

    def test_ovh_is_free_at_zero_pricing(self):
        free, _ = self.listed("ovh", [{"id": "a", "pricing": {"prompt": 0, "completion": 0}}, {"id": "b", "pricing": {"prompt": 1, "completion": 0}},
                                      {"id": "c"}])
        self.assertEqual(free, {"a": True, "b": False, "c": False})

    def test_naga_and_routeway_are_free_by_the_free_suffix(self):
        for route in ("naga", "routeway"):
            free, _ = self.listed(route, [{"id": "m:free"}, {"id": "m"}])
            self.assertEqual(free, {"m:free": True, "m": False}, route)

    def test_nvidia_lists_only_free_models(self):
        free, _ = self.listed("nvidia", [{"id": "nvidia/a"}, {"id": "nvidia/embed-x"}])
        self.assertEqual(free, {"nvidia/a": True, "nvidia/embed-x": True})

    def test_the_static_list_mechanism_is_the_signal_of_a_plan_wide_free_tier(self):
        # Synthetic lists: this proves the mechanism, not what routing.json holds (see test_routing).
        rules = {"free_ids": {"groq": ["openai/gpt-oss-120b"], "zai": ["glm-4.7-flash"], "cloudflare": ["@cf/x"]}}
        for route, ids in (("groq", ["openai/gpt-oss-120b", "llama-4"]), ("zai", ["glm-4.7-flash", "glm-5"]),
                           ("cloudflare", ["@cf/x", "@cf/y"])):
            env = {"CLOUDFLARE_ACCOUNT_ID": "acc"} if route == "cloudflare" else None
            free, _ = self.listed(route, [{"id": model} for model in ids], rules, env)
            self.assertEqual(free, {ids[0]: True, ids[1]: False}, route)
        # No list, nothing free: a paid id can never be deployed.
        free, _ = self.listed("groq", [{"id": "openai/gpt-oss-120b"}])
        self.assertEqual(free, {"openai/gpt-oss-120b": False})

    def test_a_static_free_id_the_listing_prices_above_zero_is_refused(self):
        rules = {"free_ids": {"groq": ["a", "b", "c", "d"], "zai": ["a", "b"]}}
        data = [{"id": "a", "pricing": {"prompt": "0.000001", "completion": "0"}},
                {"id": "b", "input_price": 0, "output_price": 0.5},
                {"id": "c", "pricing": {"input": 0, "output": 0}},
                {"id": "d"}]
        free, _ = self.listed("groq", data, rules)
        self.assertEqual(free, {"a": False, "b": False, "c": True, "d": True})
        free, _ = self.listed("zai", data[:2], rules)
        self.assertEqual(free, {"a": False, "b": False})

    def test_inferbase_carries_a_real_price_and_an_unpriced_id_is_not_free(self):
        rules = {"free_ids": {"inferbase": ["a", "b"]}}
        found = {m["id"]: m for m in sync.list_models(
            next(e for e in sync.ROUTES if e["route"] == "inferbase"), {"INFERBASE_API_BASE": "https://inferbase/v1"},
            Http(lists={"https://inferbase/v1/models": {"data": [
                {"id": "a", "pricing": {"prompt": "0.0000005", "completion": "0.000002"}}, {"id": "b"}, {"id": "c"}]}}), rules)}
        self.assertTrue(found["a"]["free"])
        self.assertEqual(found["a"]["price"], {"input": 5e-7, "output": 2e-6})
        self.assertFalse(found["b"]["free"])  # in the list, not priced: fail closed
        self.assertFalse(found["c"]["free"])  # priced or not, not in the list

    def test_an_anonymous_route_is_listed_and_probed_without_authorization(self):
        for route in ("kilo", "ovh"):
            self.assertTrue(next(e for e in sync.ROUTES if e["route"] == route)["anonymous"])
        env = {"KILO_API_BASE": "https://kilo/v1"}
        http = Http(lists={"https://kilo/v1/models": {"data": [{"id": "a:free", "isFree": True}, {"id": "paid"}]}})
        cat, stats = sync.build(env, http, {"providers": {}}, NOW, rules={})
        self.assertEqual(stats["route_errors"], [])
        # Listed and probed, no Authorization anywhere; the paid id is not probed.
        requests = [c for c in http.calls if c[1].startswith("https://kilo/v1")]
        self.assertEqual(len(requests), 2)
        for call in requests:
            self.assertNotIn("Authorization", call[2])
        self.assertEqual(http.probed_ids(), ["a:free"])
        statuses = {m["id"]: m["metadata"]["status"] for m in cat["providers"]["kilo"]["models"]}
        self.assertEqual(statuses, {"kilo/a:free": "usable", "kilo/paid": "unprobed"})

    def test_a_keyed_free_route_is_skipped_without_its_key(self):
        http = Http(lists={})
        cat, stats = sync.build({"REQUESTY_API_BASE": "https://requesty/v1"}, http, {"providers": {}}, NOW, rules={})
        self.assertEqual((cat["providers"]["requesty"]["models"], stats["route_errors"]), ([], []))
        self.assertEqual(http.calls, [])

    def test_second_wave_signals_fail_closed(self):
        free, _ = self.listed("electronhub", [{"id": "m:free"}, {"id": "m"}])
        self.assertEqual(free, {"m:free": True, "m": False})
        free, _ = self.listed("unorouter", [{"id": "m:free"}, {"id": "m"}])
        self.assertEqual(free, {"m:free": True, "m": False})
        free, _ = self.listed("airforce", [{"id": "a", "tier": "free"}, {"id": "b", "tier": "pro"}, {"id": "c"}])
        self.assertEqual(free, {"a": True, "b": False, "c": False})
        zero = {"input": "0", "output": "0"}
        items = [{"id": "str-zero", "pricing": zero}, {"id": "num-zero", "pricing": {"input": 0, "output": 0.0}},
                 {"id": "paid-output", "pricing": {"input": "0", "output": "0.000001"}}, {"id": "no-pricing"},
                 {"id": "empty-pricing", "pricing": {}}, {"id": "empty-strings", "pricing": {"input": "", "output": ""}},
                 {"id": "null", "pricing": {"input": None, "output": None}}, {"id": "input-only", "pricing": {"input": 0}},
                 {"id": "bool", "pricing": {"input": False, "output": False}}, {"id": "flat", "input_price": 0, "output_price": 0},
                 {"id": "prompt-names", "pricing": {"prompt": "0", "completion": "0"}}]
        free, _ = self.listed("vercel", items)
        self.assertEqual({k for k, v in free.items() if v}, {"str-zero", "num-zero"})
        # Novita: the flat per-million fields of the item, both explicitly zero; nothing else is read.
        free, _ = self.listed("novita", [{"id": "a", "input_token_price_per_m": 0, "output_token_price_per_m": 0},
                                         {"id": "b", "input_token_price_per_m": 0, "output_token_price_per_m": 5},
                                         {"id": "c", "input_token_price_per_m": 0}, {"id": "d"},
                                         {"id": "e", "input_token_price_per_m": "", "output_token_price_per_m": ""},
                                         {"id": "f", "pricing": {"input": 0, "output": 0}}])
        self.assertEqual(free, {"a": True, "b": False, "c": False, "d": False, "e": False, "f": False})

    def test_an_approved_id_on_a_pay_per_token_host_is_refused_when_the_listing_prices_it(self):
        rules = {"free_ids": {"together": ["t"], "typhoon": ["t"], "neosantara": ["t"]}}
        for route in ("together", "typhoon", "neosantara"):
            for label, item in (("input", {"pricing": {"input": 0.88, "output": 0}}), ("output only", {"pricing": {"output": 0.1}}),
                                ("prompt", {"pricing": {"prompt": "0.000001"}}), ("flat", {"input_price": 1}),
                                ("garbage", {"pricing": {"input": "n/a"}})):
                free, _ = self.listed(route, [dict(item, id="t")], rules)
                self.assertEqual(free, {"t": False}, (route, label))
            for item in ({}, {"pricing": {"input": 0, "output": 0}}):  # no price, or an explicit zero: the approved list decides
                free, _ = self.listed(route, [dict(item, id="t")], rules)
                self.assertEqual(free, {"t": True}, route)

    def test_a_bare_list_is_accepted_for_together_only_and_a_malformed_listing_is_a_route_error(self):
        for route in ("electronhub", "poe", "plugsky", "vercel"):
            entry_ = next(e for e in sync.ROUTES if e["route"] == route)
            for body in ([{"id": "m:free"}], {"models": []}, "oops", None):
                http = Http(lists={f"https://{route}/v1/models": body})
                with self.assertRaises(RuntimeError, msg=(route, body)):
                    sync.list_models(entry_, {entry_["base_env"]: f"https://{route}/v1"}, http, {})
                with self.assertRaises(RuntimeError, msg=(route, body)):
                    sync.scan_candidates(entry_, {entry_["base_env"]: f"https://{route}/v1"}, http, {})

    def test_second_wave_static_routes_deploy_only_approved_ids_and_a_priced_id_is_refused(self):
        rules = {"free_ids": {"together": ["Prism-ML/Ternary-Bonsai-27B", "x/priced"], "plugsky": []}}
        data = [{"id": "Prism-ML/Ternary-Bonsai-27B"}, {"id": "other"}, {"id": "x/priced", "pricing": {"input": 0.88, "output": 0.88}}]
        free, _ = self.listed("together", data, rules)
        self.assertEqual(free, {"Prism-ML/Ternary-Bonsai-27B": True, "other": False, "x/priced": False})
        free, _ = self.listed("plugsky", [{"id": "micro"}], rules)
        self.assertEqual(free, {"micro": False})  # an empty list deploys nothing

    def test_together_lists_a_bare_array(self):
        env = {"TOGETHER_API_BASE": "https://together/v1"}
        http = Http(lists={"https://together/v1/models": [{"id": "Prism-ML/Ternary-Bonsai-27B"}, {"id": "paid"}]})
        rules = {"free_ids": {"together": ["Prism-ML/Ternary-Bonsai-27B"]}}
        found = sync.list_models(next(e for e in sync.ROUTES if e["route"] == "together"), env, http, rules)
        self.assertEqual({m["id"]: m["free"] for m in found}, {"Prism-ML/Ternary-Bonsai-27B": True, "paid": False})
        candidates = sync.scan_candidates(next(e for e in sync.ROUTES if e["route"] == "together"), env, http, rules)
        self.assertEqual({c["id"]: c["approved"] for c in candidates}, {"Prism-ML/Ternary-Bonsai-27B": True, "paid": False})

    def test_the_candidate_scan_shows_what_an_empty_static_route_lists_and_deploys_nothing(self):
        env = {"PLUGSKY_API_BASE": "https://plugsky/v1", "PLUGSKY_API_KEY": "k"}
        http = Http(lists={"https://plugsky/v1/models": {"data": [{"id": "micro", "pricing": {"input": 0, "output": 0}}, {"id": "lite"}]}})
        rules = {"free_ids": {"plugsky": []}}
        cat, stats = sync.build(env, http, {"providers": {}}, NOW, rules=rules)
        items = {i["id"]: i for i in cat["free_candidates"]["plugsky"]}
        self.assertEqual((items["micro"]["approved"], items["micro"]["suggested"], items["lite"]["suggested"]), (False, True, False))
        self.assertEqual(http.probed_ids(), [])
        self.assertEqual(sync.plan_groups(cat, dict(RULES, free_ids=rules["free_ids"])), [])

    def test_unorouter_is_anonymous_and_a_keyed_second_wave_route_is_skipped_without_its_key(self):
        env = {"UNOROUTER_API_BASE": "https://uno/v1"}
        http = Http(lists={"https://uno/v1/models": {"data": [{"id": "a:free"}, {"id": "paid"}]}})
        cat, stats = sync.build(env, http, {"providers": {}}, NOW, rules={})
        self.assertEqual(stats["route_errors"], [])
        self.assertEqual(http.probed_ids(), ["a:free"])
        for call in http.calls:
            self.assertNotIn("Authorization", call[2])
        http = Http(lists={})
        cat, stats = sync.build({"ELECTRONHUB_API_BASE": "https://eh/v1"}, http, {"providers": {}}, NOW, rules={})
        self.assertEqual((cat["providers"]["electronhub"]["models"], stats["route_errors"], http.calls), ([], [], []))


if __name__ == "__main__":
    unittest.main()


CF_ACCOUNTS = "https://api.cloudflare.com/client/v4/accounts"
CF_SEARCH = CF_ACCOUNTS + "/acc123/ai/models/search?per_page=100&page=1"
CF_ENV = dict(ENV, CLOUDFLARE_API_KEY="k-cf", CLOUDFLARE_ACCOUNT_ID="REPLACE_WITH_CLOUDFLARE_ACCOUNT_ID")
CF_RULES = {"free_ids": {"cloudflare": ["@cf/a/approved"]}, "cooldowns": {}, "tiers": [[["vibe", 1]]]}
CF_LISTS = dict(LISTS, **{
    CF_ACCOUNTS: {"result": [{"id": "acc123", "name": "mine"}]},
    CF_SEARCH: {"result": [
        {"name": "@cf/a/approved", "task": {"name": "Text Generation"}, "properties": [{"property_id": "price", "value": [{"unit": "per M input tokens", "price": 0.1, "currency": "USD"}]}]},
        {"name": "@cf/b/zero", "task": {"name": "Text Generation"}, "properties": [{"property_id": "price", "value": [{"unit": "per M input tokens", "price": 0, "currency": "USD"}]}, {"property_id": "context_window", "value": 8192}]},
        {"name": "@cf/c/unknown", "task": {"name": "Text Generation"}}]},
    "https://api.cloudflare.com/client/v4/accounts/acc123/ai/v1/models": {"data": [{"id": "@cf/a/approved"}, {"id": "@cf/b/zero"}]},
    "https://ib/v1/models": {"data": [{"id": "x", "pricing": {"input": "0", "output": "0"}}, {"id": "y", "input_price": 1, "output_price": 2}]},
})


class FreeCandidates(unittest.TestCase):
    def build(self, lists=None, env=None, rules=CF_RULES):
        http = Http(lists=CF_LISTS if lists is None else lists)
        cat, stats = sync.build(env or CF_ENV, http, {}, NOW, rules=rules)
        return cat, stats, http

    def test_candidates_are_listed_with_signals_and_never_deployed(self):
        cat, _, _ = self.build()
        items = {i["id"]: i for i in cat["free_candidates"]["cloudflare"]}
        self.assertTrue(items["@cf/a/approved"]["approved"])
        self.assertFalse(items["@cf/a/approved"]["suggested"])
        self.assertFalse(items["@cf/b/zero"]["approved"])
        self.assertTrue(items["@cf/b/zero"]["suggested"])
        self.assertEqual(items["@cf/b/zero"]["signals"]["context_length"], 8192)
        self.assertFalse(items["@cf/c/unknown"]["suggested"])
        deployed = [d["model_info"]["id"] for d in sync.plan_groups(cat, CF_RULES)]
        self.assertFalse([i for i in deployed if "@cf/b/zero" in i or "@cf/c/unknown" in i])
        self.assertFalse(cat["providers"]["cloudflare"]["models"][0]["metadata"]["free"] and cat["providers"]["cloudflare"]["models"][1]["metadata"]["free"])
        free = {m["id"]: m["metadata"]["free"] for m in cat["providers"]["cloudflare"]["models"]}
        self.assertEqual(free, {"cloudflare/@cf/a/approved": True, "cloudflare/@cf/b/zero": False})

    def test_the_account_is_discovered_from_the_token_when_the_env_is_a_placeholder(self):
        _, _, http = self.build()
        self.assertEqual(http.calls[0][:2], ("GET", CF_ACCOUNTS))
        self.assertEqual(http.calls[0][2], {"Authorization": "Bearer k-cf"})
        self.assertIn(("GET", CF_SEARCH), [c[:2] for c in http.calls])

    def test_a_set_account_id_is_not_discovered(self):
        _, _, http = self.build(env=dict(CF_ENV, CLOUDFLARE_ACCOUNT_ID="acc123"))
        self.assertNotIn(CF_ACCOUNTS, [c[1] for c in http.calls])

    def test_two_accounts_are_not_guessed(self):
        lists = dict(CF_LISTS, **{CF_ACCOUNTS: {"result": [{"id": "a"}, {"id": "b"}]}})
        cat, stats, _ = self.build(lists=lists)
        self.assertEqual(cat["free_candidates"]["cloudflare"], [])
        self.assertTrue(any("2 accounts" in e for e in stats["route_errors"]))

    def test_a_listing_error_leaves_candidates_empty_without_failing(self):
        lists = dict(CF_LISTS, **{CF_SEARCH: None})
        cat, stats, _ = self.build(lists=lists)
        self.assertEqual(cat["free_candidates"]["cloudflare"], [])
        self.assertTrue(any("candidate scan" in e for e in stats["route_errors"]))
        self.assertGreater(stats["listed"], 0)

    def test_inferbase_records_listed_prices_and_a_keyless_route_is_skipped(self):
        env = dict(CF_ENV, INFERBASE_API_BASE="https://ib/v1", INFERBASE_API_KEY="k-ib")
        cat, _, _ = self.build(env=env)
        ib = {i["id"]: i for i in cat["free_candidates"]["inferbase"]}
        self.assertTrue(ib["x"]["suggested"])
        self.assertEqual(ib["y"]["signals"]["input_price"], 1.0)
        self.assertFalse(ib["y"]["suggested"])
        self.assertNotIn("axforge", cat["free_candidates"])

    def test_the_overview_and_the_pull_request_body_carry_the_section(self):
        cat, _, _ = self.build()
        self.assertIn("## Free-route candidates", overview.render(cat))
        self.assertIn("@cf/b/zero", overview.render(cat))
        body = sync.models_body("", "{}", cat["free_candidates"])
        self.assertIn("### Free-route candidates", body)
        self.assertIn("| @cf/b/zero | candidate |", body)
        self.assertIn("deployments added:", sync.models_body("", "{}"))


class PlanFreeRoutes(unittest.TestCase):
    """Groq, Z.ai and Cloudflare are free per plan; a pay-per-token host is free per price."""

    PRICED = [{"id": "approved", "pricing": {"prompt": "1.5e-07", "completion": "6e-07"}}, {"id": "other", "input_price": 1e-7, "output_price": 1e-7}]

    def listed(self, route, data, rules):
        env = {e["base_env"]: f"https://{route}/v1" for e in sync.ROUTES if e["route"] == route}
        http = Http(lists={f"https://{route}/v1/models": {"data": data}})
        return {m["id"]: m["free"] for m in sync.list_models(next(e for e in sync.ROUTES if e["route"] == route), env, http, rules)}

    def test_a_plan_free_route_keeps_an_approved_id_the_listing_prices(self):
        for route in ("groq", "zai"):
            rules = {"free_ids": {route: ["approved"]}, "free_by": {route: "plan"}}
            self.assertEqual(self.listed(route, self.PRICED, rules), {"approved": True, "other": False}, route)

    def test_a_price_route_refuses_an_approved_id_the_listing_prices_and_it_is_the_default(self):
        for rules in ({"free_ids": {"together": ["approved"]}, "free_by": {"together": "price"}}, {"free_ids": {"together": ["approved"]}}):
            self.assertEqual(self.listed("together", self.PRICED, rules), {"approved": False, "other": False})

    def test_the_shipped_rules_name_how_each_static_route_is_free(self):
        rules = sync.load_rules()
        self.assertEqual({r: sync.free_by(rules, r) for r in ("groq", "cloudflare", "zai", "together", "typhoon", "neosantara")},
                         {"groq": "plan", "cloudflare": "plan", "zai": "plan", "together": "price", "typhoon": "price", "neosantara": "price"})

    def test_an_unknown_free_by_value_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.json")
            with open(path, "w") as handle:
                json.dump({"free_by": {"groq": "free"}}, handle)
            with self.assertRaises(RuntimeError):
                sync.load_rules(path)

    def test_an_approved_id_a_plan_free_listing_omits_is_still_probed(self):
        # Z.ai's /models leaves its *-flash ids out.
        rules = {"free_ids": {"zai": ["glm-4.7-flash"]}, "free_by": {"zai": "plan"}}
        found = self.listed("zai", [{"id": "glm-5"}], rules)
        self.assertEqual(found, {"glm-5": False, "glm-4.7-flash": True})
        # A price-guarded route cannot: an unlisted id has no price to check.
        self.assertEqual(self.listed("typhoon", [{"id": "x"}], {"free_ids": {"typhoon": ["gone"]}}), {"x": False})

    def test_a_priced_groq_id_is_probed_and_deployed_once_usable(self):
        rules = dict(sync.load_rules(), free_ids={"groq": ["approved"]})
        http = Http(lists={"https://groq/v1/models": {"data": self.PRICED}})
        cat, _ = sync.build({"GROQ_API_BASE": "https://groq/v1", "GROQ_API_KEY": "k"}, http, {}, NOW, rules=rules)
        meta = {m["id"]: m["metadata"] for m in cat["providers"]["groq"]["models"]}
        self.assertEqual((meta["groq/approved"]["free"], meta["groq/approved"]["status"]), (True, "usable"))
        self.assertEqual(meta["groq/other"]["status"], "unprobed")
        self.assertIn("free_ids", meta["groq/other"]["reason"])
        self.assertTrue(any("approved" in d["model_info"]["id"] for d in sync.plan_groups(cat, rules)))


class ProbeReasons(unittest.TestCase):
    def test_a_reason_is_never_empty(self):
        for status, text in ((403, ""), (403, '{"status":403}'), (401, "Unauthorized access")):
            self.assertTrue(sync.classify(status, text)[1], (status, text))
        self.assertIn("HTTP 403", sync.classify(403, "")[1])

    def test_a_title_and_detail_body_gives_its_reason(self):
        status = sync.classify(403, '{"status":403,"title":"Forbidden","detail":"Authorization failed"}')
        self.assertEqual(status, ("blocked", "Forbidden Authorization failed"))


class CloudflareWithoutAnAccount(unittest.TestCase):
    def test_a_missing_account_is_one_route_error(self):
        env = dict(CF_ENV)
        lists = dict(LISTS, **{CF_ACCOUNTS: {"result": []}})
        cat, stats = sync.build(env, Http(lists=lists), {}, NOW, rules=CF_RULES)
        self.assertEqual([e for e in stats["route_errors"] if "cloudflare" in e], [
            "cloudflare: CLOUDFLARE_ACCOUNT_ID is not set (Vault field cloudflare_account_id at secret/data/platform/litellm) and the token sees 0 accounts; set it"])
        self.assertEqual(cat["free_candidates"]["cloudflare"], [])

    def test_a_template_base_names_the_account_variable(self):
        entry = next(e for e in sync.ROUTES if e["route"] == "cloudflare")
        with self.assertRaisesRegex(RuntimeError, "CLOUDFLARE_ACCOUNT_ID is not set"):
            sync.list_models(entry, {}, Http(lists={}), {})


class CandidatesAreChatModels(unittest.TestCase):
    def test_non_chat_models_are_dropped_and_suggested_come_first(self):
        data = [{"id": "text-embedding-x"}, {"id": "img", "type": "image"}, {"id": "flux-1"}, {"id": "chat-a"},
                {"id": "chat-free", "pricing": {"input": 0, "output": 0}}, {"id": "whisper-large"}]
        entry = next(e for e in sync.ROUTES if e["route"] == "inferbase")
        http = Http(lists={"https://ib/v1/models": {"data": data}})
        found = sync.scan_candidates(entry, {"INFERBASE_API_BASE": "https://ib/v1"}, http, {})
        self.assertEqual([c["id"] for c in found], ["chat-a", "chat-free"])
        text = "\n".join(overview.candidates_section({"inferbase": found}))
        self.assertLess(text.index("chat-free"), text.index("chat-a"))

    def test_an_approved_id_shows_even_when_it_looks_like_no_chat_model(self):
        entry = next(e for e in sync.ROUTES if e["route"] == "groq")
        http = Http(lists={"https://g/v1/models": {"data": [{"id": "whisper-large-v3"}]}})
        found = sync.scan_candidates(entry, {"GROQ_API_BASE": "https://g/v1"}, http, {"free_ids": {"groq": ["whisper-large-v3"]}})
        self.assertEqual([c["id"] for c in found], ["whisper-large-v3"])

    def test_the_pull_request_body_and_the_overview_show_the_same_section(self):
        cands = {"zai": [{"id": "glm-4.7-flash", "approved": True, "signals": {"unlisted": True}, "suggested": False}]}
        section = "\n".join(overview.candidates_section(cands, heading="###"))
        self.assertIn(section, sync.models_body("", "{}", cands))
        self.assertNotIn("No static-list route was scanned", sync.models_body("", "{}", cands))
        self.assertEqual("\n".join(overview.candidates_section(cands)).replace("## ", "### ", 1), section)


class InjectedApprovedIds(unittest.TestCase):
    RULES = dict(sync.load_rules(), free_ids={"zai": ["glm-4.7-flash"]})

    def build(self):
        http = Http(lists={"https://zai/v1/models": {"data": [{"id": "glm-5"}]}})
        return sync.build({"ZAI_API_BASE": "https://zai/v1", "ZAI_API_KEY": "k"}, http, {}, NOW, rules=self.RULES, max_probes=0)

    def deployed(self, cat):
        return [d["model_info"]["id"] for d in sync.plan_groups(cat, self.RULES) if "glm-4.7-flash" in d["model_info"]["id"]]

    def test_an_unlisted_id_is_not_deployed_until_a_probe_found_it_usable(self):
        cat, _ = self.build()  # no probe ran: pending
        meta = next(m["metadata"] for m in cat["providers"]["zai"]["models"] if m["id"].endswith("flash"))
        self.assertEqual((meta["free"], meta["status"]), (True, "pending"))
        self.assertEqual(self.deployed(cat), [])
        for status in ("blocked", "unknown", "error"):
            meta["status"] = status
            self.assertEqual(self.deployed(cat), [], status)
        meta["status"] = "usable"
        self.assertTrue(self.deployed(cat))

    def test_a_probed_usable_injected_id_deploys(self):
        http = Http(lists={"https://zai/v1/models": {"data": [{"id": "glm-5"}]}})
        cat, _ = sync.build({"ZAI_API_BASE": "https://zai/v1", "ZAI_API_KEY": "k"}, http, {}, NOW, rules=self.RULES)
        self.assertIn("glm-4.7-flash", "".join(http.probed_ids()))
        self.assertTrue(self.deployed(cat))


class RulesValidation(unittest.TestCase):
    def load(self, doc):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.json")
            with open(path, "w") as handle:
                json.dump(doc, handle)
            return sync.load_rules(path)

    def test_a_free_by_typo_or_a_non_object_is_a_clean_error(self):
        for doc in ({"free_by": {"gorq": "plan"}}, {"free_by": ["groq"]}, {"free_by": {"groq": "free"}}):
            with self.assertRaises(RuntimeError):
                self.load(doc)
        self.assertEqual(self.load({"free_by": {"groq": "plan"}})["free_by"], {"groq": "plan"})


class SettingsEverywhere(unittest.TestCase):
    def test_a_missing_project_id_stops_listing_and_scan_too(self):
        entry = next(e for e in sync.ROUTES if e["route"] == "watsonx")
        env = {"WATSONX_API_BASE": "https://w", "WATSONX_API_KEY": "k"}
        for call in (lambda: sync.list_models(entry, env, Http(lists={}), {}), lambda: sync.scan_candidates(entry, env, Http(lists={}), {})):
            with self.assertRaisesRegex(RuntimeError, "Vault field watsonx_project_id at secret/data/platform/litellm"):
                call()

    def test_cohere_has_its_own_list_base_name(self):
        entry = next(e for e in sync.ROUTES if e["route"] == "cohere")
        self.assertEqual(entry["base_env"], "COHERE_LIST_BASE")
        self.assertEqual(sync.missing_setting(entry, {}), "COHERE_LIST_BASE is not set; write the Vault field cohere_api_base at secret/data/platform/litellm")
