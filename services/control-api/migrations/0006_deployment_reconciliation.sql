-- Explicit operator migration 0006, AFTER 0001..0005. No startup DDL.
-- Back up the local PostgreSQL database and verify least-privileged worker
-- SELECT/UPDATE permissions separately. This does NOT create a daemon.
BEGIN;
ALTER TABLE project_deployments
    DROP CONSTRAINT ck_project_deployment_observed,
    DROP CONSTRAINT ck_project_deployment_generation;
ALTER TABLE project_deployments
    ADD COLUMN observed_at timestamptz,
    ADD COLUMN observed_code varchar(32),
    ADD COLUMN reconcile_attempts integer NOT NULL DEFAULT 0,
    ADD COLUMN reconcile_lease_id uuid,
    ADD COLUMN reconcile_lease_until timestamptz,
    ADD COLUMN reconcile_retry_at timestamptz,
    ADD CONSTRAINT ck_project_deployment_observed CHECK (
        observed_state IN ('unreconciled','ready','disabled','blocked','failed')
    ),
    ADD CONSTRAINT ck_project_deployment_generation CHECK (
        desired_generation >= 1 AND observed_generation >= 0
        AND observed_generation <= desired_generation
    ),
    ADD CONSTRAINT ck_project_reconcile_attempts CHECK (
        reconcile_attempts BETWEEN 0 AND 3
    ),
    ADD CONSTRAINT ck_project_reconcile_lease_pair CHECK (
        (reconcile_lease_id IS NULL) = (reconcile_lease_until IS NULL)
    );
-- Existing rows retain 'unreconciled', observed_generation 0 and no lease.
COMMIT;
