#!/usr/bin/env python3
"""Sync the model catalogue, and the LiteLLM deployments the catalogue's probes justify.

A provider's model list is not what an account can use (a Go model hosted outside the region
the organisation allows is listed and refused). So the sync lists each route's models from
its provider, then PROBES each one at that provider with one minimal request (max_tokens 16)
and the route's own upstream key, and classifies the result:

  usable            the request was answered
  blocked           the account is refused (region, privacy setting, disabled); the reason text is kept
  unknown           the model is gone or never existed on this provider
  budget-exhausted  credit or quota is spent
  error             anything else (rate limit, 5xx, timeout); tried again after a back-off (1 h, doubling, at most --max-age)
  not-chat          the provider lists it as no chat model (embeddings, OCR, transcription)
  unprobed          not probed on purpose (a paid OpenRouter model: the route is used for `:free` ids)
  pending           due for a probe but over this run's cap; probed in a later run

`--probe` goes to the provider, not through the proxy: a model the proxy does not know yet
cannot be probed through it, and the catalogue is what decides which models the proxy serves.

The result is cached in the catalogue itself (probed_at per model), so a recorded answer is kept and never re-probed on a timer. A model that disappears from its provider's list
is kept for one run (metadata.absent_runs) and dropped on the second, so a provider that hiccups
does not empty the catalogue. metadata.failing_probes counts consecutive blocked or unknown
probes; two of them drop the model. A model that was usable keeps its place through a transient
state (metadata.was_usable), so one bad probe does not take a deployment away.

Routing lives in git, as plain LiteLLM config. plan_groups() turns the catalogue and
catalog/routing.json into one deployment per (canonical model, account) the catalogue probed
usable, render_models() writes them as config/models.generated.yaml, and main() opens or updates
a pull request in the litellm repository from a bot branch. The sync never writes to the live
proxy and needs no master key.

Canonical groups: one LiteLLM model_name per canonical model (mistral-large, devstral,
gemini-2.5-pro, claude-sonnet-4-5, glm-5.3) whose deployments are the routes that serve it, ordered
by catalog/routing.json. Deployments that share a model_name form a group in LiteLLM: `order` is
tried lowest first and the router falls back to the next level when a level fails or is cooled
down, `weight` splits traffic inside a level, and max_budget/budget_duration cap one deployment, and model_info.account names the paid account whose cap config/account_budget.py enforces across all its deployments.
Routing is by which provider serves the canonical model, not by model family: the Vibe plan
serves non-Mistral models too, and where it serves one it wins. Only exact canonical-name
equality merges two upstream models; a near duplicate is reported in catalogue.possible_duplicates
and never merged.

Free groups: beside each free route's own "<route>/<canonical>" group, every zero-cost copy of a
canonical model (matched by canonical_name(), whatever the provider calls it) is also in one shared
"free/<canonical>" group, ordered by allowance, each copy with its own cooldown, so a 429 falls
through to the next free copy. assert_free_groups() fails the run when a copy in it is not zero-cost.

Tier groups: catalog/routing.json's `tier_groups` define the callable tier-* names and their ordered
steps; validate_tiers() checks them and `--render-tiers` renders them into config/config.yaml.

The sync sends no chat request by itself: a request spends a free tier's daily quota. A model's state comes from
the live listing (a free call), the public catalogue models.dev joined by id, and real traffic through the proxy
(see "Where the evidence" at TRAFFIC_WINDOW); `--probe <route/model>` sends one request, for debugging.

Standard library only: it runs in the LiteLLM image as a scheduled Process, and in CI.
"""
import argparse
import base64
import copy as copy_module
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

# route, the env names its provider base and key come from (config/config.yaml names the same),
# and how its list is read. The four Mistral accounts are listed and probed separately because
# their model sets differ, and a route whose key is not written yet is skipped until it is.
ROUTES = [
    {"route": "vibe", "base_env": "MISTRAL_API_BASE", "key_env": "VIBE_API_KEY", "kind": "mistral"},
    {"route": "vibe-study", "base_env": "MISTRAL_API_BASE", "key_env": "VIBE_STUDY_API_KEY", "kind": "mistral"},
    {"route": "vibe-api", "base_env": "MISTRAL_API_BASE", "key_env": "VIBE_API_GENERAL_KEY", "kind": "mistral"},
    {"route": "vibe-study-api", "base_env": "MISTRAL_API_BASE", "key_env": "VIBE_STUDY_API_GENERAL_KEY", "kind": "mistral"},
    # OpenCode Go is listed at its own base (https://opencode.ai/inference/go/v1), whose /models names
    # every Go model with the API family and base each one is served on. The proxy itself has no base:
    # LiteLLM's opencode_go provider picks the family per model.
    {"route": "opencode-go", "base_env": "OPENCODE_GO_API_BASE", "key_env": "OPENCODE_GO_API_KEY", "kind": "opencode-go"},
    {"route": "openrouter", "base_env": "OPENROUTER_API_BASE", "key_env": "OPENROUTER_API_KEY", "kind": "openrouter"},
    {"route": "local", "base_env": "LOCAL_LLM_API_BASE", "key_env": "LOCAL_LLM_API_KEY", "kind": "openai"},
    {"route": "google", "base_env": "GOOGLE_API_BASE", "key_env": "GEMINI_API_KEY", "kind": "google"},
    {"route": "anthropic", "base_env": "ANTHROPIC_API_BASE", "key_env": "ANTHROPIC_API_KEY", "kind": "anthropic"},
    # Free-only routes (agent-kit#916): a paid id is never deployed on these. Their free signal is the
    # kind (see list_models): a plan-wide free tier lists its free ids in routing.json (`free_ids`).
    {"route": "groq", "base_env": "GROQ_API_BASE", "key_env": "GROQ_API_KEY", "kind": "groq"},
    # The account id is part of Cloudflare's base, so the base is composed from CLOUDFLARE_ACCOUNT_ID.
    {"route": "cloudflare", "base_env": "CLOUDFLARE_API_BASE", "key_env": "CLOUDFLARE_API_KEY", "kind": "cloudflare",
     "base_template": "https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1"},
    {"route": "nvidia", "base_env": "NVIDIA_NIM_API_BASE", "key_env": "NVIDIA_NIM_API_KEY", "kind": "nvidia"},
    {"route": "zai", "base_env": "ZAI_API_BASE", "key_env": "ZAI_API_KEY", "kind": "zai"},
    # Anonymous routes have no key: listed and probed without an Authorization header. Their key_env is
    # never set, so only "anonymous" lets them past the missing-key skip.
    {"route": "kilo", "base_env": "KILO_API_BASE", "key_env": "KILO_API_KEY", "kind": "kilo", "anonymous": True},
    {"route": "ovh", "base_env": "OVHCLOUD_API_BASE", "key_env": "OVHCLOUD_API_KEY", "kind": "ovh", "anonymous": True},
    {"route": "requesty", "base_env": "REQUESTY_API_BASE", "key_env": "REQUESTY_API_KEY", "kind": "requesty"},
    # AxForge is not registered yet: its key is empty, so the sync skips the route like any keyless one.
    # It is also OFF by data: routing.json's free_ids.axforge is empty, because the site's "3M free
    # serverless tokens every 30 days" is followed by billing, and a zero recorded cost with no budget
    # would hide that spend. It deploys nothing until the owner has checked billing and filled the list.
    {"route": "axforge", "base_env": "AXFORGE_API_BASE", "key_env": "AXFORGE_API_KEY", "kind": "axforge"},
    {"route": "naga", "base_env": "NAGA_API_BASE", "key_env": "NAGA_API_KEY", "kind": "naga"},
    {"route": "routeway", "base_env": "ROUTEWAY_API_BASE", "key_env": "ROUTEWAY_API_KEY", "kind": "routeway"},
    {"route": "inferbase", "base_env": "INFERBASE_API_BASE", "key_env": "INFERBASE_API_KEY", "kind": "inferbase"},
    # Second wave (agent-kit#916, litellm#64). Bases come from the spec, or from the default base of LiteLLM
    # 1.109.1's own provider where the spec names none (poe, vercel, together, neosantara). A route whose base
    # the spec does not give (bazaarlink, cohere, watsonx) has its base in an optional Vault field (bazaarlink_api_base,
    # cohere_api_base, watsonx_api_base): with a key and no field it is one route error naming the field, never a guess,
    # and it deploys nothing until the owner writes the field.
    # No free signal in the listing: the free ids are the static list in routing.json (empty until approved,
    # so the candidate scan shows what the provider lists). Cohere and watsonx are not OpenAI-style lists.
    {"route": "bazaarlink", "base_env": "BAZAARLINK_API_BASE", "key_env": "BAZAARLINK_API_KEY", "kind": "bazaarlink",
     "default_base": "https://api.bazaarlink.ai/v1"},
    {"route": "cohere", "base_env": "COHERE_LIST_BASE", "key_env": "COHERE_API_KEY", "kind": "cohere",
     "default_base": "https://api.cohere.com/v1"},
    {"route": "plugsky", "base_env": "PLUGSKY_API_BASE", "key_env": "PLUGSKY_API_KEY", "kind": "plugsky"},
    {"route": "poe", "base_env": "POE_API_BASE", "key_env": "POE_API_KEY", "kind": "poe"},
    {"route": "watsonx", "base_env": "WATSONX_API_BASE", "key_env": "WATSONX_API_KEY", "kind": "watsonx",
     "needs": ["WATSONX_PROJECT_ID"]},
    {"route": "together", "base_env": "TOGETHER_API_BASE", "key_env": "TOGETHER_API_KEY", "kind": "together"},
    {"route": "typhoon", "base_env": "TYPHOON_API_BASE", "key_env": "TYPHOON_API_KEY", "kind": "typhoon"},
    {"route": "sealion", "base_env": "SEALION_API_BASE", "key_env": "SEALION_API_KEY", "kind": "sealion"},
    {"route": "gonka", "base_env": "GONKA_API_BASE", "key_env": "GONKA_API_KEY", "kind": "gonka"},
    {"route": "longcat", "base_env": "LONGCAT_API_BASE", "key_env": "LONGCAT_API_KEY", "kind": "longcat"},
    {"route": "neosantara", "base_env": "NEOSANTARA_API_BASE", "key_env": "NEOSANTARA_API_KEY", "kind": "neosantara"},
    # Fail-closed free signals in the listing itself: a `:free` suffix (Electron Hub, UnoRouter), `tier == "free"`
    # (Api.Airforce) or a zero price (Vercel, Novita). UnoRouter's free models need no key: anonymous.
    {"route": "electronhub", "base_env": "ELECTRONHUB_API_BASE", "key_env": "ELECTRONHUB_API_KEY", "kind": "electronhub"},
    {"route": "unorouter", "base_env": "UNOROUTER_API_BASE", "key_env": "UNOROUTER_API_KEY", "kind": "unorouter", "anonymous": True},
    {"route": "airforce", "base_env": "AIRFORCE_API_BASE", "key_env": "AIRFORCE_API_KEY", "kind": "airforce"},
    {"route": "vercel", "base_env": "VERCEL_AI_GATEWAY_API_BASE", "key_env": "VERCEL_AI_GATEWAY_API_KEY", "kind": "vercel"},
    {"route": "novita", "base_env": "NOVITA_API_BASE", "key_env": "NOVITA_API_KEY", "kind": "novita"},
]
# OpenCode Go models the docs say need an organisation setting (a privacy opt-in or a region):
# flagged in the catalogue as a hint; the probe still decides what the account can use.
OPT_IN_HINTS = ("deepseek-v4", "longcat-2.5", "muse-spark", "beam-free")
# Why a paid model on a free-only route has no probe.
PAID_REASON = {"openrouter": "paid OpenRouter models are not probed"}
# A route whose free ids are approved by hand says so: "paid" would hide that the owner can approve the id.
PAID_ONLY_REASON = "the provider lists it as needing a paid plan (paid_only in catalog/routing.json); it is not probed"
APPROVAL_REASON = "not in free_ids of catalog/routing.json (a free-plan id is approved there); it is not probed"
# Where the evidence for a model comes from, in order (see build()). The sync sends no chat request unless a person asks
# for one (`--probe <route/model>`, for debugging): on a free tier a request spends the daily quota.
#   1. the live listing (a free call): chat, free, served, retired;
#   2. the public catalogue models.dev, joined by id (reference_index()): chat or not, price (0 is free), deprecated;
#   3. real traffic through the proxy (Prometheus, TRAFFIC_PROMETHEUS_URL): served requests make a deployment usable,
#      401, 403 or 404 block it until the evidence ages out of the window;
#   4. the answer already recorded.
TRAFFIC_WINDOW = "7d"
DENIED_STATUSES = ("401", "403", "404")
# Free-only routes whose free ids are named in routing.json (`free_ids`): the plan is free, the list is not.
STATIC_KINDS = ("groq", "cloudflare", "zai", "axforge", "inferbase", "bazaarlink", "cohere", "plugsky", "poe", "watsonx",
                "together", "typhoon", "sealion", "gonka", "longcat", "neosantara")
MODELS_DEV_URL = "https://models.dev/api.json"
# An answer already recorded (an earlier probe or traffic) is kept; an error or a spent budget is decided again.
RECORDED = ("usable", "blocked", "unknown")
STATIC_ROUTES = frozenset(entry["route"] for entry in ROUTES if entry["kind"] in STATIC_KINDS)
STATUSES = ("usable", "blocked", "unknown", "budget-exhausted", "error", "not-chat", "unprobed", "pending")
# The states a model that was usable keeps its place through: one bad probe is not a removal.
TRANSIENT = ("error", "budget-exhausted", "pending", "blocked", "unknown")
# A probe asks for the smallest answer that still proves the account may use the model.
PROBE_MAX_TOKENS = 16
# Some providers refuse a max_tokens below their own floor and name it: "Expected a value >= 32".
PROBE_MINIMUM = re.compile(r">= (\d+)")
# The retry honours the provider's floor, but never a number that would make a probe expensive.
PROBE_RETRY_CEILING = 256
# The LiteLLM provider prefix per route kind: what the proxy hands the upstream id to.
PREFIXES = {"mistral": "mistral", "openai": "openai", "opencode-go": "opencode_go", "openrouter": "openrouter",
            "google": "gemini", "anthropic": "anthropic", "groq": "groq", "cloudflare": "cloudflare",
            "nvidia": "nvidia_nim", "zai": "zai", "kilo": "openai", "ovh": "ovhcloud", "requesty": "openai",
            "axforge": "openai", "naga": "openai", "routeway": "openai", "inferbase": "openai",
            "bazaarlink": "openai", "cohere": "cohere_chat", "plugsky": "openai", "poe": "poe", "watsonx": "watsonx",
            "together": "together_ai", "typhoon": "openai", "sealion": "openai", "gonka": "openai", "longcat": "openai",
            "neosantara": "neosantara", "electronhub": "openai", "unorouter": "openai", "airforce": "openai",
            "vercel": "vercel_ai_gateway", "novita": "novita"}
# The API families OpenCode Go names per model ("api" in its /models), in the order one is chosen.
GO_FAMILIES = ("chat-completions", "responses", "messages", "generate-content")
# Kinds whose route is free only: a paid id is never deployed, so the route cannot cost anything.
FREE_KINDS = ("openrouter", "groq", "cloudflare", "nvidia", "zai", "kilo", "ovh", "requesty", "axforge", "naga",
              "routeway", "inferbase", "bazaarlink", "cohere", "plugsky", "poe", "watsonx", "together", "typhoon", "sealion",
              "gonka", "longcat", "neosantara", "electronhub", "unorouter", "airforce", "vercel", "novita")
FREE_ONLY = frozenset(entry["route"] for entry in ROUTES if entry["kind"] in FREE_KINDS)
# A free-only route is public work only: a free-tier provider may train on the prompts, so its
# deployments are never in `canonical` (the group every key holds). Each one gets its own
# "<route>/<canonical>" group and its own access group, so a key reaches it only by holding that
# group, and that key reaches nothing paid. One group per route, not one shared free group: a
# shared group would let every free key reach every free provider, and LiteLLM grants access per
# model name, so two routes in one name would leak each other's deployments. This is code, not a rule in routing.json, so no rules
# edit can put a free deployment back in `canonical`.
FREE_ACCESS_GROUPS = {entry["route"]: f"{entry['route']}-free-public" for entry in ROUTES if entry["kind"] in FREE_KINDS}
# A free deployment the price map does not know records a real cost of 0, not an unknown. Inferbase's
# free share is a dollar allowance, so its deployments keep their price and the route's budget caps it.
FREE_PRICED = frozenset({"inferbase"})
assert set(FREE_ACCESS_GROUPS) == FREE_ONLY
# The routes whose deployments may join a shared "free/<canonical>" group (agent-kit#916, litellm#60):
# the free-only routes that record a zero cost, plus the free ids of a paid route (FREE_ID_ROUTES).
# A route that bills against a dollar allowance (FREE_PRICED) is never in it. The sync asserts the
# zero cost of every deployment in the group (assert_free_groups), so a route added here by mistake
# fails the run instead of putting a paid copy behind a free key.
FREE_GROUP_ROUTES = None  # set below, once FREE_PRICED and FREE_ID_ROUTES exist
FREE_GROUP = "free"
# A gated route is never in `canonical` either: a key reaches its deployments only by holding the
# route's own access group, and no workstation key holds it (agent-kit withholds the same routes).
# Claude is never used through the proxy by a key minted for general work; a dedicated key holding
# the group is the only way in. A model another route serves leaves the gated route out, so a name
# never mixes access groups. This is code, not a rule in routing.json, for the same reason as above.
GATED_ACCESS_GROUPS = {"anthropic": "anthropic"}
# A paid route that also serves free ids (OpenCode Go's "-free" models): a free id may train on the
# prompt, so it is public work only, like a free-only route. It is never in `canonical` nor in a
# paid group of the same model: it gets its own "<route>-free/<canonical>" group and the route's
# own free access group, alone, with no budget. The "<route>-free" prefix keeps the name clear of
# the route's own wildcard ("opencode-go/*") and lets a reader tell the route from the group.
FREE_ID_ROUTES = {"opencode-go": "opencode-go-free-public"}
FREE_GROUP_ROUTES = (FREE_ONLY - FREE_PRICED) | frozenset(FREE_ID_ROUTES)
# A non-chat modality and the LiteLLM model_info.mode that marks it. Moderation is never deployed.
MODES = {"embedding": "embedding", "ocr": "ocr", "transcription": "audio_transcription"}
# A public route's deployment is a free tier whose prompts may be used for training: it cools down
# for longer than a paid one, because its quota is per minute and per day.
PUBLIC_COOLDOWN = 120
# The access group a public deployment carries; a key without it never sees a public/* model.
PUBLIC_ACCESS_GROUP = "google-free-public"
# The access group of every free-only deployment of every provider, for the one key that reaches
# "all free models" (agent-kit#848): each OpenRouter free model (beside its own group) and each
# Google free-tier model, alone in a "google-free/<canonical>" group with no paid deployment behind
# it. A request the free tier refuses fails; it never falls over to a paid deployment.
FREE_ACCESS_GROUP = "free"
GOOGLE_FREE_ROUTE = "google-free"
# The access group every other deployment carries, and every key holds.
CANONICAL_ACCESS_GROUP = "canonical"
# The generated deployments: a file in the litellm repository, refreshed by a pull request.
MODELS_PATH = "config/models.generated.yaml"
MODELS_BRANCH = "bot/model-catalog"
MODELS_TITLE = "fix(models): refresh the generated model deployments"
MODELS_MESSAGE = "fix(models): refresh the generated model deployments"
MODELS_LABELS = ("type: chore", "area: platform")
MODELS_HEADER = "# Generated by catalog/sync.py from the probed catalogue. Do not edit by hand."
MODELS_NOTE = "# One deployment per (canonical model, account) the catalogue probed usable; the rules are in catalog/routing.json."
# Anthropic's list and messages endpoints take a version header; the value is the API's, not a secret.
ANTHROPIC_VERSION = "2023-06-01"
# The routing rules: catalog/routing.json beside this script, or the file CATALOG_RULES names.
RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "routing.json")
GITHUB = "https://api.github.com"

BUDGET = re.compile(
    r"budget_exceeded|budget has been exceeded|insufficient[_ ](credit|quota|funds)|exceeded your current quota|"
    r"payment required|out of credit",
    re.I,
)
BLOCKED = re.compile(
    r"hosted outside|select global|privacy|region|not enabled|disabled|not allowed|forbidden|permission|"
    r"access denied|unauthori[sz]ed|not available (in|for) your|zero data retention",
    re.I,
)
UNKNOWN = re.compile(
    r"not found|does not exist|unknown model|invalid model|no such model|decommission|retired|"
    r"no healthy deployments|model_not_found",
    re.I,
)
SECRETISH = re.compile(r"(sk-|key-|Bearer\s+)[A-Za-z0-9_\-.]{6,}|[A-Za-z0-9]{32,}")


def load_rules(path=None):
    """The routing rules: catalog/routing.json beside this script, or CATALOG_RULES, or `path`.

    A missing explicit path is no rules (tests); a missing default file or a malformed one raises,
    because a rule that cannot be read must not silently route traffic.
    """
    path_arg = path
    path = path or os.environ.get("CATALOG_RULES") or RULES_PATH
    try:
        with open(path) as handle:
            rules = json.load(handle)
    except FileNotFoundError:
        if not path_arg:
            # The image ships the rules (or CATALOG_RULES names them): their absence is a broken
            # deployment, and without them Google would land in the normal groups.
            raise RuntimeError(f"the routing rules {path} are missing")
        return {}
    if not isinstance(rules, dict):
        raise RuntimeError(f"the routing rules {path} are not a JSON object")
    check_free_exclusions(rules, path)
    free_by_rules = rules.get("free_by") or {}
    if not isinstance(free_by_rules, dict):
        raise RuntimeError(f"{path}: free_by must be an object of route -> \"plan\", \"price\" or \"listed\"")
    known = {entry["route"] for entry in ROUTES}
    for route, how in free_by_rules.items():
        if route not in known:
            raise RuntimeError(f"{path}: free_by names {route!r}, which is no route")
        if how not in ("plan", "price", "listed"):
            raise RuntimeError(f"{path}: free_by.{route} is {how!r}; it must be \"plan\", \"price\" or \"listed\"")
    free_all = rules.get("free_all") or []
    if not isinstance(free_all, list) or any(route not in known or route not in STATIC_ROUTES for route in free_all):
        raise RuntimeError(f"{path}: free_all must list static-list routes (those with `free_ids`) only")
    reference = rules.get("reference") or {}
    if not isinstance(reference, dict) or any(not isinstance(v, str) or (k != "_sources" and k not in known) for k, v in reference.items()):
        raise RuntimeError(f"{path}: reference must map route names to a models.dev provider id")
    reference.pop("_sources", None)
    check_free_limits(rules, path)
    for route, ids in (rules.get("paid_only") or {}).items():
        if route.startswith("_"):
            continue
        if route not in known or not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
            raise RuntimeError(f"{path}: paid_only.{route} must be a list of upstream ids of a known route")
    return rules


# The per-deployment limits LiteLLM 1.109.1 reads from litellm_params (GenericLiteLLMParams: rpm, tpm, rpd, tpd,
# daily_reset_time, max_parallel_requests) and enforces with router_settings.optional_pre_call_checks
# [enforce_model_rate_limits] (router_utils/pre_call_checks/model_rate_limit_check.py). rpd/tpd count per UTC day
# shifted by daily_reset_time ("HH:MM", UTC).
DEPLOYMENT_LIMITS = ("rpm", "tpm", "rpd", "tpd", "max_parallel_requests")
# The windows of a provider-wide (shared) budget that config/free_limits.py counts across every deployment of a route.
SHARED_WINDOWS = ("minute", "hour", "day", "month")
LIMIT_SCOPES = ("deployment", "account")
DAILY_RESET = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def check_free_limits(rules, source="the routing rules"):
    """Refuse a free_limits entry that LiteLLM or config/free_limits.py could not apply as written.

    `free_limits.<route>` holds `deployment` (per-deployment limits copied into litellm_params), `shared`
    (budgets one provider account or IP shares across all its deployments: a list of {requests|tokens, per}),
    `concurrency` (requests in flight across the whole route), `reset` (the daily reset, "HH:MM" UTC),
    `scope` (what a learned limit blocks: the one deployment or the whole account) and `source`/`status`.
    """
    limits = (rules or {}).get("free_limits")
    if limits is None:
        return
    if not isinstance(limits, dict):
        raise RuntimeError(f"{source}: free_limits must be an object of route -> limits")
    known = {entry["route"] for entry in ROUTES}
    for route, entry in limits.items():
        if route.startswith("_"):
            continue
        where = f"{source}: free_limits.{route}"
        if route not in known:
            raise RuntimeError(f"{where} names no route")
        if route not in FREE_ONLY and route not in FREE_ID_ROUTES:
            raise RuntimeError(f"{where}: only a free route has free limits")
        if not isinstance(entry, dict):
            raise RuntimeError(f"{where} is not an object")
        for key, value in (entry.get("deployment") or {}).items():
            if key not in DEPLOYMENT_LIMITS or not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise RuntimeError(f"{where}.deployment.{key} must be one of {DEPLOYMENT_LIMITS} with a positive integer")
        for index, window in enumerate(entry.get("shared") or []):
            amounts = [k for k in ("requests", "tokens") if k in window]
            if (len(amounts) != 1 or window.get("per") not in SHARED_WINDOWS or not isinstance(window[amounts[0]], int)
                    or window[amounts[0]] <= 0 or set(window) - {"requests", "tokens", "per", "note"}):
                raise RuntimeError(f"{where}.shared[{index}] must be {{requests|tokens: N, per: {'|'.join(SHARED_WINDOWS)}}}")
        if "concurrency" in entry and (not isinstance(entry["concurrency"], int) or entry["concurrency"] <= 0):
            raise RuntimeError(f"{where}.concurrency must be a positive integer")
        if "reset" in entry and not (isinstance(entry["reset"], str) and DAILY_RESET.match(entry["reset"])):
            raise RuntimeError(f"{where}.reset must be HH:MM in UTC")
        if entry.get("scope", "deployment") not in LIMIT_SCOPES:
            raise RuntimeError(f"{where}.scope must be one of {LIMIT_SCOPES}")
        if "account_match" in entry:
            try:
                re.compile(entry["account_match"])
            except (re.error, TypeError) as error:
                raise RuntimeError(f"{where}.account_match is not a valid regex: {error}")
        if entry.get("status") not in ("confirmed", "partial", "unconfirmed"):
            raise RuntimeError(f"{where}.status must be confirmed, partial or unconfirmed")


def check_free_exclusions(rules, source="the routing rules"):
    """Refuse a free_exclusions rule that would not say exactly what it excludes.

    A rule needs a non-empty `match` regex that compiles, and a non-empty list of `routes`. Without
    them the rule would match every model (an empty pattern matches everything), so it is an error
    at load, never a silent exclusion of the whole catalogue.
    """
    rule_list = (rules or {}).get("free_exclusions")
    if rule_list is None:
        return
    if not isinstance(rule_list, list):
        raise RuntimeError(f"{source}: free_exclusions must be a list of rules")
    for index, rule in enumerate(rule_list):
        where = f"{source}: free_exclusions[{index}]"
        if not isinstance(rule, dict):
            raise RuntimeError(f"{where} is not an object")
        match = rule.get("match")
        if not isinstance(match, str) or not match:
            raise RuntimeError(f"{where} has no `match` regex; without one it would exclude every model")
        try:
            re.compile(match)
        except re.error as error:
            raise RuntimeError(f"{where} `match` {match!r} is not a valid regex: {error}")
        routes = rule.get("routes")
        if not isinstance(routes, list) or not routes or not all(isinstance(r, str) and r for r in routes):
            raise RuntimeError(f"{where} needs a non-empty list of `routes`")


def classify(status, text):
    """(classification, reason) of one probe answer. Budget first, then account, then absence."""
    if 200 <= status < 300:
        return "usable", ""
    message = error_message(text)
    reason = SECRETISH.sub("[redacted]", message)[:240] or f"HTTP {status} with no reason text"
    if status == 402 or BUDGET.search(message):
        return "budget-exhausted", reason
    if status in (401, 403) or BLOCKED.search(message):
        return "blocked", reason
    if status == 404 or UNKNOWN.search(message):
        return "unknown", reason
    return "error", reason


def error_message(text):
    """The provider's own reason text, from an OpenAI-style error body or the raw text."""
    try:
        body = json.loads(text)
    except (TypeError, ValueError):
        return str(text or "")
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return " ".join(str(error.get(k) or "") for k in ("type", "message")).strip()
    if isinstance(error, str):
        return error
    # Any other body is not published: only the provider's own reason fields are. `message` is the OpenAI-style
    # one; NVIDIA answers {"status": 403, "title": "Forbidden", "detail": "Authorization failed"}.
    if isinstance(body, dict):
        parts = [body[k] for k in ("title", "detail", "message") if isinstance(body.get(k), str) and body[k]]
        return " ".join(parts)
    return ""


def is_free_price(pricing):
    try:
        return float(pricing.get("prompt", "0")) == 0 and float(pricing.get("completion", "0")) == 0
    except (TypeError, ValueError, AttributeError):
        return False


def zero_price(*prices):
    """Whether every price is present and zero: a missing price is not free."""
    try:
        # A bool is not a price (float(False) == 0.0), and an empty string is not a zero.
        return all(price is not None and not isinstance(price, bool) and float(price) == 0 for price in prices)
    except (TypeError, ValueError):
        return False


def priced_above_zero(item):
    """Whether a listing reports ANY price field that is not an explicit zero.

    Looks at every spelling of an input or output price (pricing.prompt/completion/input/output, input_price,
    output_price), each on its own, so a listing that prices only one side, or one in an odd format, still
    counts as priced. An item with no price field at all is not priced (the caller's list is then the signal).
    """
    pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
    values = [pricing[name] for name in ("prompt", "completion", "input", "output") if name in pricing]
    values += [item[name] for name in ("input_price", "output_price") if name in item]
    return any(not zero_price(value) for value in values)


def listed_prices(item):
    """The (input, output) price a listing reports, or None: pricing.prompt/completion, pricing.input/output
    or input_price/output_price. Units are the provider's own (per token for the OpenAI-style lists)."""
    pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
    for source, names in ((pricing, ("prompt", "completion")), (pricing, ("input", "output")),
                          (item, ("input_price", "output_price"))):
        try:
            pair = tuple(float(source[name]) for name in names)
        except (KeyError, TypeError, ValueError):
            continue
        return pair
    return None


def route_base(entry, env):
    """The provider base of one route: its env value, or the route's template filled from the env.

    A template whose parts are unset is no base, so the route is a route error, never a bad URL.
    """
    template = entry.get("base_template")
    if template and not env.get(entry["base_env"]):
        parts = {name: env.get(name) or "" for name in re.findall(r"{(\w+)}", template)}
        # base.env ships a REPLACE_WITH_... placeholder until the owner writes the real id.
        parts = {name: "" if value.startswith("REPLACE_WITH") else value for name, value in parts.items()}
        return template.format(**parts) if all(parts.values()) else ""
    value = (env.get(entry["base_env"]) or "").rstrip("/")
    # A REPLACE_WITH_... value is a placeholder the owner has not filled (BazaarLink's base): no value, so the default.
    value = "" if value.startswith("REPLACE_WITH") else value
    return (value or entry.get("default_base") or "").rstrip("/")


# The Vault API path (the UI shows it as secret/platform/litellm); the env files name it the same way.
VAULT_PATH = "secret/data/platform/litellm"
# Env names whose Vault field is not their lower-cased name. COHERE_API_BASE is LiteLLM's own cohere_chat base, so the
# sync's list base has its own name.
VAULT_FIELDS = {"COHERE_LIST_BASE": "cohere_api_base"}


def vault_field(env_name):
    """The Vault field a setting comes from: its env name, lower-cased (CLOUDFLARE_ACCOUNT_ID -> cloudflare_account_id)."""
    return VAULT_FIELDS.get(env_name, env_name.lower())


def missing_setting(entry, env):
    """The error naming the first setting (and its Vault field) a route needs and does not have, or "".

    Settings that are not secrets (an account id, a base URL, a project id) are Vault fields too, mapped to
    env names by the env files; an unset optional field arrives empty.
    """
    template = entry.get("base_template")
    if template and not env.get(entry["base_env"]):
        names = re.findall(r"{(\w+)}", template)
    else:
        names = [] if entry.get("default_base") else [entry["base_env"]]  # a route with a default base needs no field
    for name in names + list(entry.get("needs") or []):
        value = env.get(name) or ""
        if not value or value.startswith("REPLACE_WITH"):
            return f"{name} is not set; write the Vault field {vault_field(name)} at {VAULT_PATH}"
    return ""


def base_missing(entry, env=None):
    return missing_setting(entry, env or {}) or f"{entry['base_env']} is not set"


def free_by(rules, route):
    """How a static-list route is free: "plan" (the plan is free, whatever the listing prices the id at: Groq,
    Cloudflare, Z.ai) or "price" (a pay-per-token host: the approved id must be listed at zero, the default,
    fail closed: Together, Typhoon, Neosantara)."""
    return ((rules or {}).get("free_by") or {}).get(route, "price")


def passed(value, now=None):
    """Whether a listing's date (epoch seconds, or an ISO date or time) is already behind us; unparsable is not."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            when = datetime.datetime.fromtimestamp(value, datetime.timezone.utc)
        else:
            when = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            when = when if when.tzinfo else when.replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return False
    return when <= now


def listing_verdict(kind, item):
    """What the listing alone says about one model without a request: (chat, unavailable).

    chat is True or False when the listing names what the model is for, None when it does not. unavailable is the
    listing's own reason the model cannot be served (not served yet, out of stock, retired), or "". Sources
    (fetched 2026-11-26): Naga `kinds` and `availability.status` (served / not_yet_served / out_of_stock) and
    `deprecates_at`; Routeway `available`, `endpoints` and `retirement`; Requesty `api` and `retires`; Kilo
    `architecture.output_modalities`, `expiration_date` and `retires_on`."""
    chat, why = None, ""
    if kind == "naga":
        if isinstance(item.get("kinds"), list):
            chat = "chat" in item["kinds"]
        status = (item.get("availability") or {}).get("status") if isinstance(item.get("availability"), dict) else None
        if status and status != "served":
            why = f"the listing says {status}"
        elif passed(item.get("deprecates_at")):
            why = "the listing says it is deprecated"
    elif kind == "routeway":
        if isinstance(item.get("endpoints"), list):
            chat = "/v1/chat/completions" in item["endpoints"]
        retired = item.get("retirement") if isinstance(item.get("retirement"), dict) else {}
        if item.get("available") is False:
            why = "the listing says it is not available"
        elif passed(retired.get("retires_at")):
            why = "the listing says it is retired"
    elif kind == "requesty":
        if item.get("api"):
            chat = item["api"] == "chat"
        if passed(item.get("retires")):
            why = "the listing says it is retired"
    elif kind == "kilo":
        outputs = (item.get("architecture") or {}).get("output_modalities") if isinstance(item.get("architecture"), dict) else None
        if isinstance(outputs, list):
            chat = "text" in outputs
        if passed(item.get("expiration_date")) or passed(item.get("retires_on")):
            why = "the listing says it is retired"
    return chat, why


def paid_only(rules, route):
    """The ids a free route's provider names as needing a paid plan (routing.json `paid_only`): never free."""
    return frozenset(((rules or {}).get("paid_only") or {}).get(route) or [])


def approves_all(rules, route):
    """Whether routing.json's `free_all` lists the route: every listed chat model is a free candidate, the probe
    decides usable or not, and `free_ids` is then only the ids the plan names for sure. Plan-free routes (Groq, and the no-card accounts) and `listed` routes (zero price in the listing);
    a route whose plan frees some ids (Z.ai) or bills per token stays on `free_ids`, because a probe that is
    answered does not show the id is free, and a zero cost recorded on a paid id would hide the spend."""
    return route in ((rules or {}).get("free_all") or [])


def modality(kind, item):
    """What a listed model is for. A chat model is probed; the rest is deployed only where the
    rules say the paid key serves it, because a list alone does not prove a chat model."""
    model_id = str(item.get("id") or "").lower()
    caps = item.get("capabilities") or {}
    if kind == "mistral":
        if caps.get("ocr"):
            return "ocr"
        if caps.get("audio_transcription") or "transcribe" in model_id:
            return "transcription"
        if "embed" in model_id or caps.get("classification"):
            return "embedding"
        if caps.get("moderation"):
            return "moderation"
        return "chat"
    if kind == "cloudflare" and isinstance(item.get("task"), dict):
        # Workers AI names the task: only Text Generation is a chat model.
        return "chat" if str(item["task"].get("name") or "").lower() == "text generation" else "other"
    if kind == "cloudflare":
        return "chat"
    if kind == "cohere" and isinstance(item.get("endpoints"), list):
        return "chat" if "chat" in item["endpoints"] else "other"
    if kind == "watsonx" and isinstance(item.get("functions"), list):
        ids = {f.get("id") for f in item["functions"] if isinstance(f, dict)}
        return "chat" if ids & {"text_chat", "text_generation"} else "other"
    if any(word in model_id for word in ("whisper", "orpheus", "-tts", "tts-")):
        return "speech"
    return "embedding" if "embed" in model_id else "chat"


BARE_LIST_KINDS = ("together", "typhoon")
IAM_URL = "https://iam.cloud.ibm.com/identity/token"
WATSONX_SPECS = "/ml/v1/foundation_model_specs?version=2024-05-01&limit=200"


def listing_items(body, kind=None):
    """The items of a model listing: {"data": [...]} (OpenAI-style), or a bare list for the kinds that answer one
    (Together only); anything else is None, which the caller reports as a route error, never as zero models."""
    if isinstance(body, dict) and isinstance(body.get("data"), list):
        return body["data"]
    # Cohere's own list ({"models": [{"name", "endpoints"}]}) and watsonx's specs ({"resources": [{"model_id", "functions"}]}).
    if kind == "cohere" and isinstance(body, dict) and isinstance(body.get("models"), list):
        return [dict(m, id=m["name"]) for m in body["models"] if isinstance(m, dict) and m.get("name")]
    if kind == "watsonx" and isinstance(body, dict) and isinstance(body.get("resources"), list):
        return [dict(m, id=m["model_id"]) for m in body["resources"] if isinstance(m, dict) and m.get("model_id")]
    return body if kind in BARE_LIST_KINDS and isinstance(body, list) else None


def cloudflare_search(base, headers, http, route="cloudflare"):
    """Every item of GET /accounts/<id>/ai/models/search, paged by `page` and `result_info.total_pages`. The
    OpenAI-style base (.../ai/v1) answers no GET /models (405): the search is Workers AI's model list."""
    account = base.split("/accounts/", 1)[1].split("/", 1)[0]
    items = []
    for page in range(1, CANDIDATE_PAGES + 1):
        status, body = http("GET", f"{CLOUDFLARE_ACCOUNTS}/{account}/ai/models/search?per_page=100&page={page}", headers, None)
        if status != 200 or not isinstance(body, dict) or not isinstance(body.get("result"), list):
            raise RuntimeError(f"{route}: the model search answered {status}")
        items += [item for item in body["result"] if isinstance(item, dict)]
        info = body.get("result_info") if isinstance(body.get("result_info"), dict) else {}
        if not body["result"] or page >= int(info.get("total_pages") or 1):
            break
    return items


def fetch_listing(entry, env, http, base, headers):
    """(status, body) of one route's model list. Most routes answer GET <base>/models. Cohere's is its own
    /v1/models (Bearer key). watsonx needs an IAM token first (the API key is exchanged at IBM Cloud IAM) and then
    lists its foundation model specs."""
    kind = entry["kind"]
    if kind == "cloudflare":
        try:
            items = cloudflare_search(base, headers, http)
        except RuntimeError as error:
            return int(str(error).rsplit(" ", 1)[-1]) if str(error).rsplit(" ", 1)[-1].isdigit() else 502, str(error)
        return 200, {"data": [dict(item, id=str(item.get("name") or item.get("id") or "")) for item in items
                              if item.get("name") or item.get("id")]}
    if kind == "watsonx":
        key = env.get(entry["key_env"]) or ""
        form = "grant_type=urn%3Aibm%3Aparams%3Aoauth%3Agrant-type%3Aapikey&apikey=" + urllib.parse.quote(key, safe="")
        status, token = http("POST", IAM_URL, {"Content-Type": "application/x-www-form-urlencoded"}, form)
        if status != 200 or not isinstance(token, dict) or not token.get("access_token"):
            return status if status != 200 else 502, "the IAM token exchange failed"
        return http("GET", f"{base}{WATSONX_SPECS}", {"Authorization": f"Bearer {token['access_token']}"}, None)
    if kind == "cohere":
        return http("GET", f"{base}/models?page_size=1000", headers, None)
    return http("GET", f"{base}/models", headers, None)


def list_models(entry, env, http, rules=None, reference=None):
    """The models the provider lists for this route: id, name, context length, free, chat, modality.

    A free-only route's `free` is its kind's signal: Kilo `isFree`, a zero price (Requesty, OVH), a
    `:free` suffix (Naga, Routeway, Electron Hub, UnoRouter), `tier == "free"` (Api.Airforce), a zero
    price (Vercel, Novita), everything listed (NVIDIA), or an id in routing.json's `free_ids` for a
    plan-wide free tier or a listing with no signal (Groq, Cloudflare, Z.ai, AxForge, Inferbase, Cohere, ...).
    """
    base = route_base(entry, env).rstrip("/")
    if not base or missing_setting(entry, env):
        raise RuntimeError(base_missing(entry, env))
    free_ids = set(((rules or {}).get("free_ids") or {}).get(entry["route"]) or [])
    key = env.get(entry["key_env"]) or ""
    kind = entry["kind"]
    if kind == "anthropic":
        headers = {"anthropic-version": ANTHROPIC_VERSION, **({"x-api-key": key} if key else {})}
    else:
        headers = {"Authorization": f"Bearer {key}"} if key else {}
    status, body = fetch_listing(entry, env, http, base, headers)
    if kind == "openrouter" and key:
        # The account-aware list (models this key may use), when OpenRouter serves it.
        user_status, user_body = http("GET", f"{base}/models/user", headers, None)
        if user_status == 200 and isinstance(user_body, dict) and isinstance(user_body.get("data"), list):
            status, body = user_status, user_body
    items = listing_items(body, kind)
    if status != 200 or items is None:
        # 599 is this client's own: no HTTP answer (DNS, connect, TLS, timeout); the body is the transport error.
        detail = f" ({body})" if status == 599 and isinstance(body, str) and body else ""
        raise RuntimeError(f"{entry['route']}: the model list answered {status}{detail}")
    found = {}
    for item in items:
        if not isinstance(item, dict) or not item.get("id") or item.get("archived"):
            continue
        # Google's OpenAI-compatible list names ids "models/<id>"; the id is what a caller sends.
        model_id = str(item["id"]).removeprefix("models/") if kind == "google" else item["id"]
        name = item.get("name") or item.get("display_name") or item.get("displayName") or model_id
        out = {"id": model_id, "name": name, "context_length": None, "free": False, "chat": None}
        out["modality"] = modality(kind, dict(item, id=model_id))
        # OpenCode Go names the model it serves ("zhipuai/glm-5.3") beside its own id.
        if item.get("canonical_id"):
            out["canonical_id"] = str(item["canonical_id"])
        if kind == "opencode-go":
            # The family and base the model is served on, which the probe has to use: a Messages or
            # Responses model does not answer at /chat/completions.
            api = item.get("api") if isinstance(item.get("api"), dict) else {}
            family = next((name for name in GO_FAMILIES if isinstance(api.get(name), dict)), None)
            if family:
                out["api"] = family
                if api[family].get("base_url"):
                    out["api_base"] = str(api[family]["base_url"])
        if kind == "mistral":
            caps = item.get("capabilities") or {}
            out["chat"] = bool(caps.get("completion_chat")) if "completion_chat" in caps else None
            out["context_length"] = item.get("max_context_length")
        elif kind == "openrouter":
            out["context_length"] = item.get("context_length")
            out["free"] = item["id"].endswith(":free") or is_free_price(item.get("pricing"))
        elif kind == "google":
            out["context_length"] = item.get("inputTokenLimit")
        elif kind == "kilo":
            out["context_length"] = item.get("context_length")
            # isFree, or a listed price that is an explicit zero on both sides (a "-1" is Kilo's router sentinel, not a zero).
            pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
            out["free"] = item.get("isFree") is True or zero_price(pricing.get("prompt"), pricing.get("completion"))
        elif kind == "requesty":
            out["context_length"] = item.get("context_window") or item.get("context_length")
            out["free"] = zero_price(item.get("input_price"), item.get("output_price"))
        elif kind == "ovh":
            pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
            out["free"] = zero_price(pricing.get("prompt"), pricing.get("completion"))
        elif kind in ("naga", "routeway", "electronhub", "unorouter"):
            out["free"] = model_id.endswith(":free")
        elif kind == "airforce":
            out["free"] = item.get("tier") == "free"
        elif kind == "vercel":
            # Zero-priced ids only; a missing price is not free. The ids' free credit is not drawn by a $0 id.
            # Explicit zero for BOTH pricing.input and pricing.output (a number or a numeric string); a missing,
            # empty or unparsable price is not free. No fallback to other field spellings.
            pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
            out["free"] = zero_price(pricing.get("input"), pricing.get("output"))
        elif kind == "novita":
            # The spec names the flat per-million fields of the item; only those, both required to be zero.
            out["free"] = zero_price(item.get("input_token_price_per_m"), item.get("output_token_price_per_m"))
        elif kind == "nvidia":
            out["free"] = True
        elif kind in STATIC_KINDS:
            # The plan is free, so the list is the signal; a listing that prices the id above zero
            # overrides it (fail closed): the plan is not free for that model.
            price = listed_prices(item)
            if kind == "inferbase":
                # Inferbase's free share is a dollar allowance, so its ids are priced above zero and a
                # deployment needs the real per-token price for the route's budget to fire; an id the
                # listing does not price is not deployed.
                out["free"] = (model_id in free_ids or approves_all(rules, entry["route"])) and price is not None
                if price is not None:
                    out["price"] = {"input": price[0], "output": price[1]}
            else:
                # Pay-per-token hosts name one approved free id (Together, Typhoon, Neosantara): a listing that
                # prices it anywhere above zero, on either side, refuses it.
                # A plan-free route (Groq, Z.ai) prices its ids per token for paying accounts, so the guard
                # does not apply there: the plan is the signal.
                how = free_by(rules, entry["route"])
                if how == "listed":
                    # The listing's own price is the signal: free only on an explicit zero on both sides, and no
                    # other price field above zero. A listing that prices nothing is not free (fail closed).
                    out["free"] = (model_id in free_ids or approves_all(rules, entry["route"])) and (
                        price is not None and zero_price(*price) and not priced_above_zero(item))
                else:
                    out["free"] = (model_id in free_ids or approves_all(rules, entry["route"])) and (
                        how == "plan" or not priced_above_zero(item))
        elif entry["route"] in FREE_ID_ROUTES:
            out["free"] = model_id.endswith("-free")
        elif entry["route"] == "local":
            out["free"] = True
        ref = (reference or {}).get(reference_key(model_id))
        if ref:
            # The public catalogue, matched inside this route's own provider: it can only add what the listing lacks.
            if ref["chat"] is False and out["modality"] == "chat":
                out["modality"] = "other"
            if ref["cost"] is not None:
                if kind == "inferbase":
                    if "price" not in out:
                        out["price"] = {"input": ref["cost"][0], "output": ref["cost"][1]}
                        out["free"] = model_id in free_ids or approves_all(rules, entry["route"])
                elif kind not in STATIC_KINDS and not out["free"] and ref["cost"] == (0, 0) and not priced_above_zero(item):
                    out["free"] = True  # the catalogue says 0 and the listing prices nothing above zero
                    out["free_source"] = "models.dev"
        verdict_chat, unavailable = listing_verdict(kind, item)
        if ref and ref["deprecated"] and not unavailable:
            unavailable = "models.dev lists it as deprecated"
        if verdict_chat is False:
            out["modality"] = out["modality"] if out["modality"] != "chat" else "other"
        if unavailable:
            out["unavailable"] = unavailable
        if out["modality"] != "chat":
            out["chat"] = False
        if model_id in paid_only(rules, entry["route"]):
            # The provider says this model needs a paid plan: never free, whatever the plan or listing suggests.
            out["free"] = False
            out["paid_only"] = True
        found[model_id] = out
    if kind in STATIC_KINDS and kind != "inferbase" and free_by(rules, entry["route"]) == "plan":
        # A plan-free provider may leave its free ids out of the list (Z.ai's /models omits the *-flash ids): an
        # approved id still joins, and the probe decides whether the account can use it. A price-guarded route
        # cannot: an unlisted id has no price to check.
        for model_id in sorted(free_ids - set(found)):
            found[model_id] = {"id": model_id, "name": model_id, "context_length": None, "free": True, "chat": None,
                               "modality": "chat", "unlisted": True}
    return list(found.values())

CLOUDFLARE_ACCOUNTS = "https://api.cloudflare.com/client/v4/accounts"
CANDIDATE_PAGES = 10


def resolve_cloudflare_account(env, http, log=None):
    """The env with CLOUDFLARE_ACCOUNT_ID filled in from the token when it is unset or the base.env placeholder.

    `GET /client/v4/accounts` with the token lists the accounts it may read. Exactly one account is
    used (and logged by id and name); none or several is an error, never a guess. Returns (env, error).
    """
    value = env.get("CLOUDFLARE_ACCOUNT_ID") or ""
    key = env.get("CLOUDFLARE_API_KEY") or ""
    if (value and not value.startswith("REPLACE_WITH")) or env.get("CLOUDFLARE_API_BASE") or not key:
        return env, None
    status, body = http("GET", CLOUDFLARE_ACCOUNTS, {"Authorization": f"Bearer {key}"}, None)
    accounts = body.get("result") if status == 200 and isinstance(body, dict) else None
    accounts = [a for a in (accounts or []) if isinstance(a, dict) and a.get("id")]
    if status != 200 or not isinstance(body, dict):
        return env, f"cloudflare: CLOUDFLARE_ACCOUNT_ID is not set (Vault field cloudflare_account_id at {VAULT_PATH}) and the account list answered {status}"
    if len(accounts) != 1:
        return env, f"cloudflare: CLOUDFLARE_ACCOUNT_ID is not set (Vault field cloudflare_account_id at {VAULT_PATH}) and the token sees {len(accounts)} accounts; set it"
    if log:
        log(f"catalog: cloudflare: CLOUDFLARE_ACCOUNT_ID is not set; using the token's one account {accounts[0]['id']} ({accounts[0].get('name', '')})")
    return dict(env, CLOUDFLARE_ACCOUNT_ID=str(accounts[0]["id"])), None


def cloudflare_signals(item):
    """Workers AI search item -> (id, signals). `name` is the model id (`@cf/...`); `properties` is a
    list of {property_id, value}: `price` is a list of {unit, price, currency}, `context_window` a number."""
    props = {p.get("property_id"): p.get("value") for p in item.get("properties") or [] if isinstance(p, dict)}
    signals = {}
    prices = []
    for entry in props.get("price") if isinstance(props.get("price"), list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("price"), (int, float)):
            prices.append({"unit": str(entry.get("unit") or "")[:60], "price": entry["price"], "currency": str(entry.get("currency") or "")[:8]})
    if prices:
        signals["prices"] = prices
        signals["zero_priced"] = all(p["price"] == 0 for p in prices)
    for name in ("context_window", "beta"):
        if props.get(name) is not None:
            signals["context_length" if name == "context_window" else name] = props[name]
    task = item.get("task") if isinstance(item.get("task"), dict) else {}
    signals["modality"] = str(task.get("name") or "")[:40] or None
    return str(item.get("name") or item.get("id") or ""), {k: v for k, v in signals.items() if v is not None}


def listing_signals(kind, item):
    """What a plain OpenAI-style listing says about price and freeness, whitelisted."""
    signals = {}
    price = listed_prices(item)
    if price is not None:
        signals["input_price"], signals["output_price"] = price
        signals["zero_priced"] = not any(price)
    for flag in ("isFree", "free", "is_free"):
        if isinstance(item.get(flag), bool):
            signals["free_flag"] = item[flag]
    context_length = item.get("context_length") or item.get("context_window") or item.get("max_context_length")
    if isinstance(context_length, int) and not isinstance(context_length, bool):
        signals["context_length"] = context_length
    signals["modality"] = candidate_modality(kind, item)
    return signals


# Id parts and listing fields that mark a listed model as no chat model, beyond what `modality` knows.
NON_CHAT_ID = re.compile(r"whisper|(?:^|[-/_])tts(?:[-/_]|$)|orpheus|dall-?e|imagen|flux|stable-diffusion|sdxl|-image(?:-|$)|/image|rerank|moderation|guard", re.I)
NON_CHAT_TYPE = re.compile(r"embed|image|audio|speech|transcri|rerank|moderation|video", re.I)


def candidate_modality(kind, item):
    """`modality`, plus the listing's own type/modality fields and a few id parts (image, speech, rerank), so a
    candidate list shows chat models only."""
    found = modality(kind, item)
    if found != "chat":
        return found
    for field in ("type", "modality", "mode", "object_type", "category"):
        value = item.get(field)
        if isinstance(value, str) and NON_CHAT_TYPE.search(value):
            return value.lower()[:40]
    if NON_CHAT_ID.search(str(item.get("id") or "")):
        return "non-chat"
    return "chat"


def scan_candidates(entry, env, http, rules):
    """Every id a static-list route's provider lists, with the signals the listing gives.

    Returns [{id, approved, signals, suggested}], sorted by id. `approved` means the id is in
    routing.json's `free_ids`; the rest are candidates for the owner and are never deployed (only
    `list_models` and the plan read `free_ids`). `suggested` is a zero price or a free flag the
    listing itself gives. Raises RuntimeError on a listing error; the caller keeps the route's
    candidates empty.
    """
    route, kind = entry["route"], entry["kind"]
    base = route_base(entry, env).rstrip("/")
    if not base or missing_setting(entry, env):
        raise RuntimeError(base_missing(entry, env))
    key = env.get(entry["key_env"]) or ""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    approved = set(((rules or {}).get("free_ids") or {}).get(route) or [])
    found = {}
    if kind == "cloudflare":
        # The search endpoint carries pricing and properties; the OpenAI-style list does not.
        for item in cloudflare_search(base, headers, http, route):
            model_id, signals = cloudflare_signals(item)
            if model_id:
                found[model_id] = signals
    else:
        status, body = fetch_listing(entry, env, http, base, headers)
        items = listing_items(body, kind)
        if status != 200 or items is None:
            raise RuntimeError(f"{route}: the model list answered {status}")
        for item in items:
            if isinstance(item, dict) and item.get("id") and not item.get("archived"):
                found[str(item["id"])] = listing_signals(kind, item)
    out = []
    for model_id in sorted(found):
        signals = found[model_id]
        # Only chat models are candidates (an approved id always shows): embeddings, images and speech are
        # listed by the same providers and are no use on a chat route. Cloudflare's tasks are named, not typed.
        kind_of = str(signals.get("modality") or "chat").lower()
        if model_id not in approved and kind_of not in ("chat", "text generation"):
            continue
        suggested = signals.get("zero_priced") is True or signals.get("free_flag") is True
        out.append({"id": model_id, "approved": model_id in approved, "signals": signals, "suggested": suggested})
    # An approved id the provider no longer lists still shows, so the owner sees it went away.
    for model_id in sorted(approved - set(found)):
        out.append({"id": model_id, "approved": True, "signals": {"unlisted": True}, "suggested": False})
    return out


def go_request(base, upstream_id, key, family):
    """The probe request for one OpenCode Go model, in the API family Go serves it on."""
    headers = {"Content-Type": "application/json", "x-opencode-session": "litellm-catalog-probe"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if family == "responses":
        return f"{base}/responses", headers, {"model": upstream_id, "input": "ping", "max_output_tokens": PROBE_MAX_TOKENS}
    if family == "messages":
        headers["anthropic-version"] = ANTHROPIC_VERSION
        body = {"model": upstream_id, "max_tokens": PROBE_MAX_TOKENS, "messages": [{"role": "user", "content": "ping"}]}
        return f"{base}/messages", headers, body
    if family == "generate-content":
        body = {"contents": [{"role": "user", "parts": [{"text": "ping"}]}], "generationConfig": {"maxOutputTokens": PROBE_MAX_TOKENS}}
        return f"{base}/models/{upstream_id}:generateContent", headers, body
    return f"{base}/chat/completions", headers, {"model": upstream_id, "messages": [{"role": "user", "content": "ping"}],
                                                "max_tokens": PROBE_MAX_TOKENS}


def probe(entry, upstream_id, env, http, meta=None):
    """One minimal request at the provider itself, with the route's own key.

    Not through the proxy: a model the proxy has not deployed yet has no route to go through.
    An OpenCode Go model is probed in the family and at the base its listing named (`meta`).
    """
    meta = meta or {}
    base = route_base(entry, env).rstrip("/")
    if not base:
        return "error", f"{entry['base_env']} is not set"
    key = env.get(entry["key_env"]) or ""
    headers = {"Content-Type": "application/json"}
    if entry["kind"] == "opencode-go":
        # OpenCode answers 400 MissingSessionID without a session header, so go_request sends one.
        url, headers, body = go_request((meta.get("api_base") or base).rstrip("/"), upstream_id, key, meta.get("api"))
    elif entry["kind"] == "anthropic":
        # Anthropic's messages endpoint takes the key in its own header, and a version header.
        headers["anthropic-version"] = ANTHROPIC_VERSION
        if key:
            headers["x-api-key"] = key
        url = f"{base}/messages"
        body = {"model": upstream_id, "max_tokens": PROBE_MAX_TOKENS, "messages": [{"role": "user", "content": "ping"}]}
    else:
        if key:
            headers["Authorization"] = f"Bearer {key}"
        url = f"{base}/chat/completions"
        body = {"model": upstream_id, "messages": [{"role": "user", "content": "ping"}], "max_tokens": PROBE_MAX_TOKENS}
    status, answer = http("POST", url, headers, body)
    text = answer if isinstance(answer, str) else json.dumps(answer)
    if status == 400 and "max_tokens" in body:
        # A provider that refuses the small max_tokens names its floor: retry once at that floor.
        match = PROBE_MINIMUM.search(error_message(text))
        if match and PROBE_MAX_TOKENS < int(match[1]) <= PROBE_RETRY_CEILING:
            status, answer = http("POST", url, headers, dict(body, max_tokens=int(match[1])))
            text = answer if isinstance(answer, str) else json.dumps(answer)
    return classify(status, text)


# A leading vendor segment (OpenRouter's "mistralai/mistral-large") and a trailing version stamp
# ("mistral-large-2411", "claude-sonnet-4-5-20250929"): the two spellings that are not the model.
VENDOR = re.compile(r"^[a-z0-9][a-z0-9._-]*/")
VERSION = re.compile(r"-\d{4,8}$")


def canonical_name(route, upstream_id, rules, canonical_id=None, strip_version=True):
    """The canonical model an upstream id serves: one name per model, whichever route serves it.

    Deterministic and conservative: only a vendor prefix, a `:free` suffix, `-latest` and a version
    stamp are stripped, and an alias wins over both the raw and the normalised id. A route that
    names the model it serves (OpenCode Go's `canonical_id`) is normalised from that name instead,
    before the alias table. Two ids that still differ are two models; nothing here guesses that
    they are one.
    """
    aliases = (rules or {}).get("aliases") or {}
    source = canonical_id or upstream_id
    if source in aliases:
        return aliases[source]
    name = source.lower().removeprefix("models/")
    name = VENDOR.sub("", name, count=1).removesuffix(":free").removesuffix("-latest")
    if strip_version:
        name = VERSION.sub("", name)
    return aliases.get(name, name)


# A free group joins every free copy of one model however its provider spells the id: Cloudflare's "@cf/"
# and "@hf/" catalogue prefixes, any vendor path (the last segment is the model), a ":free" or "-free"
# suffix (or a "free" glued to a trailing date or version) that only marks the free variant. Nothing else is stripped: a quantisation (-fp8, -awq), a
# distillation, a size, a fine-tune or a dated snapshot stays its own model unless routing.json's
# `aliases` says otherwise.
CATALOGUE_PREFIX = re.compile(r"^@(cf|hf)/")
GLUED_FREE = re.compile(r"(?<=\d)free$")
# Router endpoints that pick a different model per request: not one model, so never a free group.
FREE_ROUTER_NAMES = frozenset({"free", "auto", "router"})


def free_canonical(name, rules):
    """The model a free deployment serves, for the shared free/<model> group (see CATALOGUE_PREFIX)."""
    # `free_aliases` merges spellings for the free groups only (the paid groups keep their names).
    aliases = {**((rules or {}).get("aliases") or {}), **((rules or {}).get("free_aliases") or {})}
    key = CATALOGUE_PREFIX.sub("", name.lower()).rsplit("/", 1)[-1]
    key = key.removesuffix(":free")
    if key.endswith("-free") and key != "-free":
        key = key[: -len("-free")]
    # "free" glued to a date or version ("deepseek-v4-flash-0731free"): the same marker without its hyphen.
    key = GLUED_FREE.sub("", key)
    return aliases.get(key, key)


def free_limit_params(route, rules):
    """The per-deployment limits of one free route, as LiteLLM 1.109.1 reads them from litellm_params."""
    entry = ((rules or {}).get("free_limits") or {}).get(route) or {}
    params = dict(entry.get("deployment") or {})
    if params.get("rpd") or params.get("tpd"):
        params["daily_reset_time"] = entry.get("reset", "00:00")
    return params


def loose_key(name):
    """A canonical name with everything that usually differs between two spellings removed."""
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    key = re.sub(r"^(zai|zhipu)", "", key)
    return re.sub(r"latest$", "", re.sub(r"\d{4,8}$", "", key))


def near_duplicates(catalogue, rules):
    """Canonical names that differ but look like one model on different routes: reported, never merged."""
    groups = {}
    for route, provider in (catalogue.get("providers") or {}).items():
        for model in provider.get("models") or []:
            meta = model.get("metadata") or {}
            name = canonical_name(route, str(model["id"]).split("/", 1)[1], rules, meta.get("canonical_id"))
            groups.setdefault(loose_key(name), []).append((model["id"], name, route))
    out = []
    for found in groups.values():
        if len({name for _, name, _ in found}) > 1 and len({route for _, _, route in found}) > 1:
            out.append(sorted(model_id for model_id, _, _ in found))
    return sorted(out)


def candidate(meta, modality, route, api_only):
    """Whether a listed model becomes a deployment: usable, or usable until recently.

    A model that was usable keeps its place through a transient state (an error, a spent budget,
    a pending probe, one blocked or unknown probe) and through one run of absence, so a provider
    that hiccups does not take a deployment away. Two blocked or unknown probes in a row, or two
    runs of absence, drop it. A non-chat model is never probed, so its list entry is the only
    evidence there is: the rules name the routes whose paid key serves those modalities, and
    moderation is never deployed. A free-only route deploys a free id and nothing else.
    """
    if meta.get("absent_runs", 0) >= 2:
        return False
    if route in FREE_ONLY and not meta.get("free"):
        return False
    if meta.get("status") == "usable":
        return True
    if meta.get("status") == "not-chat":
        return route in api_only and modality in MODES
    if meta.get("failing_probes", 0) >= 2:
        return False
    return bool(meta.get("was_usable")) and meta.get("status") in TRANSIENT


def litellm_params(entry, upstream_id):
    """The provider params for one upstream model: the credential name, never a secret value.

    config/config.yaml's credential_list holds the key and base as os.environ/<NAME>; a deployment
    names the credential and LiteLLM resolves it at load.
    """
    params = {"model": f"{PREFIXES[entry['kind']]}/{upstream_id}", "litellm_credential_name": entry["route"]}
    if entry["route"] == "opencode-go":
        # OpenCode answers 400 MissingSessionID without a session header, as the probe found.
        params["extra_headers"] = {"x-opencode-session": "litellm-proxy"}
    return params


def primary_mode(routes):
    """The mode a canonical group is named for: the one most of its deployments have."""
    counts = {}
    for chosen in routes.values():
        counts[chosen["modality"]] = counts.get(chosen["modality"], 0) + 1
    return sorted(counts, key=lambda mode: (-counts[mode], mode != "chat", mode))[0]


def group_name(name, mode, primary, public=False, free_route=None):
    """The LiteLLM model_name of one deployment: the canonical name, and the mode when it differs.

    A group never mixes modes: a model whose mode differs from its name-mates gets its own group,
    "<canonical>:<mode>", so an embedding request cannot land on a chat deployment. A free-only
    route's group is "<route>/<canonical>", a public one "public/<canonical>".
    """
    suffix = "" if mode == primary else ":" + MODES.get(mode, mode)
    prefix = f"{free_route}/" if free_route else ("public/" if public else "")
    return prefix + name + suffix


def window_duration(window):
    """The LiteLLM budget_duration of an account's declared window.

    {"calendar": "month"} is "1mo" (a calendar month, so it is not 30 days long); {"days": N} is "Nd".
    LiteLLM opens a window at the first spend it sees and cannot be told when a window starts, so an
    "anchor" is declared policy (next_reset) that LiteLLM only approximates: see account_budgets().
    """
    if window.get("calendar") == "month":
        return "1mo"
    if isinstance(window.get("days"), int) and window["days"] > 0:
        return f"{window['days']}d"
    raise ValueError(f"unsupported budget window {window!r}")


def next_reset(window, now):
    """When the window the owner declared next resets after `now` (an aware datetime, UTC)."""
    if window.get("calendar") == "month":
        return (now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=32)).replace(day=1)
    length = datetime.timedelta(days=window["days"])
    if not window.get("anchor"):
        return None
    anchor = datetime.datetime.fromisoformat(window["anchor"].replace("Z", "+00:00"))
    if now < anchor:
        return anchor
    return anchor + length * ((now - anchor) // length + 1)


def window_start(window, now):
    """The start (an aware UTC datetime) of the window the owner declared that contains `now`.

    A calendar month starts on the 1st at 00:00 UTC; a {"days": N, "anchor": T} cycle starts at T plus a
    whole number of N-day cycles. A window without an anchor has no declared start: None.
    """
    if window.get("calendar") == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if not window.get("anchor"):
        return None
    anchor = datetime.datetime.fromisoformat(window["anchor"].replace("Z", "+00:00"))
    if now < anchor:
        return anchor - datetime.timedelta(days=window["days"])
    return anchor + datetime.timedelta(days=window["days"]) * ((now - anchor) // datetime.timedelta(days=window["days"]))


def account_budgets(rules):
    """route -> {max_budget, budget_duration, account}: the cap of the paid account each route bills.

    `accounts` in routing.json names, per account, the routes that bill it, its cap and its window. A
    route has at most one account. Every deployment of every route of an account (and its tier copies
    and its route wildcard in config.yaml) carries model_info.account, and config/account_budget.py keeps
    one counter per account from the response cost of each call. The deployment's own max_budget and
    budget_duration stay as a per-deployment backstop (LiteLLM's own counter, which opens its window at the
    first spend).
    """
    out = {}
    for account, entry in ((rules or {}).get("accounts") or {}).items():
        for route in entry["routes"]:
            if route in out:
                raise ValueError(f"route {route} is billed by two accounts")
            out[route] = {"max_budget": entry["max_budget"], "budget_duration": window_duration(entry["window"]),
                          "account": account}
    return out


def deployment(group, canonical, route, chosen, order, weight, entry, public=False, budget=None, cooldown=None,
               free_only=False, limits=None):
    """One deployment of a canonical model: its group name, params, order, weight and info."""
    upstream_id = chosen["upstream_id"]
    params = litellm_params(entry, upstream_id)
    params.update({"order": order, "weight": weight})
    if entry["kind"] == "openrouter":
        # Free ids only, and the provider is asked neither to train on nor to fall back with the
        # prompt. The route is still public work only: a free tier is not trusted with private work.
        # The route is public work only, so a free endpoint that trains on the prompt is allowed
        # (owner, 2026-11-15): most free endpoints exist only with training on. A paid provider
        # is never a fallback.
        params["extra_body"] = {"provider": {"data_collection": "allow", "allow_fallbacks": False}}
    if (route in FREE_ONLY and route not in FREE_PRICED) or (route in FREE_ID_ROUTES and chosen.get("free")):
        params.update({"input_cost_per_token": 0, "output_cost_per_token": 0})
    elif route in FREE_PRICED and chosen.get("price"):
        params.update({"input_cost_per_token": chosen["price"]["input"], "output_cost_per_token": chosen["price"]["output"]})
    if budget:
        params.update({key: budget[key] for key in ("max_budget", "budget_duration")})
    if cooldown:
        params["cooldown_time"] = cooldown
    if limits:
        params.update(limits)
    if free_only:
        groups = [FREE_ACCESS_GROUP]
    elif route in FREE_ACCESS_GROUPS:
        groups = [FREE_ACCESS_GROUPS[route], FREE_ACCESS_GROUP]
    elif route in FREE_ID_ROUTES and chosen.get("free"):
        groups = [FREE_ID_ROUTES[route], FREE_ACCESS_GROUP]
    elif route in GATED_ACCESS_GROUPS:
        groups = [GATED_ACCESS_GROUPS[route]]
    else:
        groups = [PUBLIC_ACCESS_GROUP] if public else [CANONICAL_ACCESS_GROUP]
    info = {
        "id": ("free/" if free_only else "public/" if public else "") + f"{route}/{upstream_id}",
        "route": route,
        "canonical": canonical,
        "access_groups": groups,
    }
    if budget:
        # The account whose cap this deployment spends against: config/account_budget.py keeps one counter per
        # account and takes this deployment out of the pick when it is spent. The id stays the deployment's own,
        # so cooldown and failover stay per deployment.
        info["account"] = budget["account"]
        # Not "<route>/<upstream id>": the router resolves a model name equal to a deployment id to that one
        # deployment and skips every pre-call filter for it, so "vibe/<id>" would walk past the account cap.
        info["id"] = f"{route}:{upstream_id}"
    if chosen["modality"] in MODES:
        info["mode"] = MODES[chosen["modality"]]
    return {
        "name": group,
        "route": route,
        "upstream_id": upstream_id,
        "modality": chosen["modality"],
        "order": order,
        "weight": weight,
        "litellm_params": params,
        "model_info": info,
    }


def free_excluded(name, route, rules):
    """Whether the rules keep a public (free-tier) route out of the free groups of a canonical name.

    A provider that re-routes a model to a billed one under the same name must not keep serving it
    on the free tier: routing.json's free_exclusions names the model (a regex on the canonical name)
    and the route. Only a public route is ever excluded here; a paid route is not a free group.
    The rules are validated by check_free_exclusions, so a rule here always has a real pattern.
    """
    return any(route in rule["routes"] and re.search(rule["match"], name)
               for rule in (rules or {}).get("free_exclusions") or [])


def plan_groups(catalogue, rules):
    """The canonical model groups: one model_name per canonical model, its deployments ordered.

    Routing is by which provider serves the canonical model, not by model family: the Vibe plan
    serves non-Mistral models too, and where it serves one it wins. Only exact canonical-name
    equality merges two upstream models; a near duplicate is reported, never merged.

    A public route (a free tier whose prompts may be used for training) never appears in a normal
    group: it gets a "public/<canonical>" group of its own, first, with the normal deployments
    behind it, and only a key carrying the public access group sees it.

    A free-only route (OpenRouter) is public work only too, and free only: it is in
    no normal group and no public/ group, only in its own "<route>/<canonical>" group with its own
    access group (FREE_ACCESS_GROUPS), so the key that holds that group reaches nothing paid.
    """
    rules = rules or {}
    check_free_exclusions(rules)
    tiers = rules.get("tiers") or []
    rank = {}
    for index, tier in enumerate(tiers):
        for route, weight in tier:
            rank.setdefault(route, (index + 1, weight))
    # A route no tier names comes after every tier, at weight 1: no rule, no preference.
    unranked = (len(tiers) + 1, 1)
    backup_only = rules.get("backup_only") or {}
    exclusive = rules.get("exclusive") or []
    api_only = set(rules.get("api_only_routes") or [])
    budgets = account_budgets(rules)
    cooldowns = rules.get("cooldowns") or {}
    public_routes = set(rules.get("public_routes") or [])
    by_route = {entry["route"]: entry for entry in ROUTES}
    # canonical name -> route -> the model that serves it (the shortest upstream id wins)
    groups = {}
    for route, provider in sorted((catalogue.get("providers") or {}).items()):
        entry = by_route.get(route)
        if not entry:
            continue
        for model in provider.get("models") or []:
            meta = model.get("metadata") or {}
            kind = meta.get("modality") or "chat"
            if not candidate(meta, kind, route, api_only):
                continue
            upstream_id = str(model["id"]).split("/", 1)[1]
            if upstream_id in paid_only(rules, route):
                continue  # a paid-plan model on a free route: refused even when an older run recorded it usable
            name = canonical_name(route, upstream_id, rules, meta.get("canonical_id"))
            chosen = groups.setdefault(name, {}).get(route)
            if chosen is None or len(upstream_id) < len(chosen["upstream_id"]):
                # The free groups keep a dated snapshot apart from its base model (no alias, no merge).
                dated = canonical_name(route, upstream_id, rules, meta.get("canonical_id"), strip_version=False)
                groups[name][route] = {"upstream_id": upstream_id, "modality": kind, "free": bool(meta.get("free")),
                                      "price": meta.get("price"), "free_name": dated}
    specs = []
    # free/<model> -> route -> the free copies that route serves of it (one canonical name or several)
    free_members = {}
    for name, routes in sorted(groups.items()):
        serving = set(routes)
        # A free id is in a public group of its own, so no paid route makes it a backup.
        own_group = {route for route in serving if route in FREE_ID_ROUTES and routes[route].get("free")}
        # A backup route is in the group only when nothing better serves the model in the same mode: an
        # embedding only a general API key carries stays when a plan serves a chat model of that name.
        for route in sorted(serving - own_group):
            better = {other for other in serving & set(backup_only.get(route, []))
                      if routes[other]["modality"] == routes[route]["modality"]}
            if better:
                serving.discard(route)
        # An exclusive model is only on its own routes, and never free.
        only = set()
        for rule in exclusive:
            if re.search(rule.get("match") or "", name):
                only |= set(rule.get("routes") or [])
        if only:
            serving &= only
        # A gated route serves a model only when nothing else does.
        gated = serving & set(GATED_ACCESS_GROUPS)
        if gated and serving - gated:
            serving -= gated
        # A free-tier route the rules exclude for this model serves it in no group at all: the
        # model is refused on the free tier, and a paid route that also serves it is not affected.
        serving -= {route for route in serving & public_routes if free_excluded(name, route, rules)}
        if not serving:
            continue
        primary = primary_mode({route: routes[route] for route in serving})
        free = serving & FREE_ONLY
        free_ids = serving & own_group
        normal = serving - public_routes - FREE_ONLY - free_ids
        public = serving & public_routes
        for route in sorted(free):
            # Its own group, alone: public work only, and nothing paid behind it.
            # No cooldown_time: LiteLLM then cools a 429 for the provider's own Retry-After (router default
            # otherwise), and config/free_limits.py keeps it out of the pick until its limit resets.
            specs.append(deployment(group_name(name, routes[route]["modality"], primary, free_route=route), name, route,
                                    routes[route], 1, 1, by_route[route], budget=budgets.get(route),
                                    # A dollar-allowance route (Inferbase) is budgeted, not free: it keeps its cooldown.
                                    cooldown=cooldowns.get(route, PUBLIC_COOLDOWN) if route in FREE_PRICED else None,
                                    limits=free_limit_params(route, rules)))
        for route in sorted(free_ids):
            # A free id on a paid route: public work only, alone in its own group, nothing paid behind it.
            group = group_name(name, routes[route]["modality"], primary, free_route=f"{route}-free")
            specs.append(deployment(group, name, route, routes[route], 1, 1, by_route[route],
                                    limits=free_limit_params(route, rules)))
        for route in sorted((free | free_ids) & FREE_GROUP_ROUTES):
            if name not in FREE_ROUTER_NAMES:
                key = free_canonical(routes[route].get("free_name") or name, rules)
                free_members.setdefault(key, {}).setdefault(route, []).append(routes[route])
        # The tiers that apply to this model, renumbered from 1: a model only tier 4 serves gets 1.
        levels = sorted({rank.get(route, unranked)[0] for route in normal})
        order_of = {level: index + 1 for index, level in enumerate(levels)}
        planned = []
        for route in sorted(normal):
            order, weight = order_of[rank.get(route, unranked)[0]], rank.get(route, unranked)[1]
            planned.append((route, order, weight))
        for route, order, weight in planned:
            specs.append(deployment(group_name(name, routes[route]["modality"], primary), name, route, routes[route],
                                    order, weight, by_route[route], budget=budgets.get(route), cooldown=cooldowns.get(route)))
        for route in sorted(public):
            # The free tier alone, in the free access group: public work only, and no paid
            # deployment behind it, so a refusal fails instead of billing. Chat models only.
            if routes[route]["modality"] == "chat":
                specs.append(deployment(group_name(name, "chat", "chat", free_route=GOOGLE_FREE_ROUTE), name, route,
                                        routes[route], 1, 1, by_route[route], cooldown=PUBLIC_COOLDOWN, free_only=True))
        if public:
            # The free tier is public work only: its own group, first, with the normal deployments
            # behind it, and no budget of its own.
            for route in sorted(public):
                specs.append(deployment(group_name(name, routes[route]["modality"], primary, public=True), name, route,
                                        routes[route], 1, 1, by_route[route], public=True, cooldown=PUBLIC_COOLDOWN))
            for route, order, weight in planned:
                specs.append(deployment(group_name(name, routes[route]["modality"], primary, public=True), name, route,
                                        routes[route], order + 1, weight, by_route[route], public=True,
                                        budget=budgets.get(route), cooldown=cooldowns.get(route)))
    for key, members in sorted(free_members.items()):
        specs.extend(free_group(key, members, by_route, rules))
    if rules.get("tier_groups"):
        specs.extend(tier_copies(specs, rules))
    # A group of one deployment has nothing to fall back to: it must cool down on a 402 or 429
    # itself, which LiteLLM only does with an explicit allowed-fails entry.
    counts = {}
    for spec in specs:
        counts[spec["name"]] = counts.get(spec["name"], 0) + 1
    for spec in specs:
        cools = "cooldown_time" in spec["litellm_params"] or spec["route"] in FREE_ONLY or spec["name"].startswith(FREE_GROUP + "/")
        if counts[spec["name"]] == 1 and cools:
            spec["model_info"]["allowed_fails_policy"] = {"RateLimitErrorAllowedFails": 0}
    # Rules may add a per-route policy: Anthropic answers an exhausted spend limit with a 400, which
    # LiteLLM only cools down on with an explicit entry (a malformed request counts too, hence the
    # allowance and the short cooldown the rules give it).
    for spec in specs:
        extra = (rules.get("policies") or {}).get(spec["route"])
        if extra:
            spec["model_info"]["allowed_fails_policy"] = {**spec["model_info"].get("allowed_fails_policy", {}), **extra}
    assert_free_groups(specs)
    assert_tier_copies(specs, rules)
    assert_no_tier_shadow(specs, rules)
    return sorted(specs, key=lambda spec: (spec["name"], spec["order"], spec["route"]))


def free_group(name, members, by_route, rules):
    """The shared "free/<model>" group: every zero-cost copy of one model, on every free route.

    `members` is route -> the free copies (chosen entries) that route serves of the model; the model is
    free_canonical() of their canonical names, so "@cf/openai/gpt-oss-120b", "openai/gpt-oss-120b" and
    "gpt-oss-120b:free" meet, and an id the stripping does not unify is named in rules["aliases"]. A route
    serving the model under two ids keeps the shortest. The per-route groups ("<route>/<canonical>") stay.

    Order: rules["free_order"] (lowest first; a route it does not name comes last), so the per-minute
    providers are tried before the scarce daily and monthly ones. Each copy has its own model_info.id and no
    cooldown_time: a 429 cools it for the provider's Retry-After, and config/free_limits.py takes it out of
    the pick while its known or learned limit lasts. litellm_params carries the route's per-deployment
    limits (free_limits in routing.json) for LiteLLM's enforce_model_rate_limits.
    """
    order = {route: rank for route, rank in ((rules or {}).get("free_order") or {}).items() if isinstance(rank, int)}
    last = max(order.values(), default=0) + 1
    chosen_by_route = {route: min(copies, key=lambda c: (len(c["upstream_id"]), c["upstream_id"]))
                       for route, copies in members.items()}
    primary = primary_mode(chosen_by_route)
    specs = []
    for route, chosen in sorted(chosen_by_route.items()):
        spec = deployment(group_name(name, chosen["modality"], primary, free_route=FREE_GROUP), name, route,
                          chosen, order.get(route, last), 1, by_route[route], free_only=True,
                          limits=free_limit_params(route, rules))
        spec["model_info"]["id"] = f"{FREE_GROUP}/{route}/{chosen['upstream_id']}"
        specs.append(spec)
    return specs


def assert_free_groups(specs):
    """Fail the sync when a deployment in a free/* group is not zero-cost (or is not only in "free").

    The group is the cost boundary: a key is granted a model name, not a deployment, so one paid copy
    in a free/* group bills every key that holds the name. Zero cost means an explicit zero input and
    output price on the deployment, not an unknown one, and no budget (a budget is a price).
    """
    for spec in specs:
        if not spec["name"].startswith(FREE_GROUP + "/"):
            continue
        params, info = spec["litellm_params"], spec["model_info"]
        problems = []
        if params.get("input_cost_per_token") != 0 or params.get("output_cost_per_token") != 0:
            problems.append("its recorded cost is not an explicit zero")
        if "max_budget" in params:
            problems.append("it carries a budget, so it has a price")
        if info.get("access_groups") != [FREE_ACCESS_GROUP]:
            problems.append("its access groups are not exactly the free group")
        if spec["route"] not in FREE_GROUP_ROUTES:
            problems.append("its route is not a free route")
        if problems:
            raise RuntimeError(f"{info.get('id')} may not join {spec['name']}: " + "; ".join(problems))


def render_models(deployments):
    """The text of config/models.generated.yaml: a header, then a JSON document (valid YAML)."""
    document = {
        "model_list": [
            {"model_name": spec["name"], "litellm_params": spec["litellm_params"], "model_info": spec["model_info"]}
            for spec in deployments
        ]
    }
    return f"{MODELS_HEADER}\n{MODELS_NOTE}\n{json.dumps(document, indent=2)}\n"


# The cost class of a callable model name, by its prefix. A tier step declares its class and
# validate_tiers() checks the declaration against this table, so a paid step cannot be written down
# as free: "free/" names only ever resolve to zero-cost deployments (assert_free_groups).
STEP_CLASS_PREFIXES = {"free/": "free", "vibe-tier/": "vibe", "opencode-go-tier/": "go"}
# A paid tier step is never a route wildcard (vibe/*, opencode-go/*; those carry the account's cap too, but a
# tier step names a copy so the key's access group decides what it reaches). It is "<route>-tier/<upstream id>", a
# deployment that is a copy of the route's own budgeted deployment with the SAME model_info.id, because
# LiteLLM keeps spend per deployment id: a tier call and a direct call draw on one budget. The copy's
# access group is "<route>-tier", so a key reaches it by holding that group and never by `vibe/*`.
TIER_ROUTES = {"vibe-tier": "vibe", "opencode-go-tier": "opencode-go"}
TIERS_BEGIN = "  # BEGIN tiers"
TIERS_NOTE = " (rendered from catalog/routing.json by `python3 catalog/sync.py --render-tiers`; do not edit by hand)"
TIERS_END = "  # END tiers"
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "config.yaml")


# The one tier whose chain may hold an opt_in_only model: it is the opt-in escalation tier.
OPT_IN_TIER = "tier-strong-open"
# The access group of the opt-in tier copies. They are NOT in the shared vibe-tier / opencode-go-tier groups, which
# every other tier key holds: only the tier-strong-open key holds this one, so no other key can call an opt-in model.
OPT_IN_ACCESS_GROUP = "strong-open-tier"


def step_class(model):
    """The cost class a callable model name belongs to, or None when no prefix names one."""
    for prefix, cost in STEP_CLASS_PREFIXES.items():
        if model.startswith(prefix):
            return cost
    return None


def tier_chains(rules):
    """Every tier as its flat, ordered list of steps ({"model", "cost"}), a nested tier expanded in place.

    A step is {"model": <callable name>, "cost": <class>} or {"tier": <other tier>}, which splices that
    tier's own steps in at that position. A model already in the chain keeps its first place, so a
    ladder that loops back (tier-go-cheap naming tier-vibe) cannot repeat a model, and a tier that
    names itself through others is rejected.
    """
    groups = (rules or {}).get("tier_groups") or {}

    def expand(name, trail):
        if name not in groups:
            raise RuntimeError(f"tier {name} is not defined in the routing rules")
        if name in trail:
            raise RuntimeError("tier cycle: " + " -> ".join([*trail, name]))
        steps = []
        for step in groups[name].get("steps") or []:
            steps.extend(expand(step["tier"], [*trail, name]) if "tier" in step else [step])
        return steps

    chains = {}
    for name in groups:
        seen, chain = set(), []
        for step in expand(name, []):
            if step["model"] not in seen:
                seen.add(step["model"])
                chain.append(step)
        chains[name] = chain
    return chains


def validate_tiers(rules):
    """Check the tier definitions; raise RuntimeError naming the first thing wrong.

    Each step's declared cost class must be the class its name's prefix gives; no step may cost more than
    its tier's ceiling (so tier-free, ceiling "free", holds only free/* names and can never reach a
    paid deployment); an opt-in-only model (rules["opt_in_only"], by canonical name) is in no chain but
    tier-strong-open's, which is itself the opt-in escalation tier (owner, 2026-11-18);
    a tier name is no planned model group (assert_no_tier_shadow(), against the real plan); and each tier's key_models cover every step, because a
    key reaches a name only by holding it or its access group ("free", "vibe-tier", "opencode-go-tier")
    and a fallback the key does not hold is skipped.
    """
    rules = rules or {}
    classes = list(rules.get("tier_cost_classes") or [])
    opt_in = set(rules.get("opt_in_only") or [])
    groups = rules.get("tier_groups") or {}
    for name, chain in tier_chains(rules).items():
        if not name.startswith("tier-"):
            raise RuntimeError(f"tier {name} must be named tier-<name>")
        ceiling = groups[name].get("ceiling")
        if ceiling not in classes:
            raise RuntimeError(f"tier {name} has no valid ceiling")
        keys = groups[name].get("key_models") or []
        if not chain or name not in keys:
            raise RuntimeError(f"tier {name} needs steps, and its key_models must name it")
        for step in chain:
            model, cost = step["model"], step.get("cost")
            if cost not in classes or cost != step_class(model):
                raise RuntimeError(f"tier {name}: step {model} declares cost {cost}, its name is class {step_class(model)}")
            if classes.index(cost) > classes.index(ceiling):
                raise RuntimeError(f"tier {name}: step {model} ({cost}) is above the tier's ceiling {ceiling}")
            if name != OPT_IN_TIER and canonical_name(None, model.split("/", 1)[1], rules) in opt_in:
                raise RuntimeError(f"tier {name}: {model} is opt-in only and may be in {OPT_IN_TIER} alone")
            opt_in_step = canonical_name(None, model.split("/", 1)[1], rules) in opt_in
            if opt_in_step:
                # An opt-in copy is in its own access group, not the shared one of its prefix.
                reached = any(model == key or key == OPT_IN_ACCESS_GROUP for key in keys)
            else:
                reached = any(model == key or key == model.split("/", 1)[0] or (key == FREE_ACCESS_GROUP and cost == "free")
                              for key in keys)
            if not reached:
                raise RuntimeError(f"tier {name}: key_models do not reach step {model}")
        if name != OPT_IN_TIER and OPT_IN_ACCESS_GROUP in keys:
            raise RuntimeError(f"tier {name}: key_models hold {OPT_IN_ACCESS_GROUP}, which only {OPT_IN_TIER} may")


def assert_no_tier_shadow(specs, rules):
    """Fail the sync when a planned model group is named like a tier.

    model_group_alias would shadow the group, and a key granted that name would be rerouted down the tier's chain.
    """
    clash = sorted({spec["name"] for spec in specs} & set((rules or {}).get("tier_groups") or {}))
    if clash:
        raise RuntimeError("a model group is named like a tier: " + ", ".join(clash))


def tier_router_settings(rules):
    """The router settings that make the tiers callable: {"model_group_alias", "fallbacks"}.

    A tier name is an alias of its first step's group, and the rest of its chain is the ordered
    fallback list under the tier's own name (LiteLLM looks fallbacks up by the requested name,
    alias included; tests/test_tiers.py runs it). No default_fallbacks, ever: one stray entry would
    send every free group to a paid tier.
    """
    validate_tiers(rules)
    chains = tier_chains(rules)
    return {
        "model_group_alias": {name: chain[0]["model"] for name, chain in chains.items()},
        "fallbacks": [{name: [step["model"] for step in chain[1:]]} for name, chain in chains.items() if len(chain) > 1],
    }


def render_tier_block(rules):
    """The lines config/config.yaml holds inside router_settings, from the tier definitions."""
    settings = tier_router_settings(rules)
    lines = [TIERS_BEGIN + TIERS_NOTE, "  model_group_alias:"]
    lines += [f"    {json.dumps(name)}: {json.dumps(target)}" for name, target in settings["model_group_alias"].items()]
    lines.append("  fallbacks:")
    lines += [f"    - {json.dumps(entry)}" for entry in settings["fallbacks"]]
    lines.append(TIERS_END)
    return "\n".join(lines)


def splice_tier_block(config_text, rules):
    """config.yaml's text with the rendered tier block in place of the marked one."""
    start, end = config_text.find(TIERS_BEGIN), config_text.find(TIERS_END)
    if start < 0 or end < start:
        raise RuntimeError("config.yaml has no tiers block between its BEGIN and END markers")
    return config_text[:start] + render_tier_block(rules) + config_text[end + len(TIERS_END):]


def tier_copies(specs, rules):
    """The budgeted deployment each paid tier step is served by: a copy of the route's own deployment.

    A step "vibe-tier/<id>" or "opencode-go-tier/<id>" is the route's canonical deployment of that
    upstream id, renamed to the step and given the access group "<prefix>". It keeps its model_info.id,
    budget and cooldown, so the spend of a tier call and of a direct call share one counter.
    A step whose model the plan has no canonical deployment for gets no copy: the name is then unknown to
    the router, which skips the step. It is never served by a wildcard, which carries no budget.
    """
    copies, seen = [], set()
    for chain in tier_chains(rules).values():
        for step in chain:
            prefix, _, upstream_id = step["model"].partition("/")
            route = TIER_ROUTES.get(prefix)
            if not route or step["model"] in seen:
                continue
            seen.add(step["model"])
            for spec in specs:
                if (spec["route"], spec["upstream_id"]) == (route, upstream_id) and spec["model_info"]["access_groups"] == [CANONICAL_ACCESS_GROUP]:
                    copy = copy_module.deepcopy(spec)
                    copy["name"] = step["model"]
                    copy["order"], copy["weight"] = 1, 1
                    copy["litellm_params"]["order"], copy["litellm_params"]["weight"] = 1, 1
                    opt_in = canonical_name(None, upstream_id, rules) in set(rules.get("opt_in_only") or [])
                    copy["model_info"]["access_groups"] = [OPT_IN_ACCESS_GROUP if opt_in else prefix]
                    copy["model_info"].pop("allowed_fails_policy", None)
                    copies.append(copy)
                    break
    return copies


def assert_tier_copies(specs, rules):
    """Fail the sync when a tier copy is not the budgeted deployment of its route (same id, same cap)."""
    budgets = account_budgets(rules)
    by_id = {spec["model_info"]["id"]: spec for spec in specs if spec["name"].split("/", 1)[0] not in TIER_ROUTES}
    for spec in specs:
        prefix = spec["name"].split("/", 1)[0]
        if prefix not in TIER_ROUTES:
            continue
        route, params = TIER_ROUTES[prefix], spec["litellm_params"]
        origin = by_id.get(spec["model_info"]["id"])
        budget = budgets.get(route) or {}
        problems = []
        if spec["route"] != route or origin is None:
            problems.append("it is not a copy of a deployment of its route")
        if not budget or (params.get("max_budget"), params.get("budget_duration")) != (budget.get("max_budget"), budget.get("budget_duration")):
            problems.append("it does not carry the route's budget")
        if origin is not None and origin["litellm_params"].get("max_budget") != params.get("max_budget"):
            problems.append("its budget differs from the deployment it shares an id with")
        if problems:
            raise RuntimeError(f"{spec['model_info']['id']} may not serve {spec['name']}: " + "; ".join(problems))


def missing_tier_steps(deployments, rules):
    """Steps of the tiers whose group the plan does not (yet) hold: a free/<canonical> group, or a paid
    step's budgeted copy. The router skips an unknown step and the next one serves; the run says so."""
    names = {spec["name"] for spec in deployments}
    return sorted({step["model"] for chain in tier_chains(rules).values() for step in chain if step["model"] not in names})


def record_groups(deployments):
    """The planned groups as the catalogue records them: name -> modality, public, deployments.

    The overview renders the groups from the catalogue, so the plan is recorded beside the probes
    that justify it: one entry per group, its deployments in the order the router tries them.
    """
    groups = {}
    for spec in deployments:
        access = spec["model_info"]["access_groups"]
        gated = [group for group in access if group in GATED_ACCESS_GROUPS.values()]
        entry = groups.setdefault(spec["name"], {
            "modality": spec["modality"],
            "public": access != [CANONICAL_ACCESS_GROUP] and not gated,
            "deployments": [],
        })
        if gated:
            entry["access_groups"] = gated
        elif FREE_ACCESS_GROUP in access:
            # A free group names its own access groups, so a reader reaches it by the exact group.
            entry["access_groups"] = list(access)
        entry["deployments"].append({
            "route": spec["route"],
            "upstream_id": spec["upstream_id"],
            "order": spec["order"],
            "weight": spec["weight"],
        })
    return groups


def previous_by_id(catalogue):
    out = {}
    for route, provider in (catalogue.get("providers") or {}).items():
        for model in provider.get("models", []):
            out[model["id"]] = model
    return out


def reference_key(model_id):
    """The spellings of a model id the public catalogues use: lower case, no `models/`, `:free` or `@region` suffix."""
    base = str(model_id).lower().removeprefix("models/")
    base = base.split("@", 1)[0].removesuffix(":free")
    return base


def reference_index(rules, env, http, log=None):
    """{route: {id: {"chat": bool, "deprecated": bool, "cost": (input, output) per token or None}}} from models.dev.

    models.dev (https://models.dev/api.json, OpenCode's model database) lists per provider each model's modalities,
    `type`, `status` and `cost` (US dollars per million tokens, 0 is free). routing.json `reference` maps a route to its
    models.dev provider id; a route not named, or a catalogue that cannot be read, gives no reference and the listing
    alone decides (fail closed: no reference never makes a model free). The id must match inside the route's own provider."""
    mapping = (rules or {}).get("reference") or {}
    if not mapping:
        return {}
    status, body = http("GET", env.get("MODELS_DEV_URL") or MODELS_DEV_URL, {}, None)
    if status != 200 or not isinstance(body, dict):
        if log:
            log(f"reference: models.dev answered {status}; the listing alone decides")
        return {}
    out = {}
    for route, provider in mapping.items():
        models = (body.get(provider) or {}).get("models") if isinstance(body.get(provider), dict) else None
        index = {}
        for model_id, model in (models or {}).items():
            if not isinstance(model, dict):
                continue
            modalities = model.get("modalities") if isinstance(model.get("modalities"), dict) else {}
            outputs, inputs = modalities.get("output"), modalities.get("input")
            chat = model.get("type") in (None, "chat") and (not isinstance(outputs, list) or "text" in outputs) and (
                not isinstance(inputs, list) or "text" in inputs or "image" in inputs)
            cost = model.get("cost") if isinstance(model.get("cost"), dict) else {}
            prices = None
            if zero_price(cost.get("input"), cost.get("output")) or all(
                    isinstance(cost.get(k), (int, float)) and not isinstance(cost.get(k), bool) for k in ("input", "output")):
                prices = (cost["input"] / 1e6, cost["output"] / 1e6)
            index[reference_key(model_id)] = {"chat": chat, "deprecated": model.get("status") == "deprecated", "cost": prices}
        out[route] = index
    return out


def traffic_evidence(env, http, log=None):
    """What real traffic through the proxy says per catalogue id: {id: {"ok": n, "denied": n}}.

    Reads the proxy's own Prometheus series (litellm_proxy_total_requests_metric by status_code,
    litellm_proxy_failed_requests_metric by exception_status, both labelled model_id = the deployment's
    model_info.id, `[free/|public/]<route>/<upstream id>`) over TRAFFIC_WINDOW from TRAFFIC_PROMETHEUS_URL. Unset or
    unreachable means no evidence, never an error: the listing and the recorded answers decide then."""
    base = (env.get("TRAFFIC_PROMETHEUS_URL") or "").rstrip("/")
    if not base:
        return {}
    out = {}

    def query(promql, label, key):
        status, body = http("GET", f"{base}/api/v1/query?" + urllib.parse.urlencode({"query": promql}), {}, None)
        results = (body or {}).get("data", {}).get("result") if status == 200 and isinstance(body, dict) else None
        if results is None:
            if log:
                log(f"traffic evidence: the query for {key} answered {status}")
            return
        for row in results:
            metric = row.get("metric") or {}
            model = str(metric.get("model_id") or "")
            for prefix in ("free/", "public/"):
                model = model.removeprefix(prefix)
            try:
                count = float(row["value"][1])
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            if model and count > 0 and label(metric):
                out.setdefault(model, {"ok": 0, "denied": 0})[key] += count

    query(f"sum by (model_id, status_code) (increase(litellm_proxy_total_requests_metric[{TRAFFIC_WINDOW}]))",
          lambda m: str(m.get("status_code")) == "200", "ok")
    query(f"sum by (model_id, exception_status) (increase(litellm_proxy_failed_requests_metric[{TRAFFIC_WINDOW}]))",
          lambda m: str(m.get("exception_status")) in DENIED_STATUSES, "denied")
    return out


def stale_provider(old, route, key_env):
    """A route whose list could not be read keeps its last entries, marked stale, as a copy."""
    return {
        "metadata": dict(old.get("metadata") or {}, route=route, key_env=key_env, stale=True),
        "models": [dict(model, metadata=dict(model.get("metadata") or {})) for model in old.get("models") or []],
    }


def build(env, http, previous, now, probe_ids=(), rules=None):
    """The catalogue, and counts. `previous` is the last published catalogue (the cache). `probe_ids` names
    `<route>/<model>` ids to send one real chat request to (a person's debugging; the sync never does it by itself)."""
    cache = previous_by_id(previous)
    by_route = {entry["route"]: entry for entry in ROUTES}
    env, account_error = resolve_cloudflare_account(env, http, lambda line: print(line, file=sys.stderr))
    if account_error:
        stats_errors = [account_error]
    else:
        stats_errors = []
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    providers, stats = {}, {"listed": 0, "probed": 0, "route_errors": []}
    candidates = {}
    queue = []
    stats["route_errors"] += stats_errors
    log = lambda line: print(f"catalog: {line}", file=sys.stderr)
    reference = reference_index(rules, env, http, log)
    traffic = traffic_evidence(env, http, log)
    for entry in ROUTES:
        route = entry["route"]
        if route == "cloudflare" and account_error:
            # One clear error (already in route_errors); no listing or scan can work without the account.
            old = (previous.get("providers") or {}).get(route)
            providers[route] = stale_provider(old, route, entry["key_env"]) if old and old.get("models") else {
                "metadata": {"route": route, "key_env": entry["key_env"]}, "models": []}
            candidates[route] = []
            continue
        if not (env.get(entry["key_env"]) or "") and not (entry.get("anonymous") and route_base(entry, env)):
            # A route whose key is not written yet is skipped silently: the sync must run before the
            # owner adds it. Its last entries stay, stale, until a run can list it again.
            old = (previous.get("providers") or {}).get(route)
            if old and old.get("models"):
                providers[route] = stale_provider(old, route, entry["key_env"])
            else:
                providers[route] = {"metadata": {"route": route, "key_env": entry["key_env"]}, "models": []}
            continue
        missing = missing_setting(entry, env)
        if missing:
            # One clear route error naming the Vault field; neither a scan nor a listing can work without it.
            stats["route_errors"].append(f"{route}: {missing}"[:200])
            old = (previous.get("providers") or {}).get(route)
            providers[route] = stale_provider(old, route, entry["key_env"]) if old and old.get("models") else {
                "metadata": {"route": route, "key_env": entry["key_env"]}, "models": []}
            if entry["kind"] in STATIC_KINDS:
                candidates[route] = []
            continue
        if entry["kind"] in STATIC_KINDS:
            # Show the owner what this route lists, so an id can be approved in free_ids. Never deployed.
            try:
                candidates[route] = scan_candidates(entry, env, http, rules)
            except Exception as error:
                candidates[route] = []
                stats["route_errors"].append(f"{route}: candidate scan: {error}"[:200])
        try:
            listed = list_models(entry, env, http, rules, reference.get(route))
        except Exception as error:  # a route that cannot be listed keeps its last entries
            stats["route_errors"].append(f"{route}: {error}"[:200])
            old = (previous.get("providers") or {}).get(route)
            if old:
                providers[route] = stale_provider(old, route, entry["key_env"])
            continue
        if not listed and (previous.get("providers") or {}).get(route, {}).get("models"):
            stats["route_errors"].append(f"{route}: the provider listed no model; the last entries are kept")
            providers[route] = stale_provider(previous["providers"][route], route, entry["key_env"])
            continue
        models = []
        for item in sorted(listed, key=lambda m: m["id"]):
            full = f"{route}/{item['id']}"
            old = cache.get(full)
            meta = {"name": item["name"], "context_length": item["context_length"], "free": item["free"], "modality": item["modality"]}
            for field in ("canonical_id", "api", "api_base", "price"):
                if item.get(field):
                    meta[field] = item[field]
            if old:
                for k in ("status", "reason", "probed_at", "failing_probes", "was_usable", "verified_by"):
                    if k in (old.get("metadata") or {}):
                        meta[k] = old["metadata"][k]
            if route == "opencode-go" and any(h in item["id"] for h in OPT_IN_HINTS):
                meta["needs_org_setting_hint"] = True
            model = {"id": full, "description": "free" if item["free"] else "", "metadata": meta}
            models.append(model)
            stats["listed"] += 1
            if item["chat"] is False:
                meta.update(status="not-chat", reason="")
                meta.setdefault("probed_at", stamp)
            elif route in FREE_ONLY and item.get("paid_only"):
                meta.update(status="unprobed", reason=PAID_ONLY_REASON)
                meta.pop("probed_at", None)
                meta.pop("was_usable", None)
            elif route in FREE_ONLY and not item["free"]:
                reason = APPROVAL_REASON if by_route[route]["kind"] in STATIC_KINDS else PAID_REASON.get(route, "paid models on a free-only route are not probed")
                meta.update(status="unprobed", reason=reason)
                meta.pop("probed_at", None)
            elif item.get("unavailable"):
                # The listing itself says it cannot be served: not deployed, and no request to find out.
                meta.update(status="unknown", reason=item["unavailable"], failing_probes=2, verified_by="listing")
                meta.pop("was_usable", None)
            else:
                seen = (traffic or {}).get(full) or {}
                if seen.get("ok"):
                    meta.update(status="usable", reason="", failing_probes=0, verified_by="traffic", probed_at=stamp)
                elif seen.get("denied"):
                    # Real traffic was refused (401, 403, 404): blocked while that evidence is inside the window.
                    meta.update(status="blocked", reason="real requests were refused (401, 403 or 404)", failing_probes=2, verified_by="traffic")
                    meta.pop("was_usable", None)
                elif full in probe_ids:
                    queue.append((route, model))
                elif meta.get("status") in RECORDED:
                    pass  # an answer already recorded is kept
                elif item.get("unlisted"):
                    # An approved id the list omits has no listing to read: it waits for real traffic or a manual probe.
                    meta.update(status="pending", reason="not in the provider's list; waiting for real traffic")
                else:
                    meta.update(status="usable", reason="", failing_probes=0, verified_by="listing", probed_at=stamp)
        # A model the provider stopped listing is kept for one run: a provider that hiccups must
        # not empty the catalogue, and the proxy must not lose a model over one bad listing.
        present = {model["id"] for model in models}
        for gone in (previous.get("providers") or {}).get(route, {}).get("models", []):
            if gone["id"] in present:
                continue
            meta = dict(gone.get("metadata") or {})
            meta["absent_runs"] = meta.get("absent_runs", 0) + 1
            if meta["absent_runs"] >= 2:
                continue
            models.append({"id": gone["id"], "description": gone.get("description", ""), "metadata": meta})
        models.sort(key=lambda model: model["id"])
        providers[route] = {"metadata": {"route": route, "key_env": entry["key_env"]}, "models": models}
    for route, model in queue:
        status, reason = probe(by_route[route], model["id"].split("/", 1)[1], env, http, model["metadata"])
        meta = model["metadata"]
        meta.update(status=status, reason=reason, probed_at=stamp)
        # Two consecutive blocked or unknown probes are what drops a model.
        meta["failing_probes"] = (meta.get("failing_probes", 0) + 1) if status in ("blocked", "unknown") else 0
        meta["verified_by"] = "probe"
        stats["probed"] += 1
    for provider in providers.values():
        counts = {}
        for model in provider["models"]:
            meta = model["metadata"]
            status = meta.get("status", "pending")
            meta.setdefault("status", status)
            # A model that was usable keeps its place through a transient state; two blocked or
            # unknown probes in a row are what takes it away.
            if status == "usable":
                meta["was_usable"] = True
            elif meta.get("failing_probes", 0) >= 2:
                meta.pop("was_usable", None)
            counts[status] = counts.get(status, 0) + 1
        provider["metadata"].update(count=len(provider["models"]), by_status=counts)
    catalogue = {
        "version": 1,
        "updated_at": stamp,
        "metadata": {
            "generator": "JorisJonkers-dev litellm catalog/sync.py",
            "statuses": list(STATUSES),
            "comment": "Model ids are what a caller sends to LiteLLM (<route>/<provider model id>). "
            "status is what a probe at the provider with that route's key answered; reason is the provider's own text.",
        },
        "providers": providers,
    }
    # Reported, never merged: two spellings of one model on different routes are a person's call.
    catalogue["possible_duplicates"] = near_duplicates(catalogue, rules)
    catalogue["free_candidates"] = candidates
    return catalogue, stats


def comparable(catalogue):
    """The catalogue without its run time: what a publish compares."""
    return {k: v for k, v in catalogue.items() if k != "updated_at"}


def make_http(timeout=60):
    def http(method, url, headers, body):
        data = None if body is None else body.encode() if isinstance(body, str) else json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method=method, headers={"Accept": "application/json", "User-Agent": "JorisJonkers-dev/litellm-catalog", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw, code = resp.read().decode(), resp.status
        except urllib.error.HTTPError as error:
            raw, code = error.read().decode(errors="replace"), error.code
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            return 599, str(error)
        try:
            return code, json.loads(raw)
        except ValueError:
            return code, raw

    return http


def github_headers(token):
    """The token travels in this header only: never in a URL, a body or a printed line."""
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def github_get_text(repo, path, token, http, ref=None):
    """A published file's text and sha; None, None when it does not exist yet."""
    url = f"{GITHUB}/repos/{repo}/contents/{path}" + (f"?ref={ref}" if ref else "")
    status, body = http("GET", url, github_headers(token), None)
    if status == 404:
        return None, None
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"reading {repo}/{path} answered {status}")
    if body.get("encoding") == "none":
        # Over 1 MB the contents API leaves the content out; the blob API returns it up to 100 MB.
        status, blob = http("GET", f"{GITHUB}/repos/{repo}/git/blobs/{body['sha']}", github_headers(token), None)
        if status != 200 or not isinstance(blob, dict):
            raise RuntimeError(f"reading {repo}/{path} as a blob answered {status}")
        body = dict(body, content=blob["content"])
    return base64.b64decode(body["content"]).decode(), body["sha"]


def github_get(repo, path, token, http):
    text, sha = github_get_text(repo, path, token, http)
    return (json.loads(text) if text is not None else None), sha


def github_put_text(repo, path, token, text, sha, http, message, branch=None):
    content = base64.b64encode(text.encode()).decode()
    body = {"message": message, "content": content}
    if sha:
        body["sha"] = sha
    if branch:
        body["branch"] = branch
    status, answer = http("PUT", f"{GITHUB}/repos/{repo}/contents/{path}", github_headers(token), body)
    if status not in (200, 201):
        raise RuntimeError(f"publishing to {repo}/{path} answered {status}")


def github_put(repo, path, token, catalogue, sha, http):
    github_put_text(repo, path, token, json.dumps(catalogue, indent=2) + "\n", sha, http, "chore: refresh model catalogue")


def github_ref(repo, branch, token, http):
    """The commit a branch points at."""
    status, body = http("GET", f"{GITHUB}/repos/{repo}/git/ref/heads/{branch}", github_headers(token), None)
    sha = ((body or {}).get("object") or {}).get("sha") if isinstance(body, dict) else None
    if status != 200 or not sha:
        raise RuntimeError(f"reading {repo}'s {branch} answered {status}")
    return sha


def github_reset_branch(repo, branch, sha, token, http):
    """Point the bot branch at `sha`, creating it when it does not exist yet."""
    status, _ = http("POST", f"{GITHUB}/repos/{repo}/git/refs", github_headers(token), {"ref": f"refs/heads/{branch}", "sha": sha})
    if status in (200, 201):
        return
    if status != 422:
        raise RuntimeError(f"creating {repo}'s {branch} answered {status}")
    # 422 is GitHub's "Reference already exists": move it instead.
    status, _ = http("PATCH", f"{GITHUB}/repos/{repo}/git/refs/heads/{branch}", github_headers(token), {"sha": sha, "force": True})
    if status not in (200, 201):
        raise RuntimeError(f"resetting {repo}'s {branch} answered {status}")


def github_open_pr(repo, branch, token, http):
    """The number of the open pull request from `branch`, or None."""
    owner = repo.split("/", 1)[0]
    status, body = http("GET", f"{GITHUB}/repos/{repo}/pulls?state=open&head={owner}:{branch}", github_headers(token), None)
    if status != 200 or not isinstance(body, list):
        raise RuntimeError(f"listing {repo}'s pull requests answered {status}")
    return body[0].get("number") if body and isinstance(body[0], dict) else None


def github_close_pr(repo, number, token, http):
    http("PATCH", f"{GITHUB}/repos/{repo}/pulls/{number}", github_headers(token), {"state": "closed"})


def github_update_pr_body(repo, number, body, token, http):
    """Best effort: the candidates section must not fail the run."""
    if number:
        http("PATCH", f"{GITHUB}/repos/{repo}/pulls/{number}", github_headers(token), {"body": body})


def github_create_pr(repo, branch, token, http, title, body):
    status, answer = http("POST", f"{GITHUB}/repos/{repo}/pulls", github_headers(token),
                          {"title": title, "body": body, "head": branch, "base": "main"})
    number = answer.get("number") if isinstance(answer, dict) else None
    if status not in (200, 201) or not number:
        raise RuntimeError(f"opening a pull request on {repo} answered {status}")
    return number


def github_label(repo, number, labels, token, http):
    """Best effort: a label the repository does not have must not fail the run."""
    http("POST", f"{GITHUB}/repos/{repo}/issues/{number}/labels", github_headers(token), {"labels": list(labels)})


def deployment_ids(text):
    """The model_info ids in a generated file, ignoring its comment header."""
    if not text:
        return set()
    try:
        document = json.loads("\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#")))
    except ValueError:
        return set()
    return {str((item.get("model_info") or {}).get("id")) for item in (document.get("model_list") or []) if isinstance(item, dict)}


def models_body(current, text, candidates=None):
    """The pull request body: what the run adds and removes, then the free-route candidates."""
    before, after = deployment_ids(current), deployment_ids(text)
    body = (
        "The catalogue sync regenerated `config/models.generated.yaml` from the probed catalogue.\n\n"
        f"- deployments added: {len(after - before)}\n"
        f"- deployments removed: {len(before - after)}\n"
    )
    if candidates is not None:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from overview import candidates_section

        body += "\n" + "\n".join(candidates_section(candidates, heading="###")) + "\n"
    return body


def publish_models(repo, token, text, http, env=None, candidates=None):
    """Put the generated deployments on the bot branch and open its pull request.

    Returns False when the repository's main branch already holds this text: nothing is written,
    and an open pull request from the bot branch is left as it is. Raises RuntimeError naming the
    step that failed; never a token.
    """
    env = env or {}
    path = env.get("MODELS_PATH", MODELS_PATH)
    branch = env.get("MODELS_BRANCH", MODELS_BRANCH)
    current, sha = github_get_text(repo, path, token, http, ref="main")
    if current == text:
        # Main already holds this text: an open pull request carrying another text is stale, and
        # merging it would move main away from what the catalogue justifies. Close it.
        stale = github_open_pr(repo, branch, token, http)
        if stale is not None:
            github_close_pr(repo, stale, token, http)
        return False
    if github_open_pr(repo, branch, token, http) is not None:
        # An open pull request that already carries this text is left alone: no hourly churn.
        try:
            if github_get_text(repo, path, token, http, ref=branch)[0] == text:
                github_update_pr_body(repo, github_open_pr(repo, branch, token, http), models_body(current, text, candidates), token, http)
                return False
        except RuntimeError:
            pass
    github_reset_branch(repo, branch, github_ref(repo, "main", token, http), token, http)
    github_put_text(repo, path, token, text, sha, http, MODELS_MESSAGE, branch=branch)
    number = github_open_pr(repo, branch, token, http)
    if number is None:
        number = github_create_pr(repo, branch, token, http, MODELS_TITLE, models_body(current, text, candidates))
    else:
        github_update_pr_body(repo, number, models_body(current, text, candidates), token, http)
    github_label(repo, number, MODELS_LABELS, token, http)
    return True


def without_stamp(text):
    """A rendered page without its run time: what a publish compares."""
    return "\n".join(line for line in (text or "").splitlines() if not line.startswith("Last updated"))


def main(argv=None, env=None, http=None, now=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--output", default="model-catalog.json", help="where the catalogue is written when it is not published")
    ap.add_argument("--probe", action="append", default=[], metavar="ROUTE/MODEL",
                    help="send one real chat request to this model (debugging; it spends the provider's quota)")
    ap.add_argument("--render-tiers", action="store_true", help="rewrite the tiers block of config/config.yaml from routing.json and exit")
    args = ap.parse_args(argv)
    if args.render_tiers:
        with open(CONFIG_PATH) as handle:
            updated = splice_tier_block(handle.read(), load_rules())
        with open(CONFIG_PATH, "w") as handle:
            handle.write(updated)
        return 0
    env = os.environ if env is None else env
    http = http or make_http()
    now = now or datetime.datetime.now(datetime.timezone.utc)
    repo, path, token = env.get("CATALOG_REPO"), env.get("CATALOG_PATH", "model-catalog.json"), env.get("CATALOG_TOKEN")
    publish = bool(repo and token)
    previous, sha = None, None
    if publish:
        try:
            previous, sha = github_get(repo, path, token, http)
        except RuntimeError as error:
            # Without the last catalogue there is no cache and no safe publish: fail the run.
            print(f"catalog: {error}", file=sys.stderr)
            return 1
    elif os.path.exists(args.output):
        previous = json.load(open(args.output))
    previous = previous or {}
    rules = load_rules()
    catalogue, stats = build(env, http, previous, now, args.probe, rules)
    print(f"catalog: listed {stats['listed']} models, probed {stats['probed']} (only what --probe named; the sync sends no request by itself)")
    for line in stats["route_errors"]:
        print(f"catalog: {line}", file=sys.stderr)
    # The deployments the probes justify, as plain LiteLLM config in git, and the groups the
    # overview renders: both come from the same plan, so the page and the file cannot disagree.
    try:
        deployments = plan_groups(catalogue, rules)
        validate_tiers(rules)
    except RuntimeError as error:
        # A free group with a paid copy, or a tier chain that breaks its own rules, fails the run.
        print(f"catalog: {error}", file=sys.stderr)
        return 1
    for step in missing_tier_steps(deployments, rules):
        print(f"catalog: tier step {step} has no group in this plan; the router skips it", file=sys.stderr)
    catalogue["groups"] = record_groups(deployments)
    # A run that listed nothing at all is a failed run, so the scheduled Process shows red.
    failed = stats["listed"] == 0
    if publish:
        try:
            if previous and comparable(previous) == comparable(catalogue):
                print("catalog: unchanged, not published")
            else:
                github_put(repo, path, token, catalogue, sha, http)
                print(f"catalog: published to {repo}/{path}")
        except RuntimeError as error:
            print(f"catalog: {error}", file=sys.stderr)
            failed = True
    else:
        with open(args.output, "w") as out:
            json.dump(catalogue, out, indent=2)
            out.write("\n")
        print(f"catalog: wrote {args.output}")
    # The overview module is a sibling (the script runs as /app/catalog/sync.py), so it is
    # imported here, once this module is complete.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from overview import render

    overview = render(catalogue)
    readme = env.get("CATALOG_README_PATH", "README.md")
    if publish:
        try:
            remote, readme_sha = github_get_text(repo, readme, token, http)
            if without_stamp(remote) == without_stamp(overview):
                print(f"catalog: {readme} unchanged, not published")
            else:
                github_put_text(repo, readme, token, overview, readme_sha, http, "chore: refresh model overview")
                print(f"catalog: published the overview to {repo}/{readme}")
        except RuntimeError as error:
            print(f"catalog: {error}", file=sys.stderr)
            failed = True
    else:
        local = os.path.join(os.path.dirname(args.output), "model-overview.md")  # never the working directory's README.md
        with open(local, "w") as out:
            out.write(overview)
        print(f"catalog: wrote {local}")
    # The deployments the probes justify, as plain LiteLLM config in git: a pull request in the
    # litellm repository, or a file beside the catalogue when no repository is named.
    text = render_models(deployments)
    models_repo, models_token = env.get("MODELS_REPO"), env.get("MODELS_TOKEN")
    if models_repo and models_token:
        try:
            if publish_models(models_repo, models_token, text, http, env, catalogue.get("free_candidates") or {}):
                print(f"catalog: opened or updated the models pull request on {models_repo}")
            else:
                print(f"catalog: {models_repo} already holds the generated deployments")
        except RuntimeError as error:
            print(f"catalog: {error}", file=sys.stderr)
            failed = True
    else:
        local = os.path.join(os.path.dirname(args.output), "models.generated.yaml")
        with open(local, "w") as out:
            out.write(text)
        print(f"catalog: wrote {local}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
