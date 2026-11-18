"""The canonical model groups: naming, the routing rules, and the deployments they plan."""
import datetime
import json
import sys
import tempfile
import os
import unittest
from unittest import mock
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
        [["opencode-go", 1]],
        [["vibe-api", 2], ["vibe-study-api", 1]],
    ],
    "backup_only": {
        "opencode-go": ["vibe", "vibe-study", "anthropic"],
        "vibe-api": ["local", "google", "anthropic", "vibe", "vibe-study", "opencode-go"],
        "vibe-study-api": ["local", "google", "anthropic", "vibe", "vibe-study", "opencode-go"],
    },
    "exclusive": [{"match": "^claude", "routes": ["anthropic"]}],
    "aliases": {"claude-sonnet-4-5-20250929": "claude-sonnet-4-5"},
    "api_only_routes": ["vibe-api", "vibe-study-api"],
    "accounts": {"vibe-api": {"routes": ["vibe-api"], "max_budget": 50, "window": {"days": 30}}},
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


def per_route_plan(cat, rules):
    """The plan without the shared free/<canonical> groups: these tests are about the per-route groups.

    tests/test_free_groups.py covers the shared groups.
    """
    return [spec for spec in sync.plan_groups(cat, rules) if not spec["name"].startswith("free/")]


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
        specs = per_route_plan(cat, RULES)
        # vibe serves the model, so opencode-go and the general API key are backups and not in the
        # group; the free deployment is in its own group, never in the canonical one.
        self.assertEqual([(s["name"], s["route"], s["order"], s["weight"]) for s in specs], [
            ("mistral-large", "vibe", 1, 2),
            ("openrouter/mistral-large", "openrouter", 1, 1),
        ])

    def test_a_vibe_served_non_mistral_model_uses_the_vibe_tiers(self):
        cat = catalogue(
            ("vibe", "glm-5.3", "usable", {}),
            ("vibe-study", "glm-5.3", "usable", {}),
            ("vibe-api", "glm-5.3", "usable", {}),
            ("vibe-study-api", "glm-5.3", "usable", {}),
            ("opencode-go", "glm-5.3", "usable", {}),
        )
        self.assertEqual([(s["route"], s["order"], s["weight"]) for s in per_route_plan(cat, RULES)],
                         [("vibe", 1, 2), ("vibe-study", 1, 1)])

    def test_the_general_api_keys_serve_only_what_no_other_route_serves(self):
        # The general Mistral API keys are for the models only they carry (audio, OCR, embeddings and the
        # rest of the broad list): a model OpenCode Go or a plan serves never falls back onto them.
        go = catalogue(("opencode-go", "glm-5.3", "usable", {}), ("vibe-api", "glm-5.3", "usable", {}))
        self.assertEqual([(s["route"], s["order"]) for s in per_route_plan(go, RULES)], [("opencode-go", 1)])
        only = catalogue(("vibe-api", "magistral-medium", "usable", {}), ("vibe-study-api", "magistral-medium", "usable", {}))
        self.assertEqual([(s["route"], s["order"], s["weight"]) for s in per_route_plan(only, RULES)],
                         [("vibe-api", 1, 2), ("vibe-study-api", 1, 1)])

    def test_a_model_only_opencode_go_serves_is_order_one(self):
        cat = catalogue(("opencode-go", "glm-5.3", "usable", {}))
        self.assertEqual([(s["route"], s["order"], s["weight"]) for s in per_route_plan(cat, RULES)],
                         [("opencode-go", 1, 1)])

    def test_a_canonical_id_puts_opencode_go_in_the_same_group(self):
        cat = catalogue(
            ("vibe", "glm-5.3", "usable", {}),
            ("opencode-go", "glm-5.3-flash", "usable", {"canonical_id": "zhipuai/glm-5.3"}),
        )
        specs = per_route_plan(cat, RULES)
        self.assertEqual([(s["name"], s["route"]) for s in specs], [("glm-5.3", "vibe")])

    def test_a_non_chat_model_is_deployed_only_on_an_api_only_route(self):
        cat = catalogue(
            ("vibe", "mistral-embed", "not-chat", {"modality": "embedding"}),
            ("vibe-api", "mistral-embed", "not-chat", {"modality": "embedding"}),
            ("vibe-api", "mistral-ocr-latest", "not-chat", {"modality": "ocr"}),
            ("vibe-api", "mistral-moderation-latest", "not-chat", {"modality": "moderation"}),
        )
        specs = per_route_plan(cat, RULES)
        self.assertEqual([(s["name"], s["route"], s["model_info"].get("mode")) for s in specs],
                         [("mistral-embed", "vibe-api", "embedding"), ("mistral-ocr", "vibe-api", "ocr")])

    def test_a_mode_conflict_gives_the_odd_one_its_own_group(self):
        cat = catalogue(
            ("vibe", "bge-m3", "usable", {"modality": "chat"}),
            ("vibe-api", "bge-m3", "not-chat", {"modality": "embedding"}),
        )
        specs = per_route_plan(cat, RULES)
        self.assertEqual([(s["name"], s["route"], s["model_info"].get("mode")) for s in specs],
                         [("bge-m3", "vibe", None), ("bge-m3:embedding", "vibe-api", "embedding")])

    def test_claude_is_only_on_anthropic_and_never_free(self):
        cat = catalogue(
            ("anthropic", "claude-sonnet-4-5-20250929", "usable", {}),
            ("openrouter", "anthropic/claude-sonnet-4-5:free", "usable", {"free": True}),
            ("vibe", "claude-sonnet-4-5", "usable", {}),
        )
        specs = per_route_plan(cat, RULES)
        self.assertEqual([(s["name"], s["route"], s["order"]) for s in specs], [("claude-sonnet-4-5", "anthropic", 1)])

    def test_claude_is_gated_behind_the_anthropic_access_group_never_canonical(self):
        cat = catalogue(("anthropic", "claude-sonnet-4-5-20250929", "usable", {}), ("vibe", "glm-4.6", "usable", {}))
        specs = per_route_plan(cat, RULES)
        self.assertEqual([(s["name"], s["route"], s["model_info"]["access_groups"]) for s in specs], [
            ("claude-sonnet-4-5", "anthropic", ["anthropic"]),
            ("glm-4.6", "vibe", ["canonical"]),
        ])

    def test_a_gated_route_leaves_a_model_another_route_serves(self):
        rules = dict(RULES, exclusive=[])
        cat = catalogue(("anthropic", "glm-4.6", "usable", {}), ("vibe", "glm-4.6", "usable", {}))
        specs = per_route_plan(cat, rules)
        self.assertEqual([(s["name"], s["route"], s["model_info"]["access_groups"]) for s in specs],
                         [("glm-4.6", "vibe", ["canonical"])])

    def test_the_gated_access_groups_are_their_own(self):
        self.assertEqual(sync.GATED_ACCESS_GROUPS, {"anthropic": "anthropic"})
        groups = set(sync.GATED_ACCESS_GROUPS.values())
        self.assertFalse(groups & {sync.CANONICAL_ACCESS_GROUP, sync.PUBLIC_ACCESS_GROUP})
        self.assertFalse(groups & set(sync.FREE_ACCESS_GROUPS.values()))

    def test_a_gated_group_is_recorded_with_its_access_group_and_not_as_public(self):
        cat = catalogue(("anthropic", "claude-sonnet-4-5", "usable", {}), ("vibe", "glm-4.6", "usable", {}))
        groups = sync.record_groups(per_route_plan(cat, RULES))
        self.assertEqual(groups["claude-sonnet-4-5"]["public"], False)
        self.assertEqual(groups["claude-sonnet-4-5"]["access_groups"], ["anthropic"])
        self.assertNotIn("access_groups", groups["glm-4.6"])

    def test_openrouter_carries_free_ids_only_with_the_privacy_extra_body(self):
        cat = catalogue(
            ("openrouter", "z-ai/glm-4.6:free", "usable", {"free": True}),
            ("openrouter", "z-ai/glm-4.6", "usable", {"free": False}),
        )
        specs = per_route_plan(cat, RULES)
        self.assertEqual([(s["route"], s["upstream_id"]) for s in specs], [("openrouter", "z-ai/glm-4.6:free")])
        # Public work only, so a free endpoint that trains on the prompt is allowed (owner, 2026-11-15):
        # most free endpoints exist only with training on. No fallback to a paid provider.
        self.assertEqual(specs[0]["litellm_params"]["extra_body"],
                         {"provider": {"data_collection": "allow", "allow_fallbacks": False}})

    def test_an_opencode_go_free_id_is_public_only_in_its_own_group(self):
        cat = catalogue(
            ("opencode-go", "beam-free", "usable", {"free": True, "canonical_id": "acme/beam-1.0"}),
            ("opencode-go", "glm-4.6", "usable", {}),
        )
        specs = per_route_plan(cat, RULES)
        self.assertEqual([(s["name"], s["route"], s["order"], s["model_info"]["access_groups"]) for s in specs], [
            ("glm-4.6", "opencode-go", 1, ["canonical"]),
            ("opencode-go-free/beam-1.0", "opencode-go", 1, ["opencode-go-free-public", "free"]),
        ])
        free = specs[1]
        self.assertNotIn("max_budget", free["litellm_params"])
        self.assertIn("cooldown_time", free["litellm_params"])
        self.assertEqual(free["litellm_params"]["extra_headers"], {"x-opencode-session": "litellm-proxy"})

    def test_an_opencode_go_free_id_never_joins_a_paid_group_of_the_same_model(self):
        cat = catalogue(
            ("opencode-go", "glm-4.6-free", "usable", {"free": True, "canonical_id": "z/glm-4.6"}),
            ("vibe", "glm-4.6", "usable", {}),
        )
        specs = per_route_plan(cat, RULES)
        self.assertEqual([(s["name"], s["route"], s["model_info"]["access_groups"]) for s in specs], [
            ("glm-4.6", "vibe", ["canonical"]),
            ("opencode-go-free/glm-4.6", "opencode-go", ["opencode-go-free-public", "free"]),
        ])

    def test_the_free_id_access_groups_are_their_own(self):
        self.assertEqual(sync.FREE_ID_ROUTES, {"opencode-go": "opencode-go-free-public"})
        groups = set(sync.FREE_ID_ROUTES.values())
        self.assertFalse(groups & {sync.CANONICAL_ACCESS_GROUP, sync.PUBLIC_ACCESS_GROUP})
        self.assertFalse(groups & set(sync.FREE_ACCESS_GROUPS.values()) | groups & set(sync.GATED_ACCESS_GROUPS.values()))

    def test_local_is_a_normal_tier_and_not_a_free_deployment(self):
        rules = dict(RULES, tiers=[[["local", 2]], [["vibe", 1]]])
        cat = catalogue(("local", "glm-4.6", "usable", {"free": True}), ("vibe", "glm-4.6", "usable", {}))
        # local keeps its tier's weight: a free deployment would be order 1 at weight 1
        self.assertEqual([(s["route"], s["order"], s["weight"]) for s in per_route_plan(cat, rules)],
                         [("local", 1, 2), ("vibe", 2, 1)])

    def test_a_free_route_is_public_only_and_never_in_canonical(self):
        cat = catalogue(
            ("openrouter", "z-ai/glm-4.6:free", "usable", {"free": True}),
            ("opencode-go", "glm-4.6", "usable", {}),
            ("google", "glm-4.6", "usable", {}),
        )
        specs = per_route_plan(cat, RULES)
        self.assertEqual([(s["name"], s["route"], s["order"], s["model_info"]["access_groups"]) for s in specs], [
            ("glm-4.6", "opencode-go", 1, ["canonical"]),
            ("google-free/glm-4.6", "google", 1, ["free"]),
            ("openrouter/glm-4.6", "openrouter", 1, ["openrouter-free-public", "free"]),
            # The Google public group keeps its paid fallbacks; the free routes are not among them.
            ("public/glm-4.6", "google", 1, ["google-free-public"]),
            ("public/glm-4.6", "opencode-go", 2, ["google-free-public"]),
        ])
        free = {s["route"]: s for s in specs if s["route"] in sync.FREE_ONLY}
        for spec in free.values():
            # Alone in its group: it cools down itself, and carries no budget.
            self.assertIn("cooldown_time", spec["litellm_params"])
            self.assertEqual(spec["model_info"]["allowed_fails_policy"], {"RateLimitErrorAllowedFails": 0})
            self.assertNotIn("max_budget", spec["litellm_params"])
        self.assertEqual(free["openrouter"]["model_info"]["id"], "openrouter/z-ai/glm-4.6:free")
        # No deployment of a free route is in canonical or in another route's group.
        for spec in specs:
            if spec["route"] in sync.FREE_ONLY:
                self.assertEqual(spec["model_info"]["access_groups"], [sync.FREE_ACCESS_GROUPS[spec["route"]], "free"])
                self.assertTrue(spec["name"].startswith(spec["route"] + "/"))

    def test_each_free_route_has_its_own_access_group(self):
        wave1 = {"groq", "cloudflare", "nvidia", "zai", "kilo", "ovh", "requesty", "axforge", "naga", "routeway", "inferbase"}
        wave2 = {"bazaarlink", "electronhub", "unorouter", "cohere", "airforce", "plugsky", "poe", "vercel", "watsonx", "novita",
                 "together", "typhoon", "sealion", "gonka", "longcat", "neosantara"}
        self.assertEqual(sync.FREE_ONLY, {"openrouter"} | wave1 | wave2)
        self.assertEqual(sync.FREE_ACCESS_GROUPS, {route: f"{route}-free-public" for route in sync.FREE_ONLY})
        groups = set(sync.FREE_ACCESS_GROUPS.values())
        self.assertEqual(len(groups), len(sync.FREE_ACCESS_GROUPS))
        self.assertNotIn(sync.CANONICAL_ACCESS_GROUP, groups)
        self.assertNotIn(sync.PUBLIC_ACCESS_GROUP, groups)

    def test_the_free_groups_are_recorded_as_public(self):
        cat = catalogue(("openrouter", "z-ai/glm-4.6:free", "usable", {"free": True}),
                        ("vibe", "glm-4.6", "usable", {}))
        groups = sync.record_groups(per_route_plan(cat, RULES))
        self.assertEqual({name: entry["public"] for name, entry in groups.items()},
                         {"glm-4.6": False, "openrouter/glm-4.6": True})

    def test_google_is_only_in_a_public_group(self):
        cat = catalogue(("google", "gemini-2.5-pro", "usable", {}), ("vibe", "gemini-2.5-pro", "usable", {}))
        specs = per_route_plan(cat, RULES)
        self.assertEqual([(s["name"], s["route"], s["order"], s["model_info"]["access_groups"]) for s in specs], [
            ("gemini-2.5-pro", "vibe", 1, ["canonical"]),
            ("google-free/gemini-2.5-pro", "google", 1, ["free"]),
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
        specs = per_route_plan(cat, RULES)
        self.assertEqual([(s["name"], s["route"], s["order"]) for s in specs],
                         [("google-free/gemini-2.5-pro", "google", 1), ("public/gemini-2.5-pro", "google", 1)])
        self.assertEqual(specs[1]["model_info"]["allowed_fails_policy"], {"RateLimitErrorAllowedFails": 0})

    def test_the_free_group_holds_every_free_model_alone_with_no_paid_fallback(self):
        cat = catalogue(
            ("google", "gemini-2.5-pro", "usable", {}),
            ("google", "gemini-embedding", "not-chat", {"modality": "embedding"}),
            ("vibe", "gemini-2.5-pro", "usable", {}),
            ("openrouter", "z-ai/glm-4.6:free", "usable", {"free": True}),
            ("openrouter", "z-ai/glm-5:free", "blocked", {"free": True}),
        )
        specs = per_route_plan(cat, RULES)
        free = [s for s in specs if sync.FREE_ACCESS_GROUP in s["model_info"]["access_groups"]]
        self.assertEqual([(s["name"], s["route"]) for s in free],
                         [("google-free/gemini-2.5-pro", "google"), ("openrouter/glm-4.6", "openrouter")])
        for spec in free:
            # Alone in its group, so a refusal is a failure; never a paid route, never canonical.
            self.assertEqual(sum(1 for other in specs if other["name"] == spec["name"]), 1)
            self.assertNotIn("canonical", spec["model_info"]["access_groups"])
            self.assertNotIn("max_budget", spec["litellm_params"])
        self.assertEqual(len({s["model_info"]["id"] for s in specs}), len(specs))
        groups = sync.record_groups(specs)
        self.assertTrue(groups["google-free/gemini-2.5-pro"]["public"])
        self.assertEqual(groups["google-free/gemini-2.5-pro"]["access_groups"], ["free"])
        self.assertEqual(groups["openrouter/glm-4.6"]["access_groups"], ["openrouter-free-public", "free"])
        self.assertNotIn("access_groups", groups["public/gemini-2.5-pro"])

    def test_a_single_deployment_group_with_a_cooldown_cools_down(self):
        cat = catalogue(("vibe", "devstral", "usable", {}))
        spec = per_route_plan(cat, RULES)[0]
        self.assertEqual(spec["litellm_params"]["cooldown_time"], 5)
        self.assertEqual(spec["model_info"]["allowed_fails_policy"], {"RateLimitErrorAllowedFails": 0})
        # two deployments: the router falls back to the next order level instead
        cat = catalogue(("vibe", "devstral", "usable", {}), ("vibe-study", "devstral", "usable", {}))
        for spec in per_route_plan(cat, RULES):
            self.assertNotIn("allowed_fails_policy", spec["model_info"])
        # no cooldown in the rules: nothing to cool down
        self.assertNotIn("allowed_fails_policy", per_route_plan(cat, dict(RULES, cooldowns={}))[0]["model_info"])

    def test_opencode_go_deployments_carry_the_session_header_and_others_do_not(self):
        cat = catalogue(("opencode-go", "glm-x", "usable", {}), ("anthropic", "claude-y", "usable", {}))
        plan = {d["route"]: d for d in per_route_plan(cat, RULES)}
        self.assertEqual(plan["opencode-go"]["litellm_params"]["extra_headers"], {"x-opencode-session": "litellm-proxy"})
        self.assertNotIn("extra_headers", plan["anthropic"]["litellm_params"])

    def test_a_missing_default_rules_file_and_a_non_object_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.json")
            open(path, "w").write("[1]")
            with self.assertRaises(RuntimeError):
                sync.load_rules(path)
            with mock.patch.dict(os.environ, {"CATALOG_RULES": os.path.join(tmp, "missing.json")}):
                with self.assertRaises(RuntimeError):
                    sync.load_rules()

    def test_a_route_policy_from_the_rules_is_added(self):
        rules = dict(RULES, policies={"anthropic": {"BadRequestErrorAllowedFails": 2}}, cooldowns={"anthropic": 300})
        cat = catalogue(("anthropic", "claude-sonnet-4-5", "usable", {}))
        plan = per_route_plan(cat, rules)
        self.assertEqual(plan[0]["model_info"]["allowed_fails_policy"], {"RateLimitErrorAllowedFails": 0, "BadRequestErrorAllowedFails": 2})
        self.assertEqual(plan[0]["litellm_params"]["cooldown_time"], 300)

    def test_budgets_and_cooldowns_are_copied_from_the_rules(self):
        cat = catalogue(("vibe-api", "mistral-large", "usable", {}))
        spec = per_route_plan(cat, RULES)[0]
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
            "id": "vibe-api:mistral-large",
            "route": "vibe-api",
            "canonical": "mistral-large",
            "access_groups": ["canonical"],
            "account": "vibe-api",
            "allowed_fails_policy": {"RateLimitErrorAllowedFails": 0},
        })

    def test_a_transient_state_keeps_the_deployment(self):
        cat = catalogue(("vibe", "mistral-large", "error", {"was_usable": True}))
        self.assertEqual([s["route"] for s in per_route_plan(cat, RULES)], ["vibe"])
        cat = catalogue(("vibe", "mistral-large", "blocked", {"was_usable": True, "failing_probes": 2}))
        self.assertEqual(per_route_plan(cat, RULES), [])

    def test_a_model_absent_once_keeps_its_deployment(self):
        cat = catalogue(("vibe", "mistral-large", "usable", {"absent_runs": 1}))
        self.assertEqual([s["route"] for s in per_route_plan(cat, RULES)], ["vibe"])
        cat = catalogue(("vibe", "mistral-large", "usable", {"absent_runs": 2}))
        self.assertEqual(per_route_plan(cat, RULES), [])

    def test_two_ids_of_one_route_keep_the_shortest(self):
        cat = catalogue(("vibe", "mistral-large-latest", "usable", {}), ("vibe", "mistral-large-2411", "usable", {}))
        self.assertEqual([s["upstream_id"] for s in per_route_plan(cat, RULES)], ["mistral-large-2411"])

    def test_every_deployment_id_is_unique(self):
        cat = catalogue(
            ("google", "gemini-2.5-pro", "usable", {}),
            ("vibe", "gemini-2.5-pro", "usable", {}),
            ("vibe", "mistral-large", "usable", {}),
            ("vibe-api", "mistral-large", "usable", {}),
        )
        ids = [s["model_info"]["id"] for s in per_route_plan(cat, RULES)]
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
        first = per_route_plan(cat, RULES)
        second = per_route_plan(json.loads(json.dumps(cat)), RULES)
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
        self.assertEqual(sorted({s["name"] for s in per_route_plan(cat, RULES)}), ["glm-5.3", "zai-glm-5.3"])


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
        specs = per_route_plan(cat, RULES)
        names = {s["name"] for s in specs}
        # Gemini is served only by Google's free tier, so it has only its public group.
        for name in ("mistral-large", "public/gemini-2.5-pro", "claude-sonnet-4-5", "openrouter/glm-4.6", "glm-5.3"):
            self.assertIn(name, names)
        self.assertNotIn("gemini-2.5-pro", names)
        # A free model is never in canonical.
        self.assertNotIn("glm-4.6", names)
        self.assertNotIn("mistral-moderation", names)
        embed = [s for s in specs if s["name"] == "mistral-embed"]
        self.assertEqual([s["route"] for s in embed], ["vibe-api", "vibe-study-api"])
        self.assertEqual(embed[0]["model_info"]["mode"], "embedding")
        self.assertEqual(embed[0]["litellm_params"]["model"], "mistral/mistral-embed")
        # the values are credential names, never the secrets
        bodies = json.dumps(specs)
        for secret in ("k-vibe", "k-general", "k-gemini", "k-anthropic"):
            self.assertNotIn(secret, bodies)


class FreeRoutesWave1(unittest.TestCase):
    """The first-wave free-only routes (agent-kit#916): public work only, never canonical, never paid."""

    ROUTES = ("groq", "cloudflare", "nvidia", "zai", "kilo", "ovh", "requesty", "axforge", "naga", "routeway", "inferbase")
    RULES = dict(RULES, accounts={"inferbase": {"routes": ["inferbase"], "max_budget": 0.5, "window": {"days": 1}}}, cooldowns={"naga": 3600})

    def plan(self, *models):
        return per_route_plan(catalogue(*models), self.RULES)
        return sync.plan_groups(catalogue(*models), self.RULES)

    def test_a_paid_id_is_never_deployed_on_a_free_route(self):
        models = [(route, "paid-model", "usable", {"free": False}) for route in self.ROUTES]
        self.assertEqual(self.plan(*models), [])
        # The free id beside it is deployed, and only that one.
        models += [(route, "free-model", "usable", {"free": True}) for route in self.ROUTES]
        specs = self.plan(*models)
        self.assertEqual({spec["upstream_id"] for spec in specs}, {"free-model"})
        self.assertEqual({spec["route"] for spec in specs}, set(self.ROUTES))

    def test_no_deployment_lands_in_canonical_or_a_paid_group(self):
        specs = self.plan(*[(route, "free-model", "usable", {"free": True}) for route in self.ROUTES],
                          ("vibe", "free-model", "usable", {}), ("vibe-api", "free-model", "usable", {}))
        for spec in specs:
            if spec["route"] in self.ROUTES:
                self.assertEqual(spec["model_info"]["access_groups"], [f"{spec['route']}-free-public", "free"])
                self.assertEqual(spec["name"], f"{spec['route']}/free-model")
                self.assertNotIn("canonical", spec["model_info"]["access_groups"])
            else:
                self.assertEqual(spec["model_info"]["access_groups"], ["canonical"])
        # Each free group holds one deployment: nothing paid to fall over to, and a 429 cools it down.
        names = [spec["name"] for spec in specs]
        for spec in specs:
            if spec["route"] in self.ROUTES:
                self.assertEqual(names.count(spec["name"]), 1)
                self.assertIn("cooldown_time", spec["litellm_params"])
                self.assertEqual(spec["model_info"]["allowed_fails_policy"], {"RateLimitErrorAllowedFails": 0})

    def test_free_deployments_cost_zero_per_token_except_inferbase_which_has_a_budget(self):
        specs = {s["route"]: s for s in self.plan(*[(route, "m", "usable", {"free": True}) for route in self.ROUTES])}
        for route, spec in specs.items():
            params = spec["litellm_params"]
            if route == "inferbase":
                self.assertNotIn("input_cost_per_token", params)
                self.assertEqual((params["max_budget"], params["budget_duration"]), (0.5, "1d"))
            else:
                self.assertEqual((params["input_cost_per_token"], params["output_cost_per_token"]), (0, 0))
                self.assertNotIn("max_budget", params)
        self.assertEqual(specs["naga"]["litellm_params"]["cooldown_time"], 3600)
        self.assertEqual(specs["groq"]["litellm_params"]["cooldown_time"], sync.PUBLIC_COOLDOWN)

    def test_prefixes_and_credentials(self):
        specs = {s["route"]: s for s in self.plan(*[(route, "m", "usable", {"free": True}) for route in self.ROUTES])}
        prefixes = {"groq": "groq", "cloudflare": "cloudflare", "nvidia": "nvidia_nim", "zai": "zai", "kilo": "openai",
                    "ovh": "ovhcloud", "requesty": "openai", "axforge": "openai", "naga": "openai", "routeway": "openai",
                    "inferbase": "openai"}
        for route, prefix in prefixes.items():
            self.assertEqual(specs[route]["litellm_params"]["model"], f"{prefix}/m")
            self.assertEqual(specs[route]["litellm_params"]["litellm_credential_name"], route)

    def test_every_route_has_limits_cooldowns_and_the_real_rules_agree(self):
        rules = sync.load_rules()
        for route in self.ROUTES:
            self.assertIn(route, sync.DEFAULT_ROUTE_LIMITS)
            self.assertIn(route, rules["cooldowns"])
        # Free routes are outside the tiers, so no rule makes one a fallback of a paid group.
        tiered = {route for tier in rules["tiers"] for route, _ in tier}
        self.assertFalse(tiered & set(self.ROUTES))
        self.assertEqual(sync.account_budgets(rules)["inferbase"], {"max_budget": 0.5, "budget_duration": "1d", "account": "inferbase"})
        for route in ("requesty", "naga", "routeway"):
            self.assertEqual(rules["cooldowns"][route], 3600)

    def test_the_real_static_free_lists_are_what_they_are(self):
        free_ids = sync.load_rules()["free_ids"]
        self.assertEqual(free_ids["groq"], ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"])
        self.assertEqual(free_ids["zai"], ["glm-4.7-flash", "glm-4.5-flash", "glm-4.6v-flash"])
        # Empty on purpose: the spec names no free Cloudflare or Inferbase id, and AxForge bills after 3M
        # tokens a month. Each deploys nothing until the owner fills its list.
        for route in ("cloudflare", "inferbase", "axforge"):
            self.assertEqual(free_ids[route], [], route)

    def test_probe_limits_stay_under_half_of_each_daily_cap_at_an_hourly_sync(self):
        project = (ROOT / "deploy" / "litellm.project.yml").read_text()
        self.assertIn('schedule: "17 * * * *"', project)  # hourly: 24 runs a day
        daily_caps = {"groq": 1000, "requesty": 200, "naga": 100, "routeway": 200, "inferbase": 100}
        for route, cap in daily_caps.items():
            self.assertLessEqual(sync.DEFAULT_ROUTE_LIMITS[route] * 24, cap / 2, route)
        # OVH allows 2 a minute per IP per model, and its probes are serial: never more than 2 a run.
        self.assertLessEqual(sync.DEFAULT_ROUTE_LIMITS["ovh"], 2)
        self.assertEqual(sync.DEFAULT_ROUTE_LIMITS["axforge"], 0)

    def test_inferbase_deploys_at_its_real_price_under_its_daily_budget(self):
        specs = self.plan(("inferbase", "m", "usable", {"free": True, "price": {"input": 5e-7, "output": 2e-6}}))
        params = specs[0]["litellm_params"]
        self.assertEqual((params["input_cost_per_token"], params["output_cost_per_token"]), (5e-7, 2e-6))
        self.assertEqual((params["max_budget"], params["budget_duration"]), (0.5, "1d"))

    def test_every_openai_prefixed_free_route_has_an_api_base(self):
        config = (ROOT / "config" / "config.yaml").read_text()
        openai_routes = [e["route"] for e in sync.ROUTES if e["route"] in sync.FREE_ONLY and sync.PREFIXES[e["kind"]] == "openai"]
        self.assertEqual(set(openai_routes), {"kilo", "requesty", "axforge", "naga", "routeway", "inferbase", "bazaarlink", "electronhub",
                                              "unorouter", "airforce", "plugsky", "typhoon", "sealion", "gonka", "longcat"})
        for route in openai_routes:
            block = config.split(f"- credential_name: {route}\n", 1)[1].split("credential_info", 1)[0]
            self.assertIn("api_base: os.environ/", block, route)
        specs = self.plan(*[(route, "m", "usable", {"free": True, "price": {"input": 0, "output": 0}}) for route in openai_routes])
        for spec in specs:
            self.assertEqual(spec["litellm_params"]["litellm_credential_name"], spec["route"])
            self.assertNotIn("api_base", spec["litellm_params"])  # the credential's base is the only one

    def test_the_axforge_skip_and_static_list_mechanisms_with_a_synthetic_list(self):
        env = dict(ENV, AXFORGE_API_BASE="https://ax/v1", GROQ_API_BASE="https://groq/v1", GROQ_API_KEY="k-groq")
        lists = dict(LISTS, **{"https://groq/v1/models": {"data": [{"id": "openai/gpt-oss-120b"}, {"id": "llama-paid"}]}})
        http = Http(lists)
        rules = dict(RULES, free_ids={"groq": ["openai/gpt-oss-120b"], "axforge": ["qwen3.8-27b"]})
        cat, stats = sync.build(env, http, {"providers": {}}, NOW, rules=rules)
        self.assertEqual(stats["route_errors"], [])
        self.assertEqual(cat["providers"]["axforge"]["models"], [])
        self.assertFalse([c for c in http.calls if "ax" in c[1].split("/")[2]])
        # With a key, the static list is its free signal.
        env["AXFORGE_API_KEY"] = "k-ax"
        lists["https://ax/v1/models"] = {"data": [{"id": "qwen3.8-27b"}, {"id": "other"}]}
        cat, stats = sync.build(env, Http(lists), {"providers": {}}, NOW, rules=rules)
        self.assertEqual({m["id"]: m["metadata"]["free"] for m in cat["providers"]["axforge"]["models"]},
                         {"axforge/qwen3.8-27b": True, "axforge/other": False})

    def test_cloudflare_base_is_composed_from_the_account_id(self):
        route = entry("cloudflare")
        self.assertEqual(sync.route_base(route, {"CLOUDFLARE_ACCOUNT_ID": "abc"}),
                         "https://api.cloudflare.com/client/v4/accounts/abc/ai/v1")
        self.assertEqual(sync.route_base(route, {}), "")
        self.assertEqual(sync.route_base(route, {"CLOUDFLARE_ACCOUNT_ID": "REPLACE_WITH_CLOUDFLARE_ACCOUNT_ID"}), "")
        self.assertEqual(sync.route_base(route, {"CLOUDFLARE_API_BASE": "https://cf/v1/"}), "https://cf/v1")


class FreeRoutesWave2(unittest.TestCase):
    """The second-wave free-only routes (litellm#64): public work only, never canonical, never paid."""

    ROUTES = ("bazaarlink", "electronhub", "unorouter", "cohere", "airforce", "plugsky", "poe", "vercel", "watsonx", "novita",
              "together", "typhoon", "sealion", "gonka", "longcat", "neosantara")
    PREFIXES = {"bazaarlink": "openai", "electronhub": "openai", "unorouter": "openai", "cohere": "cohere_chat", "airforce": "openai",
                "plugsky": "openai", "poe": "poe", "vercel": "vercel_ai_gateway", "watsonx": "watsonx", "novita": "novita",
                "together": "together_ai", "typhoon": "openai", "sealion": "openai", "gonka": "openai", "longcat": "openai",
                "neosantara": "neosantara"}

    def plan(self, *models):
        return sync.plan_groups(catalogue(*models), dict(RULES, cooldowns={"airforce": 3600}))

    def test_a_paid_id_is_never_deployed_and_a_free_one_is_in_its_own_group_plus_free(self):
        paid = [(route, "paid-model", "usable", {"free": False}) for route in self.ROUTES]
        self.assertEqual(self.plan(*paid), [])
        specs = self.plan(*paid, *[(route, "m", "usable", {"free": True}) for route in self.ROUTES], ("vibe", "m", "usable", {}))
        shared = [s for s in specs if s["name"].startswith("free/")]
        self.assertTrue(shared)
        for spec in shared:  # the shared free/<canonical> group: free copies only, the access group free alone
            self.assertEqual(spec["model_info"]["access_groups"], ["free"])
            self.assertEqual(spec["litellm_params"]["input_cost_per_token"], 0)
        for spec in [s for s in specs if not s["name"].startswith("free/")]:
            if spec["route"] in self.ROUTES:
                self.assertEqual(spec["model_info"]["access_groups"], [f"{spec['route']}-free-public", "free"])
                self.assertEqual(spec["name"], f"{spec['route']}/m")
                params = spec["litellm_params"]
                self.assertEqual((params["input_cost_per_token"], params["output_cost_per_token"]), (0, 0))
                self.assertNotIn("max_budget", params)
                self.assertEqual(params["model"], f"{self.PREFIXES[spec['route']]}/m")
                self.assertEqual(params["litellm_credential_name"], spec["route"])
            else:
                self.assertEqual(spec["model_info"]["access_groups"], ["canonical"])
        self.assertEqual({s["route"] for s in specs if not s["name"].startswith("free/")} - {"vibe"}, set(self.ROUTES))
        self.assertEqual(next(s for s in specs if s["name"] == "airforce/m")["litellm_params"]["cooldown_time"], 3600)

    def test_every_route_has_a_limit_a_cooldown_a_candidate_list_where_static_and_stays_out_of_the_tiers(self):
        rules = sync.load_rules()
        for route in self.ROUTES:
            self.assertIn(route, sync.DEFAULT_ROUTE_LIMITS)
            self.assertIn(route, rules["cooldowns"])
            kind = entry(route)["kind"]
            self.assertEqual(kind in sync.STATIC_KINDS, route in rules["free_ids"], route)
        self.assertFalse({route for tier in rules["tiers"] for route, _ in tier} & set(self.ROUTES))
        for route in ("bazaarlink", "cohere", "airforce", "plugsky"):
            self.assertEqual(rules["cooldowns"][route], 3600, route)
        self.assertEqual({r for r in self.ROUTES if entry(r).get("anonymous")}, {"unorouter"})

    def test_the_real_static_lists_name_only_what_the_spec_names(self):
        free_ids = sync.load_rules()["free_ids"]
        self.assertEqual(free_ids["together"], ["Prism-ML/Ternary-Bonsai-27B"])
        self.assertEqual(free_ids["typhoon"], ["typhoon-v2.5-30b-a3b-instruct"])
        self.assertEqual(free_ids["neosantara"], ["gemini-3.1-flash-lite"])
        # No free id is named for these: the scan lists what each provider offers and the owner approves.
        for route in ("bazaarlink", "cohere", "plugsky", "poe", "watsonx", "sealion", "gonka", "longcat"):
            self.assertEqual(free_ids[route], [], route)

    def test_probe_limits_stay_under_half_of_each_daily_cap_at_an_hourly_sync(self):
        daily = {"bazaarlink": 60, "airforce": 1000, "plugsky": 500}
        for route, cap in daily.items():
            self.assertLessEqual(sync.DEFAULT_ROUTE_LIMITS[route] * 24, cap / 2, route)
        # Cohere's cap is 1,000 calls a month: one probe per run is 720 a month, so it probes nothing.
        self.assertEqual(sync.DEFAULT_ROUTE_LIMITS["cohere"], 0)
        self.assertLessEqual(sync.DEFAULT_ROUTE_LIMITS["airforce"], 1)  # 1 request a minute

    def test_no_base_is_invented_where_the_spec_names_none(self):
        envs = [(ROOT / "deploy" / "env" / name / "base.env").read_text() for name in ("litellm", "litellm-catalog")]
        for text in envs:
            for base in ("COHERE_API_BASE", "WATSONX_API_BASE"):
                self.assertNotIn(base + "=", text)
            self.assertIn("BAZAARLINK_API_BASE=REPLACE_WITH_BAZAARLINK_API_BASE\n", text)
        route = entry("bazaarlink")
        self.assertEqual(sync.route_base(route, {}), "")
        self.assertEqual(sync.route_base(route, {"BAZAARLINK_API_BASE": "REPLACE_WITH_BAZAARLINK_API_BASE"}), "")
        with self.assertRaises(RuntimeError):
            sync.list_models(route, {"BAZAARLINK_API_KEY": "k"}, Http({}), {})

    def test_the_provider_prefixes_exist_in_the_pinned_litellm(self):
        # LiteLLM 1.109.1 (the image's pin) knows each native prefix; checked where litellm is importable.
        try:
            import litellm
        except ImportError:
            self.skipTest("litellm is only importable in the image")
        for route, prefix in self.PREFIXES.items():
            self.assertIn(prefix, {str(getattr(p, "value", p)) for p in litellm.provider_list}, route)
            self.assertEqual(sync.PREFIXES[entry(route)["kind"]], prefix)


class FreeExclusions(unittest.TestCase):
    """The free tier never serves a model the 2026-12-01 re-route bills: the real routing.json decides."""

    # Dated, preview, experimental and versioned ids of 2.5 Pro and 2.5 Flash: all excluded.
    EXCLUDED = ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-pro-preview-06-05",
                "gemini-2.5-flash-preview-09-2025", "gemini-2.5-pro-exp-03-25", "gemini-2.5-flash-001"]
    # Every 2.5 Flash Lite variant, and other families, stays on the free tier.
    ALLOWED = ["gemini-2.5-flash-lite", "gemini-2.5-flash-lite-preview-09-2025", "gemini-2.5-flash-lite-001",
               "gemini-2.5-flash-lite-preview-06-17", "gemini-2.0-flash-001"]

    def setUp(self):
        self.rules = sync.load_rules(str(ROOT / "catalog" / "routing.json"))

    def test_every_dated_preview_and_exp_2_5_pro_or_flash_is_in_no_free_group(self):
        for name in self.EXCLUDED:
            with self.subTest(name=name):
                self.assertEqual(sync.plan_groups(catalogue(("google", name, "usable", {})), self.rules), [])

    def test_a_paid_route_still_serves_an_excluded_model_with_no_free_group(self):
        for name in self.EXCLUDED:
            with self.subTest(name=name):
                specs = sync.plan_groups(catalogue(("google", name, "usable", {}), ("vibe", name, "usable", {})),
                                         self.rules)
                self.assertEqual({s["route"] for s in specs}, {"vibe"})
                self.assertFalse([s for s in specs if s["name"].startswith(("public/", "google-free/"))])
                self.assertEqual(len(specs), 1)

    def test_every_flash_lite_variant_stays_on_the_free_tier(self):
        for name in self.ALLOWED:
            with self.subTest(name=name):
                canon = sync.canonical_name("google", name, self.rules)
                specs = sync.plan_groups(catalogue(("google", name, "usable", {})), self.rules)
                self.assertEqual(sorted((s["name"], s["route"]) for s in specs),
                                 [(f"google-free/{canon}", "google"), (f"public/{canon}", "google")])
                free = [s for s in specs if s["name"].startswith("google-free/")]
                self.assertEqual(free[0]["model_info"]["access_groups"], ["free"])

    def test_a_public_group_keeps_its_other_public_routes_when_only_google_is_excluded(self):
        # Hypothetical: a second public route serves the excluded model. Only the google copy goes;
        # the other public route keeps its public group.
        rules = dict(self.rules, public_routes=["google", "vibe"])
        specs = sync.plan_groups(catalogue(("google", "gemini-2.5-pro", "usable", {}),
                                           ("vibe", "gemini-2.5-pro", "usable", {})), rules)
        self.assertFalse([s for s in specs if s["route"] == "google"])
        self.assertIn(("public/gemini-2.5-pro", "vibe"), [(s["name"], s["route"]) for s in specs])

    def test_a_rule_without_a_match_is_refused_rather_than_excluding_everything(self):
        for rule in ({"routes": ["google"]}, {"match": "", "routes": ["google"]}):
            with self.subTest(rule=rule):
                rules = dict(self.rules, free_exclusions=[rule])
                with self.assertRaisesRegex(RuntimeError, "match"):
                    sync.plan_groups(catalogue(("google", "gemini-4-hydrogen", "usable", {})), rules)

    def test_a_rule_with_an_invalid_regex_is_refused(self):
        rules = dict(self.rules, free_exclusions=[{"match": "^gemini-2.5-(pro", "routes": ["google"]}])
        with self.assertRaisesRegex(RuntimeError, "not a valid regex"):
            sync.plan_groups(catalogue(), rules)

    def test_a_rule_without_routes_is_refused(self):
        rules = dict(self.rules, free_exclusions=[{"match": "^gemini-2\\.5-pro$"}])
        with self.assertRaisesRegex(RuntimeError, "routes"):
            sync.plan_groups(catalogue(), rules)

    def test_loading_a_malformed_routing_file_fails_at_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "routing.json")
            with open(path, "w") as handle:
                json.dump({"free_exclusions": [{"routes": ["google"]}]}, handle)
            with self.assertRaisesRegex(RuntimeError, "match"):
                sync.load_rules(path)

    def test_the_real_routing_file_loads_and_is_valid(self):
        self.assertTrue(self.rules["free_exclusions"])
        sync.check_free_exclusions(self.rules)


if __name__ == "__main__":
    unittest.main()
