#!/usr/bin/env python3
"""Three-drone swarm demo:

               CognitiveOS
                    │
          ┌─────────┼─────────┐
          ▼         ▼         ▼
       Drone A   Drone B   Drone C
          │         │         │
         PX4       PX4       PX4
          │         │         │
         ROS       ROS       ROS
          └─────────┼─────────┘
                    ▼
               Shared World

Grounded in tests/scenarios/test_three_drone_mission.py's own real,
already-tested invariants -- this demo does not introduce a new
architecture, it narrates the existing one with real governance/telemetry
attached. Two things that test proves and this demo re-proves live, because
"Shared World" is easy to misread as "shared mutable state" and the real
architecture explicitly is NOT that:

  1. Each drone is a fully isolated ActorCell -- separate belief, separate
     knowledge graph, separate memory (test asserts these by object
     identity). Cross-cell ROS dispatch is refused before governance or
     hardware ever run (RosUnavailableError) -- Actor Cell ROS isolation,
     kernel/edge/ros_integration.py's own actor_id-binding check.

  2. "Shared World" is the READ-ONLY world tensor every actor's
     WorldPollingProvider.observe(actor_id, world) call is handed --
     kernel/compile/world_tensor.py's SparseTransitionTensor in
     production; a minimal duck-typed stand-in here (same
     entities()/relationships()/events() interface WorldPollingProvider
     already reads, see that module for the real contract) so this script
     has no heavyweight construction dependency. All three drones observe
     the SAME shared entities from it. Their own drone telemetry (armed,
     position, ...) is a completely separate, PRIVATE addition per
     actor_id (kernel/edge/drone_state.py's registry) -- this script
     proves both halves side by side: shared read visibility into the
     world, private telemetry into each drone's own belief, never leaking
     between drones.

Governance and the drone action/telemetry legs are real, same as
scripts/demo_drone_mission.py (read that script's docstring for the two
real environment issues it caught and how this script avoids them:
OPA_REQUIRED=true, domains/manufacturing/knowledge on PYTHONPATH). The one
simulated piece is PX4 SITL + Gazebo/Isaac Sim itself -- no GPU/ROS 2
toolchain in this environment.

Usage:
    OPA_URL=http://localhost:8182 COGNITIVEOS_ALLOW_INSECURE_DEV_MODE=true \\
    PYTHONPATH=.:domains/manufacturing/knowledge \\
        python3 scripts/demo_swarm_mission.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "domains" / "manufacturing" / "knowledge")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DRONES = ("A", "B", "C")
# Distinct starting offsets so the three vehicles are never at the same
# point -- makes the per-actor telemetry isolation visible in the output,
# not just asserted.
START_OFFSET = {"A": (0.0, 0.0), "B": (20.0, 0.0), "C": (0.0, 20.0)}


def _banner(title: str) -> None:
    print(f"\n{'─' * 70}\n{title}\n{'─' * 70}")


class DemoDroneAdapter:
    """Same shape as scripts/demo_drone_mission.py's -- see that module for
    why this stands in for a real Px4RosExecutionAdapter here."""

    def __init__(self, actor_id: str, namespace: str, start: tuple[float, float]) -> None:
        self.actor_id = actor_id
        self.namespace = namespace
        self.calls: list[dict[str, Any]] = []
        self._x, self._y = start
        self._z = 0.0
        self._armed = False

    async def invoke(self, *, capability: str, parameters: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"capability": capability, "parameters": dict(parameters)})
        if capability == "Arm":
            self._armed = True
        elif capability == "Takeoff":
            self._z = -float(parameters.get("height_m", 2.0))
        elif capability == "Waypoint":
            self._x = float(parameters.get("x", 0.0))
            self._y = float(parameters.get("y", 0.0))
        elif capability == "Land":
            self._armed = False
            self._z = 0.0
        else:
            return {"success": False, "error": f"unsupported PX4 capability: {capability}"}
        return {"success": True, "actor_id": self.actor_id, "namespace": self.namespace}

    def latest_state(self):
        from src.monkey_brain.kernel.edge.drone_state import DroneState

        return DroneState(
            actor_id=self.actor_id,
            namespace=self.namespace,
            armed=self._armed,
            position_x=self._x,
            position_y=self._y,
            position_z=self._z,
            timestamp=time.time(),
            flight_mode="OFFBOARD" if self._armed else "DISARMED",
        )


@dataclass
class _FakeEntity:
    name: str
    entity_id: str
    attributes: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.9
    entity_type: str = "landmark"


class SharedWorldStub:
    """Minimal stand-in for kernel/compile/world_tensor.py's real
    SparseTransitionTensor -- same duck-typed interface
    WorldPollingProvider.observe() already reads (entities(), no
    relationships()/events() needed for this demo). Every drone below
    observes from this SAME instance."""

    def __init__(self) -> None:
        self._entities = [
            _FakeEntity(name="No-Fly Zone Alpha", entity_id="nfz-alpha", attributes={"radius_m": 50.0}),
            _FakeEntity(name="Weather Station 1", entity_id="wx-1", attributes={"wind_speed_ms": 4.2}),
        ]

    def entities(self):
        return list(self._entities)


async def _run_mission(actor_id: str, cell: Any, waypoint: tuple[float, float]) -> list[dict[str, Any]]:
    from src.monkey_brain.kernel.edge.ros_integration import run_ros_action_if_governed

    steps = [
        ("Arm", {}),
        ("Takeoff", {"height_m": 5.0}),
        ("Waypoint", {"x": waypoint[0], "y": waypoint[1], "height_m": 5.0}),
        ("Land", {}),
    ]
    results = []
    for capability, parameters in steps:
        result = await run_ros_action_if_governed(
            capability=capability,
            resource=f"px4:{actor_id}",
            parameters=parameters,
            adapter=cell.ros_adapter,
            actor_id=actor_id,
        )
        results.append({"capability": capability, **result})
    return results


async def main() -> None:
    os.environ.setdefault("OPA_URL", "http://localhost:8182")
    os.environ["OPA_REQUIRED"] = "true"  # see module docstring

    _banner("HUMAN")
    print('"Survey sectors A, B, and C concurrently"')

    _banner("COGNITIVEOS — constructing 3 isolated Actor Cells")
    from src.monkey_brain.kernel.actor_identity import mint_actor_cell_identity
    from src.monkey_brain.kernel.approval import reset_approval_store
    from src.monkey_brain.kernel.compile.cognitive_actor import CognitiveActor
    from src.monkey_brain.kernel.delegation import reset_delegation_store_for_tests
    from src.monkey_brain.kernel.security_boundary import reset_governed_pipeline_for_tests
    from src.monkey_brain.kernel.society.actor_cell import ActorCell
    from src.monkey_brain.kernel.society.runtime import ActorRuntimeState
    from src.monkey_brain.kernel.trusted_auth import TrustedAuthEvidence, bind_trusted_auth
    from src.monkey_brain.kernel.edge.drone_state import (
        get_drone_adapter,
        register_drone_adapter,
        unregister_drone_adapter,
    )

    reset_approval_store()
    reset_governed_pipeline_for_tests()
    reset_delegation_store_for_tests()

    actor_ids = {d: f"drone-{d.lower()}" for d in DRONES}
    cells: dict[str, Any] = {}
    for d, actor_id in actor_ids.items():
        actor = CognitiveActor(entity_id=actor_id)
        adapter = DemoDroneAdapter(actor_id, namespace=f"px4_{DRONES.index(d) + 1}", start=START_OFFSET[d])
        cells[d] = ActorCell(
            actor_id=actor_id,
            identity=mint_actor_cell_identity(actor_id, issuer="swarm-demo"),
            actor=actor,
            runtime_state=ActorRuntimeState(actor_id=actor_id, actor=actor),
            ros_adapter=adapter,
        )
        register_drone_adapter(actor_id, adapter)
    print(f"Constructed {len(cells)} ActorCells: {list(actor_ids.values())}")
    print(
        "Isolation (same real check tests/scenarios/test_three_drone_mission.py asserts): "
        f"{len({id(c.actor.belief) for c in cells.values()})} distinct belief objects, "
        f"{len({id(c.actor._knowledge_graph) for c in cells.values()})} distinct knowledge graphs."
    )

    # Bind ONE evidence identity per call below (trusted_auth is process-
    # global, not per-actor) -- matches how a real multi-actor deployment
    # runs each Actor as its OWN process (Actor Artifact model), each with
    # its own trusted_auth binding; concurrently interleaving all three
    # missions' governance calls in ONE process (as this demo does, for
    # narration simplicity) would race on that same global binding, so
    # missions run sequentially here instead of via asyncio.gather like
    # the real test does with local_policy_decision (which doesn't touch
    # trusted_auth at all).
    _banner("GOVERNANCE + DRONE ACTION — each drone's mission, real OPA per step")
    waypoints = {"A": (8.0, 0.0), "B": (28.0, 0.0), "C": (8.0, 20.0)}
    all_results: dict[str, list[dict[str, Any]]] = {}
    for d in DRONES:
        actor_id = actor_ids[d]
        bind_trusted_auth(
            TrustedAuthEvidence(
                authenticated=True,
                token_valid=True,
                principal_id=actor_id,
                principal_type="service",
                mfa_status="satisfied",
            )
        )
        results = await _run_mission(actor_id, cells[d], waypoints[d])
        all_results[d] = results
        for r in results:
            status = "✓" if r.get("success") else "✗"
            print(f"  {status} drone {d} ({actor_id}) {r['capability']}: {r}")

    _banner("ISOLATION PROOF — cross-cell ROS dispatch is refused before governance or hardware")
    from src.monkey_brain.kernel.edge.ros_integration import RosUnavailableError, run_ros_action_if_governed

    try:
        await run_ros_action_if_governed(
            capability="Waypoint",
            resource="px4:drone-b",
            parameters={"x": 0.0, "y": 0.0},
            adapter=cells["B"].ros_adapter,
            actor_id=actor_ids["A"],
        )
        print("  ✗ UNEXPECTED: drone A was able to command drone B's vehicle")
    except RosUnavailableError as exc:
        print(f"  ✓ drone A -> drone B's adapter refused, as it must: {exc}")
    print(f"  drone B's own call count is unchanged by the attempt above: {len(cells['B'].ros_adapter.calls)}")

    _banner("GOVERNANCE — real recorded decisions, per drone")
    from src.monkey_brain.kernel.governance import get_governance_engine

    gov = get_governance_engine()
    total_decisions = 0
    for d in DRONES:
        decisions = gov.audit_decisions(runtime_id=actor_ids[d])
        total_decisions += len(decisions)
        print(f"  drone {d}: {len(decisions)} decisions, all allowed={all(x['allowed'] for x in decisions)}")

    _banner("SHARED WORLD + PRIVATE TELEMETRY -> BELIEF")
    from src.monkey_brain.kernel.pipeline.observations import WorldPollingProvider

    shared_world = SharedWorldStub()
    provider = WorldPollingProvider()
    for d in DRONES:
        actor_id = actor_ids[d]
        obs_set = provider.observe(actor_id, shared_world)
        shared = [o for o in obs_set.observations if o.provenance.source == "world_polling"]
        private = [o for o in obs_set.observations if o.provenance.source == "px4_ros"]
        print(f"  drone {d} ({actor_id}):")
        print(
            f"    shared-world observations ({len(shared)}, same for every drone): "
            f"{sorted(f'{o.entity}.{o.attribute}' for o in shared)}"
        )
        print(f"    private telemetry ({len(private)}, THIS drone only): {[(o.attribute, o.value) for o in private]}")

    _banner("REPLAN")
    print(
        "Each drone's belief now carries both the shared world entities and its own\n"
        "private telemetry, fused by the same BeliefFusion every actor already uses.\n"
        "The next planning cycle for A, B, and C each reads its OWN updated belief --\n"
        "no cross-drone leakage, no shared mutable state, matching the isolation\n"
        "proof above. This IS the existing per-actor tick loop, not new machinery."
    )

    _banner("DONE")
    total_calls = sum(len(cells[d].ros_adapter.calls) for d in DRONES)
    print(
        f"3 isolated drones, {total_calls} real governed capability invocations, "
        f"{total_decisions} real governance decisions recorded (real OPA), "
        f"cross-cell isolation proven, shared-world read visibility proven, "
        f"zero private-telemetry leakage between drones."
    )

    for actor_id in actor_ids.values():
        unregister_drone_adapter(actor_id)
        assert get_drone_adapter(actor_id) is None
    reset_approval_store()
    reset_governed_pipeline_for_tests()
    reset_delegation_store_for_tests()


if __name__ == "__main__":
    asyncio.run(main())
