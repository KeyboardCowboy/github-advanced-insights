# Tests

## Fixtures

`fixtures/workspace/` is a synthetic workspace: a fake board, six report
definitions, and hand-built raw data. `fixtures/expected/` holds the goldens —
the view models `normalize` should produce from it.

Run anything against it by pointing the tool at the fixture workspace:

```
GH_INSIGHTS_HOME=tests/fixtures/workspace python3 ticket_aging.py normalize baseline
```

No network, no credentials. `normalize` and `build` only read and write files.

### Why synthetic

A real board exercises the ordinary path and nothing else. Each fixture is a case
a live board cannot be relied on to contain:

| Fixture | Case |
|---|---|
| `baseline` | several tickets at different ages; one that left the status and came back |
| `no-entry-event` | history predating status tracking, so the ticket cannot be aged |
| `multi-repo` | two repositories on one board, with the same issue number in both |
| `batch-day` | four tickets entering on one day, triggering batch flagging |
| `overflow` | more tickets than `max_bars` allows, producing the trailing bucket |
| `empty` | a report matching nothing, which must render zeros rather than fail |

Synthetic data also keeps real issue titles out of a public repository.

## Goldens

A golden is a saved copy of correct output. A test runs `normalize` and compares
its result to the golden; any difference fails and the diff shows what changed.
One comparison covers every stat, bin, row and note at once, so a change nobody
predicted is still caught.

This only works because the pipeline is deterministic. `normalize` measures ages
from the raw file's `fetched_at` rather than from the current time, and its
helpers contain no clock and no randomness. Verified: regenerating twice, seconds
apart, produces byte-identical files.

**Input is three files, not one:** the raw cache, the report definition, and the
project record. Changing `bin_days` in a definition legitimately shifts every bin,
so all three are frozen here.

**The view model is goldened, not the built HTML.** The HTML embeds the page
template, so a golden of it would fail on every styling change and soon be
ignored. The view model is pure data and changes only when behaviour does.

## Changing fixtures or goldens

```
python3 tests/fixtures/build_fixtures.py                    # fixtures only
python3 tests/fixtures/build_fixtures.py --update-goldens   # and the expected output
```

Regenerating fixtures without `--update-goldens` leaves the goldens stale and the
tests failing. That is deliberate: a golden should change only when someone
decided it should, and the diff is where a reviewer sees it.

**A failing golden is not automatically a stale golden.** If someone reintroduces
`now()` into the age calculation, these tests fail — that is them working, and
regenerating would erase the signal rather than fix anything. Read the diff before
reaching for `--update-goldens`.
