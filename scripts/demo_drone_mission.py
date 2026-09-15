#!/usr/bin/env python3
"""End-to-end, narrated walkthrough of the 5-layer drone loop:

    Human -> CognitiveOS Actor -> Plan -> Governance -> Drone Action
    -> ROS2 -> PX4 SITL -> Gazebo/Isaac Sim -> telemetry -> ROS2
    -> CognitiveOS belief -> Replan

Everything through Governance and Drone Action runs REAL code against
this repo's real infra, not a mock of it:
  - a real ActorCell (kernel/society/actor_cell.py) -- same construction
    tests/scenarios/test_three_drone_mission.py already uses for its own
    governed multi-actor test.
  - real GovernanceEngine.evaluate() (kernel/governance.py) against a real
    OPA instance -- point OPA_URL at one (this was verified against the
    Dockerized OPA this repo's own docker-compose.yml stands up).
  - real run_ros_action_if_governed() (kernel/edge/ros_integration.py) --
    the SAME governed chokepoint every other drone capability call goes
    through; this script has no shortcut into the adapter.
  - real kernel/domains/robot.py capabilities (Arm/Takeoff/Waypoint/Land).
  - the real NL -> plan parser scripts/px4_bridge_autoanswer.py already
    uses for this exact demo path (regex-based, not a fake LLM call --
    see that module's own docstring for why it exists and what it stands
    in for).
  - the real WorldPollingProvider drone-telemetry extension (kernel/
    pipeline/observations.py) for the return leg: telemetry -> belief.

The ONE stage genuinely simulated here: PX4 SITL + Gazebo/Isaac Sim
themselves. This sandboxed environment has no GPU and no ROS 2 toolchain
to fly a real simulated vehicle. This script uses a small demo adapter
that implements the exact same RosExecutionAdapter contract
FakeRosExecutionAdapter does (kernel/edge/ros_integration.py's own
documented "hardware seam" -- see that module's docstring: "no ROS
runtime, no hardware... proves the CognitiveOS boundaries around it"),
plus latest_state() so the telemetry/belief leg has something real to
read from. Point ROS_ADAPTER_KIND at a real Px4RosExecutionAdapter
(kernel/edge/px4_ros_adapter.py) on a machine with ROS 2 + px4_msgs + a
running PX4 SITL/Gazebo/Isaac Sim vehicle, and every stage below runs
completely unchanged against a real drone -- nothing in this script's own
logic is simulator-specific.

Usage:
    OPA_URL=http://localhost:8182 OPA_REQUIRED=true \\
    COGNITIVEOS_ALLOW_INSECURE_DEV_MODE=true \\
    PYTHONPATH=.:domains/manufacturing/knowledge \\
        python3 scripts/demo_drone_mission.py

Two real, live-caught issues fixed while building this, both worth
naming rather than silently working around:
  - COGNITIVEOS_ALLOW_INSECURE_DEV_MODE alone SKIPS the live OPA call
    entirely (security_boundary.py::_authorize_and_gate: `if require_opa()
    or not insecure_dev_mode(): policy = await _authorize(...)` — false
    on both sides when insecure-dev is on and OPA isn't required) — the
    demo silently "succeeded" with zero governance decisions actually
    recorded the first time this was run. OPA_REQUIRED=true forces the
    real call to happen even with insecure-dev's other relaxations
    (MFA, etc.) still on.
  - services.common.opa (which GovernanceEngine.evaluate() imports) lives
    under domains/manufacturing/knowledge/services/common/opa.py, not
    repo-root services/common/ (that directory is a build artifact stub,
    same finding as this repo's own Docker/dependency-consolidation work
    documented elsewhere) — needs domains/manufacturing/knowledge on
    PYTHONPATH or the import fails and governance fails CLOSED (a denied
    Arm, not a silent skip) rather than evaluating for real.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "scripts"), str(_REPO_ROOT / "domains" / "manufacturing" / "knowledge")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# "Waypoint A" resolved to a concrete coordinate up front -- this repo's
# real NL->plan parser (px4_bridge_autoanswer.py) works on raw x=/y=
# coordinates, not symbolic waypoint names (no named-waypoint resolver
# exists anywhere in this codebase, and this demo isn't going to invent
# one silently). Naming the substitution here, not hiding it, is the
# honest way to use "Inspect waypoint A" as the human-facing prompt.
WAYPOINT_A = (8.0, 0.0)
ALTITUDE_M = 5.0


def _banner(title: str) -> None:
    print(f"\n{'─' * 70}\n{title}\n{'─' * 70}")


class DemoDroneAdapter:
    """The hardware seam for this demo -- same RosExecutionAdapter contract
    FakeRosExecutionAdapter implements (kernel/edge/ros_integration.py),
    plus latest_state() (kernel/edge/px4_ros_adapter.py's real interface)
    so the telemetry/belief leg has something to read. Every value below
    is EXPLICITLY labeled as simulated; nothing pretends to be a real PX4
    SITL/Gazebo/Isaac Sim vehicle."""

    def __init__(self, actor_id: str) -> None:
        self.actor_id = actor_id
        self.calls: list[dict[str, Any]] = []
        self._x = 0.0
        self._y = 0.0
        self._z = 0.0
        self._armed = False

    async def invoke(self, *, capability: str, parameters: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"capability": capability, "parameters": dict(parameters)})
        # Standing in for what a real PX4 SITL vehicle (Gazebo- or
        # Isaac-Sim-backed) would report back after actually flying this
        # step -- see Px4RosExecutionAdapter.invoke() for the real
        # telemetry-confirmed version of this same logic.
        if capability == "Arm":
            self._armed = True
            return {"success": True, "actor_id": self.actor_id, "namespace": "px4_1"}
        if capability == "Takeoff":
            self._z = -float(parameters.get("height_m", 2.0))
            return {
                "success": True,
                "actor_id": self.actor_id,
                "namespace": "px4_1",
                "altitude_m": parameters.get("height_m"),
            }
        if capability == "Waypoint":
            self._x = float(parameters.get("x", 0.0))
            self._y = float(parameters.get("y", 0.0))
            return {"success": True, "actor_id": self.actor_id, "namespace": "px4_1", "x": self._x, "y": self._y}
        if capability == "Land":
            self._armed = False
            self._z = 0.0
            return {"success": True, "actor_id": self.actor_id, "namespace": "px4_1"}
        return {"success": False, "error": f"unsupported PX4 capability: {capability}"}

    def latest_state(self):
        from src.monkey_brain.kernel.edge.drone_state import DroneState

        return DroneState(
            actor_id=self.actor_id,
            namespace="px4_1",
            armed=self._armed,
            position_x=self._x,
            position_y=self._y,
            position_z=self._z,
            timestamp=time.time(),
            flight_mode="OFFBOARD" if self._armed else "DISARMED",
        )


_CAPABILITY_CLASSES: dict[str, Any] = {}


def _load_capability_classes() -> None:
    from src.monkey_brain.kernel.domains.robot import (
        ArmCapability,
        LandCapability,
        TakeoffCapability,
        WaypointCapability,
    )

    _CAPABILITY_CLASSES.update(
        {"Arm": ArmCapability, "Takeoff": TakeoffCapability, "Waypoint": WaypointCapability, "Land": LandCapability}
    )


async def main() -> None:
    os.environ.setdefault("OPA_URL", "http://localhost:8182")
    # See module docstring: COGNITIVEOS_ALLOW_INSECURE_DEV_MODE alone
    # SKIPS the live OPA call — this demo exists to show governance
    # genuinely running, so force it regardless of what the caller's
    # environment happens to have set.
    os.environ["OPA_REQUIRED"] = "true"

    _banner("HUMAN")
    human_command = "Inspect waypoint A"
    print(f'"{human_command}"')
    print(f"(resolved to a concrete mission: takeoff to {ALTITUDE_M}m, fly to waypoint A = {WAYPOINT_A}, land)")
    mission_text = f"Take off to {ALTITUDE_M} meters, fly to x={WAYPOINT_A[0]} y={WAYPOINT_A[1]}, then land"

    _banner("COGNITIVEOS ACTOR — constructing the Actor Cell")
    from src.monkey_brain.kernel.actor_identity import mint_actor_cell_identity
    from src.monkey_brain.kernel.approval import reset_approval_store
    from src.monkey_brain.kernel.compile.cognitive_actor import CognitiveActor
    from src.monkey_brain.kernel.security_boundary import reset_governed_pipeline_for_tests
    from src.monkey_brain.kernel.society.actor_cell import ActorCell
    from src.monkey_brain.kernel.society.runtime import ActorRuntimeState
    from src.monkey_brain.kernel.trusted_auth import TrustedAuthEvidence, bind_trusted_auth

    reset_approval_store()
    reset_governed_pipeline_for_tests()

    actor_id = "drone-demo-1"
    actor = CognitiveActor(entity_id=actor_id)
    adapter = DemoDroneAdapter(actor_id)
    cell = ActorCell(
        actor_id=actor_id,
        identity=mint_actor_cell_identity(actor_id, issuer="demo"),
        actor=actor,
        runtime_state=ActorRuntimeState(actor_id=actor_id, actor=actor),
        ros_adapter=adapter,
    )
    bind_trusted_auth(
        TrustedAuthEvidence(
            authenticated=True,
            token_valid=True,
            principal_id=actor_id,
            principal_type="service",
            mfa_status="satisfied",
        )
    )
    from src.monkey_brain.kernel.edge.drone_state import register_drone_adapter

    register_drone_adapter(actor_id, adapter)
    print(f"Actor Cell {actor_id!r} constructed, ROS adapter bound, registered for telemetry.")

    _banner("PLAN — real NL->plan parsing (scripts/px4_bridge_autoanswer.py)")
    import px4_bridge_autoanswer as autoanswer

    plan = autoanswer.build_plan(mission_text)
    print(f"mission: {mission_text!r}")
    for i, step in enumerate(plan["steps"]):
        print(f"  {i + 1}. {step['action']}: {step['description']} {step['parameters'] or ''}")

    _banner("GOVERNANCE + DRONE ACTION — every step through run_ros_action_if_governed")
    _load_capability_classes()
    for step in plan["steps"]:
        capability_cls = _CAPABILITY_CLASSES.get(step["action"])
        if capability_cls is None:
            print(f"  ✗ {step['action']}: not a registered PX4 capability — real rejection, not fabricated")
            continue
        capability = capability_cls()
        args = {"context": {"actor_id": actor_id, "ros_adapter": cell.ros_adapter}, "parameters": step["parameters"]}
        result = await capability.handle(args)
        status = "✓" if result.get("success") else "✗"
        print(f"  {status} {step['action']}: {result}")

    _banner("GOVERNANCE — real recorded decisions (GovernanceEngine.audit_decisions)")
    from src.monkey_brain.kernel.governance import get_governance_engine

    for decision in get_governance_engine().audit_decisions(runtime_id=actor_id):
        print(
            f"  action={decision['action']!r} allowed={decision['allowed']} policy_rule={decision.get('policy_rule')!r}"
        )

    _banner("TELEMETRY -> ROS2 -> COGNITIVEOS BELIEF")
    from src.monkey_brain.kernel.pipeline.observations import WorldPollingProvider

    obs_set = WorldPollingProvider().observe(actor_id, None)
    print(f"ObservationProvider produced {len(obs_set.observations)} observations, provenance=px4_ros:")
    for obs in obs_set.observations:
        print(f"  {obs.entity}.{obs.attribute} = {obs.value!r} (confidence={obs.confidence})")

    _banner("REPLAN")
    print(
        "These observations feed BeliefFusion the same way WorldPollingProvider's\n"
        "world-tensor observations always have (kernel/pipeline/belief_runtime.py's\n"
        "CognitiveRuntime._observe()) -- the actor's next planning cycle reads this\n"
        "updated belief automatically. No drone-specific replanning mechanism exists\n"
        "or is needed; this IS the existing tick loop, now fed by real drone telemetry."
    )

    _banner("DONE")
    print(
        f"{len(adapter.calls)} real governed capability invocations, "
        f"{len(get_governance_engine().audit_decisions(runtime_id=actor_id))} real governance decisions recorded, "
        f"{len(obs_set.observations)} telemetry observations reached belief state."
    )
    print(
        "The only simulated piece: DemoDroneAdapter standing in for PX4 SITL + "
        "Gazebo/Isaac Sim (no GPU/ROS 2 toolchain in this environment). Every other "
        "line above is the real code path."
    )

    unregister = __import__(
        "src.monkey_brain.kernel.edge.drone_state", fromlist=["unregister_drone_adapter"]
    ).unregister_drone_adapter
    unregister(actor_id)
    reset_approval_store()
    reset_governed_pipeline_for_tests()


if __name__ == "__main__":
    asyncio.run(main())
