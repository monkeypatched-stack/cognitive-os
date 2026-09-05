"""RosExecutionAdapter contract tests (kernel/edge/ros_integration.py).

TestAdapterContract is a mixin every adapter implementation must satisfy
-- run unconditionally against FakeRosExecutionAdapter (normal CI, no ROS
needed) and, ONLY if a real ROS 2 installation is importable in this
environment, also against RclpyRosExecutionAdapter as an optional
integration suite. This environment has no ROS 2 installation, so that
second run is expected to skip here -- this file does not fake having
ROS, it honestly reports "not run" via pytest.skip.
"""
from __future__ import annotations

import pytest

from src.monkey_brain.kernel.edge.ros_integration import (
    FakeRosExecutionAdapter,
    RosUnavailableError,
    build_ros_execution_adapter,
    run_ros_action_if_governed,
)

try:
    import rclpy  # type: ignore[import-not-found]  # noqa: F401
    _ROS_AVAILABLE = True
except ImportError:
    _ROS_AVAILABLE = False


class _AdapterContractMixin:
    """Subclasses provide `self.build_adapter()`. Every RosExecutionAdapter
    implementation, real or fake, must satisfy these."""

    def build_adapter(self):
        raise NotImplementedError

    @pytest.mark.asyncio
    async def test_invoke_returns_a_dict_with_a_success_key(self):
        adapter = self.build_adapter()
        result = await adapter.invoke(capability="test.capability", parameters={"x": 1})
        assert isinstance(result, dict)
        assert "success" in result

    @pytest.mark.asyncio
    async def test_invoke_never_performs_its_own_authorization_check(self):
        """The adapter contract explicitly forbids an adapter from
        containing governance logic -- it is an execution interface,
        never a governance mechanism (Section 1's own invariant). This is
        a structural check: the adapter class itself must not import or
        reference ensure_governed/security_boundary."""
        import inspect

        adapter = self.build_adapter()
        source = inspect.getsource(type(adapter))
        assert "ensure_governed" not in source
        assert "security_boundary" not in source


class TestFakeRosExecutionAdapterContract(_AdapterContractMixin):
    def build_adapter(self):
        return FakeRosExecutionAdapter()

    @pytest.mark.asyncio
    async def test_records_every_invocation(self):
        adapter = self.build_adapter()
        await adapter.invoke(capability="move_forward", parameters={"distance_m": 1.0})
        assert len(adapter.calls) == 1
        assert adapter.calls[0]["capability"] == "move_forward"

    @pytest.mark.asyncio
    async def test_result_is_honestly_labeled_simulated(self):
        adapter = self.build_adapter()
        result = await adapter.invoke(capability="move_forward", parameters={})
        assert result["simulated"] is True


@pytest.mark.skipif(not _ROS_AVAILABLE, reason="rclpy not installed in this environment -- real ROS integration suite requires a ROS 2 installation")
class TestRclpyRosExecutionAdapterContract(_AdapterContractMixin):
    """Only runs when rclpy is actually importable. Never faked."""

    def build_adapter(self):
        from src.monkey_brain.kernel.edge.ros_integration import RclpyRosExecutionAdapter
        return RclpyRosExecutionAdapter()


class TestGovernanceBoundaryIsUnconditional:
    """run_ros_action_if_governed is the ONLY sanctioned entry point --
    proves it always routes through ensure_governed regardless of which
    adapter is plugged in."""

    @pytest.mark.asyncio
    async def test_denied_governance_never_reaches_the_adapter(self, monkeypatch):
        adapter = FakeRosExecutionAdapter()

        async def _deny(*args, **kwargs):
            from src.monkey_brain.kernel.security_boundary import SecurityBoundaryDenied
            raise SecurityBoundaryDenied("denied for test")

        monkeypatch.setattr(
            "src.monkey_brain.kernel.security_boundary.ensure_governed", _deny,
        )
        from src.monkey_brain.kernel.security_boundary import SecurityBoundaryDenied

        with pytest.raises(SecurityBoundaryDenied):
            await run_ros_action_if_governed(
                capability="move_forward", resource="move_forward", parameters={}, adapter=adapter,
            )
        assert adapter.calls == []


class TestStartupBehaviorWhenRosUnavailable:
    def test_normal_runtime_never_crashes_when_ros_is_not_installed(self):
        """require_real=False (the default -- normal CognitiveOS
        runtime) must ALWAYS succeed regardless of whether ROS is
        installed."""
        adapter = build_ros_execution_adapter()
        assert isinstance(adapter, FakeRosExecutionAdapter)

    @pytest.mark.skipif(_ROS_AVAILABLE, reason="this test specifically covers the ROS-NOT-installed case")
    def test_robot_deployment_requiring_real_ros_gets_a_clear_actionable_error(self):
        with pytest.raises(RosUnavailableError, match="rclpy"):
            build_ros_execution_adapter(require_real=True)
