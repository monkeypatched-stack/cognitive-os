"""Learn phase — record graph transitions into topology-aware learning."""

from __future__ import annotations

import logging
from typing import Any

from src.monkey_brain.kernel.models.graph import canonical_graph_envelope

logger = logging.getLogger(__name__)


def record_outcome(
    goal: Any,
    workload: Any,
    result: Any,
    exec_ms: float = 0.0,
    run_id: str = "",
    lemon: Any = None,
    graph: Any = None,
) -> None:
    """Update graph-topology learning from the completed execution.

    Epistemic loss stays elsewhere. This only turns execution graphs into a
    graph-level transition so Bellman can learn execution strategy quality.
    """
    goal_name = getattr(goal, "name", "")
    workload_id = getattr(workload, "workload_id", "")
    llm_answered = getattr(result, "llm_answered", False)
    reward = 1.0 if llm_answered else 0.3

    graph_delta = _build_graph_delta(graph, workload_id, reward)

    try:
        from src.monkey_brain.kernel.learn.learning import Learning

        # The helper is _get_learner(). This called _get_learning(), which does not
        # exist — a NameError on EVERY completed execution, swallowed by the debug-level
        # except below. Nothing in this block has ever run: not the graph-topology
        # learner, not the Bellman policy update underneath it.
        learner = _get_learner()
        transition = learner.update(
            graph_id=workload_id,
            graph_delta=graph_delta,
            capability=goal_name,
        )
        try:
            from src.monkey_brain.kernel.fix.policy.transition import Transition

            policy = _get_policy()
            # BellmanPolicy.update takes ONE positional Transition. This called it with
            # four keywords (workload_id=, goal_name=, reward=, exec_ms=), so every
            # execution raised TypeError straight into the debug-level except below —
            # the ONLY thing that feeds the plan-selection Q-table. Its _q_table was
            # therefore empty forever, every candidate scored 0.0 in select(), and plan
            # selection collapsed to "return the first candidate" (plus 10% random
            # exploration). The Bellman policy has never influenced a single plan choice.
            #
            # The key must be the one select() reads back: it looks up
            # (hash_state(state), steps[0].capability_name) and is called WITHOUT a state
            # (see CognitiveRuntime._plan), so the state side hashes {}. Write the same
            # key or the update is unreadable — which is the second half of this bug.
            steps = list(getattr(workload, "steps", []) or [])
            action = getattr(steps[0], "capability_name", "") if steps else workload_id
            if action:
                policy.update(
                    Transition(
                        state={},
                        action=action,
                        next_state={"success": llm_answered},
                        reward=transition.graph_reward,
                        done=True,
                    )
                )
        except Exception as e:
            # Was debug — which is exactly how a hard TypeError stayed invisible.
            logger.warning("Policy update failed for workload=%r: %s", workload_id, e)
    except Exception as e:
        # Was debug — which is how a bare NameError hid here indefinitely while every
        # learning update silently did nothing.
        logger.warning("Graph learning update failed for workload=%r: %s", workload_id, e)

    # Telemetry
    try:
        from src.monkey_brain.kernel.learn.telemetry.telemetry import profile_add

        profile_add("learn_record", ms=int(exec_ms))
    except Exception:
        logger.debug("record_outcome: suppressed exception", exc_info=True)

    # Lemon
    if lemon:
        try:
            lemon.observe_learning(
                capability=workload_id,
                state_key=goal_name,
                q_before=0.0,
                q_after=reward,
                reward=reward,
                episode=0,
            )
            lemon.observe_goal(
                goal_id=run_id or goal_name,
                goal_text=goal_name,
                status="learned",
                pipeline_id=workload_id,
            )
        except Exception as e:
            logger.debug("Lemon learn observe failed: %s", e)

    logger.debug(
        "[learn] goal=%r workload=%s reward=%.2f exec_ms=%.0f",
        goal_name,
        workload_id,
        reward,
        exec_ms,
    )


def _build_graph_delta(graph: Any, workload_id: str, reward: float):
    """Build a minimal graph delta from an execution graph or dict-like graph."""
    from src.monkey_brain.kernel.execute.graph import NodeState
    from src.monkey_brain.kernel.learn.learning import GraphDelta

    envelope = canonical_graph_envelope(graph)
    graph_id = envelope.get("graph_id", "") or getattr(graph, "id", "") or workload_id
    nodes = envelope.get("nodes", [])
    edges = envelope.get("edges", [])

    node_rewards: dict[str, float] = {}
    added_nodes: list[str] = []
    for node in nodes:
        node_id = node.get("id") if isinstance(node, dict) else getattr(node, "id", "")
        state = node.get("state") if isinstance(node, dict) else getattr(node, "state", NodeState.PENDING.value)
        if node_id:
            node_rewards[node_id] = (
                1.0 if state == NodeState.COMPLETE.value else (0.0 if state == NodeState.FAILED.value else 0.5)
            )
            added_nodes.append(node_id)

    return GraphDelta(
        graph_id=graph_id,
        added_nodes=added_nodes,
        added_edges=[e for e in edges if isinstance(e, dict)],
        predicted_state=envelope,
        observed_state=envelope,
        node_rewards=node_rewards,
        graph_reward=reward,
        epistemic_loss=0.0,
        topological_loss=1.0 - reward,
        execution_metrics={"node_count": len(nodes), "edge_count": len(edges)},
    )


# ── module-level singletons ────────────────────────────────────────────────

_learner = None


def _get_learner():
    global _learner
    if _learner is None:
        from src.monkey_brain.kernel.learn.learning import Learning

        _learner = Learning()
    return _learner


def _get_policy():
    from src.monkey_brain.kernel.plan.workload.policy import get_policy

    return get_policy()
