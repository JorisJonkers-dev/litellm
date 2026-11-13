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


if __name__ == "__main__":
    unittest.main()
