"""Explicit opt-in, loopback-only PostgreSQL adapter configuration.

No credentials are hard-coded, logged or accepted from request/JWT fields.
No schema creation or network connection occurs during app import.
"""
from __future__ import annotations

from collections.abc import Mapping
import os

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from app.storage.project_access import SQLProjectAccessStore

DB_ENV = "METRIQEN_DATABASE_URL"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
MAX_DSN_CHARS = 4096


def load_local_project_store(
    environ: Mapping[str, str] | None = None,
) -> SQLProjectAccessStore | None:
    """Return a trusted adapter only for an explicit local PostgreSQL DSN.

    App startup stays health-only without METRIQEN_DATABASE_URL. When present,
    the DSN must name a local PostgreSQL psycopg database, not a remote
    destination or arbitrary SQLAlchemy plugin. Failed settings abort startup
    rather than silently disabling the intended security control.
    """
    environment = os.environ if environ is None else environ
    raw_url = environment.get(DB_ENV)
    if raw_url is None:
        return None
    if (not isinstance(raw_url, str) or not raw_url
            or len(raw_url) > MAX_DSN_CHARS or raw_url.strip() != raw_url
            or any(ch in raw_url for ch in "\r\n\t")):
        raise ValueError("local PostgreSQL configuration is invalid")
    try:
        url = make_url(raw_url)
    except ArgumentError:
        raise ValueError("local PostgreSQL configuration is invalid") from None
    if (url.drivername != "postgresql+psycopg"
            or url.host not in LOCAL_HOSTS
            or not url.database or not url.username
            or url.query):
        raise ValueError("only loopback PostgreSQL psycopg configuration is allowed")

    try:
        engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=4,
            max_overflow=0,
            connect_args={
                "connect_timeout": 3,
                "options": "-c statement_timeout=5000",
            },
        )
        return SQLProjectAccessStore(engine)
    except (SQLAlchemyError, ImportError, TypeError, ValueError):
        # Never expose passwords or the DSN through configuration errors.
        raise ValueError("local PostgreSQL adapter could not be configured") from None
