"""Synthetic local fixtures for the team storage seam."""
from uuid import uuid4
from sqlalchemy import create_engine, insert
from sqlalchemy.pool import StaticPool
from app.security.oidc import VerifiedIdentity
from app.storage.schema import metadata, organizations, organization_memberships
from app.storage.team import SQLTeamStore

ISSUER = "https://idp.example.test"

def identity(subject, issuer=ISSUER):
    return VerifiedIdentity(issuer=issuer, subject=subject, audience="control-api")

def seeded():
    engine = create_engine("sqlite+pysqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    metadata.create_all(engine)
    org = uuid4()
    with engine.begin() as conn:
        conn.execute(insert(organizations).values(id=org, name="Team"))
        conn.execute(insert(organization_memberships).values(
            issuer=ISSUER, subject="owner", organization_id=org, role="owner"))
    return engine, SQLTeamStore(engine), org
