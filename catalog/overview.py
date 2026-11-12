"""Render the catalogue as the Markdown overview that sits beside model-catalog.json.

The catalogue is JSON for a picker; a person deciding whether a route's wildcard can go needs
the same facts as a page: what the route lists, what the account can use, what is registered in
LiteLLM, and why the rest is not. So this renders it, deterministically: one catalogue gives one
page, and only the `Last updated` line moves between runs, so a publish compares text and commits
only a real change.

The canonical groups come first: one LiteLLM model_name per canonical model, its deployments in
the order the router tries them, and the ids that may be one model under two names so the owner
can add an alias. The per-route sections follow, because a wildcard is removed per route.

Published: ids, status, reason text, free flag, context length, credit and timestamps. Never a
key, a prompt or a response body.

Standard library only: it runs in the LiteLLM image beside the sync, and in CI.
"""
# The sync's statuses in its own order, so every counts line reads the same way. A status the
# sync adds later still renders, after these, alphabetically.
ORDER = ("usable", "blocked", "unknown", "budget-exhausted", "error", "not-chat", "unprobed", "pending")

HOW = (
    "The sync lists every provider's models hourly (minute 17) and probes a model with one tiny "
    "request when it is new or its last probe is older than 24 hours. A usable model is registered "
    "in LiteLLM as `<route>/<provider model id>`, grouped per route, and removed once it has been "
    "missing from its provider's list on two consecutive runs or blocked or unknown on two "
    "consecutive probes. Blocked and other unavailable models are listed here, not registered. "
    "The catalogue itself is `model-catalog.json`, beside this file."
)
FOOTER = "Generated from `model-catalog.json` by `catalog/sync.py`. Edit nothing in this file by hand."
# The routing policy in one paragraph: the tiers of catalog/routing.json, in the order the router
# tries them. Prose, not a rendering of the rules: the rules are data the sync applies.
ROUTING = (
    "Whichever provider serves a model first wins. The local server answers first when it is "
    "reachable, then Google for Gemini and the Anthropic key for Claude. The Vibe plan (vibe 2:1 "
    "vibe-study) serves every model it offers, then the paid Mistral API keys (vibe-api 2:1 "
    "vibe-study-api). OpenCode Go serves only a model none of those serve, and stands behind Google "
    "as a backup. Free models are permitted everywhere."
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


def registered(provider):
    count = (provider.get("metadata") or {}).get("registered_count")
    return count if isinstance(count, int) and not isinstance(count, bool) else 0


def ordered(tally):
    """Statuses in the sync's order, then any the sync does not know, alphabetically."""
    return [s for s in ORDER if tally.get(s)] + sorted(s for s in tally if s not in ORDER and tally[s])


def table(header, rows):
    """A GitHub-flavoured Markdown table; every cell is escaped by its caller."""
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    return lines + ["| " + " | ".join(row) + " |" for row in rows]


def summary(providers):
    """One row per route: what it lists, what is usable, what LiteLLM holds, and the rest."""
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
        rows.append([label, str(count), str(usable), str(registered(provider)), str(blocked), str(max(0, count - usable - blocked))])
    return table(["Route", "Listed", "Usable", "Registered", "Blocked", "Other"], rows)


def credit(value):
    """A remaining credit as a person reads it: two decimals, or `n/a` when the route has none."""
    if value is None or isinstance(value, bool):
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"


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
        f"{order}: " + ", ".join(f"{cell(route)} x{weight}" for route, weight in routes)
        for order, routes in grouped(entry)
    )


def remaining(entry):
    """What is left of each deployment route's credit, from the catalogue's own credit map."""
    credit_map = (entry.get("metadata") or {}).get("credit")
    if not isinstance(credit_map, dict):
        return "n/a"
    routes = []
    for _, level in grouped(entry):
        for route, _ in level:
            if route not in routes:
                routes.append(route)
    if not routes:
        return "n/a"
    return ", ".join(f"{cell(route)} {credit(credit_map.get(route))}" for route in routes)


def canonical_row(name, entry):
    """One canonical model: what it is, where it is served in order, and what is left to spend."""
    entry = entry or {}
    order = deployments(entry)
    if entry.get("registered") is False:
        order = f"{order} (not registered)".strip()
    return [cell(name), cell(entry.get("family") or ""), cell(entry.get("modality") or ""), order, remaining(entry)]


def canonical_section(catalogue):
    """The canonical groups, then what each route has left when the catalogue knows it."""
    canonical = catalogue.get("canonical") or {}
    lines = [
        "## Canonical models",
        "",
        "One row per canonical model: the deployments that serve it, in the order the router tries "
        "them (lower first, `xN` is the weight inside a level), and what is left of each route's credit.",
        "",
    ]
    if not canonical:
        lines.append("The catalogue holds no canonical models.")
    else:
        lines += table(
            ["Canonical", "Family", "Modality", "Deployments in order", "Remaining credit"],
            [canonical_row(name, canonical[name]) for name in sorted(canonical)],
        )
    credit_map = catalogue.get("credit")
    if isinstance(credit_map, dict) and credit_map:
        lines += ["", "Remaining credit per route:", ""]
        lines += table(["Route", "Remaining"], [[cell(route), credit(credit_map[route])] for route in sorted(credit_map)])
    return lines


def duplicates_section(catalogue):
    """The ids that may be one model under two names: the owner adds an alias, nothing is guessed."""
    groups = [sorted(str(i) for i in group) for group in (catalogue.get("possible_duplicates") or []) if group]
    lines = ["## Possible duplicates (not merged)", ""]
    if not groups:
        lines.append("None: no two routes list ids that look like the same model.")
        return lines
    lines += [
        "These ids may name one model on different providers. Add an alias in `catalog/routing.json` "
        "to merge one; nothing is merged on a guess.",
        "",
    ]
    lines += [f"- {', '.join(cell(i) for i in group)}" for group in sorted(groups)]
    return lines


def section(route, provider):
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
            ["Model", "Free", "Context", "Last probed", "In LiteLLM"],
            [
                [
                    cell(m.get("id")),
                    "free" if (m.get("metadata") or {}).get("free") else "",
                    context((m.get("metadata") or {}).get("context_length")),
                    stamp((m.get("metadata") or {}).get("probed_at")),
                    "yes" if (m.get("metadata") or {}).get("registered") else "no",
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
    lines += canonical_section(catalogue)
    lines += ["", "## Routing rules", "", ROUTING]
    lines += [""] + duplicates_section(catalogue)
    lines += ["", "## Summary", ""]
    lines += summary(providers)
    if not providers:
        lines += ["", "The catalogue lists no routes."]
    for route, provider in providers.items():
        lines += [""] + section(route, provider)
    lines += ["", "---", "", FOOTER, ""]
    return "\n".join(lines)
