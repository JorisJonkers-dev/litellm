"""One spending cap per paid account, across every deployment the account serves.

LiteLLM's own budget (`max_budget` on a deployment) is a counter per deployment, and the router
keeps its counter per `model_info.id`: a route with N models spends N times its cap. Sharing one id
across an account's deployments would share the counter, but cooldown and failover are keyed by id
too, so one 429 on one model would take every model of the account offline. So each deployment keeps
its own id, and this callback keeps the account's counter instead.

- A deployment names its account in `model_info.account` (the sync writes it from `accounts` in
  catalog/routing.json; the route wildcards in config.yaml carry it by hand).
- The caps and windows are read from catalog/routing.json (the image carries it next to the sync).
- After each successful call the response cost is added to the account's counter.
- Before each pick, a deployment whose account is spent is taken out of the candidates. When none is
  left the call fails with the router's own budget error, so fallbacks behave as for a LiteLLM budget.
- The window is the declared one: a calendar month from the 1st (UTC), or a cycle anchored on the
  account's own subscription.
- The counter lives in this process's memory, so after a restart it is seeded once from the proxy's
  spend log (LiteLLM_SpendLogs, summed per `model_id` since the window start and mapped to accounts
  through the router's deployments). With no database, or when the query fails or is slow, requests
  are never held up: it logs once and keeps counting in memory.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import sys
import time
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

HERE = os.path.dirname(os.path.abspath(__file__))
# In the image the catalogue sits at /app/catalog next to this file; in the repository it is ../catalog.
CATALOG = next((path for path in (os.path.join(HERE, "catalog"), os.path.join(os.path.dirname(HERE), "catalog"))
                if os.path.exists(os.path.join(path, "routing.json"))), None)
if CATALOG and CATALOG not in sys.path:
    sys.path.append(CATALOG)
import sync  # noqa: E402  (the window arithmetic lives with the rules that declare the windows)

UTC = datetime.timezone.utc
LOG = logging.getLogger("account_budget")
SEED_TIMEOUT = 5.0  # seconds a request may wait for the spend log, once
SEED_RETRY = 60.0  # seconds before a failed seed is tried again
SEED_SQL = ('SELECT model_id, COALESCE(SUM(spend), 0) AS spend FROM "LiteLLM_SpendLogs" '
            'WHERE "startTime" >= $1::timestamp GROUP BY model_id')
BUDGET_ERROR = "No deployments available for selected model, Try again in a moment: account budget"


def load_accounts(path: str | None = None) -> dict:
    path = path or os.path.join(CATALOG or HERE, "routing.json")
    with open(path) as handle:
        return json.load(handle).get("accounts") or {}


class AccountBudget(CustomLogger):
    def __init__(self, accounts: dict | None = None, clock=time.time, prisma=None, id_accounts=None) -> None:
        super().__init__()
        self.accounts = load_accounts() if accounts is None else accounts
        self.clock = clock
        # Injected in tests; in the proxy both come from proxy_server, once, on first use.
        self._prisma, self._id_accounts = prisma, id_accounts
        self._seeded = False
        self._retry_at = 0.0
        self._seed_lock = None
        # account -> [window start (epoch seconds), spend in that window]
        self.counters: dict[str, list[float]] = {}

    def _window(self, account: str, now: float) -> float | None:
        window = (self.accounts.get(account) or {}).get("window") or {}
        start = sync.window_start(window, datetime.datetime.fromtimestamp(now, UTC))
        return start.timestamp() if start else None

    def spent(self, account: str) -> float:
        """The account's spend in its current window; a new window starts from zero."""
        now = self.clock()
        counter = self.counters.get(account)
        if counter is None:
            return 0.0
        declared = self._window(account, now)
        if declared is None:
            # No declared start (a rolling window): it opens at the first spend and lasts `days`.
            days = ((self.accounts.get(account) or {}).get("window") or {}).get("days", 30)
            if now - counter[0] >= days * 86400:
                del self.counters[account]
                return 0.0
        elif counter[0] != declared:
            del self.counters[account]
            return 0.0
        return counter[1]

    def add(self, account: str, cost: float) -> None:
        if account not in self.accounts or not cost:
            return
        self.spent(account)  # opens a fresh counter when the window has turned
        now = self.clock()
        counter = self.counters.setdefault(account, [self._window(account, now) or now, 0.0])
        counter[1] += cost

    def over(self, account: str | None) -> bool:
        entry = self.accounts.get(account or "")
        return bool(entry) and self.spent(account) >= entry["max_budget"]

    @staticmethod
    def account_of(deployment: Any) -> str | None:
        info = (deployment.get("model_info") if isinstance(deployment, dict) else None) or {}
        return info.get("account")

    def _database(self):
        """(prisma client, {model_id: account}) from the running proxy, or (None, {}) without one."""
        if self._prisma is not None or self._id_accounts is not None:
            return self._prisma, self._id_accounts or {}
        try:
            from litellm.proxy import proxy_server
        except Exception:  # noqa: BLE001  # not running inside the proxy
            return None, {}
        router = getattr(proxy_server, "llm_router", None)
        ids = {}
        for entry in (router.get_model_list() if router else None) or []:
            info = entry.get("model_info") or {}
            if info.get("id") and info.get("account"):
                ids[info["id"]] = info["account"]
        return getattr(proxy_server, "prisma_client", None), ids

    async def _seed(self) -> None:
        """Add the spend the spend log holds for each account's current window, once per start."""
        if self._seeded or self.clock() < self._retry_at:
            return
        if self._seed_lock is None:
            self._seed_lock = asyncio.Lock()
        async with self._seed_lock:
            if self._seeded:
                return
            try:
                prisma, ids = self._database()
                if prisma is None:
                    LOG.warning("account_budget: no database, account spend is counted in memory only and a restart resets it")
                    self._seeded = True
                    return
                now = self.clock()
                starts = {}
                for account, entry in self.accounts.items():
                    declared = self._window(account, now)
                    days = (entry.get("window") or {}).get("days", 30)
                    starts[account] = declared if declared is not None else now - days * 86400
                earliest = datetime.datetime.fromtimestamp(min(starts.values()), UTC).strftime("%Y-%m-%d %H:%M:%S")
                rows = await asyncio.wait_for(prisma.db.query_raw(SEED_SQL, earliest), SEED_TIMEOUT)
                totals: dict[str, float] = {}
                for row in rows or []:
                    account = ids.get(row.get("model_id"))
                    if account in self.accounts:
                        totals[account] = totals.get(account, 0.0) + float(row.get("spend") or 0.0)
                for account, spent in totals.items():
                    self.add(account, spent)
                LOG.warning("account_budget: seeded from the spend log: %s", {a: round(v, 6) for a, v in totals.items()})
                self._seeded = True
            except Exception as error:  # noqa: BLE001  # never hold a request up over the spend log
                LOG.warning("account_budget: could not read the spend log (%s); counting in memory, trying again later", error)
                self._retry_at = self.clock() + SEED_RETRY

    async def async_filter_deployments(self, model, healthy_deployments, messages, request_kwargs=None, parent_otel_span=None):
        if isinstance(healthy_deployments, dict):
            healthy_deployments = [healthy_deployments]
        if not healthy_deployments:
            return healthy_deployments
        await self._seed()
        kept = [deployment for deployment in healthy_deployments if not self.over(self.account_of(deployment))]
        if not kept:
            spent = sorted({self.account_of(d) for d in healthy_deployments})
            raise ValueError(f"{BUDGET_ERROR} {spent}")
        return kept

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        params = kwargs.get("litellm_params") or {}
        info = params.get("model_info") or (params.get("metadata") or {}).get("model_info") or {}
        account = info.get("account")
        if not account:
            payload = kwargs.get("standard_logging_object") or {}
            account = (payload.get("model_info") or {}).get("account")
        cost = kwargs.get("response_cost")
        if cost is None:
            cost = (kwargs.get("standard_logging_object") or {}).get("response_cost")
        if account and isinstance(cost, (int, float)) and not isinstance(cost, bool):
            self.add(account, float(cost))


account_budget = AccountBudget()
