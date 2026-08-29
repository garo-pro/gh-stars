#!/usr/bin/env python3
"""
gh_stars.py

Compile the total star count for one or more GitHub accounts (user or
organisation, public or private-but-accessible-to-you).

Usage:
    # Non-interactive, one or more usernames as args
    ./gh_stars.py torvalds
    ./gh_stars.py torvalds anthropics my-private-org

    # Save result to a JSON file as well as printing it
    ./gh_stars.py torvalds anthropics -o stars.json

    # No args -> interactive prompt
    ./gh_stars.py

Star counts are fetched via the `gh` CLI if it is installed and
authenticated. If not, the script falls back to plain `curl` calls
against the GitHub REST API (using $GITHUB_TOKEN if set, otherwise
unauthenticated / public data only).

Output is intentionally plain:
    - a single username  -> just the number, nothing else
    - multiple usernames -> one "username: count" line per account
Accounts that error out (not found, no access, etc.) print
"username: error - <reason>" instead of a number.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from typing import Optional


# --------------------------------------------------------------------------
# gh CLI detection
# --------------------------------------------------------------------------

def gh_is_active() -> bool:
    """Return True if the gh CLI is installed AND authenticated."""
    if shutil.which("gh") is None:
        return False
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


# --------------------------------------------------------------------------
# Repo fetching - gh CLI path
# --------------------------------------------------------------------------

def fetch_repos_gh(owner: str) -> list[dict]:
    """Fetch repo list (name, stargazerCount, isFork) for owner via gh CLI."""
    cmd = [
        "gh", "repo", "list", owner,
        "--limit", "10000",
        "--json", "name,stargazerCount,isFork",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "gh repo list failed")
    return json.loads(result.stdout)


# --------------------------------------------------------------------------
# Repo fetching - curl fallback path
# --------------------------------------------------------------------------

def fetch_repos_curl(owner: str, token: Optional[str]) -> list[dict]:
    """Fetch repo list for owner via curl against the GitHub REST API.

    Paginates until an empty page is returned. Works for both user
    accounts and organisations (GitHub exposes org repos under the
    same /users/{owner}/repos endpoint for public data).
    """
    repos: list[dict] = []
    page = 1
    per_page = 100

    while True:
        url = (
            f"https://api.github.com/users/{owner}/repos"
            f"?per_page={per_page}&page={page}&type=all"
        )
        cmd = ["curl", "-s", "-H", "Accept: application/vnd.github+json"]
        if token:
            cmd += ["-H", f"Authorization: Bearer {token}"]
        cmd.append(url)

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"curl failed: {result.stderr.strip()}")

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"unexpected response for {owner!r}: {exc}") from exc

        if isinstance(data, dict) and data.get("message"):
            # GitHub returns an error object (e.g. Not Found, rate limited)
            raise RuntimeError(data["message"])

        if not data:
            break

        for repo in data:
            repos.append({
                "name": repo.get("name"),
                "stargazerCount": repo.get("stargazers_count", 0),
                "isFork": repo.get("fork", False),
            })

        if len(data) < per_page:
            break
        page += 1

    return repos


# --------------------------------------------------------------------------
# Core logic
# --------------------------------------------------------------------------

def get_star_count(
    owner: str, use_gh: bool, include_forks: bool, token: Optional[str],
) -> int:
    repos = fetch_repos_gh(owner) if use_gh else fetch_repos_curl(owner, token)
    total = 0
    for repo in repos:
        if not include_forks and repo.get("isFork"):
            continue
        total += repo.get("stargazerCount", 0) or 0
    return total


def compile_stars(
    usernames: list[str], use_gh: bool, include_forks: bool, token: Optional[str],
) -> dict[str, object]:
    """Return {username: star_count_or_error_string}."""
    results: dict[str, object] = {}
    for username in usernames:
        username = username.strip()
        if not username:
            continue
        try:
            results[username] = get_star_count(username, use_gh, include_forks, token)
        except Exception as exc:  # noqa: BLE001 - report, don't crash the batch
            results[username] = f"error - {exc}"
    return results


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def print_results(results: dict[str, object]) -> None:
    if len(results) == 1:
        (value,) = results.values()
        print(value)
        return
    for username, value in results.items():
        print(f"{username}: {value}")


def write_json(results: dict[str, object], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
        f.write("\n")


# --------------------------------------------------------------------------
# Interactive mode
# --------------------------------------------------------------------------

def interactive_usernames() -> list[str]:
    raw = input("Enter username(s) (comma or space separated): ")
    parts = raw.replace(",", " ").split()
    return parts


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile total GitHub star counts for one or more users/orgs.",
    )
    parser.add_argument(
        "usernames", nargs="*",
        help="GitHub username(s) or organisation(s). Omit for interactive mode.",
    )
    parser.add_argument(
        "-o", "--output", metavar="FILE",
        help="Write results as JSON to FILE, in addition to printing them.",
    )
    parser.add_argument(
        "--include-forks", action="store_true",
        help="Count stars on forked repos too (excluded by default).",
    )
    parser.add_argument(
        "--token", metavar="TOKEN",
        help="GitHub token for the curl fallback (defaults to $GITHUB_TOKEN). "
             "Ignored when the gh CLI is used, since gh handles its own auth.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    import os

    args = build_parser().parse_args(argv)

    usernames = args.usernames
    if not usernames:
        usernames = interactive_usernames()

    if not usernames:
        print("No username provided.", file=sys.stderr)
        return 1

    use_gh = gh_is_active()
    token = args.token or os.environ.get("GITHUB_TOKEN")

    results = compile_stars(usernames, use_gh, args.include_forks, token)

    print_results(results)

    if args.output:
        write_json(results, args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
