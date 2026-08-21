"""A stand-in for `github.graphql`, so browser tests never touch the network.

#28 called this out as the reason browser tests are affordable here: every
network call in the tool goes through one function, so there is no HTTP mock
server to run and no request interception to configure. Replacing that one name
takes the whole tool offline.

**Install this before importing the tool.** Every module does
`from github import graphql`, which copies the reference into its own namespace
at import time. Patching `github.graphql` afterwards rebinds the original module
attribute and nothing else — the already-imported copies keep calling the real
one, and the first test to hit a form would open a socket. `serve_stub.py`
patches first and imports second for exactly this reason.

The canned board matches `tests/fixtures/workspace/projects.json`, so what the
forms show lines up with what is on disk.
"""
import json
import re

# Matches the fixture workspace's board, so the settings page and the report
# definitions describe the same thing.
BOARD_TITLE = "Acme Delivery"
BOARD_STATUSES = ["Todo/Ready", "In Progress", "Dev Done / Peer Review",
                  "PR Validated", "Ready for QA", "Done"]
REPOSITORIES = ["acme/web", "acme/api"]
VIEWER_LOGIN = "acme-bot"

# Every query this stub was asked, so a test can assert that a button which
# should not have called GitHub did not, and that one which should did.
calls = []


class FakeGitHubError(Exception):
    """Stands in for github.GitHubError when a test wants a failure."""


def graphql(query, account=None):
    """Answer the handful of query shapes the server actually sends.

    Deliberately matched on substrings rather than parsed: the point is to
    return plausible data for a known caller, not to reimplement GraphQL. An
    unrecognised query raises rather than returning an empty dict, because a
    silently empty answer would show up as a confusing assertion failure three
    layers away instead of naming the query nobody taught this about.
    """
    calls.append({"query": query, "account": account})

    if "viewer" in query and "rateLimit" in query:
        return {"viewer": {"login": VIEWER_LOGIN},
                "rateLimit": {"remaining": 4987}}

    scope = "user" if re.search(r"\buser\(login:", query) else "organization"

    if "projectsV2(" in query:
        # The board picker. Returns a couple of hits so a test can click one.
        return {scope: {"projectsV2": {
            "totalCount": 2,
            "nodes": [
                {"number": 1, "title": BOARD_TITLE, "closed": False},
                {"number": 7, "title": "Acme Platform", "closed": False},
            ],
        }}}

    # Must require `query:` as well: `inspect_board` also asks for `items`, as
    # `items(first: 1) { totalCount }`, and answering that one from here returns
    # a board with no title or Status field -- which the server then raises a
    # KeyError on. The symptom is a settings page that never shows a result and
    # a test that times out pointing at the button rather than at this.
    if "items(" in query and "query:" in query and "projectV2(" in query:
        # The report form's filter preview. Answers with a couple of issues so
        # the form can report a match; a filter naming a status the fixture
        # board does not have returns nothing, which is the case the preview
        # exists to catch.
        # Greedy to the last quote: the composed filter contains escaped
        # quotes of its own, so a non-greedy match stops inside it.
        wanted = re.search(r'query:\s*"(.*)"\s*\)', query)
        composed = wanted.group(1) if wanted else ""
        # The composed filter arrives with its quotes escaped for GraphQL, so
        # match on the status name itself rather than trying to reproduce the
        # quoting -- the question is only whether this board has that column.
        known = any(s in composed for s in BOARD_STATUSES)
        if not known:
            return {scope: {"projectV2": {"items": {
                "totalCount": 0,
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [],
            }}}}
        # Two callers ask for items with a query: the report form's filter
        # preview, which wants only counts and titles, and the dashboard fetch,
        # which wants the board's identity and each issue's node id. Answering
        # with the union keeps one branch honest for both -- the preview simply
        # ignores the fields it did not ask for, as it would with the real API.
        return {scope: {"projectV2": {
            "number": 1,
            "title": BOARD_TITLE,
            "items": {
                "totalCount": 2,
                # Every items response carries pageInfo, because the fetch loop
                # reads it to decide whether to ask for another page (#1). A
                # stub without it takes the tool down a path it never takes.
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [
                    {"content": {
                        "id": f"I_stub{number}", "number": number,
                        "title": f"Fixture issue {number}",
                        "url": f"https://example.invalid/{number}",
                        "repository": {"nameWithOwner": REPOSITORIES[index]},
                    }}
                    for index, number in enumerate((105, 203))
                ],
            },
        }}}

    if "projectV2(" in query:
        number = int(re.search(r"projectV2\(number:\s*(\d+)", query).group(1))
        # Only the fixture board exists; anything else is a typo in the form,
        # which is a case the settings page is supposed to report.
        if number not in (1, 7):
            return {scope: {"projectV2": None}}
        return {scope: {"projectV2": {
            "title": BOARD_TITLE if number == 1 else "Acme Platform",
            "items": {"totalCount": 42},
            "repositories": {"nodes": [{"nameWithOwner": r} for r in REPOSITORIES]},
            "field": {"options": [{"name": s} for s in BOARD_STATUSES]},
        }}}

    if "nodes(ids:" in query and "timelineItems" in query:
        # The timeline batch. Answers with the ids it was asked for and one
        # status change each, so a refresh completes end to end rather than
        # stopping halfway with a board fetched and no history to age it by.
        ids = re.findall(r'"([^"]+)"', query)
        return {"nodes": [
            {
                "id": node_id,
                "number": 100 + index,
                "title": f"Stubbed issue {100 + index}",
                "url": f"https://example.invalid/{100 + index}",
                "repository": {"nameWithOwner": REPOSITORIES[index % len(REPOSITORIES)]},
                "timelineItems": {
                    "totalCount": 1,
                    "nodes": [{
                        "createdAt": "2026-06-10T09:00:00Z",
                        "previousStatus": "Todo/Ready",
                        "status": BOARD_STATUSES[index % 3 + 1],
                        "actor": {"login": "a-person"},
                        "project": {"number": 1, "title": BOARD_TITLE},
                    }],
                },
            }
            for index, node_id in enumerate(ids)
        ]}

    raise AssertionError(
        "fake_github has no answer for this query. Add one rather than "
        "letting a test reach the network:\n" + query.strip()[:400]
    )


def install():
    """Replace the real network seam and the credential lookup.

    `resolve_token` shells out to `gh`, so leaving it alone would make the
    tests depend on whether the machine running them happens to be logged in --
    passing on a laptop and failing in CI, or worse, the reverse.
    """
    import github
    github.graphql = graphql

    import accounts
    accounts.resolve_token = lambda account=None: "fake-token-for-tests"


def dump_calls(path):
    """Write the recorded calls where the parent process can read them."""
    with open(path, "w") as handle:
        json.dump(calls, handle)
