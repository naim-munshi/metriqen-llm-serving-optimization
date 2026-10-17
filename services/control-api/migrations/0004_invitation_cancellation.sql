-- Explicit operator migration AFTER 0001, 0002 and 0003.
-- This is not run on application startup or an HTTP request.
BEGIN;
ALTER TABLE organization_invitations
  ADD COLUMN cancelled_at timestamptz;
ALTER TABLE team_audit_events
  DROP CONSTRAINT team_audit_events_action_check;
ALTER TABLE team_audit_events
  ADD CONSTRAINT ck_team_audit_action_v2 CHECK (
    action IN ('invitation.created','invitation.accepted',
               'invitation.cancelled','membership.revoked')
  );
COMMIT;
