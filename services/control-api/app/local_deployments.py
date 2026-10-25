"""Local named inference routing aliases with one-step rollback.

These are operator-only SQLite-managed aliases for preloaded Ollama models.
They do not create, start, stop or scale independent inference workers.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

_DEPLOYMENT_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
MAX_DEPLOYMENTS = 20


class DeploymentChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tag: str


class DeploymentCreate(DeploymentChoice):
    name: str


def clean_name(value: str) -> str:
    if not isinstance(value, str) or _DEPLOYMENT_NAME.fullmatch(value) is None:
        raise HTTPException(422, "deployment name must be lowercase, 1-32 letters/digits/-/_ and begin with a letter")
    return value


class LocalDeploymentStore:
    def __init__(self, path: Path):
        if not path.is_absolute() or path.suffix != ".sqlite3":
            raise ValueError("local deployment store requires an absolute SQLite file")
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        # Invoked only under the existing opt-in local operator feature.
        with closing(self._connect()) as conn, conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS local_deployments (
                name TEXT PRIMARY KEY,
                active_tag TEXT NOT NULL,
                active_digest TEXT NOT NULL,
                previous_tag TEXT,
                previous_digest TEXT,
                enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                updated_at_utc TEXT NOT NULL,
                CHECK((previous_tag IS NULL) = (previous_digest IS NULL))
            )""")

    @staticmethod
    def _record(row: sqlite3.Row) -> dict:
        return {
            "name": row["name"],
            "active_tag": row["active_tag"],
            "active_digest": row["active_digest"],
            "previous_tag": row["previous_tag"],
            "previous_digest": row["previous_digest"],
            "enabled": bool(row["enabled"]),
            "updated_at_utc": row["updated_at_utc"],
        }

    def list(self) -> list[dict]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute("SELECT * FROM local_deployments ORDER BY name LIMIT ?",
                                (MAX_DEPLOYMENTS,)).fetchall()
        return [self._record(row) for row in rows]

    def get(self, name: str) -> dict | None:
        with closing(self._connect()) as conn, conn:
            row = conn.execute("SELECT * FROM local_deployments WHERE name = ?",
                               (name,)).fetchone()
        return self._record(row) if row is not None else None

    def create(self, name: str, tag: str, digest: str) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT count(*) FROM local_deployments").fetchone()[0] >= MAX_DEPLOYMENTS:
                raise HTTPException(409, "local deployment limit reached")
            try:
                conn.execute("""INSERT INTO local_deployments
                    (name, active_tag, active_digest, previous_tag, previous_digest, enabled, updated_at_utc)
                    VALUES (?, ?, ?, NULL, NULL, 1, ?)""", (name, tag, digest, now))
            except sqlite3.IntegrityError:
                raise HTTPException(409, "deployment name already exists") from None
            # Return the row observed under the same write transaction.
            # A concurrent operator must not replace this operation's
            # confirmation with a later routing/rollback state.
            persisted = conn.execute(
                "SELECT * FROM local_deployments WHERE name = ?", (name,)
            ).fetchone()
            return self._record(persisted)

    def promote(self, name: str, tag: str, digest: str, *,
                expected_current: dict) -> dict:
        # Model loading/approval checks occur outside the SQLite transaction.
        # A separate operator can change the active route or rollback history
        # during those checks. Compare the COMPLETE trusted preflight snapshot
        # while holding the write lock, never overwrite a concurrent update.
        if not isinstance(expected_current, dict):
            raise HTTPException(409, "deployment preflight state missing")
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM local_deployments WHERE name = ?", (name,)).fetchone()
            if row is None:
                raise HTTPException(404, "local deployment not found")
            for field in ("active_tag", "active_digest", "previous_tag",
                          "previous_digest", "updated_at_utc"):
                if row[field] != expected_current.get(field):
                    raise HTTPException(409, "deployment state changed; refresh first")
            if bool(row["enabled"]) is not expected_current.get("enabled"):
                raise HTTPException(409, "deployment state changed; refresh first")
            if row["active_tag"] == tag and row["active_digest"] == digest:
                raise HTTPException(409, "model revision is already active")
            conn.execute("""UPDATE local_deployments
                SET previous_tag = active_tag, previous_digest = active_digest,
                    active_tag = ?, active_digest = ?, updated_at_utc = ?
                WHERE name = ?""", (tag, digest, now, name))
            # Return the row observed under the same write transaction.
            # A concurrent operator must not replace this operation's
            # confirmation with a later routing/rollback state.
            persisted = conn.execute(
                "SELECT * FROM local_deployments WHERE name = ?", (name,)
            ).fetchone()
            return self._record(persisted)

    def rollback(self, name: str, *, expected_previous_tag: str, expected_previous_digest: str) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM local_deployments WHERE name = ?", (name,)).fetchone()
            if row is None:
                raise HTTPException(404, "local deployment not found")
            if row["previous_tag"] is None:
                raise HTTPException(409, "deployment has no previous revision")
            if (row["previous_tag"] != expected_previous_tag or
                    row["previous_digest"] != expected_previous_digest):
                raise HTTPException(409, "deployment rollback target changed; refresh first")
            conn.execute("""UPDATE local_deployments
                SET active_tag = previous_tag, active_digest = previous_digest,
                    previous_tag = ?, previous_digest = ?,
                    updated_at_utc = ?
                WHERE name = ?""",
                         (row["active_tag"], row["active_digest"], now, name))
            # Return the row observed under the same write transaction.
            # A concurrent operator must not replace this operation's
            # confirmation with a later routing/rollback state.
            persisted = conn.execute(
                "SELECT * FROM local_deployments WHERE name = ?", (name,)
            ).fetchone()
            return self._record(persisted)

    def set_enabled(self, name: str, enabled: bool) -> dict:
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            count = conn.execute("""UPDATE local_deployments
                SET enabled = ?, updated_at_utc = ? WHERE name = ?""",
                                 (int(enabled), datetime.now(timezone.utc).isoformat(), name))
            if count.rowcount != 1:
                raise HTTPException(404, "local deployment not found")
            # Return the row observed under the same write transaction.
            # A concurrent operator must not replace this operation's
            # confirmation with a later routing/rollback state.
            persisted = conn.execute(
                "SELECT * FROM local_deployments WHERE name = ?", (name,)
            ).fetchone()
            return self._record(persisted)
