"""PostgreSQL 16 integration against the actual checked-in SQL migration.

This module is opt-in and ONLY accepts the ephemeral GitHub Actions
metriqen_ci database on 127.0.0.1. No SQLite substitutes, remote database,
automatic app startup migration, external IdP or production credentials.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from uuid import UUID

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, inspect, insert, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.main import create_app
from app.security.http_identity import PinnedIdentitySettings
from app.security.oidc import VerifiedIdentity
from app.security.policy import Role
from app.storage.runtime import load_local_project_store
from app.storage.profiles import SQLProfileStore
from app.storage.workspaces import SQLWorkspaceStore, WorkspaceLimitReached
from app.storage.schema import (
    organizations, projects, organization_memberships, project_grants, user_profiles,
)

ORG_A = UUID("11111111-1111-4111-8111-111111111111")
ORG_B = UUID("22222222-2222-4222-8222-222222222222")
PROJECT_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PROJECT_A2 = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
PROJECT_B = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
UNKNOWN = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
ISSUER = "https://login.example.test/realms/metriqen"
AUDIENCE = "metriqen-control-api"
CLIENT = "metriqen-web"
MIGRATIONS = tuple(Path(__file__).resolve().parents[1] / "migrations" / name for name in (
    "0001_tenant_access.sql", "0002_verified_user_profiles.sql",
))


@pytest.fixture(scope="module")
def postgres_engine():
    if os.environ.get("METRIQEN_POSTGRES_CI") != "1":
        pytest.skip("ephemeral PostgreSQL CI not enabled")
    raw_url = os.environ.get("METRIQEN_TEST_POSTGRES_URL")
    if not raw_url:
        pytest.fail("PostgreSQL CI is enabled but its ephemeral DSN is absent")
    try:
        url = make_url(raw_url)
    except Exception:
        pytest.fail("invalid ephemeral PostgreSQL CI configuration")
    # Never run privileged DDL against a user-managed or production database.
    if not (url.drivername == "postgresql+psycopg"
            and url.host == "127.0.0.1" and url.port == 5432
            and url.database == "metriqen_ci"
            and url.username == "metriqen_test" and not url.query):
        pytest.fail("PostgreSQL integration requires the isolated CI database")
    store = load_local_project_store({"METRIQEN_DATABASE_URL": raw_url})
    assert store is not None
    engine = store._engine
    try:
        # This CI-only migration is run by a privileged setup fixture, not
        # by app startup or an HTTP request. psycopg autocommit ensures
        # the file's explicit BEGIN/COMMIT delimit one PostgreSQL tx.
        with psycopg.connect(
            host=url.host, port=url.port, dbname=url.database,
            user=url.username, password=url.password,
            connect_timeout=3, autocommit=True,
        ) as connection:
            for migration in MIGRATIONS:
                connection.execute(migration.read_text(encoding="utf-8"))
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def clean_ephemeral_postgres(postgres_engine):
    # Fixed test IDs and no persistent data. Only permitted on the
    # exact ephemeral database from postgres_engine.
    with postgres_engine.begin() as connection:
        connection.exec_driver_sql("TRUNCATE TABLE organizations, user_profiles CASCADE")
    yield
    with postgres_engine.begin() as connection:
        connection.exec_driver_sql("TRUNCATE TABLE organizations, user_profiles CASCADE")


@pytest.fixture(scope="module")
def signing_config():
    signing = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = signing.public_key().public_numbers()

    def b64(value):
        return base64.urlsafe_b64encode(
            value.to_bytes((value.bit_length() + 7) // 8, "big")
        ).decode("ascii").rstrip("=")

    settings = PinnedIdentitySettings(
        issuer=ISSUER, audience=AUDIENCE, authorized_party=CLIENT,
        trusted_jwks={"keys": [{
            "kty": "RSA", "kid": "trusted", "alg": "RS256", "use": "sig",
            "n": b64(public.n), "e": b64(public.e),
        }]},
    )
    return signing, settings


def issue(signing, *, subject="user-a", **untrusted_claims):
    now = datetime.now(timezone.utc)
    claims = {
        "iss": ISSUER, "aud": AUDIENCE, "sub": subject, "azp": CLIENT,
        "iat": now - timedelta(seconds=10),
        "nbf": now - timedelta(seconds=10),
        "exp": now + timedelta(minutes=5),
    }
    claims.update(untrusted_claims)
    return jwt.encode(
        claims, signing, algorithm="RS256",
        headers={"kid": "trusted", "typ": "at+jwt"},
    )


@pytest.fixture
def seeded(postgres_engine, signing_config):
    with postgres_engine.begin() as conn:
        conn.execute(insert(organizations), [
            {"id": ORG_A, "name": "Organization A"},
            {"id": ORG_B, "name": "Organization B"},
        ])
        conn.execute(insert(projects), [
            {"id": PROJECT_A, "organization_id": ORG_A, "name": "Project A"},
            {"id": PROJECT_A2, "organization_id": ORG_A, "name": "Project A2"},
            {"id": PROJECT_B, "organization_id": ORG_B, "name": "Project B"},
        ])
        conn.execute(insert(organization_memberships), [
            {"issuer": ISSUER, "subject": "user-a",
             "organization_id": ORG_A, "role": Role.VIEWER.value},
            {"issuer": ISSUER, "subject": "user-b",
             "organization_id": ORG_B, "role": Role.OWNER.value},
        ])
        conn.execute(insert(project_grants).values(
            issuer=ISSUER, subject="user-a", organization_id=ORG_A,
            project_id=PROJECT_A,
        ))
    store = load_local_project_store({
        "METRIQEN_DATABASE_URL": os.environ["METRIQEN_TEST_POSTGRES_URL"],
    })
    signing, settings = signing_config
    try:
        client = TestClient(create_app(settings, project_store=store))
        with client:
            yield postgres_engine, store, client, signing
    finally:
        store._engine.dispose()


def get(client, project_id, token):
    return client.get(
        f"/api/v1/projects/{project_id}",
        headers={"Authorization": f"Bearer {token}"},
    )


def test_checked_in_migration_creates_real_postgresql_constraints(postgres_engine):
    inspector = inspect(postgres_engine)
    assert set(inspector.get_table_names()) == {
        "organizations", "projects", "organization_memberships", "project_grants",
        "user_profiles",
    }
    grants_fk = inspector.get_foreign_keys("project_grants")
    assert any(
        fk["referred_table"] == "projects"
        and fk["constrained_columns"] == ["project_id", "organization_id"]
        and fk["referred_columns"] == ["id", "organization_id"]
        for fk in grants_fk
    )
    assert any(
        fk["referred_table"] == "organization_memberships"
        and fk["constrained_columns"] == ["issuer", "subject", "organization_id"]
        for fk in grants_fk
    )
    assert any(
        item["name"] == "ck_membership_role"
        for item in inspector.get_check_constraints("organization_memberships")
    )
    assert "uq_projects_id_organization" in {
        item["name"] for item in inspector.get_unique_constraints("projects")
    }


def test_verified_jwt_grants_scoped_project_access_in_postgres(seeded):
    _, _, client, signing = seeded
    token = issue(signing)
    response = get(client, PROJECT_A, token)
    assert response.status_code == 200
    assert response.json() == {
        "id": str(PROJECT_A), "organization_id": str(ORG_A),
        "name": "Project A",
    }
    assert response.headers["cache-control"] == "no-store"
    assert get(client, PROJECT_A2, token).status_code == 404
    assert get(client, PROJECT_B, issue(signing, subject="user-b")).status_code == 200


def test_cross_tenant_and_untrusted_jwt_claims_do_not_grant_access(seeded):
    _, store, client, signing = seeded
    forged = issue(
        signing, role="owner", organization_id=str(ORG_B),
        project_ids=[str(PROJECT_B)], permissions=["project.read"],
    )
    forbidden = get(client, PROJECT_B, forged)
    absent = get(client, UNKNOWN, forged)
    assert forbidden.status_code == absent.status_code == 404
    assert forbidden.json() == absent.json()
    assert get(client, PROJECT_A, forged).status_code == 200
    assert store.get_membership(
        issuer=ISSUER, subject="user-a' OR 1=1 --", organization_id=ORG_A,
    ) is None


def test_database_transport_failure_denies_verified_access_then_recovers(seeded):
    """Real disposable PG rows + injected store connection error; no production outage."""
    from unittest.mock import patch

    _, store, client, signing = seeded
    token = issue(signing)
    assert get(client, PROJECT_A, token).status_code == 200
    assert get(client, PROJECT_B, token).status_code == 404

    # A verified JWT is NOT enough if the authoritative PostgreSQL read
    # becomes unavailable. Never turn an outage into a cached permission.
    with patch.object(store, "get_project", side_effect=psycopg.OperationalError(
        "PRIVATE_SYNTHETIC_DB_DSN_SHOULD_NOT_APPEAR"
    )):
        denied = get(client, PROJECT_A, token)
        assert denied.status_code == 503
        assert "PRIVATE_SYNTHETIC_DB_DSN" not in denied.text
        assert denied.json() == {"detail": "project authorization unavailable"}

    with patch.object(store, "get_membership", side_effect=psycopg.OperationalError(
        "PRIVATE_SYNTHETIC_POSTGRES_CREDENTIAL"
    )):
        denied = get(client, PROJECT_A, token)
        assert denied.status_code == 503
        assert "PRIVATE_SYNTHETIC_POSTGRES_CREDENTIAL" not in denied.text

    # This is a simulated transport exception on a real migrated CI DB,
    # NOT an actual PostgreSQL restart. Restored connectivity still enforces
    # the same current SQL tenant authorization and cannot grant cross-tenant.
    assert get(client, PROJECT_A, token).status_code == 200
    assert get(client, PROJECT_B, token).status_code == 404
    assert get(client, PROJECT_A, issue(signing, subject="unknown")).status_code == 404


def test_postgres_enforces_composite_cross_tenant_fk_and_role_constraint(seeded):
    engine, _, _, _ = seeded
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(insert(project_grants).values(
                issuer=ISSUER, subject="user-a",
                organization_id=ORG_A, project_id=PROJECT_B,
            ))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(insert(organization_memberships).values(
                issuer=ISSUER, subject="unknown", organization_id=ORG_A,
                role="root-bypass",
            ))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(insert(project_grants).values(
                issuer=ISSUER, subject="unrecognized",
                organization_id=ORG_A, project_id=PROJECT_A,
            ))


def test_grant_and_role_revocation_applies_on_next_postgres_http_request(seeded):
    engine, _, client, signing = seeded
    token = issue(signing)
    assert get(client, PROJECT_A, token).status_code == 200
    assert get(client, PROJECT_A2, token).status_code == 404

    with engine.begin() as conn:
        conn.execute(insert(project_grants).values(
            issuer=ISSUER, subject="user-a",
            organization_id=ORG_A, project_id=PROJECT_A2,
        ))
    assert get(client, PROJECT_A2, token).status_code == 200

    with engine.begin() as conn:
        conn.execute(delete(project_grants).where(
            project_grants.c.project_id == PROJECT_A2,
        ))
    assert get(client, PROJECT_A2, token).status_code == 404

    with engine.begin() as conn:
        conn.execute(update(organization_memberships).where(
            organization_memberships.c.issuer == ISSUER,
            organization_memberships.c.subject == "user-a",
            organization_memberships.c.organization_id == ORG_A,
        ).values(role=Role.ADMIN.value))
    assert get(client, PROJECT_A2, token).status_code == 200
    assert get(client, PROJECT_B, token).status_code == 404

    with engine.begin() as conn:
        conn.execute(delete(organization_memberships).where(
            organization_memberships.c.issuer == ISSUER,
            organization_memberships.c.subject == "user-a",
            organization_memberships.c.organization_id == ORG_A,
        ))
    assert get(client, PROJECT_A, token).status_code == 404
    with engine.connect() as conn:
        assert conn.execute(select(project_grants)).all() == []


def test_invalid_or_missing_jwt_does_not_query_postgres(seeded):
    _, _, client, _ = seeded
    missing = client.get(f"/api/v1/projects/{PROJECT_A}")
    malformed = get(client, PROJECT_A, "not-a-jwt")
    assert missing.status_code == 401
    assert malformed.status_code == 401
    assert "metriqen_test" not in missing.text + malformed.text


def test_postgres_profile_identity_scope_and_database_constraints(postgres_engine, signing_config):
    signing, settings = signing_config
    client = TestClient(create_app(settings, profile_store=SQLProfileStore(postgres_engine)))
    alice_headers = {"Authorization": f"Bearer {issue(signing, subject='user-a')}"}
    bob_headers = {"Authorization": f"Bearer {issue(signing, subject='user-b')}"}
    assert client.get("/api/v1/me", headers=alice_headers).status_code == 404
    assert client.post("/api/v1/me", json={"display_name": "Alice"}, headers=alice_headers).status_code == 201
    assert client.get("/api/v1/me", headers=bob_headers).status_code == 404
    assert client.patch("/api/v1/me", json={"display_name": "Stolen"}, headers=bob_headers).status_code == 404
    assert client.post("/api/v1/me", json={"display_name": "Bob"}, headers=bob_headers).status_code == 201
    assert client.patch("/api/v1/me", json={"display_name": "Alice 2"}, headers=alice_headers).status_code == 200
    assert client.get("/api/v1/me", headers=alice_headers).json()["display_name"] == "Alice 2"
    assert client.get("/api/v1/me", headers=bob_headers).json()["display_name"] == "Bob"
    assert client.post("/api/v1/me", json={"display_name": "duplicate"}, headers=alice_headers).status_code == 409

    inspector = inspect(postgres_engine)
    assert inspector.has_table("user_profiles")
    assert any(item["name"] == "ck_user_profiles_display_name"
               for item in inspector.get_check_constraints("user_profiles"))
    assert inspector.get_pk_constraint("user_profiles")["constrained_columns"] == ["issuer", "subject"]
    # Account creation never creates an organization membership or grant.
    with postgres_engine.connect() as conn:
        assert len(conn.execute(select(user_profiles)).all()) == 2
        assert conn.execute(select(organization_memberships)).all() == []



def test_real_postgres_new_org_is_atomic_and_project_writes_are_tenant_scoped(seeded, signing_config):
    engine, _, _, signing = seeded
    _, settings = signing_config
    client = TestClient(create_app(
        settings,
        workspace_store=SQLWorkspaceStore(engine),
    ))
    alice = {"Authorization": f"Bearer {issue(signing, subject='user-a')}"}
    bob = {"Authorization": f"Bearer {issue(signing, subject='user-b', role='owner')}"}
    new_org = client.post("/api/v1/organizations", json={"name": "New organization"},
                          headers=alice)
    assert new_org.status_code == 201
    org_id = new_org.json()["id"]
    # New org is self-provisioned only to the verified creator.
    assert new_org.json()["role"] == "owner"
    assert all(x["id"] != org_id for x in client.get(
        "/api/v1/organizations", headers=bob).json()["organizations"])
    assert client.post(f"/api/v1/organizations/{org_id}/projects",
                       json={"name": "Cross tenant"}, headers=bob).status_code == 404
    created = client.post(f"/api/v1/organizations/{org_id}/projects",
                          json={"name": "Inference"}, headers=alice)
    assert created.status_code == 201
    projects_list = client.get(f"/api/v1/organizations/{org_id}/projects",
                               headers=alice)
    assert projects_list.status_code == 200
    assert projects_list.json()["projects"] == [created.json()]
    assert client.get(f"/api/v1/organizations/{org_id}/projects",
                      headers=bob).status_code == 404
    with engine.connect() as conn:
        membership = conn.execute(
            select(organization_memberships).where(
                organization_memberships.c.organization_id == UUID(org_id)
            )
        ).all()
    assert len(membership) == 1
    assert membership[0].issuer == ISSUER
    assert membership[0].subject == "user-a"
    assert membership[0].role == "owner"


def test_real_postgres_inference_authorization_checks_current_project_membership(seeded, signing_config):
    """This uses the actual checked-in migration on ephemeral PostgreSQL 16."""
    engine, store, _, signing = seeded
    _, settings = signing_config
    from app.security.gateway_authorization import SECRET_HEADER
    secret = "synthetic-postgres-control-secret-0123456789abcdef"
    client = TestClient(create_app(
        settings, project_store=store, gateway_internal_token=secret,
    ), client=("127.0.0.1", 45111))
    uri = "/internal/gateway/authorize"
    jwt_value = issue(signing, subject="user-a", role="owner",
                      organization_id=str(ORG_B), permissions=["inference.invoke"])
    request_headers = {
        "Authorization": "Bearer " + jwt_value,
        SECRET_HEADER: secret,
    }

    def post(target):
        return client.post(uri, headers=request_headers, json={"project_id": str(target)})

    # Viewer with project.read must NOT get inference.invoke.
    assert post(PROJECT_A).status_code == 404
    assert post(PROJECT_B).status_code == 404
    with engine.begin() as conn:
        conn.execute(update(organization_memberships).where(
            organization_memberships.c.issuer == ISSUER,
            organization_memberships.c.subject == "user-a",
            organization_memberships.c.organization_id == ORG_A,
        ).values(role=Role.DEVELOPER.value))
    assert post(PROJECT_A).json() == {"authorized": True, "project_id": str(PROJECT_A)}
    assert post(PROJECT_A2).status_code == 404
    assert post(PROJECT_B).status_code == 404
    with engine.begin() as conn:
        conn.execute(delete(project_grants).where(
            project_grants.c.organization_id == ORG_A,
            project_grants.c.subject == "user-a",
        ))
    assert post(PROJECT_A).status_code == 404
    with engine.begin() as conn:
        conn.execute(update(organization_memberships).where(
            organization_memberships.c.issuer == ISSUER,
            organization_memberships.c.subject == "user-a",
            organization_memberships.c.organization_id == ORG_A,
        ).values(role=Role.OWNER.value))
    assert post(PROJECT_A2).status_code == 200
    assert post(PROJECT_B).status_code == 404
    with engine.begin() as conn:
        conn.execute(delete(organization_memberships).where(
            organization_memberships.c.issuer == ISSUER,
            organization_memberships.c.subject == "user-a",
            organization_memberships.c.organization_id == ORG_A,
        ))
    assert post(PROJECT_A).status_code == 404
    assert post(PROJECT_A2).status_code == 404


def test_concurrent_organization_creation_enforces_principal_limit(postgres_engine):
    """PostgreSQL transactional advisory lock must serialize first-org inserts."""
    identity = VerifiedIdentity(issuer=ISSUER, subject="limit-owner",
                                audience=AUDIENCE)
    store = SQLWorkspaceStore(postgres_engine)
    store.MAX_ORGANIZATIONS = 1  # Isolated test cap; no production changes.
    start = Barrier(6)

    def create_one(number):
        start.wait(timeout=10)
        try:
            store.create_organization(identity, f"Organization {number}")
            return "created"
        except WorkspaceLimitReached:
            return "limit"

    with ThreadPoolExecutor(max_workers=6) as workers:
        results = list(workers.map(create_one, range(6)))
    assert results.count("created") == 1
    assert results.count("limit") == 5
    with postgres_engine.connect() as conn:
        rows = conn.execute(select(organizations.c.id)).all()
        owners = conn.execute(select(organization_memberships.c.organization_id).where(
            organization_memberships.c.subject == identity.subject,
            organization_memberships.c.role == Role.OWNER.value,
        )).all()
    assert len(rows) == len(owners) == 1


def test_concurrent_owner_and_admin_project_creation_enforces_shared_org_limit(postgres_engine):
    """Lock the tenant row across independent members; no 101st project race."""
    owner = VerifiedIdentity(issuer=ISSUER, subject="quota-owner",
                             audience=AUDIENCE)
    admin = VerifiedIdentity(issuer=ISSUER, subject="quota-admin",
                             audience=AUDIENCE)
    store = SQLWorkspaceStore(postgres_engine)
    store.MAX_PROJECTS_PER_ORGANIZATION = 1
    org_id = UUID(store.create_organization(owner, "Shared organization")["id"])
    with postgres_engine.begin() as conn:
        conn.execute(insert(organization_memberships).values(
            issuer=ISSUER, subject=admin.subject, organization_id=org_id,
            role=Role.ADMIN.value,
        ))
    start = Barrier(6)

    def create_one(number):
        start.wait(timeout=10)
        try:
            store.create_project(
                owner if number % 2 == 0 else admin, org_id, f"Project {number}"
            )
            return "created"
        except WorkspaceLimitReached:
            return "limit"

    with ThreadPoolExecutor(max_workers=6) as workers:
        results = list(workers.map(create_one, range(6)))
    assert results.count("created") == 1
    assert results.count("limit") == 5
    with postgres_engine.connect() as conn:
        count = conn.execute(select(func.count()).select_from(projects).where(
            projects.c.organization_id == org_id,
        )).scalar_one()
    assert count == 1
