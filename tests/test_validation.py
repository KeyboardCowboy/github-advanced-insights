"""The three validators guard everything the settings forms can save.

Two properties matter beyond "bad input is rejected". First, each returns *all*
the problems it found rather than the first, so someone filling in a form gets
one round of corrections instead of a dozen. Second, the messages name the field
in the words the form uses, because a message that names an internal key sends
the reader looking for something they cannot see.
"""
import unittest

import tests.helpers  # noqa: F401  -- points the tool at the fixtures; must precede the imports below
from accounts import validate_account
from projects import validate_project
from report_store import validate_report


def report(**overrides):
    """A report that validates cleanly, so a test can spoil exactly one field."""
    return {"copy": {"title": "Some report"}, "measure_status": "In Progress",
            "project": "acme-board", "bin_days": 7, "threshold_days": 14,
            "order": 10, **overrides}


class Accounts(unittest.TestCase):

    def test_a_valid_account_has_no_problems(self):
        self.assertEqual(validate_account({"id": "work", "gh_account": "octocat"}), [])

    def test_no_credential_source_is_allowed(self):
        # Means "whatever gh is logged in as", which is the pre-accounts behaviour.
        self.assertEqual(validate_account({"id": "default"}), [])

    def test_id_must_be_machine_readable(self):
        problems = validate_account({"id": "Work Account!"})
        self.assertTrue(any("not a valid account id" in p for p in problems))

    def test_duplicate_id_is_rejected_only_for_new_accounts(self):
        self.assertTrue(validate_account({"id": "work"}, ["work"], is_new=True))
        self.assertEqual(validate_account({"id": "work"}, ["work"], is_new=False), [])

    def test_two_credential_sources_are_rejected(self):
        problems = validate_account(
            {"id": "work", "gh_account": "octocat", "token_env": "GH_TOKEN"})
        self.assertTrue(any("not both" in p for p in problems))

    def test_env_var_name_must_look_like_one(self):
        problems = validate_account({"id": "work", "token_env": "my-token"})
        self.assertTrue(any("environment variable name" in p for p in problems))

    def test_all_problems_are_returned_together(self):
        problems = validate_account({"id": "Bad Id", "token_env": "lower-case"})
        self.assertGreaterEqual(len(problems), 2, problems)


class Projects(unittest.TestCase):

    def test_a_valid_project_has_no_problems(self):
        self.assertEqual(validate_project(
            {"id": "board", "owner": "acme", "owner_type": "organization",
             "project_number": 1}), [])

    def test_owner_type_is_constrained(self):
        problems = validate_project(
            {"id": "board", "owner": "acme", "owner_type": "team", "project_number": 1})
        self.assertTrue(any("Owner type must be one of" in p for p in problems))

    def test_project_number_must_be_a_positive_integer(self):
        for bad in (None, 0, -1, "abc", ""):
            with self.subTest(project_number=bad):
                problems = validate_project(
                    {"id": "board", "owner": "acme", "owner_type": "user",
                     "project_number": bad})
                self.assertTrue(any("positive whole number" in p for p in problems))

    def test_a_numeric_string_is_accepted(self):
        # Form fields arrive as strings; rejecting "1" would break every save.
        self.assertEqual(validate_project(
            {"id": "board", "owner": "acme", "owner_type": "user",
             "project_number": "1"}), [])

    def test_messages_use_the_word_project_not_connection(self):
        # "Connection" was the old name for this concept and appears nowhere in
        # the interface any more, so it should not appear in an error either.
        problems = validate_project({})
        self.assertEqual([p for p in problems if "onnection" in p], [])

    def test_all_problems_are_returned_together(self):
        problems = validate_project({})
        self.assertGreaterEqual(len(problems), 3, problems)


class Reports(unittest.TestCase):

    def test_a_valid_report_has_no_problems(self):
        self.assertEqual(validate_report("new-report", report(), is_new=True), [])

    def test_slug_must_be_machine_readable(self):
        problems = validate_report("New Report", report(), is_new=True)
        self.assertTrue(any("not a valid report id" in p for p in problems))

    def test_existing_slug_is_rejected_for_a_new_report(self):
        # 'baseline' is one of the fixture definitions on disk.
        problems = validate_report("baseline", report(), is_new=True)
        self.assertTrue(any("already exists" in p for p in problems))
        self.assertEqual(validate_report("baseline", report(), is_new=False), [])

    def test_title_and_status_are_required(self):
        problems = validate_report(
            "r", report(copy={"title": "  "}, measure_status=""), is_new=True)
        self.assertTrue(any("Label is required" in p for p in problems))
        self.assertTrue(any("Status is required" in p for p in problems))

    def test_unknown_project_is_rejected_and_the_known_ones_listed(self):
        problems = validate_report("r", report(project="nope"), is_new=True)
        self.assertTrue(any("does not exist" in p for p in problems))
        self.assertTrue(any("acme-board" in p for p in problems),
                        "the message should say which projects do exist")

    def test_bin_days_must_be_at_least_one(self):
        for bad in (0, -1, None, "seven"):
            with self.subTest(bin_days=bad):
                problems = validate_report("r", report(bin_days=bad), is_new=True)
                self.assertTrue(any("Range per bar" in p for p in problems))

    def test_max_bars_may_be_empty_but_not_absurd(self):
        self.assertEqual(validate_report("r", report(max_bars=""), is_new=True), [])
        self.assertEqual(validate_report("r", report(max_bars=None), is_new=True), [])
        problems = validate_report("r", report(max_bars=5000), is_new=True)
        self.assertTrue(any("unreadable chart" in p for p in problems))

    def test_threshold_of_zero_is_allowed(self):
        # Unlike bin_days, zero is meaningful: flag everything.
        self.assertEqual(validate_report("r", report(threshold_days=0), is_new=True), [])
        self.assertTrue(validate_report("r", report(threshold_days=-1), is_new=True))

    def test_all_problems_are_returned_together(self):
        problems = validate_report(
            "Not A Slug", {"copy": {}, "bin_days": 0}, is_new=True)
        self.assertGreaterEqual(len(problems), 4, problems)


if __name__ == "__main__":
    unittest.main()
