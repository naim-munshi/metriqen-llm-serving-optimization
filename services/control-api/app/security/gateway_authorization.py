"""Loopback-only, service-authenticated authorization for Go inference gateway.

Checks signed access tokens AND current server-read PostgreSQL membership for
inference.invoke on a database-resolved project. Never trusts user role claims.
This is an opt-in local development seam, not production service-to-service TLS.
"""
from __future__ import annotations

from collections.abc import Mapping
import hmac
import os
import re
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.security.http_identity import PinnedIdentitySettings, authenticated_identity
from app.security.policy import Membership, Permission, Resource, is_allowed
from app.security.project_access import ProjectAccessStore, ProjectRecord
from app.security.project_quota import RedisProjectQuota
from app.storage.deployment_routing import (
    DeploymentRoute, SQLDeploymentRoutingStore, RouteNotFound, RouteNotReady,
)

SECRET_ENV = "METRIQEN_GATEWAY_INTERNAL_TOKEN"
SECRET_HEADER = "x-metriqen-gateway-secret"
LOCAL_PEERS = frozenset({"127.0.0.1", "::1"})
ROUTING_ENV = "METRIQEN_ENFORCE_DEPLOYMENT_ROUTING"
_DEPLOYMENT_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}\\Z")


def load_deployment_routing_mode(environ: Mapping[str, str] | None = None) -> bool:
    raw = (os.environ if environ is None else environ).get(ROUTING_ENV, "")
    if raw not in ("", "0", "1"):
        raise ValueError("deployment routing requires an explicit 0 or 1")
    return raw == "1"


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
    deployment: str | None = Field(default=None, min_length=1, max_length=32)


def gateway_authorization_router(
    settings: PinnedIdentitySettings | None,
    store: ProjectAccessStore | None,
    service_secret: str | None,
    project_quota: RedisProjectQuota | None = None,
    *,
    deployment_routing_store: SQLDeploymentRoutingStore | None = None,
    require_deployment_routing: bool = False,
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
            if require_deployment_routing:
                if (payload.deployment is None or
                        _DEPLOYMENT_NAME.fullmatch(payload.deployment) is None):
                    raise HTTPException(422, "valid deployment name required")
                if deployment_routing_store is None:
                    raise HTTPException(503, "deployment routing not configured")
                try:
                    route = deployment_routing_store.resolve(
                        principal, project_id, payload.deployment,
                    )
                except RouteNotFound:
                    raise HTTPException(404, "project not found") from None
                except RouteNotReady:
                    raise HTTPException(409, "deployment not ready") from None
                if (not isinstance(route, DeploymentRoute) or
                        route.project_id != project_id or
                        route.deployment != payload.deployment):
                    raise HTTPException(503, "deployment authorization unavailable")
                if project_quota is not None:
                    project_quota.enforce(route.organization_id, project_id)
                return {
                    "authorized": True, "project_id": str(project_id),
                    "deployment": route.deployment,
                    "model_tag": route.model_tag,
                    "model_digest": route.model_digest,
                    "generation": route.generation,
                }
            if payload.deployment is not None:
                # The old project-only guard must never silently accept a
                # caller's deployment header as a routing or readiness proof.
                raise HTTPException(409, "deployment routing not enabled")
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
