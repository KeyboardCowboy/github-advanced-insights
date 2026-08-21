"""fetch_board_items must follow pageInfo until the filter is exhausted.

#1: the query asked for one page of 100, printed a warning to stderr when more
matched, and then built the report from the partial set. Nothing on the page
looked wrong — every stat, bar and row agreed with each other — it was simply a
report about a hundred tickets rather than all of them. Silent wrongness, which
is the failure this tool has to be most careful about.

These stub `ticket_aging.graphql` rather than the network. `run_graphql` looks
the name up in module globals when it is called, so replacing it here reaches
the real code path, unlike patching `github.graphql` after import.
"""
import contextlib
import io
import unittest
from unittest import mock

import tests.helpers  # noqa: F401  -- points the tool at the fixtures
import ticket_aging


def page(nodes, has_next, total, cursor="cur"):
    return {"organization": {"projectV2": {
        "number": 1,
        "title": "Acme Delivery",
        "items": {
            "totalCount": total,
            "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
            "nodes": nodes,
        },
    }}}


def issues(start, count, repo="acme/web"):
    return [{"content": {"id": f"I_{n}", "number": n, "title": f"Issue {n}",
                         "url": f"https://example.invalid/{n}",
                         "repository": {"nameWithOwner": repo}}}
            for n in range(start, start + count)]


DEFINITION = {"filter": 'status:"In Progress" is:open', "project_number": 1}
PROJECT = {"owner": "acme"}


class Pagination(unittest.TestCase):

    def fetch(self, pages):
        with mock.patch.object(ticket_aging, "graphql",
                               side_effect=pages) as stub:
            result = ticket_aging.fetch_board_items(
                DEFINITION, PROJECT, None, "organization")
        return result, stub

    def test_a_single_page_makes_one_request(self):
        (board, found, matched, seen), stub = self.fetch(
            [page(issues(1, 40), False, 40)])
        self.assertEqual(len(found), 40)
        self.assertEqual((matched, seen), (40, 40))
        self.assertEqual(stub.call_count, 1, "no second request when done")
        self.assertEqual(board["title"], "Acme Delivery")

    def test_it_keeps_going_until_the_filter_is_exhausted(self):
        (_, found, matched, seen), stub = self.fetch([
            page(issues(1, 100), True, 250),
            page(issues(101, 100), True, 250),
            page(issues(201, 50), False, 250),
        ])
        self.assertEqual(len(found), 250, "every matching issue should be kept")
        self.assertEqual((matched, seen), (250, 250))
        self.assertEqual(stub.call_count, 3)

    def test_exactly_one_full_page_does_not_fetch_a_second(self):
        # The off-by-one that a naive "keep going while the page was full" would
        # get wrong. hasNextPage is the authority, not the node count.
        (_, found, _, _), stub = self.fetch([page(issues(1, 100), False, 100)])
        self.assertEqual(len(found), 100)
        self.assertEqual(stub.call_count, 1)

    def test_the_cursor_from_each_page_is_sent_to_the_next(self):
        pages = [page(issues(1, 100), True, 150, cursor="CURSOR-A"),
                 page(issues(101, 50), False, 150)]
        with mock.patch.object(ticket_aging, "graphql", side_effect=pages) as stub:
            ticket_aging.fetch_board_items(
                DEFINITION, PROJECT, None, "organization")
        first, second = (call.args[0] for call in stub.call_args_list)
        self.assertNotIn("after:", first, "the first request has nowhere to start")
        self.assertIn('after: "CURSOR-A"', second,
                      "without the cursor every page would repeat the first")

    def test_non_issue_items_are_skipped_without_looking_like_truncation(self):
        """Draft items and pull requests sit on boards and have no Issue content.

        They are skipped, so the issue count is legitimately below totalCount.
        The completeness check has to count items *seen* — comparing totalCount
        to the issues kept would report any board holding one draft as
        truncated, on every fetch.
        """
        nodes = issues(1, 3) + [{"content": None}, {"content": None}]
        (_, found, matched, seen), _ = self.fetch([page(nodes, False, 5)])
        self.assertEqual(len(found), 3)
        self.assertEqual((matched, seen), (5, 5),
                         "seen counts items, not just the issues among them")

    def test_it_stops_at_the_page_cap_rather_than_running_away(self):
        # A filter matching everything should cost a bounded number of requests.
        endless = [page(issues(1, 100), True, 10 ** 6) for _ in range(200)]
        warning = io.StringIO()
        with mock.patch.object(ticket_aging, "graphql", side_effect=endless) as stub, \
                contextlib.redirect_stderr(warning):
            _, _, matched, seen = ticket_aging.fetch_board_items(
                DEFINITION, PROJECT, None, "organization")
        self.assertEqual(stub.call_count, ticket_aging.MAX_ITEM_PAGES)
        self.assertLess(seen, matched, "the caller can tell this is incomplete")
        # Stopping quietly would be the original bug in a new place.
        self.assertIn("Stopped after", warning.getvalue())

    def test_issues_from_several_repositories_are_all_kept(self):
        # A board can span repositories, and pagination must not assume one.
        (_, found, _, _), _ = self.fetch([
            page(issues(1, 100, "acme/web"), True, 150),
            page(issues(1, 50, "acme/api"), False, 150),
        ])
        repos = {i["repository"]["nameWithOwner"] for i in found}
        self.assertEqual(repos, {"acme/web", "acme/api"})
        self.assertEqual(len(found), 150)


if __name__ == "__main__":
    unittest.main()
