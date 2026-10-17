-- Apply after migrations 0001 and 0002 using a privileged migration role.
-- Never execute DDL at application startup.
BEGIN;
CREATE TABLE organization_invitations (
 id uuid PRIMARY KEY,
 organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
 target_issuer varchar(512) NOT NULL,
 target_subject varchar(255) NOT NULL,
 role varchar(32) NOT NULL CHECK (role IN ('admin','ml_engineer','deployment_operator','developer','auditor','viewer')),
 inviter_issuer varchar(512) NOT NULL,
 inviter_subject varchar(255) NOT NULL,
 created_at timestamptz NOT NULL,
 expires_at timestamptz NOT NULL,
 accepted_at timestamptz,
 CONSTRAINT ck_invitation_expiry CHECK (expires_at > created_at)
);
CREATE INDEX ix_team_invitations_recipient ON organization_invitations(target_issuer,target_subject,accepted_at,expires_at);
CREATE TABLE team_audit_events (
 id uuid PRIMARY KEY,
 organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
 actor_issuer varchar(512) NOT NULL,
 actor_subject varchar(255) NOT NULL,
 action varchar(64) NOT NULL CHECK (action IN ('invitation.created','invitation.accepted','membership.revoked')),
 target_issuer varchar(512) NOT NULL,
 target_subject varchar(255) NOT NULL,
 role varchar(32) NOT NULL,
 invitation_id uuid,
 created_at timestamptz NOT NULL
);
CREATE INDEX ix_team_audit_organization ON team_audit_events(organization_id,created_at);
COMMIT;
