-- PostgreSQL 16 durable project deployment desired-state and command journal.
-- Operator-only migration, after 0001..0004; NEVER executed at API startup.
-- BACKUP existing data; migration is additive and introduces no public service.
BEGIN;
CREATE TABLE project_deployments (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    project_id uuid NOT NULL,
    name varchar(32) NOT NULL,
    model_tag varchar(128) NOT NULL,
    model_digest varchar(64) NOT NULL,
    previous_tag varchar(128),
    previous_digest varchar(64),
    desired_state varchar(16) NOT NULL,
    desired_generation integer NOT NULL,
    observed_state varchar(20) NOT NULL,
    observed_generation integer NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT fk_deployment_project_org
        FOREIGN KEY (project_id, organization_id)
        REFERENCES projects(id, organization_id) ON DELETE CASCADE,
    CONSTRAINT uq_project_deployment_name UNIQUE (project_id, name),
    CONSTRAINT ck_project_deployment_desired
        CHECK (desired_state IN ('running','stopped')),
    CONSTRAINT ck_project_deployment_observed
        CHECK (observed_state IN ('unreconciled')),
    CONSTRAINT ck_project_deployment_generation
        CHECK (desired_generation >= 1 AND observed_generation = 0),
    CONSTRAINT ck_project_deployment_previous_pair
        CHECK ((previous_tag IS NULL) = (previous_digest IS NULL))
);
CREATE INDEX ix_project_deployments_scope
    ON project_deployments(organization_id, project_id);

CREATE TABLE project_deployment_commands (
    id uuid PRIMARY KEY,
    deployment_id uuid NOT NULL REFERENCES project_deployments(id) ON DELETE CASCADE,
    project_id uuid NOT NULL,
    actor_issuer varchar(512) NOT NULL,
    actor_subject varchar(255) NOT NULL,
    idempotency_key varchar(80) NOT NULL,
    request_hash varchar(64) NOT NULL,
    action varchar(16) NOT NULL,
    response_json text NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT uq_deployment_actor_idempotency
        UNIQUE (project_id, actor_issuer, actor_subject, idempotency_key),
    CONSTRAINT ck_deployment_action
        CHECK (action IN ('create','promote','rollback','stop','start'))
);
CREATE INDEX ix_project_deployment_commands
    ON project_deployment_commands(deployment_id, created_at);
COMMIT;
