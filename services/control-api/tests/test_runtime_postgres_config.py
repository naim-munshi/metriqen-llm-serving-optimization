"""Opt-in local PostgreSQL configuration: no external connection in CI.

These tests validate URL admission and lazy engine creation. They do not claim
to start PostgreSQL or apply the migration SQL.
"""
import pytest
from sqlalchemy.engine import Engine

from app.storage.project_access import SQLProjectAccessStore
from app.storage.runtime import load_local_project_store


def test_runtime_starts_without_database_configuration():
    assert load_local_project_store({}) is None


@pytest.mark.parametrize("invalid", [
    "",
    " ",
    "\tpostgresql+psycopg://user:secret@localhost/dev",
    "sqlite:///tmp/metriqen.db",
    "postgresql://user:secret@localhost/dev",
    "postgresql+psycopg://",
    "postgresql+psycopg://user:secret@public.example.test/dev",
    "postgresql+psycopg://user:secret@10.0.0.2/dev",
    "postgresql+psycopg://user:secret@169.254.169.254/dev",
    "postgresql+psycopg://user:secret@localhost/dev?options=-c%20search_path%3Devil",
    "not-a-url",
])
def test_unsafe_or_incomplete_connection_settings_fail_closed(invalid):
    with pytest.raises(ValueError) as failed:
        load_local_project_store({"METRIQEN_DATABASE_URL": invalid})
    assert "secret" not in str(failed.value)
    assert "public.example.test" not in str(failed.value)
    assert "169.254.169.254" not in str(failed.value)


def test_newline_injection_and_excessively_long_settings_denied():
    for invalid in ("postgresql+psycopg://user:p@localhost/db\nmetadata=secret",
                    "x" * 4097):
        with pytest.raises(ValueError):
            load_local_project_store({"METRIQEN_DATABASE_URL": invalid})


@pytest.mark.parametrize("dsn", [
    "postgresql+psycopg://user:local-secret@127.0.0.1:5432/metriqen_dev",
    "postgresql+psycopg://user:local-secret@localhost/metriqen_dev",
    "postgresql+psycopg://user:local-secret@[::1]:5432/metriqen_dev",
])
def test_explicit_loopback_store_is_built_without_connecting(dsn):
    store = load_local_project_store({"METRIQEN_DATABASE_URL": dsn})
    assert isinstance(store, SQLProjectAccessStore)
    engine = store._engine
    assert isinstance(engine, Engine)
    try:
        # No SQL connection has been opened; no tables are migrated/created.
        assert engine.pool.checkedout() == 0
    finally:
        engine.dispose()


def test_runtime_store_uses_bounded_connections_and_timeouts():
    store = load_local_project_store({
        "METRIQEN_DATABASE_URL": "postgresql+psycopg://reader:secret@127.0.0.1/db",
    })
    engine = store._engine
    try:
        assert engine.pool.size() == 4
        assert engine.pool._max_overflow == 0
        assert engine.pool.checkedout() == 0
    finally:
        engine.dispose()
