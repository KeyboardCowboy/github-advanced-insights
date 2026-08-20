#!/usr/bin/env python3
"""Where this installation's data lives, as opposed to its code.

The tool and the data it operates on are different things with different
lifecycles. Code is shared with everyone who clones the project; accounts,
projects, reports, and cache belong to whoever is running it. Keeping them in
one directory gives a single boundary: `workspace/` is gitignored by the tool's
own repository, so an upstream pull never conflicts with local configuration and
a contributed change never carries someone's board identifiers with it.

That same boundary is what makes reports shareable. A team that wants everyone
looking at the same reports turns `workspace/` into its own repository and
pushes it; teammates clone the tool, clone the workspace into it, and are done.

Not everything in a workspace is shareable, though, and the split matters:

    projects.json    shareable   which boards to read
    definitions/     shareable   the reports themselves
    accounts.json    personal    which credential *you* use
    cache/           derived     regenerated on demand

A shared `projects.json` names an account by **id**; each person's local
`accounts.json` maps that id to their own credential. So the id is a contract
between teammates while the credential behind it stays personal. `ensure()`
writes a `.gitignore` inside the workspace enforcing that split, so a team that
runs `git init` there cannot accidentally publish one person's account mapping.

Set `GH_INSIGHTS_HOME` to keep a workspace somewhere else entirely, for instance
a checkout of a team configuration repository.
"""
import os
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent

# Written into a new workspace so the personal/derived files stay out of any
# repository made from it, without anyone having to remember.
WORKSPACE_GITIGNORE = """\
# Personal: maps an account id to *your* credential source. Sharing this would
# hand a teammate your login rather than theirs. The ids referenced by
# projects.json are the shared contract; this file is how you satisfy them.
accounts.json

# Derived: refetched on demand.
cache/
"""


def workspace_dir():
    """The active workspace, honouring GH_INSIGHTS_HOME."""
    override = os.environ.get("GH_INSIGHTS_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return TOOL_DIR / "workspace"


WORKSPACE = workspace_dir()
ACCOUNTS_PATH = WORKSPACE / "accounts.json"
PROJECTS_PATH = WORKSPACE / "projects.json"
DEFINITIONS_DIR = WORKSPACE / "definitions"
CACHE_DIR = WORKSPACE / "cache"


def ensure():
    """Create the workspace if it does not exist. Safe to call repeatedly."""
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    DEFINITIONS_DIR.mkdir(exist_ok=True)
    CACHE_DIR.mkdir(exist_ok=True)

    gitignore = WORKSPACE / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(WORKSPACE_GITIGNORE)


def describe():
    """Where the workspace is and how it was chosen, for startup output."""
    if os.environ.get("GH_INSIGHTS_HOME", "").strip():
        return f"{WORKSPACE} (from $GH_INSIGHTS_HOME)"
    try:
        return str(WORKSPACE.relative_to(Path.cwd()))
    except ValueError:
        return str(WORKSPACE)
