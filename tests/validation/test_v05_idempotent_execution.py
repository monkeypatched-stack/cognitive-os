"""Systems Validation Suite — Sections 7 and 28: side-effect duplication /
idempotent execution, proven with a literal non-idempotent
counter.increment() capability under "execute -> succeed -> lose the
response -> retry".

The deeper, state-machine-level retry-safety guarantee (kernel/
execution_attempt.py's NOT_STARTED->...->RECONCILIATION_REQUIRED
machinery) already has extensive, real, passing coverage --
tests/security/test_commitment_vs_execution.py::TestNoUnsafeRetry::
test_non_idempotent_unknown_blocks_automatic_second_attempt and
TestRetryNeverCreatesNewCommitment::test_cannot_retry_a_succeeded_
operation_into_a_new_effect prove the SAME invariant this section asks
for, at the lower execution-attempt layer. This file proves it again at
the API layer (kernel/api/idempotency.py's @idempotent decorator +
IdempotencyStore, the ACTUAL mechanism a real HTTP retry from a client
that lost its response goes through) with a literal counter, per this
section's own literal request -- not a duplicate of the state-machine
tests, a different layer of the same stack.
"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

from src.monkey_brain.api.idempotency import IdempotencyStore, idempotent


@pytest.fixture(autouse=True)
def _memory_backend(monkeypatch):
    monkeypatch.setenv("IDEMPOTENCY_STORE_BACKEND", "memory")
    IdempotencyStore._instance = None
    yield
    IdempotencyStore._instance = None


def _fake_auth() -> str:
    return "test-user"


class _IncrementBody(BaseModel):
    amount: int = 1


class DurableCounter:
    """Stands in for a real irreversible side effect (a payment ledger
    increment, a physical actuator step, a message send) -- each
    successful call to .increment() durably increments exactly once,
    with no idempotency protection of its OWN (the point is to prove
    the boundary protects it, not that it protects itself)."""

    def __init__(self) -> None:
        self.value = 0
        self.call_log: list[int] = []

    def increment(self, amount: int) -> int:
        self.value += amount
        self.call_log.append(amount)
        return self.value


class TestCounterIncrementsExactlyOnceAcrossALostResponseRetry:
    def test_retry_after_a_lost_response_does_not_double_increment(self):
        counter = DurableCounter()
        app = FastAPI()

        @app.post("/counter/increment")
        @idempotent("counter.increment")
        async def increment(body: _IncrementBody, request: Request, user_id: str = Depends(_fake_auth)) -> dict:
            new_value = counter.increment(body.amount)
            return {"value": new_value}

        client = TestClient(app)

        # Attempt 1: succeeds, increments the durable counter once. The
        # client's HTTP response is what "gets lost" (never actually
        # observed -- this test doesn't need to simulate that beyond not
        # looking at r1 before retrying, since the SAME Idempotency-Key
        # is what a real client retry would resend).
        r1 = client.post("/counter/increment", json={"amount": 5}, headers={"Idempotency-Key": "retry-key-1"})
        assert r1.status_code == 200

        # Attempt 2: the client, having not seen attempt 1's response,
        # retries with the SAME Idempotency-Key and body.
        r2 = client.post("/counter/increment", json={"amount": 5}, headers={"Idempotency-Key": "retry-key-1"})
        assert r2.status_code == 200

        assert r1.json() == r2.json(), "the retry must be served the FIRST attempt's cached result"
        assert counter.value == 5, f"counter.increment() must have run exactly once, not {len(counter.call_log)} times"
        assert counter.call_log == [5]

    def test_ten_concurrent_retries_still_increment_exactly_once(self):
        """Section 5/28's concurrency angle applied to this same
        capability: N callers racing to submit the SAME logical
        operation (identical Idempotency-Key) must still produce exactly
        one real increment, not N."""
        import threading

        counter = DurableCounter()
        app = FastAPI()

        @app.post("/counter/increment")
        @idempotent("counter.increment")
        async def increment(body: _IncrementBody, request: Request, user_id: str = Depends(_fake_auth)) -> dict:
            new_value = counter.increment(body.amount)
            return {"value": new_value}

        client = TestClient(app)
        results = []
        lock = threading.Lock()

        def _post():
            r = client.post("/counter/increment", json={"amount": 5}, headers={"Idempotency-Key": "retry-key-concurrent"})
            with lock:
                results.append(r.status_code)

        threads = [threading.Thread(target=_post) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert counter.value == 5, f"expected exactly one real increment across 10 concurrent identical retries, got value={counter.value}"
        assert len(counter.call_log) == 1

    def test_a_genuinely_different_operation_is_not_suppressed(self):
        """The boundary must not become an accidental global rate-limit
        -- a DIFFERENT Idempotency-Key (a genuinely new operation) must
        still execute and increment for real."""
        counter = DurableCounter()
        app = FastAPI()

        @app.post("/counter/increment")
        @idempotent("counter.increment")
        async def increment(body: _IncrementBody, request: Request, user_id: str = Depends(_fake_auth)) -> dict:
            new_value = counter.increment(body.amount)
            return {"value": new_value}

        client = TestClient(app)
        r1 = client.post("/counter/increment", json={"amount": 5}, headers={"Idempotency-Key": "op-a"})
        r2 = client.post("/counter/increment", json={"amount": 5}, headers={"Idempotency-Key": "op-b"})

        assert r1.json() != r2.json()
        assert counter.value == 10
        assert counter.call_log == [5, 5]


class TestNonIdempotentCapabilityInventory:
    """Section 28: enumerate real capabilities with a potentially
    irreversible side effect and classify each. Built from a direct
    read of kernel/domains/grocery.py and kernel/domains/commerce.py's
    real capability classes for this validation pass -- not
    hypothetical categories."""

    CLASSIFICATION = {
        # capability_name: (classification, evidence)
        "OrderCreation": ("explicitly_deduplicated", "idempotency_key + IdempotencyStore reserve/complete, "
                           "plus execution_attempt.py's own attempt-state machine for the network-ambiguity case"),
        "PaymentAuthorization/PaymentCharge": ("explicitly_deduplicated", "same @idempotent + execution_attempt "
                                                "coverage as OrderCreation -- see tests/scenarios/test_mb3012_payment_authorization.py"),
        "ProductSelection (reservation)": ("explicitly_deduplicated", "try_reserve's compare-and-swap on the "
                                            "KnowledgeGraph entity -- tests/scenarios/test_transition_gate.py"),
        "DelegateTaskCapability (message send)": ("not_safe_to_retry_without_key", "a retried delegated-task "
                                                    "dispatch with no idempotency key re-executes the underlying "
                                                    "tasks; safety depends entirely on those tasks' OWN idempotency "
                                                    "(OrderCreation etc.), not on delegation itself"),
        "AskActorCapability (message send)": ("not_naturally_idempotent", "each call records a new episodic "
                                                "memory entry on both sides (test_D in test_actor_isolation_audit.py) "
                                                "-- a retried ask is a SECOND real memory, not deduplicated"),
        "BroadcastToAffiliationCapability": ("not_naturally_idempotent", "records a new memory_manager entry per "
                                               "delivery, same as AskActor -- see test_v09_messaging.py's own finding "
                                               "that the recipient-side handler has no idempotency key at all"),
        "physical ROS movement (RosExecutionAdapter.invoke)": ("unknown", "run_ros_action_if_governed has no "
                                                                  "idempotency-key parameter and no replay/sequence "
                                                                  "protection -- see test_v13_ros_governance.py; a "
                                                                  "REAL rclpy adapter's safety under a duplicate "
                                                                  "command is genuinely untested (no ROS 2 install "
                                                                  "in this environment)"),
    }

    def test_every_capability_in_the_inventory_has_a_named_classification(self):
        valid = {"explicitly_deduplicated", "naturally_idempotent", "not_naturally_idempotent",
                 "not_safe_to_retry_without_key", "unknown"}
        for name, (classification, _evidence) in self.CLASSIFICATION.items():
            assert classification in valid, f"{name}: {classification!r} is not a recognized classification"

    def test_at_least_one_capability_is_honestly_marked_unknown_or_unsafe(self):
        """Guards against the inventory silently becoming all-green over
        time without a real re-audit -- Section 28 explicitly requires
        reporting every `unknown`, not converging them to a reassuring
        default."""
        flagged = {n for n, (c, _e) in self.CLASSIFICATION.items()
                   if c in ("unknown", "not_safe_to_retry_without_key", "not_naturally_idempotent")}
        assert flagged, "expected at least one capability to be a genuine, reported gap"
