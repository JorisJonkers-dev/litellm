#!/usr/bin/env python3
"""Sync the live model catalogue: what each route's provider offers, and what the account can use.

A provider's model list is not what an account can use (a Go model hosted outside the region
the organisation allows is listed and refused). So the sync lists each route's models from
its provider, then PROBES each one through the LiteLLM proxy with one minimal request
(max_tokens 1), so the route's own upstream key is the one tested, and classifies the result:

  usable            the request was answered
  blocked           the account is refused (region, privacy setting, disabled); the reason text is kept
  unknown           the model is gone or never existed on this provider
  budget-exhausted  credit or quota is spent
  error             anything else (rate limit, 5xx, timeout); tried again next run
  not-chat          the provider lists it as no chat model (embeddings, OCR, transcription)
  unprobed          not probed on purpose (a paid OpenRouter model: the route is used for `:free` ids)
  pending           due for a probe but over this run's cap; probed in a later run

The result is cached in the catalogue itself (probed_at per model), so a run probes only models
that are new, errored or older than --max-age, and a model that disappears from its provider's
list disappears from the catalogue. Published: ids, classification, reason text, free flag and
context length. Never a key, a prompt or a response body.

Probe cost bound: at most (models probed) x (about 8 input tokens + 1 output token) per run,
the models of each route probed once per --max-age (default 24h); free and local models cost
nothing. Every run prints the number probed so the bound can be checked.

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
# and how its list is read.
ROUTES = [
    {"route": "vibe", "base_env": "MISTRAL_API_BASE", "key_env": "VIBE_API_KEY", "kind": "mistral"},
    {"route": "vibe-study", "base_env": "MISTRAL_API_BASE", "key_env": "VIBE_STUDY_API_KEY", "kind": "mistral"},
    {"route": "opencode-go", "base_env": "OPENCODE_GO_API_BASE", "key_env": "OPENCODE_GO_API_KEY", "kind": "openai"},
    {"route": "openrouter", "base_env": "OPENROUTER_API_BASE", "key_env": "OPENROUTER_API_KEY", "kind": "openrouter"},
    {"route": "local", "base_env": "LOCAL_LLM_API_BASE", "key_env": "LOCAL_LLM_API_KEY", "kind": "openai"},
]
# OpenCode Go models the docs say need an organisation setting (a privacy opt-in or a region):
# flagged in the catalogue as a hint; the probe still decides what the account can use.
OPT_IN_HINTS = ("deepseek-v4", "longcat-2.5", "muse-spark", "beam-free")
# OpenRouter's free tier allows few requests a day, so only this many free models are probed a run.
DEFAULT_ROUTE_LIMITS = {"openrouter": 30}
STATUSES = ("usable", "blocked", "unknown", "budget-exhausted", "error", "not-chat", "unprobed", "pending")

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


def list_models(entry, env, http):
    """The models the provider lists for this route: id, name, context length, free, chat."""
    base = (env.get(entry["base_env"]) or "").rstrip("/")
    if not base:
        raise RuntimeError(f"{entry['base_env']} is not set")
    key = env.get(entry["key_env"]) or ""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    status, body = http("GET", f"{base}/models", headers, None)
    if entry["kind"] == "openrouter" and key:
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
        out = {"id": item["id"], "name": item.get("name") or item["id"], "context_length": None, "free": False, "chat": None}
        if entry["kind"] == "mistral":
            caps = item.get("capabilities") or {}
            out["chat"] = bool(caps.get("completion_chat")) if "completion_chat" in caps else None
            out["context_length"] = item.get("max_context_length")
        elif entry["kind"] == "openrouter":
            out["context_length"] = item.get("context_length")
            out["free"] = item["id"].endswith(":free") or is_free_price(item.get("pricing"))
        elif entry["route"] == "local":
            out["free"] = True
        found[item["id"]] = out
    return list(found.values())


def probe(model, litellm_url, master_key, http):
    """One minimal request through the proxy; the route's wildcard picks the upstream key."""
    body = {"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}
    if model.startswith("opencode-go/"):
        # OpenCode answers 400 MissingSessionID without a session header.
        body["extra_headers"] = {"x-opencode-session": "litellm-catalog-probe"}
    headers = {"x-litellm-api-key": f"Bearer {master_key}", "Content-Type": "application/json"}
    status, answer = http("POST", f"{litellm_url.rstrip('/')}/v1/chat/completions", headers, body)
    return classify(status, answer if isinstance(answer, str) else json.dumps(answer))


def parse_age(text):
    match = re.fullmatch(r"(\d+)([hd])", text)
    if not match:
        raise ValueError("--max-age is a number of hours or days, like 24h or 2d")
    return datetime.timedelta(**{"hours" if match[2] == "h" else "days": int(match[1])})


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


def build(env, http, previous, now, max_age=datetime.timedelta(hours=24), route_limits=None, max_probes=250):
    """The catalogue, and counts. `previous` is the last published catalogue (the cache)."""
    limits = dict(DEFAULT_ROUTE_LIMITS if route_limits is None else route_limits)
    cache = previous_by_id(previous)
    master = env.get("LITELLM_MASTER_KEY") or ""
    url = env.get("LITELLM_URL") or (
        f"http://{env['LITELLM_HOST']}:{env.get('LITELLM_PORT', '4000')}" if env.get("LITELLM_HOST") else ""
    )
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    providers, stats = {}, {"listed": 0, "probed": 0, "route_errors": []}
    queue = []
    for entry in ROUTES:
        route = entry["route"]
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
            meta = {"name": item["name"], "context_length": item["context_length"], "free": item["free"]}
            if old:
                for k in ("status", "reason", "probed_at"):
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
        providers[route] = {"metadata": {"route": route, "key_env": entry["key_env"]}, "models": models}
    spent = {}
    for _, route, model in sorted(queue, key=lambda q: (q[0], q[2]["metadata"].get("probed_at", ""))):
        if stats["probed"] >= max_probes or spent.get(route, 0) >= limits.get(route, max_probes):
            continue
        if not (master and url):
            break
        status, reason = probe(model["id"], url, master, http)
        model["metadata"].update(status=status, reason=reason, probed_at=stamp)
        spent[route] = spent.get(route, 0) + 1
        stats["probed"] += 1
    for provider in providers.values():
        counts = {}
        for model in provider["models"]:
            status = model["metadata"].get("status", "pending")
            model["metadata"].setdefault("status", status)
            counts[status] = counts.get(status, 0) + 1
        provider["metadata"].update(count=len(provider["models"]), by_status=counts)
    return {
        "version": 1,
        "updated_at": stamp,
        "metadata": {
            "generator": "JorisJonkers-dev litellm catalog/sync.py",
            "statuses": list(STATUSES),
            "comment": "Model ids are what a caller sends to LiteLLM (<route>/<provider model id>). "
            "status is what a probe through that route's key answered; reason is the provider's own text.",
        },
        "providers": providers,
    }, stats


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


def github_get(repo, path, token, http):
    status, body = http("GET", f"https://api.github.com/repos/{repo}/contents/{path}", {"Authorization": f"Bearer {token}"}, None)
    if status == 404:
        return None, None
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"reading {repo}/{path} answered {status}")
    return json.loads(base64.b64decode(body["content"])), body["sha"]


def github_put(repo, path, token, catalogue, sha, http):
    content = base64.b64encode((json.dumps(catalogue, indent=2) + "\n").encode()).decode()
    body = {"message": "chore: refresh model catalogue", "content": content}
    if sha:
        body["sha"] = sha
    status, answer = http("PUT", f"https://api.github.com/repos/{repo}/contents/{path}", {"Authorization": f"Bearer {token}"}, body)
    if status not in (200, 201):
        raise RuntimeError(f"publishing to {repo}/{path} answered {status}")


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
    sha = None
    if publish:
        previous, sha = github_get(repo, path, token, http)
    elif os.path.exists(args.output):
        previous = json.load(open(args.output))
    else:
        previous = None
    previous = previous or {}
    catalogue, stats = build(env, http, previous, now, parse_age(args.max_age), None, args.max_probes)
    print(f"catalog: listed {stats['listed']} models, probed {stats['probed']} (1 token each; bound: at most {args.max_probes} a run)")
    for line in stats["route_errors"]:
        print(f"catalog: {line}", file=sys.stderr)
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
    # A run that listed nothing at all is a failed run, so the scheduled Process shows red.
    return 1 if stats["listed"] == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
