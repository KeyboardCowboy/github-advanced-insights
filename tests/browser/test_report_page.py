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

    def test_the_board_dropdown_offers_no_all(self):
        """#41: pooling boards produces a list you cannot read.

        A report's title says which column it measures, never which board it
        came from, so two boards each with an "In Progress" report give the
        reader two identical-looking entries and no way to tell them apart.
        """
        options = self.page.locator("#scope-project option").all_text_contents()
        self.assertNotIn("All", options)
        self.assertEqual(sorted(options), ["Acme Delivery", "Beta Platform"])

    def test_the_account_dropdown_still_offers_all(self):
        # Accounts are named on every board, so pooling them stays readable.
        self.assertIn("All", self.page.locator("#scope-account option").all_text_contents())

    def test_a_board_is_selected_from_the_start(self):
        # There is no unscoped state to land in, so the opening view is the
        # board of the report being shown.
        expect(self.page.locator("#scope-project")).to_have_value("acme-board")
        self.assertNotIn("beta-report", self.visible_slugs())

    def test_switching_boards_switches_the_list(self):
        self.page.select_option("#scope-project", "beta-board")
        expect(self.page.locator(".report-link")).to_have_count(1)
        self.assertEqual(self.visible_slugs(), ["beta-report"])

        self.page.select_option("#scope-project", "acme-board")
        expect(self.page.locator(".report-link")).to_have_count(6)
        self.assertNotIn("beta-report", self.visible_slugs())

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

    def test_widening_the_account_to_all_offers_every_board(self):
        # "All" survives on Account, and widening it puts both boards back on
        # offer without pooling their reports into one list.
        self.page.select_option("#scope-account", "personal")
        expect(self.page.locator("#scope-project")).to_have_count(0)

        self.page.select_option("#scope-account", "")
        expect(self.page.locator("#scope-project option")).to_have_count(2)


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

    def test_switching_back_is_remembered_too(self):
        # Returning to the board you started on is as deliberate as leaving it,
        # so it has to stick rather than being read as "nothing saved yet".
        self.goto("/")
        self.page.select_option("#scope-project", "beta-board")
        expect(self.page.locator(".report-link")).to_have_count(1)
        self.page.select_option("#scope-project", "acme-board")
        expect(self.page.locator(".report-link")).to_have_count(6)

        self.goto("/")
        expect(self.page.locator("#scope-project")).to_have_value("acme-board")
        self.assertNotIn("beta-report", self.visible_slugs())

    def test_a_board_that_no_longer_exists_resolves_to_the_one_on_screen(self):
        """A board can be deleted in Settings between visits.

        Restoring it blindly would match nothing and show an empty sidebar with
        no explanation -- a working tool that looks broken. With no "All" to
        fall back to, it resolves to the board of the report being shown.
        """
        self.goto("/")
        self.page.evaluate(
            "localStorage.setItem('ticket-aging:scope',"
            " JSON.stringify({account: null, project: 'board-that-was-deleted'}))")
        self.goto("/")

        expect(self.page.locator("#scope-project")).to_have_value("acme-board")
        expect(self.page.locator(".report-link")).not_to_have_count(0)
        self.assertNoPageErrors()

    def test_a_corrupt_stored_filter_is_ignored_rather_than_thrown_on(self):
        self.goto("/")
        self.page.evaluate(
            "localStorage.setItem('ticket-aging:scope', 'not json at all')")
        self.goto("/")

        expect(self.page.locator(".report-link")).not_to_have_count(0)
        self.assertNoPageErrors()

    def test_a_remembered_scope_that_would_hide_the_remembered_report_gives_way(self):
        """Both are remembered, and they can contradict each other.

        The report wins: it is what the reader came back for, and a narrowing
        set on an earlier visit is easy to have forgotten. Losing the report
        instead would look like it had been deleted. The scope moves to that
        report's board rather than opening up, since there is nowhere open to
        move to.
        """
        self.goto("/")
        self.page.evaluate(
            "localStorage.setItem('ticket-aging:last-report', 'overflow')")
        self.page.evaluate(
            "localStorage.setItem('ticket-aging:scope',"
            " JSON.stringify({account: null, project: 'beta-board'}))")
        self.goto("/")

        # 'overflow' lives on acme-board, so the beta-board scope would hide it.
        expect(self.page.locator(
            '.report-link[data-slug="overflow"][aria-current="true"]')).to_have_count(1)
        expect(self.page.locator("#scope-project")).to_have_value("acme-board")

    def test_resolving_that_conflict_is_persisted_not_just_applied(self):
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
        expect(self.page.locator("#scope-project")).to_have_value("acme-board")

        stored = self.page.evaluate(
            "JSON.parse(localStorage.getItem('ticket-aging:scope'))")
        self.assertEqual(stored["project"], "acme-board", stored)


class SingleOptionReadout(BrowserTest):
    """One account and one board — the fixture workspace as it ships.

    Previously both controls rendered as nothing here, so the sidebar said
    nothing about which board the reports came from and changed shape depending
    on how many accounts happened to be configured (#40).
    """

    PREBUILD_REPORTS = ("baseline",)

    def setUp(self):
        super().setUp()
        self.goto("/")

    def test_the_only_project_is_named_rather_than_hidden(self):
        expect(self.page.locator("#scope-project-fixed")).to_have_text("Acme Delivery")

    def test_it_is_a_statement_not_a_dropdown(self):
        # A select with one option invites a choice that does not exist.
        expect(self.page.locator("#scope-project")).to_have_count(0)

    def test_the_label_is_still_there(self):
        labels = self.page.locator("#scope-filters label").all_text_contents()
        self.assertIn("Project", labels)

    def test_the_reports_are_not_filtered_by_it(self):
        # The readout describes the scope; it does not narrow anything, because
        # with one value there is nothing to narrow to.
        expect(self.page.locator(".report-link")).to_have_count(6)
        self.assertNoPageErrors()


class ReadoutAndControlTogether(BrowserTest):
    """Two boards on one account: a real choice of board, none of account."""

    PREBUILD_REPORTS = ("baseline",)

    @classmethod
    def prepare_workspace(cls):
        cls.write_workspace_json("accounts.json", {
            "accounts": [{"id": "work", "label": "Work", "gh_account": "octocat"}],
            "default_account": "work",
        })
        cls.write_workspace_json("projects.json", {
            "projects": [
                {"id": "acme-board", "label": "Acme Delivery", "account": "work",
                 "owner": "acme", "owner_type": "organization", "project_number": 1},
                {"id": "beta-board", "label": "Beta Platform", "account": "work",
                 "owner": "acme", "owner_type": "organization", "project_number": 7},
            ],
            "default_project": "acme-board",
        })
        definition = json.loads(
            (cls.workspace / "definitions" / "baseline.json").read_text())
        definition["project"] = "beta-board"
        definition["copy"] = {"title": "Beta Report"}
        (cls.workspace / "definitions" / "beta-report.json").write_text(
            json.dumps(definition))

    def setUp(self):
        super().setUp()
        self.goto("/")

    def test_the_single_account_reads_out_while_the_boards_stay_a_dropdown(self):
        expect(self.page.locator("#scope-account-fixed")).to_have_text("Work")
        expect(self.page.locator("#scope-account")).to_have_count(0)
        expect(self.page.locator("#scope-project")).to_have_count(1)

    def test_the_board_dropdown_still_filters(self):
        self.page.select_option("#scope-project", "beta-board")
        expect(self.page.locator(".report-link")).to_have_count(1)
        self.assertNoPageErrors()


if __name__ == "__main__":
    unittest.main()
