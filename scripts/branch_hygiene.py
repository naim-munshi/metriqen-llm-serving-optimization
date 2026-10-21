"""Manually invoked GitHub branch hygiene with conservative evidence checks.

No deletion by default. A prune requires explicit user confirmation and only
matches branch heads to merged same-repository pull-request head SHAs.
This does not delete branchless commits or replace archive/backups.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
import sys
from urllib.parse import quote
from urllib.request import Request, urlopen

API = "https://api.github.com"
CONFIRMATION = "DELETE_VERIFIED_MERGED_BRANCHES"
PRESERVE_NAMES = {"main", "master", "MdNaim"}
PRESERVE_PREFIXES = ("release/", "hotfix/")


@dataclass(frozen=True)
class Candidate:
    name: str
    sha: str
    merged_pr: int


def classify(repo: str, default_branch: str, branches: list[dict],
             pulls: list[dict]) -> tuple[list[Candidate], list[tuple[str, str]]]:
    """Select only unchanged heads of already merged, same-repo PRs."""
    merged_heads: dict[tuple[str, str], int] = {}
    for pull in pulls:
        if not pull.get("merged_at"):
            continue
        head = pull.get("head")
        if not isinstance(head, dict):
            continue
        head_repo = head.get("repo")
        if not isinstance(head_repo, dict) or head_repo.get("full_name") != repo:
            continue
        name, sha = head.get("ref"), head.get("sha")
        number = pull.get("number")
        if isinstance(name, str) and isinstance(sha, str) and isinstance(number, int):
            merged_heads[(name, sha)] = number

    candidates = []
    manual = []
    for branch in sorted(branches, key=lambda b: b.get("name", "")):
        name = branch.get("name")
        commit = branch.get("commit")
        sha = commit.get("sha") if isinstance(commit, dict) else None
        if not isinstance(name, str) or not isinstance(sha, str):
            raise ValueError("malformed branch response: missing ref or head SHA")
        if (name == default_branch or name in PRESERVE_NAMES
                or name.startswith(PRESERVE_PREFIXES) or branch.get("protected") is not False):
            manual.append((name, "protected or deliberately preserved"))
            continue
        pr = merged_heads.get((name, sha))
        if pr is None:
            manual.append((name, "no exact matching merged same-repository PR head"))
        else:
            candidates.append(Candidate(name, sha, pr))
    return candidates, manual


def confirm_mode(mode: str, confirmation: str) -> None:
    if mode == "prune" and confirmation != CONFIRMATION:
        raise ValueError("prune denied: explicit confirmation phrase required")


class GitHub:
    def __init__(self, repo: str, token: str):
        if not token:
            raise ValueError("GITHUB_TOKEN is required")
        if not repo or "/" not in repo or repo.count("/") != 1:
            raise ValueError("GITHUB_REPOSITORY must be owner/repo")
        self.repo = repo
        self.token = token

    def request(self, path: str, method: str = "GET"):
        req = Request(API + path, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "metriqen-branch-hygiene",
        })
        with urlopen(req, timeout=30) as response:
            raw = response.read()
        return json.loads(raw) if raw else None

    def get(self, endpoint: str):
        return self.request(f"/repos/{self.repo}/{endpoint}")

    def pages(self, endpoint: str) -> list[dict]:
        output = []
        for page in range(1, 101):
            sep = "&" if "?" in endpoint else "?"
            data = self.get(f"{endpoint}{sep}per_page=100&page={page}")
            if not isinstance(data, list):
                raise ValueError("unexpected paginated GitHub API response")
            output.extend(data)
            if len(data) < 100:
                return output
        raise ValueError("too many API pages; refusing partial branch audit")

    def head_unchanged(self, candidate: Candidate) -> bool:
        branch_path = quote(candidate.name, safe="/")
        current = self.get(f"branches/{branch_path}")
        return (
            isinstance(current, dict)
            and current.get("protected") is False
            and isinstance(current.get("commit"), dict)
            and current["commit"].get("sha") == candidate.sha
            and current.get("name") == candidate.name
        )

    def delete_verified(self, candidate: Candidate) -> None:
        # GitHub delete-ref has no compare-and-swap; verify immediately first.
        if not self.head_unchanged(candidate):
            raise ValueError(f"branch changed/protected since audit: {candidate.name}")
        ref_path = quote("heads/" + candidate.name, safe="/")
        self.get_delete(f"git/refs/{ref_path}")

    def get_delete(self, endpoint: str) -> None:
        self.request(f"/repos/{self.repo}/{endpoint}", method="DELETE")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Conservative GitHub branch audit/prune")
    parser.add_argument("--mode", choices=("audit", "prune"), default="audit")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)

    try:
        confirm_mode(args.mode, args.confirm)
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        github = GitHub(repo, os.environ.get("GITHUB_TOKEN", ""))
        metadata = github.request(f"/repos/{repo}")
        default = metadata.get("default_branch")
        if not isinstance(default, str) or not default:
            raise ValueError("missing default branch metadata")
        branches = github.pages("branches")
        pulls = github.pages("pulls?state=all")
        candidates, manual = classify(repo, default, branches, pulls)
        print(f"Repository: {repo} | mode: {args.mode} | default: {default}")
        print(f"Branches: {len(branches)} | exact merged PR matches: {len(candidates)} | preserved/manual: {len(manual)}")
        for name, reason in manual:
            print(f"PRESERVE: {name} ({reason})")
        for candidate in candidates:
            print(f"CANDIDATE: {candidate.name} @ {candidate.sha[:12]} (merged PR #{candidate.merged_pr})")
        if args.mode == "audit":
            print("AUDIT ONLY — no branches deleted.")
            return 0
        if len(candidates) == 0:
            print("No verified merged PR heads to delete.")
            return 0
        print("PRUNE CONFIRMED — checking head before every deletion.")
        for candidate in candidates:
            github.delete_verified(candidate)
            print(f"DELETED: {candidate.name} (PR #{candidate.merged_pr})")
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"branch maintenance failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
