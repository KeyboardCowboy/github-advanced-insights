#!/usr/bin/env python3
"""Named projects: which account and Projects v2 board a report reads.

A report is only meaningful against one board, because its `measure_status` and
filter are written in that board's own vocabulary. So a project is a named,
first-class thing and every report references one by id, rather than the whole
tool sharing a single implicit target that can be repointed underneath it.

**No repository is stored.** A Projects v2 board can hold issues from many
repositories, and issues are fetched by node id, so the tool never needs to know
where an issue lives. Each issue's repository and URL come back from the API.

Projects live in `projects.json` beside this file, which is committed:
configuration a teammate needs on clone, not derived data. Reports stay one file
each in `definitions/`, so two people adding two reports still touch two
different files and merge cleanly.
"""
import json
import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECTS_PATH = BASE_DIR / "projects.json"
LEGACY_CONNECTIONS_PATH = BASE_DIR / "connections.json"

OWNER_TYPES = ("organization", "user")

# GitHub logins and repository names.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
# Connection ids name nothing on disk today, but they appear in URLs and in
# every definition, so keep them to the same safe shape as report slugs.
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

EMPTY = {"projects": [], "default_project": None}


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def load_projects():
    """The whole projects document, migrating a legacy config.json if needed."""
    if not PROJECTS_PATH.exists() and LEGACY_CONNECTIONS_PATH.exists():
        _migrate_connections()
    if not PROJECTS_PATH.exists():
        return dict(EMPTY)
    return {**EMPTY, **json.loads(PROJECTS_PATH.read_text())}


def list_projects():
    return load_projects()["projects"]


def get_project(project_id=None):
    """One project by id, or the default when id is None.

    Returns None rather than raising, so callers can decide whether a missing
    project is a hard error (running a report) or an empty state (the
    settings page on a fresh clone).
    """
    document = load_projects()
    wanted = project_id or document.get("default_project")
    if not wanted:
        # A single project needs no default set to be unambiguous.
        return document["projects"][0] if len(document["projects"]) == 1 else None
    return next((c for c in document["projects"] if c["id"] == wanted), None)


def account_for(project):
    """The account record a project reads through, or None for the default."""
    from accounts import get_account

    return get_account((project or {}).get("account"))


def owner_scope(project):
    """The GraphQL root field for this owner: `organization` or `user`."""
    return "user" if project.get("owner_type") == "user" else "organization"


# --------------------------------------------------------------------------
# Validation and writing
# --------------------------------------------------------------------------

def validate_project(values, existing_ids=(), is_new=False):
    """Return every problem with a candidate project, empty when usable."""
    problems = []

    project_id = str(values.get("id", "")).strip()
    if not project_id:
        problems.append("Connection id is required.")
    elif not ID_PATTERN.match(project_id):
        problems.append(
            f"'{project_id}' is not a valid id. Use lowercase letters, numbers, "
            "and hyphens."
        )
    elif is_new and project_id in existing_ids:
        problems.append(f"A project called '{project_id}' already exists.")

    owner = str(values.get("owner", "")).strip()
    if not owner:
        problems.append("Owner is required.")
    elif not NAME_PATTERN.match(owner):
        problems.append(f"'{owner}' is not a valid GitHub owner name.")

    if values.get("owner_type") not in OWNER_TYPES:
        problems.append(f"Owner type must be one of: {', '.join(OWNER_TYPES)}.")

    try:
        if values.get("project_number") is None or int(values["project_number"]) < 1:
            raise ValueError
    except (TypeError, ValueError):
        problems.append("Project number must be a positive whole number.")

    return problems


def save_project(values):
    """Create or update one project by id. Returns the saved project."""
    document = load_projects()
    existing_ids = [c["id"] for c in document["projects"]]
    is_new = str(values.get("id", "")).strip() not in existing_ids

    problems = validate_project(values, existing_ids, is_new)
    if problems:
        raise ValueError("\n".join(problems))

    saved = {
        "id": str(values["id"]).strip(),
        "label": str(values.get("label") or values["id"]).strip(),
        "owner": str(values["owner"]).strip(),
        "owner_type": values["owner_type"],
                "project_number": int(values["project_number"]),
    }

    if is_new:
        document["projects"].append(saved)
    else:
        index = existing_ids.index(saved["id"])
        document["projects"][index] = saved

    if not document.get("default_project"):
        document["default_project"] = saved["id"]

    _write(document)
    return saved


def delete_project(project_id):
    """Remove a project. Refuses while any report still references it."""
    from ticket_aging import reports_using_project

    users = reports_using_connection(project_id)
    if users:
        raise ValueError(
            f"{len(users)} report(s) still use this project: {', '.join(users)}. "
            "Point them elsewhere first."
        )

    document = load_projects()
    document["projects"] = [c for c in document["projects"] if c["id"] != project_id]
    if document.get("default_project") == project_id:
        document["default_project"] = (
            document["projects"][0]["id"] if document["projects"] else None
        )
    _write(document)


def _write(document):
    """Persist atomically: a half-written file would break every report at once."""
    temp_path = PROJECTS_PATH.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(document, indent=2) + "\n")
    os.replace(temp_path, PROJECTS_PATH)


def _migrate_connections():
    """Fold connections.json into projects.json, dropping the repository.

    `repo` is not carried over: issues are fetched by node id now, so a board
    that spans repositories works without the tool naming one.
    """
    legacy = json.loads(LEGACY_CONNECTIONS_PATH.read_text())
    migrated = []
    for record in legacy.get("connections", []):
        migrated.append({
            "id": record["id"],
            "label": record.get("label", record["id"]),
            "owner": record["owner"],
            "owner_type": record.get("owner_type", "organization"),
            "project_number": record["project"],
        })
    _write({
        "projects": migrated,
        "default_project": legacy.get("default_project")
        or (migrated[0]["id"] if migrated else None),
    })
