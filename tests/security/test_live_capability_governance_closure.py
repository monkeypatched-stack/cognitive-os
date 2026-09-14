"""Live Capability Governance Closure — regression tests for the real gap:

    ActionExecutor._execute_action() called capability.handle() directly,
    bypassing the SAME canonical governance boundary
    (kernel/security_boundary.py::ensure_governed) that
    kernel/execute/capability_bus.py::CapabilityBus.execute() already
    wraps every ITS dispatches in. The live grocery/plan-execution path
    goes through ActionExecutor + a plain CommerceCapabilityBus/
    GroceryCapabilityBus registry (discover()+handle() only, no
    ensure_governed at all) -- a DIFFERENT class from
    kernel.execute.capability_bus.CapabilityBus despite the similar name
    -- so per-capability governance never actually fired on the real path.

These tests exercise the REAL ActionExecutor.execute()/_execute_action()
(not a mocked ActionExecutor), mocking only _authorize() -- the same,
already-established single seam every other governance test in this repo
mocks (test_governance_gate.py, test_runtime_approval_gate_wiring.py) --
to control the AUTO_APPROVE/HUMAN_APPROVAL_REQUIRED/DENY decision without
needing a live OPA server. The last test in this file goes one step
further: a REAL grocery capability, a REAL KnowledgeGraph, proving the
actual side effect (a product reservation) never happens when governance
denies -- not merely that a mock was or wasn't called.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.monkey_brain.kernel.pipeline.action_executor import ActionExecutor
from src.monkey_brain.kernel.pipeline.execution import Action
from src.monkey_brain.kernel.security_boundary import (
    HumanApprovalRequired,
    SecurityBoundaryDenied,
    reset_governed_pipeline_for_tests,
)
from src.monkey_brain.kernel.approval import reset_approval_store
from src.monkey_brain.kernel.trusted_auth import TrustedAuthEvidence, bind_trusted_auth


def make_trusted_auth(principal_id: str = "user:test") -> TrustedAuthEvidence:
    return TrustedAuthEvidence(
        authenticated=True,
        token_valid=True,
        principal_id=principal_id,
        principal_type="human",
        mfa_status="satisfied",
    )


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    reset_approval_store()
    reset_governed_pipeline_for_tests()
    monkeypatch.setenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")
    monkeypatch.setenv("OPA_REQUIRED", "true")  # force _authorize() to actually run (see prior session's fix)
    bind_trusted_auth(make_trusted_auth())
    yield
    reset_approval_store()
    reset_governed_pipeline_for_tests()


def _fake_bus_and_capability():
    capability = MagicMock()
    capability.handle = MagicMock(return_value={"success": True, "result": "ok"})
    bus = MagicMock()
    bus.discover.return_value = capability
    return bus, capability


def _mock_authorize(mode: str, risk: str = "LOW"):
    """Only gates the specific capability action (action starts with
    "capability."). ActionExecutor.execute()'s own outer, generic
    ensure_governed("action_executor.execute", "actions", ...) batch-level
    check must still ALLOW -- it makes no per-capability decision itself
    (see security_boundary.py's docstrings), so a realistic OPA policy
    would never deny it based on which capabilities happen to be in the
    batch. This is what lets the per-capability decision underneath it
    actually be exercised and observed."""

    async def _authorize(action, resource, extra, *, verified_delegation=None):
        if not action.startswith("capability."):
            return {
                "allowed": True,
                "reason": "batch_level_allow",
                "approval_mode": "AUTO_APPROVE",
            }
        return {
            "allowed": mode != "DENY",
            "reason": f"test_{mode.lower()}",
            "approval_mode": mode,
            "approval_source": "POLICY_AUTOMATIC",
            "risk_level": risk,
            "policy_rule": "test_policy",
            "requires_hitl": mode == "HUMAN_APPROVAL_REQUIRED",
        }

    return _authorize


class TestNegativePathFirst:
    """Section 14, in the exact order requested: DENY, then
    HUMAN_APPROVAL_REQUIRED-with-no-approval, then ALLOW. All three
    exercise the REAL ActionExecutor.execute()."""

    @pytest.mark.asyncio
    async def test_deny_prevents_capability_execution(self, monkeypatch):
        monkeypatch.setattr(
            "src.monkey_brain.kernel.security_boundary._authorize",
            _mock_authorize("DENY", risk="CRITICAL"),
        )
        bus, capability = _fake_bus_and_capability()
        executor = ActionExecutor(capability_bus=bus)
        action = Action(action_id="a1", capability="Payment", step_index=0)

        result = await executor.execute((action,))

        assert capability.handle.called is False, "DENY must prevent capability.handle() from ever running"
        assert result.actions[0].success is False
        assert result.actions[0].result.get("denied") is True

    @pytest.mark.asyncio
    async def test_human_approval_required_with_no_approval_blocks_execution(self, monkeypatch):
        monkeypatch.setattr(
            "src.monkey_brain.kernel.security_boundary._authorize",
            _mock_authorize("HUMAN_APPROVAL_REQUIRED", risk="HIGH"),
        )
        bus, capability = _fake_bus_and_capability()
        executor = ActionExecutor(capability_bus=bus)
        action = Action(action_id="a1", capability="Payment", step_index=0)

        result = await executor.execute((action,))

        assert capability.handle.called is False, "HITL-required-but-absent must block execution"
        assert result.actions[0].success is False
        assert result.actions[0].result.get("requires_approval") is True
        assert result.actions[0].result.get("approval_id")

    @pytest.mark.asyncio
    async def test_allow_permits_capability_execution(self, monkeypatch):
        monkeypatch.setattr(
            "src.monkey_brain.kernel.security_boundary._authorize",
            _mock_authorize("AUTO_APPROVE"),
        )
        bus, capability = _fake_bus_and_capability()
        executor = ActionExecutor(capability_bus=bus)
        action = Action(action_id="a1", capability="ProductSelection", step_index=0)

        result = await executor.execute((action,))

        assert capability.handle.called is True, "AUTO_APPROVE must permit capability.handle() to run"
        assert result.actions[0].success is True


class TestFailureSemantics:
    """Section 8/13: every listed failure mode must prevent execution."""

    @pytest.mark.asyncio
    async def test_opa_failure_prevents_capability_execution(self, monkeypatch):
        # GovernanceEngine.evaluate() (kernel/governance.py) already fails
        # closed on an unreachable/erroring OPA backend -- it returns a
        # denied policy dict rather than raising -- so this simulates that
        # real shape rather than a raw exception escaping _authorize().
        async def _opa_unreachable(action, resource, extra, *, verified_delegation=None):
            if not action.startswith("capability."):
                return {
                    "allowed": True,
                    "reason": "batch_level_allow",
                    "approval_mode": "AUTO_APPROVE",
                }
            return {
                "allowed": False,
                "reason": "opa_unreachable",
                "approval_mode": "DENY",
            }

        monkeypatch.setattr("src.monkey_brain.kernel.security_boundary._authorize", _opa_unreachable)
        bus, capability = _fake_bus_and_capability()
        executor = ActionExecutor(capability_bus=bus)
        action = Action(action_id="a1", capability="Payment", step_index=0)

        result = await executor.execute((action,))

        assert capability.handle.called is False
        assert result.actions[0].success is False

    @pytest.mark.asyncio
    async def test_missing_auth_prevents_capability_execution(self, monkeypatch):
        """No insecure-dev relaxation, no authenticated evidence -> AUTH
        stage fails closed before _authorize()/capability.handle() are
        ever reached. This fails closed at execute()'s own pre-existing
        outer batch-level gate (_assert_auth() checks evidence, not
        per-capability policy) -- SecurityBoundaryDenied propagates out of
        execute() itself here, same as it already did before this task's
        change; the security property under test is simply that
        capability.handle() is never reached."""
        from src.monkey_brain.kernel.trusted_auth import unauthenticated_evidence

        monkeypatch.delenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", raising=False)
        bind_trusted_auth(unauthenticated_evidence())
        bus, capability = _fake_bus_and_capability()
        executor = ActionExecutor(capability_bus=bus)
        action = Action(action_id="a1", capability="Payment", step_index=0)

        with pytest.raises(SecurityBoundaryDenied):
            await executor.execute((action,))

        assert capability.handle.called is False


class TestCapabilityIsIncludedInAuthorization:
    """Section 5: the actual capability/action/resource being invoked must
    reach the authorization decision -- not a generic "agent may execute"."""

    @pytest.mark.asyncio
    async def test_capability_name_reaches_authorize(self, monkeypatch):
        captured = {}

        async def _capture(action, resource, extra, *, verified_delegation=None):
            captured["action"] = action
            captured["resource"] = resource
            captured["extra"] = extra
            return {"allowed": True, "approval_mode": "AUTO_APPROVE"}

        monkeypatch.setattr("src.monkey_brain.kernel.security_boundary._authorize", _capture)
        bus, capability = _fake_bus_and_capability()
        executor = ActionExecutor(capability_bus=bus)
        action = Action(
            action_id="a1",
            capability="Payment",
            step_index=0,
            parameters={"amount": 12.26, "order_id": "ORD-1"},
        )

        await executor.execute((action,))

        assert captured["action"] == "capability.Payment"
        assert captured["resource"] == "Payment"
        assert captured["extra"]["capability"] == "Payment"
        assert captured["extra"]["parameters"] == {"amount": 12.26, "order_id": "ORD-1"}

    @pytest.mark.asyncio
    async def test_agent_supplied_security_signals_in_parameters_are_stripped_before_opa(self, monkeypatch):
        """MESSAGE_SENDER_CANNOT_ASSERT_IDENTITY's sibling: an agent
        cannot smuggle authorized=True/mfa_status=satisfied/etc. into the
        parameters dict and have it reach OPA's input as if trusted."""
        from src.monkey_brain.kernel.security_boundary import build_opa_input

        captured = {}

        async def _capture(action, resource, extra, *, verified_delegation=None):
            captured["opa_input"] = build_opa_input(action=action, resource=resource, extra=extra)
            return {"allowed": True, "approval_mode": "AUTO_APPROVE"}

        monkeypatch.setattr("src.monkey_brain.kernel.security_boundary._authorize", _capture)
        bus, capability = _fake_bus_and_capability()
        executor = ActionExecutor(capability_bus=bus)
        action = Action(
            action_id="a1",
            capability="Payment",
            step_index=0,
            parameters={"authorized": True, "mfa_status": "satisfied", "amount": 12.26},
        )

        await executor.execute((action,))

        ctx = captured["opa_input"]["context"]
        assert "authorized" not in ctx["parameters"]
        assert "mfa_status" not in ctx["parameters"]
        assert ctx["parameters"]["amount"] == 12.26


class TestRetryCannotBypassGovernance:
    @pytest.mark.asyncio
    async def test_repeated_denied_attempt_never_reaches_capability(self, monkeypatch):
        monkeypatch.setattr(
            "src.monkey_brain.kernel.security_boundary._authorize",
            _mock_authorize("DENY"),
        )
        bus, capability = _fake_bus_and_capability()
        executor = ActionExecutor(capability_bus=bus)
        action = Action(action_id="a1", capability="Payment", step_index=0)

        first = await executor.execute((action,))
        second = await executor.execute((action,))

        assert capability.handle.called is False
        assert first.actions[0].success is False
        assert second.actions[0].success is False


class TestDirectCapabilityCallCannotBypassBoundary:
    @pytest.mark.asyncio
    async def test_ensure_governed_is_the_only_route_to_handle(self, monkeypatch):
        """Structural proof: ensure_governed is actually invoked on the
        way to capability.handle(), not merely importable/available."""
        calls = []
        real_ensure_governed = None
        import src.monkey_brain.kernel.security_boundary as sb

        real_ensure_governed = sb.ensure_governed

        async def _spy(action, resource, effect, **kwargs):
            calls.append((action, resource))
            return await real_ensure_governed(action, resource, effect, **kwargs)

        monkeypatch.setattr(sb, "ensure_governed", _spy)
        monkeypatch.setattr(sb, "_authorize", _mock_authorize("AUTO_APPROVE"))
        bus, capability = _fake_bus_and_capability()
        executor = ActionExecutor(capability_bus=bus)
        action = Action(action_id="a1", capability="ProductSelection", step_index=0)

        await executor.execute((action,))

        assert ("capability.ProductSelection", "ProductSelection") in calls
        assert capability.handle.called is True


class TestRealGroceryPathDeniesWithoutMockingGovernance:
    """Section 13's 'most important' test: the actual grocery production
    path (real ActionExecutor, real GroceryCapabilityBus, real
    ProductSelectionCapability, real KnowledgeGraph) -- only _authorize()
    (the one seam that would otherwise require a live OPA server) is
    controlled. Proves the REAL side effect (a product reservation) never
    happens, not merely that a mock wasn't called."""

    @pytest.mark.asyncio
    async def test_denied_capability_produces_no_real_reservation(self, monkeypatch):
        from src.monkey_brain.kernel.domains import (
            grocery,
        )  # noqa: F401 -- registers the vertical
        from src.monkey_brain.kernel.domains.commerce import (
            list_product,
            onboard_merchant,
        )
        from src.monkey_brain.kernel.domains.vertical_router import (
            build_execution_engine,
        )
        from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph()
        store = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=1.99)["store_id"]
        milk_id = list_product(
            kg,
            store,
            "merchant_a",
            "Milk",
            price=3.99,
            quantity=5,
            store_name="Trader Joe's",
        )["product_id"]

        monkeypatch.setattr(
            "src.monkey_brain.kernel.security_boundary._authorize",
            _mock_authorize("DENY", risk="CRITICAL"),
        )
        executor = build_execution_engine("grocery")
        action = Action(
            action_id="a1",
            capability="ProductSelection",
            step_index=0,
            parameters={"selection": [{"id": milk_id, "qty": 1}]},
        )
        context = {"knowledge_graph": kg, "actor_id": "denied-actor", "question": ""}

        result = await executor.execute((action,), context)

        assert result.actions[0].success is False
        assert result.actions[0].result.get("denied") is True
        # The REAL side effect: no reservation exists on the product entity.
        product = kg.get_entity(milk_id)
        assert not product.attributes.get("reservations")

    @pytest.mark.asyncio
    async def test_allowed_capability_produces_the_real_side_effect(self, monkeypatch):
        """Sibling positive case: with the SAME real path, AUTO_APPROVE
        lets the real capability run and its real effect actually happen
        -- proves the fix does not silently swallow legitimate execution."""
        from src.monkey_brain.kernel.domains import grocery  # noqa: F401
        from src.monkey_brain.kernel.domains.commerce import (
            list_product,
            onboard_merchant,
        )
        from src.monkey_brain.kernel.domains.vertical_router import (
            build_execution_engine,
        )
        from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph()
        store = onboard_merchant(kg, "merchant_a", "Trader Joe's", delivery_fee=1.99)["store_id"]
        milk_id = list_product(
            kg,
            store,
            "merchant_a",
            "Milk",
            price=3.99,
            quantity=5,
            store_name="Trader Joe's",
        )["product_id"]

        monkeypatch.setattr(
            "src.monkey_brain.kernel.security_boundary._authorize",
            _mock_authorize("AUTO_APPROVE"),
        )
        executor = build_execution_engine("grocery")
        action = Action(
            action_id="a1",
            capability="ProductSelection",
            step_index=0,
            parameters={"selection": [{"id": milk_id, "qty": 1}]},
        )
        context = {"knowledge_graph": kg, "actor_id": "allowed-actor", "question": ""}

        result = await executor.execute((action,), context)

        assert result.actions[0].success is True
        assert result.actions[0].result["selected"][0]["id"] == milk_id
