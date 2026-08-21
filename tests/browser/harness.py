"""Server + browser lifecycle for the browser tests.

Each test class gets its own server on its own port, against its own throwaway
copy of the fixture workspace. That isolation is deliberate: these tests write
to disk, and a shared workspace would make them order-dependent -- the failure
mode where a suite passes when run whole and fails when one test is run alone.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_WORKSPACE = REPO_ROOT / "tests" / "fixtures" / "workspace"

try:
    from playwright.sync_api import expect, sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the machine, not the code
    PLAYWRIGHT_AVAILABLE = False
    # Re-exported so test modules can import it from here rather than from
    # playwright directly. A module-level `from playwright...` in a test file
    # raises at *collection* time, before any skip decorator is consulted, so
    # `python3 -m unittest discover` would error on a machine without it --
    # which is exactly the property the skipping exists to protect.
    expect = None

SKIP_REASON = ("Playwright is not installed. "
               "pip install -r requirements-dev.txt && playwright install chromium")


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE, SKIP_REASON)
class BrowserTest(unittest.TestCase):
    """Base class: a running server, a browser page, and a clean workspace."""

    # Slugs to run `normalize` for before the server starts. A report with no
    # view model is a legitimate state -- the page says "never fetched" and
    # offers Refresh -- but it is not the state to test charts and filters in.
    PREBUILD_REPORTS = ()

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="gh-insights-browser-")
        cls.workspace = Path(cls._tmp) / "workspace"
        shutil.copytree(FIXTURE_WORKSPACE, cls.workspace)
        cls._drop_derived_files()

        cls.prepare_workspace()

        if cls.PREBUILD_REPORTS:
            cls._build_models()

        cls.port = free_port()
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls._server = subprocess.Popen(
            [sys.executable, "-m", "tests.browser.serve_stub"],
            cwd=REPO_ROOT,
            env={**os.environ,
                 "GH_INSIGHTS_HOME": str(cls.workspace),
                 "PORT": str(cls.port)},
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        cls._await_server()

        cls._playwright = sync_playwright().start()
        cls.browser = cls._playwright.chromium.launch()

    @classmethod
    def _drop_derived_files(cls):
        """Start from the committed raw files and nothing else.

        `build_fixtures.py --update-goldens` leaves view models behind in the
        fixture workspace. They are gitignored, so CI never sees them -- but
        copytree does, and then every report has data regardless of what
        PREBUILD_REPORTS says. That is the worst shape of test bug: green on the
        machine that wrote it, and a different result on a clean checkout.
        """
        for derived in (cls.workspace / "cache").glob("*-view-model.json"):
            derived.unlink()

    @classmethod
    def prepare_workspace(cls):
        """Hook to seed the workspace before the server reads it.

        The fixture workspace has one project and no accounts, because that is
        the smallest thing the pipeline needs. Parts of the interface only
        appear with more than one -- the sidebar hides a filter over a single
        value, on the grounds that a choice of one is not a choice -- so a class
        testing those has to build the situation it needs.
        """

    @classmethod
    def write_workspace_json(cls, name, data):
        (cls.workspace / name).write_text(json.dumps(data, indent=2))

    @classmethod
    def _build_models(cls):
        """Run the real pipeline so the page has something to draw.

        Uses `normalize`, not a hand-written view model: a fixture that drifts
        from what the pipeline actually produces would make these tests pass
        against a page the real tool never renders.
        """
        for slug in cls.PREBUILD_REPORTS:
            result = subprocess.run(
                [sys.executable, str(REPO_ROOT / "ticket_aging.py"), "normalize", slug],
                cwd=REPO_ROOT, capture_output=True, text=True,
                env={**os.environ, "GH_INSIGHTS_HOME": str(cls.workspace)})
            if result.returncode != 0:
                raise RuntimeError(f"could not build {slug}:\n{result.stderr}")

    @classmethod
    def _await_server(cls, timeout=15):
        """Poll until the port answers, rather than sleeping a hopeful amount.

        If the server died on startup its output is the useful thing to show,
        so surface that instead of a bare connection error.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cls._server.poll() is not None:
                raise RuntimeError(
                    "test server exited before it accepted a connection:\n"
                    + (cls._server.stdout.read() if cls._server.stdout else ""))
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.5):
                    return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError(f"test server did not start within {timeout}s")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "browser", None):
            cls.browser.close()
        if getattr(cls, "_playwright", None):
            cls._playwright.stop()
        cls._server.terminate()
        try:
            cls._server.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover
            cls._server.kill()
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        # Short, because everything here is local and instant. The default 30s
        # turns one wrong selector into a run that looks hung rather than failed.
        self.page.set_default_timeout(8000)
        # An uncaught exception mid-render leaves a half-built page, which then
        # shows up as a puzzling missing element rather than as the error it is.
        # This is the signal that would have caught the null-dereference from
        # the settings rename, so it is kept separate from resource noise.
        self.page_errors = []
        self.page.on("pageerror", lambda e: self.page_errors.append(str(e)))

        # Browsers log a console error for any 4xx, including the deliberate
        # "never fetched" 404. Kept apart so it cannot mask a real exception.
        self.console_messages = []
        self.page.on("console", lambda m: (
            self.console_messages.append(m.text) if m.type == "error" else None))

    def tearDown(self):
        self.context.close()

    def assertNoPageErrors(self):
        self.assertEqual(self.page_errors, [],
                         "the page threw an uncaught exception")

    # -- reading what the server wrote -------------------------------------

    def read_json(self, name):
        """A workspace file as data, for asserting on what was persisted.

        Checking the file rather than the page is the point of these tests:
        #21 was a field the form displayed correctly and never saved.
        """
        return json.loads((self.workspace / name).read_text())

    def projects(self):
        return self.read_json("projects.json")["projects"]

    def accounts(self):
        return self.read_json("accounts.json")["accounts"]

    def goto(self, path=""):
        # "load", not "networkidle": the report form runs a debounced preview
        # timer, so the network never goes quiet for long enough and every
        # navigation would burn the full timeout before returning. Readiness is
        # better expressed by the polling assertion that follows.
        self.page.goto(f"{self.base_url}{path}", wait_until="load")
