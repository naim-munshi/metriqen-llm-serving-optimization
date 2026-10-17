"""Storage + HTTP integration with SQLAlchemy Core and SQLite FK enforcement.

SQLite is a deterministic CI backend only. These are not claims about a
running PostgreSQL cluster, live identity provider or deployment security.
"""
from datetime import datetime, timedelta, timezone
import base64
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import jwt
import pytest
from sqlalchemy import create_engine, delete, event, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from app.main import create_app
from app.security.http_identity import PinnedIdentitySettings
from app.security.policy import Role
from app.storage.project_access import SQLProjectAccessStore
from app.storage.schema import (
    metadata, organization_memberships, organizations, project_grants, projects,
)

ORG_A = UUID("11111111-1111-4111-8111-111111111111")
ORG_B = UUID("22222222-2222-4222-8222-222222222222")
PROJECT_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PROJECT_A2 = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
PROJECT_B = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
ISSUER = "https://login.example.test/realms/metriqen"
AUDIENCE = "metriqen-control-api"
CLIENT = "metriqen-web"


def b64_int(value):
    return base64.urlsafe_b64encode(
        value.to_bytes((value.bit_length() + 7) // 8, "big")
    ).decode("ascii").rstrip("=")


@pytest.fixture(scope="module")
def signing_config():
    signing = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = signing.public_key().public_numbers()
    key = {"kty": "RSA", "kid": "trusted", "alg": "RS256", "use": "sig",
           "n": b64_int(numbers.n), "e": b64_int(numbers.e)}
    settings = PinnedIdentitySettings(
        issuer=ISSUER, audience=AUDIENCE, authorized_party=CLIENT,
        trusted_jwks={"keys": [key]},
    )
    return signing, settings


@pytest.fixture
def engine():
    db = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(db, "connect")
    def enable_foreign_keys(conn, _):
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    metadata.create_all(db)  # ONLY test fixture: production uses migrations.
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture
def seeded(engine, signing_config):
    with engine.begin() as conn:
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
            {"issuer": ISSUER, "subject": "user-a", "organization_id": ORG_A,
             "role": Role.VIEWER.value},
            {"issuer": ISSUER, "subject": "user-b", "organization_id": ORG_B,
             "role": Role.OWNER.value},
        ])
        conn.execute(insert(project_grants), [
            {"issuer": ISSUER, "subject": "user-a", "organization_id": ORG_A,
             "project_id": PROJECT_A},
        ])
    signing, settings = signing_config
    store = SQLProjectAccessStore(engine)
    return engine, store, TestClient(create_app(settings, project_store=store)), signing


def issue(signing, *, subject="user-a", **extra):
    now = datetime.now(timezone.utc)
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": subject, "azp": CLIENT,
              "iat": now - timedelta(seconds=10),
              "nbf": now - timedelta(seconds=10),
              "exp": now + timedelta(minutes=5)}
    claims.update(extra)
    return jwt.encode(
        claims, signing, algorithm="RS256", headers={"kid": "trusted", "typ": "at+jwt"},
    )


def get(client, target, signed):
    return client.get(
        f"/api/v1/projects/{target}",
        headers={"Authorization": f"Bearer {signed}"},
    )


def test_importing_schema_does_not_implicitly_create_tables():
    clean = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool)
    try:
        from sqlalchemy import inspect
        assert inspect(clean).get_table_names() == []
    finally:
        clean.dispose()


def test_sql_schema_has_expected_tenant_tables(engine):
    from sqlalchemy import inspect
    assert set(inspect(engine).get_table_names()) == {
        "organizations", "projects", "organization_memberships", "project_grants",
        "user_profiles", "organization_invitations", "team_audit_events",
    }


def test_storage_adapter_requires_trusted_engine():
    with pytest.raises(TypeError):
        SQLProjectAccessStore("sqlite:///bad.db")


def test_project_scoping_comes_from_persisted_org(seeded):
    _, store, _, _ = seeded
    a = store.get_project(PROJECT_A)
    b = store.get_project(PROJECT_B)
    assert (a.organization_id, a.name) == (ORG_A, "Project A")
    assert (b.organization_id, b.name) == (ORG_B, "Project B")
    assert store.get_project(UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")) is None


def test_scoped_member_grants_loaded_from_db(seeded):
    _, store, _, _ = seeded
    membership = store.get_membership(
        issuer=ISSUER, subject="user-a", organization_id=ORG_A,
    )
    assert membership.role is Role.VIEWER
    assert membership.project_ids == frozenset({PROJECT_A})
    assert store.get_membership(issuer=ISSUER, subject="user-a",
                                organization_id=ORG_B) is None


def test_authorized_project_read_through_real_sql_adapter(seeded):
    _, _, client, signing = seeded
    result = get(client, PROJECT_A, issue(signing))
    assert result.status_code == 200
    assert result.json() == {
        "id": str(PROJECT_A), "organization_id": str(ORG_A), "name": "Project A",
    }
    assert result.headers["cache-control"] == "no-store"


def test_assigned_member_denied_unassigned_project(seeded):
    _, _, client, signing = seeded
    assert get(client, PROJECT_A2, issue(signing)).status_code == 404


def test_cross_org_project_looks_unknown_even_for_valid_jwt(seeded):
    _, _, client, signing = seeded
    forbidden = get(client, PROJECT_B, issue(signing, role="owner",
                                               organization_id=str(ORG_B)))
    unknown = get(client, UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
                  issue(signing))
    assert forbidden.status_code == unknown.status_code == 404
    assert forbidden.json() == unknown.json()


def test_owner_can_read_only_own_org_even_without_project_grants(seeded):
    _, _, client, signing = seeded
    assert get(client, PROJECT_B, issue(signing, subject="user-b")).status_code == 200
    assert get(client, PROJECT_A, issue(signing, subject="user-b")).status_code == 404


def test_jwt_tenant_claim_forgery_cannot_override_db(seeded):
    _, _, client, signing = seeded
    spoofed = issue(signing, role="owner", organization_id=str(ORG_B),
                    project_ids=[str(PROJECT_B)], permissions=["project.read"])
    assert get(client, PROJECT_B, spoofed).status_code == 404
    assert get(client, PROJECT_A, spoofed).status_code == 200


def test_subject_and_issuer_queries_are_parameter_bound(seeded):
    _, store, _, _ = seeded
    for subject in ("user-a' OR 1=1 --", "user-b", "not-a-member"):
        assert store.get_membership(issuer=ISSUER, subject=subject,
                                    organization_id=ORG_A) is None
    assert store.get_membership(issuer=ISSUER + "' OR 1=1 --",
                                subject="user-a", organization_id=ORG_A) is None


def test_composite_fk_denies_cross_tenant_grant(seeded):
    engine, _, _, _ = seeded
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(insert(project_grants).values(
                issuer=ISSUER, subject="user-a",
                organization_id=ORG_A, project_id=PROJECT_B,
            ))


def test_foreign_key_requires_real_membership(seeded):
    engine, _, _, _ = seeded
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(insert(project_grants).values(
                issuer=ISSUER, subject="unknown",
                organization_id=ORG_A, project_id=PROJECT_A,
            ))


def test_role_check_constraint_rejects_invalid_role(engine):
    with engine.begin() as conn:
        conn.execute(insert(organizations).values(id=ORG_A, name="Organization A"))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(insert(organization_memberships).values(
                issuer=ISSUER, subject="user-a", organization_id=ORG_A,
                role="superadmin-bypass",
            ))


def test_deleting_membership_revokes_access_and_cascades_grants(seeded):
    engine, store, client, signing = seeded
    saved_token = issue(signing)
    assert get(client, PROJECT_A, saved_token).status_code == 200
    with engine.begin() as conn:
        conn.execute(delete(organization_memberships).where(
            organization_memberships.c.issuer == ISSUER,
            organization_memberships.c.subject == "user-a",
            organization_memberships.c.organization_id == ORG_A,
        ))
    assert get(client, PROJECT_A, saved_token).status_code == 404
    assert store.get_membership(issuer=ISSUER, subject="user-a",
                                organization_id=ORG_A) is None
    with engine.connect() as conn:
        assert conn.execute(select(project_grants)).all() == []


def test_removing_single_grant_revokes_only_that_project(seeded):
    engine, _, client, signing = seeded
    saved_token = issue(signing)
    with engine.begin() as conn:
        conn.execute(insert(project_grants).values(
            issuer=ISSUER, subject="user-a", organization_id=ORG_A,
            project_id=PROJECT_A2,
        ))
    assert get(client, PROJECT_A2, saved_token).status_code == 200
    with engine.begin() as conn:
        conn.execute(delete(project_grants).where(
            project_grants.c.project_id == PROJECT_A2,
        ))
    assert get(client, PROJECT_A2, saved_token).status_code == 404
    assert get(client, PROJECT_A, saved_token).status_code == 200


def test_membership_role_changed_in_db_takes_effect_next_request(seeded):
    engine, _, client, signing = seeded
    saved_token = issue(signing)
    assert get(client, PROJECT_A2, saved_token).status_code == 404
    with engine.begin() as conn:
        conn.execute(update(organization_memberships).where(
            organization_memberships.c.issuer == ISSUER,
            organization_memberships.c.subject == "user-a",
            organization_memberships.c.organization_id == ORG_A,
        ).values(role=Role.ADMIN.value))
    assert get(client, PROJECT_A2, saved_token).status_code == 200
    assert get(client, PROJECT_B, saved_token).status_code == 404


def test_unavailable_database_does_not_leak_sql_errors(signing_config):
    signing, settings = signing_config
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    try:
        client = TestClient(create_app(settings, project_store=SQLProjectAccessStore(engine)))
        result = get(client, PROJECT_A, issue(signing))
        assert result.status_code == 503
        assert result.json() == {"detail": "project authorization unavailable"}
        assert "sql" not in result.text.lower()
    finally:
        engine.dispose()


def test_missing_auth_short_circuits_store_queries(seeded):
    _, _, client, _ = seeded
    response = client.get(f"/api/v1/projects/{PROJECT_A}")
    assert response.status_code == 401


def test_no_identity_or_inference_secrets_in_project_response(seeded):
    _, _, client, signing = seeded
    jwt_value = issue(signing, role="superadmin",
                      secret="NEVER_EXPOSE_THIS_SECRET")
    r = get(client, PROJECT_A, jwt_value)
    assert r.status_code == 200
    assert jwt_value not in r.text
    assert "NEVER_EXPOSE_THIS_SECRET" not in r.text
