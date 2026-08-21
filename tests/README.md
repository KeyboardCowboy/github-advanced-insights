# Tests

## Running them

```
python3 -m unittest discover
```

From the repo root, with no arguments and nothing to install. Everything is
stdlib, so this is also the whole of what CI has to run.

The suite never touches the network, your credentials, or your own workspace.
`tests/helpers.py` sets `GH_INSIGHTS_HOME` to the fixtures before the tool is
imported, and anything that writes works on a throwaway copy — so a test run
leaves no changes behind and gives the same result on any machine.

| File | Covers |
|---|---|
| `test_normalize_goldens.py` | every fixture's view model, compared to its golden |
| `test_bins.py` | `build_bins`: band edges, empty bands, the overflow bucket |
| `test_markup.py` | `markup.render`: formatting, and that raw HTML stays inert |
| `test_validation.py` | the account, project and report validators |
| `test_filter_composition.py` | the GitHub filter built from `measure_status` |

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

## The dashboard fixture

`dashboard-acme-board-raw.json` is the board-wide file the dashboard pipeline
reads: every case's tickets pooled into one, because the dashboard fetches the
whole board at once rather than one status at a time. Pooling also gives it the
thing no single report has — tickets spread across several columns, which is the
entire point of the scatter.

Some of those tickets sit in columns the fixture board no longer declares. That
is deliberate: a status can be removed from a board while tickets are still in
it, and dropping them would quietly shrink the total. They are drawn after the
declared columns and flagged `off_board`.

It has its own golden, generated the same way as the report ones.

## Derived files are not fixtures

Running `build_fixtures.py --update-goldens` leaves `*-view-model.json` files in
`fixtures/workspace/cache/`. They are gitignored, so a clean checkout and CI
never see them — but `copytree` does, and then every report has data no matter
what a test's `PREBUILD_REPORTS` says.

That is the worst shape of test bug: green on the machine that generated the
goldens, different on a clean checkout. The browser harness therefore deletes
them after copying, so every run starts from the committed raw files and builds
only what it asked for.

The nested `fixtures/workspace/.gitignore` is committed for a related reason.
`workspace.ensure()` writes one there on first run that excludes `cache/`, which
is right for a real installation and wrong here, where the raw files *are* the
fixture. `ensure()` only writes it when it is missing, so the committed one is
what stops it. Delete that file and the next fixture added silently never gets
committed.

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

## Browser tests

`tests/browser/` covers what only exists once JavaScript runs: the report page,
the report form, and settings. These need Playwright, which the tool itself does
not:

```bash
pip install -r requirements-dev.txt
playwright install chromium
python3 -m unittest discover -s tests/browser -t .
```

Without Playwright they skip rather than fail, so `python3 -m unittest discover`
still works on a plain checkout. That is the property worth protecting: the
offline suite has to keep running with nothing installed, which is why CI runs
the two in separate jobs rather than adding a leg to the matrix.

### Why these exist

Unit tests cannot see the bugs that lived here. #21 was a project field the form
displayed correctly and never saved — and it had two independent causes, the
second found only because fixing the first did not fix the symptom. #20 was a
button wired to a saved record that did nothing before one existed. In both the
Python was fine; the fault was in the wiring, which only exists once the page
runs.

So these assert against `projects.json` and `accounts.json` **on disk** wherever
they can. A page that shows the right thing and writes the wrong thing is the
exact failure being guarded against, and a test that only reads the form would
have passed on both bugs.

### No network

`fake_github.py` replaces `github.graphql`, the single seam every network call
passes through, so there is no mock HTTP server and no request interception.

It has to be installed **before** the tool is imported. Every module does
`from github import graphql`, which copies the reference into its own namespace
at import time; patching afterwards rebinds a name nobody reads any more while
the live function stays wired up. `serve_stub.py` patches first and imports
second, and that ordering is the reason it is a separate file rather than a few
lines in the harness.

The stub raises on a query it does not recognise rather than returning an empty
result, so teaching it about a new call is a clear error rather than a confusing
assertion failure three layers away.

### Waiting

Prefer `expect(locator)` over reading state straight after an action. Two traps
already caught here:

- `aria-current` on a sidebar link flips when the sidebar redraws, which happens
  *before* the view model arrives. It means "selected", not "drawn" — asserting
  on the chart right after it reads the previous report.
- A successful save re-renders the list, or navigates. The card being driven is
  replaced and its result box never appears; only failures report in place.
  Waiting for the wrong one of those burns the full timeout and reads as a hang
  rather than as the failure it is.

### One workspace per class

`setUpClass` copies the fixtures once, so every test in a class shares the
result. Anything that writes — saving a report, reordering, deleting — leaves
that behind for the tests after it, and unittest runs methods in alphabetical
order, not the order they appear in the file.

This has bitten twice. A delete test ran first and removed the definition two
later tests were reading. Two reorder tests each assumed the shipped order, and
the second saw what the first had left.

Two ways out, both used here:

- **Write assertions against the list as it stands**, not against fixed slugs —
  drag the last onto the first and assert *that* moved, rather than naming one.
- **Give a destructive test its own class**, which buys it its own workspace.
  `DeletingAReport` exists for exactly that reason.

The same goes for anything the *tool* refuses to do twice. Two tests that each
create a report titled "Waiting on Review" only passed while a duplicate title
silently overwrote the first one; once that was fixed (#32), the second was
correctly refused.
