"""ActorCell — the formal Actor Cell boundary object.

docs/ACTOR_CELL_ARCHITECTURE.md Section B found that "there is no Actor Cell
object today" — what exists instead is a set of already-correctly-actor-
scoped pieces (CognitiveActor's belief/KG/memory/context/cognitive runtime,
ActorRuntimeState) with no single object formalizing "this is everything one
actor owns exclusively." This module is that object.

Deliberately a thin composition, not a rewrite: every field below is either
an existing, already-actor-scoped object (CognitiveActor, ActorRuntimeState)
or one of the two new per-actor pieces this migration adds (the identity
credential from kernel/actor_identity.py, the actor-bound ROS adapter from
kernel/edge/ros_integration.py). No new storage engine, no new persistence,
no state moved off of cloud-owned infrastructure (Society/World/Presence/
Policy/Approval/Delegation/Plans/Catalog/Registry/Audit are untouched and
stay reachable exactly as they are today, through ObservationProvider/
LocalGovernanceEvaluator/ensure_governed).

Actor Cell completeness (the task's own checklist):

    Actor Cell
    +-- Per-Actor Identity          -> ActorCell.identity
    +-- CognitiveActor
    |   +-- Beliefs                 -> ActorCell.actor.belief / ._actor_belief
    |   +-- Local KG / Knowledge    -> ActorCell.actor._knowledge_graph
    |   +-- Memory                  -> MemoryManager, actor_id-scoped (kernel/learn/memory/manager.py)
    |   +-- Context                 -> ContextConstructionEngine.build(actor_id, ...), per-call
    |   +-- Cognitive Runtime       -> ActorCell.actor._cognitive_engine
    +-- Actor Runtime State         -> ActorCell.runtime_state
    +-- Local Execution State       -> transient, inside ActionExecutor.execute()'s call scope
    +-- Actor-specific Capability State -> stateless shared capability instances (verified, ACTOR_CELL_ARCHITECTURE.md Section B)
    +-- ROS Adapter Binding         -> ActorCell.ros_adapter
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.monkey_brain.kernel.delegation import DelegationCredential
from src.monkey_brain.kernel.edge.ros_integration import RosExecutionAdapter


@dataclass
class ActorCell:
    """Everything one actor owns exclusively, composed from existing,
    already-actor-scoped objects plus this migration's two additions
    (identity, bound ROS adapter). Constructed once, alongside the
    CognitiveActor it wraps (kernel/society/runtime.py's
    `_get_or_create_actor`-equivalent construction site)."""

    actor_id: str
    identity: DelegationCredential
    """This Cell's own DelegationCredential (kernel/actor_identity.py) --
    delegate == actor_id, cryptographically bound, never shared with any
    other Cell."""
    actor: Any
    """The CognitiveActor instance -- belief, per-actor KnowledgeGraph
    (._knowledge_graph), goals, cognitive runtime. Typed Any to avoid a
    circular import with kernel/compile/cognitive_actor.py (mirrors
    ActorRuntimeState.actor's own typing one file over)."""
    runtime_state: Any
    """The ActorRuntimeState this Cell wraps (kernel/society/runtime.py).
    Typed Any for the same reason as `actor`."""
    ros_adapter: RosExecutionAdapter | None = None
    """Actor-bound ROS execution adapter (kernel/edge/ros_integration.py),
    when this actor has one -- None for non-robot actors. Constructed via
    build_ros_execution_adapter(actor_id=self.actor_id, ...) so its
    `.actor_id` always matches this Cell's own."""

    def __post_init__(self) -> None:
        if self.identity.delegate != self.actor_id:
            raise ValueError(
                f"ActorCell identity mismatch: credential.delegate={self.identity.delegate!r} "
                f"!= actor_id={self.actor_id!r} -- an ActorCell must never hold another "
                "actor's identity credential",
            )
        bound_ros_actor_id = getattr(self.ros_adapter, "actor_id", "") or ""
        if self.ros_adapter is not None and bound_ros_actor_id and bound_ros_actor_id != self.actor_id:
            raise ValueError(
                f"ActorCell ROS binding mismatch: adapter.actor_id={bound_ros_actor_id!r} "
                f"!= actor_id={self.actor_id!r} -- an ActorCell must never hold another "
                "actor's ROS adapter",
            )
