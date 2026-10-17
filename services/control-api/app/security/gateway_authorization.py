"""Loopback-only, service-authenticated authorization for Go inference gateway.

Checks signed access tokens AND current server-read PostgreSQL membership for
inference.invoke on a database-resolved project. Never trusts user role claims.
This is an opt-in local development seam, not production service-to-service TLS.
"""
from __future__ import annotations

from collections.abc import Mapping
import hmac
import os
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.security.http_identity import PinnedIdentitySettings, authenticated_identity
from app.security.policy import Membership, Permission, Resource, is_allowed
from app.security.project_access import ProjectAccessStore, ProjectRecord
from app.security.project_quota import RedisProjectQuota

SECRET_ENV = "METRIQEN_GATEWAY_INTERNAL_TOKEN"
SECRET_HEADER = "x-metriqen-gateway-secret"
LOCAL_PEERS = frozenset({"127.0.0.1", "::1"})


def load_gateway_internal_token(
    settings: PinnedIdentitySettings | None,
    store: ProjectAccessStore | None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Fail startup for an invalid or half-configured service credential."""
    env = os.environ if environ is None else environ
    token = env.get(SECRET_ENV)
    if token is None:
        return None
    if (not isinstance(token, str) or not 32 <= len(token) <= 256
            or any(ord(ch) < 33 or ord(ch) > 126 or ch in ',"' for ch in token)
            or settings is None or store is None):
        raise ValueError("local gateway authorization configuration is invalid")
    return token


class ProjectAuthorizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    project_id: str = Field(min_length=36, max_length=36)


def gateway_authorization_router(
    settings: PinnedIdentitySettings | None,
    store: ProjectAccessStore | None,
    service_secret: str | None,
    project_quota: RedisProjectQuota | None = None,
) -> APIRouter:
    router = APIRouter(include_in_schema=False)

    @router.post("/internal/gateway/authorize")
    def authorize(request: Request, payload: ProjectAuthorizationRequest):
        if settings is None or store is None or service_secret is None:
            raise HTTPException(503, "gateway authorization not configured")
        if request.client is None or request.client.host not in LOCAL_PEERS:
            raise HTTPException(403, "internal gateway access denied")
        headers = request.headers.getlist(SECRET_HEADER)
        if (len(headers) != 1 or not hmac.compare_digest(
            headers[0].encode("utf-8"), service_secret.encode("ascii")
        )):
            raise HTTPException(403, "internal gateway access denied")
        # A current dedicated access JWT is verified on *every* inference.
        principal = authenticated_identity(request, settings)
        try:
            project_id = UUID(payload.project_id)
            if str(project_id) != payload.project_id:
                raise ValueError("noncanonical project ID")
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(404, "project not found") from None
        try:
            project = store.get_project(project_id)
            if not isinstance(project, ProjectRecord) or project.project_id != project_id:
                raise HTTPException(404, "project not found")
            membership = store.get_membership(
                issuer=principal.issuer, subject=principal.subject,
                organization_id=project.organization_id,
            )
            if (not isinstance(membership, Membership) or not is_allowed(
                membership, Permission.INFERENCE_INVOKE,
                Resource(organization_id=project.organization_id,
                         project_id=project_id),
            )):
                raise HTTPException(404, "project not found")
            # Admission budget is decremented only AFTER the latest verified
            # identity and authoritative tenant permissions succeed.
            # A Redis transport error fails closed with a generic 503.
            if project_quota is not None:
                project_quota.enforce(project.organization_id, project_id)
            return {"authorized": True, "project_id": str(project_id)}
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "project authorization unavailable") from None

    return router
