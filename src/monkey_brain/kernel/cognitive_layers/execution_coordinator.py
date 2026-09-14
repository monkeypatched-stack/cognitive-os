"""Layer 3: Execution Coordination - Single Responsibility.

Responsibility: Execute cognitive workload through goal executor.
Depends on: GoalExecutor, persistence, event bus, graph store
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.monkey_brain.kernel.plan.goals.executor import GoalExecutor
from src.monkey_brain.kernel.plan.goals.run_store import get_run_store
from src.monkey_brain.kernel.compile.solid_interfaces import ServiceInterface

if TYPE_CHECKING:
    from src.monkey_brain.kernel.execute.context import ExecutionContext

logger = logging.getLogger("agentos.cognitive.layers.execution_coordinator")


class ExecutionCoordinator(ServiceInterface):
    """Execute cognitive workload.

    Responsibility: Run ExecutionContext through goal executor, persist
    execution mesh, record graph observations, support replay.
    Input: ExecutionContext, mongo client, kwargs
    Output: (answer, semantic_hits, graph_paths, llm_answered)

    This is where actual execution happens (not pure compilation).
    """

    def __init__(self) -> None:
        self._goal_executor = GoalExecutor()
        self._event_bus: Any = None

    async def execute(self, command: Any) -> Any:
        """Implement ServiceInterface - execute command."""
        if isinstance(command, dict) and "context" in command:
            return await self.execute_from_dict(command)
        raise TypeError(f"Unsupported command type: {type(command)}")

    async def execute_workload(
        self,
        context: ExecutionContext,
        mongo_client: Any,
        execution_graph: Any = None,
        **kwargs: Any,
    ) -> tuple[str, list, list, bool]:
        """Execute cognitive workload.

        Args:
            context: ExecutionContext (from RuntimeBuilder)
            mongo_client: MongoDB client
            execution_graph: Current execution graph (for observation)
            **kwargs: Additional execution parameters

        Returns:
            (answer, semantic_hits, graph_paths, llm_answered)
        """
        logger.debug(
            "[coordinator] Executing workload: run_id=%s, mode=%s",
            context.run_id,
            (context.execution_mode.name if hasattr(context.execution_mode, "name") else str(context.execution_mode)),
        )

        answer, semantic_hits, graph_paths, llm_answered = await self._goal_executor.execute_context(
            context,
            mongo_client,
            **kwargs,
        )

        self._mark_execution_graph_observed(
            execution_graph,
            context.run_id,
            answer=answer,
            semantic_hits=semantic_hits,
            graph_paths=graph_paths,
            llm_answered=llm_answered,
        )
        await self._persist_execution_mesh(
            context,
            execution_graph=execution_graph,
            plan_steps=list(kwargs.get("plan_steps") or []),
            answer=answer,
            llm_answered=llm_answered,
        )

        logger.info(
            "[coordinator] Execution complete: run_id=%s, llm_answered=%s, answer_len=%d",
            context.run_id,
            llm_answered,
            len(answer) if answer else 0,
        )

        return answer, semantic_hits, graph_paths, llm_answered

    async def execute_from_dict(self, command: dict) -> tuple[str, list, list, bool]:
        """Execute from dict (supports ServiceInterface)."""
        return await self.execute_workload(
            context=command.get("context"),
            mongo_client=command.get("mongo_client"),
            **command.get("kwargs", {}),
        )

    async def replay(self, run_id: str, mongo_client: Any) -> tuple[str, list, list, bool]:
        """Re-execute a stored IntentIR for run_id."""
        from src.monkey_brain.kernel.plan.goals.run_store import replay as _replay

        result = await _replay(run_id, mongo_client)
        await self._publish("run.replayed", {"run_id": run_id, "target": "execute"})
        return result

    def has_stored_run(self, run_id: str) -> bool:
        return get_run_store().has(run_id)

    async def _persist_execution_mesh(
        self,
        context: Any,
        *,
        execution_graph: Any = None,
        plan_steps: list,
        answer: str,
        llm_answered: bool,
    ) -> None:
        """Record this run into the three-layer graph."""
        try:
            run_id = str(context.run_id)
            agents: list[str] = []
            agent_edges: list[tuple[str, str]] = []
            graph = execution_graph if isinstance(execution_graph, dict) else {}
            graph_run_id = str(graph.get("metadata", {}).get("run_id", ""))
            if graph_run_id == run_id:
                by_id = {
                    str(n.get("id", "")): str(n.get("agent", n.get("name", "")))
                    for n in graph.get("nodes", [])
                    if isinstance(n, dict) and str(n.get("state", "")) == "complete"
                }
                agents = [a for a in by_id.values() if a]
                for edge in graph.get("edges", []):
                    if not isinstance(edge, dict):
                        continue
                    src = by_id.get(str(edge.get("from") or edge.get("src") or ""), "")
                    dst = by_id.get(str(edge.get("to") or edge.get("dst") or ""), "")
                    if src and dst:
                        agent_edges.append((src, dst))
            elif llm_answered and not str(answer).lower().startswith("error executing goal"):
                by_id = {
                    str(s.get("step_id", "")): str(s.get("agent", "") or s.get("capability_name", ""))
                    for s in plan_steps
                    if isinstance(s, dict)
                }
                agents = [a for a in by_id.values() if a]
                for s in plan_steps:
                    if not isinstance(s, dict):
                        continue
                    dst = str(s.get("agent", "") or s.get("capability_name", ""))
                    for dep in s.get("dependencies", []) or []:
                        src = by_id.get(str(dep), "")
                        if src and dst:
                            agent_edges.append((src, dst))
            if not agents:
                return

            ir = getattr(context, "intent_ir", None)
            meta = getattr(ir, "execution_metadata", None) or {}
            question = str(meta.get("question", ""))
            domain = str(meta.get("capability_group") or meta.get("domain") or "execution")

            try:
                from src.monkey_brain.kernel.compile.world_tensor import (
                    observe_execution,
                )

                observe_execution(agents, agent_edges, domain=domain, reward=1.0)
            except Exception as exc:
                logger.warning("[coordinator] observe_execution failed: %s", exc)

            from src.monkey_brain.persistence.graph_store import (
                get_graph_store_instance,
            )

            store = get_graph_store_instance()
            if store is None or not store.is_connected():
                return

            await store.record_execution_graph(run_id, question, agents, agent_edges)
            await store.compute_capability_pagerank()
            await store.compute_execution_mesh_pagerank()
        except Exception as e:
            logger.warning(
                "run=%r execution-mesh persistence failed: %s",
                getattr(context, "run_id", "?"),
                e,
            )

    def _mark_execution_graph_observed(
        self,
        execution_graph: Any,
        run_id: str,
        *,
        answer: str,
        semantic_hits: list,
        graph_paths: list,
        llm_answered: bool,
    ) -> None:
        if not isinstance(execution_graph, dict):
            return
        graph_run_id = str(execution_graph.get("metadata", {}).get("run_id", ""))
        if graph_run_id and graph_run_id != str(run_id):
            logger.warning(
                "run=%r skipping graph observation: execution_graph belongs to run=%r",
                run_id,
                graph_run_id,
            )
            return
        raw_success = bool(llm_answered) and not str(answer).lower().startswith("error executing goal")
        terminal_states = ("complete", "failed", "unimplemented")
        nodes = [n for n in execution_graph.get("nodes", []) if isinstance(n, dict) and str(n.get("id", ""))]
        has_recorded_failure = any(str(n.get("state", "")) in ("failed", "unimplemented") for n in nodes)
        overall_success = raw_success and not has_recorded_failure
        state = dict(execution_graph.get("state", {}))
        for node in nodes:
            node_id = str(node.get("id", ""))
            recorded = str(node.get("state", ""))
            if recorded in terminal_states:
                entry = dict(state.get(node_id, {}))
                entry.setdefault("status", recorded)
                state[node_id] = entry
                continue
            if overall_success:
                node_state = "complete"
            elif has_recorded_failure:
                continue
            else:
                node_state = "failed"
            node["state"] = node_state
            state[node_id] = {
                "status": node_state,
                "answer": answer,
                "llm_answered": llm_answered,
            }
        execution_graph["state"] = state
        final_states = [str(n.get("state", "")) for n in nodes]
        if "failed" in final_states or not raw_success:
            final_status = "failed"
        elif final_states and all(s == "complete" for s in final_states):
            final_status = "complete"
        else:
            final_status = "partial"
        metadata = dict(execution_graph.get("metadata", {}))
        metadata["run_id"] = run_id
        metadata["execution"] = {
            "status": final_status,
            "answer": answer,
            "semantic_hit_count": len(semantic_hits or []),
            "graph_path_count": len(graph_paths or []),
            "llm_answered": llm_answered,
        }
        execution_graph["metadata"] = metadata

    def set_event_bus(self, event_bus: Any) -> None:
        """Set event bus for publishing execution events."""
        self._event_bus = event_bus

    async def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publish execution event."""
        if self._event_bus is not None:
            await self._event_bus.publish(event_type, payload)
            logger.debug("[coordinator] Published event: %s", event_type)
