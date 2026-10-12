"""HTTP RBAC negative and positive tests with a trusted test-only store.

All keys, projects and memberships are ephemeral fixtures. No real identity
provider, production database or inference service is used in these tests.
"""
from datetime import datetime, timedelta, timezone
import base64
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import jwt
import pytest

from app.main import create_app
from app.security.http_identity import PinnedIdentitySettings
from app.security.policy import Membership, Role
from app.security.project_access import ProjectRecord


ORG_A = UUID("11111111-1111-4111-8111-111111111111")
ORG_B = UUID("22222222-2222-4222-8222-222222222222")
PROJECT_A = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PROJECT_A2 = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
PROJECT_B = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
ISSUER = "https://login.example.test/realms/metriqen"
AUDIENCE = "metriqen-control-api"
CLIENT = "metriqen-web"


def b64_int(value: int) -> str:
    return base64.urlsafe_b64encode(
        value.to_bytes((value.bit_length() + 7) // 8, "big")
    ).decode("ascii").rstrip("=")


class FakeTrustedStore:
    """Explicitly injected, mutable test fixture; NOT runtime persistence."""

    def __init__(self):
        self.projects = {
            PROJECT_A: ProjectRecord(PROJECT_A, ORG_A, "Project A"),
            PROJECT_A2: ProjectRecord(PROJECT_A2, ORG_A, "Project A2"),
            PROJECT_B: ProjectRecord(PROJECT_B, ORG_B, "Project B"),
        }
        self.memberships = {}
        self.calls = []
        self.fail_project = False
        self.fail_membership = False

    def get_project(self, project_id):
        self.calls.append(("project", project_id))
        if self.fail_project:
            raise RuntimeError("sensitive storage connection string")
        return self.projects.get(project_id)

    def get_membership(self, *, issuer, subject, organization_id):
        self.calls.append(("membership", issuer, subject, organization_id))
        if self.fail_membership:
            raise RuntimeError("private membership storage details")
        return self.memberships.get((issuer, subject, organization_id))

    def grant(self, role, *, subject="user-a", organization=ORG_A, projects=()):
        self.memberships[(ISSUER, subject, organization)] = Membership(
            organization_id=organization,
            role=role,
            project_ids=frozenset(projects),
        )


@pytest.fixture(scope="module")
def signed_material():
    signing = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    outside = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = signing.public_key().public_numbers()
    public = {"kty": "RSA", "kid": "trusted", "alg": "RS256", "use": "sig",
              "n": b64_int(numbers.n), "e": b64_int(numbers.e)}
    settings = PinnedIdentitySettings(
        issuer=ISSUER, audience=AUDIENCE, authorized_party=CLIENT,
        trusted_jwks={"keys": [public]},
    )
    return signing, outside, settings


@pytest.fixture
def sandbox(signed_material):
    signing, outside, settings = signed_material
    store = FakeTrustedStore()
    client = TestClient(create_app(settings, project_store=store))
    return client, store, signing, outside, settings


def token(signing, *, subject="user-a", typ="at+jwt", **claims_override):
    now = datetime.now(timezone.utc)
    claims = {
        "iss": ISSUER, "aud": AUDIENCE, "azp": CLIENT, "sub": subject,
        "iat": now - timedelta(seconds=15),
        "nbf": now - timedelta(seconds=15),
        "exp": now + timedelta(minutes=5),
    }
    claims.update(claims_override)
    return jwt.encode(claims, signing, algorithm="RS256",
                      headers={"kid": "trusted", "typ": typ})


def get(client, project_id, bearer):
    return client.get(f"/api/v1/projects/{project_id}",
                      headers={"Authorization": f"Bearer {bearer}"})


def test_default_app_has_no_project_access_or_business_data():
    client = TestClient(create_app())
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    result = client.get(f"/api/v1/projects/{PROJECT_A}")
    assert result.status_code == 503
    assert result.headers["cache-control"] == "no-store"


def test_verified_identity_alone_does_not_enable_projects(signed_material):
    signing, _, settings = signed_material
    client = TestClient(create_app(settings))
    result = get(client, PROJECT_A, token(signing))
    assert result.status_code == 503
    assert result.json() == {"detail": "project authorization store is not configured"}


def test_missing_token_denied_without_reaching_store(sandbox):
    client, store, _, _, _ = sandbox
    response = client.get(f"/api/v1/projects/{PROJECT_A}")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert store.calls == []


def test_forged_token_denied_without_reaching_store(sandbox):
    client, store, _, outside, _ = sandbox
    store.grant(Role.OWNER)
    response = get(client, PROJECT_A, token(outside))
    assert response.status_code == 401
    assert store.calls == []


@pytest.mark.parametrize("changes", [
    {"iss": "https://different.test"},
    {"aud": "untrusted-audience"},
    {"azp": "wrong-client"},
    {"sub": ""},
    {"exp": datetime.now(timezone.utc) - timedelta(minutes=5)},
])
def test_invalid_identity_rejected_without_store_access(sandbox, changes):
    client, store, signing, _, _ = sandbox
    store.grant(Role.OWNER)
    response = get(client, PROJECT_A, token(signing, **changes))
    assert response.status_code == 401
    assert store.calls == []


def test_id_token_does_not_access_project(sandbox):
    client, store, signing, _, _ = sandbox
    store.grant(Role.OWNER)
    assert get(client, PROJECT_A, token(signing, typ="JWT")).status_code == 401
    assert store.calls == []


@pytest.mark.parametrize("role", [Role.OWNER, Role.ADMIN])
def test_owner_and_admin_read_own_organization_projects(sandbox, role):
    client, store, signing, _, _ = sandbox
    store.grant(role, projects=())
    for project in (PROJECT_A, PROJECT_A2):
        result = get(client, project, token(signing))
        assert result.status_code == 200
        assert result.json()["id"] == str(project)
        assert result.json()["organization_id"] == str(ORG_A)
    assert get(client, PROJECT_B, token(signing)).status_code == 404


@pytest.mark.parametrize("role", [Role.VIEWER, Role.DEVELOPER, Role.ML_ENGINEER,
                                   Role.AUDITOR, Role.DEPLOYMENT_OPERATOR])
def test_project_scoped_roles_see_only_assigned_projects(sandbox, role):
    client, store, signing, _, _ = sandbox
    store.grant(role, projects=(PROJECT_A,))
    authorized = get(client, PROJECT_A, token(signing))
    assert authorized.status_code == 200
    assert authorized.json()["name"] == "Project A"
    assert get(client, PROJECT_A2, token(signing)).status_code == 404
    assert get(client, PROJECT_B, token(signing)).status_code == 404


def test_jwt_role_organization_and_project_claims_never_grant_access(sandbox):
    client, store, signing, _, _ = sandbox
    forged_claims = token(
        signing, role="owner", organization_id=str(ORG_A),
        project_ids=[str(PROJECT_A)], permissions=["project.read"],
    )
    assert get(client, PROJECT_A, forged_claims).status_code == 404
    assert store.calls == [("project", PROJECT_A),
                           ("membership", ISSUER, "user-a", ORG_A)]


def test_trusted_membership_must_match_resolved_organization(sandbox):
    client, store, signing, _, _ = sandbox
    store.memberships[(ISSUER, "user-a", ORG_A)] = Membership(
        organization_id=ORG_B, role=Role.OWNER,
    )
    assert get(client, PROJECT_A, token(signing)).status_code == 404


def test_membership_is_tied_to_verified_issuer_and_subject(sandbox):
    client, store, signing, _, _ = sandbox
    store.grant(Role.VIEWER, subject="user-b", projects=(PROJECT_A,))
    assert get(client, PROJECT_A, token(signing, subject="user-a")).status_code == 404
    result = get(client, PROJECT_A, token(signing, subject="user-b"))
    assert result.status_code == 200
    assert ("membership", ISSUER, "user-b", ORG_A) in store.calls


def test_membership_revocation_takes_effect_without_jwt_refresh(sandbox):
    client, store, signing, _, _ = sandbox
    store.grant(Role.VIEWER, projects=(PROJECT_A,))
    access_token = token(signing)
    assert get(client, PROJECT_A, access_token).status_code == 200
    del store.memberships[(ISSUER, "user-a", ORG_A)]
    assert get(client, PROJECT_A, access_token).status_code == 404


def test_wrong_project_organization_never_trusts_client_claim(sandbox):
    client, store, signing, _, _ = sandbox
    store.grant(Role.OWNER, organization=ORG_A)
    store.projects[PROJECT_B] = ProjectRecord(PROJECT_B, ORG_B, "Private B")
    result = get(client, PROJECT_B, token(signing, organization_id=str(ORG_A)))
    assert result.status_code == 404
    assert "Private B" not in result.text


def test_unknown_and_unauthorized_projects_indistinguishable(sandbox):
    client, store, signing, _, _ = sandbox
    store.grant(Role.VIEWER, projects=())
    hidden = get(client, PROJECT_A, token(signing))
    missing = get(client, UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"), token(signing))
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json() == {"detail": "project not found"}


@pytest.mark.parametrize("bad_id", ["not-a-uuid", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                                    "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaajunk"])
def test_invalid_project_identifier_fails_closed_after_authentication(sandbox, bad_id):
    client, store, signing, _, _ = sandbox
    store.grant(Role.OWNER)
    result = get(client, bad_id, token(signing))
    assert result.status_code == 404
    assert store.calls == []


@pytest.mark.parametrize("which", ["project", "membership"])
def test_store_outage_is_generic_503_and_never_leaks_sensitive_error(sandbox, which):
    client, store, signing, _, _ = sandbox
    store.grant(Role.OWNER)
    if which == "project":
        store.fail_project = True
    else:
        store.fail_membership = True
    result = get(client, PROJECT_A, token(signing))
    assert result.status_code == 503
    assert result.json() == {"detail": "project authorization unavailable"}
    assert "sensitive" not in result.text
    assert "private" not in result.text


def test_store_returning_wrong_project_identity_is_denied(sandbox):
    client, store, signing, _, _ = sandbox
    store.grant(Role.OWNER)
    store.projects[PROJECT_A] = ProjectRecord(PROJECT_B, ORG_A, "Misrouted")
    assert get(client, PROJECT_A, token(signing)).status_code == 404


def test_invalid_role_from_store_never_authorizes(sandbox):
    client, store, signing, _, _ = sandbox
    store.memberships[(ISSUER, "user-a", ORG_A)] = Membership(
        organization_id=ORG_A, role="owner", project_ids=frozenset({PROJECT_A}),
    )
    assert get(client, PROJECT_A, token(signing)).status_code == 404


def test_read_only_contract_does_not_allow_writes(sandbox):
    client, store, signing, _, _ = sandbox
    store.grant(Role.OWNER)
    route = f"/api/v1/projects/{PROJECT_A}"
    assert client.post(route, headers={"Authorization": f"Bearer {token(signing)}"}).status_code == 405
    assert client.delete(route, headers={"Authorization": f"Bearer {token(signing)}"}).status_code == 405


def test_no_identity_data_or_role_claims_leaked_in_project_response(sandbox):
    client, store, signing, _, _ = sandbox
    store.grant(Role.VIEWER, projects=(PROJECT_A,))
    token_value = token(signing, role="owner", secret="NEVER_PRINT_PRIVATE_CLAIMS")
    result = get(client, PROJECT_A, token_value)
    assert result.status_code == 200
    assert result.json() == {
        "id": str(PROJECT_A),
        "organization_id": str(ORG_A),
        "name": "Project A",
    }
    assert token_value not in result.text
    assert "NEVER_PRINT_PRIVATE_CLAIMS" not in result.text
    assert result.headers["cache-control"] == "no-store"
