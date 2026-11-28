"""The paid/<model> chains: one group per chat model across the paid routes, in routing.json's order."""
import asyncio
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "catalog"))
sys.path.insert(0, str(ROOT / "tests"))
import sync  # noqa: E402
from test_routing import ENV, RULES, Http, catalogue, entry  # noqa: E402
from test_tiers import configured_tiers  # noqa: E402

try:  # in the image, not on a workstation
    from litellm import Router
except ImportError:  # pragma: no cover
    Router = None

PAID_ROUTES = ["vibe-study", "vibe", "opencode-go", "vibe-study-api", "vibe-api"]
PAID_RULES = dict(
    RULES,
    paid_route_order=PAID_ROUTES,
    accounts={route: {"routes": [route], "max_budget": 10, "window": {"days": 30}} for route in PAID_ROUTES},
    cooldowns=dict(RULES["cooldowns"], **{route: 3600 for route in PAID_ROUTES}),
)
ALL_FIVE = [("vibe-study", "deepseek-v4.1-flash-1017"), ("vibe", "deepseek-v4.1-flash-1017"),
            ("opencode-go", "deepseek-v4.1-flash"), ("vibe-study-api", "deepseek-v4.1-flash"),
            ("vibe-api", "deepseek-v4.1-flash")]


def plan(*models, rules=PAID_RULES):
    return sync.plan_groups(catalogue(*models), rules)


def chain(specs, name):
    return [(spec["route"], spec["order"]) for spec in specs if spec["name"] == f"paid/{name}"]


def usable(route, model_id, **meta):
    return (route, model_id, "usable", dict(meta))


class PaidChains(unittest.TestCase):
    def test_the_five_routes_try_in_the_fixed_order_and_a_route_without_the_model_is_skipped(self):
        specs = plan(*[usable(route, model_id) for route, model_id in ALL_FIVE])
        self.assertEqual(chain(specs, "deepseek-v4.1-flash"),
                         [("vibe-study", 1), ("vibe", 2), ("opencode-go", 3), ("vibe-study-api", 4), ("vibe-api", 5)])
        # glm-5.3-flash is served by the Vibe plans and Go only: the API keys have no copy, so no step to skip
        glm = [usable("vibe-study", "glm-5.3-flash"), usable("vibe", "glm-5.3-flash"),
               usable("opencode-go", "glm-5.3-flash")]
        self.assertEqual(chain(plan(*glm), "glm-5.3-flash"), [("vibe-study", 1), ("vibe", 2), ("opencode-go", 3)])

    def test_the_chain_is_the_routing_file_order_not_a_hard_coded_one(self):
        rules = dict(PAID_RULES, paid_route_order=list(reversed(PAID_ROUTES)))
        specs = plan(*[usable(route, model_id) for route, model_id in ALL_FIVE], rules=rules)
        self.assertEqual([route for route, _ in chain(specs, "deepseek-v4.1-flash")], list(reversed(PAID_ROUTES)))

    def test_a_paid_copy_is_the_routes_own_budgeted_deployment(self):
        specs = plan(*[usable(route, model_id) for route, model_id in ALL_FIVE])
        canonical = {spec["model_info"]["id"]: spec for spec in specs if not spec["name"].startswith("paid/")}
        copies = [spec for spec in specs if spec["name"].startswith("paid/")]
        self.assertEqual(len(copies), 5)
        for copy_spec in copies:
            self.assertEqual(copy_spec["litellm_params"]["max_budget"], 10)
            # A streamed paid answer that goes silent is dropped in time for the next route, inside Cloudflare's limit.
            self.assertEqual(copy_spec["litellm_params"]["stream_timeout"], sync.PAID_STREAM_TIMEOUT)
            self.assertTrue(copy_spec["model_info"].get("account"))
            origin = canonical.get(copy_spec["model_info"]["id"])
            if origin is not None:
                # Where the route also has a canonical deployment, the copy shares its id, budget and cooldown.
                self.assertEqual(copy_spec["litellm_params"]["model"], origin["litellm_params"]["model"])
                self.assertEqual(copy_spec["litellm_params"].get("cooldown_time"), origin["litellm_params"].get("cooldown_time"))
        # backup_only drops opencode-go from the canonical group where Vibe serves the model; the chain keeps it
        self.assertNotIn("opencode-go", {spec["route"] for spec in specs if spec["name"] == "deepseek-v4.1-flash"})
        self.assertIn(("opencode-go", 3), chain(specs, "deepseek-v4.1-flash"))


class StreamTimeouts(unittest.TestCase):
    def test_every_direct_and_paid_deployment_drops_a_silent_stream_but_the_local_route(self):
        specs = plan(*[usable(route, model_id) for route, model_id in ALL_FIVE])
        self.assertTrue([spec for spec in specs if not spec["name"].startswith("paid/")])
        for spec in specs:
            self.assertEqual(spec["litellm_params"].get("stream_timeout"), sync.PAID_STREAM_TIMEOUT, spec["name"])
        local = next(entry for entry in sync.ROUTES if entry["route"] == "local")
        chosen = {"upstream_id": "qwen3:32b", "modality": "chat"}
        try:
            spec = sync.deployment("qwen3:32b", "qwen3:32b", "local", chosen, 1, 1, local)
        except KeyError as missing:  # the builder reads more of a chosen copy than this test names
            self.fail(f"deployment() needs {missing} in the chosen copy")
        self.assertNotIn("stream_timeout", spec["litellm_params"])


class MistralReleases(unittest.TestCase):
    def test_a_mistral_model_has_one_name_and_the_newest_release(self):
        specs = plan(usable("vibe", "mistral-large-2411"), usable("vibe", "mistral-large-2512"),
                     usable("vibe-study", "mistral-large-2411"),
                     usable("vibe", "mistral-large-latest", resolves_to="mistral-large-2512"))
        mistral = [spec for spec in specs if spec["route"] in ("vibe", "vibe-study")]
        self.assertEqual({spec["name"] for spec in mistral}, {"mistral-large", "paid/mistral-large"})
        vibe = [spec for spec in mistral if spec["route"] == "vibe"]
        self.assertEqual({spec["upstream_id"] for spec in vibe}, {"mistral-large-2512"})

    def test_no_latest_alias_is_deployed_and_an_unresolved_one_is_dropped(self):
        specs = plan(usable("vibe", "mistral-large-latest"), usable("vibe", "mistral-small-2603"),
                     usable("vibe-study", "mistral-small-latest", resolves_to="mistral-small-2603"))
        self.assertFalse([spec for spec in specs if spec["upstream_id"].endswith("-latest")])
        self.assertFalse([spec for spec in specs if spec["name"].startswith("paid/mistral-large")])
        self.assertEqual(chain(specs, "mistral-small"), [("vibe-study", 1), ("vibe", 2)])

    def test_the_listing_resolves_a_latest_alias_to_the_release_that_names_it(self):
        # test_routing's listing: "mistral-large-2411" lists "mistral-large-latest" in its aliases
        found = {m["id"]: m for m in sync.list_models(entry("vibe"), ENV, Http())}
        self.assertEqual(found["mistral-large-latest"]["resolves_to"], "mistral-large-2411")
        self.assertNotIn("resolves_to", found["mistral-large-2411"])


class PaidGuards(unittest.TestCase):
    def test_no_paid_group_holds_an_openrouter_deployment(self):
        specs = plan(*[usable(route, model_id) for route, model_id in ALL_FIVE],
                     ("openrouter", "deepseek/deepseek-v4.1-flash:free", "usable", {"free": True}))
        self.assertFalse([spec for spec in specs if spec["name"].startswith("paid/") and spec["route"] == "openrouter"])
        self.assertTrue([spec for spec in specs if spec["name"] == "free/deepseek-v4.1-flash"])

    def test_openrouter_is_refused_as_a_paid_route(self):
        with self.assertRaisesRegex(RuntimeError, "not a paid route"):
            sync.paid_route_order({"paid_route_order": ["openrouter"]})

    def test_the_sync_refuses_an_openrouter_copy_in_a_paid_group(self):
        specs = plan(*[usable(route, model_id) for route, model_id in ALL_FIVE])
        bad = copy.deepcopy(next(spec for spec in specs if spec["name"].startswith("paid/")))
        bad["route"] = "openrouter"
        with self.assertRaisesRegex(RuntimeError, "OpenRouter"):
            sync.assert_paid_groups(specs + [bad], PAID_RULES)

    def test_the_sync_refuses_an_unbudgeted_copy_in_a_paid_group(self):
        specs = plan(*[usable(route, model_id) for route, model_id in ALL_FIVE])
        bad = copy.deepcopy(next(spec for spec in specs if spec["name"].startswith("paid/")))
        bad["litellm_params"].pop("max_budget")
        with self.assertRaisesRegex(RuntimeError, "not budgeted"):
            sync.assert_paid_groups(specs + [bad], PAID_RULES)

    def test_an_opencode_go_free_id_is_in_no_paid_group_only_its_free_group(self):
        specs = plan(*[usable(route, model_id) for route, model_id in ALL_FIVE], ("opencode-go", "beam-free", "usable", {"free": True}))
        free_go = [spec for spec in specs if spec["route"] == "opencode-go" and spec["name"].startswith("free/")]
        self.assertEqual([spec["model_info"]["access_groups"] for spec in free_go], [["free"]])
        self.assertFalse([spec for spec in specs if spec["name"].startswith("paid/") and spec["model_info"]["id"].endswith("-free")])

    def test_a_paid_group_never_reaches_a_free_group(self):
        specs = plan(*[usable(route, model_id) for route, model_id in ALL_FIVE],
                     ("openrouter", "deepseek/deepseek-v4.1-flash:free", "usable", {"free": True}))
        free_ids = {spec["model_info"]["id"] for spec in specs if spec["name"].startswith("free/")}
        for spec in specs:
            if spec["name"].startswith("paid/"):
                self.assertNotIn(spec["model_info"]["id"], free_ids)
                self.assertNotIn(spec["route"], sync.FREE_ONLY)
                self.assertIn("max_budget", spec["litellm_params"])
                self.assertEqual(spec["model_info"]["access_groups"], ["paid"])


class ClaudeAccess(unittest.TestCase):
    def test_a_claude_deployment_carries_only_the_anthropic_group(self):
        specs = plan(usable("anthropic", "claude-sonnet-4-5-20250929"), usable("vibe", "mistral-large-2411"))
        claude = [spec for spec in specs if spec["model_info"]["canonical"].startswith("claude")]
        self.assertTrue(claude)
        for spec in claude:
            self.assertEqual(spec["model_info"]["access_groups"], ["anthropic"])
        self.assertFalse([spec for spec in specs if spec["name"].startswith("paid/claude")])

    def test_the_sync_refuses_a_claude_deployment_in_another_group(self):
        specs = plan(usable("anthropic", "claude-sonnet-4-5-20250929"))
        bad = copy.deepcopy(next(spec for spec in specs if spec["route"] == "anthropic"))
        bad["model_info"]["access_groups"] = ["canonical"]
        with self.assertRaisesRegex(RuntimeError, "anthropic"):
            sync.assert_paid_groups(specs + [bad], PAID_RULES)


GENERATED_TEXT = (ROOT / "config" / "models.generated.yaml").read_text()
GENERATED = json.loads(GENERATED_TEXT[GENERATED_TEXT.index("\n{"):])["model_list"]


class GeneratedChains(unittest.TestCase):
    """The checked-in config/models.generated.yaml, as the proxy loads it."""

    def test_the_two_flash_models_list_the_five_routes_in_the_fixed_order(self):
        for model in ("deepseek-v4.1-flash", "glm-5.3-flash"):
            routes = [e["model_info"]["route"] for e in GENERATED if e["model_name"] == f"paid/{model}"]
            self.assertEqual(routes, PAID_ROUTES, model)

    def test_no_paid_group_holds_openrouter_and_claude_is_only_in_the_anthropic_group(self):
        self.assertFalse([e for e in GENERATED if e["model_name"].startswith("paid/") and e["model_info"]["route"] == "openrouter"])
        self.assertFalse([e["model_name"] for e in GENERATED if e["model_name"].endswith("-latest")])
        claude = [e for e in GENERATED if str(e["model_info"].get("canonical", "")).startswith("claude")]
        self.assertTrue(claude)
        self.assertEqual({tuple(e["model_info"]["access_groups"]) for e in claude}, {("anthropic",)})


@unittest.skipIf(Router is None, "litellm is not importable here; it is in the image (run the suite there)")
class PaidFallthrough(unittest.TestCase):
    """A real LiteLLM Router over the generated paid chain, the upstream mocked: a limited route falls to the next."""

    MODEL = "deepseek-v4.1-flash"

    def router(self, limited):
        _, fallbacks = configured_tiers()
        model_list = []
        for e in GENERATED:
            if e["model_name"] not in (f"paid/{self.MODEL}", f"free/{self.MODEL}"):
                continue
            route = e["model_info"]["route"]
            fails = e["model_name"].startswith("paid/") and route in limited
            model_list.append({"model_name": e["model_name"], "model_info": {"id": e["model_info"]["id"]},
                               "litellm_params": {"model": "openai/mock", "api_key": "k", "order": e["litellm_params"]["order"],
                                                  "mock_response": "litellm.RateLimitError" if fails else f"{e['model_name']}@{route}"}})
        return Router(model_list=model_list, fallbacks=fallbacks, num_retries=0, allowed_fails=0)

    def ask(self, router):
        reply = asyncio.run(router.acompletion(model=f"paid/{self.MODEL}", messages=[{"role": "user", "content": "hi"}]))
        return reply.choices[0].message.content

    def test_a_limited_route_falls_through_to_the_next_paid_route(self):
        for index, route in enumerate(PAID_ROUTES):
            self.assertEqual(self.ask(self.router(set(PAID_ROUTES[:index]))), f"paid/{self.MODEL}@{route}")

    def test_when_every_paid_route_is_limited_the_call_fails_and_never_reaches_a_free_group(self):
        with self.assertRaises(Exception):
            self.ask(self.router(set(PAID_ROUTES)))


if __name__ == "__main__":
    unittest.main()
