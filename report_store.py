#!/usr/bin/env python3
"""Create, edit, and delete report definitions from the report form.

The store the report editor writes through. Definitions stay one JSON file each
in `definitions/`, so this reads and writes individual files rather than a
combined document: two people adding two reports still touch two different
files and merge cleanly.

Validation lives here rather than in the server so the CLI and the form cannot
disagree about what a valid report is.
"""
import json
import os
import re

from projects import get_project, list_projects
from ticket_aging import REPORTS_DIR, cache_paths

# Slugs name files and appear in URLs, so they are constrained before anything
# touches the filesystem.
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# Beyond roughly this many bars the chart stops being readable.
MAX_REASONABLE_BARS = 60

# Keys the form owns. Anything else already in a definition is preserved on
# save, so a hand-edited key is not silently dropped by a round trip.
COPY_FIELDS = (
    "title", "headline", "lede",
    "chart_title", "chart_sub",
    "table_title", "table_sub",
    "info_panel",
)


def slug_from_title(title):
    """A filesystem-safe slug derived from a report's label."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(title).lower()).strip("-")
    return slug[:64] or "report"


def existing_slugs():
    return sorted(path.stem for path in REPORTS_DIR.glob("*.json"))


def load_raw(slug):
    """A definition exactly as stored, without defaults or resolved connection."""
    path = REPORTS_DIR / f"{slug}.json"
    return json.loads(path.read_text()) if path.exists() else None


def validate_report(slug, values, is_new):
    """Every problem with a candidate report, empty when it is usable."""
    problems = []

    if not SLUG_PATTERN.match(slug or ""):
        problems.append(
            f"'{slug}' is not a valid report id. Use lowercase letters, numbers, "
            "and hyphens."
        )
    elif is_new and slug in existing_slugs():
        problems.append(f"A report called '{slug}' already exists.")

    copy = values.get("copy") or {}
    if not str(copy.get("title", "")).strip():
        problems.append("Label is required.")

    if not str(values.get("measure_status", "")).strip():
        problems.append("Status is required.")

    project_id = values.get("project")
    if project_id and get_project(project_id) is None:
        known = ", ".join(p["id"] for p in list_projects()) or "none"
        problems.append(f"Project '{project_id}' does not exist. Known: {known}.")
    elif not project_id and get_project() is None:
        problems.append("A project is required, and there is no default to fall back on.")

    bin_days = _as_int(values.get("bin_days"))
    if bin_days is None or bin_days < 1:
        problems.append("Range per bar must be a whole number of days, at least 1.")

    max_bars = values.get("max_bars")
    if max_bars not in (None, ""):
        parsed = _as_int(max_bars)
        if parsed is None or parsed < 1:
            problems.append("Max bars must be a whole number, at least 1, or left empty.")
        elif parsed > MAX_REASONABLE_BARS:
            problems.append(f"Max bars above {MAX_REASONABLE_BARS} produces an unreadable chart.")

    threshold = _as_int(values.get("threshold_days"))
    if threshold is None or threshold < 0:
        problems.append("Threshold must be a whole number of days, 0 or more.")

    if _as_int(values.get("order")) is None:
        problems.append("Order must be a whole number.")

    return problems


def save_report(slug, values):
    """Create or update one definition. Returns what was written."""
    is_new = slug not in existing_slugs()
    problems = validate_report(slug, values, is_new)
    if problems:
        raise ValueError("\n".join(problems))

    # Start from what is on disk so keys the form does not manage survive.
    stored = load_raw(slug) or {}
    copy = dict(stored.get("copy") or {})
    for field in COPY_FIELDS:
        text = str((values.get("copy") or {}).get(field, "")).strip()
        if text:
            copy[field] = text
        else:
            copy.pop(field, None)

    # The form replaces the authored notes array with the info panel, so a
    # definition edited through the form stops carrying both.
    if copy.get("info_panel"):
        copy.pop("notes", None)

    max_bars = values.get("max_bars")
    stored.update({
        "project": values.get("project") or get_project()["id"],
        "order": _as_int(values.get("order")),
        "measure_status": str(values["measure_status"]).strip(),
        "additional_filter": str(values.get("additional_filter") or "").strip(),
        "bin_days": _as_int(values.get("bin_days")),
        "max_bars": None if max_bars in (None, "") else _as_int(max_bars),
        "threshold_days": _as_int(values.get("threshold_days")),
        "copy": copy,
    })
    stored.pop("filter", None)        # removed key; the status clause is generated
    stored.pop("overflow_days", None)  # superseded by max_bars

    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / f"{slug}.json"
    # Write to a sibling temp file and rename: a half-written definition would
    # break the sidebar for every report, not just this one.
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(stored, indent=2, ensure_ascii=False) + "\n")
    os.replace(temp_path, path)
    return stored


def delete_report(slug):
    """Remove a definition and the cache files derived from it."""
    path = REPORTS_DIR / f"{slug}.json"
    if not path.exists():
        raise ValueError(f"No report called '{slug}'.")
    path.unlink()
    # Orphaned cache would otherwise linger with no definition referencing it.
    for cache_file in cache_paths(slug).values():
        cache_file.unlink(missing_ok=True)


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
