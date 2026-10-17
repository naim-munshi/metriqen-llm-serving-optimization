"""Synthetic access JWT and current-membership authorization tests.

These intentionally cannot establish real identity-provider, TLS or Redis
behavior. Gateway shares a private service secret only over loopback.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.security.gateway_authorization import (
    gateway_authorization_router, load_gateway_internal_token,
)
from app.security.policy import Role
from test_project_access_http import (
    sandbox, signed_material, token, ISSUER, ORG_A, PROJECT_A, PROJECT_A2, PROJECT_B,
)

SECRET = "synthetic-service-0123456789abcdef-0123456789abcdef"
PATH = "/internal/gateway/authorize"


def checked(sandbox):
    _, store, signer, outside, settings = sandbox
    client = TestClient(
        create_app(settings, project_store=store, gateway_internal_token=SECRET),
        client=("127.0.0.1", 55000),
    )
    return client, store, signer, outside


def headers(signer, *, subject="user-a", secret=SECRET, **claims):
    return {"Authorization": "Bearer " + token(signer, subject=subject, **claims),
            "X-Metriqen-Gateway-Secret": secret}


def request(client, project, headers_):
    return client.post(PATH, json={"project_id": str(project)}, headers=headers_)


@pytest.mark.parametrize("role,grant,allowed", [
    (Role.OWNER, False, True),
    (Role.ADMIN, False, True),
    (Role.DEVELOPER, True, True),
    (Role.ML_ENGINEER, True, True),
    (Role.DEVELOPER, False, False),
    (Role.ML_ENGINEER, False, False),
    (Role.VIEWER, True, False),
    (Role.AUDITOR, True, False),
    (Role.DEPLOYMENT_OPERATOR, True, False),
])
def test_inference_permission_not_equal_project_read(sandbox, role, grant, allowed):
    client, store, signer, _ = checked(sandbox)
    store.grant(role, projects=(PROJECT_A,) if grant else ())
    res = request(client, PROJECT_A, headers(signer))
    assert res.status_code == (200 if allowed else 404)
    if allowed:
        assert res.json() == {"authorized": True, "project_id": str(PROJECT_A)}
    else:
        assert res.json() == {"detail": "project not found"}
    assert res.headers["cache-control"] == "no-store"


def test_token_and_tenant_claims_never_substitute_for_trusted_membership(sandbox):
    client, store, signer, outside = checked(sandbox)
    forged = headers(signer, role="owner", organization_id=str(ORG_A),
                     project_ids=[str(PROJECT_A)], permissions=["inference.invoke"])
    assert request(client, PROJECT_A, forged).status_code == 404
    store.grant(Role.DEVELOPER, projects=(PROJECT_A,))
    assert request(client, PROJECT_B, forged).status_code == 404
    assert request(client, PROJECT_A2, forged).status_code == 404
    assert request(client, PROJECT_A, headers(outside)).status_code == 401
    assert request(client, PROJECT_A, headers(signer, typ="JWT")).status_code == 401
    assert request(client, PROJECT_A, headers(signer, subject="different")).status_code == 404
    assert request(client, PROJECT_A, headers(signer, exp=0)).status_code == 401
    assert request(client, PROJECT_A, headers(signer)).status_code == 200
    del store.memberships[(ISSUER, "user-a", ORG_A)]
    assert request(client, PROJECT_A, headers(signer)).status_code == 404


def test_service_auth_and_loopback_denied_before_querying_projects(sandbox):
    client, store, signer, _ = checked(sandbox)
    store.grant(Role.OWNER)
    valid = headers(signer)
    assert client.post(PATH, json={"project_id": str(PROJECT_A)}).status_code == 403
    assert request(client, PROJECT_A, headers(signer, secret="bad")).status_code == 403
    assert request(client, PROJECT_A, {"X-Metriqen-Gateway-Secret": SECRET}).status_code == 401
    assert store.calls == []
    duplicated = [
        ("Authorization", valid["Authorization"]),
        ("X-Metriqen-Gateway-Secret", SECRET),
        ("X-Metriqen-Gateway-Secret", SECRET),
    ]
    assert request(client, PROJECT_A, duplicated).status_code == 403
    remote = TestClient(client.app, client=("192.0.2.10", 50000))
    assert request(remote, PROJECT_A, valid).status_code == 403
    assert store.calls == []


@pytest.mark.parametrize("bad,expected", [
    ("NOT-AN-ID", 422),
    ("AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA", 404),
    ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 422),
    ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaab", 404),
])
def test_invalid_or_absent_project_denied(sandbox, bad, expected):
    client, store, signer, _ = checked(sandbox)
    store.grant(Role.OWNER)
    assert request(client, bad, headers(signer)).status_code == expected
    assert client.post(PATH, json={}, headers=headers(signer)).status_code == 422
    assert client.post(PATH, json={"project_id": str(PROJECT_A),
                "role": "owner"}, headers=headers(signer)).status_code == 422


def test_unauthorized_or_unconfigured_store_never_returns_positive_decision(sandbox):
    client, store, signer, _ = checked(sandbox)
    store.grant(Role.OWNER)
    store.fail_membership = True
    failed = request(client, PROJECT_A, headers(signer))
    assert failed.status_code == 503
    assert "private" not in failed.text and SECRET not in failed.text
    _, _, _, _, settings = sandbox
    disabled = TestClient(create_app(settings, project_store=store))
    assert request(disabled, PROJECT_A, headers(signer)).status_code == 503
    absent = TestClient(create_app(settings, gateway_internal_token=SECRET))
    assert request(absent, PROJECT_A, headers(signer)).status_code == 503


@pytest.mark.parametrize("value", [
    "", "x" * 31, "x" * 257, "x" * 31 + "\n", "x" * 31 + " ", "x" * 31 + ",",
])
def test_gateway_service_secret_requires_strong_explicit_configuration(sandbox, value):
    _, store, _, _, settings = sandbox
    with pytest.raises(ValueError):
        load_gateway_internal_token(settings, store, {"METRIQEN_GATEWAY_INTERNAL_TOKEN": value})
    with pytest.raises(ValueError):
        load_gateway_internal_token(None, store, {"METRIQEN_GATEWAY_INTERNAL_TOKEN": SECRET})
    with pytest.raises(ValueError):
        load_gateway_internal_token(settings, None, {"METRIQEN_GATEWAY_INTERNAL_TOKEN": SECRET})
    assert load_gateway_internal_token(settings, store, {"METRIQEN_GATEWAY_INTERNAL_TOKEN": SECRET}) == SECRET
    assert load_gateway_internal_token(settings, store, {}) is None
