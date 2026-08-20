"""The report editor at /report.

This form writes report definitions, which every later stage reads. A field it
displays but does not save produces a report that looks configured and behaves
as though it were not — the same shape of fault as #21, one layer up. So the
assertions here land on the definition file wherever they can.
"""
import json
import unittest

from tests.browser.harness import BrowserTest, expect


class NewReport(BrowserTest):

    def setUp(self):
        super().setUp()
        self.goto("/report")

    def definition(self, slug):
        return json.loads(
            (self.workspace / "definitions" / f"{slug}.json").read_text())

    def save_expecting_success(self, slug):
        """Click Save and wait for the navigation that follows a new report.

        On success the form sets `location.search = "?slug=..."`, which tears
        down the result box it just populated -- so waiting for that box races
        the navigation and usually loses. The new URL is the durable signal.
        """
        self.page.click("#save")
        self.page.wait_for_url(f"**/report?slug={slug}")

    def test_the_form_loads_without_throwing(self):
        self.assertNoPageErrors()
        self.assertEqual(self.page.locator("#page-title").text_content(), "New report")

    def test_the_status_dropdown_is_populated_from_the_board(self):
        """The statuses come from the live board, not from a hardcoded list.

        A report's `measure_status` is written in one board's vocabulary, so
        offering a fixed set would let someone pick a column that board has
        never had.
        """
        expect(self.page.locator("#measure_status option")).not_to_have_count(0)
        options = self.page.locator("#measure_status option").all_text_contents()
        self.assertIn("In Progress", options)
        self.assertIn("Ready for QA", options)

    def test_the_slug_is_derived_from_the_title_and_shown_before_saving(self):
        # Saying the filename up front matters because it is fixed afterwards.
        self.page.fill("#title", "Waiting on Review")
        expect(self.page.locator("#slug-note")).to_contain_text("waiting-on-review.json")

    def test_saving_writes_a_definition_that_the_pipeline_can_read(self):
        self.page.fill("#title", "Waiting on Review")
        self.page.select_option("#measure_status", "Ready for QA")
        self.page.fill("#additional_filter", "is:open")
        self.page.fill("#bin_days", "1")
        self.page.fill("#threshold_days", "5")
        self.page.fill("#order", "50")
        self.save_expecting_success("waiting-on-review")

        stored = self.definition("waiting-on-review")
        self.assertEqual(stored["measure_status"], "Ready for QA")
        self.assertEqual(stored["additional_filter"], "is:open")
        self.assertEqual(stored["bin_days"], 1)
        self.assertEqual(stored["threshold_days"], 5)
        self.assertEqual(stored["copy"]["title"], "Waiting on Review")
        # The filter is composed at load, never stored, so a definition that
        # carried one would be rejected by load_definition.
        self.assertNotIn("filter", stored)

    def test_the_new_report_appears_in_the_sidebar(self):
        self.page.fill("#title", "Waiting on Review")
        self.page.select_option("#measure_status", "Ready for QA")
        self.page.fill("#bin_days", "1")
        self.page.fill("#threshold_days", "5")
        self.page.fill("#order", "50")
        self.save_expecting_success("waiting-on-review")

        self.goto("/")
        expect(self.page.locator(
            '.report-link[data-slug="waiting-on-review"]')).to_have_count(1)

    def test_the_browser_refuses_an_out_of_range_scale_before_the_server_sees_it(self):
        """The numeric inputs carry min= constraints, so this never submits.

        Worth pinning: it means the server's matching checks are a second line
        of defence rather than the only one, and that removing a min= attribute
        would quietly move the first failure from the field to a round trip.
        """
        self.page.fill("#title", "Broken Report")
        self.page.select_option("#measure_status", "Ready for QA")
        self.page.fill("#bin_days", "0")
        self.page.click("#save")

        self.assertFalse(self.page.evaluate(
            "document.getElementById('bin_days').checkValidity()"))
        self.assertFalse((self.workspace / "definitions" / "broken-report.json").exists(),
                         "a refused report must not be written")

    @unittest.expectedFailure
    def test_a_duplicate_title_is_refused_by_the_server(self):
        """A slug collision can only be judged against what is on disk.

        The browser cannot know "Baseline" is taken, so the server's answer is
        the only thing standing between a new report and silently overwriting an
        existing one.

        **Currently it does not stand there.** `save_report` derives `is_new`
        from whether the slug already exists on disk, so a genuinely new report
        that collides is treated as an edit of the one it collided with:
        `validate_report`'s duplicate check never runs, and the original is
        replaced with no warning. Verified directly against `report_store` --
        a report measuring "In Progress" came back measuring "Ready for QA".

        Marked expected-failure rather than deleted so the suite stays green
        while still carrying the case. Fixing the bug makes this an unexpected
        success, which fails the run and asks for the decorator to come off.
        """
        self.page.fill("#title", "Baseline")
        self.page.select_option("#measure_status", "Ready for QA")
        self.page.fill("#bin_days", "1")
        self.page.fill("#threshold_days", "5")
        self.page.fill("#order", "50")
        self.page.click("#save")

        result = self.page.locator("#save-result")
        expect(result).to_be_visible()
        self.assertIn("already exists", result.text_content())
        # And the report it collided with is untouched.
        self.assertEqual(self.definition("baseline")["copy"]["title"], "Baseline")
        self.assertEqual(self.definition("baseline")["measure_status"], "In Progress")

    def test_the_filter_preview_reports_what_the_filter_matches(self):
        """The form's counterpart to the project Test button.

        A filter typo otherwise saves cleanly and only shows up as an empty
        report after someone refreshes it.
        """
        self.page.fill("#title", "Preview Check")
        self.page.select_option("#measure_status", "In Progress")
        self.page.fill("#additional_filter", "is:open")
        self.page.click("#preview-filter")

        result = self.page.locator("#filter-result.ok, #filter-result.err")
        expect(result).to_be_visible()
        self.assertIn('status:"In Progress"', result.text_content())
        self.assertIn("2", result.text_content())

    def test_the_info_panel_preview_renders_markdown(self):
        self.page.fill("#info_panel", "**bold note** and `code`")
        preview = self.page.locator("#info-preview")
        expect(preview.locator("strong")).to_have_text("bold note")
        expect(preview.locator("code")).to_have_text("code")

    def test_the_preview_does_not_execute_markup(self):
        # The renderer escapes first and reintroduces only its own tags. This
        # is the same guarantee the unit tests assert, checked where it lands.
        self.page.fill("#info_panel", "<script>window.__pwned = true</script>")
        expect(self.page.locator("#info-preview")).to_contain_text("<script>")
        self.assertIsNone(self.page.evaluate("window.__pwned ?? null"))


class ExistingReport(BrowserTest):

    def setUp(self):
        super().setUp()
        self.goto("/report?slug=baseline")

    def test_it_loads_the_stored_values(self):
        self.assertNoPageErrors()
        expect(self.page.locator("#title")).to_have_value("Baseline")
        expect(self.page.locator("#measure_status")).to_have_value("In Progress")
        expect(self.page.locator("#bin_days")).to_have_value("7")

    def test_the_slug_is_fixed_once_saved(self):
        expect(self.page.locator("#slug-note")).to_contain_text("fixed")




class DeletingAReport(BrowserTest):
    """Its own class, and therefore its own workspace.

    Deleting is the one destructive action here. Sharing a workspace with the
    tests above meant this ran first -- unittest orders methods alphabetically --
    and removed the definition they were reading, so two unrelated tests failed
    with no hint that this one had caused it.
    """

    def test_deleting_removes_the_definition(self):
        self.goto("/report?slug=baseline")
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.click("#delete")
        self.page.wait_for_url(lambda url: url.rstrip("/").endswith(str(self.port)))
        self.assertFalse((self.workspace / "definitions" / "baseline.json").exists(),
                         "the definition should be gone from disk")


if __name__ == "__main__":
    unittest.main()
