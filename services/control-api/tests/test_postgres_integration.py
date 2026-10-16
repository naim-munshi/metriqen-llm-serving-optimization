"""PostgreSQL 16 integration against the actual checked-in SQL migration.

This module is opt-in and ONLY accepts the ephemeral GitHub Actions
metriqen_ci database on 127.0.0.1. No SQLite substitutes, remote database,
automatic app startup migration, external IdP or production credentials.
"""
import base64
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from uuid import UUID

import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import delete, inspect, insert, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.main import create_app
from app.security.http_identity import PinnedIdentitySettings
from app.security.policy import Role
from app.storage.runtime import load_local_project_store
from app.storage.profiles import SQLProfileStore
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
