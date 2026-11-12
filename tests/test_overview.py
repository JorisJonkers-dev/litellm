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
    "canonical": {
        "claude-sonnet-4-5": {
            "family": "anthropic", "modality": "chat", "registered": True,
            "deployments": [{"route": "anthropic", "order": 1, "weight": 1, "model": "anthropic/claude-sonnet-4-5"}],
            "metadata": {"credit": {"anthropic": 41.5}},
        },
        "glm-5.3": {
            "family": "z-ai", "modality": "chat", "registered": True,
            "deployments": [
                {"route": "vibe", "order": 1, "weight": 2, "model": "vibe/glm-5.3"},
                {"route": "vibe-study", "order": 1, "weight": 1, "model": "vibe-study/glm-5.3"},
                {"route": "vibe-api", "order": 2, "weight": 2, "model": "vibe-api/glm-5.3"},
                {"route": "vibe-study-api", "order": 2, "weight": 1, "model": "vibe-study-api/glm-5.3"},
            ],
            "metadata": {"credit": {"vibe": 255, "vibe-study": 128, "vibe-api": 50, "vibe-study-api": 25}},
        },
        "mistral-embed": {
            "family": "mistral", "modality": "embedding", "registered": False,
            "deployments": [{"route": "vibe", "order": 1, "weight": 1, "model": "vibe/mistral-embed"}],
        },
    },
    "credit": {"vibe": 255, "vibe-study": None},
    "possible_duplicates": [["opencode-go/glm-5.3", "openrouter/z-ai/glm-5.3"]],
}


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split(" | ")]


def table_rows(text, header):
    """The data rows of the table whose header line is exactly `header`."""
    lines = text.splitlines()
    start = lines.index(f"| {header} |")
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
        rows = {row[0]: row[1:] for row in table_rows(overview.render(CATALOGUE), "Route | Listed | Usable | Registered | Blocked | Other")}
        self.assertEqual(rows["vibe"], ["4", "2", "1", "0", "2"])
        self.assertEqual(rows["opencode-go"], ["5", "1", "1", "1", "3"])
        self.assertEqual(rows["openrouter"], ["2", "1", "1", "0", "1"])
        self.assertEqual(rows["local (no models listed)"], ["0", "0", "0", "0", "0"])
        self.assertIn("vibe-study (provider list unavailable, last known)", rows)

    def test_missing_metadata_counts_as_zero(self):
        bare = {"updated_at": "t", "providers": {"vibe": {"models": [{"id": "vibe/x", "metadata": {"status": "usable"}}]}}}
        row = table_rows(overview.render(bare), "Route | Listed | Usable | Registered | Blocked | Other")[0]
        self.assertEqual(row, ["vibe", "1", "1", "0", "0", "0"])

    def test_route_order_follows_the_catalogue(self):
        text = overview.render(CATALOGUE)
        fixed = ("## How this stays current", "## Canonical models", "## Routing rules",
                 "## Possible duplicates (not merged)", "## Summary")
        headings = [line[3:] for line in text.splitlines() if line.startswith("## ") and line not in fixed]
        self.assertEqual(headings, list(CATALOGUE["providers"]))

    def test_canonical_models_are_sorted_with_deployments_in_order(self):
        rows = table_rows(overview.render(CATALOGUE), "Canonical | Family | Modality | Deployments in order | Remaining credit")
        self.assertEqual(rows, [
            ["claude-sonnet-4-5", "anthropic", "chat", "1: anthropic x1", "anthropic 41.50"],
            ["glm-5.3", "z-ai", "chat", "1: vibe x2, vibe-study x1 > 2: vibe-api x2, vibe-study-api x1",
             "vibe 255.00, vibe-study 128.00, vibe-api 50.00, vibe-study-api 25.00"],
            ["mistral-embed", "mistral", "embedding", "1: vibe x1 (not registered)", "n/a"],
        ])

    def test_canonical_section_comes_before_the_route_sections(self):
        text = overview.render(CATALOGUE)
        self.assertLess(text.index("## Canonical models"), text.index("## Routing rules"))
        self.assertLess(text.index("## Routing rules"), text.index("## Possible duplicates (not merged)"))
        self.assertLess(text.index("## Possible duplicates (not merged)"), text.index("## Summary"))
        self.assertLess(text.index("## Summary"), text.index("## vibe\n"))

    def test_routing_rules_paragraph(self):
        text = overview.render(CATALOGUE)
        for phrase in ("Whichever provider serves a model first wins", "vibe 2:1 vibe-study",
                       "vibe-api 2:1 vibe-study-api", "Google for Gemini", "Anthropic key for Claude",
                       "OpenCode Go serves only a model none of those serve", "Free models are permitted"):
            self.assertIn(phrase, text)

    def test_credit_table_is_rendered_when_the_catalogue_has_one(self):
        self.assertEqual(table_rows(overview.render(CATALOGUE), "Route | Remaining"),
                         [["vibe", "255.00"], ["vibe-study", "n/a"]])
        self.assertIn("Remaining credit per route:", overview.render(CATALOGUE))

    def test_no_credit_table_without_credit(self):
        text = overview.render({k: v for k, v in CATALOGUE.items() if k != "credit"})
        self.assertNotIn("| Route | Remaining |", text)
        self.assertNotIn("Remaining credit per route:", text)

    def test_possible_duplicates_are_listed_for_the_owner(self):
        text = overview.render(CATALOGUE)
        self.assertIn("- opencode-go/glm-5.3, openrouter/z-ai/glm-5.3", text)
        self.assertIn("Add an alias in `catalog/routing.json`", text)

    def test_no_possible_duplicates_says_so(self):
        text = overview.render({k: v for k, v in CATALOGUE.items() if k != "possible_duplicates"})
        self.assertIn("None: no two routes list ids that look like the same model.", text)
        self.assertNotIn("Add an alias", text)

    def test_a_catalogue_without_canonical_models_says_so(self):
        text = overview.render({"updated_at": "t", "providers": {}})
        self.assertIn("## Canonical models", text)
        self.assertIn("The catalogue holds no canonical models.", text)
        self.assertIn("None: no two routes list ids that look like the same model.", text)

    def test_canonical_cells_are_escaped_and_credit_is_two_decimals(self):
        tricky = {"updated_at": "t", "providers": {}, "canonical": {"a|b": {
            "family": "f", "modality": "chat", "registered": True,
            "deployments": [{"route": "r|x", "order": 1, "weight": 1}],
            "metadata": {"credit": {"r|x": 1.5}}}}}
        self.assertIn("| a\\|b | f | chat | 1: r\\|x x1 | r\\|x 1.50 |", overview.render(tricky))
        self.assertEqual(overview.credit(None), "n/a")
        self.assertEqual(overview.credit(True), "n/a")
        self.assertEqual(overview.credit("garbage"), "n/a")
        self.assertEqual(overview.credit(12.34), "12.34")
        self.assertEqual(overview.credit(255), "255.00")

    def test_a_deployment_without_an_order_or_a_route_still_renders(self):
        self.assertEqual(overview.deployments({"deployments": [{"route": "vibe"}, "junk", {"order": 2}]}), "0: vibe x1")
        self.assertEqual(overview.deployments({}), "")
        self.assertEqual(overview.remaining({}), "n/a")
        self.assertEqual(overview.canonical_row("x", {"registered": False}), ["x", "", "", "(not registered)", "n/a"])

    def test_counts_line_omits_zeros(self):
        self.assertIn("4 listed: 2 usable, 1 unknown, 1 not-chat", section(overview.render(CATALOGUE), "vibe"))
        self.assertIn("5 listed: 1 usable, 1 blocked, 1 budget-exhausted, 1 error, 1 pending",
                      section(overview.render(CATALOGUE), "opencode-go"))

    def test_usable_table_is_sorted_and_shows_registration(self):
        rows = table_rows(overview.render(CATALOGUE), "Model | Free | Context | Last probed | In LiteLLM")
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
