-- Metriqen tenant access schema, PostgreSQL 15+.
-- Review and apply through a privileged migration process, NEVER through
-- an unauthenticated web request or a production app's startup path.
-- This file is intentionally schema-only: no user, tenant, grant or key seeds.
BEGIN;

CREATE TABLE organizations (
    id uuid PRIMARY KEY,
    name varchar(200) NOT NULL
);

CREATE TABLE projects (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name varchar(200) NOT NULL,
    CONSTRAINT uq_projects_id_organization UNIQUE (id, organization_id)
);
CREATE INDEX ix_projects_organization ON projects(organization_id);

CREATE TABLE organization_memberships (
    issuer varchar(512) NOT NULL,
    subject varchar(255) NOT NULL,
    organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    role varchar(32) NOT NULL,
    CONSTRAINT pk_organization_memberships PRIMARY KEY (issuer, subject, organization_id),
    CONSTRAINT ck_membership_role CHECK (
        role IN ('owner', 'admin', 'ml_engineer', 'deployment_operator',
                 'developer', 'auditor', 'viewer')
    )
);

CREATE TABLE project_grants (
    issuer varchar(512) NOT NULL,
    subject varchar(255) NOT NULL,
    organization_id uuid NOT NULL,
    project_id uuid NOT NULL,
    CONSTRAINT pk_project_grants
        PRIMARY KEY (issuer, subject, organization_id, project_id),
    CONSTRAINT fk_grants_membership FOREIGN KEY (issuer, subject, organization_id)
        REFERENCES organization_memberships(issuer, subject, organization_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_grants_project_same_organization FOREIGN KEY (project_id, organization_id)
        REFERENCES projects(id, organization_id) ON DELETE CASCADE
);
CREATE INDEX ix_grants_project_organization
    ON project_grants(project_id, organization_id);

COMMIT;
