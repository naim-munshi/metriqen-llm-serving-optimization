"""Deployment write acknowledgements must describe the transition they applied.

Previously every write committed, then made a separate store.get() query.
A competing operator could update the same alias before this second query,
making the first operator's HTTP response show the other operator's state.
"""
from __future__ import annotations

from app.local_deployments import LocalDeploymentStore

A, B = "llama3.2:1b", "llama3.2:latest"
DA, DB = "a" * 64, "b" * 64


def test_all_write_results_are_captured_without_postcommit_get(tmp_path, monkeypatch):
    path = tmp_path / "model-ops.sqlite3"
    store = LocalDeploymentStore(path)
    store.initialize()

    def unexpected_second_connection(name):
        raise AssertionError("mutation must not open a second read after commit")

    monkeypatch.setattr(store, "get", unexpected_second_connection)
    created = store.create("acceptance-test", A, DA)
    assert created["active_tag"] == A
    assert created["active_digest"] == DA
    assert created["previous_tag"] is None
    assert created["enabled"] is True

    promoted = store.promote("acceptance-test", B, DB,
                             expected_current=created)
    assert promoted["active_tag"] == B
    assert promoted["previous_tag"] == A
    assert promoted["previous_digest"] == DA

    rolled_back = store.rollback(
        "acceptance-test", expected_previous_tag=A,
        expected_previous_digest=DA,
        expected_current=promoted,
    )
    assert rolled_back["active_tag"] == A
    assert rolled_back["previous_tag"] == B
    assert rolled_back["previous_digest"] == DB

    stopped = store.set_enabled("acceptance-test", False)
    assert stopped["enabled"] is False
    restarted = store.set_enabled("acceptance-test", True,
                              expected_current=stopped)
    assert restarted["enabled"] is True

    # Every result above is a persisted write, not an uncommitted in-memory
    # invention. A separate SQLite connection must observe the last state.
    final = LocalDeploymentStore(path).get("acceptance-test")
    assert final == restarted
    assert final["active_tag"] == A
    assert final["previous_tag"] == B
