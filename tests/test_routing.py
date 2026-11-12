"""The canonical model groups: naming, the routing rules, and the deployments they plan."""
import datetime
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "catalog"))
import sync  # noqa: E402

NOW = datetime.datetime(2026, 11, 10, 12, 0, tzinfo=datetime.timezone.utc)
ADMIN = "http://litellm:4000"
ENV = {
    "MISTRAL_API_BASE": "https://m/v1", "OPENCODE_GO_API_BASE": "https://oc/v1", "OPENROUTER_API_BASE": "https://or/v1",
    "GOOGLE_API_BASE": "https://g/v1", "ANTHROPIC_API_BASE": "https://a/v1", "LOCAL_LLM_API_BASE": "http://local/v1",
    "VIBE_API_KEY": "k-vibe", "VIBE_STUDY_API_KEY": "k-study", "VIBE_API_KEY_PAID": "k-paid",
    "VIBE_STUDY_API_KEY_PAID": "k-study-paid", "OPENCODE_GO_API_KEY": "k-oc", "OPENROUTER_API_KEY": "k-or",
    "GOOGLE_API_KEY": "k-google", "ANTHROPIC_API_KEY": "k-anthropic", "LOCAL_LLM_API_KEY": "none",
    "LITELLM_URL": ADMIN, "LITELLM_MASTER_KEY": "sk-master-secret",
}
LISTS = {
    "https://m/v1/models": {"data": [
        {"id": "mistral-large-latest", "capabilities": {"completion_chat": True}},
        {"id": "mistral-embed", "capabilities": {"completion_chat": False}},
        {"id": "mistral-ocr-latest", "capabilities": {"ocr": True}},
        {"id": "voxtral-mini-transcribe", "capabilities": {"audio_transcription": True}},
        {"id": "mistral-moderation-latest", "capabilities": {"moderation": True}},
    ]},
    "https://oc/v1/models": {"data": [{"id": "glm-5.3"}]},
    "https://or/v1/models": {"data": [{"id": "z-ai/glm-4.6:free", "pricing": {"prompt": "0", "completion": "0"}}]},
    "https://g/v1/models": {"data": [
        {"id": "models/gemini-2.5-pro", "displayName": "Gemini 2.5 Pro", "inputTokenLimit": 1048576},
        {"id": "models/gemini-embedding-001", "inputTokenLimit": 2048},
    ]},
    "https://a/v1/models": {"data": [{"id": "claude-sonnet-4-5-20250929", "display_name": "Claude Sonnet 4.5"}]},
    "http://local/v1/models": {"data": [{"id": "qwen3:32b"}]},
}
# The rules a real catalog/routing.json holds, inline: the tests must not depend on that file.
RULES = {
    "tiers": [
        [["vibe", 1], ["vibe-study", 1]],
        [["vibe-api", 1], ["vibe-study-api", 1]],
        [["google", 1], ["anthropic", 1]],
        [["opencode-go", 1], ["openrouter", 1], ["local", 1]],
    ],
    "backup_only": {"opencode-go": ["google", "anthropic", "vibe", "vibe-study", "vibe-api", "vibe-study-api"]},
    "exclusive": [{"match": "^claude", "routes": ["anthropic"]}],
    "free_first_unless_served_by": ["vibe", "vibe-study", "vibe-api", "vibe-study-api", "google", "anthropic"],
    "aliases": {"mistral-large-2411": "mistral-large", "claude-sonnet-4-5-20250929": "claude-sonnet"},
    "api_only_routes": ["vibe-api", "vibe-study-api"],
    "budgets": {"vibe-api": {"max_budget": 20, "budget_duration": "30d"}},
    "limits": {"vibe-api": {"rpm": 60, "tpm": 100000}},
}


class Http:
    """A stub transport: provider lists, probe answers and the proxy's admin API."""

    def __init__(self, lists=None, info=None, new=None, delete=None):
        self.lists = LISTS if lists is None else lists
        self.info = info
        self.new = new or {}
        self.delete = delete or {}
        self.calls, self.added, self.deleted = [], [], []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if method == "POST" and url.endswith(("/chat/completions", "/messages")):
            return 200, {"choices": [{"message": {"content": "SECRET-RESPONSE"}}]}
        if method == "POST" and url.endswith("/model/new"):
            self.added.append(body)
            return self.new.get(body["model_name"], (200, {"model_id": "x"}))
        if method == "POST" and url.endswith("/model/delete"):
            self.deleted.append(body["id"])
            return self.delete.get(body["id"], (200, {}))
        if method == "GET" and url.endswith("/model/info"):
            return self.info if self.info is not None else (200, {"data": []})
        if method == "GET" and url in self.lists:
            return 200, self.lists[url]
        return 404, "no"


def entry(route):
    return next(e for e in sync.ROUTES if e["route"] == route)


def catalogue(*models):
    """A catalogue from (route, upstream id, status, metadata) tuples, one provider per route."""
    providers = {}
    for route, model_id, status, meta in models:
        provider = providers.setdefault(route, {"metadata": {"route": route}, "models": []})
        provider["models"].append({"id": f"{route}/{model_id}", "metadata": dict({"status": status}, **meta)})
    return {"providers": providers}


def info(*entries):
    """A /model/info answer: (model_name, model_info) pairs."""
    return 200, {"data": [{"model_name": name, "model_info": meta} for name, meta in entries]}


def managed(body):
    """The /model/info entry the proxy keeps for a model this catalogue added."""
    return (body["model_name"], dict(body["model_info"], db_model=True))


class Naming(unittest.TestCase):
    def test_canonical_names(self):
        cases = [
            ("vibe", "mistral-large-latest", "mistral-large"),
            ("vibe", "mistral-large-2411", "mistral-large"),
            ("vibe", "Mistral-Large", "mistral-large"),
            ("google", "models/gemini-2.5-pro", "gemini-2.5-pro"),
            ("openrouter", "mistralai/mistral-large", "mistral-large"),
            ("openrouter", "z-ai/glm-4.6:free", "glm-4.6"),
            ("openrouter", "deepseek/deepseek-chat-v3-0324:free", "deepseek-chat-v3"),
            ("anthropic", "claude-sonnet-4-5-20250929", "claude-sonnet"),
            ("opencode-go", "glm-5.3", "glm-5.3"),
            ("local", "qwen3:32b", "qwen3:32b"),
        ]
        for route, upstream_id, want in cases:
            self.assertEqual(sync.canonical_name(route, upstream_id, RULES), want, upstream_id)

    def test_an_alias_wins_on_the_raw_and_on_the_normalised_id(self):
        rules = {"aliases": {"glm-5.3": "glm", "mistral-large-2411": "mistral-large"}}
        self.assertEqual(sync.canonical_name("opencode-go", "glm-5.3", rules), "glm")
        self.assertEqual(sync.canonical_name("vibe", "mistral-large-2411", rules), "mistral-large")
        self.assertEqual(sync.canonical_name("vibe", "mistral-large", rules), "mistral-large")

    def test_two_spellings_are_never_merged_by_a_guess(self):
        self.assertNotEqual(sync.canonical_name("vibe", "mistral-large", RULES),
                            sync.canonical_name("vibe", "mistral-large-instruct", RULES))
        self.assertNotEqual(sync.canonical_name("opencode-go", "glm-5.3", RULES),
                            sync.canonical_name("opencode-go", "glm-5.3-flash", RULES))

    def test_loose_key(self):
        self.assertEqual(sync.loose_key("glm-5.3"), "glm53")
        self.assertEqual(sync.loose_key("zai-glm-5.3"), "glm53")
        self.assertEqual(sync.loose_key("zhipu-glm-5.3"), "glm53")
        self.assertEqual(sync.loose_key("claude-sonnet-4-5-20250929"), "claudesonnet45")
        self.assertEqual(sync.loose_key("claude-sonnet-4-5"), "claudesonnet45")
        self.assertEqual(sync.loose_key("glm-4.6-latest"), "glm46")


class Plan(unittest.TestCase):
    def test_mistral_uses_the_plan_tiers_and_never_a_free_route(self):
        cat = catalogue(
            ("vibe", "mistral-large-latest", "usable", {}),
            ("vibe-api", "mistral-large-2411", "usable", {}),
            ("opencode-go", "mistral-large", "usable", {}),
            ("openrouter", "mistralai/mistral-large", "unprobed", {}),
        )
        specs = sync.plan_groups(cat, RULES)
        self.assertEqual([(s["route"], s["order"], s["weight"]) for s in specs], [("vibe", 1, 1), ("vibe-api", 2, 1)])

    def test_a_non_chat_model_is_registered_only_on_an_api_only_route(self):
        cat = catalogue(
            ("vibe", "mistral-embed", "not-chat", {"modality": "embedding"}),
            ("vibe-api", "mistral-embed", "not-chat", {"modality": "embedding"}),
            ("vibe-api", "mistral-ocr-latest", "not-chat", {"modality": "ocr"}),
            ("vibe-api", "mistral-moderation-latest", "not-chat", {"modality": "moderation"}),
        )
        specs = sync.plan_groups(cat, RULES)
        self.assertEqual([(s["name"], s["route"], s["model_info"].get("mode")) for s in specs],
                         [("mistral-embed", "vibe-api", "embedding"), ("mistral-ocr", "vibe-api", "ocr")])

    def test_gemini_goes_to_google_and_opencode_go_is_only_a_backup(self):
        cat = catalogue(("google", "gemini-2.5-pro", "usable", {}), ("opencode-go", "gemini-2.5-pro", "usable", {}))
        self.assertEqual([(s["route"], s["order"]) for s in sync.plan_groups(cat, RULES)], [("google", 1)])
        # with no google deployment the backup route serves it, at the first order of its own tiers
        cat = catalogue(("opencode-go", "gemini-2.5-pro", "usable", {}))
        self.assertEqual([(s["route"], s["order"]) for s in sync.plan_groups(cat, RULES)], [("opencode-go", 1)])

    def test_an_exclusive_model_is_only_on_its_route_and_never_free(self):
        cat = catalogue(
            ("anthropic", "claude-sonnet-4-5-20250929", "usable", {}),
            ("openrouter", "anthropic/claude-sonnet:free", "usable", {"free": True}),
            ("vibe", "claude-sonnet-4-5", "usable", {}),
        )
        specs = sync.plan_groups(cat, RULES)
        self.assertEqual([(s["route"], s["order"], s["model_info"]["free"]) for s in specs], [("anthropic", 1, False)])

    def test_a_free_deployment_comes_first_when_no_plan_route_serves_the_model(self):
        cat = catalogue(
            ("openrouter", "z-ai/glm-4.6:free", "usable", {"free": True}),
            ("local", "glm-4.6", "usable", {"free": True}),
        )
        self.assertEqual([(s["route"], s["order"], s["weight"]) for s in sync.plan_groups(cat, RULES)],
                         [("local", 1, 1), ("openrouter", 1, 1)])

    def test_a_free_deployment_comes_after_the_last_tier_when_the_plan_serves_the_model(self):
        cat = catalogue(
            ("vibe", "mistral-large", "usable", {}),
            ("openrouter", "mistralai/mistral-large:free", "usable", {"free": True}),
        )
        self.assertEqual([(s["route"], s["order"]) for s in sync.plan_groups(cat, RULES)], [("openrouter", 2), ("vibe", 1)])

    def test_order_and_weight_come_from_the_tier(self):
        rules = dict(RULES, tiers=[[["vibe", 3], ["vibe-study", 1]], [["vibe-api", 2]]])
        cat = catalogue(("vibe", "devstral", "usable", {}), ("vibe-study", "devstral", "usable", {}),
                        ("vibe-api", "devstral", "usable", {}))
        self.assertEqual([(s["route"], s["order"], s["weight"]) for s in sync.plan_groups(cat, rules)],
                         [("vibe", 1, 3), ("vibe-api", 2, 2), ("vibe-study", 1, 1)])

    def test_a_route_no_tier_names_comes_after_every_tier(self):
        rules = {"tiers": [[["vibe", 1]]]}
        cat = catalogue(("vibe", "devstral", "usable", {}), ("opencode-go", "devstral", "usable", {}))
        self.assertEqual([(s["route"], s["order"]) for s in sync.plan_groups(cat, rules)], [("opencode-go", 2), ("vibe", 1)])

    def test_budgets_and_limits_are_copied_from_the_rules(self):
        cat = catalogue(("vibe-api", "mistral-large", "usable", {}))
        spec = sync.plan_groups(cat, RULES)[0]
        self.assertEqual(spec["litellm_params"], {
            "model": "mistral/mistral-large",
            "api_base": "os.environ/MISTRAL_API_BASE",
            "api_key": "os.environ/VIBE_API_KEY_PAID",
            "order": 1,
            "weight": 1,
            "rpm": 60,
            "tpm": 100000,
            "max_budget": 20,
            "budget_duration": "30d",
        })
        self.assertEqual(spec["model_info"]["id"],
                         "catalog-c-" + hashlib.sha1(b"mistral-large|vibe-api|mistral-large").hexdigest()[:12])
        self.assertEqual(spec["model_info"]["access_groups"], ["canonical"])
        self.assertEqual(spec["model_info"]["modality"], "chat")
        self.assertNotIn("mode", spec["model_info"])

    def test_google_and_anthropic_deployments_carry_no_api_base(self):
        cat = catalogue(("google", "gemini-2.5-pro", "usable", {}), ("anthropic", "claude-sonnet", "usable", {}))
        specs = {s["route"]: s for s in sync.plan_groups(cat, RULES)}
        self.assertEqual(specs["google"]["litellm_params"],
                         {"model": "gemini/gemini-2.5-pro", "api_key": "os.environ/GOOGLE_API_KEY", "order": 1, "weight": 1})
        self.assertEqual(specs["anthropic"]["litellm_params"],
                         {"model": "anthropic/claude-sonnet", "api_key": "os.environ/ANTHROPIC_API_KEY", "order": 1, "weight": 1})

    def test_two_ids_of_one_route_keep_the_shortest(self):
        cat = catalogue(("vibe", "mistral-large-latest", "usable", {}), ("vibe", "mistral-large-2411", "usable", {}))
        self.assertEqual([s["upstream_id"] for s in sync.plan_groups(cat, RULES)], ["mistral-large-2411"])

    def test_an_absent_model_is_not_planned(self):
        cat = catalogue(("vibe", "mistral-large", "usable", {"absent_runs": 1}))
        self.assertEqual(sync.plan_groups(cat, RULES), [])

    def test_near_duplicates_are_reported_and_never_merged(self):
        cat = catalogue(
            ("vibe", "glm-5.3", "usable", {}),
            ("opencode-go", "glm-5.3-flash", "usable", {}),
            ("vibe", "mistral-large", "usable", {}),
            ("vibe-api", "mistral-large", "usable", {}),
        )
        self.assertEqual(sync.near_duplicates(cat, RULES), [])
        cat = catalogue(("vibe", "glm-5.3", "usable", {}), ("opencode-go", "zai-glm-5.3", "usable", {}))
        self.assertEqual(sync.near_duplicates(cat, RULES), [["opencode-go/zai-glm-5.3", "vibe/glm-5.3"]])
        self.assertEqual(sorted({s["name"] for s in sync.plan_groups(cat, RULES)}), ["glm-5.3", "zai-glm-5.3"])


class Routes(unittest.TestCase):
    def test_google_lists_without_the_models_prefix_and_probes_chat(self):
        http = Http()
        found = {m["id"]: m for m in sync.list_models(entry("google"), ENV, http)}
        self.assertEqual(sorted(found), ["gemini-2.5-pro", "gemini-embedding-001"])
        self.assertEqual(found["gemini-2.5-pro"]["name"], "Gemini 2.5 Pro")
        self.assertEqual(found["gemini-2.5-pro"]["context_length"], 1048576)
        self.assertEqual(found["gemini-2.5-pro"]["modality"], "chat")
        self.assertEqual(found["gemini-embedding-001"]["modality"], "embedding")
        self.assertIs(found["gemini-embedding-001"]["chat"], False)
        self.assertEqual(http.calls[0][2], {"Authorization": "Bearer k-google"})
        self.assertEqual(sync.probe(entry("google"), "gemini-2.5-pro", ENV, http)[0], "usable")
        call = http.calls[-1]
        self.assertEqual(call[1], "https://g/v1/chat/completions")
        self.assertEqual(call[3]["model"], "gemini-2.5-pro")
        self.assertEqual(call[2]["Authorization"], "Bearer k-google")

    def test_anthropic_lists_and_probes_with_its_own_headers(self):
        http = Http()
        found = sync.list_models(entry("anthropic"), ENV, http)
        self.assertEqual([m["id"] for m in found], ["claude-sonnet-4-5-20250929"])
        self.assertEqual(found[0]["name"], "Claude Sonnet 4.5")
        self.assertEqual(http.calls[0][2], {"anthropic-version": "2023-06-01", "x-api-key": "k-anthropic"})
        self.assertEqual(sync.probe(entry("anthropic"), "claude-sonnet-4-5-20250929", ENV, http)[0], "usable")
        call = http.calls[-1]
        self.assertEqual(call[1], "https://a/v1/messages")
        self.assertEqual(call[3], {"model": "claude-sonnet-4-5-20250929", "max_tokens": 16,
                                   "messages": [{"role": "user", "content": "ping"}]})
        self.assertEqual(call[2]["x-api-key"], "k-anthropic")
        self.assertEqual(call[2]["anthropic-version"], "2023-06-01")
        self.assertNotIn("Authorization", call[2])

    def test_no_secret_reaches_a_request_body(self):
        http = Http()
        for route in ("google", "anthropic"):
            sync.list_models(entry(route), ENV, http)
            sync.probe(entry(route), "x", ENV, http)
        bodies = json.dumps([c[3] for c in http.calls])
        for secret in ("k-google", "k-anthropic", "k-vibe", "k-paid", "sk-master-secret"):
            self.assertNotIn(secret, bodies)

    def test_a_route_whose_key_is_unset_is_skipped_silently(self):
        env = {k: v for k, v in ENV.items() if k != "GOOGLE_API_KEY"}
        http = Http()
        cat, stats = sync.build(env, http, {}, NOW)
        self.assertEqual(stats["route_errors"], [])
        self.assertEqual(cat["providers"]["google"]["models"], [])
        self.assertNotIn("https://g/v1/models", [c[1] for c in http.calls])
        # its last entries stay, stale, until a run can list it again
        previous = {"providers": {"google": {"metadata": {"route": "google"},
                                             "models": [{"id": "google/gemini-2.5-pro", "metadata": {"status": "usable"}}]}}}
        cat, stats = sync.build(env, http, previous, NOW)
        self.assertTrue(cat["providers"]["google"]["metadata"]["stale"])
        self.assertEqual([m["id"] for m in cat["providers"]["google"]["models"]], ["google/gemini-2.5-pro"])
        self.assertEqual(stats["route_errors"], [])

    def test_modality_is_recorded_for_every_listed_model(self):
        cat, _ = sync.build(ENV, Http(), {}, NOW)
        meta = {m["id"]: m["metadata"] for p in cat["providers"].values() for m in p["models"]}
        self.assertEqual(meta["vibe/mistral-large-latest"]["modality"], "chat")
        self.assertEqual(meta["vibe/mistral-embed"]["modality"], "embedding")
        self.assertEqual(meta["vibe/mistral-ocr-latest"]["modality"], "ocr")
        self.assertEqual(meta["vibe/voxtral-mini-transcribe"]["modality"], "transcription")
        self.assertEqual(meta["vibe/mistral-moderation-latest"]["modality"], "moderation")
        self.assertEqual(meta["google/gemini-embedding-001"]["modality"], "embedding")
        self.assertEqual(meta["opencode-go/glm-5.3"]["modality"], "chat")
        self.assertEqual(meta["vibe/mistral-embed"]["status"], "not-chat")
        self.assertEqual(meta["vibe/mistral-ocr-latest"]["status"], "not-chat")


class Reconcile(unittest.TestCase):
    def test_the_sync_registers_canonical_groups(self):
        cat, _ = sync.build(ENV, Http(), {}, NOW)
        http = Http(info=info())
        sync.reconcile(cat, ENV, http, rules=RULES)
        names = {b["model_name"] for b in http.added}
        for name in ("mistral-large", "gemini-2.5-pro", "claude-sonnet", "glm-4.6", "glm-5.3"):
            self.assertIn(name, names)
        self.assertNotIn("mistral-moderation", names)
        embed = [b for b in http.added if b["model_name"] == "mistral-embed"]
        self.assertEqual([b["model_info"]["route"] for b in embed], ["vibe-api", "vibe-study-api"])
        self.assertEqual(embed[0]["model_info"]["mode"], "embedding")
        self.assertEqual(embed[0]["litellm_params"]["model"], "mistral/mistral-embed")
        # the values are env names, never the secrets
        bodies = json.dumps([c[3] for c in http.calls])
        for secret in ("k-vibe", "k-paid", "k-google", "k-anthropic", "sk-master-secret"):
            self.assertNotIn(secret, bodies)

    def test_a_changed_rule_replaces_the_deployment(self):
        cat = catalogue(("vibe", "mistral-large", "usable", {}))
        http = Http(info=info())
        sync.reconcile(cat, ENV, http, rules=RULES)
        body = next(b for b in http.added if b["model_name"] == "mistral-large")
        self.assertEqual(body["litellm_params"]["order"], 1)
        self.assertEqual(body["litellm_params"]["weight"], 1)
        # the same rules again: nothing to replace
        http = Http(info=info(managed(body)))
        result = sync.reconcile(cat, ENV, http, rules=RULES)
        self.assertEqual((result["replaced"], result["removed"]), (0, 0))
        self.assertEqual(http.deleted, [])
        # a changed weight: delete, then add
        http = Http(info=info(managed(body)))
        result = sync.reconcile(cat, ENV, http, rules=dict(RULES, tiers=[[["vibe", 2]]]))
        self.assertEqual((result["replaced"], result["removed"]), (1, 0))
        self.assertEqual(http.deleted, [body["model_info"]["id"]])
        self.assertEqual(http.added[-1]["litellm_params"]["weight"], 2)

    def test_canonical_deployments_never_touch_a_model_it_did_not_create(self):
        cat = catalogue(("vibe", "mistral-large", "usable", {}))
        http = Http(info=info(("mistral-large", {"id": "hand-1", "db_model": True}),
                              ("vibe/*", {"id": "config-1", "db_model": False})))
        result = sync.reconcile(cat, ENV, http, rules=RULES)
        self.assertEqual(http.deleted, [])
        self.assertEqual(result["removed"], 0)
        self.assertEqual(len(http.added), 2)  # the explicit model and its canonical deployment

    def test_a_stale_route_keeps_its_canonical_deployments(self):
        cat = catalogue(("vibe", "mistral-large", "usable", {}))
        http = Http(info=info())
        sync.reconcile(cat, ENV, http, rules=RULES)
        body = next(b for b in http.added if b["model_name"] == "mistral-large")
        cat["providers"]["vibe"]["models"] = []
        cat["providers"]["vibe"]["metadata"]["stale"] = True
        http = Http(info=info(managed(body)))
        result = sync.reconcile(cat, ENV, http, rules=RULES)
        self.assertEqual(http.deleted, [])
        self.assertEqual(result["removed"], 0)
        # a live route that lost the model does remove it
        cat["providers"]["vibe"]["metadata"].pop("stale")
        http = Http(info=info(managed(body)))
        result = sync.reconcile(cat, ENV, http, rules=RULES)
        self.assertEqual(http.deleted, [body["model_info"]["id"]])
        self.assertEqual(result["removed"], 1)

    def test_a_deployment_goes_when_its_model_failed_twice(self):
        cat = catalogue(("vibe", "mistral-large", "usable", {}), ("vibe", "alive", "usable", {}))
        http = Http(info=info())
        sync.reconcile(cat, ENV, http, rules=RULES)
        body = next(b for b in http.added if b["model_name"] == "mistral-large")
        model = cat["providers"]["vibe"]["models"][0]
        model["metadata"].update(status="blocked", failing_probes=2)
        http = Http(info=info(managed(body)))
        result = sync.reconcile(cat, ENV, http, rules=RULES)
        self.assertEqual(http.deleted, [body["model_info"]["id"]])
        self.assertEqual(result["removed"], 1)

    def test_canonical_adds_count_toward_max_adds(self):
        cat = catalogue(("vibe", "mistral-large", "usable", {}), ("vibe", "devstral", "usable", {}))
        http = Http(info=info())
        result = sync.reconcile(cat, ENV, http, rules=RULES, max_adds=1)
        self.assertEqual(result["added"], 1)
        self.assertEqual(len(http.added), 1)

    def test_the_removal_guard_covers_canonical_deployments(self):
        cat = catalogue(("vibe", "mistral-large", "usable", {}))
        http = Http(info=info())
        sync.reconcile(cat, ENV, http, rules=RULES)
        body = next(b for b in http.added if b["model_name"] == "mistral-large")
        cat["providers"]["vibe"]["models"] = [{"id": "vibe/other", "metadata": {"status": "usable"}}]
        http = Http(info=info(managed(body)))
        result = sync.reconcile(cat, ENV, http, rules=RULES, max_removals=0)
        self.assertEqual(http.deleted, [])
        self.assertIn("removal guard", result["errors"][0])

    def test_the_catalogue_records_the_groups(self):
        cat = catalogue(("vibe", "mistral-large", "usable", {}), ("vibe-api", "mistral-large", "usable", {}))
        sync.reconcile(cat, ENV, Http(info=info()), rules=RULES)
        self.assertEqual(cat["canonical"], {"mistral-large": {
            "family": "mistral",
            "modality": "chat",
            "deployments": [
                {"route": "vibe", "order": 1, "weight": 1, "registered": True},
                {"route": "vibe-api", "order": 2, "weight": 1, "registered": True},
            ],
        }})


if __name__ == "__main__":
    unittest.main()
