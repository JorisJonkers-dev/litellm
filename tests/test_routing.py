"""The canonical model groups: naming, the routing rules, and the deployments they plan."""
import datetime
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "catalog"))
import sync  # noqa: E402

NOW = datetime.datetime(2026, 11, 10, 12, 0, tzinfo=datetime.timezone.utc)
ENV = {
    "MISTRAL_API_BASE": "https://m/v1", "OPENCODE_GO_API_BASE": "https://oc/v1", "OPENROUTER_API_BASE": "https://or/v1",
    "GOOGLE_API_BASE": "https://g/v1", "ANTHROPIC_API_BASE": "https://a/v1", "LOCAL_LLM_API_BASE": "http://local/v1",
    "VIBE_API_KEY": "k-vibe", "VIBE_STUDY_API_KEY": "k-study",
    "VIBE_API_GENERAL_KEY": "k-general", "VIBE_STUDY_API_GENERAL_KEY": "k-study-general",
    "OPENCODE_GO_API_KEY": "k-oc", "OPENROUTER_API_KEY": "k-or",
    "GEMINI_API_KEY": "k-gemini", "ANTHROPIC_API_KEY": "k-anthropic", "LOCAL_LLM_API_KEY": "none",
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
        [["local", 1]],
        [["google", 1], ["anthropic", 1]],
        [["vibe", 2], ["vibe-study", 1]],
        [["vibe-api", 2], ["vibe-study-api", 1]],
        [["opencode-go", 1]],
    ],
    "backup_only": {"opencode-go": ["vibe", "vibe-study", "vibe-api", "vibe-study-api", "anthropic"]},
    "exclusive": [{"match": "^claude", "routes": ["anthropic"]}],
    "free_first_unless_served_by": ["vibe", "vibe-study", "vibe-api", "vibe-study-api", "anthropic"],
    "aliases": {"claude-sonnet-4-5-20250929": "claude-sonnet-4-5"},
    "api_only_routes": ["vibe-api", "vibe-study-api"],
    "budgets": {"vibe-api": {"max_budget": 50, "budget_duration": "30d"}},
    "cooldowns": {"vibe": 5, "vibe-study": 5, "vibe-api": 5, "vibe-study-api": 5,
                  "google": 120, "anthropic": 5, "opencode-go": 5, "openrouter": 5, "local": 5},
    "public_routes": ["google"],
}


class Http:
    """A stub transport: provider lists and probe answers."""

    def __init__(self, lists=None):
        self.lists = LISTS if lists is None else lists
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if method == "POST" and url.endswith(("/chat/completions", "/messages")):
            return 200, {"choices": [{"message": {"content": "SECRET-RESPONSE"}}]}
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
            ("anthropic", "claude-sonnet-4-5-20250929", "claude-sonnet-4-5"),
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

    def test_a_route_that_names_the_model_it_serves_is_normalised_from_that_name(self):
        self.assertEqual(sync.canonical_name("opencode-go", "glm-5.3-flash", RULES, "zhipuai/glm-5.3"), "glm-5.3")
        self.assertEqual(sync.canonical_name("opencode-go", "glm-5.3-flash", RULES), "glm-5.3-flash")
        # the alias table is consulted with the name the route gives, after normalisation
        self.assertEqual(sync.canonical_name("opencode-go", "glm-5.3-flash", {"aliases": {"glm-5.3": "glm"}}, "zhipuai/glm-5.3"), "glm")

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
    def test_mistral_uses_the_plan_tiers_and_never_a_free_route_first(self):
        cat = catalogue(
            ("vibe", "mistral-large-latest", "usable", {}),
            ("vibe-api", "mistral-large-2411", "usable", {}),
            ("opencode-go", "mistral-large", "usable", {}),
            ("openrouter", "mistralai/mistral-large:free", "usable", {"free": True}),
        )
        specs = sync.plan_groups(cat, RULES)
        # vibe is tier 3 and vibe-api tier 4, renumbered from 1; opencode-go is a backup because
        # vibe serves the model; the free deployment comes after the last tier.
        self.assertEqual([(s["route"], s["order"], s["weight"]) for s in specs],
                         [("vibe", 1, 2), ("vibe-api", 2, 2), ("openrouter", 3, 1)])

    def test_a_vibe_served_non_mistral_model_uses_the_vibe_tiers(self):
        cat = catalogue(
            ("vibe", "glm-5.3", "usable", {}),
            ("vibe-study", "glm-5.3", "usable", {}),
            ("vibe-api", "glm-5.3", "usable", {}),
            ("vibe-study-api", "glm-5.3", "usable", {}),
            ("opencode-go", "glm-5.3", "usable", {}),
        )
        self.assertEqual([(s["route"], s["order"], s["weight"]) for s in sync.plan_groups(cat, RULES)],
                         [("vibe", 1, 2), ("vibe-study", 1, 1), ("vibe-api", 2, 2), ("vibe-study-api", 2, 1)])

    def test_a_model_only_opencode_go_serves_is_order_one(self):
        cat = catalogue(("opencode-go", "glm-5.3", "usable", {}))
        self.assertEqual([(s["route"], s["order"], s["weight"]) for s in sync.plan_groups(cat, RULES)],
                         [("opencode-go", 1, 1)])

    def test_a_canonical_id_puts_opencode_go_in_the_same_group(self):
        cat = catalogue(
            ("vibe", "glm-5.3", "usable", {}),
            ("opencode-go", "glm-5.3-flash", "usable", {"canonical_id": "zhipuai/glm-5.3"}),
        )
        specs = sync.plan_groups(cat, RULES)
        self.assertEqual([(s["name"], s["route"]) for s in specs], [("glm-5.3", "vibe")])

    def test_a_non_chat_model_is_deployed_only_on_an_api_only_route(self):
        cat = catalogue(
            ("vibe", "mistral-embed", "not-chat", {"modality": "embedding"}),
            ("vibe-api", "mistral-embed", "not-chat", {"modality": "embedding"}),
            ("vibe-api", "mistral-ocr-latest", "not-chat", {"modality": "ocr"}),
            ("vibe-api", "mistral-moderation-latest", "not-chat", {"modality": "moderation"}),
        )
        specs = sync.plan_groups(cat, RULES)
        self.assertEqual([(s["name"], s["route"], s["model_info"].get("mode")) for s in specs],
                         [("mistral-embed", "vibe-api", "embedding"), ("mistral-ocr", "vibe-api", "ocr")])

    def test_a_mode_conflict_gives_the_odd_one_its_own_group(self):
        cat = catalogue(
            ("vibe", "bge-m3", "usable", {"modality": "chat"}),
            ("vibe-api", "bge-m3", "not-chat", {"modality": "embedding"}),
        )
        specs = sync.plan_groups(cat, RULES)
        self.assertEqual([(s["name"], s["route"], s["model_info"].get("mode")) for s in specs],
                         [("bge-m3", "vibe", None), ("bge-m3:embedding", "vibe-api", "embedding")])

    def test_claude_is_only_on_anthropic_and_never_free(self):
        cat = catalogue(
            ("anthropic", "claude-sonnet-4-5-20250929", "usable", {}),
            ("openrouter", "anthropic/claude-sonnet-4-5:free", "usable", {"free": True}),
            ("vibe", "claude-sonnet-4-5", "usable", {}),
        )
        specs = sync.plan_groups(cat, RULES)
        self.assertEqual([(s["name"], s["route"], s["order"]) for s in specs], [("claude-sonnet-4-5", "anthropic", 1)])

    def test_openrouter_carries_free_ids_only_with_the_privacy_extra_body(self):
        cat = catalogue(
            ("openrouter", "z-ai/glm-4.6:free", "usable", {"free": True}),
            ("openrouter", "z-ai/glm-4.6", "usable", {"free": False}),
        )
        specs = sync.plan_groups(cat, RULES)
        self.assertEqual([(s["route"], s["upstream_id"]) for s in specs], [("openrouter", "z-ai/glm-4.6:free")])
        self.assertEqual(specs[0]["litellm_params"]["extra_body"],
                         {"provider": {"data_collection": "deny", "allow_fallbacks": False}})

    def test_local_is_a_normal_tier_and_not_a_free_deployment(self):
        rules = dict(RULES, tiers=[[["local", 2]], [["vibe", 1]]])
        cat = catalogue(("local", "glm-4.6", "usable", {"free": True}), ("vibe", "glm-4.6", "usable", {}))
        # local keeps its tier's weight: a free deployment would be order 1 at weight 1
        self.assertEqual([(s["route"], s["order"], s["weight"]) for s in sync.plan_groups(cat, rules)],
                         [("local", 1, 2), ("vibe", 2, 1)])

    def test_a_free_deployment_comes_first_when_no_plan_route_serves_the_model(self):
        cat = catalogue(
            ("openrouter", "z-ai/glm-4.6:free", "usable", {"free": True}),
            ("opencode-go", "glm-4.6", "usable", {}),
        )
        self.assertEqual([(s["route"], s["order"], s["weight"]) for s in sync.plan_groups(cat, RULES)],
                         [("openrouter", 1, 1), ("opencode-go", 2, 1)])

    def test_a_free_deployment_comes_after_the_last_tier_when_a_plan_route_serves_the_model(self):
        cat = catalogue(
            ("vibe", "mistral-large", "usable", {}),
            ("openrouter", "mistralai/mistral-large:free", "usable", {"free": True}),
        )
        self.assertEqual([(s["route"], s["order"]) for s in sync.plan_groups(cat, RULES)], [("vibe", 1), ("openrouter", 2)])

    def test_google_is_only_in_a_public_group(self):
        cat = catalogue(("google", "gemini-2.5-pro", "usable", {}), ("vibe", "gemini-2.5-pro", "usable", {}))
        specs = sync.plan_groups(cat, RULES)
        self.assertEqual([(s["name"], s["route"], s["order"], s["model_info"]["access_groups"]) for s in specs], [
            ("gemini-2.5-pro", "vibe", 1, ["canonical"]),
            ("public/gemini-2.5-pro", "google", 1, ["google-free-public"]),
            ("public/gemini-2.5-pro", "vibe", 2, ["google-free-public"]),
        ])
        public = {s["route"]: s for s in specs if s["name"].startswith("public/")}
        self.assertEqual(public["google"]["litellm_params"]["cooldown_time"], 120)
        self.assertNotIn("max_budget", public["google"]["litellm_params"])
        self.assertEqual(public["google"]["model_info"]["id"], "public/google/gemini-2.5-pro")
        self.assertEqual(public["vibe"]["model_info"]["id"], "public/vibe/gemini-2.5-pro")

    def test_a_model_only_google_serves_has_only_the_public_group(self):
        cat = catalogue(("google", "gemini-2.5-pro", "usable", {}))
        specs = sync.plan_groups(cat, RULES)
        self.assertEqual([(s["name"], s["route"], s["order"]) for s in specs], [("public/gemini-2.5-pro", "google", 1)])
        self.assertEqual(specs[0]["model_info"]["allowed_fails_policy"], {"RateLimitErrorAllowedFails": 0})

    def test_a_single_deployment_group_with_a_cooldown_cools_down(self):
        cat = catalogue(("vibe", "devstral", "usable", {}))
        spec = sync.plan_groups(cat, RULES)[0]
        self.assertEqual(spec["litellm_params"]["cooldown_time"], 5)
        self.assertEqual(spec["model_info"]["allowed_fails_policy"], {"RateLimitErrorAllowedFails": 0})
        # two deployments: the router falls back to the next order level instead
        cat = catalogue(("vibe", "devstral", "usable", {}), ("vibe-study", "devstral", "usable", {}))
        for spec in sync.plan_groups(cat, RULES):
            self.assertNotIn("allowed_fails_policy", spec["model_info"])
        # no cooldown in the rules: nothing to cool down
        self.assertNotIn("allowed_fails_policy", sync.plan_groups(cat, dict(RULES, cooldowns={}))[0]["model_info"])

    def test_a_route_policy_from_the_rules_is_added(self):
        rules = dict(RULES, policies={"anthropic": {"BadRequestErrorAllowedFails": 2}}, cooldowns={"anthropic": 300})
        cat = catalogue(("anthropic", "claude-sonnet-4-5", "usable", {}))
        plan = sync.plan_groups(cat, rules)
        self.assertEqual(plan[0]["model_info"]["allowed_fails_policy"], {"RateLimitErrorAllowedFails": 0, "BadRequestErrorAllowedFails": 2})
        self.assertEqual(plan[0]["litellm_params"]["cooldown_time"], 300)

    def test_budgets_and_cooldowns_are_copied_from_the_rules(self):
        cat = catalogue(("vibe-api", "mistral-large", "usable", {}))
        spec = sync.plan_groups(cat, RULES)[0]
        self.assertEqual(spec["litellm_params"], {
            "model": "mistral/mistral-large",
            "litellm_credential_name": "vibe-api",
            "order": 1,
            "weight": 2,
            "max_budget": 50,
            "budget_duration": "30d",
            "cooldown_time": 5,
        })
        self.assertEqual(spec["model_info"], {
            "id": "vibe-api/mistral-large",
            "route": "vibe-api",
            "canonical": "mistral-large",
            "access_groups": ["canonical"],
            "allowed_fails_policy": {"RateLimitErrorAllowedFails": 0},
        })

    def test_a_transient_state_keeps_the_deployment(self):
        cat = catalogue(("vibe", "mistral-large", "error", {"was_usable": True}))
        self.assertEqual([s["route"] for s in sync.plan_groups(cat, RULES)], ["vibe"])
        cat = catalogue(("vibe", "mistral-large", "blocked", {"was_usable": True, "failing_probes": 2}))
        self.assertEqual(sync.plan_groups(cat, RULES), [])

    def test_a_model_absent_once_keeps_its_deployment(self):
        cat = catalogue(("vibe", "mistral-large", "usable", {"absent_runs": 1}))
        self.assertEqual([s["route"] for s in sync.plan_groups(cat, RULES)], ["vibe"])
        cat = catalogue(("vibe", "mistral-large", "usable", {"absent_runs": 2}))
        self.assertEqual(sync.plan_groups(cat, RULES), [])

    def test_two_ids_of_one_route_keep_the_shortest(self):
        cat = catalogue(("vibe", "mistral-large-latest", "usable", {}), ("vibe", "mistral-large-2411", "usable", {}))
        self.assertEqual([s["upstream_id"] for s in sync.plan_groups(cat, RULES)], ["mistral-large-2411"])

    def test_every_deployment_id_is_unique(self):
        cat = catalogue(
            ("google", "gemini-2.5-pro", "usable", {}),
            ("vibe", "gemini-2.5-pro", "usable", {}),
            ("vibe", "mistral-large", "usable", {}),
            ("vibe-api", "mistral-large", "usable", {}),
        )
        ids = [s["model_info"]["id"] for s in sync.plan_groups(cat, RULES)]
        self.assertEqual(len(ids), len(set(ids)))

    def test_the_plan_is_deterministic(self):
        cat = catalogue(
            ("vibe", "mistral-large", "usable", {}),
            ("vibe-study", "mistral-large", "usable", {}),
            ("vibe-api", "mistral-large", "usable", {}),
            ("google", "gemini-2.5-pro", "usable", {}),
            ("vibe", "gemini-2.5-pro", "usable", {}),
            ("openrouter", "z-ai/glm-4.6:free", "usable", {"free": True}),
            ("opencode-go", "glm-4.6", "usable", {}),
        )
        first = sync.plan_groups(cat, RULES)
        second = sync.plan_groups(json.loads(json.dumps(cat)), RULES)
        self.assertEqual(json.dumps(first), json.dumps(second))
        keys = [(s["name"], s["order"], s["route"]) for s in first]
        self.assertEqual(keys, sorted(keys))

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
        self.assertEqual(http.calls[0][2], {"Authorization": "Bearer k-gemini"})
        self.assertEqual(sync.probe(entry("google"), "gemini-2.5-pro", ENV, http)[0], "usable")
        call = http.calls[-1]
        self.assertEqual(call[1], "https://g/v1/chat/completions")
        self.assertEqual(call[3]["model"], "gemini-2.5-pro")
        self.assertEqual(call[2]["Authorization"], "Bearer k-gemini")

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
        for secret in ("k-gemini", "k-anthropic", "k-vibe", "k-general", "k-study-general"):
            self.assertNotIn(secret, bodies)

    def test_a_route_whose_key_is_unset_is_skipped_silently(self):
        env = {k: v for k, v in ENV.items() if k != "GEMINI_API_KEY"}
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

    def test_the_sync_plans_the_whole_catalogue_without_a_proxy(self):
        cat, _ = sync.build(ENV, Http(), {}, NOW)
        specs = sync.plan_groups(cat, RULES)
        names = {s["name"] for s in specs}
        # Gemini is served only by Google's free tier, so it has only its public group.
        for name in ("mistral-large", "public/gemini-2.5-pro", "claude-sonnet-4-5", "glm-4.6", "glm-5.3"):
            self.assertIn(name, names)
        self.assertNotIn("gemini-2.5-pro", names)
        self.assertNotIn("mistral-moderation", names)
        embed = [s for s in specs if s["name"] == "mistral-embed"]
        self.assertEqual([s["route"] for s in embed], ["vibe-api", "vibe-study-api"])
        self.assertEqual(embed[0]["model_info"]["mode"], "embedding")
        self.assertEqual(embed[0]["litellm_params"]["model"], "mistral/mistral-embed")
        # the values are credential names, never the secrets
        bodies = json.dumps(specs)
        for secret in ("k-vibe", "k-general", "k-gemini", "k-anthropic"):
            self.assertNotIn(secret, bodies)


if __name__ == "__main__":
    unittest.main()
