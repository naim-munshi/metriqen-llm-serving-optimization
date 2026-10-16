-- Metriqen verified-user profile data, PostgreSQL 15+.
-- Apply explicitly after 0001_tenant_access.sql in an operator-controlled migration.
-- No user identity, organization membership or administrative privilege is seeded.
BEGIN;

CREATE TABLE user_profiles (
    issuer varchar(512) NOT NULL,
    subject varchar(255) NOT NULL,
    display_name varchar(120) NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_user_profiles PRIMARY KEY (issuer, subject),
    CONSTRAINT ck_user_profiles_display_name CHECK (
        char_length(btrim(display_name)) >= 1
    )
);

COMMIT;
