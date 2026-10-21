"""Local-only Metriqen control plane with opt-in identity and policy checks.

The default process has no user login, provisioned PostgreSQL membership store,
or public/production authorization. Optional pinned-JWKS OIDC browser sessions,
project RBAC, local operator management and Redis controls require independent
trusted configuration. Never expose this development API publicly.
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
from app.storage.team import SQLTeamStore
from app.storage.durable_deployments import SQLDurableDeploymentStore
from app.storage.deployment_routing import SQLDeploymentRoutingStore
from app.durable_deployments import durable_deployments_router
from app.team_api import team_router
from app.security.gateway_authorization import (gateway_authorization_router, load_gateway_internal_token, load_deployment_routing_mode)
from app.security.project_quota import RedisProjectQuota, load_project_quota
from app.security.project_inflight import RedisProjectInflight, load_project_inflight
from app.security.project_output_reservations import (
    RedisProjectOutputReservations, load_project_output_reservations,
)


def create_app(
    identity_settings: PinnedIdentitySettings | None = None,
    *,
    project_store: ProjectAccessStore | None = None,
    profile_store: ProfileStore | None = None,
    account_quota: RedisAccountQuota | None = None,
    browser_login: BrowserLogin | None = None,
    workspace_store: SQLWorkspaceStore | None = None,
    team_store: SQLTeamStore | None = None,
    durable_deployment_store: SQLDurableDeploymentStore | None = None,
    gateway_internal_token: str | None = None,
    project_quota: RedisProjectQuota | None = None,
    project_inflight: RedisProjectInflight | None = None,
    project_output_reservations: RedisProjectOutputReservations | None = None,
    deployment_routing_store: SQLDeploymentRoutingStore | None = None,
    require_deployment_routing: bool = False,
) -> FastAPI:
    """Create isolated app instances; trusted stores are explicitly injected."""
    if project_quota is not None and (
        identity_settings is None or project_store is None or
        gateway_internal_token is None
    ):
        raise ValueError("project quota requires trusted gateway authorization")
    if project_inflight is not None and (
        identity_settings is None or project_store is None or
        gateway_internal_token is None
    ):
        raise ValueError("distributed project concurrency requires guarded authorization")
    if project_output_reservations is not None and (
        project_inflight is None or identity_settings is None
        or project_store is None or gateway_internal_token is None
    ):
        raise ValueError("project output cap reservations require guarded Redis leases")
    if require_deployment_routing and (
        identity_settings is None or project_store is None or
        gateway_internal_token is None or deployment_routing_store is None
    ):
        raise ValueError("strict deployment routing requires verified identity and SQL store")
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
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
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
    application.include_router(team_router(identity_settings, team_store, account_quota))
    application.include_router(durable_deployments_router(
        identity_settings, durable_deployment_store, account_quota))
    application.include_router(gateway_authorization_router(
        identity_settings, project_store, gateway_internal_token, project_quota,
        deployment_routing_store=deployment_routing_store,
        require_deployment_routing=require_deployment_routing,
        project_inflight=project_inflight,
        project_output_reservations=project_output_reservations))

    # Browser authentication is an explicit localhost-only OIDC/Redis opt-in.
    # No login/session routes or static account page are exposed by default.
    if browser_login is not None:
        application.include_router(browser_login_router(browser_login, profile_store,
                                                        account_quota, workspace_store, team_store))

    # Operator-only model lifecycle is opt-in and never substitutes for
    # project-scoped identity or inference gateway authorization.
    configure_local_models(application)
    return application


# Load only operator-pinned local configuration at startup; no SQL connection
# or schema migration runs here. Without a DSN, project reads still deny 503.
_configured_project_store = load_local_project_store()
_configured_identity = load_identity_settings()
_configured_project_quota = load_project_quota()
_configured_project_inflight = load_project_inflight()
_configured_project_output = load_project_output_reservations(_configured_project_inflight)
app = create_app(
    _configured_identity,
    project_store=_configured_project_store,
    profile_store=(SQLProfileStore(_configured_project_store._engine)
                   if _configured_project_store is not None else None),
    account_quota=load_account_quota(),
    browser_login=browser_configured(None, _configured_identity),
    workspace_store=(SQLWorkspaceStore(_configured_project_store._engine)
                     if _configured_project_store is not None else None),
    team_store=(SQLTeamStore(_configured_project_store._engine)
                if _configured_project_store is not None else None),
    durable_deployment_store=(SQLDurableDeploymentStore(_configured_project_store._engine)
                              if _configured_project_store is not None else None),
    gateway_internal_token=load_gateway_internal_token(
        _configured_identity, _configured_project_store),
    project_quota=_configured_project_quota,
    project_inflight=_configured_project_inflight,
    project_output_reservations=_configured_project_output,
    deployment_routing_store=(SQLDeploymentRoutingStore(_configured_project_store._engine)
                              if _configured_project_store is not None else None),
    require_deployment_routing=load_deployment_routing_mode(),
)
