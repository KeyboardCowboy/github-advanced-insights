# Roadmap

Design notes and decisions carried over from this tool's first life inside the NYU Langone
wiki repository, where it was built between 2026-08-19 and its extraction here. Kept as one
document rather than split into issues so the reasoning stays attached to each decision;
the intent is to file these as issues and let this file shrink.

Sections marked **built** are done. Everything else is a proposal, and several were
explicitly rejected with reasons, which are worth reading before re-proposing them.

---

# Plan: Settings Interface for Report Definitions

**Status:** Planned, not built (2026-08-19). Design only. The tool it extends is
`` (see `README.md`), which currently works and needs no changes to
keep working. Build scope was not chosen; the options are at the end.

## Problem

A report today is a JSON file in `definitions/`, hand-written. That is fine for
Chris working with an agent, but it means nobody else on the team can add or adjust a
report without editing JSON and knowing the key vocabulary. The goal is self-service: a
form where someone gives a report a label, a GitHub filter, a description, and "how to
read it" notes, and it appears in the sidebar.

The question that prompted this plan was where those reports would be stored, so that
they are not recreated every time the server starts.

## The storage question is already answered

`definitions/*.json` **is** the persistence layer, and it is the right one:

- It survives restarts, because it is files on disk rather than server memory.
- It is version-controlled, so definitions are diffable, reviewable, and revertable.
- It travels on clone, so a teammate gets the reports along with the tool.
- It is already the single source of truth that both the CLI and the server read.

Critically, **nothing caches definitions in memory**. `load_definition()` reads the file
and `ordered_slugs()` globs the directory on every request. A settings UI can write a
file and the sidebar will reflect it on the next page load, with no restart and no cache
invalidation to design.

So this is not a storage problem. A settings interface is a form that writes the JSON
files the tool already reads. No database, no new format, no migration.

## Why per-project status vocabularies need no generic categories

The concern that started this discussion was that every project has different statuses,
so any fixed set of categories the tool invents would be wrong somewhere.

It does not need to invent any. The board already publishes its own vocabulary, in
workflow order, for one GraphQL point:

```graphql
{ organization(login: "nyulh") { projectV2(number: 172) {
    field(name: "Status") { ... on ProjectV2SingleSelectField { options { name } } } } } }
```

Verified 2026-08-19 on board 172, which returned: Icebox, INFO, New/Inbox, Blocked,
Researching, Todo/Ready, In Progress, Dev Done / Peer Review, PR Validated, Ready for QA,
In QA, QA Validated, Needs UAT, Done.

So the form takes a project number and then offers **that project's real statuses** as a
dropdown, and can default a new report's `order` to the status's index in that list. The
tool stays generic by asking the board rather than by inventing a taxonomy.

## Endpoints to add

Alongside the existing read and refresh routes in `ticket_aging_server.py`:

| Route | Does |
|---|---|
| `GET /api/board/<project>/statuses` | the board's Status options, in workflow order, for the dropdown |
| `POST /api/reports` | create a definition from the form |
| `PUT /api/reports/<slug>` | edit an existing definition |
| `DELETE /api/reports/<slug>` | remove a definition and its cache files |
| `PUT /api/reports/order` | reorder, rewriting every `order` value in tens |
| `POST /api/reports/validate` | run a candidate filter and report what it matches |

## Design decisions

**Validate the filter before saving.** The highest-value thing the form can do is run the
candidate filter against GitHub and show the live match count plus a few issue titles. A
filter typo otherwise saves cleanly and only surfaces as a wrong or empty report at the
next refresh. Costs 1 GraphQL point per test.

**Slug is derived once, then frozen.** Generate it from the label on create; keep it
immutable afterwards. The slug names the cache files (`<slug>-raw.json`,
`<slug>-view-model.json`, `<slug>.html`) and appears in API paths, so renaming a label
must not rename the slug or it orphans the cache and breaks links. Label editable, slug
not. Validate against the existing `SLUG_PATTERN` before touching the filesystem.

**Ordering stays in tens.** Drag-to-reorder rewrites every `order` value as 10, 20, 30 on
save, which keeps room to slot a report between two others by hand later.

**Writes stay local and atomic.** The server binds to 127.0.0.1 only, so there is no
authorization to design, but writes should still go to a temp file and be renamed into
place so a failed write cannot leave a half-written definition that breaks the sidebar.

**Git stays manual.** Saving a report writes an uncommitted file. The UI should not make
commits. It can show a count of uncommitted definitions as a nudge.

**Deleting removes cache too.** Otherwise `cache/` accumulates orphans that no
definition references.

## What the form collects

Mapping to the definition keys documented in `README.md`:

| Field | Key | Notes |
|---|---|---|
| Label | `copy.title` | also seeds the slug on create |
| Project | `project` | drives the status dropdown |
| Status | `measure_status` | dropdown from the board |
| Filter | `filter` | prefilled from the status, editable, testable |
| Headline | `copy.headline` | the report's H2 question |
| Description | `copy.lede` | |
| How to read it | `copy.notes` | authored notes; derived notes still append at normalize |
| Band width | `bin_days` | |
| Overflow at | `overflow_days` | optional |
| Reference line | `threshold_days` | |
| Position | `order` | defaults to the status's board position |

## Tradeoff to weigh before building

This is a few hundred lines of form UI, CRUD handlers, and validation, against a current
flow where a report is described in conversation and written in about a minute. It earns
its place if the goal is the team self-serving without going through Chris or an agent,
which has been the direction of this tool since it moved into the repo.

## Build options considered

1. **Full editor** — create, edit, delete, drag-to-reorder, status dropdown, filter
   validation. The version a teammate could use without knowing the JSON exists.
2. **Create and edit only** — same form and validation, but `order` is a number field
   rather than drag-to-reorder. Noticeably less work; reordering is rare once set up.
3. **Read-only settings view** — lists every definition with its filter, bands, copy, and
   raw JSON. No writes. Useful to judge whether the full editor is worth building.
4. **Leave it as JSON files** — no UI. Costs nothing, stays flexible, no self-service.

Not chosen yet.

---

# Plan: Publishing as a Standalone Open-Source Project

**Status:** Planned, not built (2026-08-19). Design only, discussed alongside the settings
interface above because the two constrain each other.

**Scope note (2026-08-19):** this whole section is for the open-source ambition, **not for
the NYU Langone deployment**. That deployment has no GitHub Pages and stays entirely
local: the local server for browsing, local JSON files for definitions, `gh` auth for
data, and published artifacts for sharing a single report. Nothing below is on the path to
a working tool here. It matters only if the tool is published for others to adopt.

## The idea

Make the tool generic enough to publish as its own GitHub project. Someone clones it into
their own repo, defines reports against their own board, and publishes the built pages to
GitHub Pages, refreshed either locally or by a scheduled Action.

## Can a settings UI on GitHub Pages write definitions back to the repo?

**No.** GitHub Pages is static file hosting with no server-side execution, so a page
served from it has no process to receive a write and no filesystem to write to.

This splits the tool cleanly, which is the right architecture anyway:

- **Settings UI is local authoring.** It runs against the local server, writes JSON to
  disk, and the author commits and pushes.
- **Pages is publishing.** It serves built HTML read-only.

**The existing template already degrades correctly for this.** It probes `/api/reports` at
load; on Pages nothing answers, so it falls back to the data island baked into the page
and hides both the sidebar and the Refresh button. No change is needed to "turn off"
refresh in the published context.

### The option deliberately rejected

A Pages page could write definitions by calling the GitHub REST contents API directly.
That requires a GitHub token in the browser, in `localStorage`, on a page whose source
anyone can read. It is a bad trade for saving a `git push`. Local authoring plus push is
simpler and safer. Do not build this.

## Gaps between the current tool and a publishable project

**Static mode has no navigation.** Falling back to the data island hides the sidebar
entirely, so a Pages deployment of five reports is five unlinked pages. A published
version needs either a static index page or a nav baked in at build time from the
definitions. This is new work.

**Hardcoded identity.** As of 2026-08-19 the tool assumes one org and one repo:

- `REPO_OWNER` and `REPO_NAME` in `status_history.py`, 4 references total
- `organization(login: ...)` in the item query, which fails for user-owned projects and
  needs a `user(login: ...)` variant
- The issue URL built in `normalize_report()`

These would move to a `config.json` beside `definitions/`, with first-run setup.

**Scheduled refresh needs the right token.** A cron workflow can run `refresh` and commit
the rebuilt HTML, but **the default `GITHUB_TOKEN` cannot read Projects v2**. Org-level
project data requires a PAT or GitHub App with `read:project` stored as a repo secret.
This is the most likely setup failure and belongs prominently in the project README.

## Authentication: one transport, two credential sources

Decided 2026-08-19. Supersedes an earlier note in this plan that framed `gh` and token
auth as two request paths.

### The correction that led here

A PAT works fine against the GraphQL API, Projects v2 included. Verified by POSTing to
`https://api.github.com/graphql` with a bearer token and no `gh` in the request path. The
real constraint is scope, not client:

| Token | Reads Projects v2 |
|---|---|
| Actions default `GITHUB_TOKEN` | no |
| Classic PAT with `project` / `read:project` | yes |
| Fine-grained PAT with org Projects read | yes |
| GitHub App with `read:project` | yes |

### The design

Resolve a **token** from one of two sources, then always make the same HTTP request.
`gh` becomes a credential source, not a request path, which is fewer moving parts than
the current `subprocess.run(["gh", "api", "graphql", ...])` and removes the need for a
second transport when CI support lands.

```
GITHUB_TOKEN env var  ->  else  ->  gh auth token  ->  else  ->  clear error
```

- Replaces `run_graphql()` in `ticket_aging.py` and `_fetch_batch()`'s call in
  `status_history.py` with a single shared `github_graphql(query)` using stdlib `urllib`.
- The error when neither source resolves must name the fix directly: run `gh auth login`
  and ensure the `project` scope, or set `GITHUB_TOKEN`.
- Verified 2026-08-19 that `gh auth token` returns a credential that works as a plain
  bearer, on an account carrying the `project` scope. This means **the token path is
  testable locally with `GITHUB_TOKEN=$(gh auth token)`**, so it does not ship untested
  merely because the maintainer cannot create a PAT.

**Known limitation to document:** this hardcodes `api.github.com`. `gh api` would honour a
configured enterprise host; raw `urllib` will not. Fine for github.com, needs host config
for GHES.

### Phasing

**MVP: `gh` auth is the documented, supported path.** Requirement is the `gh` CLI,
authenticated, with the `project` scope. Nothing in the tool handles or stores a
credential itself. `GITHUB_TOKEN` is implemented and tested but documented only as the CI
route.

**Phase 2: GitHub Actions.** No code change, because the env var path already exists. It
is a workflow file, a repo secret, and documentation.

**Constraint on Phase 2 for this deployment.** A scheduled workflow needs a token secret
with `read:project`, and the auto-injected `GITHUB_TOKEN` cannot supply it. Chris cannot
currently create a PAT, so unless NYU Langone will issue a fine-grained PAT or a GitHub
App for this, **auto-refresh is a capability adopters get and this deployment does not**.
Local refresh stays the path here. Confirm with security before planning around it.

## Warning for adopters: Pages sites are public

Access control for GitHub Pages exists only on GitHub Enterprise Cloud. On Pro or Team, a
site built from a **private** repo is still served publicly.

These reports carry issue numbers, full issue titles, and timing data for internal work,
so any adopter needs to weigh that before publishing. It does not apply to the NYU
Langone deployment, which has no Pages and shares individual reports as private
artifacts instead.

## Sequencing note

The settings interface should be built before or alongside genericization, not after. Its
form fields are where a project number, owner, and repo would naturally be collected, so
building it against hardcoded identity would mean rebuilding parts of it later.

Because this deployment is local-only, the settings interface is the entire near-term
scope. Genericizing identity, the `urllib` auth swap, Pages navigation, and Actions are
all publishing concerns that can wait until there is a reason to publish.

---

# Plan: What the Report Form Should Expose

**Status:** Planned, not built (2026-08-19). Follows the settings interface plan above; the
connection half of that is now built, the report editor is not.

## Already customizable

Before adding knobs, what a definition can set today:

| Pipeline | Copy |
|---|---|
| `connection`, `filter`, `measure_status` | `title`, `headline`, `lede` |
| `bin_days` (range per bar) | `stat_noun`, `chart_sub` |
| `overflow_days` (where bars stop) | `chart_title`, `table_title` |
| `threshold_days` (the reference line) | `notes` (authored; derived ones append) |
| `batch_min`, `order` | |

Two things often asked for are already here: **range per bar** is `bin_days`, and the
**threshold indicator** is `threshold_days`. Bar count is derived from `bin_days` plus
`overflow_days` rather than set directly, which is the right way round: it cannot produce a
chart whose bars do not tile the range.

The five existing reports differ mainly in filter, bins, threshold, and copy. That is the
evidence for which knobs earn their place.

## Worth adding

**Business days vs calendar days.** Every report carries a standing caveat that durations
are wall-clock, so a 21-day wait spans three weekends. For pure-latency columns (Peer
Review, Ready for QA) business days is arguably the honest measure. This removes a caveat
rather than adding one. Key: `count_days: "calendar" | "business"`.

**A percentile stat tile.** The four tiles are hardcoded: count, median, oldest,
over-threshold. The 85th percentile is the standard Kanban service-level number and is far
more useful to quote than a median ("85% clear QA within N days"). Key: `percentile: 85`.

**Emphasis past the threshold.** Every bar is currently one blue, so reading "how much is
over the line" means tracing the dashed rule. Bars past `threshold_days` should take a
distinct treatment. Probably a default rather than a setting.

**Table sort.** Always days-descending today. Right for a backlog; In Progress may want
most-recently-started first. Key: `sort: "days" | "entered" | "number"`.

## Worth considering

- **Assignee column** in the table. The "who is sitting on this" answer for In Progress and
  Peer Review, but it needs extra fields per issue in the fetch, so it is not free.
- **Selectable stat tiles** from a small vocabulary (count, median, percentile, oldest,
  over-threshold) instead of the fixed four.
- **Disabling batch-day flagging.** `batch_min` can only be set very high to suppress it;
  accepting `null` would be clearer.

## Worth resisting

- **Chart type as a config key.** A different chart is a different template, not a setting.
- **Per-report colors.** Breaks the visual system for no analytical gain.
- **Manual y-axis max or tick step.** Auto-derived is correct; a manual bound invites
  truncated bars that misrepresent the data.

## Form design consequence

Every knob is something the form must render, validate, and document, and that a teammate
must understand. Group the fields rather than listing twenty: **Data** (connection, filter,
status), **Scale** (bins, overflow, threshold), **Copy** (title, headline, lede, notes),
with anything else behind an Advanced disclosure. Most keys should stay optional with good
defaults.

## The stronger idea: an aging WIP scatter

Not a per-report setting. The canonical companion to these histograms is a single chart
with every column on the x-axis, one dot per ticket, age on the y-axis, and percentile
bands drawn across it. It answers "where is everything right now, and what is old" at a
glance.

Five per-column histograms structurally cannot show this, because each only knows its own
column. This is a second view rather than a sixth report, and it is the strongest argument
for keeping the per-report form small: the cross-cutting question does not belong in it.

## Report form MVP (specified 2026-08-19)

Chris's scope for the first version of the report editor. Deliberately small; the
"worth adding" list above is deferred to an issue queue once the tool moves to its own
repository.

### Fields

**Configuration**

| Form field | Definition key | State |
|---|---|---|
| Label (sidebar) | `copy.title` | exists |
| Filter query (text) | `filter` | exists; needs the Test button pattern from the connection form |
| Range per bar, days (int) | `bin_days` | exists |
| Max bars (int) | *new*, see below | needs deciding |
| Threshold indicator, days (int) | `threshold_days` | exists |

**Details**: Title -> `copy.headline`, Description -> `copy.lede`. Both exist.

**Chart**: Chart title -> `copy.chart_title`, Chart description -> `copy.chart_sub`. Both exist.

**Table**: Table title -> `copy.table_title` exists. **Table description does not exist**;
it is currently auto-generated ("Dates in red mark days when several tickets entered at
once..."). Add `copy.table_sub`, with the generated text as the fallback.

**Info panel (optional)**: a rich-text field. See the storage note below.

### Settled and built 2026-08-19

Three of the open questions below were decided and implemented before the form exists,
since they are pipeline concerns rather than form concerns:

- **`max_bars` replaces `overflow_days`**, counting banded bars only. The overflow bucket
  is appended beyond them, and only when data actually runs that far.
- **The status clause is generated, not typed.** A definition sets `measure_status`, and
  the filter is composed as `status:"<measure_status>" <additional_filter>`. The form field
  is therefore labelled **"Additional Filter"**. This removes a class of definition where
  the filter's status and the measured status silently disagree, which nothing caught
  before. The composed filter is still displayed in full on the report page, so the set
  stays reproducible in the Projects UI.
- **`copy.table_sub` added**, with the previously generated text as its fallback.

The capability given up by composing the filter: a report can no longer select a set by one
criterion while measuring entry into an unrelated status (for example, all open bugs aged
from when they entered In Progress). No current report does this, and the coupling is what
makes these reports a coherent type.

### Max bars is not the same as overflow_days

`overflow_days` is expressed in **days** ("stop banding at day 14"). Max bars is a
**count**. They relate as `max_bars * bin_days = overflow_days`, and a bar count is
arguably the better control, since bar count is what governs legibility.

One decision to pin before building: **does `max_bars` include the overflow bucket?**
Recommendation: no. `max_bars` counts banded bars, and the overflow bucket is appended
beyond it only when data runs past the range. In Progress would then read `bin_days: 1,
max_bars: 14`, producing 14 daily bars plus a `14+` bucket.

Leaving `max_bars` unset keeps today's behaviour for the other four reports: bands run to
the oldest ticket with no overflow bucket.

### Two fields missing from the MVP list that the form still needs

**`measure_status`.** The filter selects the tickets; `measure_status` is the status whose
entry timestamp the clock runs from. It cannot be reliably derived from the filter, which
may contain no status clause or several. It must be its own field, and should be a dropdown
populated from the connection's board, which the connection test already fetches.

**`connection`.** Required once more than one connection exists. May default and stay
hidden while there is only one.

`order` and `batch_min` can stay out of the MVP form: new reports append at the end, and
`batch_min` has a sensible default.

### Info panel: store Markdown, not HTML

Notes today are plain text, escaped before rendering, then `**bold**` converted. That
escaping is what makes it impossible for note text to inject markup. A WYSIWYG emitting raw
HTML removes that guarantee, and these pages are published as artifacts and shared, so
injected markup would travel with them. Storing HTML means writing and maintaining a
sanitizer allowlist.

**Recommendation: the WYSIWYG stores Markdown.** Most rich-text editors can emit it. It
keeps the same authoring experience, keeps definitions diffable in git (which now matters,
since they are committed), and needs no sanitizer. Render a restricted subset: bold,
italic, lists, links, inline code.

If raw HTML is chosen instead, sanitize at the **normalize** stage rather than in the
template, so what lands in the view model is already safe and the view stays dumb.

### Authored vs derived notes

The derived notes (batch-move days, tickets with no entry event, "nothing is estimated")
are computed per refresh and cannot be authored. Keep them as their own list below the info
panel rather than merging the two, or a refresh will appear to rewrite the author's text.

### Validation the form should do

- Filter: a Test button reporting live match count and sample titles, as the connection
  form does.
- `bin_days` and `max_bars`: at least 1. Warn past roughly 40 bars, where the chart stops
  being legible.
- `threshold_days`: warn when it exceeds `max_bars * bin_days`, since the reference line
  would fall outside the banded range. The renderer clamps it, but silently.
- Slug: derived from Label on create, then frozen, per the settings-interface plan above.

---

# Feature: Per-connection authentication

**Status:** Logged, not built (2026-08-19).

## What `gh` auth is

Verified on this machine 2026-08-19. `gh` is a credential **store**, not an auth type. It
holds whatever you logged in with:

| Account | Token prefix | Type |
|---|---|---|
| Chris-Albrecht_NYULH | `gho_` | OAuth user-to-server token, GitHub CLI OAuth app, browser/device flow |
| KeyboardCowboy | `ghp_` | classic personal access token |

So "gh auth vs a PAT" is a false choice: `gh auth login` can produce either, and a PAT can
be handed to it with `--with-token`.

## The assumption to correct

Checking the repo out into one folder per account does **not** scope auth. `gh` config
lives at `~/.config/gh`, machine-global, with a single *active* account and no
per-directory notion. Two checkouts would share whichever account was last switched to,
producing plausible-looking results from the wrong account. Separate checkouts give
separate report definitions, not separate credentials.

## The design

Auth belongs on the **connection**, which already names the owner, repo, and board. One
checkout can then hold reports for a personal account and a work account at once, and the
sidebar already groups reports by connection.

`gh auth token --user <login>` retrieves a specific account's token and was verified
against both accounts above, so the hook already exists.

A connection gains an optional auth source:

```json
{ "id": "personal", "owner": "KeyboardCowboy", ...,
  "auth": { "gh_account": "KeyboardCowboy" } }
```

or, for CI and for anyone not using `gh`:

```json
{ "auth": { "token_env": "GH_TOKEN_PERSONAL" } }
```

Resolution order, per connection:

1. `auth.token_env` -> read that environment variable
2. `auth.gh_account` -> `gh auth token --user <login>`
3. neither -> `gh auth token` for the active account (today's behaviour)

**Hard constraint: `connections.json` is committed.** It can therefore store only a
*reference* to a credential (an account login or an environment variable name), never a
token value. The settings form must not accept a pasted token, and the connection test
should report which account it resolved so a silent fallback to the wrong one is visible.

## Composes with the urllib swap

The earlier plan replaces `subprocess.run(["gh", "api", "graphql", ...])` with a stdlib
request against a resolved token. That refactor and this feature are the same shape:
resolve a token, then make the request. Doing them together is less work than either
alone, and it is what lets an adopter run without `gh` installed at all.

## Rejected: active account as a visibility filter

Considered 2026-08-19: resolve the active `gh` account, then show only the connections and
reports that account can reach, so switching accounts swaps the whole workspace.

Not adopted. Auth and visibility are separate concerns, and coupling them makes the tool
worse in four ways:

1. **"Accessible" costs a request per connection.** Verified 2026-08-19: the personal
   account cannot resolve `nyulh` at all ("Could not resolve to an Organization"), so
   accessibility is only knowable by asking GitHub, and the answer arrives as an error
   rather than a boolean. Six connections would mean six calls before the sidebar renders.
2. **Per-connection auth removes the reason to switch.** If each connection resolves its
   own token, work and personal reports are live in the same session. Hiding one set to see
   the other is strictly less useful than showing both.
3. **`gh auth switch` is machine-global.** Using it as a view filter here would change the
   behaviour of every other `gh`-using tool on the machine, which is a large side effect
   for a display preference.
4. **The files are committed.** `definitions/` and `connections.json` travel on clone, so a
   teammate would see content the owner's local `gh` state was hiding, with nothing
   explaining the difference.

The underlying need, keeping three contexts from cluttering one sidebar, is real. Two
cleaner answers:

- **Separate repositories per context.** Right for organisational separation: Langone
  reports in the Langone wiki, personal ones elsewhere. Genuinely separate and committed
  separately, with no hidden state.
- **A show/hide toggle by connection**, stored as a local UI preference rather than derived
  from auth.

A connection whose auth fails should report that plainly ("Cannot reach this board as
KeyboardCowboy") rather than vanishing. Silent invisibility is the same failure mode as a
misspelled status or a silently repointed board: plausible-looking output from the wrong
premise.

---

# Plan: Accounts, Projects, and Dropping the Repo

**Status:** Planned, not built (2026-08-19). A rename and a model change; supersedes parts
of the connection model shipped earlier the same day.

## The model

Three levels, replacing the flat "connection":

```
Account   credential only: a gh account login or an env var name, never a token
  Project  owner + owner type + project number, authenticated by one account
    Report  measure_status, additional_filter, scale, copy
```

"Connection" is renamed **Project**, which is what it actually is. `repo` disappears; see
below.

## Repo is not needed and should be removed

Verified 2026-08-19.

`projectV2.repositories` exists and lists a project's linked repositories (board 172
returns exactly 1, `nyulh/ContentHub-Board`), so repos *can* be inferred. But the better
answer is that the tool should not carry a repo at all.

Timelines are currently fetched with `repository(owner, name) { issue(number) }`, which
**hardcodes a single-repo assumption**. Projects v2 boards can hold issues from many
repositories, so that is a latent bug, not just a missing feature.

Issues can instead be fetched by node id, which is repo-agnostic by construction:

```graphql
nodes(ids: [...]) {
  ... on Issue { number url repository { nameWithOwner } timelineItems(...) }
}
```

Verified at cost 1 for a three-issue batch, returning status events, each issue's `url`,
and its owning repository. Consequences:

- `repo` leaves the stored model entirely.
- Issue URLs come from the API rather than being constructed from config, so they are
  right even when a board spans repositories.
- The item query already returns issue node ids, so this is a change to
  `status_history.fetch_histories` and its caller, not a new round trip.

## Sidebar: dropdowns, not grouping

Replace the connection grouping with up to two filters above the report list:

1. **Account** — shown only when more than one account is configured.
2. **Project** — shown only when the selected account has more than one project.

Both collapse to nothing in the common single-account, single-project case, so the sidebar
looks as it does today until there is a reason for it not to.

This also removes the need to switch the active `gh` account: `gh auth token --user
<login>` returns a specific account's token without changing which account is active,
verified 2026-08-19. The account dropdown filters the view; it does not change machine
state.

## Project picker: typeahead, not a select

`projectsV2(first:n, query:"...")` lists an owner's projects for 1 point, so the form can
offer real projects rather than asking for a number from a URL.

**But `nyulh` returned 205 projects**, many of them "@someone's untitled project". A plain
`<select>` of that length is unusable. The field needs a search box that passes its input
to the `query:` parameter and shows matches, not a dropdown of everything.

Once a project is chosen, its statuses come from the same place they do today, and its
linked repositories can be shown as read-only context rather than stored.

## Migration

Existing `connections.json` records carry `owner`, `owner_type`, `repo`, `project`. The
migration keeps everything but `repo`, splits the credential out into an account record
defaulting to the active `gh` account, and renames the file. Report definitions reference
`connection: "<id>"`, which becomes `project: "<id>"`; the key rename needs the same
clear-error treatment the removed `filter` key got.
