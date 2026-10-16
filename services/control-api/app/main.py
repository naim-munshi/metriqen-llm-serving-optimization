"""Local-only Metriqen control API with explicit auth and project-read seams.

There is no configured membership database, user login, inference API-key
system or production authorization. Do not deploy this API publicly.
"""

from fastapi import FastAPI, Request

from app.security.http_identity import (
    PinnedIdentitySettings,
    authenticated_identity,
    load_identity_settings,
)
from app.security.project_access import ProjectAccessStore, read_authorized_project
from app.storage.runtime import load_local_project_store
from app.local_models import configure_local_models
from app.accounts import profile_router
from app.storage.profiles import ProfileStore, SQLProfileStore
from app.security.redis_quota import RedisAccountQuota, load_account_quota
from app.browser_auth import BrowserLogin, browser_configured, browser_login_router
from app.workspaces import organization_router
from app.storage.workspaces import SQLWorkspaceStore


def create_app(
    identity_settings: PinnedIdentitySettings | None = None,
    *,
    project_store: ProjectAccessStore | None = None,
    profile_store: ProfileStore | None = None,
    account_quota: RedisAccountQuota | None = None,
    browser_login: BrowserLogin | None = None,
    workspace_store: SQLWorkspaceStore | None = None,
) -> FastAPI:
    """Create isolated app instances; trusted stores are explicitly injected."""
    application = FastAPI(
        title="Metriqen Control API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @application.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @application.get("/health/live", tags=["operations"])
    def live() -> dict[str, str]:
        return {"service": "metriqen-control-api", "status": "ok"}

    @application.get("/health/ready", tags=["operations"])
    def ready() -> dict[str, str]:
        # This does not attest model availability or IdP connectivity.
        return {"service": "metriqen-control-api", "status": "ready"}

    @application.get("/api/v1/identity", tags=["security"])
    def identity(request: Request) -> dict[str, str]:
        verified = authenticated_identity(request, identity_settings)
        # No org, role, or project data is taken from untrusted JWT claims.
        return {"subject": verified.subject, "issuer": verified.issuer,
                "audience": verified.audience}

    @application.get("/api/v1/projects/{project_id}", tags=["projects"])
    def project_detail(request: Request, project_id: str) -> dict[str, str]:
        return read_authorized_project(
            request, project_id,
            identity_settings=identity_settings,
            store=project_store,
        )

    # All account writes require a fresh, verified access identity; no
    # browser-provided issuer, subject or JWT role can select the DB row.
    application.include_router(profile_router(identity_settings, profile_store,
                                              account_quota))

    # JWT authorizes the principal, PostgreSQL membership authorizes the
    # tenant resource. No JWT-supplied role or organization creates a grant.
    application.include_router(organization_router(identity_settings, workspace_store,
                                                  account_quota))

    # Browser authentication is an explicit localhost-only OIDC/Redis opt-in.
    # No login/session routes or static account page are exposed by default.
    if browser_login is not None:
        application.include_router(browser_login_router(browser_login, profile_store,
                                                        account_quota, workspace_store))

    # Operator-only model lifecycle is opt-in and never substitutes for
    # project-scoped identity or inference gateway authorization.
    configure_local_models(application)
    return application


# Load only operator-pinned local configuration at startup; no SQL connection
# or schema migration runs here. Without a DSN, project reads still deny 503.
_configured_project_store = load_local_project_store()
_configured_identity = load_identity_settings()
app = create_app(
    _configured_identity,
    project_store=_configured_project_store,
    profile_store=(SQLProfileStore(_configured_project_store._engine)
                   if _configured_project_store is not None else None),
    account_quota=load_account_quota(),
    browser_login=browser_configured(None, _configured_identity),
    workspace_store=(SQLWorkspaceStore(_configured_project_store._engine)
                     if _configured_project_store is not None else None),
)
