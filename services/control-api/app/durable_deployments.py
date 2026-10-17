"""Verified OIDC project deployment desired-state API, local opt-in only.

This stores durable commands but DOES NOT deploy models, contact GPU workers,
or claim a revision is ready. All permission checks use current SQL records.
"""
from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.security.http_identity import PinnedIdentitySettings, authenticated_identity
from app.security.redis_quota import RedisAccountQuota
from app.storage.durable_deployments import (
    DeploymentNotFound, DeploymentConflict, SQLDurableDeploymentStore,
)

_DEPLOYMENT_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
_MODEL_TAG = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}(?::[A-Za-z0-9][A-Za-z0-9_.-]{0,39})?\Z"
)
_MODEL_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_-]{16,80}\Z")


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model_tag: str = Field(min_length=1, max_length=128)
    model_digest: str = Field(min_length=64, max_length=64)


class DeploymentCreate(Revision):
    name: str = Field(min_length=1, max_length=32)


class DeploymentPromote(Revision):
    expected_generation: int = Field(ge=1, le=1000000, strict=True)


class DeploymentMutation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_generation: int = Field(ge=1, le=1000000, strict=True)


def canonical_project(value: str) -> UUID:
    try:
        parsed = UUID(value)
        if str(parsed) != value:
            raise ValueError("noncanonical UUID")
        return parsed
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(404, "deployment resource not found") from None


def clean_name(value: str) -> str:
    if _DEPLOYMENT_NAME.fullmatch(value) is None:
        raise HTTPException(422, "invalid deployment name")
    return value


def clean_revision(tag: str, digest: str) -> tuple[str, str]:
    if _MODEL_TAG.fullmatch(tag) is None or _MODEL_DIGEST.fullmatch(digest) is None:
        raise HTTPException(422, "invalid desired model revision")
    return tag, digest


def required_key(request: Request) -> str:
    values = request.headers.getlist("idempotency-key")
    if len(values) != 1 or _IDEMPOTENCY_KEY.fullmatch(values[0]) is None:
        raise HTTPException(422, "one 16–80 character Idempotency-Key is required")
    return values[0]


def safely(action):
    try:
        return action()
    except DeploymentNotFound:
        raise HTTPException(404, "deployment resource not found") from None
    except DeploymentConflict:
        raise HTTPException(409, "deployment version, name or idempotency conflict") from None
    except HTTPException:
        raise
    except Exception:
        # Fail closed. Never echo DB DDL, credentials, tenant existence or SQL.
        raise HTTPException(503, "deployment database unavailable") from None


def durable_deployments_router(
    settings: PinnedIdentitySettings | None,
    store: SQLDurableDeploymentStore | None,
    account_quota: RedisAccountQuota | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/projects/{project_id}/deployments",
                       tags=["deployment desired state"])

    def trusted(request: Request, project_id: str):
        principal = authenticated_identity(request, settings)
        if store is None:
            raise HTTPException(503, "deployment database not configured")
        return principal, canonical_project(project_id)

    def mutation(request: Request, principal):
        key = required_key(request)
        if account_quota is not None:
            account_quota.enforce(principal)
        return key

    @router.get("")
    def list_deployments(request: Request, project_id: str):
        principal, pid = trusted(request, project_id)
        return {"deployments": safely(lambda: store.list(principal, pid))}

    @router.get("/{name}")
    def get_deployment(request: Request, project_id: str, name: str):
        principal, pid = trusted(request, project_id)
        name = clean_name(name)
        return safely(lambda: store.get(principal, pid, name))

    @router.post("", status_code=201)
    def create(request: Request, project_id: str, payload: DeploymentCreate):
        principal, pid = trusted(request, project_id)
        name = clean_name(payload.name)
        tag, digest = clean_revision(payload.model_tag, payload.model_digest)
        key = mutation(request, principal)
        return safely(lambda: store.create(principal, pid, name, tag, digest, key))

    @router.post("/{name}/promote")
    def promote(request: Request, project_id: str, name: str,
                payload: DeploymentPromote):
        principal, pid = trusted(request, project_id)
        name = clean_name(name)
        tag, digest = clean_revision(payload.model_tag, payload.model_digest)
        key = mutation(request, principal)
        return safely(lambda: store.change(
            principal, pid, name, "promote", key, payload.expected_generation,
            tag=tag, digest=digest,
        ))

    @router.post("/{name}/rollback")
    def rollback(request: Request, project_id: str, name: str,
                 payload: DeploymentMutation):
        principal, pid = trusted(request, project_id)
        name = clean_name(name)
        key = mutation(request, principal)
        return safely(lambda: store.change(
            principal, pid, name, "rollback", key, payload.expected_generation,
        ))

    @router.post("/{name}/stop")
    def stop(request: Request, project_id: str, name: str,
             payload: DeploymentMutation):
        principal, pid = trusted(request, project_id)
        name = clean_name(name)
        key = mutation(request, principal)
        return safely(lambda: store.change(
            principal, pid, name, "stop", key, payload.expected_generation,
        ))

    @router.post("/{name}/start")
    def start(request: Request, project_id: str, name: str,
              payload: DeploymentMutation):
        principal, pid = trusted(request, project_id)
        name = clean_name(name)
        key = mutation(request, principal)
        return safely(lambda: store.change(
            principal, pid, name, "start", key, payload.expected_generation,
        ))

    return router
