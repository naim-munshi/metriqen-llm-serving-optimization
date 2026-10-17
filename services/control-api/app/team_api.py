"""Verified-identity team APIs; PostgreSQL is the sole authority for roles.

Bearer endpoints are distinct from the local browser BFF, which uses a
revocable session plus exact Origin/CSRF checks. Neither trusts client roles.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.security.http_identity import PinnedIdentitySettings, authenticated_identity
from app.security.policy import Role
from app.security.redis_quota import RedisAccountQuota
from app.storage.team import SQLTeamStore, TeamConflict, TeamNotFound
from app.workspaces import canonical_org_id


class InviteInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target_subject: str = Field(min_length=1, max_length=255)
    role: Literal[
        "owner", "admin", "ml_engineer", "deployment_operator",
        "developer", "auditor", "viewer",
    ]


class RevokeMemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target_subject: str = Field(min_length=1, max_length=255)


def subject_value(value: str) -> str:
    # A subject is an exact opaque IdP value, NOT an email lookup or role claim.
    # No normalization is performed, avoiding Unicode/whitespace impersonation.
    if not value or value != value.strip() or not value.isprintable():
        raise HTTPException(422, "invalid exact identity subject")
    return value


def invitation_id(value: str) -> UUID:
    try:
        parsed = UUID(value)
        if str(parsed) != value:
            raise ValueError("not a canonical UUID")
        return parsed
    except (ValueError, TypeError):
        raise HTTPException(404, "invitation not found") from None


def team_safely(action: Callable):
    try:
        return action()
    except TeamNotFound:
        raise HTTPException(404, "team resource not found") from None
    except TeamConflict:
        raise HTTPException(409, "team resource conflict") from None
    except HTTPException:
        raise
    except Exception:
        # Do not expose database strings, SQL queries, or private identity data.
        raise HTTPException(503, "team database unavailable") from None


def require_team(store: SQLTeamStore | None) -> SQLTeamStore:
    if store is None:
        raise HTTPException(503, "team database is not configured")
    return store


def team_router(settings: PinnedIdentitySettings | None,
                store: SQLTeamStore | None,
                quota: RedisAccountQuota | None = None) -> APIRouter:
    router = APIRouter(tags=["teams"])

    def trusted(request: Request):
        principal = authenticated_identity(request, settings)
        return principal, require_team(store)

    def throttle(principal):
        if quota is not None:
            quota.enforce(principal)

    @router.get("/api/v1/invitations")
    def pending(request: Request):
        principal, team = trusted(request)
        return {"invitations": team_safely(lambda: team.pending(principal))}

    @router.post("/api/v1/invitations/{invite_id}/accept")
    def accept(request: Request, invite_id: str):
        principal, team = trusted(request)
        identifier = invitation_id(invite_id)
        throttle(principal)
        return team_safely(lambda: team.accept(principal, identifier))

    @router.post("/api/v1/organizations/{org_id}/invitations", status_code=201)
    def invite(request: Request, org_id: str, payload: InviteInput):
        principal, team = trusted(request)
        identifier = canonical_org_id(org_id)
        target = subject_value(payload.target_subject)
        throttle(principal)
        return team_safely(lambda: team.invite(principal, identifier, target, Role(payload.role)))

    @router.get("/api/v1/organizations/{org_id}/members")
    def members(request: Request, org_id: str):
        principal, team = trusted(request)
        identifier = canonical_org_id(org_id)
        return {"members": team_safely(lambda: team.members(principal, identifier))}

    @router.post("/api/v1/organizations/{org_id}/members/revoke")
    def revoke(request: Request, org_id: str, payload: RevokeMemberInput):
        principal, team = trusted(request)
        identifier = canonical_org_id(org_id)
        target = subject_value(payload.target_subject)
        throttle(principal)
        team_safely(lambda: team.revoke(principal, identifier, target))
        return {"revoked": True}

    @router.get("/api/v1/organizations/{org_id}/audit")
    def audit(request: Request, org_id: str):
        principal, team = trusted(request)
        identifier = canonical_org_id(org_id)
        return {"events": team_safely(lambda: team.audit(principal, identifier))}

    return router
