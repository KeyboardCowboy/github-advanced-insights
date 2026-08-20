---
name: write-issue
description: Write, file, or rewrite a GitHub issue for the github-advanced-insights repo, including setting its labels and project board fields. Use this whenever the user asks to create a ticket, file an issue, log a bug, add a feature request, "add a ticket for this", or improve the wording of an existing issue — and also when a conversation surfaces something worth tracking and the user agrees to capture it. Covers the repo's issue templates, the punctuated label scheme, and the Advanced Insights Development project board.
---

# Writing issues for github-advanced-insights

An issue here is read later by someone with none of the context that produced it —
often the author, months on. The job is to leave them enough to act without
reconstructing the conversation.

## Read the template first

The templates live in `.github/ISSUE_TEMPLATE/`, as plain markdown:

| File | Type | Label applied |
|---|---|---|
| `1-bug.md` | Bug | `#Bug` |
| `2-feature.md` | Feature request | `#Feature` |
| `3-task.md` | Task | `#Task` |
| `4-documentation.md` | Documentation | `#Documentation` |

There is no template for an Epic, because an epic is written by hand once its
sub-issues are known. Label it `#Epic`, set its Issue Type to Epic, and give it a
table of its sub-issues with their dependency order, so a reader can tell where to
start. Link the children with `addSubIssue` rather than only listing them, so
GitHub tracks the progress.

**Read the file for the type you are writing, every time, and use its headings
verbatim.** Do not work from memory or from a copy embedded anywhere else. These
templates get edited, and an issue written against a stale idea of them creates
exactly the inconsistency they exist to prevent.

The headings in the template are the sections your body needs. Copy them at
whatever level the template uses; they are all `##` today, but read rather than
assume, since that is a formatting choice which can change. Where a template
offers a checkbox list, tick one from the list
rather than writing your own wording; those vocabularies stay scannable only if
everyone uses them.

Some fields exist for the person triaging rather than the person reporting. The
bug template's "Where it happens" is one: a user often cannot know which stage
broke, but you can, because you read the code before writing the issue. Fill in
what you actually determined and leave the rest.

If genuinely important material has no home in the template, add a `Notes`
section at the end rather than distorting a field to hold it.

## Verify before you write

The single biggest quality difference in this repo's issues is whether the claims
in them were checked. Read the code, run the query, reproduce the behaviour.

This is not ceremony. In practice it changes conclusions:

- An issue asserting a GitHub API limitation was **wrong**; one test showed the
  feature worked and the original note had compared mismatched baselines.
- A bug reported as one fault turned out to have **two independent causes**,
  found only because fixing the first one did not fix the symptom.
- A rewrite went through three passes because the first two described the
  mechanism inaccurately.

Cite what you found as `file.py:line`. A reader who can jump straight to the code
does not have to trust your summary.

## What makes these issues good

**Lead with the story, not the mechanism.** A title naming the internal cause
tells a reader nothing about whether they care. "Shared report files have no way
to reach each other" beats "Static builds have no navigation between reports".
Open the body with the situation someone is actually in.

**Name the decision the implementer will hit.** Most issues here contain a real
fork that has to be settled before code is written — where the files travel, what
happens when two saved preferences conflict, whether a value is validated on
restore. Surfacing it turns a vague ticket into a scoped one.

**Record rejected options with the reasoning.** An alternative dismissed without
its argument gets re-proposed by the next person. Under "Alternatives considered",
say what was rejected *and why*, so the decision holds.

**Flag silent wrongness.** This tool reads a live board, so its characteristic
failure is output that looks entirely normal while resting on a wrong premise: a
truncated result set, a stale age, a filter that silently matched nothing. That is
more urgent than a visible error, and the bug template's "How does it fail?" field
exists to capture it. Say so plainly when it applies.

**Give the reader real numbers.** "205 projects, most of them untitled" argues for
a search box. "An owner can have many projects" does not.

## Filing it

Create the issue with its type label:

```bash
gh issue create --repo KeyboardCowboy/github-advanced-insights \
  --title "..." --label '#Bug' --body-file <path>
```

Add a facet label too when one fits: `UI/UX` or `Architecture/DevOps`. Labels
without punctuation are project-specific; `#` marks the issue type and `!` marks
something the issue needs before it can move, such as `!Needs Research`.

Then set the project board fields. **A new issue is not added to the board
automatically**, so this step is not optional:

```bash
.claude/skills/write-issue/scripts/set_fields.sh <issue> <Issue Type> <Facet[,Facet]> <Status>
```

The script adds the issue to the board if it is missing, then sets all three.

| Field | Values |
|---|---|
| Issue Type | Epic, Feature Request, Task, Bug, Documentation |
| Facet | UI, Arch/DevOps, Docs (multi-select) |
| Status | Backlog, Ready, In Progress, In Review, Done |

New issues normally start in `Backlog`. Move to `In Progress` when you begin work
on one and `In Review` once it is committed — the board is only useful if it
tracks reality.

Priority exists as a field but is left to the user; do not guess a ranking.

## Referring to issues from commits

`Closes #N` only when the work is verifiable from the commit itself. For anything
that cannot be confirmed until it reaches GitHub — issue templates, workflow
files, anything the platform renders — use `Refs #N` and let a human close it.

**Check the number exists before you write it.** A `Closes #N` pointing at a
number nobody has used yet will silently close whatever unrelated issue is created
with that number later:

```bash
gh issue view <N> --repo KeyboardCowboy/github-advanced-insights --json number
```

If you are filing an issue and fixing it in the same stretch of work, create the
issue first so you are referring to a number that exists.
