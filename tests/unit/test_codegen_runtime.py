"""Tests for CodeGenRuntime (kernel/codegen_runtime.py) — the 4th runtime,
walking the SDLC (Specification..Release) as an ExecutionGraph of Broca
agents, executed via the shared ProcessManager.

Covers:
  1. SDLC graph structure — Implementation fans out into DDD construct
     checks + compliance checks, all gating Review (the concrete ask:
     "the code gen agent must use the ddd and ddd check agents for
     code gen")
  2. BrocaCapabilityBus adapter: successful dispatch translates a Broca
     AgentResult into the CapabilityBus-shaped result GraphScheduler expects
  3. BrocaCapabilityBus adapter: unknown capability -> graceful failure,
     not a crash
  4. BrocaCapabilityBus context accumulation — a later stage's dispatch
     sees an earlier stage's payload merged into its context
  5. Full happy-path run through ProcessManager with a stub registry
     (all 22 SDLC nodes complete)
  6. compliance_domains is configurable — passing extra domains adds nodes,
     not just the 3 defaults

Written, not executed, per project convention (write test files; don't run
pytest as part of a fix/feature change) — each scenario here was
independently verified via standalone scripts during development.
"""

import asyncio
import sys
import os

_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_repo, os.path.join(_repo, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.monkey_brain.kernel.codegen_runtime import (
    CodeGenRuntime,
    BrocaCapabilityBus,
    _DEFAULT_COMPLIANCE_DOMAINS,
    _DDD_CONSTRUCT_AGENTS,
)
from src.monkey_brain.kernel.process.manager import ProcessManager
from src.monkey_brain.kernel.process.models import RuntimeProcessState
from src.monkey_brain.kernel.execute.graph import NodeState


def test_sdlc_graph_implementation_fans_out_into_ddd_and_compliance():
    rt = CodeGenRuntime()
    graph = rt.build_sdlc_graph("build a work order tracking service")

    service_gen = "sdlc:implementation:service_gen"
    ddd_node_ids = {f"sdlc:implementation:ddd_{name}" for name in _DDD_CONSTRUCT_AGENTS}
    compliance_node_ids = {f"sdlc:implementation:compliance_{d}" for d in _DEFAULT_COMPLIANCE_DOMAINS}

    for node_id in ddd_node_ids | compliance_node_ids:
        node = graph.get_node(node_id)
        assert node is not None, f"missing node {node_id}"
        deps = {e.src for e in graph.incoming(node_id) if e.rel == "depends_on"}
        assert deps == {service_gen}, f"{node_id} should depend only on service_gen, got {deps}"

    review = graph.get_node("sdlc:review")
    review_deps = {e.src for e in graph.incoming("sdlc:review") if e.rel == "depends_on"}
    assert service_gen in review_deps
    assert ddd_node_ids <= review_deps
    assert compliance_node_ids <= review_deps


def test_sdlc_graph_is_linear_before_implementation_and_after_testing():
    rt = CodeGenRuntime()
    graph = rt.build_sdlc_graph("q")

    def deps_of(node_id):
        return {e.src for e in graph.incoming(node_id) if e.rel == "depends_on"}

    assert deps_of("sdlc:requirements") == {"sdlc:specification"}
    assert deps_of("sdlc:architecture") == {"sdlc:requirements"}
    assert deps_of("sdlc:design") == {"sdlc:architecture"}
    assert deps_of("sdlc:packaging") == {"sdlc:testing:integration"}
    assert deps_of("sdlc:release") == {"sdlc:deploy"}


def test_compliance_domains_configurable():
    rt = CodeGenRuntime()
    graph_default = rt.build_sdlc_graph("q")
    graph_extra = rt.build_sdlc_graph("q", compliance_domains=("soc2", "gdpr", "iso27001", "fda", "gxp"))

    assert graph_extra.node_count > graph_default.node_count
    assert graph_extra.get_node("sdlc:implementation:compliance_fda") is not None
    assert graph_default.get_node("sdlc:implementation:compliance_fda") is None


def test_broca_capability_bus_successful_dispatch():
    async def scenario():
        class FakeAgentResult:
            success = True
            payload = {"requirements": [{"id": "R1", "statement": "do the thing"}]}

        class FakeAgent:
            async def handle(self, context):
                return FakeAgentResult()

        class FakeRegistry:
            def discover(self, capability_name):
                assert capability_name == "requirements"
                return FakeAgent()

        bus = BrocaCapabilityBus(registry=FakeRegistry())
        result = await bus.execute("requirements", {"question": "q"})
        assert result.success is True
        assert result.output == FakeAgentResult.payload
        assert result.error == ""

    asyncio.run(scenario())


def test_broca_capability_bus_unknown_capability_is_graceful():
    async def scenario():
        class EmptyRegistry:
            def discover(self, capability_name):
                return None

        bus = BrocaCapabilityBus(registry=EmptyRegistry())
        result = await bus.execute("nonexistent_capability", {})
        assert result.success is False
        assert "no Broca agent registered" in result.error

    asyncio.run(scenario())


def test_broca_capability_bus_agent_exception_is_graceful():
    async def scenario():
        class ExplodingAgent:
            async def handle(self, context):
                raise RuntimeError("boom")

        class Registry:
            def discover(self, capability_name):
                return ExplodingAgent()

        bus = BrocaCapabilityBus(registry=Registry())
        result = await bus.execute("cap", {})
        assert result.success is False
        assert "boom" in result.error

    asyncio.run(scenario())


def test_broca_capability_bus_accumulates_context_across_stages():
    async def scenario():
        calls = []

        class RecordingAgent:
            def __init__(self, name):
                self.name = name

            async def handle(self, context):
                calls.append((self.name, dict(context)))

                class R:
                    success = True
                    payload = {f"{self.name}_output": True}

                return R()

        class Registry:
            def discover(self, capability_name):
                return RecordingAgent(capability_name)

        bus = BrocaCapabilityBus(registry=Registry(), base_context={"question": "q"})
        await bus.execute("stage_a", {})
        await bus.execute("stage_b", {})

        # stage_b's call should see stage_a's output merged into context
        _, stage_b_context = calls[1]
        assert stage_b_context.get("stage_a_output") is True

    asyncio.run(scenario())


def test_full_sdlc_happy_path_via_stub_registry():
    async def scenario():
        class StubAgent:
            def __init__(self, name):
                self.name = name

            async def handle(self, context):
                class R:
                    success = True
                    payload = {f"{self.name}_done": True}

                return R()

        class StubRegistry:
            def discover(self, capability_name):
                return StubAgent(capability_name)

        pm = ProcessManager(max_repair_attempts=1)
        rt = CodeGenRuntime()
        rt.process_manager = pm

        from src.monkey_brain.kernel.plan.goals.intent_ir import build_intent_ir
        from src.monkey_brain.kernel.execute.context import ExecutionContext
        from src.monkey_brain.kernel.execute.models import ExecutionMode

        run_id = "test-sdlc-happy"
        question = "build a work order tracking service"
        ir = build_intent_ir(
            intent={"intent": "build_service", "confidence": 0.9},
            goal=type(
                "G",
                (),
                {
                    "to_dict": lambda self: {
                        "name": "build_service",
                        "goal_type": "create",
                    },
                    "entities": (),
                },
            )(),
            run_id=run_id,
            question=question,
        )
        context = ExecutionContext.create(
            run_id=run_id,
            execution_mode=ExecutionMode.EXECUTE,
            intent_ir=ir,
            user_id="u1",
        )
        graph = rt.build_sdlc_graph(question)
        bus = BrocaCapabilityBus(registry=StubRegistry(), base_context={"question": question})

        await pm.create_process(context, graph=graph, target="codegen", capability_bus=bus)
        await pm.start_process(run_id)

        for _ in range(30):
            await pm.tick_all()
            if pm.get_process(run_id).state != RuntimeProcessState.RUNNING:
                break

        final = pm.get_process(run_id)
        assert final.state == RuntimeProcessState.COMPLETED
        summary = final.graph.get_execution_summary()
        assert summary["states"].get("complete") == summary["total_nodes"]

    asyncio.run(scenario())


if __name__ == "__main__":
    tests = [
        test_sdlc_graph_implementation_fans_out_into_ddd_and_compliance,
        test_sdlc_graph_is_linear_before_implementation_and_after_testing,
        test_compliance_domains_configurable,
        test_broca_capability_bus_successful_dispatch,
        test_broca_capability_bus_unknown_capability_is_graceful,
        test_broca_capability_bus_agent_exception_is_graceful,
        test_broca_capability_bus_accumulates_context_across_stages,
        test_full_sdlc_happy_path_via_stub_registry,
    ]
    ok = 0
    failures = []
    for fn in tests:
        try:
            fn()
            ok += 1
        except Exception as e:
            failures.append(f"{fn.__name__}: {e!r}")
    print(f"CodeGenRuntime: {ok}/{len(tests)} passed")
    for f in failures:
        print(f"  FAIL: {f}")
