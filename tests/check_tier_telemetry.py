"""Runs inside the image (tests/telemetry.test.mjs), where LiteLLM is installed.

Forces a deployment failure with LiteLLM's mock testing (`mock_response` raising), so no upstream is
called, and checks that the requested tier and the serving deployment differ in the series and in
the spend-log metadata, and that a priced deployment behind a free request is told apart.
"""

import asyncio
import sys
import unittest

sys.path.insert(0, "/app")

import litellm  # noqa: E402
from litellm import Router  # noqa: E402
from litellm.integrations.custom_logger import CustomLogger  # noqa: E402
from litellm.proxy.spend_tracking.spend_tracking_utils import _get_spend_logs_metadata  # noqa: E402
from prometheus_client import generate_latest  # noqa: E402

import tier_telemetry as tt  # noqa: E402

# LiteLLM mock testing: this string makes the mocked deployment raise a 429.
RATE_LIMIT = "litellm.RateLimitError"


def deployment(group, model_id, order, mock, **params):
    return {
        "model_name": group,
        "litellm_params": {"model": f"openai/{model_id}", "api_key": "k", "mock_response": mock, "order": order, **params},
        "model_info": {"id": model_id},
    }


def router(models, **kwargs):
    return Router(model_list=models, num_retries=2, allowed_fails=0, cooldown_time=60, **kwargs)


def series(name=tt.METRIC + "_total"):
    out = []
    for line in generate_latest().decode().splitlines():
        if line.startswith(name + "{"):
            labels = dict(part.split("=", 1) for part in line[line.index("{") + 1 : line.index("}")].split(","))
            out.append(({k: v.strip('"') for k, v in labels.items()}, float(line.rsplit(" ", 1)[1])))
    return out


def one(**want):
    found = [value for labels, value in series() if all(labels.get(k) == v for k, v in want.items())]
    assert len(found) == 1, (want, series())
    return found[0]


class TierTelemetryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        litellm.callbacks = [tt.tier_telemetry]

    async def ask(self, r, model, key="free@test"):
        await r.acompletion(
            model=model,
            messages=[{"role": "user", "content": "p"}],
            metadata={"user_api_key_alias": key},
        )
        await asyncio.sleep(0.5)  # logging callbacks run after the response

    async def test_free_deployment_answering_first_is_not_a_fallback(self):
        r = router([deployment("tier-free", "free-first", 1, "ok"), deployment("tier-free", "paid-second", 2, "ok", input_cost_per_token=0.001)])
        await self.ask(r, "tier-free", key="direct@test")
        self.assertEqual(one(requested_group="tier-free", api_key_alias="direct@test", model_id="free-first", priced="false", fallback="false"), 1)

    async def test_forced_failover_inside_the_group_shows_the_paid_deployment_serving(self):
        r = router([deployment("tier-free", "free-down", 1, RATE_LIMIT), deployment("tier-free", "paid-up", 2, "ok", input_cost_per_token=0.001)])
        await self.ask(r, "tier-free", key="leak@test")
        self.assertEqual(one(requested_group="tier-free", api_key_alias="leak@test", model_id="paid-up", priced="true", fallback="true"), 1)
        self.assertEqual([labels for labels, _ in series() if labels["model_id"] == "free-down"], [])

    async def test_a_cross_group_fallback_shows_requested_and_served_differing(self):
        r = router(
            [deployment("tier-free", "free-dead", 1, RATE_LIMIT), deployment("paid-group", "paid-serves", 1, "ok", input_cost_per_token=0.001)],
            fallbacks=[{"tier-free": ["paid-group"]}],
        )
        await self.ask(r, "tier-free", key="cross@test")
        self.assertEqual(one(requested_group="tier-free", api_key_alias="cross@test", model_id="paid-serves", priced="true", fallback="true"), 1)

    async def test_a_wildcard_request_is_bounded_and_the_spend_log_carries_the_record(self):
        captured = {}

        class Capture(CustomLogger):
            # Registered after the callback under test: it sees the metadata the logging hook wrote.
            async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
                captured.update(kwargs["litellm_params"]["metadata"])

        litellm.callbacks = [tt.tier_telemetry, Capture()]
        r = router([deployment("free/some-model", "free-one", 1, RATE_LIMIT), deployment("free/some-model", "paid-two", 2, "ok", input_cost_per_token=0.001)])
        await self.ask(r, "free/some-model", key="spend@test")
        record = captured["spend_logs_metadata"]["tier_telemetry"]
        self.assertEqual(
            {k: record[k] for k in ("requested_model", "requested_group", "served_model_id", "served_provider", "priced", "fallback")},
            {"requested_model": "free/some-model", "requested_group": "free/*", "served_model_id": "paid-two", "served_provider": "openai", "priced": True, "fallback": True},
        )
        # The spend log keeps the record: LiteLLM copies metadata.spend_logs_metadata into its row.
        kept = _get_spend_logs_metadata(captured)
        self.assertEqual(kept["spend_logs_metadata"]["tier_telemetry"]["served_model_id"], "paid-two")

    async def test_a_deployment_priced_only_by_litellms_cost_map_counts_as_priced(self):
        # The generated deployments carry no price of their own: the price comes from LiteLLM's map.
        entry = deployment("tier-free", "mapped", 1, "ok")
        entry["litellm_params"]["model"] = "mistral/mistral-small-latest"
        await self.ask(router([entry]), "tier-free", key="mapped@test")
        self.assertEqual(one(api_key_alias="mapped@test", model_id="mapped", priced="true", fallback="false"), 1)

    async def test_an_explicit_zero_cost_is_free_even_when_the_cost_map_prices_the_model(self):
        entry = deployment("tier-free", "zeroed", 1, "ok", input_cost_per_token=0, output_cost_per_token=0)
        entry["litellm_params"]["model"] = "mistral/mistral-small-latest"  # priced in the cost map
        await self.ask(router([entry]), "tier-free", key="zero@test")
        self.assertEqual(one(api_key_alias="zero@test", model_id="zeroed", priced="false", fallback="false"), 1)

    def test_group_bounding(self):
        self.assertEqual(tt.requested_group("tier-vibe"), "tier-vibe")
        self.assertEqual(tt.requested_group("free/qwen"), "free/*")
        self.assertEqual(tt.requested_group("vibe/anything-1"), "vibe/*")
        self.assertEqual(tt.requested_group("mistral-large"), "canonical")
        self.assertEqual(tt.requested_group(None), "unknown")


if __name__ == "__main__":
    unittest.main()
