"""Ephemeral Redis 7 BFF login/session outage and recovery integration.

Only runs with METRIQEN_REDIS_CI=1 against the disposable GitHub Actions
127.0.0.1:6379 Redis service. Never discovers production credentials.
"""
from __future__ import annotations

import json
import os
import secrets
import time

from fastapi import HTTPException
import pytest
import redis

from app.browser_auth import BrowserSessionStore, FLOW_SECONDS, SESSION_SECONDS

pytestmark = pytest.mark.skipif(
    os.environ.get("METRIQEN_REDIS_CI") != "1",
    reason="requires explicitly configured ephemeral localhost Redis CI service",
)

PREFIX = "metriqen:browser:v1:"
USER = "browser_session_ci"
PASSWORD = "only-ci-browser-redis-password-0123456789"
URL = f"redis://{USER}:{PASSWORD}@127.0.0.1:6379/0"


@pytest.fixture
def stores():
    admin = redis.Redis(host="127.0.0.1", port=6379, db=0, socket_timeout=2)
    admin.ping()
    admin.execute_command(
        "ACL", "SETUSER", USER, "on", ">" + PASSWORD,
        "~" + PREFIX + "*",
        "+set", "+get", "+getdel", "+del", "+ping",
    )
    first = redis.Redis.from_url(URL, socket_connect_timeout=1, socket_timeout=2)
    second = redis.Redis.from_url(URL, socket_connect_timeout=1, socket_timeout=2)
    try:
        first.ping()
        second.ping()
        yield admin, BrowserSessionStore(first), BrowserSessionStore(second)
    finally:
        for key in admin.scan_iter(match=PREFIX + "*"):
            admin.delete(key)
        first.close()
        second.close()
        admin.execute_command("ACL", "DELUSER", USER)
        admin.close()


def test_one_time_oidc_flow_and_cross_replica_revocation(stores):
    admin, one, two = stores
    ticket = secrets.token_urlsafe(32)
    subject = "synthetic-oidc-subject-never-a-real-user"
    record = {"issuer": "https://ci-idp.example.test", "subject": subject,
              "csrf": secrets.token_urlsafe(32)}
    one.create("flow", ticket, record, FLOW_SECONDS)
    key = one.key("flow", ticket)
    assert ticket.encode() not in key.encode()
    assert subject not in key
    assert 0 < admin.ttl(key) <= FLOW_SECONDS
    assert two.consume_flow(ticket) == record
    assert one.consume_flow(ticket) is None  # GETDEL is atomic, no code replay

    session = secrets.token_urlsafe(32)
    one.create("session", session, record, SESSION_SECONDS)
    assert two.get_session(session) == record
    assert 0 < admin.ttl(two.key("session", session)) <= SESSION_SECONDS
    two.delete_session(session)
    assert one.get_session(session) is None
    assert subject not in str(list(admin.scan_iter(match=PREFIX + "*")))


def test_redis_connection_loss_fails_closed_then_recovers_without_resurrecting(stores):
    admin, one, two = stores
    ticket = secrets.token_urlsafe(32)
    record = {"issuer": "https://ci-idp.example.test",
              "subject": "synthetic-subject", "csrf": secrets.token_urlsafe(32)}
    one.create("session", ticket, record, SESSION_SECONDS)
    assert two.get_session(ticket) == record

    live_connection = two.connection
    # Inject a transport outage via a distinct, absent *loopback* endpoint.
    # The real disposable Redis server remains running, undisturbed.
    unreachable = redis.Redis(host="127.0.0.1", port=6398, db=0,
                              socket_connect_timeout=0.2, socket_timeout=0.2,
                              retry_on_timeout=False)
    two.connection = unreachable
    try:
        for action in (
            lambda: two.get_session(ticket),
            lambda: two.delete_session(ticket),
            lambda: two.consume_flow(ticket),
            lambda: two.create("session", secrets.token_urlsafe(32), record, SESSION_SECONDS),
        ):
            with pytest.raises(HTTPException) as rejected:
                action()
            assert rejected.value.status_code == 503
            assert rejected.value.detail == "browser session service unavailable"
            assert "127.0.0.1" not in str(rejected.value.detail)
    finally:
        two.connection = live_connection
        unreachable.close()

    # Network recovery must not grant a new session or bypass prior revocation.
    assert one.get_session(ticket) == record
    two.delete_session(ticket)
    assert one.get_session(ticket) is None
    assert two.get_session(ticket) is None
    # Expiry is authoritative in Redis, not cached by a replica.
    expiring = secrets.token_urlsafe(32)
    one.create("session", expiring, record, SESSION_SECONDS)
    admin.pexpire(one.key("session", expiring), 1)
    time.sleep(0.04)
    assert two.get_session(expiring) is None


def test_browser_session_acl_cannot_read_other_tenant_namespaces(stores):
    _, one, _ = stores
    with pytest.raises(redis.exceptions.NoPermissionError):
        one.connection.get("metriqen:project-inference:v1:not-browser")
    with pytest.raises(redis.exceptions.NoPermissionError):
        one.connection.get("unrelated:tenant:session")
