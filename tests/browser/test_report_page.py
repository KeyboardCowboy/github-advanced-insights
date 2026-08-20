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

    def test_filtering_away_the_open_report_switches_to_one_in_scope(self):
        """Otherwise the chart shows a report the sidebar no longer lists.

        It is also what lets a filter survive: the remembered report is then
        always one the remembered filter can show, so the two do not contradict
        each other on the next load.
        """
        self.page.click('.report-link[data-slug="overflow"]')
        expect(self.page.locator(
            '.report-link[data-slug="overflow"][aria-current="true"]')).to_have_count(1)

        self.page.select_option("#scope-project", "beta-board")
        expect(self.page.locator(".report-link")).to_have_count(1)
        expect(self.page.locator(
            '.report-link[data-slug="beta-report"][aria-current="true"]')).to_have_count(1)

    def test_selecting_all_restores_every_report(self):
        self.page.select_option("#scope-project", "beta-board")
        expect(self.page.locator(".report-link")).to_have_count(1)
        self.page.select_option("#scope-project", "")
        expect(self.page.locator(".report-link")).to_have_count(7)
        self.assertIn("beta-report", self.visible_slugs())


class RememberedScope(BrowserTest):
    """The filters have to survive a round trip to Settings and back (#23).

    Same two accounts and boards as ScopeFilters: without a real choice the
    controls do not render and there is nothing to remember.
    """

    PREBUILD_REPORTS = ("baseline",)

    prepare_workspace = ScopeFilters.__dict__["prepare_workspace"]

    def visible_slugs(self):
        return self.page.locator(".report-link").evaluate_all(
            "els => els.map(e => e.dataset.slug)")

    def test_a_filter_survives_leaving_the_page_and_coming_back(self):
        self.goto("/")
        self.page.select_option("#scope-project", "beta-board")
        expect(self.page.locator(".report-link")).to_have_count(1)
        # Filtering moves the selection into the filter, so the stored report
        # and the stored filter agree rather than contradicting on return.
        expect(self.page.locator(
            '.report-link[data-slug="beta-report"][aria-current="true"]')
        ).to_have_count(1)

        # The actual journey from the report: Settings, then back.
        self.goto("/settings")
        self.goto("/")

        expect(self.page.locator("#scope-project")).to_have_value("beta-board")
        self.assertEqual(self.visible_slugs(), ["beta-report"])

    def test_selecting_all_is_remembered_too(self):
        # Clearing a filter is as deliberate as setting one, so it has to stick
        # rather than being read as "nothing saved yet".
        self.goto("/")
        self.page.select_option("#scope-project", "beta-board")
        expect(self.page.locator(".report-link")).to_have_count(1)
        self.page.select_option("#scope-project", "")
        expect(self.page.locator(".report-link")).to_have_count(7)

        self.goto("/")
        expect(self.page.locator("#scope-project")).to_have_value("")
        self.assertIn("beta-report", self.visible_slugs())

    def test_a_filter_naming_something_that_no_longer_exists_falls_back_to_all(self):
        """A board can be deleted in Settings between visits.

        Restoring the filter blindly would match nothing and show an empty
        sidebar with no explanation -- the reader would have a working tool that
        looks broken.
        """
        self.goto("/")
        self.page.evaluate(
            "localStorage.setItem('ticket-aging:scope',"
            " JSON.stringify({account: null, project: 'board-that-was-deleted'}))")
        self.goto("/")

        expect(self.page.locator("#scope-project")).to_have_value("")
        self.assertIn("beta-report", self.visible_slugs())
        self.assertNoPageErrors()

    def test_a_corrupt_stored_filter_is_ignored_rather_than_thrown_on(self):
        self.goto("/")
        self.page.evaluate(
            "localStorage.setItem('ticket-aging:scope', 'not json at all')")
        self.goto("/")

        expect(self.page.locator(".report-link")).not_to_have_count(0)
        self.assertNoPageErrors()

    def test_a_remembered_filter_that_would_hide_the_remembered_report_is_cleared(self):
        """Both are remembered, and they can contradict each other.

        The report wins: it is what the reader came back for, and a narrowing
        set on an earlier visit is easy to have forgotten. Losing the report
        instead would look like it had been deleted.
        """
        self.goto("/")
        self.page.evaluate(
            "localStorage.setItem('ticket-aging:last-report', 'overflow')")
        self.page.evaluate(
            "localStorage.setItem('ticket-aging:scope',"
            " JSON.stringify({account: null, project: 'beta-board'}))")
        self.goto("/")

        # 'overflow' lives on acme-board, so the beta-board filter would hide it.
        expect(self.page.locator(
            '.report-link[data-slug="overflow"][aria-current="true"]')).to_have_count(1)
        expect(self.page.locator("#scope-project")).to_have_value("")

    def test_clearing_that_conflict_is_persisted_not_just_applied(self):
        # Otherwise the same contradiction is resolved again on every load, and
        # the stored filter stays permanently at odds with what is on screen.
        self.goto("/")
        self.page.evaluate(
            "localStorage.setItem('ticket-aging:last-report', 'overflow')")
        self.page.evaluate(
            "localStorage.setItem('ticket-aging:scope',"
            " JSON.stringify({account: null, project: 'beta-board'}))")
        self.goto("/")
        # Wait for boot to settle before reading storage: goto returns on load,
        # and boot resolves the conflict asynchronously after that.
        expect(self.page.locator("#scope-project")).to_have_value("")

        stored = self.page.evaluate(
            "JSON.parse(localStorage.getItem('ticket-aging:scope'))")
        self.assertIsNone(stored["project"], stored)


if __name__ == "__main__":
    unittest.main()
