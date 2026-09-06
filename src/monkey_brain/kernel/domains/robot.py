"""Robot domain capability.

Closes the swarm-readiness audit's finding (docs/ACTOR_CELL_ARCHITECTURE.md
/ this session's audit) that kernel/edge/ros_integration.py's governed ROS
path had zero production callers: no live capability had ever actually
invoked run_ros_action_if_governed, so "every physical action is governed"
was true only vacuously (there was no physical action to govern).

Deliberately a no-op liveness check, not a real motion capability. The
audit was explicit that inventing a fake capability such as MoveRobot to
satisfy the architecture is out of scope -- HeartbeatCapability proves the
full path (ActionExecutor -> ensure_governed -> capability.handle() ->
run_ros_action_if_governed -> ensure_governed(force_authorize=True) ->
actor-bound RosExecutionAdapter) actually works end-to-end without
fabricating a robot task that doesn't exist.

Registered on the same GroceryCapabilityBus every other real capability
uses (see kernel/domains/grocery.py::build_default_capability_bus) --
per ros_integration.py's own module docstring, that IS "the SAME
CapabilityBus every other capability uses," not a separate robot-only
dispatch path.
"""
from __future__ import annotations

from typing import Any


class HeartbeatCapability:
    """A no-op liveness ping against this actor's own bound ROS adapter.

    context["ros_adapter"] is this actor's own RosExecutionAdapter (set by
    ActorCell.ros_adapter -- see kernel/society/actor_cell.py -- and
    injected into context by the same context_factory that already
    injects context["actor_local_knowledge_graph"]). None for any actor
    that isn't a robot deployment (node_class != "robot"), which is the
    normal, honest case for every non-robot actor -- this capability
    degrades to a clear error, never a crash or a silent no-op success.
    """

    name = "Heartbeat"

    async def handle(self, args: dict) -> dict[str, Any]:
        context = args.get("context", {}) or {}
        actor_id = context.get("actor_id", "")
        adapter = context.get("ros_adapter")
        if adapter is None:
            return {"success": False, "error": "no ROS adapter bound to this actor"}

        from src.monkey_brain.kernel.edge.ros_integration import run_ros_action_if_governed

        return await run_ros_action_if_governed(
            capability="Heartbeat",
            resource=f"ros:{actor_id}" if actor_id else "ros",
            parameters={},
            adapter=adapter,
            actor_id=actor_id,
        )
