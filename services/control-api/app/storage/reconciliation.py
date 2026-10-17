"""Transactional, short-lived worker claims for observed deployment snapshots.

Project owner authorization is handled by OIDC-backed desired-state APIs.
Only a trusted, separately launched local operator process may use this store.
No HTTP route grants a user the power to report successful observations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import Engine, or_, select, update

from app.storage.schema import project_deployments

LEASE_SECONDS = 180
MAX_ATTEMPTS = 3
RETRY_SECONDS = 10
OBSERVED = frozenset({"ready", "disabled", "blocked", "failed"})
REASONS = frozenset({
    "not_allowlisted", "model_missing", "digest_mismatch",
    "loaded_digest_mismatch", "preload_unverified", "runtime_unavailable",
})


@dataclass(frozen=True, slots=True)
class ReconciliationClaim:
    id: UUID
    lease_id: UUID
    generation: int
    model_tag: str
    model_digest: str
    desired_state: str
    attempt: int


class SQLReconciliationStore:
    """Durable, bounded work claims shared by independent PostgreSQL workers."""

    def __init__(self, engine: Engine):
        if not isinstance(engine, Engine):
            raise TypeError("SQLAlchemy Engine required")
        self.engine = engine

    def claim_one(self, *, excluded: frozenset[UUID] = frozenset(),
                  now: datetime | None = None) -> ReconciliationClaim | None:
        now = now or datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            stmt = select(project_deployments).where(
                project_deployments.c.reconcile_attempts < MAX_ATTEMPTS,
                or_(
                    project_deployments.c.reconcile_lease_id.is_(None),
                    project_deployments.c.reconcile_lease_until <= now,
                ),
                or_(
                    project_deployments.c.reconcile_retry_at.is_(None),
                    project_deployments.c.reconcile_retry_at <= now,
                ),
                or_(
                    project_deployments.c.observed_generation <
                    project_deployments.c.desired_generation,
                    project_deployments.c.observed_state.in_(("failed", "blocked")),
                ),
            )
            if excluded:
                stmt = stmt.where(project_deployments.c.id.not_in(excluded))
            row = conn.execute(stmt.order_by(
                project_deployments.c.updated_at, project_deployments.c.id,
            ).limit(1).with_for_update(skip_locked=True)).one_or_none()
            if row is None:
                return None
            lease = uuid4()
            attempt = row.reconcile_attempts + 1
            conn.execute(update(project_deployments).where(
                project_deployments.c.id == row.id,
            ).values(
                reconcile_lease_id=lease,
                reconcile_lease_until=now + timedelta(seconds=LEASE_SECONDS),
                reconcile_attempts=attempt,
                reconcile_retry_at=None,
            ))
            return ReconciliationClaim(
                id=row.id, lease_id=lease, generation=row.desired_generation,
                model_tag=row.model_tag, model_digest=row.model_digest,
                desired_state=row.desired_state, attempt=attempt,
            )

    def finish(self, claim: ReconciliationClaim, state: str,
               reason: str | None = None, *,
               now: datetime | None = None) -> bool:
        """CAS verifies claim AND generation, excluding stale/expired workers."""
        if (state not in OBSERVED or
                (reason is not None and reason not in REASONS) or
                (state in ("ready", "disabled") and reason is not None) or
                (state in ("blocked", "failed") and reason is None)):
            raise ValueError("invalid fixed observed status or reason")
        now = now or datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            row = conn.execute(select(project_deployments).where(
                project_deployments.c.id == claim.id,
            ).with_for_update()).one_or_none()
            if (row is None or row.desired_generation != claim.generation or
                    row.reconcile_lease_id != claim.lease_id or
                    row.reconcile_lease_until is None or
                    (row.reconcile_lease_until.replace(tzinfo=timezone.utc)
                     if row.reconcile_lease_until.tzinfo is None
                     else row.reconcile_lease_until) <= now):
                return False
            retry = None
            if state in ("failed", "blocked") and row.reconcile_attempts < MAX_ATTEMPTS:
                retry = now + timedelta(seconds=RETRY_SECONDS * (2 ** (row.reconcile_attempts - 1)))
            result = conn.execute(update(project_deployments).where(
                project_deployments.c.id == claim.id,
                project_deployments.c.reconcile_lease_id == claim.lease_id,
                project_deployments.c.desired_generation == claim.generation,
            ).values(
                observed_state=state,
                observed_generation=claim.generation,
                observed_at=now,
                observed_code=reason,
                reconcile_lease_id=None,
                reconcile_lease_until=None,
                reconcile_retry_at=retry,
            ))
            return result.rowcount == 1
