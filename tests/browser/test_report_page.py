"""The report page: switching reports and the sidebar scope filters.

This page is the one users spend their time on, and almost all of it is built by
JavaScript from a view model — the served HTML is a shell. Nothing below the
template can be checked without running it.

The scope filters need a workspace bigger than the fixtures ship with: a filter
over a single value stays hidden, on the reasoning that a choice of one is not a
choice. So `prepare_workspace` builds the multi-account, multi-board situation
those controls exist for.
"""
import json
import unittest

from tests.browser.harness import BrowserTest, expect


class ReportSwitching(BrowserTest):
    PREBUILD_REPORTS = ("baseline", "overflow", "empty")

    def setUp(self):
        super().setUp()
        self.goto("/")

    def test_the_page_renders_without_throwing(self):
        self.assertNoPageErrors()
        self.assertEqual(self.page.locator("h1").first.text_content(),
                         "Work Item Age by Status")

    def test_the_sidebar_lists_every_report(self):
        slugs = self.page.locator(".report-link").evaluate_all(
            "els => els.map(e => e.dataset.slug)")
        self.assertEqual(sorted(slugs),
                         ["baseline", "batch-day", "empty", "multi-repo",
                          "no-entry-event", "overflow"])

    def test_selecting_a_report_draws_its_data(self):
        self.page.click('.report-link[data-slug="overflow"]')
        # `aria-current` flips when the sidebar redraws, which happens before
        # the view model arrives -- it means "selected", not "drawn", and
        # asserting straight after it reads the previous report's chart. The
        # polling assertion waits for the render instead.
        #
        # The overflow fixture exists to produce a trailing bucket, so "5+"
        # appearing in the SVG means this report was drawn and not the last one.
        expect(self.page.locator("#histogram")).to_contain_text("5+")

    def test_the_composed_filter_is_shown_to_the_reader(self):
        self.page.click('.report-link[data-slug="baseline"]')
        line = self.page.locator("#filter-line")
        line.wait_for(state="visible")
        self.assertIn('status:"In Progress"', line.text_content())

    def test_a_report_with_no_data_says_so_rather_than_failing(self):
        # 'batch-day' is deliberately not prebuilt, so it has never been fetched.
        self.page.click('.report-link[data-slug="batch-day"]')
        self.page.wait_for_function(
            "() => document.body.innerText.toLowerCase().includes('no data')"
            " || document.body.innerText.toLowerCase().includes('not fetched')")
        self.assertNoPageErrors()

    def test_an_empty_report_renders_zeros_rather_than_breaking(self):
        self.page.click('.report-link[data-slug="empty"]')
        self.page.locator(
            '.report-link[data-slug="empty"][aria-current="true"]').wait_for()
        self.assertNoPageErrors()


class ScopeFilters(BrowserTest):
    """Two accounts and two boards, which is the smallest setup that shows them."""

    PREBUILD_REPORTS = ("baseline", "overflow")

    @classmethod
    def prepare_workspace(cls):
        cls.write_workspace_json("accounts.json", {
            "accounts": [
                {"id": "work", "label": "Work", "gh_account": "octocat"},
                {"id": "personal", "label": "Personal", "gh_account": "hubot"},
            ],
            "default_account": "work",
        })
        cls.write_workspace_json("projects.json", {
            "projects": [
                {"id": "acme-board", "label": "Acme Delivery", "account": "work",
                 "owner": "acme", "owner_type": "organization", "project_number": 1},
                {"id": "beta-board", "label": "Beta Platform", "account": "personal",
                 "owner": "acme", "owner_type": "organization", "project_number": 7},
            ],
            "default_project": "acme-board",
        })
        # One report on the second board, so filtering has something to hide.
        definition = json.loads(
            (cls.workspace / "definitions" / "baseline.json").read_text())
        definition["project"] = "beta-board"
        definition["copy"] = {"title": "Beta Report"}
        (cls.workspace / "definitions" / "beta-report.json").write_text(
            json.dumps(definition))

    def setUp(self):
        super().setUp()
        self.goto("/")

    def visible_slugs(self):
        return self.page.locator(".report-link").evaluate_all(
            "els => els.map(e => e.dataset.slug)")

    def test_both_filters_appear_when_there_is_a_choice(self):
        self.assertNoPageErrors()
        self.assertTrue(self.page.locator("#scope-account").is_visible())
        self.assertTrue(self.page.locator("#scope-project").is_visible())

    def test_filtering_by_project_narrows_the_report_list(self):
        self.assertIn("beta-report", self.visible_slugs())
        self.page.select_option("#scope-project", "beta-board")
        expect(self.page.locator(".report-link")).to_have_count(1)
        self.assertEqual(self.visible_slugs(), ["beta-report"])

    def test_filtering_by_account_narrows_to_that_accounts_boards(self):
        self.page.select_option("#scope-account", "personal")
        expect(self.page.locator(".report-link")).to_have_count(1)
        self.assertEqual(self.visible_slugs(), ["beta-report"])

    def test_narrowing_the_account_clears_an_orphaned_project(self):
        """Otherwise the two filters can contradict each other and show nothing.

        Selecting a board, then an account that board does not belong to, would
        leave an impossible pair selected and an empty list with no clue why.
        """
        self.page.select_option("#scope-project", "beta-board")
        self.page.select_option("#scope-account", "work")

        # Work owns exactly one board, so the project filter disappears
        # entirely rather than falling back to "All": a choice of one is not a
        # choice. What matters is that the stale beta-board selection did not
        # survive to combine with it and show nothing.
        expect(self.page.locator("#scope-project")).to_have_count(0)
        expect(self.page.locator(".report-link")).not_to_have_count(0)
        self.assertNotIn("beta-report", self.visible_slugs())

    def test_selecting_all_restores_every_report(self):
        self.page.select_option("#scope-project", "beta-board")
        expect(self.page.locator(".report-link")).to_have_count(1)
        self.page.select_option("#scope-project", "")
        expect(self.page.locator(".report-link")).to_have_count(7)
        self.assertIn("beta-report", self.visible_slugs())


if __name__ == "__main__":
    unittest.main()
