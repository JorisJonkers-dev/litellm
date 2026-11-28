"""Free groups across providers, their limits, and config/free_limits.py keeping a limited copy out of the pick."""
import asyncio
import copy
import datetime
import json
import re
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "catalog"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "config"))
import sync  # noqa: E402
from test_routing import RULES as BASE_RULES, catalogue  # noqa: E402

try:  # in the image, not on a workstation
    import litellm
    from litellm import Router
except ImportError:  # pragma: no cover
    litellm = Router = None
    # The callback needs only CustomLogger's base class to be imported; a stand-in lets its logic run here.
    custom_logger = types.ModuleType("litellm.integrations.custom_logger")
    custom_logger.CustomLogger = type("CustomLogger", (), {"__init__": lambda self, *a, **k: None})
    sys.modules.setdefault("litellm", types.ModuleType("litellm"))
    sys.modules.setdefault("litellm.integrations", types.ModuleType("litellm.integrations"))
    sys.modules["litellm.integrations.custom_logger"] = custom_logger

import free_limits  # noqa: E402

if litellm is None:  # the stand-in served the import only: other tests must still see no LiteLLM here
    for name in ("litellm.integrations.custom_logger", "litellm.integrations", "litellm"):
        sys.modules.pop(name, None)

REAL = sync.load_rules()
UTC = datetime.timezone.utc
GENERATED = json.loads((lambda t: t[t.index("{"):])((ROOT / "config" / "models.generated.yaml").read_text()))["model_list"]


def plan(*models, rules=None):
    rules = rules or dict(BASE_RULES, free_aliases=REAL["free_aliases"], free_order=REAL["free_order"], free_limits=REAL["free_limits"])
    return sync.plan_groups(catalogue(*models), rules)


def free(route, model_id):
    return (route, model_id, "usable", {"free": True, "chat_evidence": "listing"})


def group(specs, name):
    return sorted(spec["route"] for spec in specs if spec["name"] == name)


class Clustering(unittest.TestCase):
    def test_free_canonical_strips_only_what_marks_a_provider_or_the_free_variant(self):
        cases = {
            "@cf/openai/gpt-oss-120b": "gpt-oss-120b", "openai/gpt-oss-120b": "gpt-oss-120b", "gpt-oss-120b:free": "gpt-oss-120b",
            "@hf/thebloke/model-x": "model-x", "reflection/beam-free": "beam-1.0-501b-a23b", "GLM-4.7-Flash": "glm-4.7-flash",
            "@cf/meta/llama-3.3-70b-instruct-fp8-fast": "llama-3.3-70b-instruct-fp8-fast",
            "gpt-oss-safeguard-20b": "gpt-oss-safeguard-20b", "deepseek-v4-flash-0731": "deepseek-v4-flash-0731",
            "gemma-4-31b": "gemma-4-31b-it", "gemma-4-31b-it": "gemma-4-31b-it", "nemotron-3.5-lightning-30b-a3b": "nemotron-3.5-lightning",
        }
        for raw, want in cases.items():
            self.assertEqual(sync.free_canonical(raw, REAL), want, raw)

    def test_a_dotted_or_hyphenated_version_is_one_free_group(self):
        cases = {
            "qwen3-6-27b": "qwen3.6-27b", "qwen3.6-27b": "qwen3.6-27b", "qwen3-8-max": "qwen3.8-max",
            "qwen3-8-max-0902": "qwen3.8-max-0902", "deepseek-v3-1": "deepseek-v3.1", "deepseek-v3.1": "deepseek-v3.1",
            "minimax-m3-1-flash": "minimax-m3.1-flash", "deepseek-v4-1-flash": "deepseek-v4.1-flash",
            "deepseek-v4-1-flash-1017": "deepseek-v4.1-flash", "qwen2-5-coder-32b": "qwen2.5-coder-32b",
        }
        for raw, want in cases.items():
            self.assertEqual(sync.free_canonical(raw, REAL), want, raw)
        # Only a version after a letter is read: a model size after a hyphen, and a dated stamp, keep their spelling.
        for raw in ("gemma-2-9b-it", "llama-3-70b", "deepseek-v4-flash-0731", "model-v2-2025-01"):
            self.assertEqual(sync.free_canonical(raw, REAL), raw, raw)

    def test_one_model_on_every_free_route_is_one_group(self):
        specs = plan(free("cloudflare", "@cf/openai/gpt-oss-120b"), free("groq", "openai/gpt-oss-120b"),
                     free("unorouter", "gpt-oss-120b:free"), free("electronhub", "gpt-oss-120b:free"))
        self.assertEqual(group(specs, "free/gpt-oss-120b"), ["cloudflare", "electronhub", "groq", "unorouter"])

    def test_variants_snapshots_and_quantisations_stay_apart(self):
        specs = plan(free("groq", "openai/gpt-oss-20b"), free("groq", "openai/gpt-oss-safeguard-20b"),
                     free("unorouter", "gpt-oss-safeguard-20b:free"),
                     free("gonka", "deepseek-v4-flash-0731"), free("unorouter", "deepseek-v4-flash:free"),
                     free("cloudflare", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"), free("kilo", "meta/llama-3.3-70b-instruct:free"),
                     free("routeway", "gemma-4-26b-a4b-it-darksoul"), free("naga", "google/gemma-4-26b-a4b-it:free"))
        names = {spec["name"] for spec in specs if spec["name"].startswith("free/")}
        for name in ("free/gpt-oss-20b", "free/deepseek-v4-flash-0731", "free/deepseek-v4-flash",
                     "free/llama-3.3-70b-instruct-fp8-fast", "free/llama-3.3-70b-instruct", "free/gemma-4-26b-a4b-it-darksoul",
                     "free/gemma-4-26b-a4b-it"):
            self.assertIn(name, names)
        # A safety classifier is not a chat model: it joins no free group, and never gpt-oss-20b's.
        self.assertNotIn("free/gpt-oss-safeguard-20b", names)
        self.assertEqual(group(specs, "free/gpt-oss-20b"), ["groq"])

    def test_an_alias_merges_what_models_dev_names_one_model(self):
        specs = plan(free("unorouter", "gemma-4-31b:free"), free("openrouter", "google/gemma-4-31b-it:free"),
                     free("bazaarlink", "nemotron-3.5-lightning-free"), free("requesty", "nvidia/nemotron-3.5-lightning-30b-a3b"))
        self.assertEqual(group(specs, "free/gemma-4-31b-it"), ["openrouter", "unorouter"])
        self.assertEqual(group(specs, "free/nemotron-3.5-lightning"), ["bazaarlink", "requesty"])

    def test_a_dated_snapshot_joins_its_base_only_through_an_alias_and_a_glued_free_is_a_marker(self):
        specs = plan(free("cloudflare", "@cf/deepseek-ai/deepseek-v4.1-flash-1017"), free("electronhub", "deepseek-v4.1-flash:free"),
                     free("neosantara", "deepseek-v4.1-flash-free"), free("bazaarlink", "deepseek/deepseek-v4-flash-0731free:free"),
                     free("gonka", "deepseek-ai/DeepSeek-V4-Flash-0731"), free("unorouter", "deepseek-v4-flash:free"),
                     rules=dict(BASE_RULES, aliases=REAL["aliases"], free_aliases=REAL["free_aliases"], free_order=REAL["free_order"]))
        self.assertEqual(group(specs, "free/deepseek-v4.1-flash"), ["cloudflare", "electronhub", "neosantara"])
        self.assertEqual(group(specs, "free/deepseek-v4-flash-0731"), ["bazaarlink", "gonka"])
        # No alias says v4-flash (April) is the 0731 snapshot: it stays apart.
        self.assertEqual(group(specs, "free/deepseek-v4-flash"), ["unorouter"])

    def test_a_router_endpoint_is_not_a_model(self):
        specs = plan(free("openrouter", "openrouter/free"), free("kilo", "kilo/free"))
        self.assertEqual(group(specs, "free/free"), [])

    def test_a_route_serving_a_model_twice_keeps_one_copy(self):
        specs = plan(free("kilo", "reflection/beam-free"), free("kilo", "reflection/beam-1.0-501b-a23b:free"))
        self.assertEqual(group(specs, "free/beam-1.0-501b-a23b"), ["kilo"])

    def test_order_puts_per_minute_providers_before_scarce_daily_and_monthly_ones(self):
        specs = plan(free("cohere", "gpt-oss-120b"), free("requesty", "openai/gpt-oss-120b"), free("groq", "openai/gpt-oss-120b"),
                     free("kilo", "openai/gpt-oss-120b:free"))
        order = {spec["route"]: spec["order"] for spec in specs if spec["name"] == "free/gpt-oss-120b"}
        self.assertLess(order["groq"], order["kilo"])
        self.assertLess(order["kilo"], order["requesty"])
        self.assertLess(order["requesty"], order["cohere"])

    def test_paid_and_budgeted_deployments_never_join(self):
        specs = plan(free("groq", "openai/gpt-oss-120b"), ("vibe", "gpt-oss-120b", "usable", {"free": True}),
                     ("vibe-api", "gpt-oss-120b", "usable", {}), ("anthropic", "gpt-oss-120b", "usable", {}))
        self.assertEqual(group(specs, "free/gpt-oss-120b"), ["groq"])


class ChatOnly(unittest.TestCase):
    """free/* groups hold chat-completion models only (fail closed)."""

    def test_non_chat_models_never_join_a_free_group(self):
        ids = ["dreamshaper:free", "juggernaut-xl:free", "wai-cute-pony:free", "voyage-4:free", "bge-m3:free", "rerank-v3.5",
               "typhoon-asr-realtime", "cohere-transcribe-03-2026", "riva-translate-4b-instruct-v2", "typhoon-ocr-v2", "parse-v5.0",
               "suno-v5:free", "google/lyria-3-pro-preview:free", "meta/llama-guard-3-8b", "qwen3guard-gen-8b", "sea-guard",
               "gpt-oss-safeguard-20b:free", "llama-prompt-guard-2-86m", "absolutereality:free", "albedobase-xl-31:free",
               "anything-v5:free", "nova-furry-pony:free", "prefect-pony:free", "aura-1:free", "orca-router:free"]
        specs = plan(*[free("unorouter", i) for i in ids], free("unorouter", "glm-4.7-flash:free"))
        self.assertEqual({s["name"] for s in specs if s["name"].startswith("free/")}, {"free/glm-4.7-flash"})

    def test_a_free_all_route_needs_evidence_that_a_model_is_chat(self):
        evidence = [("typhoon", "typhoon-v2.5-30b-a3b-instruct", "usable", {"free": True, "chat_evidence": "models.dev"}),
                    ("typhoon", "mystery-model", "usable", {"free": True}),
                    ("unorouter", "mystery-model:free", "usable", {"free": True})]
        rules = dict(BASE_RULES, free_all=["typhoon"], free_order=REAL["free_order"])
        names = {s["name"] for s in plan(*evidence, rules=rules) if s["name"].startswith("free/")}
        self.assertIn("free/typhoon-v2.5-30b-a3b-instruct", names)
        self.assertEqual(group(plan(*evidence, rules=rules), "free/mystery-model"), [])

    def test_unorouter_needs_chat_evidence_not_just_the_free_suffix(self):
        # Its listing carries no model type: image, speech and router ids arrive with ":free" like chat models.
        unknown = ["absolutereality:free", "albedobase-xl-31:free", "anything-v5:free", "nova-furry-pony:free", "prefect-pony:free",
                   "tunix-pony:free", "aura-1:free", "orca-router:free", "mystery-model:free"]
        bare = [("unorouter", i, "usable", {"free": True}) for i in unknown]
        self.assertEqual([s for s in plan(*bare) if s["name"].startswith("free/")], [])
        # A name alone refuses a diffusion, speech or router id even when something vouches for it.
        vouched = [("unorouter", i, "usable", {"free": True, "chat_evidence": "models.dev"}) for i in unknown[:-1]]
        self.assertEqual([s for s in plan(*vouched) if s["name"].startswith("free/")], [])
        # A chat model with models.dev evidence stays; other routes are unaffected.
        kept = plan(("unorouter", "glm-4.7-flash:free", "usable", {"free": True, "chat_evidence": "models.dev"}),
                    ("unorouter", "mystery-model:free", "usable", {"free": True}),
                    ("kilo", "mystery-model:free", "usable", {"free": True}))
        self.assertEqual(group(kept, "free/glm-4.7-flash"), ["unorouter"])
        self.assertEqual(group(kept, "free/mystery-model"), ["kilo"])

    def test_electronhub_naga_and_routeway_need_chat_evidence_too(self):
        for route in ("electronhub", "naga", "routeway"):
            with self.subTest(route=route):
                self.assertIn(route, sync.CHAT_EVIDENCE_ROUTES)
                bare = plan((route, "mystery-model:free", "usable", {"free": True}))
                self.assertEqual([s for s in bare if s["name"].startswith("free/")], [])
                kept = plan((route, "glm-4.7-flash:free", "usable", {"free": True, "chat_evidence": "models.dev"}),
                            (route, "mystery-model:free", "usable", {"free": True}))
                self.assertEqual(group(kept, "free/glm-4.7-flash"), [route])
                self.assertEqual(group(kept, "free/mystery-model"), [])

    def test_the_generated_free_groups_hold_no_non_chat_model(self):
        for entry in GENERATED:
            if entry["model_name"].startswith("free/"):
                self.assertIsNone(sync.FREE_NON_CHAT.search(entry["litellm_params"]["model"]), entry["model_name"])
                self.assertNotIn("mode", entry["model_info"], entry["model_name"])

    def test_plugsky_serves_only_its_free_plan_models(self):
        plugsky = {e["litellm_params"]["model"] for e in GENERATED if e["model_info"].get("route") == "plugsky"}
        self.assertTrue(plugsky)
        self.assertLessEqual(plugsky, {"openai/plugsky-micro", "openai/plugsky-lite"})


class Nvidia(unittest.TestCase):
    def test_nvidia_nim_copies_join_the_free_groups_once_usable(self):
        specs = plan(free("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b"), free("nvidia", "nvidia/nemotron-3-ultra-550b-a55b"),
                     free("nvidia", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"), free("kilo", "nvidia/nemotron-3.5-lightning:free"),
                     free("nvidia", "nvidia/llama-3.1-nemotron-safety-guard-8b-v3"))
        self.assertEqual(group(specs, "free/nemotron-3.5-lightning"), ["kilo", "nvidia"])
        self.assertEqual(group(specs, "free/nemotron-3-ultra-550b-a55b"), ["nvidia"])
        self.assertEqual(group(specs, "free/nemotron-3-nano-omni-30b-a3b-reasoning"), ["nvidia"])
        self.assertFalse([s for s in specs if "guard" in s["upstream_id"]])
        # A blocked NVIDIA answer keeps it out (the probe or traffic decides), and a credit error blocks the account.
        self.assertEqual(plan(("nvidia", "nvidia/nemotron-3-ultra-550b-a55b", "blocked", {"free": True})), [])
        self.assertEqual(REAL["free_limits"]["nvidia"]["shared"], [{"requests": 40, "per": "minute"}])


class OpenRouterFree(unittest.TestCase):
    """OpenRouter's free models: every free chat model plans, the router endpoint is a last tier step, all say free."""
    ROUTER = "free-router/openrouter"

    def specs(self, *extra):
        return plan(free("openrouter", "particle-ai/particle-1:free"), free("openrouter", "nvidia/nemotron-3.5-super-vl:free"),
                    free("openrouter", "google/gemma-4-31b-it:free"), free("openrouter", "openrouter/free"), *extra,
                    rules=dict(BASE_RULES, free_aliases=REAL["free_aliases"], free_order=REAL["free_order"],
                               free_limits=REAL["free_limits"], free_routers=REAL["free_routers"]))

    def test_vision_language_chat_models_are_planned_like_any_other(self):
        specs = self.specs()
        self.assertEqual(group(specs, "free/particle-1"), ["openrouter"])
        self.assertEqual(group(specs, "free/nemotron-3.5-super-vl"), ["openrouter"])
        self.assertIsNone(sync.FREE_NON_CHAT.search("nvidia/nemotron-3.5-super-vl:free"))
        self.assertIsNone(sync.FREE_NON_CHAT.search("particle-ai/particle-1:free"))
        # The music models are not chat.
        self.assertIsNotNone(sync.FREE_NON_CHAT.search("google/lyria-3-pro-preview"))

    def test_the_generated_file_holds_all_ten_free_chat_models(self):
        ids = {e["litellm_params"]["model"].removeprefix("openrouter/") for e in GENERATED
               if e["model_info"].get("route") == "openrouter" and e["model_name"].startswith("free/")}
        self.assertEqual(len(ids), 10)
        # The free router is the eleventh OpenRouter deployment, and no model.
        self.assertIn("free-router/openrouter", {e["model_name"] for e in GENERATED if e["model_info"].get("route") == "openrouter"})
        self.assertLessEqual({"particle-ai/particle-1:free", "nvidia/nemotron-3.5-super-vl:free"}, ids)

    def test_the_router_endpoint_is_no_model_group_but_one_free_router_step(self):
        specs = self.specs()
        self.assertFalse([s for s in specs if s["name"] in ("free/free", "openrouter/free")])
        router = [s for s in specs if s["name"] == self.ROUTER]
        self.assertEqual(len(router), 1)
        spec = router[0]
        self.assertEqual(spec["litellm_params"]["model"], "openrouter/openrouter/free")
        self.assertEqual((spec["litellm_params"]["input_cost_per_token"], spec["litellm_params"]["output_cost_per_token"]), (0, 0))
        self.assertEqual(spec["litellm_params"]["rpm"], 20)
        self.assertNotIn("max_budget", spec["litellm_params"])
        self.assertNotIn("cooldown_time", spec["litellm_params"])
        self.assertEqual(spec["model_info"]["access_groups"], ["free"])
        self.assertEqual(spec["model_info"]["id"], "free-router/openrouter/openrouter/free")
        # Its id is nobody else's: the model groups keep their ids.
        self.assertEqual(len({s["model_info"]["id"] for s in specs}), len(specs))

    def test_the_router_step_is_planned_only_while_the_route_lists_it_usable(self):
        none = plan(free("openrouter", "google/gemma-4-31b-it:free"), rules=dict(BASE_RULES, free_routers=REAL["free_routers"]))
        self.assertFalse([s for s in none if s["name"] == self.ROUTER])
        blocked = plan(("openrouter", "openrouter/free", "blocked", {"free": True}), rules=dict(BASE_RULES, free_routers=REAL["free_routers"]))
        self.assertEqual(blocked, [])

    def test_a_free_router_must_be_named_for_a_free_only_route(self):
        for bad in ({"free-router/vibe": {"route": "vibe", "upstream_id": "x"}},
                    {"free/openrouter": {"route": "openrouter", "upstream_id": "openrouter/free"}},
                    {"free-router/openrouter": {"route": "openrouter"}}):
            with self.assertRaises(RuntimeError, msg=bad):
                sync.free_routers({"free_routers": bad})
        self.assertEqual(list(sync.free_routers(REAL)), [self.ROUTER])

    def test_tier_free_ends_on_the_router_and_stays_free(self):
        chain = sync.tier_chains(REAL)["tier-free"]
        self.assertEqual(chain[-1], {"model": self.ROUTER, "cost": "free"})
        self.assertEqual({step["cost"] for step in chain}, {"free"})
        sync.validate_tiers(REAL)
        for tier in ("tier-vibe", "tier-go-cheap", "tier-strong-open", "tier-review"):
            self.assertNotIn(self.ROUTER, [s["model"] for s in sync.tier_chains(REAL)[tier]])
        rules = copy.deepcopy(REAL)
        rules["tier_groups"]["tier-free"]["steps"][-1]["cost"] = "vibe"
        with self.assertRaises(RuntimeError):
            sync.validate_tiers(rules)

    def test_the_router_step_is_in_the_rendered_config(self):
        self.assertIn('"free/qwen3.8-27b", "free-router/openrouter"]', (ROOT / "config" / "config.yaml").read_text())

    def test_every_free_deployment_is_labelled_free_with_its_limit(self):
        specs = self.specs()
        limit = "1,000 requests a day per account (50 under 10 credits bought); 20 requests a minute per model"
        self.assertEqual(sync.free_limit_note("openrouter", REAL), limit)
        for spec in specs:
            info = spec["model_info"]
            self.assertIs(info["free"], True, info["id"])
            self.assertEqual((info["cost_class"], info["free_limit"]), ("free", limit), info["id"])
        # A route whose limits are not published still says so, and a paid deployment carries no such label.
        self.assertIn("not published", sync.free_limit_note("nvidia", {}))
        paid = plan(("vibe", "mistral-large", "usable", {}), rules=dict(BASE_RULES))
        self.assertTrue(paid)
        for spec in paid:
            self.assertNotIn("free", spec["model_info"])

    def test_the_published_catalogue_entry_says_free_with_the_limit(self):
        from unittest import mock
        entry = next(e for e in sync.ROUTES if e["route"] == "openrouter")
        listing = {"data": [{"id": "particle-ai/particle-1:free", "pricing": {"prompt": "0", "completion": "0"}},
                            {"id": "paid/model", "pricing": {"prompt": "0.1", "completion": "0.1"}}]}
        env = {"OPENROUTER_API_KEY": "k", "OPENROUTER_API_BASE": "https://or.invalid/v1"}
        with mock.patch.object(sync, "fetch_listing", return_value=(200, listing)):
            cat, _ = sync.build(env, lambda *a: (200, listing), {}, datetime.datetime(2026, 11, 27, tzinfo=UTC), rules=REAL)
        models = {m["id"]: m for m in cat["providers"]["openrouter"]["models"]}
        free_entry = models["openrouter/particle-ai/particle-1:free"]
        self.assertEqual(free_entry["metadata"]["cost_class"], "free")
        self.assertEqual(free_entry["metadata"]["free_limit"], sync.free_limit_note("openrouter", REAL))
        self.assertTrue(free_entry["description"].startswith("free (1,000 requests a day per account"))
        self.assertNotIn("cost_class", models["openrouter/paid/model"]["metadata"])
        self.assertEqual(models["openrouter/paid/model"]["description"], "")


class PaidOnly(unittest.TestCase):
    """Cloudflare's paid-plan models (routing.json paid_only, from its pricing page) are never free."""

    def test_a_paid_plan_model_is_never_planned_even_when_recorded_usable(self):
        rules = dict(BASE_RULES, paid_only=REAL["paid_only"], free_aliases=REAL["free_aliases"])
        specs = plan(free("cloudflare", "@cf/zai-org/glm-5.3"), free("cloudflare", "@cf/deepseek-ai/deepseek-v4.1-flash-1017"),
                     free("cloudflare", "@cf/zai-org/glm-4.7-flash"), free("electronhub", "glm-5.3:free"), rules=rules)
        self.assertEqual({s["upstream_id"] for s in specs if s["route"] == "cloudflare"}, {"@cf/zai-org/glm-4.7-flash"})
        self.assertFalse(any("glm-5.3" in s["upstream_id"] or "deepseek" in s["upstream_id"] for s in specs if s["route"] == "cloudflare"))
        self.assertEqual(group(specs, "free/glm-5.3"), ["electronhub"])

    def test_the_listing_marks_it_not_free_and_the_sync_does_not_make_it_usable(self):
        from unittest import mock
        entry = next(e for e in sync.ROUTES if e["route"] == "cloudflare")
        items = [{"id": "@cf/zai-org/glm-5.3", "task": {"name": "Text Generation"}},
                 {"id": "@cf/zai-org/glm-4.7-flash", "task": {"name": "Text Generation"}}]
        with mock.patch.object(sync, "fetch_listing", return_value=(200, {"data": items})):
            listed = {m["id"]: m for m in sync.list_models(entry, {"CLOUDFLARE_API_KEY": "k", "CLOUDFLARE_ACCOUNT_ID": "a",
                                                                   "CLOUDFLARE_API_BASE": "https://cf.invalid/v1"},
                                                             lambda *a: (200, {}), REAL)}
        self.assertFalse(listed["@cf/zai-org/glm-5.3"]["free"])
        self.assertTrue(listed["@cf/zai-org/glm-5.3"]["paid_only"])
        self.assertNotIn("paid_only", listed["@cf/zai-org/glm-4.7-flash"])

    def test_the_generated_file_holds_none(self):
        paid = set(REAL["paid_only"]["cloudflare"])
        self.assertTrue(paid)
        for entry in GENERATED:
            if entry["model_info"].get("route") == "cloudflare":
                self.assertNotIn(entry["litellm_params"]["model"].removeprefix("cloudflare/"), paid)


class Generated(unittest.TestCase):
    """The committed config/models.generated.yaml."""

    def free_entries(self):
        return [e for e in GENERATED if e["model_name"].startswith("free/")]

    def test_a_free_deployment_is_reachable_only_as_free_model(self):
        """Owner, 2026-11-26: the free/ prefix is the only path to, and the only marker of, a free deployment."""
        for entry in GENERATED:
            name, info, params = entry["model_name"], entry["model_info"], entry["litellm_params"]
            free = params.get("input_cost_per_token") == 0 and params.get("output_cost_per_token") == 0 and "account" not in info
            if info.get("route") in sync.FREE_GROUP_ROUTES and free or "free" in info.get("access_groups", []):
                # The one exception is a free router step (routing.json free_routers), see OpenRouterFree.
                if name.startswith(sync.AUTO_GROUP + "/"):
                    # An auto/ group copies the free deployments of a model for public-repository keys (litellm#941).
                    self.assertEqual(info["access_groups"], [sync.AUTO_ACCESS_GROUP], name)
                    continue
                self.assertTrue(name.startswith(("free/", sync.FREE_ROUTER_PREFIX)), name)
                self.assertEqual(info["access_groups"], ["free"], name)
            if name.startswith("free/"):
                self.assertNotIn("free", name[len("free/"):], name)
        prefixes = {e["model_name"].split("/")[0] for e in GENERATED if "/" in e["model_name"]}
        self.assertFalse(prefixes & {"public", "google-free", "opencode-go-free", "openrouter", "kilo", "groq", "unorouter"})
        groups = {g for e in GENERATED for g in e["model_info"].get("access_groups", [])}
        self.assertFalse(groups & {"openrouter-free-public", "google-free-public", "opencode-go-free-public"})

    def test_every_free_group_deployment_is_free_public_only_and_without_a_fixed_cooldown(self):
        entries = self.free_entries()
        self.assertGreater(len(entries), 100)
        for entry in entries:
            params, info = entry["litellm_params"], entry["model_info"]
            self.assertEqual((params["input_cost_per_token"], params["output_cost_per_token"]), (0, 0), info["id"])
            self.assertEqual(info["access_groups"], ["free"], info["id"])
            self.assertIn(info["route"], sync.FREE_GROUP_ROUTES, info["id"])
            self.assertNotIn(info["route"], ("vibe", "vibe-study", "vibe-api", "vibe-study-api", "anthropic"))
            self.assertNotIn("cooldown_time", params, info["id"])
            self.assertNotIn("max_budget", params, info["id"])
            self.assertNotIn("account", info, info["id"])

    def test_the_owners_example_models_are_groups_across_providers(self):
        sizes = {}
        for entry in self.free_entries():
            sizes[entry["model_name"]] = sizes.get(entry["model_name"], 0) + 1
        for name in ("free/glm-4.7-flash", "free/gpt-oss-120b", "free/gemma-4-31b-it", "free/nemotron-3.5-lightning"):
            self.assertGreaterEqual(sizes.get(name, 0), 3, name)
        self.assertNotIn("free/gemma-4-31b", sizes)

    # Near misses the audit kept apart on purpose, with the reason (see AGENTS.md, Free groups).
    KEPT_APART = {
        frozenset({"codestral", "codestral-2508"}): "codestral is a moving alias (models.dev: 2024-05 or 25.08 by provider)",
        frozenset({"qwen2.5-coder-32b", "qwen2.5-coder-32b-instruct"}): "Qwen2.5-Coder-32B is the base model, -instruct the chat tune",
        frozenset({"typhoon-ocr", "typhoon-ocr-preview"}): "one provider lists both as separate models",
        frozenset({"gemini-3-flash", "gemini-3-flash-preview"}): "Google lists both as separate models",
        frozenset({"gemini-3.1-flash-lite", "gemini-3.1-flash-lite-preview"}): "a preview and its GA release are separate releases",
        frozenset({"gemini-3.1-pro", "gemini-3.1-pro-preview"}): "a preview and its release are separate releases (as gemini-3.1-flash-lite)",
        frozenset({"deepseek-v4-flash", "deepseek-v4-flash-0731"}): "models.dev lists the April and the 0731 release as two models",
        frozenset({"qwen3.8-max", "qwen3.8-max-0902"}): "models.dev lists Qwen3.8 Max (2026-08-03) and Qwen3.8 Max 0902 (2026-09-02) as two releases",
        frozenset({"gemma-4-26b-a4b", "gemma-4-26b-a4b-it"}): "a bare id may be the base model: models.dev lists only the -it chat tune (as qwen2.5-coder)",
        frozenset({"llama-3.2-11b-vision", "llama-3.2-11b-vision-instruct"}): "Meta publishes Llama-3.2-11B-Vision (base) and -Instruct (chat tune) as two models (as qwen2.5-coder)",
    }

    def test_no_free_group_is_a_near_miss_of_another(self):
        """Two free groups whose ids differ only by a date stamp, punctuation or a variant marker are one model or a listed exception."""
        import re
        def loose(name):
            name = re.sub(r"free$", "", name.split(":")[0])
            name = re.sub(r"[-_.]?(\d{4}|\d{6}|\d{8})$", "", name)
            name = re.sub(r"[-_](it|instruct|chat|preview|latest|exp)$", "", name)
            return re.sub(r"[^a-z0-9]", "", name)
        by_key = {}
        # An alias added since the file was generated already merges its names: the next sync writes them as one.
        aliases = {k: v for k, v in (REAL.get("free_aliases") or {}).items() if not k.startswith("_")}
        for name in {aliases.get(n, n) for n in (e["model_name"][len("free/"):] for e in self.free_entries())}:
            by_key.setdefault(loose(name), set()).add(name)
        clashes = [frozenset(names) for names in by_key.values() if len(names) > 1]
        self.assertEqual([c for c in clashes if c not in self.KEPT_APART], [])

    def test_each_free_deployment_carries_its_routes_limits(self):
        for entry in self.free_entries():
            want = sync.free_limit_params(entry["model_info"]["route"], REAL)
            got = {k: v for k, v in entry["litellm_params"].items() if k in sync.DEPLOYMENT_LIMITS + ("daily_reset_time",)}
            self.assertEqual(got, want, entry["model_info"]["id"])
        groq = next(e for e in GENERATED if e["model_name"] == "free/gpt-oss-120b" and e["model_info"]["route"] == "groq")
        self.assertEqual({k: groq["litellm_params"][k] for k in ("rpm", "rpd", "tpm", "tpd", "daily_reset_time")},
                         {"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000, "daily_reset_time": "00:00"})

    def test_the_input_cap_reaches_the_inferbase_deployment_as_model_info(self):
        specs = plan(free("inferbase", "deepseek-v4.1-flash"), free("groq", "deepseek-v4.1-flash"))
        inferbase = group(specs, "free/deepseek-v4.1-flash")
        self.assertEqual(inferbase, ["groq", "inferbase"])
        for spec in specs:
            if spec["name"] == "free/deepseek-v4.1-flash" and spec["route"] == "inferbase":
                self.assertEqual(spec["model_info"]["max_input_tokens"], 8192)
            elif spec["name"] == "free/deepseek-v4.1-flash":
                self.assertNotIn("max_input_tokens", spec["model_info"], spec["route"])

    def test_the_free_tier_steps_name_groups_the_catalogue_serves(self):
        names = {e["model_name"] for e in GENERATED}
        steps = [step["model"] for tier in REAL["tier_groups"].values() for step in tier["steps"]
                 if step.get("model", "").startswith("free/")]
        served = [step for step in steps if step in names]
        self.assertGreaterEqual(len(served), 5, served)
        for old in ("free/hy4-flash-free", "free/longcat-2.5-free", "free/beam-free", "free/gemma-4-31b"):
            self.assertNotIn(old, steps)

    def test_the_proxy_loads_the_callback_and_enforces_deployment_limits(self):
        config = (ROOT / "config" / "config.yaml").read_text()
        self.assertIn('"free_limits.free_limits"', config)
        self.assertIn("optional_pre_call_checks: [router_budget_limiting, enforce_model_rate_limits]", config)
        self.assertIn("COPY config/free_limits.py /app/free_limits.py", (ROOT / "Dockerfile").read_text())


class LimitRules(unittest.TestCase):
    def test_the_real_rules_load(self):
        sync.check_free_limits(REAL)
        for route, entry in REAL["free_limits"].items():
            if not route.startswith("_"):
                self.assertIn("source", entry, route)

    def test_a_bad_entry_is_refused(self):
        for bad in ({"nope": {"status": "confirmed"}}, {"vibe": {"status": "confirmed"}},
                    {"kilo": {"shared": [{"requests": 1, "per": "week"}], "status": "confirmed"}},
                    {"kilo": {"shared": [{"requests": 1, "tokens": 2, "per": "day"}], "status": "confirmed"}},
                    {"kilo": {"deployment": {"rph": 1}, "status": "confirmed"}},
                    {"kilo": {"reset": "8:00", "status": "confirmed"}}, {"kilo": {"scope": "ip", "status": "confirmed"}},
                    {"kilo": {"status": "maybe"}}, {"kilo": {"account_match": "(", "status": "confirmed"}}):
            with self.assertRaises(RuntimeError, msg=bad):
                sync.check_free_limits({"free_limits": bad})

    def test_a_bad_request_cap_is_refused(self):
        self.assertEqual(REAL["free_limits"]["inferbase"]["max_input_tokens"], 8192)
        for bad in (0, -1, "8192", True, 1.5):
            with self.assertRaises(RuntimeError, msg=repr(bad)):
                sync.check_free_limits({"free_limits": {"inferbase": {"max_input_tokens": bad, "status": "confirmed"}}})

    def test_deployment_limits_are_litellm_params(self):
        self.assertEqual(sync.free_limit_params("ovh", REAL), {"rpm": 2})
        self.assertEqual(sync.free_limit_params("electronhub", REAL), {})
        self.assertEqual(sync.free_limit_params("x", {"free_limits": {"x": {"deployment": {"rpd": 5}, "reset": "08:00"}}}),
                         {"rpd": 5, "daily_reset_time": "08:00"})


def dep(route, model, group=None, **params):
    base = {"model": f"openai/{model}", "litellm_credential_name": route, "input_cost_per_token": 0, "output_cost_per_token": 0}
    base.update(params)
    return {"model_name": group or f"free/{model}", "litellm_params": base,
            "model_info": {"id": f"free/{route}/{model}", "route": route}}


class Error(Exception):
    def __init__(self, status, message="Rate limit exceeded", headers=None):
        super().__init__(message)
        self.status_code, self.message = status, message
        self.litellm_response_headers = headers or {}


TEST_RULES = {
    "cooldowns": {"electronhub": 120},
    "free_limits": {
        "kilo": {"shared": [{"requests": 3, "per": "hour"}], "scope": "account", "status": "confirmed"},
        "groq": {"deployment": {"rpd": 2}, "scope": "deployment", "status": "partial"},
        "openrouter": {"shared": [{"requests": 50, "per": "day"}], "reset": "00:00", "scope": "deployment",
                       "account_match": "free-models-per-day", "status": "confirmed"},
        "routeway": {"concurrency": 1, "scope": "account", "status": "unconfirmed"},
        "plugsky": {"shared": [{"tokens": 100, "per": "day"}], "scope": "account", "status": "unconfirmed"},
        "cohere": {"shared": [{"requests": 2, "per": "month"}], "scope": "deployment", "status": "unconfirmed"},
    },
}


class Hook(unittest.TestCase):
    def setUp(self):
        self.now = [datetime.datetime(2026, 11, 26, 22, 30, tzinfo=UTC).timestamp()]
        self.hook = free_limits.FreeLimits(TEST_RULES, clock=lambda: self.now[0], prisma=None, deployments=[])

    def at(self, *args):
        self.now[0] = datetime.datetime(*args, tzinfo=UTC).timestamp()

    def pick(self, deployments, model="free/m"):
        return [d["model_info"]["id"] for d in asyncio.run(self.hook.async_filter_deployments(model, deployments, None))]

    def call(self, deployment):
        asyncio.run(self.hook.async_pre_call_check(deployment))

    def kwargs(self, deployment, **extra):
        # As the router logs a call: the deployment's model and group in metadata, its model_info beside them.
        metadata = {"deployment": deployment["litellm_params"]["model"], "model_group": deployment["model_name"]}
        return dict({"litellm_params": {"model_info": deployment["model_info"], "metadata": metadata}}, **extra)

    def refuse(self, deployment, error):
        asyncio.run(self.hook.async_log_failure_event(self.kwargs(deployment, exception=error), None, None, None))

    def succeed(self, deployment, tokens=0, headers=None):
        response = types.SimpleNamespace(usage=types.SimpleNamespace(total_tokens=tokens), _hidden_params={"additional_headers": headers or {}})
        asyncio.run(self.hook.async_log_success_event(self.kwargs(deployment), response, None, None))

    def test_a_shared_hourly_budget_counts_every_model_of_the_route_and_returns_at_the_next_hour(self):
        a, b, other = dep("kilo", "a"), dep("kilo", "b", group="kilo/b"), dep("groq", "a")
        for deployment in (a, b, a):
            self.call(deployment)
        self.assertEqual(self.pick([a, b, other]), ["free/groq/a"])
        self.at(2026, 11, 26, 23, 0)
        self.assertEqual(self.pick([a, b, other]), ["free/kilo/a", "free/kilo/b", "free/groq/a"])

    def test_a_deployment_limit_is_shared_by_every_group_of_one_upstream_model_and_resets_daily(self):
        free_copy, route_copy = dep("groq", "a", rpd=2), dep("groq", "a", group="groq/a", rpd=2)
        route_copy["model_info"]["id"] = "groq/a"
        self.call(free_copy)
        self.call(route_copy)
        self.assertEqual(self.pick([free_copy, dep("groq", "b", rpd=2)]), ["free/groq/b"])
        self.at(2026, 11, 27, 0, 0)
        self.assertEqual(self.pick([free_copy]), ["free/groq/a"])

    def test_retry_after_blocks_the_deployment_until_then(self):
        a, b = dep("electronhub", "a"), dep("electronhub", "b")
        self.refuse(a, Error(429, headers={"retry-after": "30"}))
        self.assertEqual(self.pick([a, b]), ["free/electronhub/b"])
        self.now[0] += 30
        self.assertEqual(self.pick([a, b]), ["free/electronhub/a", "free/electronhub/b"])

    def test_without_any_reset_information_the_routes_cooldown_is_the_backstop(self):
        a = dep("electronhub", "a")
        self.refuse(a, Error(429))
        self.now[0] += 119
        self.assertEqual(self.pick([a, dep("groq", "x")]), ["free/groq/x"])
        self.now[0] += 1
        self.assertIn("free/electronhub/a", self.pick([a]))

    def test_an_account_scope_route_blocks_all_its_models(self):
        a, b = dep("kilo", "a"), dep("kilo", "b")
        self.refuse(a, Error(429, "Rate limit exceeded for free models. Please try again later.", {"retry-after": "600"}))
        self.assertEqual(self.pick([a, b, dep("groq", "x")]), ["free/groq/x"])

    def test_a_daily_account_error_blocks_the_account_until_the_daily_reset(self):
        a, b = dep("openrouter", "a"), dep("openrouter", "b")
        self.refuse(a, Error(429, "Rate limit exceeded: free-models-per-day"))
        self.assertEqual(self.pick([a, b, dep("groq", "x")]), ["free/groq/x"])
        self.at(2026, 11, 27, 0, 0)
        self.assertEqual(len(self.pick([a, b])), 2)

    CAPPED = {"inferbase": {"max_input_tokens": 8192, "scope": "account", "status": "unconfirmed"}}
    SIZE_402 = ("This request is about 9,774 input tokens; the free plan allows 8,192 per request on Inferbase's own "
                "models. Upgrade to Pro to lift it, shorten the input, or use your own provider key.")

    def capped(self, model="a"):
        deployment = dep("inferbase", model)
        deployment["model_info"]["max_input_tokens"] = 8192
        return deployment

    def hook_with_caps(self):
        return free_limits.FreeLimits(dict(TEST_RULES, free_limits=dict(TEST_RULES["free_limits"], **self.CAPPED)),
                                      clock=lambda: self.now[0], prisma=None, deployments=[])

    def pick_for(self, hook, deployments, messages):
        return [d["model_info"]["id"] for d in asyncio.run(hook.async_filter_deployments("free/m", deployments, messages))]

    @staticmethod
    def messages(words):
        return [{"role": "user", "content": " ".join(f"word{i}" for i in range(words))}]

    def test_a_request_over_a_copys_input_cap_skips_that_copy_and_keeps_the_uncapped_one(self):
        hook = self.hook_with_caps()
        big, plain = self.capped(), dep("electronhub", "b")
        self.assertEqual(self.pick_for(hook, [big, plain], self.messages(20000)), ["free/electronhub/b"])

    def test_a_small_request_keeps_both_copies_in_order(self):
        hook = self.hook_with_caps()
        big, plain = self.capped(), dep("electronhub", "b")
        self.assertEqual(self.pick_for(hook, [big, plain], self.messages(10)), ["free/inferbase/a", "free/electronhub/b"])

    def test_a_request_too_big_for_every_copy_fails_at_once_with_the_cap(self):
        hook = self.hook_with_caps()
        with self.assertRaisesRegex(ValueError, "every free copy of free/m caps a request below 8192 input tokens"):
            self.pick_for(hook, [self.capped()], self.messages(20000))

    def test_the_input_size_falls_back_to_a_third_of_the_json_without_a_tokenizer(self):
        self.assertEqual(free_limits.input_tokens("free/m", self.messages(3)), len(json.dumps(self.messages(3))) // 3)

    def test_a_size_402_blocks_nothing(self):
        hook = self.hook_with_caps()
        big, plain = self.capped(), dep("electronhub", "b")
        asyncio.run(hook.async_log_failure_event(self.kwargs(big, exception=Error(402, self.SIZE_402)), None, None, None))
        self.assertEqual(hook.store.blocked_until("a:inferbase", self.now[0]), 0)
        self.assertEqual(hook.store.blocked_until("d:inferbase|openai/a", self.now[0]), 0)
        self.assertEqual(self.pick_for(hook, [big, plain], self.messages(10)), ["free/inferbase/a", "free/electronhub/b"])

    def test_another_402_still_blocks_the_account(self):
        hook = self.hook_with_caps()
        big, plain = self.capped(), dep("electronhub", "b")
        asyncio.run(hook.async_log_failure_event(self.kwargs(big, exception=Error(402, "Payment required: add a payment method")), None, None, None))
        self.assertEqual(self.pick_for(hook, [big, plain], self.messages(10)), ["free/electronhub/b"])

    def test_a_402_blocks_the_account(self):
        a, b = dep("electronhub", "a"), dep("electronhub", "b")
        self.refuse(a, Error(402, "Payment required", {"retry-after": "60"}))
        self.assertEqual(self.pick([a, b, dep("groq", "x")]), ["free/groq/x"])

    def test_an_error_that_is_no_limit_is_left_to_the_router(self):
        a = dep("electronhub", "a")
        self.refuse(a, Error(500, "upstream exploded"))
        self.assertEqual(self.pick([a]), ["free/electronhub/a"])

    def test_a_401_blocks_only_that_deployment_for_the_dead_block(self):
        a, b = dep("gonka", "a"), dep("gonka", "b")
        self.refuse(a, Error(401, "API key is paused or access disabled"))
        self.assertEqual(self.pick([a, b, dep("groq", "x")]), ["free/gonka/b", "free/groq/x"])
        self.now[0] += free_limits.DEAD_BLOCK - 1
        self.assertEqual(self.pick([a, b]), ["free/gonka/b"])
        self.now[0] += 1
        self.assertEqual(self.pick([a, b]), ["free/gonka/a", "free/gonka/b"])

    def test_a_400_saying_the_model_is_deprecated_blocks_that_deployment(self):
        a, b = dep("neosantara", "a"), dep("neosantara", "b")
        self.refuse(a, Error(400, "Model 'deepseek-v4.1-flash-free' is deprecated"))
        self.assertEqual(self.pick([a, b]), ["free/neosantara/b"])
        self.now[0] += free_limits.DEAD_BLOCK
        self.assertEqual(len(self.pick([a, b])), 2)

    def test_a_404_blocks_that_deployment(self):
        a, b = dep("typhoon", "a"), dep("typhoon", "b")
        self.refuse(a, Error(404, "Not Found"))
        self.assertEqual(self.pick([a, b]), ["free/typhoon/b"])

    def test_a_plain_400_does_not_block(self):
        a = dep("neosantara", "a")
        self.refuse(a, Error(400, "invalid parameter: temperature"))
        self.assertEqual(self.pick([a]), ["free/neosantara/a"])

    def test_the_limit_refusal_text_never_blocks_even_with_a_dead_status(self):
        a = dep("electronhub", "a")
        self.refuse(a, Error(401, free_limits.LIMIT_ERROR))
        self.assertEqual(self.pick([a]), ["free/electronhub/a"])

    def test_a_dead_copy_with_a_reset_header_is_blocked_until_that_reset(self):
        a, b = dep("gonka", "a"), dep("gonka", "b")
        self.refuse(a, Error(403, "Forbidden", {"retry-after": "90"}))
        self.assertEqual(self.pick([a, b]), ["free/gonka/b"])
        self.now[0] += 90
        self.assertEqual(len(self.pick([a, b])), 2)

    def test_a_plain_403_blocks_that_deployment_for_fifteen_minutes_only(self):
        a, b = dep("gonka", "a"), dep("gonka", "b")
        self.refuse(a, Error(403, "Forbidden: this model is not available in your region"))
        self.assertEqual(self.pick([a, b]), ["free/gonka/b"])
        self.now[0] += free_limits.FORBIDDEN_BLOCK - 1
        self.assertEqual(self.pick([a, b]), ["free/gonka/b"])
        self.now[0] += 1
        self.assertEqual(len(self.pick([a, b])), 2)
        self.assertLess(free_limits.FORBIDDEN_BLOCK, free_limits.DEAD_BLOCK)

    def test_a_403_that_speaks_of_a_quota_is_handled_like_a_429(self):
        a, b = dep("electronhub", "a"), dep("electronhub", "b")
        self.refuse(a, Error(403, "Quota exceeded for this key"))
        self.assertEqual(self.pick([a, b]), ["free/electronhub/b"])
        self.now[0] += 120  # the route's cooldown, not fifteen minutes and not six hours
        self.assertEqual(len(self.pick([a, b])), 2)

    def test_a_404_still_blocks_for_the_dead_block(self):
        a, b = dep("typhoon", "a"), dep("typhoon", "b")
        self.refuse(a, Error(404, "Not Found"))
        self.now[0] += free_limits.DEAD_BLOCK - 1
        self.assertEqual(self.pick([a, b]), ["free/typhoon/b"])
        self.now[0] += 1
        self.assertEqual(len(self.pick([a, b])), 2)

    def test_a_bare_quota_or_credit_text_no_longer_waits_for_the_daily_reset(self):
        a, b = dep("electronhub", "a"), dep("electronhub", "b")
        self.refuse(a, Error(429, "You have exceeded your quota; check your credit"))
        self.now[0] += 120
        self.assertEqual(len(self.pick([a, b])), 2)

    def test_a_text_that_clearly_means_a_day_waits_for_the_daily_reset(self):
        for text in ("limit of 50 requests per day", "daily allowance spent", "no more requests today", "24h limit reached"):
            self.at(2026, 11, 26, 22, 30)
            a, b = dep("electronhub", text[:6]), dep("electronhub", "b")
            self.refuse(a, Error(429, text))
            self.now[0] += 120
            self.assertEqual(self.pick([a, b]), ["free/electronhub/b"], text)
            self.at(2026, 11, 27, 0, 0)
            self.assertEqual(len(self.pick([a, b])), 2, text)

    def test_a_route_that_declares_an_hourly_window_is_blocked_to_the_next_clock_hour(self):
        rules = dict(TEST_RULES, free_limits=dict(TEST_RULES["free_limits"], kilo=dict(TEST_RULES["free_limits"]["kilo"], window="hour")))
        hook = free_limits.FreeLimits(rules, clock=lambda: self.now[0], prisma=None, deployments=[])
        a = dep("kilo", "a")
        asyncio.run(hook.async_log_failure_event(self.kwargs(a, exception=Error(429, "Rate limit exceeded for free models. Daily quota")), None, None, None))
        self.at(2026, 11, 26, 22, 59)
        self.assertEqual(hook.store.blocked_until("a:kilo", self.now[0]), datetime.datetime(2026, 11, 26, 23, 0, tzinfo=UTC).timestamp())
        self.at(2026, 11, 26, 23, 0)
        self.assertEqual(hook.store.blocked_until("a:kilo", self.now[0]), 0)

    def test_the_real_kilo_route_declares_an_hourly_window_and_the_slow_routes_a_longer_cooldown(self):
        self.assertEqual(REAL["free_limits"]["kilo"]["window"], "hour")
        for route in ("electronhub", "unorouter", "neosantara"):
            self.assertEqual(REAL["cooldowns"][route], 300)

    def test_poe_saying_it_needs_a_subscription_blocks_the_account_for_the_dead_block(self):
        a, b = dep("poe", "a"), dep("poe", "b")
        self.refuse(a, Error(402, "This model requires an active Poe subscription"))
        self.assertEqual(self.pick([a, b, dep("groq", "x")]), ["free/groq/x"])
        self.now[0] += free_limits.DEAD_BLOCK - 1
        self.assertEqual(self.pick([a, b, dep("groq", "x")]), ["free/groq/x"])
        self.now[0] += 1
        self.assertEqual(len(self.pick([a, b])), 2)

    def test_a_retry_after_of_zero_never_shortens_a_subscription_block(self):
        a, b = dep("poe", "a"), dep("poe", "b")
        text = "litellm.PaymentRequiredError: PoeException - The model X requires an active Poe subscription for API access."
        with self.assertLogs("free_limits", "WARNING") as logs:
            self.refuse(a, Error(402, text, {"retry-after": "0", "x-ratelimit-reset": "0"}))
        self.assertIn("a:poe unavailable for 21600s", logs.records[0].getMessage())
        self.assertEqual(self.hook.unavailable_until(a), self.now[0] + free_limits.DEAD_BLOCK)

    def test_a_zero_retry_after_on_a_429_falls_back_to_the_cooldown(self):
        a, b = dep("electronhub", "a"), dep("electronhub", "b")
        self.refuse(a, Error(429, "Rate limit exceeded", {"retry-after": "0"}))
        self.assertEqual(self.pick([a, b]), ["free/electronhub/b"])

    INFERBASE = ("litellm.PaymentRequiredError: Free plan limit: 100 requests per UTC day on Inferbase's own models for this "
                 "organization, all used. It resets at 00:00 UTC.")

    def test_inferbases_daily_402_blocks_the_account_until_the_named_reset(self):
        a = dep("inferbase", "a")
        self.refuse(a, Error(402, self.INFERBASE))
        self.assertEqual(self.hook.unavailable_until(a), datetime.datetime(2026, 11, 27, 0, 0, tzinfo=UTC).timestamp())
        self.at(2026, 11, 27, 0, 0)
        self.assertEqual(len(self.pick([a])), 1)

    def test_a_named_reset_time_and_other_day_phrases_set_the_block(self):
        cases = (("Quota used. Resets at 06:30 UTC.", (2026, 11, 27, 6, 30)), ("100 requests a day", (2026, 11, 27, 0, 0)),
                 ("limit is 5 per day", (2026, 11, 27, 0, 0)))
        for text, end in cases:
            self.at(2026, 11, 26, 22, 30)
            a = dep("electronhub", text[:5])
            self.refuse(a, Error(429, text))
            self.assertEqual(self.hook.unavailable_until(a), datetime.datetime(*end, tzinfo=UTC).timestamp(), text)

    def test_electronhub_needing_phone_verification_blocks_the_whole_account_and_logs_an_owner_action(self):
        a, b = dep("electronhub", "a"), dep("electronhub", "b")
        text = "To prevent abuse and high traffic on free models, phone verification is required. Verify at electronhub.ai"
        with self.assertLogs("free_limits", "WARNING") as logs:
            self.refuse(a, Error(403, text))
        line = logs.records[0].getMessage()
        self.assertIn("a:electronhub unavailable for 21600s", line)
        self.assertIn("needs account verification", line)
        self.assertEqual(self.hook.unavailable_until(b), self.now[0] + free_limits.DEAD_BLOCK)

    def test_every_block_logs_its_key_length_status_and_the_first_120_characters_of_the_text(self):
        a = dep("gonka", "a")
        text = "Forbidden\n" + "x " * 100
        with self.assertLogs("free_limits", "WARNING") as logs:
            self.refuse(a, Error(403, text))
        line = logs.records[0].getMessage()
        self.assertTrue(line.startswith("free_limits: d:gonka|openai/a unavailable for 900s (status 403: Forbidden x x "), line)
        self.assertNotIn("\n", line)
        self.assertEqual(line.split("(status 403: ", 1)[1].rstrip(")"), " ".join(text.split())[:120])

    def test_a_log_line_never_carries_a_key_or_a_header_value(self):
        a = dep("gonka", "a")
        error = Error(401, "Incorrect API key provided: sk-abcdef1234567890XYZ and Bearer eyJhbGciOiJIUzI1NiJ9abcdef", {"authorization": "Bearer topsecretheader", "retry-after": "60"})
        with self.assertLogs("free_limits", "WARNING") as logs:
            self.refuse(a, error)
        line = logs.records[0].getMessage()
        for secret in ("sk-abcdef1234567890XYZ", "eyJhbGciOiJIUzI1NiJ9abcdef", "topsecretheader"):
            self.assertNotIn(secret, line)
        self.assertIn("(status 401: Incorrect API key provided: ***", line)

    def test_our_own_refusal_carries_a_marker_and_keeps_the_prefix_and_the_back_in_format(self):
        self.assertTrue(free_limits.LIMIT_ERROR.startswith("No deployments available for selected model, Try again in a moment: free limits"))
        self.assertIn("limiter refusal", free_limits.LIMIT_ERROR)
        a = dep("electronhub", "a")
        self.refuse(a, Error(429, headers={"retry-after": "30"}))
        with self.assertRaisesRegex(ValueError, r"limiter refusal.*back in 30s"):
            self.pick([a])

    def test_a_free_deployment_is_planned_with_a_request_timeout_a_route_may_override(self):
        specs = plan(free("groq", "gpt-oss-120b"), free("poe", "gpt-oss-120b"))
        free_specs = [spec for spec in specs if spec["name"].startswith("free/")]
        self.assertTrue(free_specs)
        for spec in free_specs:
            self.assertEqual(spec["litellm_params"]["timeout"], sync.FREE_TIMEOUT, spec["route"])
            self.assertEqual(spec["litellm_params"]["stream_timeout"], sync.FREE_STREAM_TIMEOUT, spec["route"])
        self.assertEqual(sync.free_timeout("poe", dict(REAL, free_limits={"poe": {"timeout": 90}})), 90)
        self.assertEqual(sync.free_stream_timeout("poe", dict(REAL, free_limits={"poe": {"stream_timeout": 45}})), 45)
        # A finished answer may take far longer than a stream may go silent.
        self.assertGreater(sync.FREE_TIMEOUT, sync.FREE_STREAM_TIMEOUT)

    def test_every_attempt_at_a_free_stream_fits_before_cloudflare_cuts_the_request(self):
        # The proxy sends no byte until the first token; Cloudflare answers 524 after about 100 s of silence.
        # The first attempt and every retry wait up to stream_timeout each, so together they must fit.
        router = (ROOT / "config" / "config.yaml").read_text()
        retries = int(re.search(r"^\s*num_retries:\s*(\d+)", router, re.MULTILINE).group(1))
        routes = {"": sync.FREE_STREAM_TIMEOUT}
        routes.update({route: sync.free_stream_timeout(route, REAL) for route, rule in (REAL.get("free_limits") or {}).items() if isinstance(rule, dict)})
        routes["paid and tier steps"] = sync.PAID_STREAM_TIMEOUT
        for route, seconds in routes.items():
            self.assertLess((retries + 1) * seconds, sync.CLOUDFLARE_IDLE_LIMIT, route or "default")

    def test_when_every_copy_is_limited_the_call_fails_at_once(self):
        a = dep("electronhub", "a")
        self.refuse(a, Error(429, headers={"retry-after": "30"}))
        with self.assertRaisesRegex(ValueError, "free limits.*back in 30s"):
            self.pick([a])

    def test_a_paid_deployment_is_never_touched(self):
        paid = {"model_name": "glm-5.3", "litellm_params": {"model": "mistral/glm-5.3", "litellm_credential_name": "vibe", "max_budget": 1},
                "model_info": {"id": "vibe:glm-5.3", "route": "vibe", "account": "vibe"}}
        self.refuse(paid, Error(429, headers={"retry-after": "600"}))
        self.assertEqual(self.pick([paid]), ["vibe:glm-5.3"])
        self.assertIsNone(self.hook.route_of(paid))

    def test_route_concurrency_holds_one_request_in_flight(self):
        a, b = dep("routeway", "a"), dep("routeway", "b")
        self.call(a)
        self.assertEqual(self.pick([a, b, dep("groq", "x")]), ["free/groq/x"])
        self.succeed(a)
        self.assertEqual(self.pick([a, b]), ["free/routeway/a", "free/routeway/b"])

    def test_a_lost_slot_expires(self):
        a = dep("routeway", "a")
        self.call(a)
        self.now[0] += free_limits.SLOT_TTL
        self.assertEqual(self.pick([a]), ["free/routeway/a"])

    def test_a_success_with_nothing_left_blocks_until_the_headers_reset(self):
        a, b = dep("groq", "a"), dep("groq", "b")
        self.succeed(a, headers={"llm_provider-x-ratelimit-remaining-requests": "0", "llm_provider-x-ratelimit-reset-requests": "2m30s"})
        self.succeed(b, headers={"x-ratelimit-remaining-requests": "5", "x-ratelimit-reset-requests": "1m"})
        self.assertEqual(self.pick([a, b]), ["free/groq/b"])
        self.now[0] += 150
        self.assertEqual(self.pick([a, b]), ["free/groq/a", "free/groq/b"])

    def test_tokens_count_after_a_success(self):
        a = dep("plugsky", "a")
        self.call(a)
        self.succeed(a, tokens=100)
        self.assertEqual(self.pick([a, dep("groq", "x")]), ["free/groq/x"])

    def test_a_monthly_budget_turns_with_the_calendar_month(self):
        a = dep("cohere", "a")
        self.call(a)
        self.call(a)
        self.assertEqual(self.pick([a, dep("groq", "x")]), ["free/groq/x"])
        self.at(2026, 12, 1, 0, 0)
        self.assertEqual(self.pick([a]), ["free/cohere/a"])


class Seeding(unittest.TestCase):
    """After a restart the day's and the month's counts come back from the spend log."""

    def test_counts_of_the_current_windows_are_seeded_and_older_ones_are_not(self):
        now = datetime.datetime(2026, 11, 26, 22, 30, tzinfo=UTC)
        kilo, groq = dep("kilo", "a"), dep("groq", "a", rpd=2)

        class Prisma:
            class db:
                @staticmethod
                async def query_raw(sql, since):
                    Prisma.sql = sql
                    return [{"model_id": "free/kilo/a", "hour": now.replace(minute=0, tzinfo=None), "n": 3, "tokens": 10},
                            {"model_id": "free/groq/a", "hour": "2026-11-26T01:00:00", "n": 2, "tokens": 5},
                            {"model_id": "free/groq/a", "hour": "2026-11-25T23:00:00", "n": 9, "tokens": 5},
                            {"model_id": "unknown", "hour": "2026-11-26T01:00:00", "n": 9, "tokens": 5}]

        hook = free_limits.FreeLimits(TEST_RULES, clock=lambda: now.timestamp(), prisma=Prisma, deployments=[kilo, groq])
        kept = asyncio.run(hook.async_filter_deployments("free/a", [kilo, groq, dep("electronhub", "a")], None))
        self.assertEqual([d["model_info"]["id"] for d in kept], ["free/electronhub/a"])
        self.assertIn("LiteLLM_SpendLogs", Prisma.sql)

    def test_without_a_database_it_serves_and_counts_from_zero(self):
        hook = free_limits.FreeLimits(TEST_RULES, clock=lambda: 0.0, prisma=None, deployments=None)
        hook._database = lambda: (None, [])
        a = dep("kilo", "a")
        self.assertEqual(len(asyncio.run(hook.async_filter_deployments("free/a", [a], None))), 1)


class ResetValues(unittest.TestCase):
    def test_reset_header_formats(self):
        now = 1_800_000_000.0
        self.assertEqual(free_limits.parse_reset("30", now), now + 30)
        self.assertAlmostEqual(free_limits.parse_reset("2m59.56s", now), now + 179.56)
        self.assertAlmostEqual(free_limits.parse_reset("6ms", now), now + 0.006)
        self.assertEqual(free_limits.parse_reset("1h", now), now + 3600)
        self.assertEqual(free_limits.parse_reset(str(int(now + 90)), now), now + 90)
        self.assertEqual(free_limits.parse_reset(str(int((now + 90) * 1000)), now), now + 90)
        self.assertEqual(free_limits.parse_reset("Fri, 15 Jan 2027 08:00:00 GMT", now),
                         datetime.datetime(2027, 1, 15, 8, tzinfo=UTC).timestamp())
        for nothing in (None, "", "soon", "-1"):
            self.assertIsNone(free_limits.parse_reset(nothing, now))

    def test_a_header_is_found_however_litellm_spelled_it(self):
        for headers in ({"retry-after": "5"}, {"Retry-After": "5"}, {"llm_provider-retry-after": "5"}, {"retry_after": "5"}):
            self.assertEqual(free_limits.header(headers, "retry-after"), "5")

    def test_the_daily_window_starts_at_the_routes_reset(self):
        at = datetime.datetime(2026, 11, 26, 7, 59, tzinfo=UTC).timestamp()
        key, ends = free_limits.window_key("day", at, free_limits.reset_offset("08:00"))
        self.assertEqual(ends, datetime.datetime(2026, 11, 26, 8, tzinfo=UTC).timestamp())
        self.assertEqual(key, "20261125")


@unittest.skipIf(Router is None, "litellm is not importable here; it is in the image (run the suite there)")
class RouterIntegration(unittest.TestCase):
    """The callback and LiteLLM's own per-deployment limits in LiteLLM's Router, the upstream mocked."""

    def router(self, hook, extra=None):
        model_list = []
        for route in ("kilo", "groq", "electronhub"):
            params = {"model": "openai/mock", "api_key": "k", "mock_response": route, "order": {"kilo": 1, "groq": 2, "electronhub": 3}[route],
                      "input_cost_per_token": 0, "output_cost_per_token": 0, "litellm_credential_name": route}
            params.update((extra or {}).get(route, {}))
            model_list.append({"model_name": "free/m", "litellm_params": params,
                               "model_info": {"id": f"free/{route}/m", "route": route}})
        litellm.callbacks = [hook]
        return Router(model_list=model_list, num_retries=2, routing_strategy="simple-shuffle",
                      optional_pre_call_checks=["enforce_model_rate_limits"])

    def ask(self, router):
        async def main():
            reply = await router.acompletion(model="free/m", messages=[{"role": "user", "content": "hi"}])
            await asyncio.sleep(0.2)  # the callbacks run after the answer
            return reply.choices[0].message.content
        return asyncio.run(main())

    def test_a_spent_shared_budget_moves_the_pick_without_a_failed_attempt(self):
        hook = free_limits.FreeLimits({"free_limits": {"kilo": {"shared": [{"requests": 2, "per": "hour"}], "scope": "account", "status": "confirmed"}}})
        router = self.router(hook)
        self.assertEqual([self.ask(router) for _ in range(3)], ["kilo", "kilo", "groq"])

    def test_litellm_holds_a_deployment_to_its_own_rpd(self):
        hook = free_limits.FreeLimits({"free_limits": {}})
        router = self.router(hook, extra={"kilo": {"rpd": 1, "daily_reset_time": "08:00"}})
        self.assertEqual([self.ask(router) for _ in range(2)], ["kilo", "groq"])

    def test_a_provider_429_moves_the_next_calls_until_retry_after(self):
        hook = free_limits.FreeLimits({"free_limits": {}, "cooldowns": {"kilo": 3600}})
        router = self.router(hook, extra={"kilo": {"mock_response": "litellm.RateLimitError"}})
        self.assertEqual(self.ask(router), "groq")
        self.assertTrue(hook.unavailable_until({"litellm_params": {"model": "openai/mock", "litellm_credential_name": "kilo",
                                                                    "input_cost_per_token": 0, "output_cost_per_token": 0},
                                                 "model_info": {"route": "kilo"}, "model_name": "free/m"}))


if __name__ == "__main__":
    unittest.main()
