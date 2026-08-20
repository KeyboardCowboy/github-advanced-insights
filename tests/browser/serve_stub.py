"""Entry point for the test server subprocess.

Run as `python3 -m tests.browser.serve_stub` with `GH_INSIGHTS_HOME` and `PORT`
already in the environment.

The ordering in here is the whole point of the file, and it is easy to break by
tidying: the stub goes in *before* `ticket_aging_server` is imported. The server
and its dependencies all use `from github import graphql`, so each one copies the
reference at import time. Patch after they load and you have rebound a name
nobody reads any more, while the live function stays wired up — the tests would
then quietly make real API calls and fail in whatever way the network felt like
that day.
"""
import sys

from tests.browser import fake_github

fake_github.install()

# Only now is it safe to pull in anything that touches GitHub.
import ticket_aging_server  # noqa: E402

if __name__ == "__main__":
    # main() parses argv; it reads the port from $PORT, which the parent set.
    sys.argv = ["ticket_aging_server"]
    ticket_aging_server.main()
