"""Wave 6 hard-invariant and certification gate tests."""

import asyncio
import json
from pathlib import Path

from scripts.check_architecture_conformance import collect

from src.monkey_brain.kernel.kernel import Kernel


def test_kernel_architecture_invariants_are_canonical():
    kernel = Kernel()
    result = kernel.validate_architecture()

    assert not result["violations"]
    assert kernel.runtime_registry is kernel.registry
    assert kernel.runtime_selector.registry is kernel.registry


def test_kernel_execute_dispatches_only_registered_runtime():
    class Runtime:
        async def execute(self, request, **kwargs):
            return {"request": request, "kwargs": kwargs}

    kernel = Kernel()
    runtime = Runtime()
    kernel.registry.register("test", runtime)

    result = asyncio.run(kernel.execute("request", runtime_id="test", trace_id="trace"))

    assert result == {"request": "request", "kwargs": {"trace_id": "trace"}}


def test_positive_ownership_graph_is_fully_verified():
    result = collect()
    ownership = result["positive_ownership"]

    assert ownership["score"] == 100.0
    assert ownership["passed"] == ownership["total"]
    assert all(item["passed"] for item in ownership["checks"].values())

    graph_path = Path(__file__).parents[1] / "OWNERSHIP_GRAPH.json"
    graph = json.loads(graph_path.read_text())
    assert set(graph["owners"]) == {
        "Kernel",
        "ActorRuntime",
        "PlanetaryRuntime",
        "CognitiveOS",
        "AgentRuntime",
        "CapabilityRuntime",
    }
