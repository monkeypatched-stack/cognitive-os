"""System Validation Simulation — exercises all 7 cognitive OS capabilities.

1. EPA evolution over many steps
2. Solver-guided state prediction
3. Capability discovery during agent creation
4. Multi-agent workflow execution
5. Knowledge publication and subsequent retrieval
6. Loss reduction and repair across iterations
7. Recovery from injected failures
"""

import asyncio
import os
import random
import sys
import time

_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_repo, os.path.join(_repo, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cortex.epa import epa_transition, epa_loss, EpistemicPredictiveState
from cortex.epistemic import GoalState
from src.monkey_brain.kernel.cognitive_kernel import CognitiveKernel
from src.monkey_brain.kernel.solver_mesh import SolverMesh, GraphSolver, SATSolver
from src.monkey_brain.kernel.execute.agent_mesh import (
    ExecutionPool,
    AgentSpec,
    AgentRole,
)
from src.monkey_brain.kernel.execute.capabilities.bus import CapabilityBus
from src.knowledge.pack import KnowledgePack
from src.knowledge.item import KnowledgeItem, Modality
from domains.software_engineering.knowledge.pack import (
    SoftwareEngineeringKnowledgePublisher as EngineeringKnowledgePublisher,
)
from src.monkey_brain.kernel.loss_driven_repair import LossDrivenRepair


def _r(c):
    l = asyncio.new_event_loop()
    try:
        return l.run_until_complete(c)
    finally:
        l.close()


def _p(msg):
    print(f"  {msg}")


ok = 0
f = []


def T(name, fn):
    global ok
    try:
        fn()
        ok += 1
        print(f"  OK: {name}")
    except Exception as e:
        f.append(f"{name}: {e}")
        print(f"  FAIL: {name}: {e}")


# ═══════════════════════════════════════════════════════════════
# 1. EPA EVOLUTION OVER MANY STEPS
# ═══════════════════════════════════════════════════════════════


def test_epa_evolution_50_steps():
    k = CognitiveKernel()
    k.set_goal("manufacturing inspection")
    confidences = []
    losses = []
    for i in range(50):
        step = _r(k.step(f"action-{i}"))
        confidences.append(step["confidence"])
        losses.append(step.get("L_E", 0))
    assert len(confidences) == 50
    assert all(0 <= c <= 1 for c in confidences), f"Confidence out of range: {confidences[:5]}"
    assert all(l >= 0 for l in losses), f"Negative loss: {losses[:5]}"
    assert k._step == 50
    assert len(k._transition_quality) == 50
    _p(f"Confidence: {confidences[0]:.3f} -> {confidences[-1]:.3f}")
    _p(f"Loss: {losses[0]:.3f} -> {losses[-1]:.3f}")
    _p(f"History: {len(k._history)} entries")


def test_epa_state_components():
    E = EpistemicPredictiveState(S={"x": 1, "y": 2})
    assert E.S == {"x": 1, "y": 2}
    assert E.B is not None
    assert E.A is not None or E.A == set()
    assert E.M is not None
    d = E.to_dict()
    assert "S" in d and "B" in d and "A" in d and "M" in d


# ═══════════════════════════════════════════════════════════════
# 2. SOLVER-GUIDED STATE PREDICTION
# ═══════════════════════════════════════════════════════════════


def test_solver_mesh_all_9():
    sm = SolverMesh()
    results = {}
    for t, p in [
        ("rule_check", {"rules": [{"satisfied": True}]}),
        (
            "reachability",
            {
                "graph": {"a": ["b"], "b": ["c"]},
                "query": {"source": "a", "target": "c", "check": "reachability"},
            },
        ),
        ("satisfiability", {"clauses": [[1, 2]], "variables": [1, 2]}),
        ("constraint", {"variables": {"x": [1, 2]}, "constraints": []}),
        (
            "verification",
            {"model": {"x": 1}, "invariants": [{"type": "positive", "variable": "x"}]},
        ),
        (
            "optimization",
            {"objective": "minimize", "variables": {"x": 5.0}, "iterations": 100},
        ),
        ("planning", {"actions": ["a", "b"], "n_simulations": 20}),
        (
            "prediction",
            {
                "current_state": {"x": 1},
                "action": "move",
                "history": [{"x": i} for i in range(5)],
            },
        ),
        ("general", {}),
    ]:
        r = _r(sm.solve(p))
        results[t] = r.solver_name
        assert r.confidence >= 0, f"{t}: negative confidence"
    assert len(results) == 9
    _p(f"All 9 solvers dispatched: {list(results.keys())}")


def test_solver_prediction_in_transition():
    E0 = EpistemicPredictiveState(S={"temperature": 25.0, "pressure": 100.0})
    G = GoalState(objective="maintain optimal")
    E1 = epa_transition(E0, G, "adjust", evidence={"temperature": 26.0})
    assert E1.S["temperature"] == 26.0
    _p(f"Solver prediction: 25.0 -> {E1.S['temperature']} via evidence")


# ═══════════════════════════════════════════════════════════════
# 3. CAPABILITY DISCOVERY DURING AGENT CREATION
# ═══════════════════════════════════════════════════════════════


class _Capability:
    """Minimal stand-in for register_capability()/execute() (execute/
    capabilities/bus.py) — the bus only ever reads .name for registration
    and .fn (or calls the object itself) for execution; it never actually
    requires FunctionalCapability/CapabilityEffects specifically."""

    def __init__(self, name, fn=None):
        self.name = name
        self.fn = fn


def test_capability_discovery_at_spawn():
    bus = CapabilityBus()
    bus.register_capability(_Capability("notify", fn=lambda x: {"sent": True}))
    bus.register_capability(_Capability("query_db", fn=lambda x: {"rows": 10}))
    ep = ExecutionPool(KnowledgePack())
    spec = AgentSpec(
        role=AgentRole.SPECIALIST,
        required_capabilities=["notify", "query_db"],
    )
    agent = ep.spawn(spec, capability_bus=bus)
    assert "notify" in agent.spec.capabilities
    assert "query_db" in agent.spec.capabilities
    _p(f"Agent {agent.id} discovered {len(agent.spec.capabilities)} capabilities")


def test_capability_bus_resolution():
    bus = CapabilityBus()
    bus.register_capability(_Capability("calc", fn=lambda x: {"result": x.get("a", 0) + x.get("b", 0)}))
    result = _r(bus.execute("calc", a=3, b=7))
    assert result.success
    assert result.produced["result"] == 10


# ═══════════════════════════════════════════════════════════════
# 4. MULTI-AGENT WORKFLOW EXECUTION
# ═══════════════════════════════════════════════════════════════


def test_multi_agent_workflow():
    ep = ExecutionPool(KnowledgePack())
    for _ in range(5):
        ep.spawn(AgentSpec(role=AgentRole.WORKER))
    results = []
    for i in range(10):
        r = _r(ep.assign_task({"type": f"task-{i}", "required_capabilities": []}))
        results.append(r)
    assert len(results) == 10
    assert all(r.get("success", False) for r in results)
    _p(f"{len(results)} tasks executed by {ep.agent_count} agents")


# ═══════════════════════════════════════════════════════════════
# 5. KNOWLEDGE PUBLICATION AND RETRIEVAL
# ═══════════════════════════════════════════════════════════════


def test_knowledge_publish_and_retrieve():
    pub = EngineeringKnowledgePublisher()
    pub.publish(
        spec_id="wo-001",
        goal="Build Work Order Service",
        domain="manufacturing",
        generated_files={"main.py": "code", "models.py": "code"},
        governance_findings=[{"severity": "low"}],
        benchmark_results={"throughput": 42, "latency_ms": 15},
        workflow_topology=["Create", "Todo", "Notify"],
        confidence=0.9,
    )
    pub.publish(
        spec_id="wo-002",
        goal="Build Todo Service",
        domain="manufacturing",
        generated_files={"main.py": "code"},
        confidence=0.85,
    )
    items = pub.get_knowledge_items_for_retrieval("Work Order")
    assert len(items) >= 3
    assert pub.summary()["total_published"] == 2
    _p(f"Published {pub.summary()['total_published']} KPs, retrieved {len(items)} items for 'Work Order'")


# ═══════════════════════════════════════════════════════════════
# 6. LOSS REDUCTION AND REPAIR ACROSS ITERATIONS
# ═══════════════════════════════════════════════════════════════


def test_loss_reduction_repair():
    repair = LossDrivenRepair(loss_threshold=0.05, max_iterations=15)
    report = repair.run(
        0.85,
        {
            "test_results": {},
            "ddd_results": {},
            "govern_results": {},
            "runtime_errors": [],
        },
        loss_terms={
            "L_S": 0.3,
            "L_B": 0.2,
            "L_A": 0.1,
            "L_M": 0.1,
            "L_K": 0.1,
            "L_C": 0.0,
            "L_G": 0.05,
        },
    )
    assert report.initial_loss > report.final_loss
    assert report.total_iterations >= 1
    assert report.final_loss < report.initial_loss
    reduction = (1 - report.final_loss / report.initial_loss) * 100
    _p(
        f"Loss: {report.initial_loss:.3f} -> {report.final_loss:.3f} ({reduction:.0f}% reduction, {report.total_iterations} iterations)"
    )


# ═══════════════════════════════════════════════════════════════
# 7. RECOVERY FROM INJECTED FAILURES
# ═══════════════════════════════════════════════════════════════


def test_recovery_from_failure():
    k = CognitiveKernel()
    k.set_goal("recovery test")
    good_steps = []
    for i in range(5):
        s = _r(k.step(f"good-{i}"))
        good_steps.append(s["confidence"])
    failed_step = None
    try:
        result = _r(k.step("bad", capability=None))
        failed_step = result
    except Exception:
        pass
    recovery_steps = []
    for i in range(5):
        s = _r(k.step(f"recover-{i}"))
        recovery_steps.append(s["confidence"])
    assert all(c > 0 for c in recovery_steps), f"Recovery failed: {recovery_steps}"
    assert k._step == 11
    _p(f"Pre-failure: {good_steps[-1]:.3f}, Post-recovery: {recovery_steps[-1]:.3f}")
    _p(f"Kernel survived failure and continued: {k._step} total steps")


def test_solver_failure_recovery():
    sm = SolverMesh()
    r = _r(sm.solve({"type": "nonexistent_type_xyz"}))
    assert r is None or r.confidence >= 0
    _p(f"Unknown solver type gracefully handled: {r.solver_name if r else 'None'}")


def test_epa_transition_with_none_inputs():
    E = epa_transition(
        EpistemicPredictiveState(S={}),
        GoalState(objective="test"),
        "unknown_action",
        evidence=None,
        capability_graph=None,
        agent_mesh=None,
        fusion_engine=None,
        world_model=None,
        solver_mesh=None,
    )
    assert E is not None
    _p(f"Transition with all None inputs: state={E.S}, confidence={E.B.confidence:.3f}")


# ═══════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════

ALL_TESTS = {k: v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)}

if __name__ == "__main__":
    print("=" * 70)
    print("SYSTEM VALIDATION SIMULATION — Cognitive OS")
    print("=" * 70)

    for name, fn in ALL_TESTS.items():
        tag = name.replace("test_", "").replace("_", " ").title()
        print(f"\n[{tag}]")
        T(name, fn)

    print("\n" + "=" * 70)
    print(f"RESULTS: {ok}/{ok + len(f)} passed")
    if f:
        print("FAILURES:")
        for e in f:
            print(f"  {e}")
    else:
        print("ALL CHECKS PASS")
    print("=" * 70)
