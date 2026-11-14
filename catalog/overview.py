"""Render the catalogue as the Markdown overview that sits beside model-catalog.json.

The catalogue is JSON for a picker; a person deciding whether a route's wildcard can go needs
the same facts as a page: what the route lists, what the account can use, which canonical group
each model is deployed under, and why the rest is not. So this renders it, deterministically: one
catalogue gives one page, and only the `Last updated` line moves between runs, so a publish
compares text and commits only a real change.

The canonical groups come first: one LiteLLM model_name per canonical model, its deployments in
the order the router tries them, and the ids that may be one model under two names so the owner
can add an alias. The per-route sections follow, because a wildcard is removed per route.

Published: ids, status, reason text, free flag, context length, group and timestamps. Never a
key, a prompt or a response body.

Standard library only: it runs in the LiteLLM image beside the sync, and in CI.
"""
# The sync's statuses in its own order, so every counts line reads the same way. A status the
# sync adds later still renders, after these, alphabetically.
ORDER = ("usable", "blocked", "unknown", "budget-exhausted", "error", "not-chat", "unprobed", "pending")
# The routes whose deployments are a free tier that may train on the prompts, and the provider
# each one is. A group holding one is a public group, and its row says so.
PUBLIC_ROUTES = {"google": "Google", "openrouter": "OpenRouter", "opencode-free": "OpenCode Zen"}


def public_group(provider):
    return f"free tier, {provider} may train on prompts, public work only"


def public_row(provider):
    return f"free tier — {provider} may train on prompts"

HOW = (
    "The sync lists every provider's models hourly (minute 17) and probes a model with one tiny "
    "request when it is new or its last probe is older than 24 hours. The routing itself is "
    "generated configuration: the sync writes `config/models.generated.yaml` in the litellm "
    "repository and opens a pull request, so a deployment reaches the proxy only when that pull "
    "request is merged and the image is redeployed. A usable model is deployed as "
    "`<route>/<provider model id>` under its canonical group, and dropped once it has been missing "
    "from its provider's list on two consecutive runs or blocked or unknown on two consecutive "
    "probes. Blocked and other unavailable models are listed here, not deployed. The catalogue "
    "itself is `model-catalog.json`, beside this file."
)
FOOTER = "Generated from `model-catalog.json` by `catalog/sync.py`. Edit nothing in this file by hand."
# The routing policy in one paragraph: the tiers of catalog/routing.json, in the order the router
# tries them. Prose, not a rendering of the rules: the rules are data the sync applies.
ROUTING = (
    "Whichever provider serves a model first wins. The local server answers first when it is "
    "reachable, then the Anthropic key for Claude. The Vibe plan (vibe 2:1 vibe-study) serves every "
    "model it offers, then the general Mistral API keys (vibe-api 2:1 vibe-study-api). OpenCode Go "
    "serves only a model none of those serve. Google's free tier is never in a normal group: Gemini "
    "is served from a `public/<model>` group that only keys with the `google-free-public` access "
    "group see, because Google may train on those prompts. The free OpenRouter and OpenCode Zen "
    "models are public work only too: each is served from its own `<route>/<model>` group, alone, "
    "that only keys with that route's access group (`openrouter-free-public`, "
    "`opencode-free-public`) see, and no free model is in a normal group. Claude is only ever on the "
    "Anthropic key."
)


def cell(text):
    """One table cell: a pipe would split the row and a newline would end it."""
    return " ".join(str(text).split()).replace("|", "\\|")


def reason_cell(text):
    """A reason is provider text: angle brackets would open HTML inside the details block."""
    return cell(text).replace("<", "&lt;").replace(">", "&gt;")


def context(value):
    """A context window as a person reads it: 128000 -> 128k, 8192 stays exact."""
    if value is None or isinstance(value, bool):
        return ""
    try:
        size = int(value)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    for unit, scale in (("m", 1000000), ("k", 1000)):
        if size >= scale and size % scale == 0:
            return f"{size // scale}{unit}"
    return str(size)


def stamp(value):
    """A probe time as the catalogue gives it, or `never` when the model was not probed."""
    return cell(value) if value else "never"


def status_of(model):
    return (model.get("metadata") or {}).get("status") or "pending"


def by_status(provider):
    """The route's counts: the sync's own tally, or one made from the models when it is absent."""
    tally = (provider.get("metadata") or {}).get("by_status")
    if isinstance(tally, dict):
        return {status: count for status, count in tally.items() if count}
    out = {}
    for model in provider.get("models") or []:
        out[status_of(model)] = out.get(status_of(model), 0) + 1
    return out


def listed(provider):
    count = (provider.get("metadata") or {}).get("count")
    return count if isinstance(count, int) and not isinstance(count, bool) else len(provider.get("models") or [])


def ordered(tally):
    """Statuses in the sync's order, then any the sync does not know, alphabetically."""
    return [s for s in ORDER if tally.get(s)] + sorted(s for s in tally if s not in ORDER and tally[s])


def table(header, rows):
    """A GitHub-flavoured Markdown table; every cell is escaped by its caller."""
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    return lines + ["| " + " | ".join(row) + " |" for row in rows]


def summary(providers):
    """One row per route: what it lists, what is usable, what is blocked, and the rest."""
    rows = []
    for route, provider in providers.items():
        tally = by_status(provider)
        count = listed(provider)
        usable, blocked = tally.get("usable", 0), tally.get("blocked", 0)
        label = cell(route)
        if (provider.get("metadata") or {}).get("stale"):
            label += " (provider list unavailable, last known)"
        if not count:
            label += " (no models listed)"
        rows.append([label, str(count), str(usable), str(blocked), str(max(0, count - usable - blocked))])
    return table(["Route", "Listed", "Usable", "Blocked", "Other"], rows)


def groups(catalogue):
    """The canonical groups the sync planned: name -> {modality, public, deployments}."""
    found = catalogue.get("groups")
    return found if isinstance(found, dict) else {}


def group_index(catalogue):
    """(route, upstream id) -> the canonical group it is deployed under, for the per-route tables."""
    index = {}
    for name, entry in groups(catalogue).items():
        for deployment in (entry or {}).get("deployments") or []:
            if not isinstance(deployment, dict):
                continue
            route, upstream = deployment.get("route"), deployment.get("upstream_id")
            if route and upstream:
                index[(str(route), str(upstream))] = name
    return index


def group_of(index, model_id):
    """The group a catalogue model id (`<route>/<upstream id>`) is deployed under, or empty."""
    route, _, upstream = str(model_id).partition("/")
    return index.get((route, upstream), "")


def grouped(entry):
    """A canonical model's deployments per order level: [(order, [(route, weight), ...]), ...].

    LiteLLM tries the lowest order first and falls back to the next level, so the order is the
    routing decision and the weight only splits traffic inside one level. Two deployments of one
    route in one level add up, because the router sees one weight per deployment.
    """
    levels = {}
    for deployment in entry.get("deployments") or []:
        if not isinstance(deployment, dict):
            continue
        route = str(deployment.get("route") or "")
        if not route:
            continue
        order = deployment.get("order")
        order = order if isinstance(order, int) and not isinstance(order, bool) else 0
        weight = deployment.get("weight")
        weight = weight if isinstance(weight, int) and not isinstance(weight, bool) and weight > 0 else 1
        levels.setdefault(order, {})
        levels[order][route] = levels[order].get(route, 0) + weight
    return [(order, sorted(levels[order].items())) for order in sorted(levels)]


def deployments(entry):
    """`1: vibe x2, vibe-study x1 > 2: vibe-api x2`: the group in the order the router tries it."""
    return " > ".join(
        f"{order}: " + ", ".join(
            f"{cell(route)} x{weight}" + (f" ({public_row(PUBLIC_ROUTES[route])})" if route in PUBLIC_ROUTES else "")
            for route, weight in routes
        )
        for order, routes in grouped(entry)
    )


def public_providers(entry):
    """The providers in a group that may train on the prompts, in route order."""
    routes = {route for _, level in grouped(entry or {}) for route, _ in level}
    return [PUBLIC_ROUTES[route] for route in sorted(routes) if route in PUBLIC_ROUTES]


def is_public(entry):
    """A group is public when it holds a deployment on a route that may train on the prompts."""
    if (entry or {}).get("public"):
        return True
    return bool(public_providers(entry))


def group_row(name, entry):
    """One canonical group: what it is, where it is served in order, and whether it is public."""
    entry = entry or {}
    note = ""
    if is_public(entry):
        note = public_group(" and ".join(public_providers(entry)) or "the provider")
    return [cell(name), cell(entry.get("modality") or ""), deployments(entry), note]


def groups_section(catalogue):
    """The canonical groups, in name order, with the deployments the router tries in order."""
    found = groups(catalogue)
    lines = [
        "## Canonical models",
        "",
        "One row per canonical group: the deployments that serve it, in the order the router tries "
        "them (lower first, `xN` is the weight inside a level). A `public/<model>` group is the "
        "Google free tier plus the same deployments the normal group has, and only keys with the "
        "`google-free-public` access group see it. An `openrouter/<model>` or `opencode-free/<model>` "
        "group is that route's free model alone, and only keys with `openrouter-free-public` or "
        "`opencode-free-public` see it.",
        "",
    ]
    if not found:
        lines.append("The catalogue holds no canonical models.")
    else:
        lines += table(
            ["Group", "Modality", "Deployments in order", "Notes"],
            [group_row(name, found[name]) for name in sorted(found)],
        )
    return lines


def duplicates_section(catalogue):
    """The ids that may be one model under two names: the owner adds an alias, nothing is guessed."""
    groups_found = [sorted(str(i) for i in group) for group in (catalogue.get("possible_duplicates") or []) if group]
    lines = ["## Possible duplicates (not merged)", ""]
    if not groups_found:
        lines.append("None: no two routes list ids that look like the same model.")
        return lines
    lines += [
        "These ids may name one model on different providers. Add an alias in `catalog/routing.json` "
        "to merge one; nothing is merged on a guess.",
        "",
    ]
    lines += [f"- {', '.join(cell(i) for i in group)}" for group in sorted(groups_found)]
    return lines


def section(route, provider, index):
    """One route: its counts, the usable models, and the rest folded away with their reasons."""
    tally = by_status(provider)
    models = provider.get("models") or []
    parts = [f"{tally[status]} {status}" for status in ordered(tally)]
    lines = [f"## {route}", "", f"{listed(provider)} listed" + (": " + ", ".join(parts) if parts else "")]
    usable = sorted((m for m in models if status_of(m) == "usable"), key=lambda m: str(m.get("id")))
    unavailable = sorted((m for m in models if status_of(m) != "usable"), key=lambda m: (status_of(m), str(m.get("id"))))
    lines.append("")
    if not models:
        lines.append("No models listed for this route.")
    elif not usable:
        lines.append("No usable models on this route.")
    else:
        lines += table(
            ["Model", "Free", "Context", "Last probed", "Group"],
            [
                [
                    cell(m.get("id")),
                    "free" if (m.get("metadata") or {}).get("free") else "",
                    context((m.get("metadata") or {}).get("context_length")),
                    stamp((m.get("metadata") or {}).get("probed_at")),
                    cell(group_of(index, m.get("id"))),
                ]
                for m in usable
            ],
        )
    if unavailable:
        lines += ["", f"<details><summary>Unavailable ({len(unavailable)})</summary>", ""]
        lines += table(
            ["Model", "Status", "Reason", "Last probed"],
            [
                [
                    cell(m.get("id")),
                    cell(status_of(m)),
                    reason_cell(str((m.get("metadata") or {}).get("reason") or "")[:120]),
                    stamp((m.get("metadata") or {}).get("probed_at")),
                ]
                for m in unavailable
            ],
        )
        lines += ["", "</details>"]
    return lines


def render(catalogue):
    """The overview page for one catalogue. Only the `Last updated` line depends on the run."""
    catalogue = catalogue or {}
    providers = catalogue.get("providers") or {}
    index = group_index(catalogue)
    lines = [
        "# LiteLLM model overview",
        "",
        f"Last updated: {cell(catalogue.get('updated_at') or 'unknown')}",
        "",
        "## How this stays current",
        "",
        HOW,
        "",
    ]
    lines += groups_section(catalogue)
    lines += ["", "## Routing rules", "", ROUTING]
    lines += [""] + duplicates_section(catalogue)
    lines += ["", "## Summary", ""]
    lines += summary(providers)
    if not providers:
        lines += ["", "The catalogue lists no routes."]
    for route, provider in providers.items():
        lines += [""] + section(route, provider, index)
    lines += ["", "---", "", FOOTER, ""]
    return "\n".join(lines)
