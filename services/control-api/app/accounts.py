"""OIDC-authenticated, server-resolved current-user profile endpoints.

No password signup, IdP session, org membership or JWT role claim is created.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.security.http_identity import PinnedIdentitySettings, authenticated_identity
from app.storage.profiles import ProfileAlreadyExists, ProfileStore
from app.security.redis_quota import RedisAccountQuota


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    display_name: str = Field(min_length=1, max_length=120)


def canonical_name(name: str) -> str:
    value = name.strip()
    if (not value or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or len(value) > 120):
        raise HTTPException(422, "display_name must be 1-120 printable characters")
    return value


def profile_router(identity_settings: PinnedIdentitySettings | None,
                   store: ProfileStore | None,
                   quota: RedisAccountQuota | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/v1/me", tags=["accounts"])

    def verified(request: Request):
        identity = authenticated_identity(request, identity_settings)
        if store is None:
            raise HTTPException(503, "account profile store is not configured")
        return identity

    @router.get("")
    def get_me(request: Request) -> dict:
        identity = verified(request)
        try:
            record = store.get(identity)
        except Exception:
            raise HTTPException(503, "account profile temporarily unavailable") from None
        if record is None:
            raise HTTPException(404, "account profile not yet created")
        return record

    @router.post("", status_code=201)
    def create_me(request: Request, payload: ProfileInput) -> dict:
        identity = verified(request)
        name = canonical_name(payload.display_name)
        if quota is not None:
            quota.enforce(identity)
        try:
            return store.create(identity, name)
        except ProfileAlreadyExists:
            raise HTTPException(409, "account profile already exists") from None
        except Exception:
            raise HTTPException(503, "account profile temporarily unavailable") from None

    @router.patch("")
    def update_me(request: Request, payload: ProfileInput) -> dict:
        identity = verified(request)
        name = canonical_name(payload.display_name)
        if quota is not None:
            quota.enforce(identity)
        try:
            record = store.change_name(identity, name)
        except Exception:
            raise HTTPException(503, "account profile temporarily unavailable") from None
        if record is None:
            raise HTTPException(404, "account profile not yet created")
        return record

    return router
