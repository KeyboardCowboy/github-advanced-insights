"""Shared setup for the test suite.

**Import this before anything from the tool.** `workspace.py` resolves its paths
from `GH_INSIGHTS_HOME` when it is first imported, so pointing the tool at the
fixtures has to happen before that import. Setting it here, at module level,
means any test module that starts with `from helpers import ...` is safe.

Without this a test would read the developer's own workspace: their real boards,
their real reports. The results would differ per machine and could quietly pass.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_WORKSPACE = REPO_ROOT / "tests" / "fixtures" / "workspace"
EXPECTED = REPO_ROOT / "tests" / "fixtures" / "expected"

# Must be set before the tool is imported anywhere in the process.
os.environ["GH_INSIGHTS_HOME"] = str(FIXTURE_WORKSPACE)
sys.path.insert(0, str(REPO_ROOT))

# Every fixture, so a test can assert it covered all of them rather than
# silently skipping one that was added later.
FIXTURE_SLUGS = sorted(p.stem for p in (FIXTURE_WORKSPACE / "definitions").glob("*.json"))


def run_cli(*args, workspace=None):
    """Run the CLI against a workspace and return the completed process.

    A subprocess rather than an import: it exercises the real entry point, and
    it lets each test use its own copy of the workspace without fighting over
    module-level path constants.
    """
    env = {**os.environ, "GH_INSIGHTS_HOME": str(workspace or FIXTURE_WORKSPACE)}
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "ticket_aging.py"), *args],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT)


class TemporaryWorkspace:
    """A throwaway copy of the fixture workspace.

    Normalize and build write into `cache/`, so tests that run them work on a
    copy. Otherwise a test run would leave the checked-out fixtures modified and
    the next run would not start from the same state.
    """

    def __enter__(self):
        self._dir = tempfile.mkdtemp(prefix="gh-insights-test-")
        self.path = Path(self._dir) / "workspace"
        shutil.copytree(FIXTURE_WORKSPACE, self.path)
        return self.path

    def __exit__(self, *exc):
        shutil.rmtree(self._dir, ignore_errors=True)
