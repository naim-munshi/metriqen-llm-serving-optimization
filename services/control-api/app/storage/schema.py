"""Portable SQLAlchemy Core schema for Metriqen tenant access data.

PostgreSQL is the intended persistent backend. SQLite is used in isolated
CI fixtures to exercise the constraints; it is not a production substitute.
No tables are created or seeded when this module is imported.
"""
from __future__ import annotations

from sqlalchemy import (
    CheckConstraint, Column, DateTime, ForeignKey, ForeignKeyConstraint,
    Index, Integer, MetaData, String, Table, Text, UniqueConstraint, Uuid,
)

metadata = MetaData()

organizations = Table(
    "organizations", metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("name", String(200), nullable=False),
)

projects = Table(
    "projects", metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("organization_id", Uuid(as_uuid=True),
           ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
    Column("name", String(200), nullable=False),
    UniqueConstraint("id", "organization_id", name="uq_projects_id_organization"),
)
Index("ix_projects_organization", projects.c.organization_id)

organization_memberships = Table(
    "organization_memberships", metadata,
    Column("issuer", String(512), primary_key=True),
    Column("subject", String(255), primary_key=True),
    Column("organization_id", Uuid(as_uuid=True),
           ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True),
    Column("role", String(32), nullable=False),
    CheckConstraint(
        "role IN ('owner','admin','ml_engineer','deployment_operator',"
        "'developer','auditor','viewer')",
        name="ck_membership_role",
    ),
)

project_grants = Table(
    "project_grants", metadata,
    Column("issuer", String(512), primary_key=True),
    Column("subject", String(255), primary_key=True),
    Column("organization_id", Uuid(as_uuid=True), primary_key=True),
    Column("project_id", Uuid(as_uuid=True), primary_key=True),
    ForeignKeyConstraint(
        ["issuer", "subject", "organization_id"],
        ["organization_memberships.issuer", "organization_memberships.subject",
         "organization_memberships.organization_id"],
        name="fk_grants_membership", ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["project_id", "organization_id"],
        ["projects.id", "projects.organization_id"],
        name="fk_grants_project_same_organization", ondelete="CASCADE",
    ),
)
Index("ix_grants_project_organization",
      project_grants.c.project_id, project_grants.c.organization_id)


# An OIDC issuer + its subject identifies one user; subject alone is never
# unique across identity providers. This table grants NO tenant membership.
user_profiles = Table(
    "user_profiles", metadata,
    Column("issuer", String(512), primary_key=True),
    Column("subject", String(255), primary_key=True),
    Column("display_name", String(120), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("length(trim(display_name)) >= 1",
                    name="ck_user_profiles_display_name"),
)


organization_invitations = Table(
    "organization_invitations", metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("organization_id", Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
    Column("target_issuer", String(512), nullable=False),
    Column("target_subject", String(255), nullable=False),
    Column("role", String(32), nullable=False),
    Column("inviter_issuer", String(512), nullable=False),
    Column("inviter_subject", String(255), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("accepted_at", DateTime(timezone=True)),
    Column("cancelled_at", DateTime(timezone=True)),
    CheckConstraint("role IN ('admin','ml_engineer','deployment_operator','developer','auditor','viewer')", name="ck_invitation_role"),
    CheckConstraint("expires_at > created_at", name="ck_invitation_expiry"),
)
Index("ix_team_invitations_recipient", organization_invitations.c.target_issuer, organization_invitations.c.target_subject)
team_audit_events = Table(
    "team_audit_events", metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("organization_id", Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
    Column("actor_issuer", String(512), nullable=False),
    Column("actor_subject", String(255), nullable=False),
    Column("action", String(64), nullable=False),
    Column("target_issuer", String(512), nullable=False),
    Column("target_subject", String(255), nullable=False),
    Column("role", String(32), nullable=False),
    Column("invitation_id", Uuid(as_uuid=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("action IN ('invitation.created','invitation.accepted','invitation.cancelled','membership.revoked')", name="ck_team_audit_action"),
)
Index("ix_team_audit_organization", team_audit_events.c.organization_id, team_audit_events.c.created_at)


# Project-scoped durable desired-state records. These are not live Ollama
# workers: observed state remains 'unreconciled' until a separate trusted
# reconciliation milestone is implemented.
project_deployments = Table(
    "project_deployments", metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("organization_id", Uuid(as_uuid=True), nullable=False),
    Column("project_id", Uuid(as_uuid=True), nullable=False),
    Column("name", String(32), nullable=False),
    Column("model_tag", String(128), nullable=False),
    Column("model_digest", String(64), nullable=False),
    Column("previous_tag", String(128)),
    Column("previous_digest", String(64)),
    Column("desired_state", String(16), nullable=False),
    Column("desired_generation", Integer, nullable=False),
    Column("observed_state", String(20), nullable=False),
    Column("observed_generation", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["project_id", "organization_id"],
        ["projects.id", "projects.organization_id"],
        ondelete="CASCADE", name="fk_deployment_project_org",
    ),
    UniqueConstraint("project_id", "name", name="uq_project_deployment_name"),
    CheckConstraint("desired_state IN ('running','stopped')",
                    name="ck_project_deployment_desired"),
    CheckConstraint("observed_state IN ('unreconciled')",
                    name="ck_project_deployment_observed"),
    CheckConstraint("desired_generation >= 1 AND observed_generation = 0",
                    name="ck_project_deployment_generation"),
    CheckConstraint("(previous_tag IS NULL) = (previous_digest IS NULL)",
                    name="ck_project_deployment_previous_pair"),
)
Index("ix_project_deployments_scope", project_deployments.c.organization_id,
      project_deployments.c.project_id)

project_deployment_commands = Table(
    "project_deployment_commands", metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("deployment_id", Uuid(as_uuid=True),
           ForeignKey("project_deployments.id", ondelete="CASCADE"),
           nullable=False),
    Column("project_id", Uuid(as_uuid=True), nullable=False),
    Column("actor_issuer", String(512), nullable=False),
    Column("actor_subject", String(255), nullable=False),
    Column("idempotency_key", String(80), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("action", String(16), nullable=False),
    Column("response_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("project_id", "actor_issuer", "actor_subject",
                     "idempotency_key", name="uq_deployment_actor_idempotency"),
    CheckConstraint("action IN ('create','promote','rollback','stop','start')",
                    name="ck_deployment_action"),
)
Index("ix_project_deployment_commands", project_deployment_commands.c.deployment_id,
      project_deployment_commands.c.created_at)
