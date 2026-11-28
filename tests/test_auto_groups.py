"""auto/<model> groups and the family aliases (litellm#941)."""
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
from test_free_groups import FREE_RULES, free_models  # noqa: E402
from test_paid_groups import ALL_FIVE, PAID_RULES, plan, usable  # noqa: E402
from test_routing import catalogue  # noqa: E402
from test_tiers import configured_tiers  # noqa: E402

try:  # in the image, not on a workstation
    from litellm import Router
    from litellm.proxy.auth.auth_checks import can_object_call_model
except ImportError:  # pragma: no cover
    Router = can_object_call_model = None

RULES = sync.load_rules()
GENERATED_TEXT = (ROOT / "config" / "models.generated.yaml").read_text()
GENERATED = json.loads(GENERATED_TEXT[GENERATED_TEXT.index("\n{"):])["model_list"]
NAMES = sync.group_names(GENERATED_TEXT)
FREE_OPENROUTER = ("openrouter", "deepseek/deepseek-v4.1-flash:free", "usable", {"free": True})
FREE_GROQ = ("groq", "deepseek-v4.1-flash", "usable", {})


def both(*extra):
    """A catalogue where deepseek-v4.1-flash has a free copy and a paid chain."""
    return plan(*[usable(route, model_id) for route, model_id in ALL_FIVE], FREE_OPENROUTER, *extra)


def group(specs, name):
    return [spec for spec in specs if spec["name"] == name]


class AutoGroups(unittest.TestCase):
    def test_a_model_with_a_free_and_a_paid_chain_has_an_auto_group_of_its_free_copies(self):
        specs = both()
        auto = group(specs, "auto/deepseek-v4.1-flash")
        free = group(specs, "free/deepseek-v4.1-flash")
        self.assertTrue(auto)
        self.assertEqual([spec["model_info"]["id"] for spec in auto], [spec["model_info"]["id"] for spec in free])
        for spec in auto:
            self.assertEqual(spec["model_info"]["access_groups"], [sync.AUTO_ACCESS_GROUP])
            self.assertEqual((spec["litellm_params"]["input_cost_per_token"], spec["litellm_params"]["output_cost_per_token"]), (0, 0))
            self.assertNotIn("max_budget", spec["litellm_params"])
        # the free group keeps its own access group: auto copies do not widen it
        self.assertEqual({tuple(spec["model_info"]["access_groups"]) for spec in free}, {("free",)})

    def test_a_model_served_only_free_or_only_paid_has_no_auto_group(self):
        specs = plan(usable("vibe", "mistral-large-2411"), FREE_OPENROUTER)
        self.assertFalse([spec for spec in specs if spec["name"].startswith("auto/mistral-large")])
        self.assertFalse(group(plan(*[usable(route, model_id) for route, model_id in ALL_FIVE]), "auto/deepseek-v4.1-flash"))
        self.assertFalse(group(plan(FREE_OPENROUTER), "auto/deepseek-v4.1-flash"))

    def test_no_auto_deployment_is_paid_or_outside_its_free_group(self):
        specs = both()
        sync.assert_auto_groups(specs)
        paid_ids = {spec["model_info"]["id"] for spec in specs if spec["name"].startswith("paid/")}
        for spec in specs:
            if spec["name"].startswith("auto/"):
                self.assertNotIn(spec["model_info"]["id"], paid_ids)
                self.assertNotIn("account", spec["model_info"])

    def test_the_sync_refuses_an_auto_deployment_that_is_not_a_free_copy(self):
        specs = both()
        paid = copy.deepcopy(next(spec for spec in specs if spec["name"] == "paid/deepseek-v4.1-flash"))
        paid["name"] = "auto/deepseek-v4.1-flash"
        paid["model_info"]["access_groups"] = [sync.AUTO_ACCESS_GROUP]
        with self.assertRaisesRegex(RuntimeError, "not a free/ deployment"):
            sync.assert_auto_groups(specs + [paid])
        wide = copy.deepcopy(next(spec for spec in specs if spec["name"].startswith("auto/")))
        wide["model_info"]["access_groups"] = ["free"]
        with self.assertRaisesRegex(RuntimeError, "public-repo"):
            sync.assert_auto_groups([spec for spec in specs if not spec["name"].startswith("auto/")] + [wide])
        priced = copy.deepcopy(next(spec for spec in specs if spec["name"].startswith("auto/")))
        priced["litellm_params"]["max_budget"] = 5
        with self.assertRaisesRegex(RuntimeError, "params differ"):
            sync.assert_auto_groups([spec for spec in specs if not spec["name"].startswith("auto/")] + [priced])

    def test_the_fallback_is_the_paid_group_of_the_same_model_only(self):
        names = [spec["name"] for spec in both()]
        self.assertEqual(sync.auto_fallbacks(names, {}), [{"auto/deepseek-v4.1-flash": ["paid/deepseek-v4.1-flash"]}])
        self.assertEqual(sync.auto_fallbacks([n for n in names if n != "paid/deepseek-v4.1-flash"], {}), [])

    def test_the_generated_file_and_config_hold_them_consistently(self):
        autos = {name for name in NAMES if name.startswith("auto/")}
        self.assertTrue(autos)
        for name in autos:
            model = name.partition("/")[2]
            self.assertIn(f"free/{model}", NAMES)
            self.assertIn(f"paid/{model}", NAMES)
        for entry in GENERATED:
            if entry["model_name"].startswith("auto/"):
                self.assertEqual(entry["model_info"]["access_groups"], ["public-repo"])
        _, fallbacks = configured_tiers()
        keyed = {key: value for entry in fallbacks for key, value in entry.items()}
        for name in autos:
            self.assertEqual(keyed[name], ["paid/" + name.partition("/")[2]])


def models(*names):
    return [f"{prefix}/{name}" for prefix in ("free", "paid", "auto") for name in names]


class FamilyAliases(unittest.TestCase):
    def test_a_family_points_at_the_newest_model_under_each_prefix(self):
        aliases = sync.family_aliases(models("glm-5", "glm-5.3", "glm-5.3-flash", "glm-5.3-fast"), RULES)
        self.assertEqual(aliases["free/glm"], "free/glm-5.3")
        self.assertEqual(aliases["paid/glm"], "paid/glm-5.3")
        self.assertEqual(aliases["auto/glm"], "auto/glm-5.3")
        self.assertEqual(aliases["free/glm-flash"], "free/glm-5.3-flash")

    def test_the_alias_moves_when_a_newer_model_appears(self):
        before = sync.family_aliases(models("deepseek-v4-flash", "deepseek-v4.1-flash"), RULES)
        after = sync.family_aliases(models("deepseek-v4-flash", "deepseek-v4.1-flash", "deepseek-v4.10-flash"), RULES)
        self.assertEqual(before["free/deepseek-flash"], "free/deepseek-v4.1-flash")
        # a version compares as numbers, so 4.10 is newer than 4.1
        self.assertEqual(after["free/deepseek-flash"], "free/deepseek-v4.10-flash")
        self.assertEqual(after["paid/deepseek-flash"], "paid/deepseek-v4.10-flash")
        gone = sync.family_aliases(models("deepseek-v4-flash"), RULES)
        self.assertEqual(gone["free/deepseek-flash"], "free/deepseek-v4-flash")

    def test_a_family_never_mixes_models_of_another_family_or_a_dated_snapshot(self):
        aliases = sync.family_aliases(
            ["free/mistral-large-2512", "free/mistral-large-4", "free/mistral-large-4-1t-a52b", "free/gemini-3.1-flash-lite",
             "free/gemini-3.5-flash", "free/gemini-3.9-pro", "free/gemini-3.9-pro-preview", "free/deepseek-v5-pro"], RULES)
        self.assertEqual(aliases["free/mistral-large"], "free/mistral-large-4")
        self.assertEqual(aliases["free/gemini-flash"], "free/gemini-3.5-flash")
        self.assertEqual(aliases["free/gemini-pro"], "free/gemini-3.9-pro")
        self.assertEqual(aliases["free/deepseek-pro"], "free/deepseek-v5-pro")
        self.assertNotIn("free/deepseek-flash", aliases)

    def test_a_family_name_that_is_a_group_is_that_group_and_a_prefix_without_members_has_no_alias(self):
        aliases = sync.family_aliases(["paid/mistral-medium", "paid/mistral-medium-3.5", "free/glm-5.3"], RULES)
        self.assertNotIn("paid/mistral-medium", aliases)
        self.assertEqual(aliases["free/glm"], "free/glm-5.3")
        self.assertNotIn("paid/glm", aliases)
        self.assertNotIn("auto/glm", aliases)

    def test_every_alias_points_into_its_own_prefix_and_is_a_group_of_the_generated_file(self):
        aliases = sync.family_aliases(NAMES, RULES)
        self.assertTrue(aliases)
        for alias, target in aliases.items():
            self.assertEqual(alias.partition("/")[0], target.partition("/")[0], alias)
            self.assertIn(target, NAMES)
            self.assertNotIn(alias, NAMES)
            self.assertIn(alias.partition("/")[0], ("free", "paid", "auto"))
        # the family list of the issue, all present in the rules
        families = {key for key in RULES["families"] if not key.startswith("_")}
        self.assertEqual(families, {"deepseek-flash", "deepseek-pro", "glm", "glm-flash", "mistral-large", "mistral-medium",
                                    "mistral-small", "codestral", "gemini-flash", "gemini-pro"})

    def test_the_config_holds_exactly_these_aliases(self):
        alias, _ = configured_tiers()
        for name, target in sync.family_aliases(NAMES, RULES).items():
            self.assertEqual(alias[name], target)


@unittest.skipIf(Router is None, "litellm is not importable here; it is in the image (run the suite there)")
class AutoRouting(unittest.TestCase):
    """The rendered fallbacks and aliases, loaded into LiteLLM's Router with the upstream mocked."""

    MODEL = "glm-5.3-flash"

    def router(self, free_fails=True, paid_fails=False, extra=()):
        alias, fallbacks = configured_tiers()
        model_list = []
        for entry in GENERATED:
            name = entry["model_name"]
            if name not in (f"auto/{self.MODEL}", f"free/{self.MODEL}", f"paid/{self.MODEL}", *extra):
                continue
            fails = name.startswith(("auto/", "free/")) and free_fails or name.startswith("paid/") and paid_fails
            model_list.append({"model_name": name, "model_info": {"id": entry["model_info"]["id"],
                                                               "access_groups": entry["model_info"]["access_groups"]},
                               "litellm_params": {"model": "openai/mock", "api_key": "k", "order": entry["litellm_params"].get("order", 1),
                                                  "mock_response": "litellm.RateLimitError" if fails else name}})
        return Router(model_list=model_list, model_group_alias=alias, fallbacks=fallbacks, num_retries=0)

    def ask(self, router, model):
        reply = asyncio.run(router.acompletion(model=model, messages=[{"role": "user", "content": "hi"}]))
        return reply.choices[0].message.content

    def test_a_free_group_answers_first(self):
        reply = self.ask(self.router(free_fails=False), f"auto/{self.MODEL}")
        self.assertEqual(reply, f"auto/{self.MODEL}")

    def test_when_every_free_copy_is_limited_the_call_falls_through_to_the_paid_group(self):
        router = self.router(free_fails=True)
        self.assertEqual(self.ask(router, f"auto/{self.MODEL}"), f"paid/{self.MODEL}")
        # the free group alone never reaches a paid deployment
        with self.assertRaises(Exception):
            self.ask(router, f"free/{self.MODEL}")

    def test_both_limited_fails_and_the_family_alias_walks_the_same_chain(self):
        with self.assertRaises(Exception):
            self.ask(self.router(free_fails=True, paid_fails=True), f"auto/{self.MODEL}")
        self.assertEqual(self.ask(self.router(free_fails=True), "auto/glm-flash"), f"paid/{self.MODEL}")
        self.assertEqual(self.ask(self.router(free_fails=False), "free/glm-flash"), f"free/{self.MODEL}")
        self.assertEqual(self.ask(self.router(), "paid/glm-flash"), f"paid/{self.MODEL}")

    def test_a_key_without_the_public_repo_group_cannot_call_auto(self):
        router = self.router()

        def call(models, model):
            return can_object_call_model(model=model, llm_router=router, models=models, team_model_aliases=None,
                                         team_id=None, key_model_aliases=None, object_type="key")

        for model in (f"auto/{self.MODEL}", "auto/glm-flash"):
            for held in (["free"], ["paid"], ["free", "paid"], ["tier-free"]):
                with self.assertRaises(Exception, msg=(model, held)):
                    call(held, model)
            self.assertTrue(call([sync.AUTO_ACCESS_GROUP], model))
            self.assertTrue(call([sync.AUTO_ACCESS_GROUP, "free", "paid"], model))
        # the public-repo group opens auto/ and nothing else
        for model in (f"free/{self.MODEL}", f"paid/{self.MODEL}"):
            with self.assertRaises(Exception, msg=model):
                call([sync.AUTO_ACCESS_GROUP], model)


if __name__ == "__main__":
    unittest.main()
