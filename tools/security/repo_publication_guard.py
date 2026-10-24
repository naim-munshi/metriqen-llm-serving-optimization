"""Fail CI if known private/local artifact paths are tracked in the public repo.

This deliberately inspects Git's *index paths*, not the working tree, network,
environment variables, file contents or secrets. It supplements (does NOT
replace) private disclosure, secret scanning and operator review.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
EXACT_ALLOWED = frozenset({".env.example"})
PRIVATE_SEGMENTS = frozenset({
    ".venv", "venv", "node_modules", "private-ui-samples",
})
PRIVATE_PAIRS = frozenset({
    ("reports", "local"),
    ("reports", "private"),
    ("data", "private"),
    ("artifacts", "private"),
})
PRIVATE_SUFFIXES = (
    ".pem", ".p12", ".pfx", ".key", ".sqlite", ".sqlite3", ".sqlite-wal",
    ".sqlite-shm", ".db", ".db-journal",
)


def rejected_reason(path: str) -> str | None:
    """Return a stable non-sensitive category, or None for an allowed path."""
    if not path or "\x00" in path or "\\" in path:
        return "invalid_path"
    pieces = tuple(part.casefold() for part in PurePosixPath(path).parts)
    if not pieces or pieces[0] in (".", "..") or ".." in pieces:
        return "invalid_path"
    if path in EXACT_ALLOWED:
        return None
    if any(part == ".env" or part.startswith(".env.") for part in pieces):
        return "environment_file"
    if any(part in PRIVATE_SEGMENTS for part in pieces):
        return "private_directory"
    if any(pair in zip(pieces, pieces[1:]) for pair in PRIVATE_PAIRS):
        return "private_directory"
    if pieces[-1].endswith(PRIVATE_SUFFIXES):
        return "potential_credential_or_state_file"
    return None


def check_paths(paths: list[str]) -> list[tuple[str, str]]:
    return [(path, reason) for path in paths
            if (reason := rejected_reason(path)) is not None]


def tracked_paths(root: Path = ROOT) -> list[str]:
    """Git index only: .gitignore cannot protect against git add -f."""
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "-z", "--"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        check=False, timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError("tracked-file inspection unavailable")
    return [name.decode("utf-8", "surrogateescape")
            for name in result.stdout.split(b"\0") if name]


def main() -> int:
    try:
        violations = check_paths(tracked_paths())
    except (OSError, RuntimeError, subprocess.SubprocessError):
        print("FAIL: unable to validate public tracked-file safety", file=sys.stderr)
        return 2
    if violations:
        print("FAIL: unsafe paths tracked in public repository", file=sys.stderr)
        for path, category in violations:
            # Never inspect, print or upload file contents.
            print(f"  {category}: {path}", file=sys.stderr)
        return 2
    print("PASS: no known private artifact or credential-file paths tracked")
    print("Scope: filenames only; not secret scanning or release/security approval")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
