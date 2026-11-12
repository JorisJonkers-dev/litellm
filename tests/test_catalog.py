"""The catalogue sync: listing, probing at the provider, classification, caching, registration, publishing."""
import base64
import contextlib
import datetime
import hashlib
import io
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
import overview  # noqa: E402

NOW = datetime.datetime(2026, 11, 10, 12, 0, tzinfo=datetime.timezone.utc)
ADMIN = "http://litellm:4000"
ENV = {
    "MISTRAL_API_BASE": "https://m/v1", "OPENCODE_GO_API_BASE": "https://oc/v1", "OPENROUTER_API_BASE": "https://or/v1",
    "LOCAL_LLM_API_BASE": "http://local/v1", "VIBE_API_KEY": "k-vibe", "VIBE_STUDY_API_KEY": "k-study",
    "OPENCODE_GO_API_KEY": "k-oc", "OPENROUTER_API_KEY": "k-or", "LOCAL_LLM_API_KEY": "none",
    "LITELLM_URL": ADMIN, "LITELLM_MASTER_KEY": "sk-master-secret",
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
    """A stub transport: provider lists, probe answers, the proxy's admin API and GitHub.

    `answers` maps (provider url, upstream model id) to (status, body), or to a list of those
    consumed in order, so a retried probe sees the next answer.
    """

    def __init__(self, answers=None, lists=None, info=None, new=None, delete=None):
        self.answers = answers or {}
        self.lists = LISTS if lists is None else lists
        self.info = info
        self.new = new or {}
        self.delete = delete or {}
        self.calls, self.puts = [], []
        self.added, self.deleted = [], []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if method == "POST" and url.endswith("/chat/completions"):
            answer = self.answers.get((url, body["model"]), (200, {"choices": [{"message": {"content": "SECRET-RESPONSE"}}]}))
            return answer.pop(0) if isinstance(answer, list) else answer
        if method == "POST" and url.endswith("/model/new"):
            self.added.append(body)
            return self.new.get(body["model_name"], (200, {"model_id": "x"}))
        if method == "POST" and url.endswith("/model/delete"):
            self.deleted.append(body["id"])
            return self.delete.get(body["id"], (200, {}))
        if method == "GET" and url.endswith("/model/info"):
            return self.info if self.info is not None else (404, "no")
        if method == "PUT":
            self.puts.append((url, body))
            return 201, {}
        if method == "GET" and url in self.lists:
            return (200, self.lists[url]) if self.lists[url] is not None else (500, "down")
        return 404, "no"

    def probed(self):
        """(provider url, upstream id) of every probe: the provider is asked, never the proxy."""
        return [(c[1], c[3]["model"]) for c in self.calls if c[0] == "POST" and c[1].endswith("/chat/completions")]

    def probed_ids(self):
        return [model for _, model in self.probed()]


def info(*entries):
    """A /model/info answer: (model_name, model_info) pairs."""
    return 200, {"data": [{"model_name": name, "model_info": meta} for name, meta in entries]}


def managed(name, route=None, id=None, **extra):
    """A model this catalogue registered earlier."""
    return (name, {"id": id or "catalog-" + name.replace("/", "-"), "managed_by": "litellm-catalog",
                   "db_model": True, "route": route or name.split("/", 1)[0], **extra})


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
        # the proxy is never asked to answer a probe, and never sees the master key here
        self.assertEqual([c for c in http.calls if c[1].startswith(ADMIN)], [])
        self.assertNotIn("sk-master-secret", json.dumps([c[2] for c in http.calls]))

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

    def test_probing_needs_no_proxy(self):
        http = Http()
        env = {k: v for k, v in ENV.items() if k not in ("LITELLM_URL", "LITELLM_MASTER_KEY")}
        cat, stats = sync.build(env, http, {}, NOW)
        self.assertGreater(stats["probed"], 0)
        self.assertEqual(cat["providers"]["local"]["models"][0]["metadata"]["status"], "usable")

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

    def test_admin_url_from_url_or_host_and_port(self):
        self.assertEqual(sync.admin_url(ENV), ADMIN)
        self.assertEqual(sync.admin_url({"LITELLM_HOST": "litellm.ns", "LITELLM_PORT": "4000"}), "http://litellm.ns:4000")
        self.assertEqual(sync.admin_url({"LITELLM_HOST": "litellm.ns"}), "http://litellm.ns:4000")
        self.assertEqual(sync.admin_url({}), "")

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


class Reconcile(unittest.TestCase):
    def catalogue(self, http=None):
        return sync.build(ENV, http or Http(), {}, NOW)[0]

    def test_adds_only_usable_models_with_env_names_and_both_auth_headers(self):
        blocked = ("https://oc/v1/chat/completions", "deepseek-v4-pro")
        cat = self.catalogue(Http(answers={blocked: (403, '{"error":{"message":"hosted outside Europe"}}')}))
        http = Http(info=info())
        result = sync.reconcile(cat, ENV, http)
        names = [body["model_name"] for body in http.added]
        self.assertIn("opencode-go/glm-5.3-flash", names)
        self.assertNotIn("opencode-go/deepseek-v4-pro", names)  # blocked
        self.assertNotIn("openrouter/c/d", names)  # unprobed
        self.assertNotIn("vibe/mistral-embed", names)  # not-chat
        self.assertEqual(result["added"], len(names))
        body = next(b for b in http.added if b["model_name"] == "opencode-go/glm-5.3-flash")
        self.assertEqual(body["litellm_params"], {"model": "openai/glm-5.3-flash",
                                                  "api_base": "os.environ/OPENCODE_GO_API_BASE",
                                                  "api_key": "os.environ/OPENCODE_GO_API_KEY"})
        self.assertEqual(body["model_info"]["managed_by"], "litellm-catalog")
        self.assertEqual(body["model_info"]["route"], "opencode-go")
        self.assertEqual(body["model_info"]["access_groups"], ["opencode-go"])
        self.assertIs(body["model_info"]["free"], False)
        self.assertEqual(body["model_info"]["id"], "catalog-opencode-go-" + hashlib.sha1(b"opencode-go/glm-5.3-flash").hexdigest()[:12])
        mistral = next(b for b in http.added if b["model_name"] == "vibe/mistral-large")
        self.assertEqual(mistral["litellm_params"]["model"], "mistral/mistral-large")
        # the values are env names, never the secrets
        text = json.dumps(http.added)
        for secret in ("k-vibe", "k-study", "k-oc", "k-or", "sk-master-secret"):
            self.assertNotIn(secret, text)
        # both key headers on every admin call
        admin = [c for c in http.calls if c[1].startswith(ADMIN)]
        self.assertTrue(admin)
        for call in admin:
            self.assertEqual(call[2]["Authorization"], "Bearer sk-master-secret")
            self.assertEqual(call[2]["x-litellm-api-key"], "Bearer sk-master-secret")
            self.assertEqual(call[2]["Content-Type"], "application/json")

    def test_never_touches_models_it_did_not_create(self):
        cat = self.catalogue()
        http = Http(info=info(
            ("vibe/*", {"id": "config-1", "db_model": False}),
            ("hand/made", {"id": "hand-1", "db_model": True}),
            managed("local/gone"),
        ))
        result = sync.reconcile(cat, ENV, http)
        self.assertEqual(http.deleted, ["catalog-local-gone"])
        self.assertEqual(result["removed"], 1)

    def test_a_route_missing_from_the_catalogue_is_not_evidence(self):
        cat = {"providers": {}}
        http = Http(info=info(managed("vibe/a"), managed("vibe/b")))
        result = sync.reconcile(cat, ENV, http)
        self.assertEqual(http.deleted, [])
        self.assertEqual(result["removed"], 0)

    def test_failing_probes_remove_nothing_on_a_route_where_nothing_is_usable(self):
        cat = {"providers": {"vibe": {"metadata": {}, "models": [
            {"id": "vibe/a", "metadata": {"status": "unknown", "failing_probes": 2}},
            {"id": "vibe/b", "metadata": {"status": "unknown", "failing_probes": 2}}]}}}
        http = Http(info=info(managed("vibe/a"), managed("vibe/b")))
        sync.reconcile(cat, ENV, http)
        self.assertEqual(http.deleted, [])
        cat["providers"]["vibe"]["models"].append({"id": "vibe/c", "metadata": {"status": "usable"}})
        http = Http(info=info(managed("vibe/a"), managed("vibe/b")))
        sync.reconcile(cat, ENV, http)
        self.assertEqual(len(http.deleted), 2)

    def test_respects_max_adds(self):
        cat = self.catalogue()
        http = Http(info=info())
        result = sync.reconcile(cat, ENV, http, max_adds=2)
        self.assertEqual([b["model_name"] for b in http.added], ["local/qwen3:32b", "opencode-go/deepseek-v4-pro"])
        self.assertEqual(result["added"], 2)

    def test_removal_guard_removes_nothing(self):
        cat = self.catalogue()
        http = Http(info=info(*[managed(f"local/gone{i}") for i in range(3)]))
        result = sync.reconcile(cat, ENV, http, max_removals=2)
        self.assertEqual(http.deleted, [])
        self.assertEqual(result["removed"], 0)
        self.assertIn("removal guard: 3 removals exceed 2", result["errors"])

    def test_a_stale_route_removes_nothing_and_keeps_its_published_flags(self):
        first = self.catalogue()
        sync.reconcile(first, ENV, Http(info=info()))
        flags = {m["id"]: m["metadata"]["registered"] for m in first["providers"]["local"]["models"]}
        lists = dict(LISTS, **{"http://local/v1/models": None})
        cat = sync.build(ENV, Http(lists=lists), first, NOW + datetime.timedelta(hours=1))[0]
        self.assertTrue(cat["providers"]["local"]["metadata"]["stale"])
        http = Http(info=info(managed("local/gone")))
        result = sync.reconcile(cat, ENV, http)
        self.assertEqual(http.deleted, [])
        self.assertEqual(result["removed"], 0)
        self.assertEqual({m["id"]: m["metadata"]["registered"] for m in cat["providers"]["local"]["models"]}, flags)

    def test_model_info_failure_changes_nothing(self):
        for answer in ((500, "down"), (200, {"nope": 1})):
            cat = self.catalogue()
            before = json.dumps(cat, sort_keys=True)
            http = Http(info=answer)
            result = sync.reconcile(cat, ENV, http)
            self.assertEqual(json.dumps(cat, sort_keys=True), before)
            self.assertEqual(http.added, [])
            self.assertEqual(http.deleted, [])
            self.assertIn("model/info", result["skipped_reason"])
            self.assertEqual(result["errors"], [])

    def test_a_model_that_failed_twice_is_removed_but_not_one_that_is_flapping(self):
        cat = self.catalogue()
        for provider in cat["providers"].values():
            for model in provider["models"]:
                if model["id"] == "opencode-go/glm-5.3-flash":
                    model["metadata"].update(status="blocked", failing_probes=2)
                if model["id"] == "opencode-go/deepseek-v4-pro":
                    model["metadata"].update(status="error", failing_probes=2)
                if model["id"] == "openrouter/c/d":
                    model["metadata"].update(status="unprobed", failing_probes=2)
        cat["providers"]["opencode-go"]["models"].append({"id": "opencode-go/alive", "metadata": {"status": "usable"}})  # the route itself works
        cat["providers"]["openrouter"]["models"].append({"id": "openrouter/alive", "metadata": {"status": "usable"}})
        http = Http(info=info(managed("opencode-go/glm-5.3-flash"), managed("opencode-go/deepseek-v4-pro"), managed("openrouter/c/d")))
        result = sync.reconcile(cat, ENV, http)
        self.assertEqual(http.deleted, ["catalog-opencode-go-glm-5.3-flash"])
        self.assertEqual(result["removed"], 1)

    def test_registered_flags_and_counts_come_from_the_final_managed_set(self):
        cat = self.catalogue()
        sync.reconcile(cat, ENV, Http(info=info(managed("vibe/mistral-large"))))
        vibe = {m["id"]: m["metadata"]["registered"] for m in cat["providers"]["vibe"]["models"]}
        self.assertEqual(vibe, {"vibe/mistral-large": True, "vibe/bare": True, "vibe/mistral-embed": False})
        self.assertEqual(cat["providers"]["vibe"]["metadata"]["registered_count"], 2)
        self.assertEqual(cat["providers"]["local"]["metadata"]["registered_count"], 1)

    def test_an_already_registered_answer_counts_as_registered(self):
        cat = self.catalogue()
        http = Http(info=info(), new={"local/qwen3:32b": (400, '{"error":"model_name already exists"}')})
        result = sync.reconcile(cat, ENV, http)
        self.assertEqual(result["errors"], [])
        self.assertTrue(cat["providers"]["local"]["models"][0]["metadata"]["registered"])

    def test_a_failed_add_is_an_error_and_not_registered(self):
        cat = self.catalogue()
        http = Http(info=info(), new={"local/qwen3:32b": (500, "boom " + "x " * 300)})
        result = sync.reconcile(cat, ENV, http)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(len(result["errors"][0].split(": ", 1)[1]), 200)
        self.assertFalse(cat["providers"]["local"]["models"][0]["metadata"]["registered"])

    def test_no_admin_access_skips(self):
        cat = self.catalogue()
        result = sync.reconcile(cat, {k: v for k, v in ENV.items() if k != "LITELLM_MASTER_KEY"}, Http())
        self.assertTrue(result["skipped_reason"])
        self.assertNotIn("registered", cat["providers"]["local"]["models"][0]["metadata"])


class Gh(Http):
    """GitHub's contents API over a dict of path -> text."""

    def __init__(self, files=None, **kw):
        super().__init__(**kw)
        self.files = dict(files or {})

    def __call__(self, method, url, headers, body):
        prefix = "https://api.github.com/repos/o/r/contents/"
        if url.startswith(prefix):
            path = url[len(prefix):]
            if method == "GET":
                if path not in self.files:
                    return 404, ""
                return 200, {"content": base64.b64encode(self.files[path].encode()).decode(), "sha": "sha-" + path}
            self.puts.append((path, body))
            self.files[path] = base64.b64decode(body["content"]).decode()
            return 201, {}
        return super().__call__(method, url, headers, body)


class Main(unittest.TestCase):
    def test_writes_a_local_file_and_reads_it_back_as_the_cache(self):
        env = {k: v for k, v in ENV.items() if k != "LITELLM_MASTER_KEY"}
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "c.json")
            self.assertEqual(sync.main(["--output", out], env, Http(), NOW), 0)
            http = Http()
            sync.main(["--output", out], env, http, NOW + datetime.timedelta(hours=1))
            self.assertEqual(http.probed(), [])
            self.assertEqual(json.load(open(out))["version"], 1)
            self.assertTrue(os.path.exists(os.path.join(tmp, "README.md")))

    def test_a_run_that_lists_nothing_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(sync.main(["--output", os.path.join(tmp, "c.json")], {}, Http(), NOW), 1)

    def test_publishes_the_catalogue_when_changed_and_not_when_unchanged(self):
        env = dict(ENV, CATALOG_REPO="o/r", CATALOG_TOKEN="t")
        env.pop("LITELLM_MASTER_KEY")
        cat, _ = sync.build(env, Http(), {}, NOW)
        gh = Gh({"model-catalog.json": json.dumps(cat)})
        sync.main([], env, gh, NOW + datetime.timedelta(hours=1))
        self.assertEqual([p for p, _ in gh.puts if p == "model-catalog.json"], [])
        gh = Gh({"model-catalog.json": json.dumps(cat)})
        sync.main([], env, gh, NOW + datetime.timedelta(hours=25))
        puts = [b for p, b in gh.puts if p == "model-catalog.json"]
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0]["sha"], "sha-model-catalog.json")
        self.assertEqual(puts[0]["message"], "chore: refresh model catalogue")

    def test_publishes_the_overview_only_when_it_changed_ignoring_the_stamp(self):
        env = dict(ENV, CATALOG_REPO="o/r", CATALOG_TOKEN="t")
        env.pop("LITELLM_MASTER_KEY")
        cat, _ = sync.build(env, Http(), {}, NOW)
        # a different run time alone is not a change
        gh = Gh({"model-catalog.json": json.dumps(cat), "README.md": overview.render(dict(cat, updated_at="1999-01-01T00:00:00Z"))})
        sync.main([], env, gh, NOW + datetime.timedelta(hours=1))
        self.assertEqual([p for p, _ in gh.puts if p == "README.md"], [])
        # a real change is
        changed = json.loads(json.dumps(cat))
        changed["providers"]["local"]["models"][0]["metadata"]["status"] = "blocked"
        gh = Gh({"model-catalog.json": json.dumps(cat), "README.md": overview.render(changed)})
        sync.main([], env, gh, NOW + datetime.timedelta(hours=1))
        puts = [b for p, b in gh.puts if p == "README.md"]
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0]["message"], "chore: refresh model overview")
        self.assertEqual(puts[0]["sha"], "sha-README.md")

    def test_a_reconcile_error_fails_the_run_but_still_publishes(self):
        env = dict(ENV, CATALOG_REPO="o/r", CATALOG_TOKEN="t")
        cat, _ = sync.build(env, Http(), {}, NOW)
        gh = Gh({"model-catalog.json": json.dumps(cat)}, info=(500, "down"))
        self.assertEqual(sync.main([], env, gh, NOW + datetime.timedelta(hours=25)), 1)
        self.assertEqual(len([p for p, _ in gh.puts if p == "model-catalog.json"]), 1)

    def test_prints_one_registration_summary_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                sync.main(["--output", os.path.join(tmp, "c.json")], ENV, Http(info=info()), NOW)
            self.assertIn("catalog: registered +", buf.getvalue())

    def test_no_key_reaches_a_published_file_or_a_reason(self):
        env = dict(ENV, CATALOG_REPO="o/r", CATALOG_TOKEN="t")
        gh = Gh(info=info())
        sync.main([], env, gh, NOW)
        published = json.dumps(gh.files)
        for secret in ("k-vibe", "k-study", "k-oc", "k-or", "sk-master-secret", "SECRET-RESPONSE"):
            self.assertNotIn(secret, published)
        cat, _ = sync.build(env, Http(), {}, NOW)
        result = sync.reconcile(cat, ENV, Http(info=info(), new={"local/qwen3:32b": (500, "bad key sk-master-secret")}))
        self.assertNotIn("sk-master-secret", json.dumps(result))

    def test_github_errors_surface(self):
        with self.assertRaisesRegex(RuntimeError, "reading"):
            sync.github_get("o/r", "p", "t", lambda *a: (500, "x"))
        with self.assertRaisesRegex(RuntimeError, "publishing"):
            sync.github_put("o/r", "p", "t", {}, None, lambda *a: (422, "x"))
        self.assertEqual(sync.github_get_text("o/r", "p", "t", lambda *a: (404, "")), (None, None))


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
