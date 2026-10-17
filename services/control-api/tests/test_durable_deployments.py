"""Tenant-scoped durable desired deployment HTTP and SQL security contracts."""
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.main import create_app
from app.security.policy import Role
from app.storage.durable_deployments import (
    SQLDurableDeploymentStore, DeploymentConflict, DeploymentNotFound,
    MAX_DEPLOYMENTS_PER_PROJECT,
)
from app.storage.schema import (
    organization_memberships, organizations, project_deployment_commands,
    project_deployments, project_grants, projects,
)
from test_sql_project_store import (
    seeded, engine, signing_config, issue, PROJECT_A, PROJECT_A2, PROJECT_B,
    ORG_A, ORG_B, ISSUER,
)

A = "llama3.2:1b"
B = "mistral:latest"
DA = "a" * 64
DB = "b" * 64


@pytest.fixture
def deployment_env(seeded, signing_config):
    engine, _, _, _ = seeded
    signing, settings = signing_config
    store = SQLDurableDeploymentStore(engine)
    client = TestClient(create_app(
        settings, durable_deployment_store=store,
    ))
    return client, engine, store, signing


def base(project=PROJECT_A):
    return f"/api/v1/projects/{project}/deployments"


def auth(signing, subject="user-a", key=None, **claims):
    values = {"Authorization": f"Bearer {issue(signing, subject=subject, **claims)}"}
    if key:
        values["Idempotency-Key"] = key
    return values


def create(client, signing, key="idempotent-create-0001", project=PROJECT_A,
           *, subject="user-a", name="stable", tag=A, digest=DA):
    return client.post(base(project), json={
        "name": name, "model_tag": tag, "model_digest": digest,
    }, headers=auth(signing, subject=subject, key=key))


def change(client, signing, operation, generation, key, *,
           project=PROJECT_A, subject="user-a", name="stable",
           tag=B, digest=DB):
    payload = {"expected_generation": generation}
    if operation == "promote":
        payload.update(model_tag=tag, model_digest=digest)
    return client.post(f"{base(project)}/{name}/{operation}", json=payload,
                       headers=auth(signing, subject=subject, key=key))


def become(engine, role=Role.DEPLOYMENT_OPERATOR, project=PROJECT_A):
    with engine.begin() as conn:
        conn.execute(update(organization_memberships).where(
            organization_memberships.c.issuer == ISSUER,
            organization_memberships.c.subject == "user-a",
            organization_memberships.c.organization_id == ORG_A,
        ).values(role=role.value))
        if project == PROJECT_A2:
            conn.execute(insert(project_grants).values(
                issuer=ISSUER, subject="user-a",
                organization_id=ORG_A, project_id=PROJECT_A2,
            ))


def test_desired_state_lifecycle_survives_store_recreation_and_retries(deployment_env):
    client, engine, store, signing = deployment_env
    become(engine)
    first = create(client, signing)
    assert first.status_code == 201, first.text
    state = first.json()
    assert state["desired_state"] == "running"
    assert state["desired_generation"] == 1
    assert state["observed_state"] == "unreconciled"
    assert state["observed_generation"] == 0
    assert state["previous_tag"] is None
    assert create(client, signing).json() == state  # same key and exact snapshot
    assert len(client.get(base(), headers=auth(signing)).json()["deployments"]) == 1
    promote = change(client, signing, "promote", 1, "promote-request-0001")
    assert promote.status_code == 200
    assert promote.json()["model_tag"] == B
    assert promote.json()["previous_tag"] == A
    assert promote.json()["desired_generation"] == 2
    assert change(client, signing, "promote", 1, "promote-request-0001").json() == promote.json()
    assert change(client, signing, "promote", 1, "different-request-0001").status_code == 409
    stopped = change(client, signing, "stop", 2, "stop-request-00001")
    assert stopped.json()["desired_generation"] == 3
    assert stopped.json()["desired_state"] == "stopped"
    assert change(client, signing, "start", 3, "start-request-0001").json()["desired_state"] == "running"
    rolled = change(client, signing, "rollback", 4, "rollback-request-0001")
    assert rolled.json()["model_tag"] == A
    assert rolled.json()["previous_tag"] == B
    assert rolled.json()["desired_generation"] == 5
    assert rolled.json()["observed_state"] == "unreconciled"
    assert SQLDurableDeploymentStore(engine).get(
        __import__("app.security.oidc", fromlist=["VerifiedIdentity"]).VerifiedIdentity(
            subject="user-a", issuer=ISSUER, audience="metriqen-control-api",
        ),
        PROJECT_A, "stable",
    ) == rolled.json()
    with engine.connect() as conn:
        cmds = conn.execute(select(project_deployment_commands)).all()
        assert [c.action for c in cmds] == ["create", "promote", "stop", "start", "rollback"]
    assert client.get(f"{base()}/stable", headers=auth(signing)).json() == rolled.json()


def test_tenant_and_role_boundaries_exclude_forged_jwt_claims(deployment_env):
    client, engine, _, signing = deployment_env
    forged = auth(signing, key="claim-injection-0001",
                  role="owner", permissions=["deployment.manage"],
                  organization_id=str(ORG_B))
    assert client.post(base(PROJECT_A), json={
        "name": "stable", "model_tag": A, "model_digest": DA,
    }, headers=forged).status_code == 404
    assert client.get(base(PROJECT_A), headers=forged).status_code == 200
    assert create(client, signing).status_code == 404  # viewer cannot write.
    become(engine, Role.ML_ENGINEER)
    assert create(client, signing).status_code == 404
    become(engine, Role.DEPLOYMENT_OPERATOR)
    assert create(client, signing).status_code == 201
    assert create(client, signing, project=PROJECT_A2, key="second-project-0001").status_code == 404
    assert create(client, signing, project=PROJECT_B, key="other-tenants-0001").status_code == 404
    assert client.get(base(PROJECT_B), headers=auth(signing)).status_code == 404
    assert client.get(base(PROJECT_A2), headers=auth(signing)).status_code == 404
    # Legitimate grant is server-side, JWT unchanged.
    become(engine, Role.DEPLOYMENT_OPERATOR, PROJECT_A2)
    assert create(client, signing, project=PROJECT_A2, key="second-project-0001").status_code == 201
    # Revoked grant instantly denies both read and write; replay does not bypass.
    with engine.begin() as conn:
        conn.execute(delete(project_grants).where(
            project_grants.c.project_id == PROJECT_A2,
        ))
    assert client.get(base(PROJECT_A2), headers=auth(signing)).status_code == 404
    assert create(client, signing, project=PROJECT_A2, key="second-project-0001").status_code == 404
    assert client.get(base(PROJECT_A), headers=auth(signing, subject="user-b")).status_code == 404


def test_idempotency_scope_rejects_same_key_different_body_and_action(deployment_env):
    client, engine, store, signing = deployment_env
    become(engine)
    first = create(client, signing, key="unique-command-0001")
    assert first.status_code == 201
    assert create(client, signing, key="unique-command-0001", digest=DB).status_code == 409
    assert create(client, signing, key="different-command-0002").status_code == 409
    assert change(client, signing, "stop", 1, "unique-command-0001").status_code == 409
    assert change(client, signing, "rollback", 1, "new-rollback-0001").status_code == 409
    assert change(client, signing, "stop", 1, "stop-request-00001").status_code == 200
    assert change(client, signing, "stop", 1, "stop-request-00001").status_code == 200
    assert change(client, signing, "stop", 2, "stop-request-00002").status_code == 409
    assert change(client, signing, "start", 1, "stale-request-0001").status_code == 409
    assert len(store.list(
        __import__("app.security.oidc", fromlist=["VerifiedIdentity"]).VerifiedIdentity(
            subject="user-a", issuer=ISSUER, audience="metriqen-control-api",
        ), PROJECT_A)) == 1


@pytest.mark.parametrize("bad", [
    {"name": "../prod", "model_tag": A, "model_digest": DA},
    {"name": "UPPER", "model_tag": A, "model_digest": DA},
    {"name": "n" * 33, "model_tag": A, "model_digest": DA},
    {"name": "stable", "model_tag": "https://external.test/insecure", "model_digest": DA},
    {"name": "stable", "model_tag": A, "model_digest": "A" * 64},
    {"name": "stable", "model_tag": A, "model_digest": "short"},
    {"name": "stable", "model_tag": A, "model_digest": DA, "role": "owner"},
])
def test_invalid_payload_never_creates_desired_state(deployment_env, bad):
    client, engine, _, signing = deployment_env
    become(engine)
    result = client.post(base(), json=bad, headers=auth(signing, key="valid-key-invalid-body"))
    assert result.status_code == 422
    assert client.get(base(), headers=auth(signing)).json() == {"deployments": []}


@pytest.mark.parametrize("headers", [
    {}, {"Idempotency-Key": "tiny"},
    {"Idempotency-Key": "a" * 81},
    {"Idempotency-Key": "unsafe space in key"},
    {"Idempotency-Key": "abc" + "/" + "def" * 8},
])
def test_write_requires_replay_safe_header(deployment_env, headers):
    client, engine, _, signing = deployment_env
    become(engine)
    headers = {**auth(signing), **headers}
    response = client.post(base(), json={
        "name": "stable", "model_tag": A, "model_digest": DA,
    }, headers=headers)
    assert response.status_code == 422
    assert client.get(base(), headers=auth(signing)).json() == {"deployments": []}


def test_storage_failure_rolls_back_generation_and_command(deployment_env, monkeypatch):
    client, engine, store, signing = deployment_env
    become(engine)
    assert create(client, signing).status_code == 201
    def broken(*args, **kwargs):
        raise RuntimeError("private connection / credentials")
    with monkeypatch.context() as ctx:
        ctx.setattr(SQLDurableDeploymentStore, "_record_command", staticmethod(broken))
        result = change(client, signing, "stop", 1, "failed-command-0001")
        assert result.status_code == 503
        assert "private" not in result.text
    assert client.get(f"{base()}/stable", headers=auth(signing)).json()["desired_generation"] == 1
    assert change(client, signing, "stop", 1, "failed-command-0001").status_code == 200
    with engine.connect() as conn:
        assert len(conn.execute(select(project_deployment_commands)).all()) == 2


def test_schema_enforces_composite_project_tenant_and_cascade(deployment_env):
    client, engine, _, signing = deployment_env
    become(engine)
    assert create(client, signing).status_code == 201
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(insert(project_deployments).values(
                id=uuid4(), project_id=PROJECT_B, organization_id=ORG_A,
                name="foreign", model_tag=A, model_digest=DA,
                desired_state="running", desired_generation=1,
                observed_state="unreconciled", observed_generation=0,
                created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
                updated_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            ))
    with engine.begin() as conn:
        conn.execute(delete(projects).where(projects.c.id == PROJECT_A))
    with engine.connect() as conn:
        assert conn.execute(select(project_deployments)).all() == []
        assert conn.execute(select(project_deployment_commands)).all() == []


def test_unauthorized_requests_and_no_database_fail_closed(deployment_env):
    client, engine, _, signing = deployment_env
    assert client.get(base()).status_code == 401
    assert client.post(base(), json={
        "name": "stable", "model_tag": A, "model_digest": DA,
    }).status_code == 401
    configured = TestClient(create_app())
    assert configured.get(base()).status_code == 503
    assert configured.post(base(), json={
        "name": "stable", "model_tag": A, "model_digest": DA,
    }).status_code == 503
    assert client.get(base("malformed"), headers=auth(signing)).status_code == 404
