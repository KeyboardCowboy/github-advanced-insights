#!/usr/bin/env python3
"""Named GitHub accounts: where each project's credential comes from.

An account records a *source* for a token, never a token. `accounts.json` is
committed alongside the projects that reference it, so a secret in it would be a
secret in git. Two sources are supported:

    {"gh_account": "KeyboardCowboy"}   -> gh auth token --user KeyboardCowboy
    {"token_env": "GH_TOKEN_WORK"}     -> read that environment variable

`gh` is a credential store rather than an auth type: it holds an OAuth
user-to-server token for an account logged in through the browser, and a classic
or fine-grained PAT for an account logged in with one. Either works here.

Retrieving a specific account's token does **not** change which account is
active, so several projects can read from different accounts in one session and
nothing has to be switched.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ACCOUNTS_PATH = BASE_DIR / "accounts.json"

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
ENV_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")

EMPTY = {"accounts": [], "default_account": None}

# Tokens are looked up once per process. A refresh makes many requests and
# shelling out to `gh` for each would dominate the runtime.
_TOKEN_CACHE = {}


def load_accounts():
    if not ACCOUNTS_PATH.exists():
        return dict(EMPTY)
    return {**EMPTY, **json.loads(ACCOUNTS_PATH.read_text())}


def list_accounts():
    return load_accounts()["accounts"]


def get_account(account_id=None):
    """One account by id, the default when id is None, or None when unresolvable."""
    document = load_accounts()
    wanted = account_id or document.get("default_account")
    if not wanted:
        return document["accounts"][0] if len(document["accounts"]) == 1 else None
    return next((a for a in document["accounts"] if a["id"] == wanted), None)


def resolve_token(account=None):
    """The token for an account, or the active `gh` account's when none is named.

    Raises RuntimeError with a message naming the fix, since every caller is
    either a CLI run or an HTTP handler that must turn it into a response.
    """
    key = (account or {}).get("id", "__active__")
    if key in _TOKEN_CACHE:
        return _TOKEN_CACHE[key]

    env_name = (account or {}).get("token_env")
    if env_name:
        token = os.environ.get(env_name, "").strip()
        if not token:
            raise RuntimeError(
                f"Account '{account['id']}' reads its token from ${env_name}, "
                f"which is not set."
            )
    else:
        login = (account or {}).get("gh_account")
        command = ["gh", "auth", "token"] + (["--user", login] if login else [])
        result = subprocess.run(command, capture_output=True, text=True)
        token = result.stdout.strip()
        if result.returncode != 0 or not token:
            who = f"account '{login}'" if login else "the active gh account"
            raise RuntimeError(
                f"Could not get a token for {who}. Run `gh auth login` and make "
                f"sure the `project` scope is granted. ({result.stderr.strip()})"
            )

    _TOKEN_CACHE[key] = token
    return token


def describe_source(account):
    """A short, secret-free description of where a token comes from."""
    if not account:
        return "active gh account"
    if account.get("token_env"):
        return f"${account['token_env']}"
    if account.get("gh_account"):
        return f"gh account {account['gh_account']}"
    return "active gh account"


def validate_account(values, existing_ids=(), is_new=False):
    problems = []

    account_id = str(values.get("id", "")).strip()
    if not ID_PATTERN.match(account_id):
        problems.append(
            f"'{account_id}' is not a valid account id. Use lowercase letters, "
            "numbers, and hyphens."
        )
    elif is_new and account_id in existing_ids:
        problems.append(f"An account called '{account_id}' already exists.")

    gh_account = str(values.get("gh_account") or "").strip()
    token_env = str(values.get("token_env") or "").strip()

    if gh_account and token_env:
        problems.append("Choose either a gh account or an environment variable, not both.")
    if gh_account and not LOGIN_PATTERN.match(gh_account):
        problems.append(f"'{gh_account}' is not a valid GitHub login.")
    if token_env and not ENV_PATTERN.match(token_env):
        problems.append(
            f"'{token_env}' is not a valid environment variable name. "
            "Use capitals, digits, and underscores."
        )
    # Neither is allowed: that means "whatever gh is currently logged in as",
    # which is the behaviour this tool had before accounts existed.

    return problems


def save_account(values):
    document = load_accounts()
    existing_ids = [a["id"] for a in document["accounts"]]
    is_new = str(values.get("id", "")).strip() not in existing_ids

    problems = validate_account(values, existing_ids, is_new)
    if problems:
        raise ValueError("\n".join(problems))

    saved = {
        "id": str(values["id"]).strip(),
        "label": str(values.get("label") or values["id"]).strip(),
    }
    # Only ever a reference to a credential; accounts.json is committed.
    if str(values.get("token_env") or "").strip():
        saved["token_env"] = str(values["token_env"]).strip()
    elif str(values.get("gh_account") or "").strip():
        saved["gh_account"] = str(values["gh_account"]).strip()

    if is_new:
        document["accounts"].append(saved)
    else:
        document["accounts"][existing_ids.index(saved["id"])] = saved
    if not document.get("default_account"):
        document["default_account"] = saved["id"]

    _write(document)
    _TOKEN_CACHE.pop(saved["id"], None)
    return saved


def delete_account(account_id):
    """Remove an account. Refuses while any project still reads through it."""
    from projects import list_projects

    users = [p["id"] for p in list_projects() if p.get("account") == account_id]
    if users:
        raise ValueError(
            f"{len(users)} project(s) still use this account: {', '.join(users)}. "
            "Point them elsewhere first."
        )

    document = load_accounts()
    document["accounts"] = [a for a in document["accounts"] if a["id"] != account_id]
    if document.get("default_account") == account_id:
        document["default_account"] = (
            document["accounts"][0]["id"] if document["accounts"] else None
        )
    _write(document)


def _write(document):
    temp_path = ACCOUNTS_PATH.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(document, indent=2) + "\n")
    os.replace(temp_path, ACCOUNTS_PATH)
