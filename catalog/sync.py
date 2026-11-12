#!/usr/bin/env python3
"""Sync the live model catalogue: what each route's provider offers, and what the account can use.

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
cannot be probed through it, and the catalogue is what decides which models the proxy registers.

The result is cached in the catalogue itself (probed_at per model), so a run probes only models
that are new, errored or older than --max-age. A model that disappears from its provider's list
is kept for one run (metadata.absent_runs) and dropped on the second, so a provider that hiccups
does not empty the catalogue. metadata.failing_probes counts consecutive blocked or unknown
probes; the registration step removes a model after two of them.

Registration: reconcile() makes the proxy's database models match the catalogue through the admin
API. It adds a usable model the proxy does not have, removes a model the catalogue lost or that
failed twice, and touches only the models it created itself (model_info.managed_by). Published:
ids, classification, reason text, free flag, context length and whether the proxy has the model
registered. Never a key, a prompt or a response body.

Canonical groups: on top of those per-route explicit models, the sync registers one LiteLLM
model_name per canonical model (mistral-large, devstral, gemini-2.5-pro, claude-sonnet, glm-5.3)
whose deployments are the routes that serve it, ordered by catalog/routing.json. Deployments that
share a model_name form a group in LiteLLM: `order` is tried lowest first and the router falls back
to the next level when a level fails or is cooled down, `weight` splits traffic inside a level, and
rpm/tpm and max_budget/budget_duration cap one deployment. Routing is by which provider serves the
canonical model, not by model family: the Vibe plan serves non-Mistral models too, and where it
serves one it wins. Only exact canonical-name equality merges two upstream models; a near duplicate
is reported in catalogue.possible_duplicates and never merged.

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
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request

# route, the env names its provider base and key come from (config/config.yaml names the same),
# and how its list is read. The paid Vibe routes and the Google and Anthropic routes have no
# wildcard in the config: the catalogue registers their models, and a route whose key is not
# written yet is skipped until it is.
ROUTES = [
    {"route": "vibe", "base_env": "MISTRAL_API_BASE", "key_env": "VIBE_API_KEY", "kind": "mistral"},
    {"route": "vibe-study", "base_env": "MISTRAL_API_BASE", "key_env": "VIBE_STUDY_API_KEY", "kind": "mistral"},
    {"route": "vibe-api", "base_env": "MISTRAL_API_BASE", "key_env": "VIBE_API_KEY_PAID", "kind": "mistral"},
    {"route": "vibe-study-api", "base_env": "MISTRAL_API_BASE", "key_env": "VIBE_STUDY_API_KEY_PAID", "kind": "mistral"},
    {"route": "opencode-go", "base_env": "OPENCODE_GO_API_BASE", "key_env": "OPENCODE_GO_API_KEY", "kind": "openai"},
    {"route": "openrouter", "base_env": "OPENROUTER_API_BASE", "key_env": "OPENROUTER_API_KEY", "kind": "openrouter"},
    {"route": "google", "base_env": "GOOGLE_API_BASE", "key_env": "GOOGLE_API_KEY", "kind": "google"},
    {"route": "anthropic", "base_env": "ANTHROPIC_API_BASE", "key_env": "ANTHROPIC_API_KEY", "kind": "anthropic"},
    {"route": "local", "base_env": "LOCAL_LLM_API_BASE", "key_env": "LOCAL_LLM_API_KEY", "kind": "openai"},
]
# OpenCode Go models the docs say need an organisation setting (a privacy opt-in or a region):
# flagged in the catalogue as a hint; the probe still decides what the account can use.
OPT_IN_HINTS = ("deepseek-v4", "longcat-2.5", "muse-spark", "beam-free")
# OpenRouter's free tier allows few requests a day, so only this many free models are probed a run.
DEFAULT_ROUTE_LIMITS = {"openrouter": 30}
STATUSES = ("usable", "blocked", "unknown", "budget-exhausted", "error", "not-chat", "unprobed", "pending")
# A probe asks for the smallest answer that still proves the account may use the model.
PROBE_MAX_TOKENS = 16
# Some providers refuse a max_tokens below their own floor and name it: "Expected a value >= 32".
PROBE_MINIMUM = re.compile(r">= (\d+)")
# The retry honours the provider's floor, but never a number that would make a probe expensive.
PROBE_RETRY_CEILING = 256
# The models the proxy stores for this catalogue carry this marker; nothing else is ever touched.
MANAGED_BY = "litellm-catalog"
# The LiteLLM provider prefix per route kind: what the proxy hands the upstream id to.
PREFIXES = {"mistral": "mistral", "openai": "openai", "openrouter": "openrouter", "google": "gemini", "anthropic": "anthropic"}
# Kinds whose provider LiteLLM addresses itself: their deployments carry no api_base.
NO_API_BASE = ("google", "anthropic")
# Anthropic's list and messages endpoints take a version header; the value is the API's, not a secret.
ANTHROPIC_VERSION = "2023-06-01"
# A non-chat modality and the LiteLLM model_info.mode that marks it. Moderation is never registered.
MODES = {"embedding": "embedding", "ocr": "ocr", "transcription": "audio_transcription"}
# The routing rules: catalog/routing.json beside this script, or the file CATALOG_RULES names.
RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "routing.json")

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

    A missing file is no rules, so the sync runs before the rules are written; a malformed one
    raises, because a rule that cannot be read must not silently route traffic.
    """
    path = path or os.environ.get("CATALOG_RULES") or RULES_PATH
    try:
        with open(path) as handle:
            rules = json.load(handle)
    except FileNotFoundError:
        return {}
    return rules if isinstance(rules, dict) else {}


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
    """What a listed model is for. A chat model is probed; the rest is registered only where the
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

    Not through the proxy: a model the proxy has not registered yet has no route to go through.
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


def canonical_name(route, upstream_id, rules):
    """The canonical model an upstream id serves: one name per model, whichever route serves it.

    Deterministic and conservative: only a vendor prefix, a `:free` suffix, `-latest` and a version
    stamp are stripped, and an alias wins over both the raw and the normalised id. Two ids that
    still differ are two models; nothing here guesses that they are one.
    """
    aliases = (rules or {}).get("aliases") or {}
    if upstream_id in aliases:
        return aliases[upstream_id]
    name = upstream_id.lower().removeprefix("models/")
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
            name = canonical_name(route, str(model["id"]).split("/", 1)[1], rules)
            groups.setdefault(loose_key(name), []).append((model["id"], name, route))
    out = []
    for found in groups.values():
        if len({name for _, name, _ in found}) > 1 and len({route for _, _, route in found}) > 1:
            out.append(sorted(model_id for model_id, _, _ in found))
    return sorted(out)


def registerable(meta, modality, route, api_only):
    """Whether a listed model becomes a deployment: usable, or a non-chat model on an api-only route.

    A non-chat model is never probed, so its list entry is the only evidence there is; the rules
    name the routes whose paid key serves those modalities, and moderation is never registered.
    """
    if meta.get("absent_runs"):
        return False
    if meta.get("status") == "usable":
        return True
    return meta.get("status") == "not-chat" and route in api_only and modality in MODES


def litellm_params(entry, upstream_id):
    """The provider params for one upstream model: env names, never the secret values."""
    params = {"model": f"{PREFIXES[entry['kind']]}/{upstream_id}"}
    if entry["kind"] not in NO_API_BASE:
        params["api_base"] = f"os.environ/{entry['base_env']}"
    params["api_key"] = f"os.environ/{entry['key_env']}"
    return params


def spec_hash(params, order, weight, limits, budgets):
    """A hash of everything a rule can change, so a changed rule replaces the deployment."""
    source = {"litellm_params": params, "order": order, "weight": weight, "limits": limits or {}, "budgets": budgets or {}}
    return hashlib.sha1(json.dumps(source, sort_keys=True).encode()).hexdigest()


def deployment(name, route, chosen, order, weight, entry, budgets, limits):
    """One deployment of a canonical model: the add body, its order and weight, and its spec hash."""
    upstream_id = chosen["upstream_id"]
    params = litellm_params(entry, upstream_id)
    params.update({"order": order, "weight": weight})
    params.update(limits.get(route) or {})
    params.update(budgets.get(route) or {})
    info = {
        "id": "catalog-c-" + hashlib.sha1(f"{name}|{route}|{upstream_id}".encode()).hexdigest()[:12],
        "managed_by": MANAGED_BY,
        "route": route,
        "canonical": name,
        "modality": chosen["modality"],
        "access_groups": ["canonical"],
        "free": chosen["free"],
        "spec": spec_hash(params, order, weight, limits.get(route), budgets.get(route)),
    }
    if chosen["modality"] in MODES:
        info["mode"] = MODES[chosen["modality"]]
    return {
        "name": name,
        "route": route,
        "upstream_id": upstream_id,
        "order": order,
        "weight": weight,
        "litellm_params": params,
        "model_info": info,
    }


def family(name):
    """The model family a canonical name belongs to: its first token, a grouping hint only."""
    return name.split("-")[0]


def plan_groups(catalogue, rules):
    """The canonical model groups: one model_name per canonical model, its deployments ordered.

    Routing is by which provider serves the canonical model, not by model family: the Vibe plan
    serves non-Mistral models too, and where it serves one it wins. Only exact canonical-name
    equality merges two upstream models; a near duplicate is reported, never merged.
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
    limits = rules.get("limits") or {}
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
            if not registerable(meta, kind, route, api_only):
                continue
            upstream_id = str(model["id"]).split("/", 1)[1]
            name = canonical_name(route, upstream_id, rules)
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
        free = {route for route in serving if routes[route]["free"]} if not only else set()
        # The tiers that apply to this model, renumbered from 1: a model only tier 4 serves gets 1.
        levels = sorted({rank.get(route, unranked)[0] for route in serving - free})
        # Free deployments come first unless a plan route serves the model, then after the last tier.
        free_before = bool(free) and not (serving & free_first)
        order_of = {level: index + 1 + (1 if free_before else 0) for index, level in enumerate(levels)}
        free_order = 1 if free_before else len(levels) + 1
        for route in sorted(serving):
            if route in free:
                order, weight = free_order, 1
            else:
                order, weight = order_of[rank.get(route, unranked)[0]], rank.get(route, unranked)[1]
            specs.append(deployment(name, route, routes[route], order, weight, by_route[route], budgets, limits))
    return specs


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
                providers[route] = dict(old, metadata=dict(old.get("metadata", {}), stale=True))
            else:
                providers[route] = {"metadata": {"route": route, "key_env": entry["key_env"]}, "models": []}
            continue
        try:
            listed = list_models(entry, env, http)
        except Exception as error:  # a route that cannot be listed keeps its last entries
            stats["route_errors"].append(f"{route}: {error}"[:200])
            old = (previous.get("providers") or {}).get(route)
            if old:
                providers[route] = dict(old, metadata=dict(old.get("metadata", {}), stale=True))
            continue
        if not listed and (previous.get("providers") or {}).get(route, {}).get("models"):
            stats["route_errors"].append(f"{route}: the provider listed no model; the last entries are kept")
            providers[route] = dict(previous["providers"][route], metadata=dict(previous["providers"][route].get("metadata", {}), stale=True))
            continue
        models = []
        for item in sorted(listed, key=lambda m: m["id"]):
            full = f"{route}/{item['id']}"
            old = cache.get(full)
            meta = {"name": item["name"], "context_length": item["context_length"], "free": item["free"], "modality": item["modality"]}
            if old:
                for k in ("status", "reason", "probed_at", "failing_probes"):
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
        # Two consecutive blocked or unknown probes are what the registration step removes on.
        meta["failing_probes"] = (meta.get("failing_probes", 0) + 1) if status in ("blocked", "unknown") else 0
        spent[route] = spent.get(route, 0) + 1
        stats["probed"] += 1
    for provider in providers.values():
        counts = {}
        for model in provider["models"]:
            status = model["metadata"].get("status", "pending")
            model["metadata"].setdefault("status", status)
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


def admin_url(env):
    """The proxy's admin API base: LITELLM_URL, or the host and port composed."""
    return env.get("LITELLM_URL") or (
        f"http://{env['LITELLM_HOST']}:{env.get('LITELLM_PORT', '4000')}" if env.get("LITELLM_HOST") else ""
    )


def admin_headers(master):
    """Both key headers: the proxy reads whichever one it is configured for."""
    return {
        "Authorization": f"Bearer {master}",
        "x-litellm-api-key": f"Bearer {master}",
        "Content-Type": "application/json",
    }


def add_body(entry, upstream_id, free):
    """The /model/new body for one catalogue model. Env names, never the secret values."""
    full = f"{entry['route']}/{upstream_id}"
    return {
        "model_name": full,
        "litellm_params": litellm_params(entry, upstream_id),
        "model_info": {
            "id": f"catalog-{entry['route']}-{hashlib.sha1(full.encode()).hexdigest()[:12]}",
            "managed_by": MANAGED_BY,
            "route": entry["route"],
            "access_groups": [entry["route"]],
            "free": bool(free),
        },
    }


def canonical_body(spec):
    """The /model/new body for one canonical deployment: the group name, its params and its info."""
    return {"model_name": spec["name"], "litellm_params": spec["litellm_params"], "model_info": spec["model_info"]}


def reconcile(catalogue, env, http, max_adds=100, max_removals=25, rules=None):
    """Make the proxy's database models match the catalogue, through the admin API.

    Only the models this catalogue created (model_info.managed_by) are ever touched: the
    config-defined routes and hand-made models are not ours to change. A run that cannot read
    the current set changes nothing, because an add could duplicate and a removal could hit a
    model that is not ours.

    The per-route explicit models come first, then the canonical groups. A deployment whose
    model_info.spec no longer matches the rules is replaced (delete, then add), because order,
    weight, limits and budgets belong to the deployment, not to the group.
    """
    result = {"added": 0, "removed": 0, "replaced": 0, "deferred": 0, "errors": [], "skipped_reason": ""}
    url, master = admin_url(env), env.get("LITELLM_MASTER_KEY") or ""
    if not (url and master):
        result["skipped_reason"] = "no admin URL or master key"
        return result
    headers = admin_headers(master)
    status, body = http("GET", f"{url}/model/info", headers, None)
    entries = body.get("data") if isinstance(body, dict) else None
    if status != 200 or not isinstance(entries, list):
        result["skipped_reason"] = f"model/info answered {status}"
        return result
    managed, grouped = {}, {}
    for item in entries:
        info = (item.get("model_info") or {}) if isinstance(item, dict) else {}
        if info.get("managed_by") != MANAGED_BY or not info.get("db_model"):
            continue
        if info.get("canonical"):
            # A canonical deployment shares its model_name with its siblings: key it by its own id.
            grouped[info.get("id") or item.get("model_name")] = dict(info, model_name=item.get("model_name"))
        else:
            managed[item.get("model_name")] = info
    providers = catalogue.get("providers") or {}
    stale = {route for route, provider in providers.items() if (provider.get("metadata") or {}).get("stale")}
    wanted = {}
    for route, provider in sorted(providers.items()):
        if route in stale:
            continue
        for model in provider.get("models", []):
            wanted[model["id"]] = model
    by_route = {entry["route"]: entry for entry in ROUTES}
    adds_all = [
        model
        for name, model in sorted(wanted.items())
        if (model.get("metadata") or {}).get("status") == "usable"
        and "absent_runs" not in (model.get("metadata") or {})
        and name not in managed
    ]
    adds = adds_all[:max_adds]
    if len(adds_all) > max_adds:
        result["deferred"] = len(adds_all) - max_adds  # registered by the next runs
    added = set()
    for model in adds:
        route, upstream_id = model["id"].split("/", 1)
        entry = by_route.get(route)
        if not entry:
            continue
        status, answer = http("POST", f"{url}/model/new", headers, add_body(entry, upstream_id, (model.get("metadata") or {}).get("free")))
        text = answer if isinstance(answer, str) else json.dumps(answer)
        if 200 <= status < 300:
            added.add(model["id"])
            result["added"] += 1
        elif re.search(r"already|duplicate|unique", text, re.I):
            added.add(model["id"])  # registered by an earlier run: ours, and current
        else:
            result["errors"].append(f"add {model['id']}: {SECRETISH.sub('[redacted]', text)[:200]}")
    # A model is removed when the catalogue lost it, or when two probes in a row failed. A model
    # that is merely erroring, out of budget, pending or unprobed is left alone: that flaps.
    keep = {"error", "budget-exhausted", "pending", "unprobed"}
    removals = []
    for name, info in sorted(managed.items()):
        route = info.get("route") or name.split("/", 1)[0]
        if route in stale or route not in providers:
            continue  # the provider list failed (or has no entry): its models are not evidence of anything
        if name not in wanted:
            removals.append((name, info))
            continue
        meta = wanted[name].get("metadata") or {}
        # A route where nothing is usable is a route-level fault (a changed path, a revoked key),
        # not evidence that each of its models is gone.
        route_alive = any((m.get("metadata") or {}).get("status") == "usable" for m in providers[route].get("models", []))
        if meta.get("failing_probes", 0) >= 2 and meta.get("status") not in keep and route_alive:
            removals.append((name, info))
    if len(removals) > max_removals:
        result["errors"].append(f"removal guard: {len(removals)} removals exceed {max_removals}; run once with --max-removals {len(removals)} to confirm")
        removals = []
    removed = set()
    for name, info in removals:
        if not info.get("id"):
            result["errors"].append(f"remove {name}: the proxy reported no model id")
            continue
        status, answer = http("POST", f"{url}/model/delete", headers, {"id": info["id"]})
        if 200 <= status < 300:
            removed.add(name)
            result["removed"] += 1
        else:
            text = answer if isinstance(answer, str) else json.dumps(answer)
            result["errors"].append(f"remove {name}: {SECRETISH.sub('[redacted]', text)[:200]}")
    final = (set(managed) | added) - removed
    for route, provider in sorted(providers.items()):
        if route in stale:
            continue  # its last published flags stand: this run knows nothing new about it
        count = 0
        for model in provider.get("models", []):
            model["metadata"]["registered"] = model["id"] in final
            count += 1 if model["metadata"]["registered"] else 0
        provider["metadata"]["registered_count"] = count
    # Canonical groups: one model_name per canonical model, its deployments ordered by the rules.
    planned = plan_groups(catalogue, rules)
    alive = {route: any((m.get("metadata") or {}).get("status") == "usable" for m in provider.get("models", []))
             for route, provider in providers.items()}
    wanted_canonical = {}
    for spec in planned:
        route = spec["route"]
        if route in stale or route not in providers:
            continue  # a route whose list failed is not evidence for or against its deployments
        meta = (wanted.get(f"{route}/{spec['upstream_id']}") or {}).get("metadata") or {}
        # The deployment goes when its underlying explicit model failed twice, exactly as that model does.
        if meta.get("failing_probes", 0) >= 2 and meta.get("status") not in keep and alive.get(route):
            continue
        wanted_canonical[spec["model_info"]["id"]] = spec
    present = set(grouped)
    adds_left = max(0, max_adds - result["added"])
    for model_id, spec in sorted(wanted_canonical.items(), key=lambda item: (item[1]["name"], item[1]["order"], item[1]["route"])):
        current = grouped.get(model_id)
        if current is not None and current.get("spec") == spec["model_info"]["spec"]:
            continue
        if current is not None:
            if not current.get("id"):
                result["errors"].append(f"replace {spec['name']}: the proxy reported no model id")
                continue
            status, answer = http("POST", f"{url}/model/delete", headers, {"id": current["id"]})
            if not 200 <= status < 300:
                text = answer if isinstance(answer, str) else json.dumps(answer)
                result["errors"].append(f"replace {spec['name']}: {SECRETISH.sub('[redacted]', text)[:200]}")
                continue
            present.discard(model_id)
        elif adds_left <= 0:
            result["deferred"] += 1
            continue
        status, answer = http("POST", f"{url}/model/new", headers, canonical_body(spec))
        text = answer if isinstance(answer, str) else json.dumps(answer)
        if 200 <= status < 300:
            present.add(model_id)
            if current is not None:
                result["replaced"] += 1
            else:
                result["added"] += 1
                adds_left -= 1
        elif re.search(r"already|duplicate|unique", text, re.I):
            present.add(model_id)  # registered by an earlier run: ours, and current
        else:
            result["errors"].append(f"add {spec['name']}: {SECRETISH.sub('[redacted]', text)[:200]}")
    # A deployment is removed when the plan no longer has it, under the same route and guard rules.
    removals = []
    for model_id, info in sorted(grouped.items()):
        route = info.get("route") or ""
        if model_id in wanted_canonical or route in stale or route not in providers:
            continue
        removals.append((model_id, info))
    if len(removals) > max_removals:
        result["errors"].append(f"removal guard: {len(removals)} removals exceed {max_removals}; run once with --max-removals {len(removals)} to confirm")
        removals = []
    for model_id, info in removals:
        if not info.get("id"):
            result["errors"].append(f"remove {info.get('model_name')}: the proxy reported no model id")
            continue
        status, answer = http("POST", f"{url}/model/delete", headers, {"id": info["id"]})
        if 200 <= status < 300:
            present.discard(model_id)
            result["removed"] += 1
        else:
            text = answer if isinstance(answer, str) else json.dumps(answer)
            result["errors"].append(f"remove {info.get('model_name')}: {SECRETISH.sub('[redacted]', text)[:200]}")
    record = {}
    for spec in planned:
        entry = record.setdefault(spec["name"], {"family": family(spec["name"]), "modality": spec["model_info"]["modality"], "deployments": []})
        entry["deployments"].append({
            "route": spec["route"],
            "order": spec["order"],
            "weight": spec["weight"],
            "registered": spec["model_info"]["id"] in present,
        })
    for entry in record.values():
        entry["deployments"].sort(key=lambda item: (item["order"], item["route"]))
    catalogue["canonical"] = {name: record[name] for name in sorted(record)}
    return result


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


def github_get_text(repo, path, token, http):
    """A published file's text and sha; None, None when it does not exist yet."""
    status, body = http("GET", f"https://api.github.com/repos/{repo}/contents/{path}", {"Authorization": f"Bearer {token}"}, None)
    if status == 404:
        return None, None
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"reading {repo}/{path} answered {status}")
    return base64.b64decode(body["content"]).decode(), body["sha"]


def github_get(repo, path, token, http):
    text, sha = github_get_text(repo, path, token, http)
    return (json.loads(text) if text is not None else None), sha


def github_put_text(repo, path, token, text, sha, http, message):
    content = base64.b64encode(text.encode()).decode()
    body = {"message": message, "content": content}
    if sha:
        body["sha"] = sha
    status, answer = http("PUT", f"https://api.github.com/repos/{repo}/contents/{path}", {"Authorization": f"Bearer {token}"}, body)
    if status not in (200, 201):
        raise RuntimeError(f"publishing to {repo}/{path} answered {status}")


def github_put(repo, path, token, catalogue, sha, http):
    github_put_text(repo, path, token, json.dumps(catalogue, indent=2) + "\n", sha, http, "chore: refresh model catalogue")


def without_stamp(text):
    """A rendered page without its run time: what a publish compares."""
    return "\n".join(line for line in (text or "").splitlines() if not line.startswith("Last updated"))


def main(argv=None, env=None, http=None, now=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--output", default="model-catalog.json", help="where the catalogue is written when it is not published")
    ap.add_argument("--max-age", default="24h")
    ap.add_argument("--max-probes", type=int, default=250)
    ap.add_argument("--max-removals", type=int, default=25, help="the most models one run may remove; a larger removal is refused until confirmed with a higher value")
    args = ap.parse_args(argv)
    env = os.environ if env is None else env
    http = http or make_http()
    now = now or datetime.datetime.now(datetime.timezone.utc)
    repo, path, token = env.get("CATALOG_REPO"), env.get("CATALOG_PATH", "model-catalog.json"), env.get("CATALOG_TOKEN")
    publish = bool(repo and token)
    sha = None
    if publish:
        previous, sha = github_get(repo, path, token, http)
    elif os.path.exists(args.output):
        previous = json.load(open(args.output))
    else:
        previous = None
    previous = previous or {}
    rules = load_rules()
    catalogue, stats = build(env, http, previous, now, parse_age(args.max_age), None, args.max_probes, rules)
    print(f"catalog: listed {stats['listed']} models, probed {stats['probed']} (16 tokens max each; bound: at most {args.max_probes} a run)")
    for line in stats["route_errors"]:
        print(f"catalog: {line}", file=sys.stderr)
    # A run that listed nothing at all is a failed run, so the scheduled Process shows red.
    failed = stats["listed"] == 0
    if admin_url(env) and env.get("LITELLM_MASTER_KEY"):
        result = reconcile(catalogue, env, http, max_removals=args.max_removals, rules=rules)
        print(f"catalog: registered +{result['added']} -{result['removed']} ~{result['replaced']} (errors {len(result['errors'])}, deferred {result['deferred']})")
        for line in result["errors"]:
            print(f"catalog: {line}", file=sys.stderr)
        if result["skipped_reason"]:
            print(f"catalog: registration skipped: {result['skipped_reason']}", file=sys.stderr)
        failed = failed or bool(result["errors"]) or bool(result["skipped_reason"])
    if publish:
        if previous and comparable(previous) == comparable(catalogue):
            print("catalog: unchanged, not published")
        else:
            github_put(repo, path, token, catalogue, sha, http)
            print(f"catalog: published to {repo}/{path}")
    else:
        with open(args.output, "w") as out:
            json.dump(catalogue, out, indent=2)
            out.write("\n")
        print(f"catalog: wrote {args.output}")
    # The overview module is a sibling (the script runs as /app/catalog/sync.py) and imports this
    # module for its status order, so it is imported here, once this module is complete.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from overview import render

    overview = render(catalogue)
    readme = env.get("CATALOG_README_PATH", "README.md")
    if publish:
        remote, readme_sha = github_get_text(repo, readme, token, http)
        if without_stamp(remote) == without_stamp(overview):
            print(f"catalog: {readme} unchanged, not published")
        else:
            github_put_text(repo, readme, token, overview, readme_sha, http, "chore: refresh model overview")
            print(f"catalog: published the overview to {repo}/{readme}")
    else:
        local = os.path.join(os.path.dirname(args.output), "model-overview.md")  # never the working directory's README.md
        with open(local, "w") as out:
            out.write(overview)
        print(f"catalog: wrote {local}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
