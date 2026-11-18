"""The tier groups of catalog/routing.json (litellm#61): definition, rendering, config load and chains."""
import asyncio
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "catalog"))
sys.path.insert(0, str(ROOT / "tests"))
import sync  # noqa: E402
from test_free_groups import FREE_RULES, free_models, plan  # noqa: E402

try:  # in the image, not on a workstation
    import litellm
    from litellm import Router
except ImportError:  # pragma: no cover
    litellm = Router = None

RULES = sync.load_rules()
CONFIG = (ROOT / "config" / "config.yaml").read_text()
TIERS = ["tier-free", "tier-vibe", "tier-go-cheap", "tier-strong-open", "tier-review"]


def live_config():
    return "\n".join(line for line in CONFIG.splitlines() if not line.lstrip().startswith("#"))


def configured_tiers():
    """The alias map and fallbacks exactly as config/config.yaml's text holds them (JSON per line)."""
    block = CONFIG[CONFIG.index(sync.TIERS_BEGIN):CONFIG.index(sync.TIERS_END)].splitlines()[1:]
    alias, fallbacks, target = {}, [], None
    for line in block:
        text = line.strip()
        if text in ("model_group_alias:", "fallbacks:"):
            target = text
        elif target == "model_group_alias:":
            key, value = text.split(": ", 1)
            alias[json.loads(key)] = json.loads(value)
        else:
            fallbacks.append(json.loads(text[2:]))
    return alias, fallbacks


class Definition(unittest.TestCase):
    def test_the_five_tiers_are_defined_declaratively(self):
        self.assertEqual(sorted(RULES["tier_groups"]), sorted(TIERS))
        for name, tier in RULES["tier_groups"].items():
            self.assertIn(tier["ceiling"], RULES["tier_cost_classes"], name)
            for step in tier["steps"]:
                self.assertTrue(("model" in step and "cost" in step) != ("tier" in step), (name, step))

    def test_the_real_rules_validate_and_no_tier_names_an_opt_in_model(self):
        sync.validate_tiers(RULES)
        for name, chain in sync.tier_chains(RULES).items():
            for step in chain:
                self.assertNotIn(sync.canonical_name(None, step["model"].split("/", 1)[1], RULES), RULES["opt_in_only"], (name, step))
        # The three owner-gated models are named, so a rule edit cannot quietly drop one.
        self.assertEqual(sorted(RULES["opt_in_only"]), ["glm-5.3", "gpt-6-luna", "kimi-k3.1-pro"])

    def test_the_cost_classes_of_each_tier(self):
        chains = {name: [step["cost"] for step in chain] for name, chain in sync.tier_chains(RULES).items()}
        self.assertEqual(set(chains["tier-free"]), {"free"})
        self.assertEqual(chains["tier-vibe"][0], "vibe")
        self.assertEqual(sync.tier_chains(RULES)["tier-vibe"][0]["model"], "vibe/deepseek-v4-1-flash-1017")
        self.assertEqual(set(chains["tier-go-cheap"]), {"go"})
        # review: free models, then Vibe Flash, then Go Flash, in that order.
        review = chains["tier-review"]
        self.assertEqual(review, sorted(review, key=["free", "vibe", "go"].index))
        self.assertEqual(set(review), {"free", "vibe", "go"})
        # The ladder is free -> Vibe -> Go: vibe falls to go, and go never climbs back.
        self.assertEqual(sorted(set(chains["tier-vibe"]), key=["free", "vibe", "go"].index), ["vibe", "go"])

    def test_validation_refuses_each_way_a_tier_can_go_wrong(self):
        def edit(**kw):
            rules = json.loads(json.dumps(RULES))
            for name, fn in kw.items():
                fn(rules["tier_groups"][name.replace("_", "-")])
            return rules

        bad = {
            "a paid step in tier-free": edit(tier_free=lambda t: t["steps"].append({"model": "vibe/mistral-small-latest", "cost": "vibe"})),
            "a wrong class": edit(tier_free=lambda t: t["steps"].append({"model": "opencode-go/x", "cost": "free"})),
            "a free name declared paid": edit(tier_vibe=lambda t: t["steps"].append({"model": "free/x", "cost": "go"})),
            "an opt-in model": edit(tier_go_cheap=lambda t: t["steps"].append({"model": "opencode-go/kimi-k3.1-pro", "cost": "go"})),
            "an opt-in model by alias": edit(tier_vibe=lambda t: t["steps"].append({"model": "vibe/zai-glm-5-3", "cost": "vibe"})),
            "an unknown prefix": edit(tier_go_cheap=lambda t: t["steps"].append({"model": "anthropic/claude-x", "cost": "go"})),
            "a step the key cannot reach": edit(tier_go_cheap=lambda t: t["key_models"].remove("opencode-go/*")),
            "no own name in key_models": edit(tier_go_cheap=lambda t: t["key_models"].remove("tier-go-cheap")),
            "a cycle": edit(tier_go_cheap=lambda t: t["steps"].append({"tier": "tier-strong-open"})),
            "an unknown tier": edit(tier_go_cheap=lambda t: t["steps"].append({"tier": "tier-nope"})),
            "a ceiling above the classes": edit(tier_free=lambda t: t.update(ceiling="claude")),
        }
        for label, rules in bad.items():
            with self.assertRaises(RuntimeError, msg=label):
                sync.validate_tiers(rules)

    def test_a_tier_above_its_ceiling_is_refused_even_when_declared_honestly(self):
        rules = json.loads(json.dumps(RULES))
        rules["tier_groups"]["tier-review"]["ceiling"] = "vibe"
        with self.assertRaisesRegex(RuntimeError, "above the tier's ceiling"):
            sync.validate_tiers(rules)


class Rendering(unittest.TestCase):
    def test_config_yaml_holds_exactly_what_routing_json_renders(self):
        self.assertIn(sync.render_tier_block(RULES), CONFIG)
        self.assertEqual(sync.splice_tier_block(CONFIG, RULES), CONFIG)

    def test_each_tier_is_an_alias_of_its_first_step_with_the_rest_as_ordered_fallbacks(self):
        alias, fallbacks = configured_tiers()
        chains = sync.tier_chains(RULES)
        for name in TIERS:
            self.assertEqual(alias[name], chains[name][0]["model"])
        keyed = {key: value for entry in fallbacks for key, value in entry.items()}
        for name in TIERS:
            self.assertEqual(keyed[name], [step["model"] for step in chains[name][1:]])

    def test_no_default_fallbacks_and_nothing_keyed_on_a_free_group(self):
        self.assertNotIn("default_fallbacks", live_config())
        self.assertNotIn("default_fallbacks", (ROOT / "config" / "models.generated.yaml").read_text())
        _, fallbacks = configured_tiers()
        keyed = {key for entry in fallbacks for key in entry}
        self.assertEqual(keyed, set(TIERS))
        self.assertFalse(any(key.startswith("free/") for key in keyed))

    def test_tier_free_and_tier_review_reach_a_paid_deployment_only_through_declared_paid_steps(self):
        chains = sync.tier_chains(RULES)
        self.assertTrue(all(step["model"].startswith("free/") for step in chains["tier-free"]))
        for step in chains["tier-review"]:
            self.assertEqual(step["cost"] != "free", not step["model"].startswith("free/"))
        # The tiers' own names are in no catalogue group: nothing the sync plans can shadow one.
        names = {spec["name"] for spec in plan(*free_models())}
        self.assertFalse(names & set(TIERS))

    def test_the_tier_block_is_spliced_and_a_missing_marker_is_an_error(self):
        self.assertEqual(sync.splice_tier_block(CONFIG.replace("model_group_alias:", "model_group_alias:", 1), RULES), CONFIG)
        with self.assertRaises(RuntimeError):
            sync.splice_tier_block("router_settings: {}", RULES)

    def test_missing_free_steps_are_listed_for_the_run(self):
        specs = plan(*free_models(), rules=FREE_RULES)
        missing = sync.missing_tier_steps(specs, RULES)
        # This plan holds Nemotron 3 Ultra, which tier-review starts on, and none of the other free steps.
        self.assertNotIn("free/nemotron-3-ultra-550b-a55b", missing)
        self.assertIn("free/hy4-flash-free", missing)
        self.assertTrue(all(name.startswith("free/") for name in missing))

    def test_render_tiers_rewrites_only_the_block(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            stale = CONFIG.replace('"tier-free": "free/hy4-flash-free"', '"tier-free": "stale"')
            path.write_text(stale)
            with mock.patch.object(sync, "CONFIG_PATH", str(path)):
                self.assertEqual(sync.main(["--render-tiers"]), 0)
            self.assertEqual(path.read_text(), CONFIG)

    def test_the_sync_refuses_to_run_with_broken_tiers(self):
        from unittest import mock
        rules = json.loads(json.dumps(RULES))
        rules["tier_groups"]["tier-free"]["steps"].append({"model": "vibe/x", "cost": "vibe"})
        with mock.patch.object(sync, "build", return_value=({"providers": {}}, {"listed": 1, "probed": 0, "route_errors": []})):
            with mock.patch.object(sync, "load_rules", return_value=rules):
                self.assertEqual(sync.main(["--output", "/nonexistent/out.json"], env={}, http=mock.Mock()), 1)


class Access(unittest.TestCase):
    def test_the_free_key_reaches_tier_free_with_the_free_group_and_the_tier_name(self):
        keys = RULES["tier_groups"]["tier-free"]["key_models"]
        self.assertEqual(keys, ["free", "tier-free"])

    def test_every_key_models_list_reaches_its_chain_and_reaches_nothing_more_expensive(self):
        rank = RULES["tier_cost_classes"].index
        for name, tier in RULES["tier_groups"].items():
            wanted = {step["model"] for step in sync.tier_chains(RULES)[name]}
            for model in wanted:
                self.assertTrue(any(model == k or (k.endswith("/*") and model.startswith(k[:-1])) or (k == "free" and model.startswith("free/"))
                                    for k in tier["key_models"]), (name, model))
            # No key holds a route above the tier's ceiling.
            ceiling = rank(tier["ceiling"])
            self.assertTrue(not any(k.startswith("opencode-go") for k in tier["key_models"]) or ceiling >= rank("go"), name)
            self.assertTrue(not any(k.startswith("vibe") for k in tier["key_models"]) or ceiling >= rank("vibe"), name)


@unittest.skipIf(Router is None, "litellm is not importable here; it is in the image (run the suite there)")
class ConfigLoad(unittest.TestCase):
    """Load the rendered alias and fallbacks into LiteLLM's Router and walk every chain, upstream mocked."""

    def router(self, chain, failing):
        alias, fallbacks = configured_tiers()
        model_list = []
        for step in chain:
            model_list.append({"model_name": step["model"], "litellm_params": {
                "model": "openai/mock", "api_key": "k",
                "mock_response": "litellm.RateLimitError" if step["model"] in failing else step["model"]}})
        return Router(model_list=model_list, model_group_alias=alias, fallbacks=fallbacks, num_retries=0)

    def ask(self, router, tier):
        reply = asyncio.run(router.acompletion(model=tier, messages=[{"role": "user", "content": "hi"}]))
        return reply.choices[0].message.content

    def test_every_chain_loads_and_walks_in_order_when_each_step_is_rate_limited_in_turn(self):
        for tier, chain in sync.tier_chains(RULES).items():
            models = [step["model"] for step in chain]
            for index, model in enumerate(models):
                failing = set(models[:index])
                self.assertEqual(self.ask(self.router(chain, failing), tier), model, (tier, index))

    def test_a_chain_with_every_step_rate_limited_fails_and_tier_free_never_reaches_a_paid_step(self):
        for tier, chain in sync.tier_chains(RULES).items():
            models = [step["model"] for step in chain]
            with self.assertRaises(Exception, msg=tier):
                self.ask(self.router(chain, set(models)), tier)
        # Only the tier-free chain's own models exist in that router: nothing paid is there to be reached.
        free_chain = sync.tier_chains(RULES)["tier-free"]
        self.assertTrue(all(step["model"].startswith("free/") for step in free_chain))

    def test_a_missing_first_group_falls_through_to_the_next_step(self):
        chain = sync.tier_chains(RULES)["tier-free"]
        router = self.router(chain[1:], set())  # free/hy4-flash-free has no deployment yet
        self.assertEqual(self.ask(router, "tier-free"), chain[1]["model"])

    def test_a_route_wildcard_serves_a_tier_step(self):
        alias, fallbacks = configured_tiers()
        router = Router(model_list=[{"model_name": "vibe/*", "litellm_params": {"model": "openai/*", "api_key": "k", "mock_response": "vibe"}}],
                        model_group_alias=alias, fallbacks=fallbacks, num_retries=0)
        self.assertEqual(self.ask(router, "tier-vibe"), "vibe")


if __name__ == "__main__":
    unittest.main()
