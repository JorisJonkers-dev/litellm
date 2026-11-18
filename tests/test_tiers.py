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
        self.assertEqual(sync.tier_chains(RULES)["tier-vibe"][0]["model"], "vibe-tier/deepseek-v4-1-flash-1017")
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
            "a paid step in tier-free": edit(tier_free=lambda t: t["steps"].append({"model": "vibe-tier/mistral-small-2603", "cost": "vibe"})),
            "a wrong class": edit(tier_free=lambda t: t["steps"].append({"model": "opencode-go-tier/x", "cost": "free"})),
            "a free name declared paid": edit(tier_vibe=lambda t: t["steps"].append({"model": "free/x", "cost": "go"})),
            "an opt-in model": edit(tier_go_cheap=lambda t: t["steps"].append({"model": "opencode-go-tier/kimi-k3.1-pro", "cost": "go"})),
            "an opt-in model by alias": edit(tier_vibe=lambda t: t["steps"].append({"model": "vibe-tier/zai-glm-5-3", "cost": "vibe"})),
            "a route wildcard step (no budget on a wildcard)": edit(tier_go_cheap=lambda t: t["steps"].append({"model": "opencode-go/mimo-v2.6-flash", "cost": "go"})),
            "an unknown prefix": edit(tier_go_cheap=lambda t: t["steps"].append({"model": "anthropic/claude-x", "cost": "go"})),
            "a step the key cannot reach": edit(tier_go_cheap=lambda t: t["key_models"].remove("opencode-go-tier")),
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
        self.assertIn("vibe-tier/zai-glm-5-3-flash", missing)

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
        rules["tier_groups"]["tier-free"]["steps"].append({"model": "vibe-tier/x", "cost": "vibe"})
        with mock.patch.object(sync, "build", return_value=({"providers": {}}, {"listed": 1, "probed": 0, "route_errors": []})):
            with mock.patch.object(sync, "load_rules", return_value=rules):
                self.assertEqual(sync.main(["--output", "/nonexistent/out.json"], env={}, http=mock.Mock()), 1)


class Access(unittest.TestCase):
    def test_the_free_key_reaches_tier_free_with_the_free_group_and_the_tier_name(self):
        keys = RULES["tier_groups"]["tier-free"]["key_models"]
        self.assertEqual(keys, ["free", "tier-free"])

    def test_every_key_models_list_reaches_its_chain_and_no_route_wildcard(self):
        rank = RULES["tier_cost_classes"].index
        for name, tier in RULES["tier_groups"].items():
            for step in sync.tier_chains(RULES)[name]:
                self.assertTrue(step["model"] in tier["key_models"] or step["model"].split("/", 1)[0] in tier["key_models"]
                                or (step["cost"] == "free" and "free" in tier["key_models"]), (name, step))
            # A tier key holds access groups and tier names, never a route wildcard: a wildcard has no budget.
            self.assertFalse([k for k in tier["key_models"] if "*" in k or k in ("vibe", "opencode-go")], name)
            # No key holds a route above the tier's ceiling.
            ceiling = rank(tier["ceiling"])
            self.assertTrue("opencode-go-tier" not in tier["key_models"] or ceiling >= rank("go"), name)
            self.assertTrue("vibe-tier" not in tier["key_models"] or ceiling >= rank("vibe"), name)


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


BUDGETED = dict(RULES, budgets={"opencode-go": {"max_budget": 0.05, "budget_duration": "30d"},
                                "vibe": {"max_budget": 0.05, "budget_duration": "30d"}})
PAID_MODELS = [("opencode-go", "mimo-v2.6-flash", "usable", {}), ("opencode-go", "qwen3.8-flash", "usable", {}),
               ("vibe", "zai-glm-5-3-flash", "usable", {}), ("vibe", "mistral-small-2603", "usable", {})]


class TierCopies(unittest.TestCase):
    def specs(self, rules=BUDGETED, models=PAID_MODELS):
        from test_routing import catalogue
        return sync.plan_groups(catalogue(*models), rules)

    def test_a_paid_step_is_a_copy_of_the_routes_budgeted_deployment_with_its_id(self):
        specs = self.specs()
        copies = {s["name"]: s for s in specs if s["name"].split("/")[0] in sync.TIER_ROUTES}
        self.assertEqual(sorted(copies), ["opencode-go-tier/mimo-v2.6-flash", "opencode-go-tier/qwen3.8-flash",
                                          "vibe-tier/mistral-small-2603", "vibe-tier/zai-glm-5-3-flash"])
        by_id = {s["model_info"]["id"]: s for s in specs if s["name"] not in copies}
        for name, copy in copies.items():
            origin = by_id[copy["model_info"]["id"]]  # the same id: one spend counter
            self.assertEqual(copy["litellm_params"]["max_budget"], 0.05)
            self.assertEqual(copy["litellm_params"]["max_budget"], origin["litellm_params"]["max_budget"])
            self.assertEqual(copy["model_info"]["access_groups"], [name.split("/")[0]])
            self.assertEqual(copy["litellm_params"]["model"], origin["litellm_params"]["model"])

    def test_a_step_with_no_deployment_gets_no_copy_and_is_never_a_wildcard(self):
        names = {s["name"] for s in self.specs()}
        self.assertNotIn("opencode-go-tier/deepseek-v4.1-flash", names)
        self.assertIn("opencode-go-tier/deepseek-v4.1-flash", sync.missing_tier_steps(self.specs(), RULES))
        # No tier step name falls under a wildcard of config.yaml, which carries no budget.
        wild = [line.split("model_name:")[1].strip() for line in CONFIG.splitlines() if "model_name:" in line and line.strip().endswith("/*") or "- model_name: " in line and "/*" in line]
        for chain in sync.tier_chains(RULES).values():
            for step in chain:
                self.assertFalse(any(step["model"].startswith(w[:-1]) for w in wild if w.endswith("/*")), step)

    def test_the_sync_refuses_a_tier_copy_without_its_routes_budget(self):
        specs = self.specs()
        copy = next(s for s in specs if s["name"].startswith("vibe-tier/"))
        copy["litellm_params"].pop("max_budget")
        with self.assertRaisesRegex(RuntimeError, "may not serve vibe-tier"):
            sync.assert_tier_copies(specs, BUDGETED)
        specs = self.specs()
        copy = next(s for s in specs if s["name"].startswith("vibe-tier/"))
        copy["model_info"]["id"] = "vibe/elsewhere"
        with self.assertRaisesRegex(RuntimeError, "not a copy"):
            sync.assert_tier_copies(specs, BUDGETED)
        # No budget in the rules for the route: the plan fails rather than deploy an uncapped tier step.
        with self.assertRaisesRegex(RuntimeError, "route's budget"):
            self.specs(dict(RULES, budgets={}))

    def test_the_generated_file_holds_a_budgeted_copy_for_every_paid_step_the_catalogue_serves(self):
        text = (ROOT / "config" / "models.generated.yaml").read_text()
        entries = json.loads(text[text.index("{"):])["model_list"]
        by_id = {e["model_info"]["id"]: e for e in entries if e["model_name"].split("/")[0] not in sync.TIER_ROUTES}
        copies = [e for e in entries if e["model_name"].split("/")[0] in sync.TIER_ROUTES]
        self.assertTrue(copies)
        for entry in copies:
            origin = by_id[entry["model_info"]["id"]]
            route = sync.TIER_ROUTES[entry["model_name"].split("/")[0]]
            budget = RULES["budgets"][route]
            self.assertEqual((entry["litellm_params"]["max_budget"], entry["litellm_params"]["budget_duration"]),
                             (budget["max_budget"], budget["budget_duration"]), entry["model_name"])
            self.assertEqual(origin["litellm_params"]["max_budget"], entry["litellm_params"]["max_budget"])
            self.assertEqual(entry["model_info"]["access_groups"], [entry["model_name"].split("/")[0]])


SCENARIOS = {
    # What each call of a scenario is asked, in order. Every scenario runs in a process of its own: LiteLLM
    # keeps budget state process-wide, and only the first Router built in a process enforces it.
    "go_budget_spent": ["tier-go-cheap", "tier-go-cheap"],
    "direct_spend_then_tier": ["mimo-v2.6-flash", "tier-go-cheap"],
    "tier_spend_then_direct": ["tier-go-cheap", "mimo-v2.6-flash"],
    "ladder": ["tier-vibe"] * 4,
}


def run_scenario(name):
    """The answers of one scenario against the planned deployments, upstream mocked: a name, or "ERR"."""
    from test_routing import catalogue
    specs = sync.plan_groups(catalogue(*PAID_MODELS), BUDGETED)
    alias, fallbacks = configured_tiers()
    model_list = []
    for spec in specs:
        params = {k: v for k, v in spec["litellm_params"].items() if k != "litellm_credential_name"}
        # Every token costs 0.01, so one answer spends more than the 0.05 budget.
        params.update(model="openai/mock", api_key="k", mock_response=spec["name"], input_cost_per_token=0.01, output_cost_per_token=0.01)
        model_list.append({"model_name": spec["name"], "litellm_params": params, "model_info": {"id": spec["model_info"]["id"]}})

    async def main():
        router = Router(model_list=model_list, model_group_alias=alias, fallbacks=fallbacks, num_retries=0,
                        optional_pre_call_checks=["router_budget_limiting"])
        answers = []
        for model in SCENARIOS[name]:
            try:
                reply = await router.acompletion(model=model, messages=[{"role": "user", "content": "hi"}])
                answers.append(reply.choices[0].message.content)
            except Exception:
                answers.append("ERR")
            await asyncio.sleep(0.3)  # the spend is recorded by a callback after the answer
        return answers

    return asyncio.run(main())


@unittest.skipIf(Router is None, "litellm is not importable here; it is in the image (run the suite there)")
class TierBudgets(unittest.TestCase):
    """The budget of a route is enforced for a tier call: with it spent, the tier does not reach the route."""

    def answers(self, name):
        import subprocess
        done = subprocess.run([sys.executable, "-W", "ignore", __file__, name], capture_output=True, text=True, check=True)
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_with_the_go_budget_spent_a_tier_go_cheap_call_does_not_reach_go(self):
        self.assertEqual(self.answers("go_budget_spent"), ["opencode-go-tier/mimo-v2.6-flash", "ERR"])

    def test_spend_through_the_route_group_counts_against_the_tier_and_the_other_way_round(self):
        self.assertEqual(self.answers("direct_spend_then_tier"), ["mimo-v2.6-flash", "ERR"])
        self.assertEqual(self.answers("tier_spend_then_direct"), ["opencode-go-tier/mimo-v2.6-flash", "ERR"])

    def test_the_ladder_falls_from_vibe_to_go_and_stops_when_both_are_spent(self):
        # deepseek (Vibe) and the first two Go steps have no deployment in this catalogue and are skipped.
        self.assertEqual(self.answers("ladder"), ["vibe-tier/zai-glm-5-3-flash", "vibe-tier/mistral-small-2603",
                                                  "opencode-go-tier/mimo-v2.6-flash", "ERR"])

    def test_a_tier_call_never_reaches_an_unbudgeted_wildcard(self):
        wildcard = {"model_name": "opencode-go/*", "litellm_params": {"model": "openai/*", "api_key": "k", "mock_response": "WILDCARD"}}
        alias, fallbacks = configured_tiers()
        bare = Router(model_list=[wildcard], model_group_alias=alias, fallbacks=fallbacks, num_retries=0)
        with self.assertRaises(Exception):
            asyncio.run(bare.acompletion(model="tier-go-cheap", messages=[{"role": "user", "content": "hi"}]))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in SCENARIOS:
        print(json.dumps(run_scenario(sys.argv[1])))
    else:
        unittest.main()
