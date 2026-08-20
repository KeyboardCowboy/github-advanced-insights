# Work Item Age

[![Tests](https://github.com/KeyboardCowboy/github-advanced-insights/actions/workflows/ci.yml/badge.svg)](https://github.com/KeyboardCowboy/github-advanced-insights/actions/workflows/ci.yml)

A small, local tool that answers one question about a GitHub Projects v2 board:
**how long has each unfinished ticket been sitting where it is?**

Point it at a board, define a report per column, and it charts the distribution of how long
tickets have waited there, with a table of every ticket and the notes needed to read the
numbers honestly.

No database, no hosting, no build step. Python's standard library and a GitHub token.

## The metric: work item age, not cycle time

These reports measure **work item age**: elapsed time on work that is *still unfinished*,
counted from when a ticket entered its current column up to now. Every ticket shown is one
you can still act on.

That is deliberately not cycle time, and the difference changes what you do with it:

| | Measures | Available | Use |
|---|---|---|---|
| **Cycle time** | start → finish, on completed work | after delivery | lagging: reports what already happened |
| **Lead time** | request → delivery, on completed work | after delivery | lagging: what the requester experienced |
| **Work item age** | entry → now, on unfinished work | continuously | leading: what to intervene on today |

Cycle time can only be computed once a ticket is done, by which point nothing can be done
about it. Age is the actionable counterpart.

Canonical work item age runs from when an item entered the workflow as a whole. This
measures it per column, the current unbroken stint in one status, because the question is
"where is work piling up," not "how old is this overall." A ticket that left a column and
came back is aged from its latest entry.

## Requirements

- Python 3.9 or newer. No packages to install.
- A GitHub token with the `project` scope.

The token comes from the [`gh` CLI](https://cli.github.com) by default, so an authenticated
`gh` needs no configuration. `gh` is **not** required: an account can name an environment
variable instead, which is what lets this run in CI or a container.

Note that the token GitHub Actions injects automatically **cannot** read Projects v2. A
scheduled refresh needs a PAT or GitHub App with `read:project` as a secret.

## Getting started

```
python3 ticket_aging_server.py
```

Open <http://localhost:8080>. On a fresh clone with no configuration, the settings page is
where you start:

1. **Add an account** naming where its token comes from, or skip it to use whatever `gh` is
   logged in as.
2. **Add a project**: the owner, whether that owner is an organization or a user, and the
   board number. The search box finds boards by title, which matters when an owner has
   hundreds.
3. **Create a report**: pick a status, and the tool ages every ticket in it.

The server binds to `127.0.0.1` only. Refreshing reads with your credentials, so do not
expose it to a network without putting authentication in front of the refresh route.

## How it works

Three stages, kept separate so refreshing data never rebuilds the interface:

| Stage | Reads | Writes |
|---|---|---|
| `fetch` | GitHub | `cache/<slug>-raw.json` |
| `normalize` | raw JSON | `cache/<slug>-view-model.json` |
| `build` | template + view model | `cache/<slug>.html` |

```
python3 ticket_aging.py refresh <slug>    # all three
python3 ticket_aging.py report <slug>     # terminal view, no API calls
python3 ticket_aging.py list              # what is defined
```

Every derived value — stats, histogram bins, batch flags, bar widths — is computed in
`normalize`. The template does geometry and nothing else, so changing how a number is
calculated never means touching the interface.

**`views/ticket-aging.html` runs in two contexts from one file.** At load it probes
`/api/reports`: if the local server answers it runs as an app with a sidebar and a refresh
button; if nothing answers it falls back to the view model baked into its data island. That
second path is what makes a built `cache/<slug>.html` shareable as a standalone file. The
template also renders correctly against an *empty* model, showing zeros, so it can be
opened before anything has been fetched.

## The workspace

Code and data are separate. Everything specific to *your* installation lives in
`workspace/`, which this repository gitignores:

```
workspace/
  accounts.json     personal    which credential you use
  projects.json     shareable   which boards to read
  definitions/      shareable   the reports themselves
  cache/            derived     regenerated on demand
```

It is created on first run. Set `GH_INSIGHTS_HOME` to keep it somewhere else, such as a
checkout of a team configuration repository.

Because the workspace is gitignored here, pulling an update never conflicts with your
configuration, and contributing a change never carries your board identifiers with it.

### Sharing reports with a team

Make the workspace its own repository:

```
cd workspace && git init && git remote add origin <your-config-repo>
```

Teammates clone this tool, clone that workspace into it, and add their own account.

**One part stays personal.** `accounts.json` maps an account id to *your* credential, so
sharing it would hand a teammate your login rather than theirs. A shared `projects.json`
names an account by **id**; each person's local `accounts.json` satisfies that id with
their own `gh` login or environment variable. The id is the contract, the credential is
not. A `.gitignore` written inside the workspace enforces this, so nobody has to remember.

## Configuration files

Editable through the UI or by hand; the two routes write the same files.

- **`accounts.json`** — where credentials come from. Records a *source*, never a token:
  a `gh` login (`gh_account`) or an environment variable name (`token_env`).
- **`projects.json`** — the boards. Owner, owner type, project number, and which account
  reads it. **No repository**, because a board can span several and issues are fetched by
  node id.
- **`definitions/*.json`** — one file per report, so two people adding two reports touch
  two different files and merge cleanly.

A report definition:

```json
{
  "project": "my-board",
  "order": 20,
  "measure_status": "In Progress",
  "additional_filter": "is:open -type:Epic,Feature",
  "bin_days": 1,
  "max_bars": 14,
  "threshold_days": 14,
  "copy": { "title": "Work In Flight", "headline": "...", "info_panel": "..." }
}
```

| Key | What it does |
|---|---|
| `measure_status` | The status the report covers. The clock runs from entry into it, **and its filter clause is generated**, so the tickets shown and the moment measured can never disagree. |
| `additional_filter` | Further board-filter clauses, appended to the generated status clause. Same syntax as the Projects filter bar. |
| `bin_days` | Histogram band width. 1 to see individual days, 14+ for a backlog measured in months. |
| `max_bars` | Optional. How many banded bars to draw; anything past them collapses into one trailing bucket, added only when data runs that far. |
| `threshold_days` | Where the reference line is drawn: the point past which a wait stops looking normal for *that* column. |
| `batch_min` | Same-day entries at or above this count are flagged as a board sweep rather than individual hand-offs. |
| `copy.info_panel` | Optional prose in a small Markdown subset. Stored as Markdown rather than HTML so definitions stay diffable and authored text can never inject markup. |

Band width and the reference line are per report because columns run on different
timescales: a hand-off step is measured in days, a backlog in months. One setting for all
of them either collapses the fast columns into a single bar or spreads the slow ones across
twenty near-empty ones.

## Guardrails

The recurring failure mode with board data is plausible-looking output from a wrong
premise, so several checks exist specifically to make that loud:

- **Test filter** runs a candidate filter before saving and reports the match count.
- **Status is a dropdown read from the board**, so it cannot be misspelled. A wrong status
  would otherwise produce an empty report rather than an error.
- **Repointing a project at a different board** re-queries it and refuses when reports
  reference statuses the new board lacks, naming them.
- **Test account** reports which login a token resolved to, because a misconfigured account
  falls back to the active `gh` login and would otherwise look like success.
- Deleting an account or project is refused while anything references it.

## Caveats

- Durations are **wall-clock**, not business days. A three-week wait spans three weekends.
- The item query fetches the **first 100 matches**; past that it warns rather than
  truncating silently.
- Aging depends on recorded status events. Tickets whose board history predates them are
  excluded from counts and listed by number.

See `docs/github-api-notes.md` for the API details and `docs/roadmap.md` for what is
planned and what was deliberately rejected.

## Contributing

Issues use four types, applied automatically by the templates:

| Label | For |
|---|---|
| `#Bug` | Something is not working as designed |
| `#Feature` | A new capability, with a user story and justification |
| `#Task` | Generic work item |
| `#Documentation` | Notes, ADRs, and information about the project |

Punctuation groups labels that are not project-specific: `#` marks the issue type and `!`
marks something the issue needs before it can move, such as `!Needs Research`. Labels
without punctuation are project-specific, like `UI/UX`.

Templates live in `.github/ISSUE_TEMPLATE/` as plain markdown, so what you fill in is
exactly what the issue looks like. Their headings are the expected shape of a report; keep
them and delete any that genuinely do not apply.

The bug template asks *how* something fails, because this tool reads a live board and its
characteristic failure is output that looks entirely normal while resting on a wrong
premise. Silent wrongness is more urgent than a visible error.

## License

MIT. See `LICENSE`.
