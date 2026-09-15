"""ROS integration point (kernel/edge/ros_integration.py) — proves the
one invariant this module exists to enforce: a robot capability is
NEVER invoked except through the governance boundary, whether the
decision comes from a cached local snapshot or (in these tests, via the
same mechanism as every other governance test in this repo) a mocked
central one."""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.approval import reset_approval_store
from src.monkey_brain.kernel.edge.ros_integration import run_ros_action_if_governed
from src.monkey_brain.kernel.security_boundary import (
    SecurityBoundaryDenied,
    reset_governed_pipeline_for_tests,
)
from src.monkey_brain.kernel.trusted_auth import TrustedAuthEvidence, bind_trusted_auth


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    reset_approval_store()
    reset_governed_pipeline_for_tests()
    monkeypatch.setenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")
    bind_trusted_auth(
        TrustedAuthEvidence(
            authenticated=True,
            token_valid=True,
            principal_id="robot-1",
            principal_type="service",
            mfa_status="satisfied",
        )
    )
    yield
    reset_approval_store()
    reset_governed_pipeline_for_tests()


class _FakeAdapter:
    def __init__(self) -> None:
        self.called = False

    async def invoke(self, *, capability: str, parameters: dict) -> dict:
        self.called = True
        return {"success": True, "moved": True}


class TestRosOnlyExecutesThroughGovernance:
    @pytest.mark.asyncio
    async def test_allowed_local_decision_invokes_the_adapter(self):
        adapter = _FakeAdapter()
        result = await run_ros_action_if_governed(
            capability="MoveArm",
            resource="arm-1",
            parameters={"angle": 90},
            adapter=adapter,
            local_policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"},
        )
        assert adapter.called is True
        assert result == {"success": True, "moved": True}

    @pytest.mark.asyncio
    async def test_denied_local_decision_never_invokes_the_adapter(self):
        adapter = _FakeAdapter()
        with pytest.raises(SecurityBoundaryDenied):
            await run_ros_action_if_governed(
                capability="MoveArm",
                resource="arm-1",
                parameters={"angle": 90},
                adapter=adapter,
                local_policy_decision={"allowed": False, "approval_mode": "DENY"},
            )
        assert adapter.called is False

    @pytest.mark.asyncio
    async def test_no_decision_at_all_still_goes_through_real_governance(self, monkeypatch):
        """Omitting local_policy_decision must not silently allow --
        it falls through to the normal live _authorize() path, same as
        any other capability."""
        adapter = _FakeAdapter()

        async def deny(action, resource, extra, *, verified_delegation=None):
            return {
                "allowed": False,
                "approval_mode": "DENY",
                "reason": "no cached or central authority",
            }

        monkeypatch.setattr("src.monkey_brain.kernel.security_boundary._authorize", deny)
        monkeypatch.setenv("OPA_REQUIRED", "true")

        with pytest.raises(SecurityBoundaryDenied):
            await run_ros_action_if_governed(
                capability="MoveArm",
                resource="arm-1",
                parameters={"angle": 90},
                adapter=adapter,
            )


class TestRosIdempotency:
    """idempotency_key closes the gap tests/validation/test_v13_ros_governance.py
    documents and deliberately leaves open: an identical command sent twice
    moved the vehicle twice. Governance itself must still re-run on every
    call — only the physical effect (adapter.invoke()) is deduplicated."""

    @pytest.mark.asyncio
    async def test_same_key_invokes_the_adapter_only_once(self):
        adapter = _FakeAdapter()
        kwargs = dict(
            capability="MoveArm",
            resource="arm-1",
            parameters={"angle": 90},
            adapter=adapter,
            local_policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"},
            idempotency_key="plan-step-7",
        )

        first = await run_ros_action_if_governed(**kwargs)
        adapter.called = False  # reset the flag; a second real invoke would flip it back
        second = await run_ros_action_if_governed(**kwargs)

        assert first == second == {"success": True, "moved": True}
        assert adapter.called is False  # replayed the cached result, never called invoke() again

    @pytest.mark.asyncio
    async def test_different_key_invokes_the_adapter_again(self):
        adapter = _FakeAdapter()
        kwargs = dict(
            capability="MoveArm",
            resource="arm-1",
            parameters={"angle": 90},
            adapter=adapter,
            local_policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"},
        )

        await run_ros_action_if_governed(**kwargs, idempotency_key="plan-step-7")
        adapter.called = False
        await run_ros_action_if_governed(**kwargs, idempotency_key="plan-step-8")

        assert adapter.called is True

    @pytest.mark.asyncio
    async def test_no_key_preserves_prior_always_invoke_behavior(self):
        adapter = _FakeAdapter()
        kwargs = dict(
            capability="MoveArm",
            resource="arm-1",
            parameters={"angle": 90},
            adapter=adapter,
            local_policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"},
        )

        await run_ros_action_if_governed(**kwargs)
        adapter.called = False
        await run_ros_action_if_governed(**kwargs)

        assert adapter.called is True

    @pytest.mark.asyncio
    async def test_same_key_different_parameters_is_a_conflict_not_a_replay(self):
        adapter = _FakeAdapter()
        await run_ros_action_if_governed(
            capability="MoveArm",
            resource="arm-1",
            parameters={"angle": 90},
            adapter=adapter,
            local_policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"},
            idempotency_key="plan-step-7",
        )
        adapter.called = False
        result = await run_ros_action_if_governed(
            capability="MoveArm",
            resource="arm-1",
            parameters={"angle": 45},  # different payload, same key -- a caller bug, not a retry
            adapter=adapter,
            local_policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"},
            idempotency_key="plan-step-7",
        )

        assert result["success"] is False
        assert "different" in result["error"]
        assert adapter.called is False  # never touched the vehicle for the conflicting request
        assert adapter.called is False
