"""Wave 1 tests for Kernel-owned runtime/discovery registry facades."""

import pytest

from src.monkey_brain.kernel.kernel import (
    AgentRegistry,
    CapabilityRegistry,
    RuntimeRegistry,
)
from src.monkey_brain.kernel.scheduler import RuntimeDescriptor


class _Runtime:
    pass


class _Capability:
    name = "example"


def test_runtime_registry_registers_metadata_and_preserves_lookup():
    registry = RuntimeRegistry()
    runtime = _Runtime()
    descriptor = RuntimeDescriptor(
        runtime_id="runtime-1",
        name="cognitive",
        runtime_class="_Runtime",
        version="2.0.0",
        dependencies=[],
        owner="kernel.boot",
        provenance="test",
    )

    registry.register("runtime-1", runtime, descriptor=descriptor)

    assert registry.get("runtime-1") is runtime
    assert registry.descriptor("runtime-1").version == "2.0.0"
    assert registry.descriptor("runtime-1").owner == "kernel.boot"
    registry.validate()


def test_runtime_registry_tracks_lifecycle_health_and_shutdown_metadata():
    registry = RuntimeRegistry()
    descriptor = RuntimeDescriptor(
        runtime_id="runtime-1",
        name="cognitive",
        lifecycle_state="booting",
        health_state="unknown",
        shutdown_hook="shutdown",
    )
    registry.register("runtime-1", _Runtime(), descriptor=descriptor)

    registry.set_lifecycle("runtime-1", "ready")
    from src.monkey_brain.kernel.kernel import ComponentHealth, HealthState

    registry.set_health("runtime-1", ComponentHealth("runtime-1", HealthState.HEALTHY))

    result = registry.descriptor("runtime-1")
    assert result.lifecycle_state == "ready"
    assert result.health_state == "healthy"
    assert result.shutdown_hook == "shutdown"


def test_runtime_registry_rejects_duplicate_ids_and_names():
    registry = RuntimeRegistry()
    registry.register("one", _Runtime(), descriptor=RuntimeDescriptor(runtime_id="one", name="same"))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(
            "one",
            _Runtime(),
            descriptor=RuntimeDescriptor(runtime_id="one", name="other"),
        )
    with pytest.raises(ValueError, match="name already registered"):
        registry.register(
            "two",
            _Runtime(),
            descriptor=RuntimeDescriptor(runtime_id="two", name="same"),
        )


def test_runtime_registry_validates_missing_dependencies_and_cycles():
    missing = RuntimeRegistry()
    missing.register(
        "one",
        _Runtime(),
        descriptor=RuntimeDescriptor(runtime_id="one", name="one", dependencies=["missing"]),
    )
    with pytest.raises(RuntimeError, match="missing dependencies"):
        missing.validate()

    cycle = RuntimeRegistry()
    cycle.register(
        "one",
        _Runtime(),
        descriptor=RuntimeDescriptor(runtime_id="one", name="one", dependencies=["two"]),
    )
    cycle.register(
        "two",
        _Runtime(),
        descriptor=RuntimeDescriptor(runtime_id="two", name="two", dependencies=["one"]),
    )
    with pytest.raises(RuntimeError, match="dependency cycle"):
        cycle.validate()


def test_runtime_registry_rejects_invalid_states():
    registry = RuntimeRegistry()
    with pytest.raises(ValueError, match="lifecycle"):
        registry.register(
            "one",
            _Runtime(),
            descriptor=RuntimeDescriptor(runtime_id="one", lifecycle_state="invalid"),
        )
    with pytest.raises(ValueError, match="health"):
        registry.register(
            "two",
            _Runtime(),
            descriptor=RuntimeDescriptor(runtime_id="two", health_state="invalid"),
        )


def test_agent_registry_adapts_local_backend():
    registry = AgentRegistry()
    agent = object()
    registry.register(agent, "agent-1")
    assert registry.discover("agent-1") is agent
    assert registry.names() == ["agent-1"]


def test_capability_registry_adapts_existing_bus():
    class Bus:
        _capabilities = {"example": _Capability()}

        def discover(self, name):
            return self._capabilities.get(name)

    registry = CapabilityRegistry()
    registry.attach_bus(Bus())
    assert registry.discover("example").name == "example"
    assert registry.names() == ["example"]
