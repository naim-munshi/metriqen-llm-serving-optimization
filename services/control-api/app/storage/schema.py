"""Portable SQLAlchemy Core schema for Metriqen tenant access data.

PostgreSQL is the intended persistent backend. SQLite is used in isolated
CI fixtures to exercise the constraints; it is not a production substitute.
No tables are created or seeded when this module is imported.
"""
from __future__ import annotations

from sqlalchemy import (
    CheckConstraint, Column, ForeignKey, ForeignKeyConstraint,
    Index, MetaData, String, Table, UniqueConstraint, Uuid,
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
