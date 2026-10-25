"""Local model/deployment stores release SQLite handles on success and failure.

sqlite3.Connection's __exit__ commits or rolls back but does NOT close().
These tests track the actual connections to ensure explicit close, and
retain transaction semantics in exception paths without a running Mac.
"""
from __future__ import annotations

import sqlite3

from fastapi import HTTPException
import pytest

from app.local_deployments import LocalDeploymentStore
from app.local_models import Registry

A = "llama3.2:1b"
B = "llama3.2:latest"
DA, DB = "a" * 64, "b" * 64


def track_connections(monkeypatch, store):
    original = store._connect
    seen = []

    def connect():
        conn = original()
        seen.append(conn)
        return conn

    monkeypatch.setattr(store, "_connect", connect)
    return seen


def assert_all_closed(seen):
    assert seen, "test should exercise actual SQLite connections"
    for conn in seen:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")


def test_registry_connection_handles_close_after_success_and_rollback(tmp_path, monkeypatch):
    registry = Registry(tmp_path / "registry.sqlite3")
    seen = track_connections(monkeypatch, registry)
    registry.initialize()
    assert registry.list() == []
    assert registry.get(A) is None
    assert registry.add(A, DA)["digest"] == DA
    with pytest.raises(HTTPException) as duplicate:
        registry.add(A, DB)
    assert duplicate.value.status_code == 409
    assert registry.get(A)["digest"] == DA
    assert registry.approve(A)["approved"] is True
    assert registry.list()[0]["approved"] is True
    assert_all_closed(seen)


def test_deployment_connection_handles_close_after_success_and_rollback(tmp_path, monkeypatch):
    store = LocalDeploymentStore(tmp_path / "models.sqlite3")
    seen = track_connections(monkeypatch, store)
    store.initialize()
    assert store.list() == []
    assert store.get("acceptance-test") is None
    assert store.create("acceptance-test", A, DA)["active_tag"] == A
    with pytest.raises(HTTPException) as duplicate:
        store.create("acceptance-test", B, DB)
    assert duplicate.value.status_code == 409
    assert store.get("acceptance-test")["active_digest"] == DA

    assert store.promote("acceptance-test", B, DB,
                         expected_current=store.get("acceptance-test"))["previous_tag"] == A
    with pytest.raises(HTTPException) as already_active:
        store.promote("acceptance-test", B, DB,
                      expected_current=store.get("acceptance-test"))
    assert already_active.value.status_code == 409
    with pytest.raises(HTTPException) as changed_history:
        store.rollback("acceptance-test", expected_previous_tag=B,
                       expected_previous_digest=DB,
                       expected_current=store.get("acceptance-test"))
    assert changed_history.value.status_code == 409
    assert store.rollback("acceptance-test", expected_previous_tag=A,
                          expected_previous_digest=DA,
                          expected_current=store.get("acceptance-test"))["active_tag"] == A
    assert store.set_enabled("acceptance-test", False)["enabled"] is False
    assert store.set_enabled("acceptance-test", True,
                              expected_current=store.get("acceptance-test"))["enabled"] is True
    assert store.list()[0]["previous_tag"] == B
    assert_all_closed(seen)
