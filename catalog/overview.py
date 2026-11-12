"""Render the catalogue as the Markdown overview that sits beside model-catalog.json.

The catalogue is JSON for a picker; a person deciding whether a route's wildcard can go needs
the same facts as a page: what the route lists, what the account can use, what is registered in
LiteLLM, and why the rest is not. So this renders it, deterministically: one catalogue gives one
page, and only the `Last updated` line moves between runs, so a publish compares text and commits
only a real change.

Published: ids, status, reason text, free flag, context length and timestamps. Never a key, a
prompt or a response body.

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
        "## Summary",
        "",
    ]
    lines += summary(providers)
    if not providers:
        lines += ["", "The catalogue lists no routes."]
    for route, provider in providers.items():
        lines += [""] + section(route, provider)
    lines += ["", "---", "", FOOTER, ""]
    return "\n".join(lines)
