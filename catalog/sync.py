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
  error             anything else (rate limit, 5xx, timeout); tried again next run
  not-chat          the provider lists it as no chat model (embeddings, OCR, transcription)
  unprobed          not probed on purpose (a paid OpenRouter model: the route is used for `:free` ids)
  pending           due for a probe but over this run's cap; probed in a later run

The probe goes to the provider, not through the proxy: a model the proxy does not know yet
cannot be probed through it, and the catalogue is what decides which models the proxy serves.

The result is cached in the catalogue itself (probed_at per model), so a run probes only models
that are new, errored or older than --max-age. A model that disappears from its provider's list
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
down, `weight` splits traffic inside a level, and max_budget/budget_duration cap one deployment.
Routing is by which provider serves the canonical model, not by model family: the Vibe plan
serves non-Mistral models too, and where it serves one it wins. Only exact canonical-name
equality merges two upstream models; a near duplicate is reported in catalogue.possible_duplicates
and never merged.

Probe cost bound: each probe is one minimal request (about 8 input tokens + 16 output tokens), and
a provider that refuses a small max_tokens is retried once with its own minimum, so a run costs at
most (models probed) x (about 8 input + 16 output tokens), doubled in the worst case. The models
of each route are probed once per --max-age (default 24h); free and local models cost nothing.
Every run prints the number probed so the bound can be checked.

Standard library only: it runs in the LiteLLM image as a scheduled Process, and in CI.
"""
import argparse
import base64
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request

# route, the env names its provider base and key come from (config/config.yaml names the same),
# and how its list is read. The four Mistral accounts are listed and probed separately because
# their model sets differ, and a route whose key is not written yet is skipped until it is.
ROUTES = [
    {"route": "vibe", "base_env": "MISTRAL_API_BASE", "key_env": "VIBE_API_KEY", "kind": "mistral"},
    {"route": "vibe-study", "base_env": "MISTRAL_API_BASE", "key_env": "VIBE_STUDY_API_KEY", "kind": "mistral"},
    {"route": "vibe-api", "base_env": "MISTRAL_API_BASE", "key_env": "VIBE_API_GENERAL_KEY", "kind": "mistral"},
    {"route": "vibe-study-api", "base_env": "MISTRAL_API_BASE", "key_env": "VIBE_STUDY_API_GENERAL_KEY", "kind": "mistral"},
    {"route": "opencode-go", "base_env": "OPENCODE_GO_API_BASE", "key_env": "OPENCODE_GO_API_KEY", "kind": "openai"},
    {"route": "openrouter", "base_env": "OPENROUTER_API_BASE", "key_env": "OPENROUTER_API_KEY", "kind": "openrouter"},
    {"route": "local", "base_env": "LOCAL_LLM_API_BASE", "key_env": "LOCAL_LLM_API_KEY", "kind": "openai"},
    {"route": "google", "base_env": "GOOGLE_API_BASE", "key_env": "GEMINI_API_KEY", "kind": "google"},
    {"route": "anthropic", "base_env": "ANTHROPIC_API_BASE", "key_env": "ANTHROPIC_API_KEY", "kind": "anthropic"},
]
# OpenCode Go models the docs say need an organisation setting (a privacy opt-in or a region):
# flagged in the catalogue as a hint; the probe still decides what the account can use.
OPT_IN_HINTS = ("deepseek-v4", "longcat-2.5", "muse-spark", "beam-free")
# OpenRouter's free tier allows few requests a day, so only this many free models are probed a run.
DEFAULT_ROUTE_LIMITS = {"openrouter": 30}
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
PREFIXES = {"mistral": "mistral", "openai": "openai", "openrouter": "openrouter", "google": "gemini", "anthropic": "anthropic"}
# Kinds whose route is free only: a paid id is never deployed, so the route cannot cost anything.
FREE_ONLY = frozenset(entry["route"] for entry in ROUTES if entry["kind"] == "openrouter")
# A non-chat modality and the LiteLLM model_info.mode that marks it. Moderation is never deployed.
MODES = {"embedding": "embedding", "ocr": "ocr", "transcription": "audio_transcription"}
# A public route's deployment is a free tier whose prompts may be used for training: it cools down
# for longer than a paid one, because its quota is per minute and per day.
PUBLIC_COOLDOWN = 120
# The access group a public deployment carries; a key without it never sees a public/* model.
PUBLIC_ACCESS_GROUP = "google-free-public"
# The generated deployments: a file in the litellm repository, refreshed by a pull request.
MODELS_PATH = "config/models.generated.yaml"
MODELS_BRANCH = "bot/model-catalog"
MODELS_TITLE = "chore(models): refresh the generated model deployments"
MODELS_MESSAGE = "chore(models): refresh the generated model deployments"
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
    return rules


def classify(status, text):
    """(classification, reason) of one probe answer. Budget first, then account, then absence."""
    if 200 <= status < 300:
        return "usable", ""
    message = error_message(text)
    reason = SECRETISH.sub("[redacted]", message)[:240]
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
    # Any other body is not published: only a `message` field is the provider's own reason.
    message = body.get("message") if isinstance(body, dict) else None
    return message if isinstance(message, str) else ""


def is_free_price(pricing):
    try:
        return float(pricing.get("prompt", "0")) == 0 and float(pricing.get("completion", "0")) == 0
    except (TypeError, ValueError, AttributeError):
        return False


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
    return "embedding" if "embed" in model_id else "chat"


def list_models(entry, env, http):
    """The models the provider lists for this route: id, name, context length, free, chat, modality."""
    base = (env.get(entry["base_env"]) or "").rstrip("/")
    if not base:
        raise RuntimeError(f"{entry['base_env']} is not set")
    key = env.get(entry["key_env"]) or ""
    kind = entry["kind"]
    if kind == "anthropic":
        headers = {"anthropic-version": ANTHROPIC_VERSION, **({"x-api-key": key} if key else {})}
    else:
        headers = {"Authorization": f"Bearer {key}"} if key else {}
    status, body = http("GET", f"{base}/models", headers, None)
    if kind == "openrouter" and key:
        # The account-aware list (models this key may use), when OpenRouter serves it.
        user_status, user_body = http("GET", f"{base}/models/user", headers, None)
        if user_status == 200 and isinstance(user_body, dict) and isinstance(user_body.get("data"), list):
            status, body = user_status, user_body
    if status != 200 or not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise RuntimeError(f"{entry['route']}: the model list answered {status}")
    found = {}
    for item in body["data"]:
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
        if kind == "mistral":
            caps = item.get("capabilities") or {}
            out["chat"] = bool(caps.get("completion_chat")) if "completion_chat" in caps else None
            out["context_length"] = item.get("max_context_length")
        elif kind == "openrouter":
            out["context_length"] = item.get("context_length")
            out["free"] = item["id"].endswith(":free") or is_free_price(item.get("pricing"))
        elif kind == "google":
            out["context_length"] = item.get("inputTokenLimit")
        elif entry["route"] == "local":
            out["free"] = True
        if out["modality"] != "chat":
            out["chat"] = False
        found[model_id] = out
    return list(found.values())


def probe(entry, upstream_id, env, http):
    """One minimal request at the provider itself, with the route's own key.

    Not through the proxy: a model the proxy has not deployed yet has no route to go through.
    """
    base = (env.get(entry["base_env"]) or "").rstrip("/")
    if not base:
        return "error", f"{entry['base_env']} is not set"
    key = env.get(entry["key_env"]) or ""
    headers = {"Content-Type": "application/json"}
    if entry["kind"] == "anthropic":
        # Anthropic's messages endpoint takes the key in its own header, and a version header.
        headers["anthropic-version"] = ANTHROPIC_VERSION
        if key:
            headers["x-api-key"] = key
        url = f"{base}/messages"
        body = {"model": upstream_id, "max_tokens": PROBE_MAX_TOKENS, "messages": [{"role": "user", "content": "ping"}]}
    else:
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if entry["route"] == "opencode-go":
            # OpenCode answers 400 MissingSessionID without a session header.
            headers["x-opencode-session"] = "litellm-catalog-probe"
        url = f"{base}/chat/completions"
        body = {"model": upstream_id, "messages": [{"role": "user", "content": "ping"}], "max_tokens": PROBE_MAX_TOKENS}
    status, answer = http("POST", url, headers, body)
    text = answer if isinstance(answer, str) else json.dumps(answer)
    if status == 400:
        # A provider that refuses the small max_tokens names its floor: retry once at that floor.
        match = PROBE_MINIMUM.search(error_message(text))
        if match and PROBE_MAX_TOKENS < int(match[1]) <= PROBE_RETRY_CEILING:
            status, answer = http("POST", url, headers, dict(body, max_tokens=int(match[1])))
            text = answer if isinstance(answer, str) else json.dumps(answer)
    return classify(status, text)


def parse_age(text):
    match = re.fullmatch(r"(\d+)([hd])", text)
    if not match:
        raise ValueError("--max-age is a number of hours or days, like 24h or 2d")
    return datetime.timedelta(**{"hours" if match[2] == "h" else "days": int(match[1])})


# A leading vendor segment (OpenRouter's "mistralai/mistral-large") and a trailing version stamp
# ("mistral-large-2411", "claude-sonnet-4-5-20250929"): the two spellings that are not the model.
VENDOR = re.compile(r"^[a-z0-9][a-z0-9._-]*/")
VERSION = re.compile(r"-\d{4,8}$")


def canonical_name(route, upstream_id, rules, canonical_id=None):
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
    name = VERSION.sub("", VENDOR.sub("", name, count=1).removesuffix(":free").removesuffix("-latest"))
    return aliases.get(name, name)


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


def group_name(name, mode, primary, public=False):
    """The LiteLLM model_name of one deployment: the canonical name, and the mode when it differs.

    A group never mixes modes: a model whose mode differs from its name-mates gets its own group,
    "<canonical>:<mode>", so an embedding request cannot land on a chat deployment.
    """
    suffix = "" if mode == primary else ":" + MODES.get(mode, mode)
    return ("public/" if public else "") + name + suffix


def deployment(group, canonical, route, chosen, order, weight, entry, public=False, budget=None, cooldown=None):
    """One deployment of a canonical model: its group name, params, order, weight and info."""
    upstream_id = chosen["upstream_id"]
    params = litellm_params(entry, upstream_id)
    params.update({"order": order, "weight": weight})
    if entry["kind"] == "openrouter":
        # Free ids only, and the provider may neither train on nor fall back with the prompt.
        params["extra_body"] = {"provider": {"data_collection": "deny", "allow_fallbacks": False}}
    if budget:
        params.update(budget)
    if cooldown:
        params["cooldown_time"] = cooldown
    info = {
        "id": ("public/" if public else "") + f"{route}/{upstream_id}",
        "route": route,
        "canonical": canonical,
        "access_groups": [PUBLIC_ACCESS_GROUP] if public else ["canonical"],
    }
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


def plan_groups(catalogue, rules):
    """The canonical model groups: one model_name per canonical model, its deployments ordered.

    Routing is by which provider serves the canonical model, not by model family: the Vibe plan
    serves non-Mistral models too, and where it serves one it wins. Only exact canonical-name
    equality merges two upstream models; a near duplicate is reported, never merged.

    A public route (a free tier whose prompts may be used for training) never appears in a normal
    group: it gets a "public/<canonical>" group of its own, first, with the normal deployments
    behind it, and only a key carrying the public access group sees it.
    """
    rules = rules or {}
    tiers = rules.get("tiers") or []
    rank = {}
    for index, tier in enumerate(tiers):
        for route, weight in tier:
            rank.setdefault(route, (index + 1, weight))
    # A route no tier names comes after every tier, at weight 1: no rule, no preference.
    unranked = (len(tiers) + 1, 1)
    backup_only = rules.get("backup_only") or {}
    exclusive = rules.get("exclusive") or []
    free_first = set(rules.get("free_first_unless_served_by") or [])
    api_only = set(rules.get("api_only_routes") or [])
    budgets = rules.get("budgets") or {}
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
            name = canonical_name(route, upstream_id, rules, meta.get("canonical_id"))
            chosen = groups.setdefault(name, {}).get(route)
            if chosen is None or len(upstream_id) < len(chosen["upstream_id"]):
                groups[name][route] = {"upstream_id": upstream_id, "modality": kind, "free": bool(meta.get("free"))}
    specs = []
    for name, routes in sorted(groups.items()):
        serving = set(routes)
        # A backup route is in the group only when nothing better serves the model.
        for route in sorted(serving):
            if route in backup_only and serving & set(backup_only[route]):
                serving.discard(route)
        # An exclusive model is only on its own routes, and never free.
        only = set()
        for rule in exclusive:
            if re.search(rule.get("match") or "", name):
                only |= set(rule.get("routes") or [])
        if only:
            serving &= only
        if not serving:
            continue
        primary = primary_mode({route: routes[route] for route in serving})
        normal = serving - public_routes
        public = serving & public_routes
        # Only a free-only route's deployment is a free deployment; a local one is a normal tier.
        free = set() if only else {route for route in normal if by_route[route]["kind"] == "openrouter" and routes[route]["free"]}
        # The tiers that apply to this model, renumbered from 1: a model only tier 4 serves gets 1.
        levels = sorted({rank.get(route, unranked)[0] for route in normal - free})
        # Free deployments come first unless a plan route serves the model, then after the last tier.
        free_before = bool(free) and not (serving & free_first)
        order_of = {level: index + 1 + (1 if free_before else 0) for index, level in enumerate(levels)}
        free_order = 1 if free_before else len(levels) + 1
        planned = []
        for route in sorted(normal):
            if route in free:
                order, weight = free_order, 1
            else:
                order, weight = order_of[rank.get(route, unranked)[0]], rank.get(route, unranked)[1]
            planned.append((route, order, weight))
        for route, order, weight in planned:
            specs.append(deployment(group_name(name, routes[route]["modality"], primary), name, route, routes[route],
                                    order, weight, by_route[route], budget=budgets.get(route), cooldown=cooldowns.get(route)))
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
    # A group of one deployment has nothing to fall back to: it must cool down on a 402 or 429
    # itself, which LiteLLM only does with an explicit allowed-fails entry.
    counts = {}
    for spec in specs:
        counts[spec["name"]] = counts.get(spec["name"], 0) + 1
    for spec in specs:
        if counts[spec["name"]] == 1 and "cooldown_time" in spec["litellm_params"]:
            spec["model_info"]["allowed_fails_policy"] = {"RateLimitErrorAllowedFails": 0}
    # Rules may add a per-route policy: Anthropic answers an exhausted spend limit with a 400, which
    # LiteLLM only cools down on with an explicit entry (a malformed request counts too, hence the
    # allowance and the short cooldown the rules give it).
    for spec in specs:
        extra = (rules.get("policies") or {}).get(spec["route"])
        if extra:
            spec["model_info"]["allowed_fails_policy"] = {**spec["model_info"].get("allowed_fails_policy", {}), **extra}
    return sorted(specs, key=lambda spec: (spec["name"], spec["order"], spec["route"]))


def render_models(deployments):
    """The text of config/models.generated.yaml: a header, then a JSON document (valid YAML)."""
    document = {
        "model_list": [
            {"model_name": spec["name"], "litellm_params": spec["litellm_params"], "model_info": spec["model_info"]}
            for spec in deployments
        ]
    }
    return f"{MODELS_HEADER}\n{MODELS_NOTE}\n{json.dumps(document, indent=2)}\n"


def record_groups(deployments):
    """The planned groups as the catalogue records them: name -> modality, public, deployments.

    The overview renders the groups from the catalogue, so the plan is recorded beside the probes
    that justify it: one entry per group, its deployments in the order the router tries them.
    """
    groups = {}
    for spec in deployments:
        entry = groups.setdefault(spec["name"], {
            "modality": spec["modality"],
            "public": spec["model_info"]["access_groups"] == [PUBLIC_ACCESS_GROUP],
            "deployments": [],
        })
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


def due(previous, now, max_age):
    """Why a model needs a probe: new, errored, or probed longer ago than max_age. None if fresh."""
    meta = (previous or {}).get("metadata") or {}
    if not previous or meta.get("status") in (None, "error", "unprobed"):
        return 0 if not previous else 1
    try:
        probed = datetime.datetime.fromisoformat(meta["probed_at"])
    except (KeyError, ValueError):
        return 1
    return 2 if now - probed >= max_age else None


def stale_provider(old, route, key_env):
    """A route whose list could not be read keeps its last entries, marked stale, as a copy."""
    return {
        "metadata": dict(old.get("metadata") or {}, route=route, key_env=key_env, stale=True),
        "models": [dict(model, metadata=dict(model.get("metadata") or {})) for model in old.get("models") or []],
    }


def build(env, http, previous, now, max_age=datetime.timedelta(hours=24), route_limits=None, max_probes=250, rules=None):
    """The catalogue, and counts. `previous` is the last published catalogue (the cache)."""
    limits = dict(DEFAULT_ROUTE_LIMITS if route_limits is None else route_limits)
    cache = previous_by_id(previous)
    by_route = {entry["route"]: entry for entry in ROUTES}
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    providers, stats = {}, {"listed": 0, "probed": 0, "route_errors": []}
    queue = []
    for entry in ROUTES:
        route = entry["route"]
        if not (env.get(entry["key_env"]) or ""):
            # A route whose key is not written yet is skipped silently: the sync must run before the
            # owner adds it. Its last entries stay, stale, until a run can list it again.
            old = (previous.get("providers") or {}).get(route)
            if old and old.get("models"):
                providers[route] = stale_provider(old, route, entry["key_env"])
            else:
                providers[route] = {"metadata": {"route": route, "key_env": entry["key_env"]}, "models": []}
            continue
        try:
            listed = list_models(entry, env, http)
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
            if item.get("canonical_id"):
                meta["canonical_id"] = item["canonical_id"]
            if old:
                for k in ("status", "reason", "probed_at", "failing_probes", "was_usable"):
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
            elif route == "openrouter" and not item["free"]:
                meta.update(status="unprobed", reason="paid OpenRouter models are not probed")
                meta.pop("probed_at", None)
            else:
                why = due(old, now, max_age)
                if why is not None:
                    queue.append((why, route, model))
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
    spent = {}
    for _, route, model in sorted(queue, key=lambda q: (q[0], q[2]["metadata"].get("probed_at", ""))):
        if stats["probed"] >= max_probes or spent.get(route, 0) >= limits.get(route, max_probes):
            continue
        status, reason = probe(by_route[route], model["id"].split("/", 1)[1], env, http)
        meta = model["metadata"]
        meta.update(status=status, reason=reason, probed_at=stamp)
        # Two consecutive blocked or unknown probes are what drops a model.
        meta["failing_probes"] = (meta.get("failing_probes", 0) + 1) if status in ("blocked", "unknown") else 0
        spent[route] = spent.get(route, 0) + 1
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
    return catalogue, stats


def comparable(catalogue):
    """The catalogue without its run time: what a publish compares."""
    return {k: v for k, v in catalogue.items() if k != "updated_at"}


def make_http(timeout=60):
    def http(method, url, headers, body):
        data = None if body is None else json.dumps(body).encode()
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


def models_body(current, text):
    """The pull request body: what the run adds and removes, and nothing else."""
    before, after = deployment_ids(current), deployment_ids(text)
    return (
        "The catalogue sync regenerated `config/models.generated.yaml` from the probed catalogue.\n\n"
        f"- deployments added: {len(after - before)}\n"
        f"- deployments removed: {len(before - after)}\n"
    )


def publish_models(repo, token, text, http, env=None):
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
                return False
        except RuntimeError:
            pass
    github_reset_branch(repo, branch, github_ref(repo, "main", token, http), token, http)
    github_put_text(repo, path, token, text, sha, http, MODELS_MESSAGE, branch=branch)
    number = github_open_pr(repo, branch, token, http)
    if number is None:
        number = github_create_pr(repo, branch, token, http, MODELS_TITLE, models_body(current, text))
    github_label(repo, number, MODELS_LABELS, token, http)
    return True


def without_stamp(text):
    """A rendered page without its run time: what a publish compares."""
    return "\n".join(line for line in (text or "").splitlines() if not line.startswith("Last updated"))


def main(argv=None, env=None, http=None, now=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--output", default="model-catalog.json", help="where the catalogue is written when it is not published")
    ap.add_argument("--max-age", default="24h")
    ap.add_argument("--max-probes", type=int, default=250)
    args = ap.parse_args(argv)
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
    catalogue, stats = build(env, http, previous, now, parse_age(args.max_age), None, args.max_probes, rules)
    print(f"catalog: listed {stats['listed']} models, probed {stats['probed']} (16 tokens max each; bound: at most {args.max_probes} a run)")
    for line in stats["route_errors"]:
        print(f"catalog: {line}", file=sys.stderr)
    # The deployments the probes justify, as plain LiteLLM config in git, and the groups the
    # overview renders: both come from the same plan, so the page and the file cannot disagree.
    deployments = plan_groups(catalogue, rules)
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
            if publish_models(models_repo, models_token, text, http, env):
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
