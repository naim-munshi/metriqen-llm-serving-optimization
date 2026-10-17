"""Unit contracts: output-cap token reservations are conservative, atomic and opt-in."""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from app.security.project_inflight import RedisProjectInflight
from app.security.project_output_reservations import (
    KEY_PREFIX, MAX_REQUEST_TOKENS, RedisProjectOutputReservations,
    load_project_output_reservations,
)
from app.security.policy import Role
from test_project_inflight import FakeRedis, SERVICE, SECRET, BASE
from test_project_access_http import sandbox, signed_material, token, PROJECT_A, ORG_A, ISSUER


class FakeBudgetRedis(FakeRedis):
    def __init__(self):
        super().__init__()
        self.spend = {}
        self.receipts = set()
        self.ttls = {}
        self.attempts = []

    def eval(self, script, numkeys, *args):
        if "ZSCORE" not in script:
            return super().eval(script, numkeys, *args)
        with self.lock:
            if self.fail:
                raise RuntimeError("PRIVATE_REDIS_SECRET_MUST_NOT_LEAK")
            if self.override is not self.INVALID_SENTINEL:
                return self.override
            assert numkeys == 3
            active_key, budget_key, receipt_key, permit, amount, limit, ttl = args
            self.attempts.append((active_key, budget_key, receipt_key, amount))
            members = self.members.get(active_key, {})
            if permit not in members or members[permit] <= self.now:
                return -1
            if receipt_key in self.receipts:
                return -2
            if self.ttls.get(budget_key, float("inf")) <= self.now:
                self.spend.pop(budget_key, None)
                self.ttls.pop(budget_key, None)
            current = self.spend.get(budget_key, 0)
            if current + amount > limit:
                return 0
            self.spend[budget_key] = current + amount
            self.ttls.setdefault(budget_key, self.now + 60000)
            self.receipts.add(receipt_key)
            return 1


def test_two_replicas_atomic_reserve_and_replay_safe_output_budget():
    fake = FakeBudgetRedis()
    leases = RedisProjectInflight(fake, SECRET, limit=16)
    budgets = [RedisProjectOutputReservations(leases, limit=1024),
               RedisProjectOutputReservations(leases, limit=1024)]
    org, project = uuid4(), uuid4()
    permits = [leases.acquire(org, project) for _ in range(16)]

    def try_reserve(i):
        try:
            budgets[i % 2].reserve(**permits[i], requested_max_tokens=256)
            return 204
        except HTTPException as e:
            return e.status_code

    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(try_reserve, range(16)))
    assert outcomes.count(204) == 4
    assert outcomes.count(429) == 12
    assert sum(fake.spend.values()) == 1024
    assert len(fake.receipts) == 4
    assert len({key for _, key, _, _ in fake.attempts}) == 1
    for args in fake.attempts:
        assert str(org) not in str(args)
        assert str(project) not in str(args)
        assert args[1].startswith(KEY_PREFIX + "budget:")
        assert args[2].startswith(KEY_PREFIX + "receipt:")
    winner = next(i for i, result in enumerate(outcomes) if result == 204)
    with pytest.raises(HTTPException) as err:
        budgets[0].reserve(**permits[winner], requested_max_tokens=1)
    assert err.value.status_code == 409
    assert sum(fake.spend.values()) == 1024

    for lease in permits:
        leases.release(**lease)
    # Releasing active capacity does not refund a previously reserved cap.
    with pytest.raises(HTTPException) as err:
        budgets[0].reserve(**permits[0], requested_max_tokens=1)
    assert err.value.status_code == 409
    next_lease = leases.acquire(org, project)
    with pytest.raises(HTTPException) as err:
        budgets[1].reserve(**next_lease, requested_max_tokens=1)
    assert err.value.status_code == 429
    assert sum(fake.spend.values()) == 1024

    # Budget resets at 60s; an unspent, still-live permit may then
    # atomically reserve again. Previously spent permit never replays.
    fake.now = 60001
    budgets[0].reserve(**next_lease, requested_max_tokens=256)
    assert sum(fake.spend.values()) == 256
    assert len(fake.receipts) == 5


def test_missing_expired_released_or_wrong_scope_permit_never_spends():
    fake = FakeBudgetRedis()
    lease_store = RedisProjectInflight(fake, SECRET)
    budget = RedisProjectOutputReservations(lease_store, limit=2048)
    org, project = uuid4(), uuid4()
    a = lease_store.acquire(org, project)
    b = lease_store.acquire(org, project)
    for candidate in [
        {"scope": "b" * 64, "permit": a["permit"]},
        {"scope": a["scope"], "permit": b["permit"][:-1] + "f"},
    ]:
        with pytest.raises(HTTPException) as err:
            budget.reserve(**candidate, requested_max_tokens=256)
        assert err.value.status_code == 409
    assert fake.spend == {}
    lease_store.release(**a)
    with pytest.raises(HTTPException) as err:
        budget.reserve(**a, requested_max_tokens=256)
    assert err.value.status_code == 409
    fake.now = 240001
    with pytest.raises(HTTPException) as err:
        budget.reserve(**b, requested_max_tokens=256)
    assert err.value.status_code == 409
    assert fake.spend == {}


def test_invalid_max_tokens_redis_outage_and_bad_results_fail_closed():
    fake = FakeBudgetRedis()
    lease_store = RedisProjectInflight(fake, SECRET)
    budget = RedisProjectOutputReservations(lease_store, limit=4096)
    lease = lease_store.acquire(uuid4(), uuid4())
    for cap in [0, -1, 2049, True, 1.0, "256", None]:
        with pytest.raises(HTTPException) as err:
            budget.reserve(**lease, requested_max_tokens=cap)
        assert err.value.status_code == 422
    assert fake.spend == {}
    fake.fail = True
    with pytest.raises(HTTPException) as err:
        budget.reserve(**lease, requested_max_tokens=256)
    assert err.value.status_code == 503
    assert "PRIVATE" not in str(err.value.detail)
    fake.fail = False
    for result in (None, True, "1", b"1", [], 2, -3):
        fake.override = result
        with pytest.raises(HTTPException) as err:
            budget.reserve(**lease, requested_max_tokens=256)
        assert err.value.status_code == 503
    assert fake.spend == {}


def test_trusted_gateway_reservation_endpoint_requires_service_secret_and_live_lease(sandbox):
    _, store, signer, _, settings = sandbox
    fake = FakeBudgetRedis()
    inflight = RedisProjectInflight(fake, SECRET, limit=3)
    budget = RedisProjectOutputReservations(inflight, limit=512)
    control = TestClient(create_app(
        settings, project_store=store, gateway_internal_token=SERVICE,
        project_inflight=inflight, project_output_reservations=budget,
    ), client=("127.0.0.1", 50201))
    headers = {
        "Authorization": "Bearer " + token(signer, role="owner"),
        "X-Metriqen-Gateway-Secret": SERVICE,
    }
    def authorize():
        return control.post(BASE + "/authorize",
            json={"project_id": str(PROJECT_A)}, headers=headers)
    assert authorize().status_code == 404
    assert fake.spend == {}
    store.grant(Role.OWNER)
    first = authorize()
    assert first.status_code == 200
    assert first.json()["output_reservation_required"] is True
    one = first.json()["lease"]
    def reserve(permit, amount=256, secret=SERVICE):
        return control.post(BASE + "/reserve-output",
            json={**permit, "max_tokens": amount},
            headers={"X-Metriqen-Gateway-Secret": secret})
    assert reserve(one, secret="forged").status_code == 403
    assert reserve(one, amount="256").status_code == 422
    remote = TestClient(control.app, client=("192.0.2.10", 50202))
    assert remote.post(BASE + "/reserve-output",
        json={**one, "max_tokens": 256},
        headers={"X-Metriqen-Gateway-Secret": SERVICE}).status_code == 403
    assert reserve(one).status_code == 204
    assert reserve(one).status_code == 409
    control.post(BASE + "/release", json=one,
                 headers={"X-Metriqen-Gateway-Secret": SERVICE})
    two = authorize()
    assert two.status_code == 200
    assert reserve(two.json()["lease"]).status_code == 204
    assert sum(fake.spend.values()) == 512
    three = authorize()
    assert three.status_code == 200
    assert reserve(three.json()["lease"], amount=1).status_code == 429
    assert sum(fake.spend.values()) == 512
    # Revocation prevents NEW auth even though an already issued permit
    # cannot be forcibly canceled by this opt-in local budget protocol.
    del store.memberships[(ISSUER, "user-a", ORG_A)]
    assert authorize().status_code == 404


@pytest.mark.parametrize("limit", ["", "0", "1", "0256", "255", "1000001", "1.0",
                                   "-256", "true", " 512 ", "abc", "9" * 100])
def test_invalid_budget_configuration_rejected(limit):
    lease = RedisProjectInflight(FakeBudgetRedis(), SECRET)
    with pytest.raises(ValueError):
        load_project_output_reservations(lease, {
            "METRIQEN_PROJECT_OUTPUT_RESERVATION_PER_MINUTE": limit,
        })


def test_opt_in_needs_redis_leases_identity_and_sql_config(sandbox):
    _, store, _, _, settings = sandbox
    lease = RedisProjectInflight(FakeBudgetRedis(), SECRET)
    assert load_project_output_reservations(lease, {}) is None
    with pytest.raises(ValueError):
        load_project_output_reservations(None, {
            "METRIQEN_PROJECT_OUTPUT_RESERVATION_PER_MINUTE": "1024",
        })
    budget = load_project_output_reservations(lease, {
        "METRIQEN_PROJECT_OUTPUT_RESERVATION_PER_MINUTE": "1024",
    })
    assert budget.limit == 1024
    with pytest.raises(ValueError):
        create_app(settings, project_output_reservations=budget)
    with pytest.raises(ValueError):
        create_app(settings, project_store=store, gateway_internal_token=SERVICE,
                   project_output_reservations=budget)
    with pytest.raises(ValueError):
        create_app(settings, project_store=store, project_inflight=lease,
                   project_output_reservations=budget)
    # Without budget, existing guarded projects remain backward compatible.
    client = TestClient(create_app(
        settings, project_store=store, gateway_internal_token=SERVICE,
        project_inflight=lease,
    ), client=("127.0.0.1", 50200))
    assert client.post(BASE + "/reserve-output", json={
        "scope": "a" * 64, "permit": "b" * 32, "max_tokens": 256,
    }, headers={"X-Metriqen-Gateway-Secret": SERVICE}).status_code == 503
