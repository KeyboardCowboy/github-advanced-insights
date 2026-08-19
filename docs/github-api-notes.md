# GitHub API notes

What this tool relies on, and the things that are easy to get wrong. Verified against the
GitHub GraphQL API in August 2026.

## Status history lives in GraphQL only

Every board status change is recorded as a `ProjectV2ItemStatusChangedEvent` on the issue's
timeline. **Use GraphQL, not REST.** The REST timeline endpoint (`/issues/{n}/timeline`)
returns the same events but with the from/to values stripped, so REST can tell you *that*
an issue moved and never *where to*.

The GraphQL event carries `previousStatus`, `status`, `project`, `actor`, `createdAt`, and
`wasAutomated`. Filter server-side with
`itemTypes: [PROJECT_V2_ITEM_STATUS_CHANGED_EVENT]` so the response skips unrelated label
and comment events.

Two things that change the answer:

- **A first event with an empty `previousStatus`** is the initial placement when the issue
  was added to the board, not a move between columns.
- **Group events by project before computing any duration.** An issue on two boards returns
  one interleaved timeline covering two independent histories; measuring across them
  produces nonsense intervals.

## Fetch issues by node id, not by repository and number

A Projects v2 board can hold issues from several repositories, and issue numbers are only
unique *within* a repository. Fetching through `repository(owner, name) { issue(number) }`
therefore both assumes a single repo and risks collapsing two different issues that share a
number.

`nodes(ids: [...])` avoids all of it, costs the same, and returns each issue's `url` and
owning `repository` directly, so nothing downstream has to know where an issue lives.

## The `type:` filter works, including negated

Filtering by issue type server-side is reliable. Verified on a real board: `type:Epic`
returned 59 items, all genuinely Epics; `-type:Epic,Feature` dropped exactly the 9
Epic/Feature items from a 94-item set, matching a client-side check issue for issue.

This is worth stating because it is easy to conclude otherwise from a mismatched baseline.
Comparing `type:Epic` against a count of *component epics* (label + type + open) will show
a discrepancy that belongs to the narrower baseline, not to the filter.

## Query cost

The GraphQL budget is 5,000 points per hour, calculated from query *shape* rather than
request count. Cost scales with how many connections you request and how deeply they nest.

- A server-side-filtered `ProjectV2.items(query: "...")` pull costs **1 point**, versus
  thousands for an unfiltered full-board fetch. The `query` argument takes the same syntax
  as the Projects UI filter bar.
- Issue timelines batch 20 to a request at **1 point** per batch.
- A full report refresh is about **3 points**, so cost is not a practical constraint.

Include `rateLimit { cost remaining }` as a sibling field to have a query report its own
cost. Check the budget with a cheap REST call before a large pull, not after it fails.

## Limits worth knowing

- **The item query fetches the first 100 matches.** Past that the tool warns on stderr
  rather than silently truncating. Pagination is not implemented.
- **Aging depends on recorded status events.** Issues whose board history predates GitHub
  emitting them have no entry event; they are excluded from counts and listed by number
  rather than counted as zero days.
- **Endpoint is `api.github.com`.** GitHub Enterprise Server would need a configurable
  host; `gh api` would have honoured one, direct HTTPS does not.
