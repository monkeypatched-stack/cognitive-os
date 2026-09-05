"""Systems Validation Suite — Section 25: ROS governance, extending the
existing contract tests (tests/unit/test_ros_integration_contract.py,
tests/unit/test_edge_ros_integration.py) with the specific adversarial
scenarios this section names: a malicious actor invoking ROS without
authority, and stale/replayed ROS commands.

No real ROS 2 installation exists in this environment (`ros2` binary
absent, confirmed at the start of this validation pass) -- every test
here runs against FakeRosExecutionAdapter, exactly like the existing
contract suite. Testing a REAL RclpyRosExecutionAdapter under a
malicious/replayed command remains UNTESTABLE here; see the final
report.
"""
from __future__ import annotations

import inspect

import pytest

from src.monkey_brain.kernel.approval import reset_approval_store
from src.monkey_brain.kernel.edge.ros_integration import (
    FakeRosExecutionAdapter, RosExecutionAdapter, run_ros_action_if_governed,
)
from src.monkey_brain.kernel.security_boundary import (
    SecurityBoundaryDenied, reset_governed_pipeline_for_tests,
)
from src.monkey_brain.kernel.trusted_auth import TrustedAuthEvidence, bind_trusted_auth


@pytest.fixture(autouse=True)
def _reset():
    reset_approval_store()
    reset_governed_pipeline_for_tests()
    yield
    reset_approval_store()
    reset_governed_pipeline_for_tests()


class TestDirectRosInvocationIsUnavailableToRealActorCode:
    def test_the_adapter_alone_has_no_governance_so_direct_construction_is_the_actual_risk_surface(self):
        """Honest structural finding matching Section 8's own framing:
        RosExecutionAdapter (the Protocol) has NO governance built in by
        design (its own docstring: "must not itself perform any
        authorization check"). "Direct execution unavailable" is true
        only because no real Actor code path holds a bare adapter
        without also going through run_ros_action_if_governed -- proven
        by confirming there is exactly ONE production call site that
        constructs+uses an adapter this way."""
        import ast
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        domains_dir = repo_root / "src" / "monkey_brain" / "kernel" / "domains"
        direct_invoke_sites = []
        for path in domains_dir.glob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "invoke"):
                    continue
                # RosExecutionAdapter.invoke's specific contract shape
                # (capability=..., parameters=...) -- narrower than any
                # arbitrary domain-level .invoke() dispatcher (e.g.
                # commerce.py's own generic capability-invoke helper,
                # unrelated to ROS).
                kwnames = {kw.arg for kw in node.keywords}
                if {"capability", "parameters"} <= kwnames:
                    direct_invoke_sites.append(f"{path.name}:{node.lineno}")
        assert direct_invoke_sites == [], (
            f"found .invoke() call site(s) in kernel/domains/*.py that bypass "
            f"run_ros_action_if_governed: {direct_invoke_sites}"
        )

    @pytest.mark.asyncio
    async def test_a_malicious_actor_without_authority_is_denied(self, monkeypatch):
        bind_trusted_auth(TrustedAuthEvidence(
            authenticated=True, token_valid=True, principal_id="malicious-actor",
            principal_type="service", mfa_status="satisfied",
        ))
        monkeypatch.delenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", raising=False)
        monkeypatch.setenv("OPA_REQUIRED", "true")

        async def _deny(action, resource, extra, *, verified_delegation=None):
            return {"allowed": False, "approval_mode": "DENY", "reason": "no authority over this robot"}

        monkeypatch.setattr("src.monkey_brain.kernel.security_boundary._authorize", _deny)

        adapter = FakeRosExecutionAdapter()
        with pytest.raises(SecurityBoundaryDenied):
            await run_ros_action_if_governed(
                capability="SelfDestruct", resource="robot-arm-1", parameters={},
                adapter=adapter,
            )
        assert adapter.calls == []


class TestStaleOrReplayedRosCommandsHaveNoProtectionOfTheirOwn:
    """FINDING: run_ros_action_if_governed has NO idempotency-key,
    sequence-number, or staleness parameter of its own (confirmed via
    signature inspection) -- whatever replay/staleness protection a real
    ROS deployment gets comes ENTIRELY from whatever the CALLER (an
    Actor's own executed plan step) supplies as `idempotency_key`/
    `operation_id` further up the ensure_governed chain, same as any
    other capability (this suite's own test_v05_idempotent_execution.py
    inventory already marks "physical ROS movement" as `unknown` for
    exactly this reason). Proven directly: two structurally-identical
    calls (a genuine replay) both execute the underlying adapter action
    when no caller-supplied idempotency key is present."""

    def test_run_ros_action_if_governed_has_no_replay_protection_parameters(self):
        params = set(inspect.signature(run_ros_action_if_governed).parameters)
        for absent in ("idempotency_key", "sequence", "nonce", "replay_token"):
            assert absent not in params

    @pytest.mark.asyncio
    async def test_an_identical_command_sent_twice_moves_the_robot_twice(self, monkeypatch):
        monkeypatch.setenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")
        adapter = FakeRosExecutionAdapter()

        result1 = await run_ros_action_if_governed(
            capability="MoveArm", resource="arm-1", parameters={"angle": 90}, adapter=adapter,
            local_policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"},
        )
        # A genuine replay: the exact same message, no idempotency
        # wrapper anywhere in between.
        result2 = await run_ros_action_if_governed(
            capability="MoveArm", resource="arm-1", parameters={"angle": 90}, adapter=adapter,
            local_policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"},
        )
        assert result1["success"] is True
        assert result2["success"] is True
        assert len(adapter.calls) == 2, (
            "this IS the finding: nothing here deduplicates a replayed physical-movement "
            "command -- a robot arm asked to move twice, moves twice"
        )
