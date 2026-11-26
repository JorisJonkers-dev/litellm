"""One spending cap per paid account: the declared accounts, their windows, and the Router enforcing them."""
import asyncio
import datetime
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "catalog"))
sys.path.insert(0, str(ROOT / "tests"))
import sync  # noqa: E402
sys.path.insert(0, str(ROOT / "config"))

try:  # in the image, not on a workstation
    from litellm import Router
except ImportError:  # pragma: no cover
    Router = None

RULES = sync.load_rules()
UTC = datetime.timezone.utc


class Windows(unittest.TestCase):
    def test_durations(self):
        self.assertEqual(sync.window_duration({"calendar": "month"}), "1mo")
        self.assertEqual(sync.window_duration({"days": 30, "anchor": "2026-11-04T10:41:00Z"}), "30d")
        self.assertEqual(sync.window_duration({"days": 1}), "1d")
        with self.assertRaises(ValueError):
            sync.window_duration({"weeks": 2})

    def test_calendar_month_resets_on_the_first(self):
        window = {"calendar": "month"}
        self.assertEqual(sync.next_reset(window, datetime.datetime(2026, 11, 18, 16, 41, tzinfo=UTC)),
                         datetime.datetime(2026, 12, 1, tzinfo=UTC))
        self.assertEqual(sync.next_reset(window, datetime.datetime(2026, 12, 31, 23, 59, tzinfo=UTC)),
                         datetime.datetime(2027, 1, 1, tzinfo=UTC))

    def test_the_go_cycle_is_anchored_on_its_subscription(self):
        window = RULES["accounts"]["opencode-go"]["window"]
        now = datetime.datetime(2026, 11, 18, 16, 41, tzinfo=UTC)
        reset = sync.next_reset(window, now)
        self.assertEqual(reset, datetime.datetime(2026, 12, 4, 10, 41, tzinfo=UTC))
        self.assertEqual(reset - now, datetime.timedelta(days=15, hours=18))
        self.assertEqual(sync.next_reset(window, reset), datetime.datetime(2027, 1, 3, 10, 41, tzinfo=UTC))
        self.assertEqual(sync.next_reset(window, datetime.datetime(2026, 11, 1, tzinfo=UTC)), datetime.datetime(2026, 11, 4, 10, 41, tzinfo=UTC))
        self.assertIsNone(sync.next_reset({"days": 30}, now))

    def test_the_window_that_contains_a_moment(self):
        month = {"calendar": "month"}
        self.assertEqual(sync.window_start(month, datetime.datetime(2026, 11, 18, 16, 41, tzinfo=UTC)),
                         datetime.datetime(2026, 11, 1, tzinfo=UTC))
        go = RULES["accounts"]["opencode-go"]["window"]
        self.assertEqual(sync.window_start(go, datetime.datetime(2026, 11, 18, tzinfo=UTC)), datetime.datetime(2026, 11, 4, 10, 41, tzinfo=UTC))
        self.assertEqual(sync.window_start(go, datetime.datetime(2026, 12, 4, 10, 41, tzinfo=UTC)), datetime.datetime(2026, 12, 4, 10, 41, tzinfo=UTC))
        self.assertEqual(sync.window_start(go, datetime.datetime(2026, 12, 4, 10, 40, tzinfo=UTC)), datetime.datetime(2026, 11, 4, 10, 41, tzinfo=UTC))
        self.assertIsNone(sync.window_start({"days": 30}, datetime.datetime(2026, 11, 18, tzinfo=UTC)))


class Accounts(unittest.TestCase):
    def test_the_owners_caps(self):
        budgets = sync.account_budgets(RULES)
        self.assertEqual({r: (b["max_budget"], b["budget_duration"]) for r, b in budgets.items()}, {
            "vibe": (255, "1mo"), "vibe-study": (127, "1mo"), "vibe-api": (25.5, "1mo"), "vibe-study-api": (12.7, "1mo"),
            "anthropic": (90, "30d"), "opencode-go": (40, "30d")})

    def test_a_route_is_billed_by_one_account(self):
        rules = {"accounts": {"a": {"routes": ["x"], "max_budget": 1, "window": {"days": 1}},
                              "b": {"routes": ["x"], "max_budget": 1, "window": {"days": 1}}}}
        with self.assertRaisesRegex(ValueError, "two accounts"):
            sync.account_budgets(rules)

    def test_every_deployment_of_an_account_names_it_and_keeps_its_own_id(self):
        text = (ROOT / "config" / "models.generated.yaml").read_text()
        entries = json.loads(text[text.index("{"):])["model_list"]
        budgets = sync.account_budgets(RULES)
        ids, seen = [], set()
        for entry in entries:
            params, info = entry["litellm_params"], entry["model_info"]
            if "account" not in info:
                # Only a free id of a paid route goes without an account: in its own "<route>-free/" group or a
                # shared free/<model> group, at an explicit zero cost.
                if info["route"] in budgets:
                    self.assertRegex(entry["model_name"], r"^(free/|[a-z-]+-free/)", info["id"])
                    self.assertEqual((params["input_cost_per_token"], params["output_cost_per_token"]), (0, 0), info["id"])
                continue
            budget = budgets[info["route"]]
            self.assertEqual(info["account"], budget["account"])
            self.assertEqual((params["max_budget"], params["budget_duration"]), (budget["max_budget"], budget["budget_duration"]))
            # An id that is a model name ("vibe/<id>") would reach the deployment past every pre-call filter.
            self.assertEqual(info["id"].split(":")[0], info["route"])
            self.assertNotIn("/", info["id"])
            seen.add(info["route"])
        self.assertTrue({"vibe", "opencode-go"} <= seen)

    def test_the_route_wildcards_name_their_account(self):
        config = (ROOT / "config" / "config.yaml").read_text()
        for route in ("vibe", "vibe-study", "vibe-api", "vibe-study-api", "opencode-go"):
            block = config[config.index(f"- model_name: {route}/*\n"):].split("\n  - ")[0]
            self.assertIn(f"model_info: {{ account: {route},", block, route)
        self.assertIn("account_budget.account_budget", config)
        self.assertIn("COPY config/account_budget.py", (ROOT / "Dockerfile").read_text())


@unittest.skipIf(Router is None, "litellm is not importable here; it is in the image (run the suite there)")
class Hook(unittest.TestCase):
    """The callback's counter and windows, without a Router."""

    def hook(self, now):
        import account_budget
        self.clock = [now.timestamp()]
        return account_budget.AccountBudget(RULES["accounts"], clock=lambda: self.clock[0])

    def test_the_counter_is_per_account_and_turns_with_the_calendar_month(self):
        hook = self.hook(datetime.datetime(2026, 11, 30, 23, 0, tzinfo=UTC))
        hook.add("vibe", 254.0)
        self.assertFalse(hook.over("vibe"))
        hook.add("vibe", 1.0)
        self.assertTrue(hook.over("vibe"))
        self.assertFalse(hook.over("vibe-study"))
        self.assertFalse(hook.over("vibe-api"))
        self.clock[0] = datetime.datetime(2026, 12, 1, 0, 0, tzinfo=UTC).timestamp()
        self.assertFalse(hook.over("vibe"))
        self.assertEqual(hook.spent("vibe"), 0.0)

    def test_the_go_cycle_turns_on_its_anchor(self):
        hook = self.hook(datetime.datetime(2026, 12, 4, 10, 40, tzinfo=UTC))
        hook.add("opencode-go", 40.0)
        self.assertTrue(hook.over("opencode-go"))
        self.clock[0] = datetime.datetime(2026, 12, 4, 10, 41, tzinfo=UTC).timestamp()
        self.assertFalse(hook.over("opencode-go"))

    def test_a_rolling_window_opens_at_the_first_spend(self):
        hook = self.hook(datetime.datetime(2026, 11, 18, tzinfo=UTC))
        hook.add("anthropic", 90.0)
        self.assertTrue(hook.over("anthropic"))
        self.clock[0] += 29 * 86400
        self.assertTrue(hook.over("anthropic"))
        self.clock[0] += 2 * 86400
        self.assertFalse(hook.over("anthropic"))

    def test_an_account_it_does_not_know_is_never_over(self):
        hook = self.hook(datetime.datetime(2026, 11, 18, tzinfo=UTC))
        hook.add("elsewhere", 1e9)
        self.assertFalse(hook.over("elsewhere"))
        self.assertFalse(hook.over(None))


class FakePrisma:
    """The spend log as the proxy's prisma client answers a raw query: rows of model_id and spend."""

    def __init__(self, rows=None, error=None, delay=0):
        self.rows, self.error, self.delay, self.queries = rows or [], error, delay, []
        self.db = self

    async def query_raw(self, sql, *params):
        self.queries.append((sql, params))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.rows


@unittest.skipIf(Router is None, "litellm is not importable here; it is in the image (run the suite there)")
class Seeding(unittest.TestCase):
    """After a restart the counters are seeded from the spend log, and a failing log never blocks a request."""
    IDS = {"vibe:a": "vibe", "vibe:b": "vibe", "hash-of-the-vibe-wildcard": "vibe", "vibe-study:a": "vibe-study"}
    NOW = datetime.datetime(2026, 11, 18, 16, 41, tzinfo=UTC)

    def hook(self, prisma):
        import account_budget
        return account_budget.AccountBudget(RULES["accounts"], clock=lambda: self.NOW.timestamp(), prisma=prisma, id_accounts=self.IDS)

    def pick(self, hook):
        deployments = [{"model_info": {"id": "vibe:a", "account": "vibe"}}, {"model_info": {"id": "vibe-study:a", "account": "vibe-study"}}]
        return [d["model_info"]["id"] for d in asyncio.run(hook.async_filter_deployments("m", deployments, None))]

    def test_spend_before_a_restart_blocks_the_account_after_it(self):
        prisma = FakePrisma([{"model_id": "vibe:a", "spend": 200.0}, {"model_id": "vibe:b", "spend": 30.0},
                             {"model_id": "hash-of-the-vibe-wildcard", "spend": 25.5}, {"model_id": "vibe-study:a", "spend": 3.0},
                             {"model_id": "unknown", "spend": 999.0}])
        hook = self.hook(prisma)
        self.assertEqual(self.pick(hook), ["vibe-study:a"])  # 255.5 on vibe, 3 on vibe-study
        self.assertAlmostEqual(hook.spent("vibe"), 255.5)
        self.assertAlmostEqual(hook.spent("vibe-study"), 3.0)
        self.assertEqual(len(prisma.queries), 1)
        self.pick(hook)
        self.assertEqual(len(prisma.queries), 1)  # once per start
        # The query reaches back to the earliest window start: the 1st for a calendar month, 30 days for a rolling one.
        self.assertEqual(prisma.queries[0][1], ("2026-10-19 16:41:00",))

    def test_spend_under_the_cap_after_a_restart_is_still_served(self):
        hook = self.hook(FakePrisma([{"model_id": "vibe:a", "spend": 100.0}]))
        self.assertEqual(self.pick(hook), ["vibe:a", "vibe-study:a"])

    def test_no_database_counts_in_memory_and_serves(self):
        hook = self.hook(None)
        self.assertEqual(self.pick(hook), ["vibe:a", "vibe-study:a"])
        hook.add("vibe", 255.0)
        self.assertEqual(self.pick(hook), ["vibe-study:a"])

    def test_a_failing_or_slow_spend_log_never_blocks_a_request(self):
        import account_budget
        hook = self.hook(FakePrisma(error=RuntimeError("connection refused")))
        self.assertEqual(self.pick(hook), ["vibe:a", "vibe-study:a"])
        hook = self.hook(FakePrisma(delay=1))
        original, account_budget.SEED_TIMEOUT = account_budget.SEED_TIMEOUT, 0.05
        try:
            self.assertEqual(self.pick(hook), ["vibe:a", "vibe-study:a"])
        finally:
            account_budget.SEED_TIMEOUT = original

    def test_a_failed_seed_is_tried_again_later(self):
        prisma = FakePrisma(error=RuntimeError("down"))
        hook = self.hook(prisma)
        self.pick(hook)
        self.pick(hook)
        self.assertEqual(len(prisma.queries), 1)  # not on every request
        prisma.error, prisma.rows = None, [{"model_id": "vibe:a", "spend": 255.0}]
        hook.clock = lambda: self.NOW.timestamp() + 61
        self.assertEqual(self.pick(hook), ["vibe-study:a"])


MODELS = [("vibe", "mistral-small-2603", "usable", {}), ("vibe", "zai-glm-5-3-flash", "usable", {}),
          ("vibe-study", "mistral-small-2603", "usable", {}), ("opencode-go", "mimo-v2.6-flash", "usable", {})]
CAPPED = dict(RULES, accounts={
    "vibe": {"routes": ["vibe"], "max_budget": 0.05, "window": {"calendar": "month"}},
    "vibe-study": {"routes": ["vibe-study"], "max_budget": 0.05, "window": {"calendar": "month"}},
    "opencode-go": {"routes": ["opencode-go"], "max_budget": 0.05, "window": {"days": 30}}})
SCENARIOS = {
    # Every scenario is a process of its own. A token costs 0.01 and an answer has more than 5, so one answer
    # spends the 0.05 cap.
    "model_a_then_model_b_of_the_account": ["vibe-tier/mistral-small-2603", "vibe-tier/zai-glm-5-3-flash", "vibe/wild", "glm-5.3-flash"],
    "other_accounts_unaffected": ["vibe-tier/mistral-small-2603", "vibe-tier/zai-glm-5-3-flash", "vibe-study-probe", "mimo-v2.6-flash"],
    "wildcard_spend_counts": ["vibe/wild", "vibe-tier/zai-glm-5-3-flash", "glm-5.3-flash"],
    "direct_spend_counts_for_tier": ["glm-5.3-flash", "vibe-tier/mistral-small-2603"],
}


def planned_model_list(specs):
    model_list = []
    for spec in specs:
        params = {k: v for k, v in spec["litellm_params"].items() if k not in ("litellm_credential_name", "max_budget", "budget_duration")}
        params.update(model="openai/mock", api_key="k", mock_response=spec["name"], input_cost_per_token=0.01, output_cost_per_token=0.01)
        model_list.append({"model_name": spec["name"], "litellm_params": params, "model_info": spec["model_info"]})
    wild = {"model": "openai/*", "api_key": "k", "mock_response": "wild", "input_cost_per_token": 0.01, "output_cost_per_token": 0.01}
    model_list.append({"model_name": "vibe/*", "litellm_params": wild, "model_info": {"account": "vibe"}})
    return model_list


def run_scenario(name):
    import litellm
    import account_budget
    from test_routing import catalogue
    specs = sync.plan_groups(catalogue(*MODELS), dict(CAPPED, tiers=RULES["tiers"]))
    litellm.callbacks = [account_budget.AccountBudget(CAPPED["accounts"])]

    async def main():
        router = Router(model_list=planned_model_list(specs), num_retries=0)
        answers = []
        for model in SCENARIOS[name]:
            if model == "vibe-study-probe":
                model = next(s["name"] for s in specs if s["route"] == "vibe-study")
            try:
                await router.acompletion(model=model, messages=[{"role": "user", "content": "hi"}])
                answers.append("ok")
            except Exception:
                answers.append("ERR")
            await asyncio.sleep(0.3)  # the spend is recorded by a callback after the answer
        return answers

    return asyncio.run(main())


@unittest.skipIf(Router is None, "litellm is not importable here; it is in the image (run the suite there)")
class SharedCounter(unittest.TestCase):
    def answers(self, name):
        done = subprocess.run([sys.executable, "-W", "ignore", __file__, name], capture_output=True, text=True, check=True)
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_spending_the_cap_through_one_model_blocks_the_other_models_tier_copies_and_wildcard_of_the_account(self):
        self.assertEqual(self.answers("model_a_then_model_b_of_the_account"), ["ok", "ERR", "ERR", "ERR"])

    def test_another_account_is_not_blocked(self):
        self.assertEqual(self.answers("other_accounts_unaffected"), ["ok", "ERR", "ok", "ok"])

    def test_a_wildcard_call_counts_against_the_account(self):
        self.assertEqual(self.answers("wildcard_spend_counts"), ["ok", "ERR", "ERR"])

    def test_a_direct_call_and_a_tier_copy_draw_on_one_cap(self):
        self.assertEqual(self.answers("direct_spend_counts_for_tier"), ["ok", "ERR"])


def deployment(group, upstream, **extra):
    params = dict(model="openai/" + upstream, api_key="key-" + upstream, mock_response="from:" + upstream)
    params.update(extra)
    return {"model_name": group, "litellm_params": params,
            "model_info": {"id": "vibe:" + upstream, "account": "vibe", "allowed_fails_policy": {"RateLimitErrorAllowedFails": 0}}}


@unittest.skipIf(Router is None, "litellm is not importable here; it is in the image (run the suite there)")
class OwnIds(unittest.TestCase):
    """Every deployment keeps its own id, so routing, failover and cooldown stay per deployment."""
    messages = [{"role": "user", "content": "hi"}]

    def test_a_request_always_reaches_its_own_models_upstream(self):
        router = Router(model_list=[deployment("vibe/A", "A"), deployment("vibe/B", "B")], num_retries=0)

        async def run():
            return [(group, (await router.acompletion(model=group, messages=self.messages)).choices[0].message.content)
                    for group in [("vibe/A", "vibe/B")[(i * 7 + i // 3) % 2] for i in range(50)]]
        for group, answer in asyncio.run(run()):
            self.assertEqual(answer, "from:" + group.split("/")[1])

    def test_a_failover_inside_a_group_reaches_the_other_deployment(self):
        bad = deployment("g", "bad", mock_response="litellm.RateLimitError", cooldown_time=3600)
        router = Router(model_list=[bad, deployment("g", "good")], num_retries=3)

        async def run():
            return [(await router.acompletion(model="g", messages=self.messages)).choices[0].message.content for _ in range(4)]
        self.assertEqual(asyncio.run(run()), ["from:good"] * 4)

    def test_a_429_on_one_model_does_not_cool_another_model_of_the_account(self):
        a = deployment("vibe/A", "A", mock_response="litellm.RateLimitError", cooldown_time=3600)
        router = Router(model_list=[a, deployment("vibe/B", "B")], num_retries=0)

        async def run():
            answers = []
            for group in ["vibe/A", "vibe/B", "vibe/B"]:
                try:
                    answers.append((await router.acompletion(model=group, messages=self.messages)).choices[0].message.content)
                except Exception as error:
                    answers.append(type(error).__name__)
            return answers
        self.assertEqual(asyncio.run(run()), ["RateLimitError", "from:B", "from:B"])


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in SCENARIOS:
        print(json.dumps(run_scenario(sys.argv[1])))
    else:
        unittest.main()
