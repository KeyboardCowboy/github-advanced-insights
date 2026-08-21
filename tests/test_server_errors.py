"""The server must answer API requests with JSON, including when it fails.

The page parses every API response as JSON, so an exception that escapes a route
reaches the reader as `Unexpected token '<', "<!DOCTYPE "... is not valid JSON`.
That names the parser rather than the fault, and sends whoever is debugging it
into the browser instead of the server log.

This runs the real server, not the stubbed one the browser tests use. The stub
replaces both `github.graphql` and `resolve_token`, which is exactly the code
path this is about -- so the case cannot be reproduced there.
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
import urllib.error
import urllib.request
from pathlib import Path

import tests.helpers  # noqa: F401
from tests.helpers import FIXTURE_WORKSPACE, REPO_ROOT


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class ApiFailsAsJson(unittest.TestCase):
    """A server with no `gh` on its PATH, which is the demo's situation.

    The Tugboat preview runs in a container with no GitHub CLI, so resolving a
    token fails before any request is made. Nothing here reaches the network:
    the failure happens first.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="gh-insights-servererr-")
        cls.workspace = Path(cls._tmp) / "workspace"
        shutil.copytree(FIXTURE_WORKSPACE, cls.workspace)
        cls.port = free_port()
        cls._server = subprocess.Popen(
            [sys.executable, str(REPO_ROOT / "ticket_aging_server.py")],
            cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env={"GH_INSIGHTS_HOME": str(cls.workspace),
                 "PORT": str(cls.port),
                 # sys.executable is absolute, so the server still starts; it
                 # simply cannot find the binary it reads tokens from.
                 "PATH": "/usr/bin:/bin"})
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.5):
                    return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("test server did not start")

    @classmethod
    def tearDownClass(cls):
        cls._server.terminate()
        try:
            cls._server.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover
            cls._server.kill()
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def post(self, path, body=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode() if body is not None else b"",
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def test_refreshing_without_gh_returns_json_not_html(self):
        status, body = self.post("/api/dashboard/acme-board/refresh")
        self.assertNotIn("<!DOCTYPE", body)
        self.assertNotEqual(status, 200)
        payload = json.loads(body)          # the assertion that matters
        self.assertIn("gh", payload["message"],
                      "the message should name the missing command")

    def test_refreshing_a_report_without_gh_also_returns_json(self):
        # Same seam, different route: the guard is on the dispatch rather than
        # on one handler, so a route nobody thought about still answers JSON.
        status, body = self.post("/api/reports/baseline/refresh")
        self.assertNotIn("<!DOCTYPE", body)
        self.assertNotEqual(status, 200)
        json.loads(body)

    def test_an_unknown_api_route_is_json_too(self):
        status, body = self.post("/api/there-is-no-such-thing")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["error"], "not_found")

    def test_the_server_survives_the_failure(self):
        # A route that raises must not take the process with it; the next
        # request has to be answered normally.
        self.post("/api/dashboard/acme-board/refresh")
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/api/reports", timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("reports", json.loads(response.read()))


if __name__ == "__main__":
    unittest.main()
