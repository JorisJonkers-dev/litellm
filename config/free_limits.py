"""Keep a free deployment out of the pick while its provider limit lasts, and let it back in at reset.

LiteLLM 1.109.1 enforces per-deployment rpm, tpm, rpd, tpd and max_parallel_requests itself
(`enforce_model_rate_limits`), but only when the call is about to go out: a deployment over its limit
fails that attempt and spends a retry. It knows nothing of a budget a provider shares across all its
models (Kilo's 200 an hour per IP, Requesty's 200 a day), and nothing of what a provider says in its
answers. This callback adds both, before routing:

- Counts. Every attempt on a free route (async_pre_call_check, just before the call) is counted per
  deployment (route + upstream model, so the per-route group, the free/<model> group and a tier copy of one
  upstream model share their count) against the deployment's own litellm_params limits, and per route
  against the route's `shared` budgets and `concurrency` in catalog/routing.json `free_limits`. Tokens are
  added after a success. Windows are clock windows: the minute, the hour, the day from the route's
  `reset` (HH:MM UTC), the calendar month (UTC).
- Learning. A success whose x-ratelimit-remaining-requests or -tokens is 0 blocks until the matching
  x-ratelimit-reset-*. A 429, a 402, or a quota error blocks until Retry-After, else x-ratelimit-reset*,
  else the next daily reset when the error speaks of a day or a quota, else the route's `cooldowns`
  value. A dead copy (a 401, 403 or 404, or a 400 whose text says the model is deprecated, gone or missing)
  blocks its deployment for DEAD_BLOCK (six hours) unless the answer gives a reset. A learned block covers
  the one deployment or the whole account, as the route's `scope` says (`account_match` escalates a matching
  error to the account; a 402 is always the account).
- Filtering. async_filter_deployments drops every deployment that is blocked or whose window is spent,
  so the router picks the next free copy at once, and none is sent to a limited provider. A deployment
  comes back the moment its block ends or its window turns. When nothing is left the call fails with the
  router's no-deployments error, and a tier's next step serves.

Paid deployments are never touched. The state is in memory, which is right for the one proxy replica; the
store is one small interface (`MemoryStore`) so a shared store (Redis) can replace it.
"""

from __future__ import annotations

import asyncio
import calendar
import datetime
import email.utils
import json
import logging
import os
import re
import sys
import time
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

HERE = os.path.dirname(os.path.abspath(__file__))
# In the image the rules sit at /app/catalog next to this file; in the repository at ../catalog.
RULES = next((path for path in (os.path.join(HERE, "catalog", "routing.json"),
                                os.path.join(os.path.dirname(HERE), "catalog", "routing.json"))
              if os.path.exists(path)), None)
UTC = datetime.timezone.utc
CATALOG = os.path.dirname(RULES) if RULES else None
if CATALOG and CATALOG not in sys.path:
    sys.path.append(CATALOG)
try:
    import sync  # noqa: E402  (which routes are free is decided once, with the rules)
    FREE_ROUTES = frozenset(sync.FREE_ONLY - sync.FREE_PRICED)
    FREE_ID_ROUTES = frozenset(sync.FREE_ID_ROUTES)
except ImportError:  # pragma: no cover  # the image always carries catalog/sync.py beside the rules
    FREE_ROUTES, FREE_ID_ROUTES = frozenset(), frozenset()
LOG = logging.getLogger("free_limits")
LIMIT_ERROR = "No deployments available for selected model, Try again in a moment: free limits"
# Seconds a slot counts as in flight when its end was never logged (a lost callback must not wedge a route).
SLOT_TTL = 600.0
DEFAULT_BLOCK = 60.0
# A dead copy (a key the provider paused, a model it retired) stays out this long unless its answer gives a reset.
DEAD_BLOCK = 6 * 3600.0
DEAD_STATUS = (401, 403, 404)
DEAD_MODEL_ERROR = re.compile(r"deprecated|model_deprecated|model not found|does not exist|no longer available|decommissioned", re.I)
SEED_TIMEOUT = 5.0  # seconds a request may wait for the spend log, once
SEED_RETRY = 60.0  # seconds before a failed seed is tried again
# Hourly request and token sums per deployment since a moment: the counts a restart would otherwise forget.
SEED_SQL = ('SELECT model_id, date_trunc(\'hour\', "startTime") AS hour, COUNT(*) AS n, '
            'COALESCE(SUM(total_tokens), 0) AS tokens FROM "LiteLLM_SpendLogs" '
            'WHERE "startTime" >= $1::timestamp GROUP BY model_id, hour')
DAILY_ERROR = re.compile(r"per[ -]day|daily|quota|exhausted|insufficient|credit|allowance|neuron", re.I)
QUOTA_ERROR = re.compile(r"rate.?limit|too many requests|quota|exhausted|insufficient[_ ](credit|quota|funds)|payment required|"
                         r"out of credit|limit exceeded", re.I)
DURATION = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)")


def load_rules(path: str | None = None) -> dict:
    path = path or RULES
    if not path:
        return {}
    with open(path) as handle:
        return json.load(handle)


def parse_reset(value: Any, now: float) -> float | None:
    """When a reset header value says the limit resets: an epoch second, or None when it says nothing.

    Accepts a duration ("2m59.56s", "7.66s", "6ms", "1h"), a number of seconds, an epoch in seconds or
    milliseconds (OpenRouter's X-RateLimit-Reset), and an HTTP date (Retry-After may be one).
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        number = None
    if number is not None:
        if number < 0:
            return None
        if number > 1e12:  # epoch milliseconds
            return number / 1000.0
        if number > 1e9:  # epoch seconds
            return number
        return now + number
    parts = DURATION.findall(text)
    if parts and "".join(f"{n}{u}" for n, u in parts) == text.replace(" ", ""):
        scale = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}
        return now + sum(float(n) * scale[u] for n, u in parts)
    parsed = email.utils.parsedate_tz(text)
    if parsed is not None:
        return float(email.utils.mktime_tz(parsed))
    return None


def header(headers: dict, name: str) -> Any:
    """A response header however LiteLLM spelled it: bare, `llm_provider-` prefixed, or with underscores."""
    if not headers:
        return None
    lowered = {str(k).lower(): v for k, v in headers.items()}
    for key in (name, f"llm_provider-{name}", name.replace("-", "_")):
        if key in lowered:
            return lowered[key]
    return None


def window_key(per: str, now: float, reset_offset: float) -> tuple[str, float]:
    """(the window's key, when it ends) for a clock window containing `now`."""
    moment = datetime.datetime.fromtimestamp(now, UTC)
    if per == "minute":
        start = moment.replace(second=0, microsecond=0)
        return start.strftime("%Y%m%d%H%M"), start.timestamp() + 60
    if per == "hour":
        start = moment.replace(minute=0, second=0, microsecond=0)
        return start.strftime("%Y%m%d%H"), start.timestamp() + 3600
    if per == "day":
        shifted = moment - datetime.timedelta(seconds=reset_offset)
        start = shifted.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(seconds=reset_offset)
        return start.strftime("%Y%m%d"), start.timestamp() + 86400
    if per == "month":
        start = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        days = calendar.monthrange(start.year, start.month)[1]
        return start.strftime("%Y%m"), start.timestamp() + days * 86400
    raise ValueError(f"unknown window {per!r}")


def reset_offset(reset: str | None) -> float:
    if not reset:
        return 0.0
    hours, minutes = reset.split(":")
    return int(hours) * 3600 + int(minutes) * 60


class MemoryStore:
    """Counters, blocks and in-flight slots in this process. The interface a Redis store would implement."""

    def __init__(self) -> None:
        self.counts: dict[str, tuple[float, float]] = {}  # key -> (value, expires at)
        self.blocks: dict[str, float] = {}  # key -> blocked until
        self.slots: dict[str, list[float]] = {}  # key -> start times of requests in flight

    def get(self, key: str, now: float) -> float:
        value, expires = self.counts.get(key, (0.0, 0.0))
        if expires <= now:
            self.counts.pop(key, None)
            return 0.0
        return value

    def add(self, key: str, amount: float, expires: float, now: float) -> float:
        value = self.get(key, now) + amount
        self.counts[key] = (value, expires)
        return value

    def block(self, key: str, until: float) -> None:
        self.blocks[key] = max(until, self.blocks.get(key, 0.0))

    def blocked_until(self, key: str, now: float) -> float:
        until = self.blocks.get(key, 0.0)
        if until <= now:
            self.blocks.pop(key, None)
            return 0.0
        return until

    def in_flight(self, key: str, now: float) -> int:
        live = [start for start in self.slots.get(key, []) if now - start < SLOT_TTL]
        self.slots[key] = live
        return len(live)

    def acquire(self, key: str, now: float) -> None:
        self.slots.setdefault(key, []).append(now)

    def release(self, key: str) -> None:
        if self.slots.get(key):
            self.slots[key].pop(0)


class FreeLimits(CustomLogger):
    def __init__(self, rules: dict | None = None, clock=time.time, store: MemoryStore | None = None,
                 prisma=None, deployments=None) -> None:
        super().__init__()
        rules = load_rules() if rules is None else rules
        self.limits = {route: entry for route, entry in (rules.get("free_limits") or {}).items() if not route.startswith("_")}
        self.cooldowns = rules.get("cooldowns") or {}
        self.clock = clock
        self.store = store or MemoryStore()
        # model_info.id -> the deployment last sent, so a logged call is matched to its limits
        self.by_id: dict[str, dict] = {}
        # Injected in tests; in the proxy both come from proxy_server, once, on first use.
        self._prisma, self._deployments = prisma, deployments
        self._seeded = False
        self._retry_at = 0.0
        self._seed_lock = None

    # --- which deployments are ours, and their keys ---------------------------------------------------

    @staticmethod
    def _parts(deployment: Any) -> tuple[dict, dict, str]:
        deployment = deployment if isinstance(deployment, dict) else {}
        return deployment.get("litellm_params") or {}, deployment.get("model_info") or {}, deployment.get("model_name") or ""

    def route_of(self, deployment: Any) -> str | None:
        """The free route of a deployment, or None for a deployment this callback leaves alone.

        Ours: a deployment of a free-only route that records no price (catalog/sync.py FREE_ONLY less FREE_PRICED),
        and a free id of a paid route (OpenCode Go's "-free" ids, in a free group). Never a deployment that names
        a paid account (model_info.account) or carries a budget.
        """
        params, info, name = self._parts(deployment)
        route = info.get("route") or params.get("litellm_credential_name")
        if not route or info.get("account") or "max_budget" in params:
            return None
        if route in FREE_ROUTES or route in self.limits:
            return route
        free_id = (name.startswith("free/") or name.startswith(f"{route}-free/") or str(info.get("id", "")).startswith("free/")
                   or (params.get("input_cost_per_token") == 0 and params.get("output_cost_per_token") == 0))
        return route if route in FREE_ID_ROUTES and free_id else None

    @staticmethod
    def deployment_key(route: str, params: dict) -> str:
        return f"{route}|{params.get('model')}"

    def _entry(self, route: str) -> dict:
        return self.limits.get(route) or {}

    def _offset(self, route: str, params: dict | None = None) -> float:
        reset = (params or {}).get("daily_reset_time") or self._entry(route).get("reset")
        return reset_offset(reset)

    def _windows(self, route: str, params: dict) -> list[tuple[str, str, str, int]]:
        """(counter scope key, unit, per, limit) for each window this deployment is held to."""
        own = self.deployment_key(route, params)
        out = []
        for name, unit, per in (("rpm", "requests", "minute"), ("rpd", "requests", "day"),
                                ("tpm", "tokens", "minute"), ("tpd", "tokens", "day")):
            if isinstance(params.get(name), int) and params[name] > 0:
                out.append((f"d:{own}", unit, per, params[name]))
        for window in self._entry(route).get("shared") or []:
            unit = "requests" if "requests" in window else "tokens"
            out.append((f"a:{route}", unit, window["per"], window[unit]))
        return out

    def _counter(self, scope: str, unit: str, per: str, route: str, params: dict, now: float) -> tuple[str, float]:
        key, ends = window_key(per, now, self._offset(route, params))
        return f"{scope}|{unit}|{per}|{key}", ends

    # --- why a deployment is out, and until when ------------------------------------------------------

    def unavailable_until(self, deployment: Any) -> float:
        """0 when the deployment may be picked now, else the moment it may be picked again."""
        route = self.route_of(deployment)
        if route is None:
            return 0.0
        params, _, _ = self._parts(deployment)
        now = self.clock()
        until = max(self.store.blocked_until(f"a:{route}", now),
                    self.store.blocked_until(f"d:{self.deployment_key(route, params)}", now))
        for scope, unit, per, limit in self._windows(route, params):
            key, ends = self._counter(scope, unit, per, route, params, now)
            if self.store.get(key, now) >= limit:
                until = max(until, ends)
        concurrency = self._entry(route).get("concurrency")
        if concurrency and self.store.in_flight(f"a:{route}", now) >= concurrency:
            until = max(until, now + 1)
        return until

    # --- LiteLLM hooks --------------------------------------------------------------------------------

    # --- after a restart: the counts so far, from the spend log -----------------------------------------

    def _database(self):
        """(prisma client, the router's deployments) from the running proxy, or (None, []) without one."""
        if self._prisma is not None or self._deployments is not None:
            return self._prisma, self._deployments or []
        try:
            from litellm.proxy import proxy_server
        except Exception:  # noqa: BLE001  # not running inside the proxy
            return None, []
        router = getattr(proxy_server, "llm_router", None)
        return getattr(proxy_server, "prisma_client", None), (router.get_model_list() if router else None) or []

    async def _seed(self) -> None:
        """Count what the spend log holds for the current windows, once per start.

        The counters are in memory: without this a restart would let every daily and monthly budget be spent
        again. Rows are hourly sums per model_id; an hour counts in a window when the hour starts inside it, so
        a window that turns mid-hour (a daily reset at HH:30) is seeded at hour precision. A block learned from a
        429 is not in the log: after a restart the first 429 of a provider is learned again.
        """
        if self._seeded or self.clock() < self._retry_at:
            return
        if self._seed_lock is None:
            self._seed_lock = asyncio.Lock()
        async with self._seed_lock:
            if self._seeded:
                return
            try:
                prisma, deployments = self._database()
                if prisma is None:
                    LOG.warning("free_limits: no database, free-route counts start from zero at every restart")
                    self._seeded = True
                    return
                ours = {}
                for deployment in deployments:
                    info = deployment.get("model_info") or {}
                    if info.get("id") and self.route_of(deployment):
                        ours[info["id"]] = deployment
                now = self.clock()
                since = min(window_key("month", now, 0)[1] - 31 * 86400, now - 2 * 86400)
                since = datetime.datetime.fromtimestamp(since, UTC).strftime("%Y-%m-%d %H:%M:%S")
                rows = await asyncio.wait_for(prisma.db.query_raw(SEED_SQL, since), SEED_TIMEOUT)
                seeded = 0
                for row in rows or []:
                    deployment = ours.get(row.get("model_id"))
                    if deployment is None:
                        continue
                    hour = row.get("hour")
                    if isinstance(hour, str):
                        hour = datetime.datetime.fromisoformat(hour.replace("Z", "+00:00"))
                    if hour.tzinfo is None:
                        hour = hour.replace(tzinfo=UTC)
                    self.count_past(deployment, hour.timestamp(), int(row.get("n") or 0), int(row.get("tokens") or 0), now)
                    seeded += 1
                LOG.warning("free_limits: seeded %d hourly rows from the spend log", seeded)
                self._seeded = True
            except Exception as error:  # noqa: BLE001  # never hold a request up over the spend log
                LOG.warning("free_limits: could not read the spend log (%s); counting from zero, trying again later", error)
                self._retry_at = self.clock() + SEED_RETRY

    def count_past(self, deployment: dict, at: float, requests: int, tokens: int, now: float) -> None:
        """Add requests and tokens made at `at` to every current window of `deployment` that contains `at`."""
        route = self.route_of(deployment)
        if route is None:
            return
        params, _, _ = self._parts(deployment)
        for scope, unit, per, _ in self._windows(route, params):
            if per == "minute":
                continue
            offset = self._offset(route, params)
            if window_key(per, at, offset)[0] != window_key(per, now, offset)[0] or at > now:
                continue
            key, ends = self._counter(scope, unit, per, route, params, now)
            self.store.add(key, requests if unit == "requests" else tokens, ends, now)

    async def async_filter_deployments(self, model, healthy_deployments, messages, request_kwargs=None, parent_otel_span=None):
        if isinstance(healthy_deployments, dict):
            healthy_deployments = [healthy_deployments]
        if not healthy_deployments:
            return healthy_deployments
        if any(self.route_of(deployment) for deployment in healthy_deployments):
            await self._seed()
        kept, out = [], []
        for deployment in healthy_deployments:
            until = self.unavailable_until(deployment)
            (out if until else kept).append((deployment, until))
        if out and not kept:
            soonest = min(until for _, until in out)
            raise ValueError(f"{LIMIT_ERROR}: every free deployment of {model} is limited; the first is back in "
                             f"{max(0, int(soonest - self.clock()))}s")
        return [deployment for deployment, _ in kept]

    async def async_pre_call_check(self, deployment, parent_otel_span=None):
        """Count the attempt just before it goes out; refuse it when a concurrent request took the last unit."""
        route = self.route_of(deployment)
        if route is None:
            return deployment
        until = self.unavailable_until(deployment)
        if until:
            import litellm
            import httpx
            raise litellm.RateLimitError(
                message=f"{LIMIT_ERROR}: {route} is limited",
                llm_provider="",
                model=(deployment.get("litellm_params") or {}).get("model", ""),
                response=httpx.Response(status_code=429, headers={"retry-after": str(max(1, int(until - self.clock())))},
                                        request=httpx.Request("POST", "https://free-limits.invalid")),
            )
        params, info, _ = self._parts(deployment)
        if info.get("id"):
            self.by_id[info["id"]] = deployment
        now = self.clock()
        for scope, unit, per, _ in self._windows(route, params):
            if unit == "requests":
                key, ends = self._counter(scope, unit, per, route, params, now)
                self.store.add(key, 1, ends, now)
        if self._entry(route).get("concurrency"):
            self.store.acquire(f"a:{route}", now)
        return deployment

    def _deployment_of(self, kwargs: dict) -> dict:
        """The deployment a logged call went to: the one async_pre_call_check saw, else rebuilt from the kwargs."""
        params = kwargs.get("litellm_params") or {}
        metadata = params.get("metadata") or {}
        info = params.get("model_info") or metadata.get("model_info") or (kwargs.get("standard_logging_object") or {}).get("model_info") or {}
        known = self.by_id.get(info.get("id") or "")
        if known is not None:
            return known
        rebuilt = {key: params[key] for key in ("rpm", "rpd", "tpm", "tpd", "daily_reset_time", "litellm_credential_name",
                                                "input_cost_per_token", "output_cost_per_token", "max_budget") if key in params}
        rebuilt["model"] = metadata.get("deployment") or params.get("model") or kwargs.get("model")
        return {"model_name": metadata.get("model_group") or "", "litellm_params": rebuilt, "model_info": info}

    def _finished(self, route: str) -> None:
        if self._entry(route).get("concurrency"):
            self.store.release(f"a:{route}")

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        try:
            deployment = self._deployment_of(kwargs)
            route = self.route_of(deployment)
            if route is None:
                return
            self._finished(route)
            params = deployment["litellm_params"]
            now = self.clock()
            usage = getattr(response_obj, "usage", None) or (kwargs.get("standard_logging_object") or {})
            tokens = (getattr(usage, "total_tokens", None) if not isinstance(usage, dict) else usage.get("total_tokens")) or 0
            if tokens:
                for scope, unit, per, _ in self._windows(route, params):
                    if unit == "tokens":
                        key, ends = self._counter(scope, unit, per, route, params, now)
                        self.store.add(key, tokens, ends, now)
            hidden = getattr(response_obj, "_hidden_params", None) or {}
            headers = dict(hidden.get("additional_headers") or {})
            payload_headers = ((kwargs.get("standard_logging_object") or {}).get("hidden_params") or {}).get("additional_headers") or {}
            headers = {**payload_headers, **headers}
            self.learn_headers(route, params, headers)
        except Exception as error:  # noqa: BLE001  # logging must never fail a served request
            LOG.warning("free_limits: success not recorded (%s)", error)

    def learn_headers(self, route: str, params: dict, headers: dict) -> None:
        """Block when a success says nothing is left: x-ratelimit-remaining-* 0 until its x-ratelimit-reset-*."""
        now = self.clock()
        for kind in ("requests", "tokens", ""):
            suffix = f"-{kind}" if kind else ""
            remaining = header(headers, f"x-ratelimit-remaining{suffix}")
            try:
                left = float(remaining)
            except (TypeError, ValueError):
                continue
            if left > 0:
                continue
            until = parse_reset(header(headers, f"x-ratelimit-reset{suffix}"), now)
            if until is None:
                until = now + float(self.cooldowns.get(route, DEFAULT_BLOCK))
            self._block(route, params, until, account=False)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        try:
            deployment = self._deployment_of(kwargs)
            route = self.route_of(deployment)
            if route is None:
                return
            self._finished(route)
            exception = kwargs.get("exception")
            self.learn_failure(route, deployment["litellm_params"], exception)
        except Exception as error:  # noqa: BLE001
            LOG.warning("free_limits: failure not recorded (%s)", error)

    def learn_failure(self, route: str, params: dict, exception: Any) -> None:
        """Block after a 429, a 402, a quota error or a dead copy, until the provider says, or the best guess.

        A dead copy (401, 403, 404, or a 400 whose text says the model is deprecated, gone or missing) blocks
        its own deployment for DEAD_BLOCK, so the router takes the next copy of the group. A plain 400 never
        blocks. Our own LIMIT_ERROR refusal never blocks.
        """
        if exception is None:
            return
        status = getattr(exception, "status_code", None)
        text = str(getattr(exception, "message", "") or exception)
        if LIMIT_ERROR in text:
            return  # our own refusal: nothing new was learned
        dead = status in DEAD_STATUS or (status == 400 and DEAD_MODEL_ERROR.search(text) is not None)
        if status not in (402, 429) and not QUOTA_ERROR.search(text) and not dead:
            return
        now = self.clock()
        headers = {}
        for source in (getattr(exception, "litellm_response_headers", None), getattr(getattr(exception, "response", None), "headers", None),
                       getattr(exception, "headers", None)):
            if source:
                try:
                    headers.update({str(k).lower(): v for k, v in dict(source).items()})
                except Exception:  # noqa: BLE001
                    pass
        until = parse_reset(header(headers, "retry-after"), now)
        if until is None:
            for name in ("x-ratelimit-reset-requests", "x-ratelimit-reset", "x-ratelimit-reset-tokens"):
                until = parse_reset(header(headers, name), now)
                if until is not None:
                    break
        if until is None and dead:
            until = now + DEAD_BLOCK
        if until is None and DAILY_ERROR.search(text):
            _, until = window_key("day", now, self._offset(route, params))
        if until is None:
            until = now + float(self.cooldowns.get(route, DEFAULT_BLOCK))
        entry = self._entry(route)
        account = (status == 402 or entry.get("scope") == "account"
                   or bool(entry.get("account_match") and re.search(entry["account_match"], text, re.I)))
        self._block(route, params, until, account=account)

    def _block(self, route: str, params: dict, until: float, account: bool) -> None:
        scope = self._entry(route).get("scope", "deployment")
        key = f"a:{route}" if account or scope == "account" else f"d:{self.deployment_key(route, params)}"
        self.store.block(key, until)
        LOG.warning("free_limits: %s unavailable for %ss", key, int(until - self.clock()))


free_limits = FreeLimits()
