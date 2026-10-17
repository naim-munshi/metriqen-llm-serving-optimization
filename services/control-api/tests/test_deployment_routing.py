"""Strict project/deployment routing: signed JWT + SQL rights + fresh ready state.

Uses synthetic JWTs and SQLite FK fixtures; disposable PostgreSQL integration
is tested separately. No live Ollama/IdP/production execution is claimed.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, update

from app.main import create_app
from app.security.gateway_authorization import load_deployment_routing_mode
from app.security.policy import Role
from app.storage.deployment_routing import (
    SQLDeploymentRoutingStore, RouteNotFound, RouteNotReady,
    ROUTING_FRESHNESS_SECONDS,
)
from app.storage.project_access import SQLProjectAccessStore
from app.storage.reconciliation import SQLReconciliationStore
from app.storage.schema import (
    organization_memberships, project_deployments, project_grants,
)
from test_durable_deployments import (
    deployment_env, become, create, change, auth, base, A, B, DA, DB,
)
from test_sql_project_store import (
    seeded, engine, signing_config, PROJECT_A, PROJECT_A2, PROJECT_B,
    ISSUER, ORG_A,
)
from test_project_access_http import sandbox, signed_material

SECRET = "project-bound-local-service-secret-0123456789abcdef"
INTERNAL = "/internal/gateway/authorize"


class CountingQuota:
    def __init__(self):
        self.used = []

    def enforce(self, org, project):
        self.used.append((org, project))


@pytest.fixture
def bound_route(deployment_env, signing_config):
    writer, engine, _, signer = deployment_env
    _, settings = signing_config
    quota = CountingQuota()
    control = TestClient(
        create_app(
            settings, project_store=SQLProjectAccessStore(engine),
            gateway_internal_token=SECRET,
            deployment_routing_store=SQLDeploymentRoutingStore(engine),
            require_deployment_routing=True,
            project_quota=quota,
        ), client=("127.0.0.1", 47111),
    )
    return control, writer, engine, signer, quota


def call(client, signer, *, project=PROJECT_A, deployment="stable",
         subject="user-a", **claims):
    return client.post(INTERNAL, headers={
        **auth(signer, subject=subject, **claims),
        "X-Metriqen-Gateway-Secret": SECRET,
    }, json={"project_id": str(project), "deployment": deployment})


def attest_ready(engine, *, now=None):
    store = SQLReconciliationStore(engine)
    t = now or datetime.now(timezone.utc)
    claim = store.claim_one(now=t)
    assert claim is not None
    assert store.finish(claim, "ready", now=t)


def test_strict_route_needs_fresh_ready_generation_and_current_sql_grant(bound_route):
    client, writer, engine, signer, quota = bound_route
    # Untrusted role/permission claims cannot promote a viewer to owner.
    assert call(client, signer, role="owner",
                permissions=["inference.invoke"]).status_code == 404
    become(engine, Role.OWNER)
    assert call(client, signer).status_code == 404  # No deployment.
    assert quota.used == []
    assert create(writer, signer).status_code == 201
    become(engine, Role.DEVELOPER)
    assert call(client, signer).status_code == 409  # Unreconciled.
    assert quota.used == []
    attest_ready(engine)
    success = call(client, signer)
    assert success.status_code == 200, success.text
    assert success.json() == {
        "authorized": True, "project_id": str(PROJECT_A),
        "deployment": "stable", "model_tag": A, "model_digest": DA,
        "generation": 1,
    }
    assert len(quota.used) == 1
    assert call(client, signer, deployment="unknown").status_code == 404
    assert call(client, signer, project=PROJECT_A2).status_code == 404
    assert call(client, signer, project=PROJECT_B).status_code == 404
    assert len(quota.used) == 1
    # New desired generation invalidates *all* old observation evidence.
    promote = change(writer, signer, "promote", 1, "strict-promotion-0001")
    assert promote.status_code == 404  # Developer cannot manage deployments.
    become(engine, Role.OWNER)
    promote = change(writer, signer, "promote", 1, "strict-promotion-0001")
    assert promote.status_code == 200
    assert call(client, signer).status_code == 409
    attest_ready(engine)
    switched = call(client, signer)
    assert switched.status_code == 200
    assert switched.json()["model_tag"] == B
    assert switched.json()["model_digest"] == DB
    assert switched.json()["generation"] == 2
    stopped = change(writer, signer, "stop", 2, "strict-stop-0001")
    assert stopped.status_code == 200
    assert call(client, signer).status_code == 409
    assert len(quota.used) == 2  # Only two actually-ready attempts charged.
    with engine.begin() as conn:
        conn.execute(delete(organization_memberships).where(
            organization_memberships.c.issuer == ISSUER,
            organization_memberships.c.subject == "user-a",
            organization_memberships.c.organization_id == ORG_A,
        ))
    assert call(client, signer).status_code == 404
    assert len(quota.used) == 2


def test_observation_expiry_blocks_stale_models_without_quota(bound_route):
    client, writer, engine, signer, quota = bound_route
    become(engine, Role.OWNER)
    assert create(writer, signer).status_code == 201
    attest_ready(engine)
    assert call(client, signer).status_code == 200
    past = datetime.now(timezone.utc) - timedelta(seconds=ROUTING_FRESHNESS_SECONDS + 2)
    with engine.begin() as conn:
        conn.execute(update(project_deployments).values(observed_at=past))
    assert call(client, signer).status_code == 409
    assert len(quota.used) == 1
    # The operator can re-run a fresh digest verification after 60 seconds.
    store = SQLReconciliationStore(engine)
    claim = store.claim_one(now=datetime.now(timezone.utc))
    assert claim is not None
    assert store.finish(claim, "ready")
    assert call(client, signer).status_code == 200
    assert len(quota.used) == 2


@pytest.mark.parametrize("malformed", [
    None, "", "../prod", "stable/other", "STABLE", "a" * 33,
    "stable\nsecond", "%2Fother",
])
def test_ambiguous_client_deployment_does_not_fallback_to_project_only(bound_route, malformed):
    client, writer, engine, signer, quota = bound_route
    become(engine, Role.OWNER)
    assert create(writer, signer).status_code == 201
    attest_ready(engine)
    payload = {"project_id": str(PROJECT_A)}
    if malformed is not None:
        payload["deployment"] = malformed
    res = client.post(INTERNAL, headers={
        **auth(signer), "X-Metriqen-Gateway-Secret": SECRET,
    }, json=payload)
    assert res.status_code == 422
    assert quota.used == []


def test_failure_and_cross_tenant_never_use_ready_claims(bound_route):
    client, writer, engine, signer, quota = bound_route
    become(engine, Role.OWNER)
    assert create(writer, signer).status_code == 201
    store = SQLReconciliationStore(engine)
    claim = store.claim_one()
    assert store.finish(claim, "failed", "runtime_unavailable")
    assert call(client, signer).status_code == 409
    with engine.begin() as conn:
        conn.execute(update(project_deployments).values(
            observed_state="ready", observed_generation=1,
            observed_code=None, observed_at=datetime.now(timezone.utc),
        ))
    # Even if SQL stored a bogus future timestamp, fail closed.
    with engine.begin() as conn:
        conn.execute(update(project_deployments).values(
            observed_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        ))
    assert call(client, signer).status_code == 409
    with engine.begin() as conn:
        conn.execute(update(project_deployments).values(
            observed_at=datetime.now(timezone.utc),
            observed_generation=0,
        ))
    assert call(client, signer).status_code == 409
    assert quota.used == []


def test_no_strict_bypass_by_partial_configuration(sandbox, signing_config):
    _, store, signer, _, settings = sandbox
    from app.security.gateway_authorization import load_gateway_internal_token
    with pytest.raises(ValueError):
        create_app(settings, project_store=store,
                   gateway_internal_token=SECRET,
                   require_deployment_routing=True)
    with pytest.raises(ValueError):
        create_app(settings, gateway_internal_token=SECRET,
                   deployment_routing_store=object(),
                   require_deployment_routing=True)
    with pytest.raises(ValueError):
        create_app(project_store=store, gateway_internal_token=SECRET,
                   deployment_routing_store=object(),
                   require_deployment_routing=True)
    assert load_deployment_routing_mode({}) is False
    assert load_deployment_routing_mode({
        "METRIQEN_ENFORCE_DEPLOYMENT_ROUTING": "1",
    }) is True
    for bad in ("true", "True", "yes", " ", "01", "2"):
        with pytest.raises(ValueError):
            load_deployment_routing_mode({
                "METRIQEN_ENFORCE_DEPLOYMENT_ROUTING": bad,
            })


def test_legacy_control_api_rejects_explicit_deployment_claim(sandbox):
    _, store, signer, _, settings = sandbox
    client = TestClient(create_app(
        settings, project_store=store, gateway_internal_token=SECRET,
    ), client=("127.0.0.1", 47112))
    store.grant(Role.OWNER)
    assert call(client, signer).status_code == 409
    assert client.post(INTERNAL, headers={
        **auth(signer), "X-Metriqen-Gateway-Secret": SECRET,
    }, json={"project_id": str(PROJECT_A)}).status_code == 200
