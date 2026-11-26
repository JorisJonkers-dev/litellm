"""Free groups across providers, their limits, and config/free_limits.py keeping a limited copy out of the pick."""
import asyncio
import datetime
import json
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
    return (route, model_id, "usable", {"free": True})


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
        for name in ("free/gpt-oss-20b", "free/gpt-oss-safeguard-20b", "free/deepseek-v4-flash-0731", "free/deepseek-v4-flash",
                     "free/llama-3.3-70b-instruct-fp8-fast", "free/llama-3.3-70b-instruct", "free/gemma-4-26b-a4b-it-darksoul",
                     "free/gemma-4-26b-a4b-it"):
            self.assertIn(name, names)
        self.assertEqual(group(specs, "free/gpt-oss-safeguard-20b"), ["groq", "unorouter"])

    def test_an_alias_merges_what_models_dev_names_one_model(self):
        specs = plan(free("unorouter", "gemma-4-31b:free"), free("openrouter", "google/gemma-4-31b-it:free"),
                     free("bazaarlink", "nemotron-3.5-lightning-free"), free("requesty", "nvidia/nemotron-3.5-lightning-30b-a3b"))
        self.assertEqual(group(specs, "free/gemma-4-31b-it"), ["openrouter", "unorouter"])
        self.assertEqual(group(specs, "free/nemotron-3.5-lightning"), ["bazaarlink", "requesty"])

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
                     ("vibe-api", "gpt-oss-120b", "usable", {}), ("anthropic", "gpt-oss-120b", "usable", {}),
                     ("inferbase", "gpt-oss-120b", "usable", {"free": True, "price": {"input": 1e-6, "output": 1e-6}}))
        self.assertEqual(group(specs, "free/gpt-oss-120b"), ["groq"])


class Generated(unittest.TestCase):
    """The committed config/models.generated.yaml."""

    def free_entries(self):
        return [e for e in GENERATED if e["model_name"].startswith("free/")]

    def test_every_free_group_deployment_is_free_public_only_and_without_a_fixed_cooldown(self):
        entries = self.free_entries()
        self.assertGreater(len(entries), 100)
        for entry in entries:
            params, info = entry["litellm_params"], entry["model_info"]
            self.assertEqual((params["input_cost_per_token"], params["output_cost_per_token"]), (0, 0), info["id"])
            self.assertEqual(info["access_groups"], ["free"], info["id"])
            self.assertIn(info["route"], sync.FREE_GROUP_ROUTES, info["id"])
            self.assertNotIn(info["route"], ("vibe", "vibe-study", "vibe-api", "vibe-study-api", "anthropic", "inferbase"))
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

    def test_each_free_deployment_carries_its_routes_limits(self):
        for entry in self.free_entries():
            want = sync.free_limit_params(entry["model_info"]["route"], REAL)
            got = {k: v for k, v in entry["litellm_params"].items() if k in sync.DEPLOYMENT_LIMITS + ("daily_reset_time",)}
            self.assertEqual(got, want, entry["model_info"]["id"])
        groq = next(e for e in GENERATED if e["model_name"] == "free/gpt-oss-120b" and e["model_info"]["route"] == "groq")
        self.assertEqual({k: groq["litellm_params"][k] for k in ("rpm", "rpd", "tpm", "tpd", "daily_reset_time")},
                         {"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000, "daily_reset_time": "00:00"})

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

    def test_a_402_blocks_the_account(self):
        a, b = dep("electronhub", "a"), dep("electronhub", "b")
        self.refuse(a, Error(402, "Payment required", {"retry-after": "60"}))
        self.assertEqual(self.pick([a, b, dep("groq", "x")]), ["free/groq/x"])

    def test_an_error_that_is_no_limit_is_left_to_the_router(self):
        a = dep("electronhub", "a")
        self.refuse(a, Error(500, "upstream exploded"))
        self.assertEqual(self.pick([a]), ["free/electronhub/a"])

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
