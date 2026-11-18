"""Requested tier against the deployment that served it, as a Prometheus series and spend-log metadata.

LiteLLM's own prometheus callback already labels the request series with `requested_model`,
`model_id` and `api_provider`, and the spend log stores the served `model_group`, `model_id` and
provider. Neither says whether the serving deployment is priced, whether a retry or fallback
happened on the way, or groups the requested name into a bounded set a rule can match. This small
callback adds those, and nothing else: metadata only, never a prompt or a response.

Series, per successful request:

  litellm_tier_served_total{requested_group, api_key_alias, api_provider, model_id, priced, fallback}

  requested_group  `tier-*` and every bare-name request as such (`tier-free`, `canonical`); any
                   `<route>/<id>` request collapses to `<route>/*`, so a wildcard route cannot mint
                   a series per model id a client sends. `free/<canonical>` becomes `free/*`.
  model_id         the serving deployment (the generated config's `model_info.id`)
  api_provider     the serving deployment's LiteLLM provider (mistral, openrouter, gemini, ...)
  priced           "true" when the deployment has a price: an explicit input or output cost on the
                   deployment, a cost-map price for its model, or a request that was charged
  fallback         "true" when the router retried or fell back before this deployment answered

The same facts go to the spend log under `metadata.spend_logs_metadata.tier_telemetry`.
"""

from __future__ import annotations

from typing import Any

from litellm.integrations.custom_logger import CustomLogger
from prometheus_client import Counter

METRIC = "litellm_tier_served"
LABELS = ("requested_group", "api_key_alias", "api_provider", "model_id", "priced", "fallback")


def requested_group(requested: Any) -> str:
    """A bounded label for the model name the caller asked for."""
    if not isinstance(requested, str) or not requested:
        return "unknown"
    if requested.startswith("tier-"):
        return requested
    if "/" in requested:
        return f"{requested.split('/', 1)[0]}/*"
    return "canonical"


def _positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _cost_map_priced(model: Any, provider: Any) -> bool:
    if not isinstance(model, str) or not model:
        return False
    try:
        import litellm

        info = litellm.get_model_info(model=model, custom_llm_provider=provider)
    except Exception:  # noqa: BLE001  # an id the cost map does not know is simply not priced there
        return False
    return _positive(info.get("input_cost_per_token")) or _positive(info.get("output_cost_per_token"))


def is_priced(kwargs: dict, metadata: dict, hidden: dict) -> bool:
    model_info = metadata.get("model_info") or {}
    params = kwargs.get("litellm_params") or {}
    for source in (model_info, params):
        if _positive(source.get("input_cost_per_token")) or _positive(source.get("output_cost_per_token")):
            return True
    if _positive(kwargs.get("response_cost")) or _positive(hidden.get("response_cost")):
        return True
    return _cost_map_priced(hidden.get("litellm_model_name") or kwargs.get("model"), kwargs.get("custom_llm_provider"))


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def describe(kwargs: dict, result: Any = None) -> dict:
    """What the request asked for and what served it, from the logging payload alone."""
    params = kwargs.get("litellm_params") or {}
    metadata = params.get("metadata") or {}
    hidden = dict(metadata.get("hidden_params") or {})
    hidden.update(getattr(result, "_hidden_params", None) or {})
    headers = hidden.get("additional_headers") or {}
    requested = metadata.get("original_model_group") or metadata.get("model_group") or kwargs.get("model")
    retries = _count(metadata.get("attempted_retries")) or _count(headers.get("x-litellm-attempted-retries"))
    fallbacks = _count(metadata.get("attempted_fallbacks")) or _count(headers.get("x-litellm-attempted-fallbacks"))
    served_group = metadata.get("model_group")
    moved_group = bool(requested and served_group and requested != served_group and "*" not in str(served_group))
    return {
        "requested_model": requested,
        "requested_group": requested_group(requested),
        "api_key_alias": metadata.get("user_api_key_alias") or "None",
        "served_model_id": (metadata.get("model_info") or {}).get("id") or hidden.get("model_id") or kwargs.get("model_id") or "unknown",
        "served_provider": kwargs.get("custom_llm_provider") or hidden.get("custom_llm_provider") or "unknown",
        "priced": is_priced(kwargs, metadata, hidden),
        "fallback": bool(retries or fallbacks or moved_group),
        "attempted_retries": retries,
        "attempted_fallbacks": fallbacks,
    }


# One series for the process: a second instance of the callback must not register it again.
SERVED = Counter(METRIC, "Requests served, by requested tier and by the deployment that served them", LABELS)


class TierTelemetry(CustomLogger):

    async def async_logging_hook(self, kwargs: dict, result: Any, call_type: str):
        """Runs before the spend log is built, so the record lands in its metadata."""
        try:
            record = describe(kwargs, result)
            metadata = (kwargs.setdefault("litellm_params", {}) or {}).setdefault("metadata", {})
            logged = dict(metadata.get("spend_logs_metadata") or {})
            logged["tier_telemetry"] = record
            metadata["spend_logs_metadata"] = logged
        except Exception:  # noqa: BLE001  # telemetry must never fail a request
            pass
        return kwargs, result

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        try:
            record = describe(kwargs, response_obj)
            SERVED.labels(
                requested_group=record["requested_group"],
                api_key_alias=record["api_key_alias"],
                api_provider=str(record["served_provider"]),
                model_id=str(record["served_model_id"]),
                priced=str(record["priced"]).lower(),
                fallback=str(record["fallback"]).lower(),
            ).inc()
        except Exception:  # noqa: BLE001
            pass


tier_telemetry = TierTelemetry()
