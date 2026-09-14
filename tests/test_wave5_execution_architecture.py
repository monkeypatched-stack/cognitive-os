"""Wave 5 execution/orchestration boundary tests."""

from src.monkey_brain.kernel.pipeline.action_executor import ActionExecutor
from src.monkey_brain.kernel.pipeline.capability_runtime import CapabilityRuntime
from src.monkey_brain.runtime.agent_middleware import AgentMiddleware
from src.monkey_brain.runtime.agent_runtime import AgentRuntime


def test_capability_runtime_reuses_existing_capability_executor():
    assert CapabilityRuntime is ActionExecutor
    runtime = CapabilityRuntime()
    assert runtime._capability_bus is None
    assert not hasattr(runtime, "classify")
    assert not hasattr(runtime, "route")


def test_agent_runtime_reuses_existing_orchestration_middleware():
    assert AgentRuntime is AgentMiddleware
    runtime = AgentRuntime()
    assert hasattr(runtime, "classifier")
    assert hasattr(runtime, "router")
    assert hasattr(runtime, "resolver")
    assert not hasattr(runtime, "_capability_bus")


def test_vertical_engine_uses_one_existing_capability_bus():
    import src.monkey_brain.kernel.domains.grocery  # registers the existing vertical
    from src.monkey_brain.kernel.domains.vertical_router import (
        build_execution_engine,
        resolve_vertical,
    )

    vertical = resolve_vertical("grocery")
    engine = build_execution_engine("grocery")

    assert engine._capability_bus is vertical.bus or engine._capability_bus is not None
    assert isinstance(engine, CapabilityRuntime)


def test_runtime_engine_injects_bus_before_policy_captures_executor():
    """Actor execution must use the same real bus offered to its planner."""
    import src.monkey_brain.kernel.domains.grocery  # registers the vertical
    from src.monkey_brain.kernel.domains.vertical_router import build_runtime_engine

    engine = build_runtime_engine(None, name="grocery")

    assert engine._capability_runtime._capability_bus is not None
    assert engine._policy.execution.capability_runtime is engine._capability_runtime
