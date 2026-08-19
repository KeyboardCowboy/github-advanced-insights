#!/usr/bin/env python3
"""One place every GraphQL request goes out through.

Requests are made directly over HTTPS with a bearer token resolved from the
account, rather than shelling out to `gh api graphql`. That matters for two
reasons:

  - `gh api` always uses whichever account is *active*, so it cannot serve two
    projects that read from different accounts in one session.
  - It makes `gh` optional. With `GITHUB_TOKEN` set, or an account naming an
    environment variable, this runs with nothing but Python, which is what lets
    it work in CI or a container.

`gh` remains the default credential source, so an existing setup keeps working
with no configuration.

Known limitation: the endpoint is github.com. `gh api` would honour a configured
enterprise host; this does not.
"""
import json
import urllib.error
import urllib.request

from accounts import resolve_token

ENDPOINT = "https://api.github.com/graphql"
TIMEOUT_SECONDS = 30


class GitHubError(RuntimeError):
    """A request failed, or the API answered with errors."""


def graphql(query, account=None):
    """Run a GraphQL query as `account` and return its `data` payload.

    Raises GitHubError with a readable message. Callers decide whether that
    becomes an exit (CLI) or a response (server); neither wants a traceback.
    """
    try:
        token = resolve_token(account)
    except RuntimeError as exc:
        raise GitHubError(str(exc)) from exc

    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps({"query": query}).encode(),
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            # Identifies the caller in GitHub's logs, which is expected of any
            # client and helps when debugging rate limits.
            "User-Agent": "work-item-age-reports",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:200]
        raise GitHubError(f"GitHub returned HTTP {exc.code}. {detail}") from exc
    except urllib.error.URLError as exc:
        raise GitHubError(f"Could not reach GitHub: {exc.reason}") from exc

    if payload.get("errors"):
        # Surface the first message; the rest are usually the same fault
        # repeated per field.
        raise GitHubError(payload["errors"][0].get("message", "Unknown GraphQL error."))
    return payload["data"]
