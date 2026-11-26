"""The shared free/<canonical> groups: one group per model across every free route (litellm#60)."""
import asyncio
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "catalog"))
sys.path.insert(0, str(ROOT / "tests"))
import sync  # noqa: E402
from test_routing import RULES, catalogue  # noqa: E402

try:  # LiteLLM is in the image and not on a workstation: the Router tests run where it is importable.
    import litellm
    from litellm import Router
except ImportError:  # pragma: no cover
    litellm = Router = None

FREE_RULES = dict(
    RULES,
    free_order={"openrouter": 1, "requesty": 2, "kilo": 3, "nvidia": 4, "ovh": 4},
    cooldowns={"openrouter": 120, "requesty": 3600, "kilo": 120, "nvidia": 120, "ovh": 120},
    accounts={"inferbase": {"routes": ["inferbase"], "max_budget": 0.5, "window": {"days": 1}}},
)
# Nemotron 3 Ultra as five providers spell it: only the vendor prefix and ":free" differ.
NEMOTRON = [
    ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"),
    ("requesty", "nvidia/nemotron-3-ultra-550b-a55b"),
    ("kilo", "nvidia/nemotron-3-ultra-550b-a55b:free"),
    ("nvidia", "nvidia/nemotron-3-ultra-550b-a55b"),
    ("ovh", "Nemotron-3-Ultra-550B-A55B"),
]


def plan(*models, rules=FREE_RULES):
    return sync.plan_groups(catalogue(*models), rules)


def free_models(model_ids=NEMOTRON):
    return [(route, model_id, "usable", {"free": True, "chat_evidence": "listing"}) for route, model_id in model_ids]


class FreeGroup(unittest.TestCase):
    def group(self, specs, name="free/nemotron-3-ultra-550b-a55b"):
        return [spec for spec in specs if spec["name"] == name]

    def test_ids_that_differ_per_provider_meet_in_one_canonical_group(self):
        specs = plan(*free_models())
        found = self.group(specs)
        self.assertEqual([spec["route"] for spec in found], ["openrouter", "requesty", "kilo", "nvidia", "ovh"])
        self.assertEqual({spec["model_info"]["canonical"] for spec in found}, {"nemotron-3-ultra-550b-a55b"})
        # The shared group is the only way to a free copy: no per-route group (owner, 2026-11-26).
        self.assertEqual({spec["name"] for spec in specs}, {"free/nemotron-3-ultra-550b-a55b"})

    def test_a_model_that_is_not_the_same_canonical_name_is_not_merged(self):
        specs = plan(*free_models(NEMOTRON[:2] + [("kilo", "nvidia/nemotron-3-ultra-550b")]))
        self.assertEqual(len(self.group(specs)), 2)
        self.assertEqual(len(self.group(specs, "free/nemotron-3-ultra-550b")), 1)

    def test_an_alias_unifies_an_id_the_stripping_cannot(self):
        rules = dict(FREE_RULES, aliases={"nemo-ultra": "nemotron-3-ultra-550b-a55b"})
        specs = plan(*free_models(NEMOTRON[:1] + [("kilo", "nemo-ultra")]), rules=rules)
        self.assertEqual([spec["route"] for spec in self.group(specs)], ["openrouter", "kilo"])

    def test_deployments_are_ordered_with_their_own_id_and_no_fixed_cooldown(self):
        found = self.group(plan(*free_models()))
        self.assertEqual([spec["order"] for spec in found], [1, 2, 3, 4, 4])
        # No cooldown_time: LiteLLM cools a 429 for the provider's Retry-After (a fixed one would override it).
        for spec in found:
            self.assertNotIn("cooldown_time", spec["litellm_params"])
        ids = [spec["model_info"]["id"] for spec in found]
        self.assertEqual(len(set(ids)), 5)
        self.assertEqual(ids[0], "free/openrouter/nvidia/nemotron-3-ultra-550b-a55b:free")
        for spec in found:
            self.assertNotIn("allowed_fails", spec["litellm_params"])
            self.assertNotIn("allowed_fails_policy", spec["model_info"])
            self.assertEqual(spec["model_info"]["access_groups"], ["free"])

    def test_a_route_the_order_rule_does_not_name_comes_last(self):
        models = free_models(NEMOTRON[:1] + [("groq", "nemotron-3-ultra-550b-a55b")])
        self.assertEqual([spec["order"] for spec in self.group(plan(*models))], [1, 5])

    def test_every_deployment_is_zero_cost_and_nothing_paid_is_in_the_group(self):
        specs = plan(*free_models(), ("vibe", "nemotron-3-ultra-550b-a55b", "usable", {}),
                     ("vibe-api", "nemotron-3-ultra-550b-a55b", "usable", {}),
                     ("inferbase", "nemotron-3-ultra-550b-a55b", "usable", {"free": True, "price": {"input": 1e-6, "output": 2e-6}}))
        for spec in self.group(specs):
            self.assertEqual((spec["litellm_params"]["input_cost_per_token"], spec["litellm_params"]["output_cost_per_token"]), (0, 0))
            self.assertNotIn(spec["route"], ("vibe", "vibe-api", "inferbase"))
            self.assertNotIn("max_budget", spec["litellm_params"])

    def test_a_free_id_on_a_paid_route_joins_with_an_explicit_zero_cost(self):
        specs = plan(("opencode-go", "beam-free", "usable", {"free": True}), ("opencode-go", "deepseek-v4.1-flash", "usable", {}),
                     ("openrouter", "beam-free", "usable", {"free": True}))
        # "-free" only marks the free variant: both copies meet in free/beam.
        found = self.group(specs, "free/beam")
        self.assertEqual({spec["route"] for spec in found}, {"opencode-go", "openrouter"})
        self.assertTrue(all(spec["litellm_params"]["input_cost_per_token"] == 0 for spec in found))
        # The paid id of the same route is not in any free group.
        self.assertEqual(self.group(specs, "free/deepseek-v4.1-flash"), [])

    def test_a_paid_deployment_joining_a_free_group_fails_the_plan(self):
        # Inferbase bills against a dollar allowance. If a rule edit made it a free-group route, the copy
        # would carry its real price and the sync must refuse the whole plan.
        models = free_models(NEMOTRON[:1]) + [("inferbase", "nvidia/nemotron-3-ultra-550b-a55b", "usable",
                                               {"free": True, "price": {"input": 1e-6, "output": 2e-6}})]
        with mock.patch.object(sync, "FREE_GROUP_ROUTES", sync.FREE_GROUP_ROUTES | {"inferbase"}):
            with self.assertRaisesRegex(RuntimeError, r"inferbase.*may not join free/nemotron.*explicit zero"):
                plan(*models)

    def test_the_assertion_names_each_way_a_deployment_can_be_paid(self):
        good = self.group(plan(*free_models(NEMOTRON[:1])))[0]
        sync.assert_free_groups([good])
        for label, edit in {
            "unknown cost": lambda s: s["litellm_params"].pop("input_cost_per_token"),
            "priced": lambda s: s["litellm_params"].update(output_cost_per_token=1e-6),
            "budget": lambda s: s["litellm_params"].update(max_budget=1),
            "route access group": lambda s: s["model_info"].update(access_groups=["free", "canonical"]),
            "paid route": lambda s: s.update(route="vibe"),
        }.items():
            bad = copy.deepcopy(good)
            edit(bad)
            with self.assertRaises(RuntimeError, msg=label):
                sync.assert_free_groups([bad])
        # A deployment outside free/* is not this assertion's business.
        paid = copy.deepcopy(good)
        paid.update(name="mistral-large")
        paid["litellm_params"]["input_cost_per_token"] = 1e-6
        sync.assert_free_groups([paid])

    def test_main_fails_the_run_when_the_plan_is_refused(self):
        with mock.patch.object(sync, "plan_groups", side_effect=RuntimeError("x may not join free/y")):
            with mock.patch.object(sync, "build", return_value=({"providers": {}}, {"listed": 1, "probed": 0, "route_errors": []})):
                with mock.patch.object(sync, "load_rules", return_value=FREE_RULES):
                    self.assertEqual(sync.main(["--output", "/nonexistent/out.json"], env={}, http=mock.Mock()), 1)

    def test_no_chain_reachable_from_a_free_group_ends_at_a_paid_deployment(self):
        """A free/* group has no fallbacks of its own, and no tier chain it is in leaves its ceiling."""
        specs = plan(*free_models())
        free_names = {spec["name"] for spec in specs if spec["name"].startswith("free/")}
        # Every name in the group's own deployments is the free group: the router has nothing paid to fall to.
        for spec in specs:
            if spec["name"] in free_names:
                self.assertEqual(spec["model_info"]["access_groups"], ["free"])
        config = (ROOT / "config" / "config.yaml").read_text()
        live = "\n".join(line for line in config.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("default_fallbacks", live)
        # No fallbacks entry is keyed on a free/* group (tests/test_tiers.py covers the tier chains).
        self.assertNotRegex(live, r'\{"?free/')
        self.assertNotIn("default_fallbacks", (ROOT / "config" / "models.generated.yaml").read_text())
        self.assertNotIn("default_fallbacks", json.dumps(sync.load_rules()))


@unittest.skipIf(Router is None, "litellm is not importable here; it is in the image (run the suite there)")
class FreeGroupRouter(unittest.TestCase):
    """The planned deployments loaded into LiteLLM's own Router, with the upstream mocked."""

    def router(self, failing):
        found = [spec for spec in plan(*free_models()) if spec["name"] == "free/nemotron-3-ultra-550b-a55b"]
        model_list = []
        for spec in found:
            params = {k: v for k, v in spec["litellm_params"].items() if k != "litellm_credential_name"}
            params.update(model="openai/mock", api_key="k", mock_response="litellm.RateLimitError" if spec["route"] in failing else spec["route"])
            model_list.append({"model_name": spec["name"], "litellm_params": params, "model_info": {"id": spec["model_info"]["id"]}})
        return Router(model_list=model_list, num_retries=2, routing_strategy="simple-shuffle", enable_pre_call_checks=True)

    def cooled(self, router):
        """The ids LiteLLM has cooled down, read from the Router's own cooldown cache."""
        ids = [spec["model_info"]["id"] for spec in plan(*free_models()) if spec["name"].startswith("free/")]
        return sorted(model_id for model_id, _ in router.health().cooldowns.get_active_cooldowns(model_ids=ids, parent_otel_span=None))

    def ask(self, router):
        reply = asyncio.run(router.acompletion(model="free/nemotron-3-ultra-550b-a55b", messages=[{"role": "user", "content": "hi"}]))
        return reply.choices[0].message.content

    def test_a_429_cools_only_that_copy_and_the_next_free_copy_serves(self):
        router = self.router({"openrouter"})
        self.assertEqual(self.ask(router), "requesty")
        cooled = self.cooled(router)
        self.assertEqual(cooled, ["free/openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"])
        # The cooled copy is skipped from now on; the next copy keeps serving.
        self.assertEqual(self.ask(router), "requesty")
        self.assertEqual(self.cooled(router), cooled)

    def test_a_second_429_walks_to_the_next_copy(self):
        router = self.router({"openrouter", "requesty"})
        self.assertEqual(self.ask(router), "kilo")
        cooled = set(self.cooled(router))
        self.assertEqual({i.split("/")[1] for i in cooled}, {"openrouter", "requesty"})

    def test_every_free_copy_cooled_fails_instead_of_reaching_anything_else(self):
        router = self.router({"openrouter", "requesty", "kilo", "nvidia", "ovh"})
        with self.assertRaises(Exception):
            self.ask(router)


if __name__ == "__main__":
    unittest.main()
