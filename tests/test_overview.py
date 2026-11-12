"""The overview page: what the catalogue says, rendered deterministically."""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "catalog"))
import overview  # noqa: E402
import sync  # noqa: E402


def model(route, upstream, status, **meta):
    return {"id": f"{route}/{upstream}", "description": "free" if meta.get("free") else "",
            "metadata": {"name": f"Pretty {upstream}", "status": status, **meta}}


def provider(route, models, **meta):
    tally = {}
    for m in models:
        tally[m["metadata"]["status"]] = tally.get(m["metadata"]["status"], 0) + 1
    return {"metadata": {"route": route, "key_env": "SOME_ROUTE_KEY", "count": len(models), "by_status": tally, **meta},
            "models": models}


CATALOGUE = {
    "version": 1,
    "updated_at": "2026-11-10T12:00:00Z",
    "providers": {
        "vibe": provider("vibe", [
            model("vibe", "mistral-large", "usable", context_length=128000, probed_at="2026-11-10T12:00:00Z", registered=True),
            model("vibe", "mistral-small", "usable", context_length=8192, probed_at="2026-11-10T11:00:00Z"),
            model("vibe", "mistral-embed", "not-chat", probed_at="2026-11-10T12:00:00Z"),
            model("vibe", "mistral-gone", "unknown", reason="The model `x` does not exist", probed_at="2026-11-09T12:00:00Z"),
        ], registered_count=1),
        "opencode-go": provider("opencode-go", [
            model("opencode-go", "glm-5.3-flash", "usable", probed_at="2026-11-10T12:00:00Z", registered=True),
            model("opencode-go", "deepseek-v4-pro", "blocked", reason="This Go model is hosted outside Europe", probed_at="2026-11-10T12:00:00Z"),
            model("opencode-go", "spent", "budget-exhausted", reason="Budget has been exceeded"),
            model("opencode-go", "flaky", "error", reason="upstream 503"),
            model("opencode-go", "later", "pending"),
        ], registered_count=1),
        "openrouter": provider("openrouter", [
            model("openrouter", "a/b:free", "usable", free=True, context_length=200000, probed_at="2026-11-10T12:00:00Z", registered=True),
            model("openrouter", "c/d", "unprobed", reason="paid OpenRouter models are not probed"),
        ], registered_count=1),
        "local": provider("local", []),
        "vibe-study": provider("vibe-study", [
            model("vibe-study", "mistral-large", "usable", probed_at="2026-11-08T12:00:00Z"),
        ], stale=True),
    },
}


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split(" | ")]


def table_rows(text, header):
    """The data rows of the table whose header line starts with `header`."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"| {header} |"))
    rows = []
    for line in lines[start + 2:]:
        if not line.startswith("|"):
            break
        rows.append(cells(line))
    return rows


def section(text, route):
    """The lines of one route's section, up to the next heading."""
    lines = text.splitlines()
    start = lines.index(f"## {route}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return lines[start:end]


class Render(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(overview.render(CATALOGUE), overview.render(copy.deepcopy(CATALOGUE)))
        self.assertEqual(overview.render(CATALOGUE), overview.render(CATALOGUE))

    def test_only_the_last_updated_line_moves(self):
        later = dict(CATALOGUE, updated_at="2026-11-10T13:00:00Z")
        before, after = overview.render(CATALOGUE).splitlines(), overview.render(later).splitlines()
        self.assertEqual(len(before), len(after))
        changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(len(changed), 1)
        self.assertEqual(after[changed[0]], "Last updated: 2026-11-10T13:00:00Z")

    def test_header_and_footer(self):
        text = overview.render(CATALOGUE)
        self.assertTrue(text.startswith("# LiteLLM model overview\n\nLast updated: 2026-11-10T12:00:00Z\n"))
        self.assertIn("## How this stays current", text)
        self.assertIn("minute 17", text)
        self.assertIn("24 hours", text)
        self.assertIn("`<route>/<provider model id>`", text)
        self.assertIn("two consecutive runs", text)
        self.assertIn("two consecutive probes", text)
        self.assertIn("`model-catalog.json`", text)
        self.assertIn("Edit nothing in this file by hand.", text)
        self.assertTrue(text.endswith("\n"))

    def test_summary_counts(self):
        rows = {row[0]: row[1:] for row in table_rows(overview.render(CATALOGUE), "Route")}
        self.assertEqual(rows["vibe"], ["4", "2", "1", "0", "2"])
        self.assertEqual(rows["opencode-go"], ["5", "1", "1", "1", "3"])
        self.assertEqual(rows["openrouter"], ["2", "1", "1", "0", "1"])
        self.assertEqual(rows["local (no models listed)"], ["0", "0", "0", "0", "0"])
        self.assertIn("vibe-study (provider list unavailable, last known)", rows)

    def test_missing_metadata_counts_as_zero(self):
        bare = {"updated_at": "t", "providers": {"vibe": {"models": [{"id": "vibe/x", "metadata": {"status": "usable"}}]}}}
        row = table_rows(overview.render(bare), "Route")[0]
        self.assertEqual(row, ["vibe", "1", "1", "0", "0", "0"])

    def test_route_order_follows_the_catalogue(self):
        text = overview.render(CATALOGUE)
        headings = [line[3:] for line in text.splitlines() if line.startswith("## ") and line not in ("## How this stays current", "## Summary")]
        self.assertEqual(headings, list(CATALOGUE["providers"]))

    def test_counts_line_omits_zeros(self):
        self.assertIn("4 listed: 2 usable, 1 unknown, 1 not-chat", section(overview.render(CATALOGUE), "vibe"))
        self.assertIn("5 listed: 1 usable, 1 blocked, 1 budget-exhausted, 1 error, 1 pending",
                      section(overview.render(CATALOGUE), "opencode-go"))

    def test_usable_table_is_sorted_and_shows_registration(self):
        rows = table_rows(overview.render(CATALOGUE), "Model")
        self.assertEqual(rows, [
            ["vibe/mistral-large", "", "128k", "2026-11-10T12:00:00Z", "yes"],
            ["vibe/mistral-small", "", "8192", "2026-11-10T11:00:00Z", "no"],
        ])
        self.assertIn("| openrouter/a/b:free | free | 200k | 2026-11-10T12:00:00Z | yes |", overview.render(CATALOGUE))

    def test_unavailable_block_is_ordered_by_status_then_id(self):
        lines = section(overview.render(CATALOGUE), "opencode-go")
        self.assertIn("<details><summary>Unavailable (4)</summary>", lines)
        start = lines.index("<details><summary>Unavailable (4)</summary>")
        rows = [cells(line) for line in lines[start + 2:] if line.startswith("| ") and not line.startswith("| ---")][1:]
        self.assertEqual([row[0] for row in rows], [
            "opencode-go/deepseek-v4-pro", "opencode-go/spent", "opencode-go/flaky", "opencode-go/later",
        ])
        self.assertEqual([row[1] for row in rows], ["blocked", "budget-exhausted", "error", "pending"])
        self.assertEqual(rows[3][2], "")
        self.assertEqual(rows[3][3], "never")

    def test_reason_is_cut_to_120_characters(self):
        long = {"updated_at": "t", "providers": {"vibe": provider("vibe", [
            model("vibe", "x", "blocked", reason="r" * 300)])}}
        row = [line for line in overview.render(long).splitlines() if line.startswith("| vibe/x |")][0]
        self.assertEqual(len(cells(row)[2]), 120)

    def test_escaping_of_pipes_newlines_and_angle_brackets(self):
        tricky = {"updated_at": "t", "providers": {"vibe": provider("vibe", [
            model("vibe", "a|b", "usable", probed_at="t"),
            model("vibe", "c", "blocked", reason="line one\nline two | <b>bold</b>"),
        ])}}
        text = overview.render(tricky)
        self.assertIn("| vibe/a\\|b |", text)
        self.assertIn("line one line two \\| &lt;b&gt;bold&lt;/b&gt;", text)
        self.assertNotIn("<b>", text)
        usable_row = next(line for line in text.splitlines() if line.startswith("| vibe/a"))
        self.assertEqual(len(cells(usable_row)), 5)
        unavailable_row = next(line for line in text.splitlines() if line.startswith("| vibe/c"))
        self.assertEqual(len(cells(unavailable_row)), 4)

    def test_stale_and_empty_routes(self):
        text = overview.render(CATALOGUE)
        self.assertIn("No models listed for this route.", section(text, "local"))
        self.assertIn("0 listed", section(text, "local"))
        self.assertIn("vibe-study (provider list unavailable, last known)", text)
        self.assertIn("1 listed: 1 usable", section(text, "vibe-study"))

    def test_a_route_without_usable_models_says_so(self):
        blocked = {"updated_at": "t", "providers": {"vibe": provider("vibe", [
            model("vibe", "x", "blocked", reason="no")])}}
        lines = section(overview.render(blocked), "vibe")
        self.assertIn("No usable models on this route.", lines)
        self.assertNotIn("| Model | Free | Context | Last probed | In LiteLLM |", lines)
        self.assertIn("<details><summary>Unavailable (1)</summary>", lines)

    def test_empty_catalogue_does_not_crash(self):
        for empty in ({}, {"providers": {}}, {"updated_at": "t", "providers": {}}):
            text = overview.render(empty)
            self.assertIn("# LiteLLM model overview", text)
            self.assertIn("Last updated: ", text)
            self.assertIn("| Route | Listed | Usable | Registered | Blocked | Other |", text)
            self.assertIn("The catalogue lists no routes.", text)
            self.assertIn("Edit nothing in this file by hand.", text)
        self.assertIn("Last updated: unknown", overview.render({}))

    def test_status_order_matches_the_sync(self):
        self.assertEqual(overview.ORDER, sync.STATUSES)

    def test_context_formatting(self):
        self.assertEqual(overview.context(128000), "128k")
        self.assertEqual(overview.context(8192), "8192")
        self.assertEqual(overview.context(200000), "200k")
        self.assertEqual(overview.context(1000000), "1m")
        self.assertEqual(overview.context(0), "")
        self.assertEqual(overview.context(None), "")
        self.assertEqual(overview.context("garbage"), "")
        self.assertEqual(overview.context(True), "")

    def test_no_keys_and_no_names(self):
        text = overview.render(CATALOGUE)
        self.assertNotIn("SOME_ROUTE_KEY", text)
        self.assertNotIn("Pretty ", text)
        self.assertNotIn("key_env", text)


if __name__ == "__main__":
    unittest.main()
