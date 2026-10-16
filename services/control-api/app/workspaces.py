"""OIDC-verification-gated organization/project API (no client-supplied roles)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.security.http_identity import PinnedIdentitySettings, authenticated_identity
from app.security.redis_quota import RedisAccountQuota
from app.storage.workspaces import SQLWorkspaceStore, WorkspaceNotFound, WorkspaceLimitReached


class WorkspaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=200)


def clean_workspace_name(name: str) -> str:
    text = name.strip()
    if not text or len(text) > 200 or any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise HTTPException(422, "workspace name must be 1-200 printable characters")
    return text


def canonical_org_id(value: str) -> UUID:
    try:
        result = UUID(value)
        if str(result) != value:
            raise ValueError("noncanonical identifier")
        return result
    except (ValueError, TypeError):
        raise HTTPException(404, "organization not found") from None


def organization_router(settings: PinnedIdentitySettings | None,
                        store: SQLWorkspaceStore | None,
                        quota: RedisAccountQuota | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/v1/organizations", tags=["organizations"])

    def trusted(request: Request):
        principal = authenticated_identity(request, settings)
        if store is None:
            raise HTTPException(503, "organization database is not configured")
        return principal

    def safely(call):
        try:
            return call()
        except WorkspaceNotFound:
            raise HTTPException(404, "organization not found") from None
        except WorkspaceLimitReached:
            raise HTTPException(409, "workspace resource limit reached") from None
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "organization database unavailable") from None

    @router.get("")
    def list_organizations(request: Request):
        principal = trusted(request)
        return {"organizations": safely(lambda: store.list_organizations(principal))}

    @router.post("", status_code=201)
    def create_organization(request: Request, payload: WorkspaceInput):
        principal = trusted(request)
        name = clean_workspace_name(payload.name)
        if quota is not None:
            quota.enforce(principal)
        return safely(lambda: store.create_organization(principal, name))

    @router.get("/{org_id}/projects")
    def list_projects(request: Request, org_id: str):
        principal = trusted(request)
        identifier = canonical_org_id(org_id)
        return {"projects": safely(lambda: store.list_projects(principal, identifier))}

    @router.post("/{org_id}/projects", status_code=201)
    def create_project(request: Request, org_id: str, payload: WorkspaceInput):
        principal = trusted(request)
        identifier = canonical_org_id(org_id)
        name = clean_workspace_name(payload.name)
        if quota is not None:
            quota.enforce(principal)
        return safely(lambda: store.create_project(principal, identifier, name))

    return router
