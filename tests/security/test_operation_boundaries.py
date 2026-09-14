"""Security-critical operation boundary tests.

Insecure-dev is explicitly unset. Classification defaults to critical.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.operation_classification import (
    OperationClass,
    classify_operation,
)
from src.monkey_brain.kernel.pipeline.action_executor import ActionExecutor
from src.monkey_brain.kernel.pipeline.execution import Action
from src.monkey_brain.kernel.security_boundary import (
    SecurityBoundaryDenied,
    commitment_active,
    ensure_governed,
    run_governed_mutation,
)
from src.monkey_brain.kernel.trusted_auth import (
    TrustedAuthEvidence,
    bind_trusted_auth,
    unauthenticated_evidence,
)


@pytest.fixture(autouse=True)
def _secure(monkeypatch):
    monkeypatch.delenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", raising=False)
    monkeypatch.delenv("COGNITIVEOS_PRODUCTION_MODE", raising=False)
    bind_trusted_auth(unauthenticated_evidence())


class TestClassification:
    def test_read_is_non_critical(self):
        assert classify_operation("get_customer") is OperationClass.READ_ONLY
        assert classify_operation("list_orders") is OperationClass.READ_ONLY
        assert classify_operation("query") is OperationClass.READ_ONLY

    def test_proposal_is_non_critical(self):
        assert classify_operation("plan") is OperationClass.PROPOSAL_ONLY
        assert classify_operation("predict") is OperationClass.PROPOSAL_ONLY
        assert classify_operation("simulate") is OperationClass.PROPOSAL_ONLY

    def test_mutation_is_critical(self):
        assert classify_operation("world.entity.create") is OperationClass.SECURITY_CRITICAL
        assert classify_operation("orders.create") is OperationClass.SECURITY_CRITICAL

    def test_execute_and_actor_are_critical(self):
        assert classify_operation("runtime.execute") is OperationClass.SECURITY_CRITICAL
        assert classify_operation("actor.tick") is OperationClass.SECURITY_CRITICAL
        assert classify_operation("capability.Payment") is OperationClass.SECURITY_CRITICAL

    def test_external_write_and_payment_are_critical(self):
        assert classify_operation("payments.webhook") is OperationClass.SECURITY_CRITICAL
        assert classify_operation("orders.refund") is OperationClass.SECURITY_CRITICAL

    def test_authority_and_policy_changes_are_critical(self):
        assert classify_operation("grant_role") is OperationClass.SECURITY_CRITICAL
        assert classify_operation("revoke_session") is OperationClass.SECURITY_CRITICAL
        assert classify_operation("policy.update") is OperationClass.SECURITY_CRITICAL

    def test_audit_modification_is_critical(self):
        assert classify_operation("audit.delete") is OperationClass.SECURITY_CRITICAL

    def test_unknown_defaults_critical(self):
        assert classify_operation("do_the_thing") is OperationClass.SECURITY_CRITICAL
        assert classify_operation("") is OperationClass.SECURITY_CRITICAL

    def test_agent_cannot_declare_payment_read_only(self):
        assert (
            classify_operation(
                "send_payment",
                declared=OperationClass.READ_ONLY,
            )
            is OperationClass.SECURITY_CRITICAL
        )

    def test_simulate_capture_is_critical_not_proposal(self):
        assert classify_operation("simulate_capture") is OperationClass.SECURITY_CRITICAL


class TestBypassResistance:
    @pytest.mark.asyncio
    async def test_ungoverned_capability_simulation_denied(self):
        executor = ActionExecutor(capability_bus=None)
        result = await executor._execute_actions(
            (Action(action_id="a1", capability="PaymentCapability"),),
            context={},
        )
        assert result.actions[0].success is False
        assert (
            "forbidden" in (result.actions[0].error or "").lower()
            or "ungoverned" in (result.actions[0].error or "").lower()
        )

    @pytest.mark.asyncio
    async def test_direct_executor_without_auth_denies(self):
        executor = ActionExecutor(capability_bus=None)
        bind_trusted_auth(unauthenticated_evidence())
        with pytest.raises(SecurityBoundaryDenied):
            await executor.execute(
                (Action(action_id="a1", capability="create_order"),),
                context={"authorized": True, "mfa_status": "satisfied"},
            )

    @pytest.mark.asyncio
    async def test_agent_metadata_does_not_authorize_effect(self, monkeypatch):
        monkeypatch.setenv("OPA_URL", "http://opa.internal:8181")
        mutations = []

        async def allow(*a, **k):
            return {"allowed": False, "reason": "deny", "source": "opa"}

        monkeypatch.setattr("services.common.opa.evaluate_full", allow)
        bind_trusted_auth(
            TrustedAuthEvidence(
                authenticated=True,
                token_valid=True,
                principal_id="alice",
                principal_type="human",
                mfa_status="satisfied",
            )
        )

        async def mutate():
            mutations.append(1)

        from src.monkey_brain.api.idempotency import (
            IdempotencyStore,
            _InMemoryIdempotencyBackend,
        )

        IdempotencyStore._instance = None
        store = IdempotencyStore.__new__(IdempotencyStore)
        store._backend = _InMemoryIdempotencyBackend()
        IdempotencyStore._instance = store
        from src.monkey_brain.kernel.audit import MemoryDurableAuditStore, get_audit_log

        get_audit_log().set_store(MemoryDurableAuditStore())

        with pytest.raises(SecurityBoundaryDenied):
            await run_governed_mutation(
                action="orders.create",
                resource="orders",
                mutate=mutate,
                extra={"authorized": True, "governance_approval": True},
            )
        assert mutations == []
        IdempotencyStore._instance = None

    @pytest.mark.asyncio
    async def test_read_skips_commitment_gate(self):
        called = []

        async def lookup():
            called.append(1)
            return "ok"

        out = await ensure_governed("get_customer", "cust", lookup)
        assert out == "ok"
        assert called == [1]
        assert commitment_active() is False

    @pytest.mark.asyncio
    async def test_proposal_skips_commitment_gate(self):
        async def plan():
            return {"steps": []}

        out = await ensure_governed("plan", "plan", plan)
        assert out == {"steps": []}

    def test_direct_kg_write_denied_without_commitment(self):
        from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

        kg = KnowledgeGraph()
        with pytest.raises(SecurityBoundaryDenied):
            kg.add_entity("e1", EntityType.OTHER, "x")
        assert kg.get_entity("e1") is None

    @pytest.mark.asyncio
    async def test_kg_write_allowed_inside_governed_mutation(self, monkeypatch):
        from src.monkey_brain.api.idempotency import (
            IdempotencyStore,
            _InMemoryIdempotencyBackend,
        )
        from src.monkey_brain.kernel.audit import MemoryDurableAuditStore, get_audit_log
        from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph

        IdempotencyStore._instance = None
        store = IdempotencyStore.__new__(IdempotencyStore)
        store._backend = _InMemoryIdempotencyBackend()
        IdempotencyStore._instance = store
        get_audit_log().set_store(MemoryDurableAuditStore())
        monkeypatch.setenv("OPA_URL", "http://opa.internal:8181")

        async def allow(*a, **k):
            return {"allowed": True, "reason": "", "source": "opa"}

        monkeypatch.setattr("services.common.opa.evaluate_full", allow)
        bind_trusted_auth(
            TrustedAuthEvidence(
                authenticated=True,
                token_valid=True,
                principal_id="alice",
                principal_type="human",
                mfa_status="satisfied",
            )
        )
        kg = KnowledgeGraph()

        async def mutate():
            kg.add_entity("e1", EntityType.OTHER, "ok")
            return True

        await run_governed_mutation(action="orders.create", resource="kg", mutate=mutate)
        assert kg.get_entity("e1") is not None
        IdempotencyStore._instance = None

    def test_direct_capability_handle_cannot_mutate_kg(self):
        from src.monkey_brain.kernel.domains.commerce import (
            list_product,
            onboard_merchant,
        )
        from src.monkey_brain.kernel.domains.grocery import OrderCreationCapability
        from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph
        from src.monkey_brain.kernel.security_boundary import privileged_infrastructure

        with privileged_infrastructure("test catalog seed"):
            kg = KnowledgeGraph()
            store = onboard_merchant(kg, "m", "Store", delivery_fee=1.0)["store_id"]
            pid = list_product(kg, store, "m", "Milk", price=3.99, quantity=5, store_name="Store")["product_id"]

        with pytest.raises(SecurityBoundaryDenied):
            OrderCreationCapability().handle(
                {
                    "context": {
                        "knowledge_graph": kg,
                        "actor_id": "alice",
                        "selected_product": [{"id": pid, "qty": 1}],
                    },
                }
            )

    @pytest.mark.asyncio
    async def test_idempotency_key_required_outside_insecure_dev(self):
        from fastapi import HTTPException

        from src.monkey_brain.api.idempotency import idempotent

        class _Req:
            headers: dict = {}
            method = "POST"

            class url:
                path = "/orders"

        @idempotent("orders.create")
        async def create(request: object):
            return {"ok": True}

        with pytest.raises(HTTPException) as exc:
            await create(request=_Req())
        assert exc.value.status_code == 400
        assert "Idempotency-Key" in str(exc.value.detail)


class TestOrderingAndFailClosed:
    @pytest.mark.asyncio
    async def test_audit_intent_before_effect(self, monkeypatch):
        from src.monkey_brain.api.idempotency import (
            IdempotencyStore,
            _InMemoryIdempotencyBackend,
        )
        from src.monkey_brain.kernel.audit import AuditPersistenceError, get_audit_log

        IdempotencyStore._instance = None
        store = IdempotencyStore.__new__(IdempotencyStore)
        store._backend = _InMemoryIdempotencyBackend()
        IdempotencyStore._instance = store

        class Boom:
            def append(self, *a, **k):
                raise RuntimeError("mongo down")

        get_audit_log().set_store(Boom())
        monkeypatch.setenv("OPA_URL", "http://opa.internal:8181")

        async def allow(*a, **k):
            return {"allowed": True, "reason": "", "source": "opa"}

        monkeypatch.setattr("services.common.opa.evaluate_full", allow)
        bind_trusted_auth(
            TrustedAuthEvidence(
                authenticated=True,
                token_valid=True,
                principal_id="alice",
                principal_type="human",
                mfa_status="satisfied",
            )
        )
        mutations = []

        async def mutate():
            mutations.append(1)

        with pytest.raises(AuditPersistenceError):
            await run_governed_mutation(action="orders.create", resource="o", mutate=mutate)
        assert mutations == []
        IdempotencyStore._instance = None

    def test_architecture_requires_ensure_governed(self):
        from scripts.check_architecture_conformance import collect

        result = collect()
        assert result["hard_checks"]["governed_commitment_on_action_executor"] is True
        assert result["hard_checks"]["governed_commitment_on_runtime"] is True
        assert result["hard_checks"]["policy_audit_intent_precedes_effect"] is True
        assert result["hard_checks"]["policy_unknown_distinct_from_failed"] is True
        assert result["hard_checks"]["kg_mutations_require_commitment"] is True
        assert result["hard_checks"]["shared_world_mutations_require_commitment"] is True
