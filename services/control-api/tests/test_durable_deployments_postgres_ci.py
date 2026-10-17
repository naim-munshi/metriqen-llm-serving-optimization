"""Isolated PostgreSQL 16 test of migrations 0005–0006, idempotency and leases.

Only works on a fresh, synthetic 'metriqen_ci' database on loopback. Never
points at the user's Mac, other schemas or a production PostgreSQL server.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy import insert, inspect, select
from sqlalchemy.engine import make_url

from app.security.oidc import VerifiedIdentity
from app.security.policy import Role
from app.storage.durable_deployments import (
    DeploymentConflict, DeploymentNotFound, SQLDurableDeploymentStore,
)
from app.storage.runtime import load_local_project_store
from app.storage.schema import (
    organizations, projects, organization_memberships, project_grants,
    project_deployments, project_deployment_commands,
)


def fixture():
    if os.getenv("METRIQEN_POSTGRES_CI") != "1":
        pytest.skip("ephemeral PostgreSQL integration disabled")
    raw = os.environ.get("METRIQEN_TEST_POSTGRES_URL", "")
    try:
        url = make_url(raw)
        valid = (url.drivername == "postgresql+psycopg"
                 and url.host == "127.0.0.1" and url.port == 5432
                 and url.database == "metriqen_ci"
                 and url.username == "metriqen_test" and not url.query)
    except Exception:
        valid = False
    if not valid:
        pytest.fail("refusing untrusted PostgreSQL migration destination")
    migrations = Path(__file__).resolve().parents[1] / "migrations"
    files = (
        "0001_tenant_access.sql", "0002_verified_user_profiles.sql",
        "0003_team_invitations_audit.sql", "0004_invitation_cancellation.sql",
        "0005_project_deployment_desired_state.sql",
        "0006_deployment_reconciliation.sql",
    )
    with psycopg.connect(host=url.host, port=url.port, dbname=url.database,
                         user=url.username, password=url.password,
                         connect_timeout=3, autocommit=True) as conn:
        for name in files:
            conn.execute((migrations / name).read_text(encoding="utf-8"))
    engine = load_local_project_store({"METRIQEN_DATABASE_URL": raw})._engine
    return engine


def principal(subject, issuer="https://ci.example.test"):
    return VerifiedIdentity(subject=subject, issuer=issuer,
                            audience="metriqen-control-api")


def test_real_postgres_deployment_retries_role_revocation_and_cas_races():
    engine = fixture()
    org, outside_org, project, outside_project = [uuid4() for _ in range(4)]
    issuer = principal("owner").issuer
    tag_a, tag_b = "llama3.2:1b", "mistral:latest"
    dig_a, dig_b = "a" * 64, "b" * 64
    try:
        inspector = inspect(engine)
        assert {"project_deployments", "project_deployment_commands"} <= set(
            inspector.get_table_names())
        assert {x["name"] for x in inspector.get_unique_constraints(
            "project_deployment_commands")} >= {"uq_deployment_actor_idempotency"}
        with engine.begin() as conn:
            conn.execute(insert(organizations), [
                {"id": org, "name": "A"}, {"id": outside_org, "name": "B"},
            ])
            conn.execute(insert(projects), [
                {"id": project, "organization_id": org, "name": "main"},
                {"id": outside_project, "organization_id": outside_org, "name": "other"},
            ])
            conn.execute(insert(organization_memberships), [
                {"issuer": issuer, "subject": "owner",
                 "organization_id": org, "role": Role.OWNER.value},
                {"issuer": issuer, "subject": "operator",
                 "organization_id": org, "role": Role.DEPLOYMENT_OPERATOR.value},
            ])
            conn.execute(insert(project_grants).values(
                issuer=issuer, subject="operator",
                organization_id=org, project_id=project,
            ))

        first = SQLDurableDeploymentStore(engine).create(
            principal("owner"), project, "assistant", tag_a, dig_a,
            "parallel-create-key-0001")
        assert first["desired_generation"] == 1
        assert first["observed_state"] == "unreconciled"
        # Two separate store instances and connections must resolve duplicate
        # replay to the exact original response, not two separate rows.
        def repeated(_):
            return SQLDurableDeploymentStore(engine).create(
                principal("owner"), project, "assistant", tag_a, dig_a,
                "parallel-create-key-0001")
        with ThreadPoolExecutor(max_workers=8) as pool:
            snapshots = list(pool.map(repeated, range(16)))
        assert all(item == first for item in snapshots)

        # Different operations sharing expected generation=1 race over the
        # same project's SQL row. At most one may advance desired_generation.
        def promote():
            return SQLDurableDeploymentStore(engine).change(
                principal("owner"), project, "assistant", "promote",
                "promote-race-key-0001", 1, tag=tag_b, digest=dig_b)
        def stop():
            return SQLDurableDeploymentStore(engine).change(
                principal("owner"), project, "assistant", "stop",
                "stop-race-key-00001", 1)
        def compete(action):
            try:
                return action()
            except DeploymentConflict:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(compete, (promote, stop)))
        assert sum(isinstance(item, dict) for item in outcomes) == 1
        assert outcomes.count("conflict") == 1
        state = SQLDurableDeploymentStore(engine).get(
            principal("owner"), project, "assistant")
        assert state["desired_generation"] == 2
        assert state["observed_generation"] == 0
        from app.storage.reconciliation import SQLReconciliationStore, LEASE_SECONDS
        from datetime import datetime, timedelta, timezone
        reconciler = SQLReconciliationStore(engine)
        t0 = datetime.now(timezone.utc)
        # Independent workers race over a PostgreSQL row lock; exactly one
        # receives the active lease even when they use separate connections.
        def claim_worker(_):
            return SQLReconciliationStore(engine).claim_one(now=t0)
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(claim_worker, range(16)))
        assert len([item for item in claims if item is not None]) == 1
        leased = next(item for item in claims if item is not None)
        assert leased.generation == 2
        assert not reconciler.finish(
            leased, "ready", now=t0 + timedelta(seconds=LEASE_SECONDS + 1),
        )  # an expired worker cannot claim a late health success
        reclaimed = reconciler.claim_one(now=t0 + timedelta(seconds=LEASE_SECONDS + 2))
        assert reclaimed is not None and reclaimed.attempt == 2
        assert reclaimed.lease_id != leased.lease_id
        assert reconciler.finish(
            reclaimed, "blocked", "not_allowlisted",
            now=t0 + timedelta(seconds=LEASE_SECONDS + 3),
        )
        assert reconciler.claim_one(now=t0 + timedelta(seconds=LEASE_SECONDS + 4)) is None
        recovered = SQLDurableDeploymentStore(engine).get(
            principal("owner"), project, "assistant")
        assert recovered["observed_state"] == "blocked"
        assert recovered["observed_generation"] == 2
        assert recovered["reconcile_attempts"] == 2
        with engine.connect() as conn:
            assert len(conn.execute(select(project_deployments)).all()) == 1
            assert len(conn.execute(select(project_deployment_commands)).all()) == 2

        with pytest.raises(DeploymentNotFound):
            SQLDurableDeploymentStore(engine).get(
                principal("owner"), outside_project, "assistant")
        assert SQLDurableDeploymentStore(engine).list(
            principal("operator"), project) == [recovered]
        from sqlalchemy import delete
        with engine.begin() as conn:
            conn.execute(delete(project_grants).where(
                project_grants.c.project_id == project,
                project_grants.c.subject == "operator",
            ))
        with pytest.raises(DeploymentNotFound):
            SQLDurableDeploymentStore(engine).list(principal("operator"), project)
        with pytest.raises(DeploymentNotFound):
            SQLDurableDeploymentStore(engine).change(
                principal("operator"), project, "assistant", "stop",
                "operator-no-grant-0001", 2)
        # New user-authorized desired state supersedes the previously blocked
        # runtime snapshot; even a worker holding an old lease cannot
        # republish it for the new generation.
        fresh = SQLDurableDeploymentStore(engine).change(
            principal("owner"), project, "assistant",
            "start" if state["desired_state"] == "stopped" else "stop",
            "new-generation-after-observation", 2,
        )
        assert fresh["observed_state"] == "unreconciled"
        assert fresh["observed_generation"] == 0
        assert fresh["observed_at"] is None
        assert fresh["reconcile_attempts"] == 0

        from app.storage.deployment_routing import (
            SQLDeploymentRoutingStore, RouteNotFound, RouteNotReady,
        )
        route_store = SQLDeploymentRoutingStore(engine)
        with pytest.raises(RouteNotReady):
            route_store.resolve(principal("owner"), project, "assistant")
        with pytest.raises(RouteNotFound):
            route_store.resolve(principal("owner"), outside_project, "assistant")
        with pytest.raises(RouteNotFound):
            route_store.resolve(principal("operator"), project, "assistant")
        if fresh["desired_state"] == "stopped":
            fresh = SQLDurableDeploymentStore(engine).change(
                principal("owner"), project, "assistant", "start",
                "activate-for-routing-0001", fresh["desired_generation"],
            )
        # Real PostgreSQL: a trusted worker lease is completed at the latest
        # desired generation, and only that exact persisted digest is served.
        lease = SQLReconciliationStore(engine).claim_one()
        assert lease is not None and lease.generation == fresh["desired_generation"]
        assert SQLReconciliationStore(engine).finish(lease, "ready")
        bound = route_store.resolve(principal("owner"), project, "assistant")
        assert bound.project_id == project
        assert bound.model_tag == fresh["model_tag"]
        assert bound.model_digest == fresh["model_digest"]
        assert bound.generation == fresh["desired_generation"]
        assert route_store.resolve(principal("owner"), project, "assistant") == bound
        # A new stopped generation denies inference regardless of the same
        # bearer subject, prior 'ready' observation, or idempotent commands.
        stopped = SQLDurableDeploymentStore(engine).change(
            principal("owner"), project, "assistant", "stop",
            "stop-after-ready-for-serve", fresh["desired_generation"],
        )
        assert stopped["observed_state"] == "unreconciled"
        with pytest.raises(RouteNotReady):
            route_store.resolve(principal("owner"), project, "assistant")
    finally:
        engine.dispose()
