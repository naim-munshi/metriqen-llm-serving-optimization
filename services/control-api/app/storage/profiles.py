"""Verified OIDC identity profile storage. No permissions live in this table."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.security.oidc import VerifiedIdentity
from app.storage.schema import user_profiles


class ProfileAlreadyExists(Exception):
    """Only the unique issuer+subject record can be created once."""


class ProfileStore(Protocol):
    def get(self, identity: VerifiedIdentity) -> dict | None: ...
    def create(self, identity: VerifiedIdentity, display_name: str) -> dict: ...
    def change_name(self, identity: VerifiedIdentity, display_name: str) -> dict | None: ...


def as_profile(row) -> dict:
    return {
        "issuer": row.issuer,
        "subject": row.subject,
        "display_name": row.display_name,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


class SQLProfileStore:
    """Parameterized, current-request identity lookups; no JWT profile trust.

    The engine must come from the trusted, local PostgreSQL configuration,
    not from an HTTP field. No database migrations are executed here.
    """

    def __init__(self, engine: Engine):
        if not isinstance(engine, Engine):
            raise TypeError("trusted SQLAlchemy Engine required")
        self._engine = engine

    def get(self, identity: VerifiedIdentity) -> dict | None:
        query = select(user_profiles).where(
            user_profiles.c.issuer == identity.issuer,
            user_profiles.c.subject == identity.subject,
        )
        with self._engine.connect() as conn:
            row = conn.execute(query).one_or_none()
        return as_profile(row) if row is not None else None

    def create(self, identity: VerifiedIdentity, display_name: str) -> dict:
        now = datetime.now(timezone.utc)
        statement = insert(user_profiles).values(
            issuer=identity.issuer, subject=identity.subject,
            display_name=display_name, created_at=now, updated_at=now,
        )
        try:
            with self._engine.begin() as conn:
                conn.execute(statement)
        except IntegrityError as exc:
            # The HTTP boundary returns only an opaque conflict; never echo SQL.
            raise ProfileAlreadyExists from exc
        created = self.get(identity)
        if created is None:
            raise RuntimeError("created profile cannot be read")
        return created

    def change_name(self, identity: VerifiedIdentity, display_name: str) -> dict | None:
        now = datetime.now(timezone.utc)
        statement = update(user_profiles).where(
            user_profiles.c.issuer == identity.issuer,
            user_profiles.c.subject == identity.subject,
        ).values(display_name=display_name, updated_at=now)
        with self._engine.begin() as conn:
            updated = conn.execute(statement).rowcount
        return self.get(identity) if updated else None
