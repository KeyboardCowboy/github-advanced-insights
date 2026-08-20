"""Settings: account and project cards, the parts only JavaScript builds.

Both bugs #28 cites lived on this page, and neither was reachable from a unit
test. #21 was a project field the form displayed correctly and never saved; #20
was a button wired to a saved record that did nothing before one existed. What
they have in common is that the Python was fine — the fault was in the wiring
between the form and the request, which only exists once the page runs.

So these assert against `projects.json` and `accounts.json` on disk wherever
they can. A page that shows the right thing and writes the wrong thing is
exactly the failure being guarded against.
"""
import unittest

from tests.browser.harness import BrowserTest


class SettingsPage(BrowserTest):
    """Shared helpers.

    Both templates render `<form class="project">` -- the account card reuses
    the project card's styling -- so an unscoped `form.project` matches both
    kinds. Scoping every lookup to its list is not tidiness; an unscoped `.last`
    silently drives the wrong card and the test hangs waiting for a result that
    appears somewhere else on the page.
    """

    def setUp(self):
        super().setUp()
        self.goto("/settings")

    def account_card(self, index=-1):
        cards = self.page.locator("#account-list form.project")
        return cards.last if index == -1 else cards.nth(index)

    def project_card(self, index=-1):
        cards = self.page.locator("#project-list form.project")
        return cards.last if index == -1 else cards.nth(index)

    def save_new(self, card, list_id):
        """Save a new card and wait for the outcome, whichever it is.

        The two paths look nothing alike. A successful save calls `load()`,
        which re-renders the whole list -- the card being driven is replaced and
        never shows a result box. A failed save leaves the card in place and
        reports into it. Waiting for only one of those hangs for the full
        timeout whenever the other happens, which reads as a broken test rather
        than as the failure it is.

        A card awaiting its first save carries `is-new`; the re-rendered one
        does not. Its absence is therefore the success signal.
        """
        card.locator("button.primary").click()
        self.page.wait_for_function(
            f"() => !document.querySelector('#{list_id} .is-new')"
            f"      || document.querySelector('#{list_id} .result.err') !== null")

    def save_expecting_refusal(self, card):
        """Save something invalid and return the problems reported."""
        card.locator("button.primary").click()
        return self.settled_result(card)

    def run_test_button(self, card):
        """Click Test and return what the card reported."""
        card.locator("button.test").click()
        return self.settled_result(card)

    def settled_result(self, card):
        """Wait past the transient "Checking..." placeholder.

        `showResult` sets no kind class while a request is in flight and adds
        `ok` or `err` once it lands, so the class is what says the answer is
        final. Waiting on visibility alone reliably catches the placeholder and
        asserts against the word "Checking...".
        """
        box = card.locator(".result.ok, .result.err")
        box.wait_for(state="visible")
        return box.text_content()


class AccountCards(SettingsPage):

    def test_the_page_builds_without_throwing(self):
        # The settings rename once left an anchor unmatched and this page threw
        # "Cannot read properties of null" while still looking half-right.
        self.assertNoPageErrors()
        self.assertTrue(self.page.locator("#add-account").is_visible())

    def test_account_id_is_derived_from_the_label_as_you_type(self):
        self.page.click("#add-account")
        card = self.account_card()
        card.locator("input[name=label]").fill("Work Account")
        self.assertEqual(card.locator("input[name=id]").input_value(), "work-account")

    def test_typing_an_id_stops_it_tracking_the_label(self):
        # Otherwise a deliberate id is silently overwritten by the next
        # keystroke in Label, which is worse than never deriving it at all.
        self.page.click("#add-account")
        card = self.account_card()
        card.locator("input[name=label]").fill("Work")
        card.locator("input[name=id]").fill("chosen-by-hand")
        card.locator("input[name=label]").fill("Work Account Renamed")
        self.assertEqual(card.locator("input[name=id]").input_value(), "chosen-by-hand")

    def test_saving_an_account_writes_it_to_disk(self):
        self.page.click("#add-account")
        card = self.account_card()
        card.locator("input[name=label]").fill("Work")
        card.locator("input[name=gh_account]").fill("octocat")
        self.save_new(card, "account-list")

        saved = [a for a in self.accounts() if a["id"] == "work"]
        self.assertEqual(len(saved), 1, f"account not persisted: {self.accounts()}")
        self.assertEqual(saved[0]["gh_account"], "octocat")
        self.assertEqual(saved[0]["label"], "Work")

    def test_test_button_works_before_the_account_is_saved(self):
        """Regression for #20.

        The button used to read the saved record, so on a new card it looked
        enabled, did nothing, and gave no reason. It has to test what is in the
        form right now — which is the only state that exists yet.
        """
        self.page.click("#add-account")
        card = self.account_card()
        card.locator("input[name=label]").fill("Unsaved")
        card.locator("input[name=gh_account]").fill("octocat")

        # The stub answers as acme-bot; seeing that means the request carried
        # the form's values rather than reading a record that does not exist.
        self.assertIn("acme-bot", self.run_test_button(card))
        self.assertEqual([a["id"] for a in self.accounts() if a["id"] == "unsaved"], [],
                         "Test must not save the account as a side effect")

    def test_invalid_account_reports_every_problem_at_once(self):
        self.page.click("#add-account")
        card = self.account_card()
        card.locator("input[name=id]").fill("Not A Valid Id")
        card.locator("input[name=gh_account]").fill("octocat")
        card.locator("input[name=token_env]").fill("GH_TOKEN")
        text = self.save_expecting_refusal(card)
        self.assertIn("not a valid account id", text)
        self.assertIn("not both", text,
                      "both problems should surface together, not one per attempt")


class ProjectCards(SettingsPage):

    def test_the_fixture_project_is_listed(self):
        self.assertNoPageErrors()
        titles = self.page.locator("#project-list .project-title").all_text_contents()
        self.assertTrue(any("Acme Delivery" in t for t in titles),
                        f"fixture board missing from {titles}")

    def test_cards_are_collapsible(self):
        """Every card open at once made the page unreadable with a few boards."""
        self.page.click("#add")
        card = self.project_card()
        details = card.locator("details")
        self.assertTrue(details.get_attribute("open") is not None,
                        "a new card should start open, since it needs filling in")
        card.locator("summary").click()
        self.assertIsNone(details.get_attribute("open"))

    def test_saving_a_project_persists_the_selected_account(self):
        """Regression for #21.

        The account dropdown showed the right value and the saved file did not
        contain it. Two independent causes: the client's FIELDS list omitted
        `account`, and `save_project` had no `account` key. Fixing either alone
        left the symptom, so this asserts on the file rather than the form.
        """
        # An account has to exist before a project can point at one.
        self.page.click("#add-account")
        account_card = self.account_card()
        account_card.locator("input[name=label]").fill("Work")
        account_card.locator("input[name=gh_account]").fill("octocat")
        self.save_new(account_card, "account-list")

        self.page.reload(wait_until="networkidle")
        self.page.click("#add")
        card = self.project_card()
        card.locator("input[name=label]").fill("Second Board")
        card.locator("input[name=owner]").fill("acme")
        card.locator("input[name=project_number]").fill("7")
        card.locator("select[name=account]").select_option("work")
        self.save_new(card, "project-list")

        saved = [p for p in self.projects() if p["id"] == "second-board"]
        self.assertEqual(len(saved), 1, f"project not persisted: {self.projects()}")
        self.assertEqual(saved[0]["account"], "work",
                         "the selected account reached the form but not the file")

    def test_test_project_reports_the_board_it_found(self):
        card = self.page.locator("#project-list form.project").first
        card.locator("summary").click()
        self.assertIn("Acme Delivery", self.run_test_button(card))

    def test_a_board_number_that_does_not_exist_is_reported(self):
        self.page.click("#add")
        card = self.project_card()
        card.locator("input[name=label]").fill("Typo Board")
        card.locator("input[name=owner]").fill("acme")
        card.locator("input[name=project_number]").fill("999")
        self.assertIn("999", self.run_test_button(card))


if __name__ == "__main__":
    unittest.main()
