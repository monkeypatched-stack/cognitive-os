"""Cognitive Loop - Single Responsibility.

Responsibility: The iterative learning loop (plan -> simulate -> act -> compare -> learn -> reflect).
Depends on: graph manager, simulation runtime, comparator runtime, knowledge manager,
            world coordinator, observation pipeline, audit service
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger("agentos.cognitive.layers.cognitive_loop")


class CognitiveLoop:
    """The iterative cognitive learning loop.

    Owns:
        Knowledge Acquisition -> Planning -> Simulation -> Execution
        -> Comparison -> Learning -> Reflection -> Convergence

    Independent from bootstrapping — receives all dependencies via constructor.
    """

    def __init__(
        self,
        *,
        graph_manager: Any = None,
        knowledge_manager: Any = None,
        world_coordinator: Any = None,
        observation_pipeline: Any = None,
        audit_service: Any = None,
        lemon: Any = None,
        persistence: Any = None,
        event_bus: Any = None,
        semantic_memory: Any = None,
    ) -> None:
        self._graph_manager = graph_manager
        self._knowledge_manager = knowledge_manager
        self._world_coordinator = world_coordinator
        self._observation_pipeline = observation_pipeline
        self._audit = audit_service
        self._lemon = lemon
        self._persistence = persistence
        self._event_bus = event_bus
        self._semantic_memory = semantic_memory
        self._sim_runtime: Any = None
        self._comp_runtime: Any = None
        self._trust_network: Any = None

    async def _publish(self, event_type: str, payload: dict) -> None:
        if self._event_bus is not None:
            await self._event_bus.publish(event_type, payload)

    async def run(self, question: str, mongo_client: Any = None, **kwargs: Any) -> dict:
        """Full cognitive cycle.

        INTENT -> Knowledge -> Plan -> Simulate -> Act -> Compare -> Learn -> Reflect
        """
        if self._graph_manager is None:
            from src.monkey_brain.kernel.graph_manager import GraphManager

            self._graph_manager = GraphManager()

        max_epochs = kwargs.get("max_epochs", 10)
        convergence_threshold = kwargs.get("convergence_threshold", 0.1)
        batch_size = kwargs.get("batch_size", 5)
        max_wall_clock_seconds = kwargs.get("max_wall_clock_seconds", 300.0)

        await self._publish("cognitive.run.started", {"question": question, "max_epochs": max_epochs})

        # What do I know?
        knowledge = {}
        knowledge_gaps = []
        if self._knowledge_manager:
            knowledge = await self._knowledge_manager.query_semantic_memory(question)
            knowledge_gaps = self._knowledge_manager.find_knowledge_gaps(question, knowledge)

        # Acquire or ground
        if knowledge_gaps and self._knowledge_manager:
            await self._publish(
                "cognitive.acquire.started",
                {"gaps": knowledge_gaps, "question": question},
            )
            await self._knowledge_manager.acquire_knowledge(knowledge_gaps, question)
            await self._publish("cognitive.acquire.completed", {})
        elif self._knowledge_manager:
            await self._publish("cognitive.ground.started", {"question": question})
            await self._knowledge_manager.ground_knowledge(knowledge, question)
            await self._publish(
                "cognitive.ground.completed",
                {"knowledge_items": knowledge.get("count", 0)},
            )

        await self._publish("cognitive.intention", {"question": question, "gaps": len(knowledge_gaps)})

        # Build/Update Execution Graph -> ACT -> OBSERVE -> COMPARE -> LEARN
        epoch_results = []
        _loop_start = time.monotonic()
        act_result: dict = {}

        for epoch in range(max_epochs):
            if time.monotonic() - _loop_start > max_wall_clock_seconds:
                logger.warning(
                    "[cognitive] wall-clock timeout after %.1fs (epoch %d/%d)",
                    max_wall_clock_seconds,
                    epoch,
                    max_epochs,
                )
                await self._publish(
                    "cognitive.run.timeout",
                    {"elapsed": max_wall_clock_seconds, "epoch": epoch},
                )
                if self._lemon:
                    self._lemon.counter("cognitive.run.timeout")
                break
            try:
                graph_delta = await self._plan(
                    question,
                    self._graph_manager.graph,
                    planner=kwargs.get("planner", "compiler"),
                )
                self._graph_manager.apply_plan(graph_delta)

                from src.monkey_brain.kernel.execute.graph import sign_graph_dict

                sign_graph_dict(self._graph_manager.graph)

                if not self._graph_manager.graph.get("nodes"):
                    break

                self._audit_record(
                    "plan",
                    "graph_planned",
                    "planner",
                    details={
                        "nodes": len(self._graph_manager.graph.get("nodes", [])),
                        "edges": len(self._graph_manager.graph.get("edges", [])),
                    },
                )

                # Governance gate
                if self._world_coordinator:
                    gov = self._audit.governance_check(self._graph_manager.graph) if self._audit else {"blocked": False}
                else:
                    gov = {"blocked": False}
                if gov and gov.get("blocked"):
                    logger.warning("Governance blocked execution: %s", gov.get("reason"))
                    self._audit_record(
                        "governance",
                        "execution_blocked",
                        "governance_engine",
                        outcome="denied",
                        details={"reason": gov.get("reason")},
                    )
                    if self._lemon:
                        self._lemon.counter("governance.execution_blocked")
                    break

                self._audit_record("governance", "execution_allowed", "governance_engine")

                await self._simulate(question, mongo_client)
                simulation_graph = (
                    self._sim_runtime.simulation_graph
                    if self._sim_runtime and hasattr(self._sim_runtime, "simulation_graph")
                    else {}
                )

                # Fuse observations
                if self._observation_pipeline and self._world_coordinator:
                    self._observation_pipeline.fuse_observations(
                        question,
                        simulation_graph,
                        publish_world_update_fn=self._world_coordinator.publish_world_update,
                    )

                # Batch execution
                all_observations: list[dict] = []
                act_result = {"observations": [], "node_states": {}, "failed_nodes": []}
                for batch in range(batch_size):
                    path = self._graph_manager.next_path("start", "goal")
                    batch_result = await self._act(path, question, mongo_client)
                    if batch_result:
                        all_observations.extend(batch_result.get("observations", []))
                        act_result["node_states"].update(batch_result.get("node_states", {}))
                        act_result["failed_nodes"].extend(batch_result.get("failed_nodes", []))

                if act_result:
                    await self._compare(simulation_graph, act_result, mongo_client)

                # Estimate world state
                if self._observation_pipeline and self._world_coordinator:
                    self._observation_pipeline.estimate_world_state(
                        all_observations,
                        question,
                        simulation_graph,
                        publish_world_update_fn=self._world_coordinator.publish_world_update,
                    )

                # Aggregate policy delta
                if self._observation_pipeline:
                    policy_delta = self._observation_pipeline.aggregate_policy_delta(
                        all_observations,
                    )
                else:
                    policy_delta = {}

                learn_result = self._graph_manager.apply_learning(policy_delta, simulation_graph)
                self._graph_manager.compile_graph()

                # Publish learned knowledge
                self._publish_learned_knowledge(policy_delta)

                self._audit_record(
                    "learn",
                    "epoch_complete",
                    "learning_loop",
                    details={
                        "epoch": epoch + 1,
                        "perplexity": learn_result.get("perplexity", 0),
                        "nodes_learned": learn_result.get("nodes_learned", 0),
                        "q_table_size": learn_result.get("q_table_size", 0),
                    },
                )

                learned_delta = learn_result.get("graph_delta", {})
                if learned_delta.get("nodes") or learned_delta.get("edges"):
                    self._graph_manager.apply_plan(learned_delta)

                epoch_results.append(
                    {
                        "epoch": epoch + 1,
                        "nodes": len(self._graph_manager.graph.get("nodes", [])),
                        "batches": batch_size,
                        "observations": len(all_observations),
                        "perplexity": learn_result["perplexity"],
                        "topological_loss": learn_result["topological_loss"],
                        "epistemic_loss": learn_result["epistemic_loss"],
                        "synced": learn_result["synced"],
                        "q_table_size": learn_result["q_table_size"],
                        "failed_nodes": act_result.get("failed_nodes", []),
                    }
                )

                await self._publish("cognitive.run.epoch", epoch_results[-1])

                if self._lemon:
                    epoch_str = str(epoch + 1)
                    self._lemon.histogram(
                        "cognitive.epoch.perplexity",
                        learn_result["perplexity"],
                        epoch=epoch_str,
                    )
                    self._lemon.histogram(
                        "cognitive.epoch.topological_loss",
                        learn_result["topological_loss"],
                        epoch=epoch_str,
                    )
                    self._lemon.counter("cognitive.epoch.completed", epoch=epoch_str)

                if self._graph_manager.loss <= 0.0 or self._graph_manager.converged(convergence_threshold):
                    break

            except asyncio.TimeoutError:
                logger.error("[cognitive] epoch %d timed out", epoch + 1)
                await self._publish(
                    "cognitive.run.epoch_failed",
                    {"epoch": epoch + 1, "error": "timeout"},
                )
                if self._lemon:
                    self._lemon.counter(
                        "cognitive.epoch.failed",
                        epoch=str(epoch + 1),
                        error="TimeoutError",
                    )
                break
            except Exception as e:
                import traceback

                logger.error(
                    "[cognitive] epoch %d failed: %s\n%s",
                    epoch + 1,
                    e,
                    traceback.format_exc(),
                )
                await self._publish(
                    "cognitive.run.epoch_failed",
                    {"epoch": epoch + 1, "error": str(e)[:200]},
                )
                if self._lemon:
                    self._lemon.counter(
                        "cognitive.epoch.failed",
                        epoch=str(epoch + 1),
                        error=type(e).__name__,
                    )
                if epoch_results and epoch_results[-1].get("failed_nodes"):
                    break
                continue

        # Reflect
        await self._reflect(epoch_results, act_result)

        # Update Procedural Memory
        self._graph_manager.save()

        result = {
            "cycles": len(epoch_results),
            "converged": self._graph_manager.converged(convergence_threshold),
            "final_loss": self._graph_manager.loss,
            "epoch_results": epoch_results,
            "execution_graph": self._graph_manager.graph,
            "simulation_graph": (self._sim_runtime.simulation_graph if self._sim_runtime else {}),
            "diff": self._comp_runtime.diff if self._comp_runtime else {},
            "q_table": self._graph_manager.q_table.snapshot(),
            "graph_synced": (epoch_results[-1].get("synced", False) if epoch_results else False),
            "semantic_memory": (
                self._knowledge_manager.get_semantic_memory_summary() if self._knowledge_manager else {}
            ),
            "knowledge_gaps": knowledge_gaps,
        }

        await self._publish(
            "cognitive.run.completed",
            {
                "cycles": len(epoch_results),
                "converged": result["converged"],
            },
        )
        return result

    async def _plan(self, question: str, current_graph: dict, planner: str = "compiler") -> dict:
        """Generate candidate graphs and select the best via Bellman policy."""
        await self._publish("cognitive.plan.started", {"question": question, "planner": planner})

        knowledge = {}
        if self._semantic_memory and self._semantic_memory.available:
            knowledge = await self._semantic_memory.query(question) or {}

        goal_ir: dict = {}
        if planner == "etass":
            from broca.agents.specification_discovery_agent import (
                SpecificationDiscoveryAgent,
            )
            from broca.agents.graph_generator_agent import GraphGeneratorAgent

            spec_agent = SpecificationDiscoveryAgent()
            spec_result = await spec_agent.handle({"question": question})
            spec_payload = spec_result.payload

            if not spec_payload.get("discovered"):
                await self._publish("cognitive.plan.failed", {"reason": "discovery failed"})
                return {"nodes": [], "edges": [], "remove_nodes": []}

            spec_payload["knowledge"] = knowledge

            graph_agent = GraphGeneratorAgent()
            graph_result = await graph_agent.handle(
                {
                    "specification": spec_payload.get("specification", {}),
                    "discovery": spec_payload.get("discovery", {}),
                    "knowledge": knowledge,
                    "intent": question,
                }
            )
            candidates = graph_result.payload.get("candidates", [])
        else:
            from broca.agents.compiler_pipeline import CompilerPipeline

            if self._trust_network is None:
                from src.monkey_brain.kernel.compile.trust import TrustNetwork

                self._trust_network = TrustNetwork()

            pipeline = CompilerPipeline(
                trust_network=self._trust_network,
                trust_threshold=0.5,
            )
            pipeline_result = await pipeline.handle(
                {
                    "intent": question,
                    "question": question,
                    "knowledge": knowledge,
                }
            )
            goal_ir = pipeline_result.payload.get("goal_ir", {})
            candidates = pipeline_result.payload.get("candidates", [])

        if not candidates:
            await self._publish("cognitive.plan.failed", {"reason": "no valid candidates"})
            return {"nodes": [], "edges": [], "remove_nodes": []}

        from src.monkey_brain.kernel.plan.workload.policy import get_policy
        from src.monkey_brain.kernel.plan.workload.workload import (
            Workload,
            WorkloadStep,
        )

        policy = get_policy()
        if len(candidates) == 1:
            selected = candidates[0]
        else:
            policy_workloads = []
            for c in candidates:
                graph_data = c["graph"]
                w = Workload(
                    workload_id=c["execution_graph_id"],
                    steps=[
                        WorkloadStep(
                            capability_name=n.get("name") or n.get("agent", ""),
                            agent_name=n.get("agent", ""),
                        )
                        for n in graph_data.get("nodes", [])
                    ],
                    metadata={"execution_graph_id": c["execution_graph_id"]},
                )
                w.build_dag()
                policy_workloads.append(w)

            selected_workload = policy.select(policy_workloads)
            selected = next(c for c in candidates if c["execution_graph_id"] == selected_workload.workload_id)

        graph_delta = selected["graph"]
        graph_delta.setdefault("remove_nodes", [])
        graph_delta["execution_graph_id"] = selected["execution_graph_id"]
        graph_delta["goal_ir"] = goal_ir

        await self._publish(
            "cognitive.plan.completed",
            {
                "nodes": len(graph_delta.get("nodes", [])),
                "candidates": len(candidates),
                "knowledge_items": len(knowledge.get("results", [])),
                "intent_type": goal_ir.get("intent_type", ""),
                "domain": goal_ir.get("domain", ""),
            },
        )
        return graph_delta

    async def _simulate(self, question: str, mongo_client: Any = None) -> dict:
        """Dry-run via SimulationRuntime."""
        await self._publish("cognitive.simulate.started", {"question": question})

        try:
            from src.monkey_brain.kernel.simulation_runtime import (
                get_simulation_runtime,
            )

            self._sim_runtime = get_simulation_runtime()
            self._sim_runtime.lemon = self._lemon
            self._sim_runtime.persistence = self._persistence
            self._sim_runtime._event_bus = self._event_bus

            ir = await self._sim_runtime.compile_intent(question, store=False)
            if ir is None:
                return {"predicted": {}, "error": "intent compilation failed"}

            result = await self._sim_runtime.run(ir, mongo_client)
            await self._publish("cognitive.simulate.completed", {"run_id": ir.run_id})
            return {"predicted": result, "run_id": ir.run_id}
        except Exception as e:
            logger.warning("[cognitive] simulate failed: %s", e)
            return {"predicted": {}, "error": str(e)[:200]}

    async def _act(self, path: list[dict], question: str, mongo_client: Any = None) -> dict:
        """Graph-driven execution with concurrent dispatch and rate limiting."""
        await self._publish("cognitive.act.started", {"path_length": len(path)})

        observations: list[dict] = []
        node_states: dict[str, str] = {}
        node_rewards: dict[str, float] = {}
        failed_nodes: list[str] = []

        rate_limiter = (
            self._semantic_memory._rate_limiter
            if self._semantic_memory and hasattr(self._semantic_memory, "_rate_limiter")
            else None
        )
        completed: set[str] = set()
        remaining = {n["id"]: n for n in path}
        edges = self._graph_manager.graph.get("edges", [])

        while remaining:
            ready = [
                node
                for nid, node in remaining.items()
                if all(
                    dep in completed
                    for dep in (e["from"] for e in edges if e["to"] == nid and e.get("type") != "feedback")
                )
            ]
            if not ready:
                break

            results = await asyncio.gather(
                *(self._run_node(node, question, mongo_client, rate_limiter) for node in ready)
            )

            for node, (reward, status, latency_ms) in zip(ready, results):
                node_id = node["id"]
                if status != "complete":
                    failed_nodes.append(node_id)

                obs = self._graph_manager.annotate(
                    {
                        "node_id": node_id,
                        "agent": node.get("agent", ""),
                        "status": status,
                        "reward": reward,
                        "latency_ms": latency_ms,
                    }
                )
                observations.append(obs)
                node_states[node_id] = status
                node_rewards[node_id] = reward
                completed.add(node_id)

                if self._lemon:
                    self._lemon.counter(
                        ("cognitive.node.complete" if status == "complete" else "cognitive.node.failed"),
                        node=node_id,
                        agent=node.get("agent", ""),
                    )
                    self._lemon.histogram("cognitive.node.latency_ms", latency_ms, node=node_id)

            remaining = {nid: n for nid, n in remaining.items() if nid not in completed}

        await self._publish(
            "cognitive.act.completed",
            {
                "nodes_executed": len(observations),
                "failed_nodes": failed_nodes,
            },
        )

        return {
            "nodes_executed": len(observations),
            "observations": observations,
            "node_states": node_states,
            "node_rewards": node_rewards,
            "failed_nodes": failed_nodes,
        }

    @staticmethod
    def _node_state_from_result(result: Any, agent_name: str = "") -> str:
        """complete / unimplemented / failed — from what the agent actually reported."""
        payload = getattr(result, "payload", None)
        payload = payload if isinstance(payload, dict) else {}

        if payload.get("unimplemented"):
            logger.warning("[act] %s is unimplemented — node is not complete", agent_name)
            return "unimplemented"

        success = payload.get("success", getattr(result, "success", True))
        if not success:
            logger.warning(
                "[act] %s reported failure — node is not complete: %s",
                agent_name,
                str(payload.get("error", ""))[:120],
            )
            return "failed"
        return "complete"

    async def _run_node(
        self,
        node: dict,
        question: str,
        mongo_client: Any,
        rate_limiter: Any,
    ) -> tuple[float, str, float]:
        """Execute one node and time it, returning (reward, status, latency_ms)."""
        node_id = node["id"]
        start = time.time()
        try:
            reward, status = await self._execute_with_rate_limit(node, question, mongo_client, rate_limiter)
        except Exception as e:
            logger.warning("[act] node %s failed: %s", node_id, e)
            reward, status = 0.0, "failed"
        return reward, status, (time.time() - start) * 1000

    async def _execute_with_rate_limit(
        self,
        node: dict,
        question: str,
        mongo_client: Any,
        rate_limiter: Any,
    ) -> tuple[float, str]:
        """Execute a node with rate limiting and distributed tracing."""
        agent_name = node.get("agent", "")
        node_type = node.get("type", "execution")

        if node_type == "cognitive" or not agent_name or agent_name == "auto_agent_generator":
            return 0.5, "complete"

        trace_id = node["id"]
        span_id = f"{agent_name}-{int(time.time() * 1000)}"

        if self._lemon:
            self._lemon.start_trace(f"act:{agent_name}")
            self._lemon.start_span(
                f"execute:{agent_name}",
                component=agent_name,
                trace_id=trace_id,
                span_id=span_id,
                node_id=node["id"],
            )

        if rate_limiter:
            await rate_limiter.acquire(agent_name)

        try:
            from broca.registry import get_registry

            registry = get_registry()
            agent = registry.discover(agent_name)

            if agent is None:
                logger.warning(
                    "[act] no agent registered for %r — node is unimplemented",
                    agent_name,
                )
                return 0.0, "unimplemented"

            context = {
                "question": question,
                "node": node,
                "node_id": node["id"],
                "mongo_client": mongo_client,
                "trace_id": trace_id,
                "span_id": span_id,
                "agent_name": agent_name,
                "target_agent": node.get("target_agent", ""),
                "target_description": node.get("target_description", ""),
            }
            result = await agent.handle(context)
            reward = getattr(result, "reward", 0.5) if hasattr(result, "reward") else 0.5

            state = self._node_state_from_result(result, agent_name)

            if self._lemon:
                self._lemon.finish_span("ok" if state == "complete" else state)

            return reward, state
        except Exception:
            if self._lemon:
                self._lemon.finish_span("error")
            raise
        finally:
            if rate_limiter:
                await rate_limiter.release(agent_name)
            if self._lemon:
                self._lemon.finish_trace()

    async def _compare(self, simulation_graph: dict, act_result: dict, mongo_client: Any = None) -> dict:
        """Diff Simulation Graph vs Execution Graph."""
        await self._publish("cognitive.compare.started", {})

        try:
            from src.monkey_brain.kernel.comparator_runtime import (
                get_comparator_runtime,
            )
            from src.monkey_brain.kernel.models.graph import canonical_graph_envelope

            self._comp_runtime = get_comparator_runtime()
            self._comp_runtime.lemon = self._lemon
            self._comp_runtime.persistence = self._persistence
            self._comp_runtime._event_bus = self._event_bus

            predicted_snapshot = simulation_graph.get("graph") or simulation_graph
            actual_snapshot = act_result.get("graph") or act_result

            if not isinstance(predicted_snapshot, dict):
                predicted_snapshot = {}
            if not isinstance(actual_snapshot, dict):
                actual_snapshot = {}

            predicted_envelope = canonical_graph_envelope(predicted_snapshot)
            actual_envelope = canonical_graph_envelope(actual_snapshot)

            comparison_result = await self._comp_runtime.compare(predicted_envelope, actual_envelope)

            epistemic_loss = comparison_result.epistemic_loss
            reward = comparison_result.comparison_score

            predicted_state = (
                predicted_snapshot.get("metadata", {})
                .get("summary", {})
                .get("predicted_state", simulation_graph.get("predicted_state", {}))
            )
            actual_states = (
                actual_snapshot.get("metadata", {})
                .get("summary", {})
                .get("observed_state", act_result.get("node_states", {}))
            )

            self._comp_runtime.diff = {
                "predicted": predicted_state,
                "actual": actual_states,
                "predicted_snapshot": predicted_snapshot,
                "actual_snapshot": actual_snapshot,
                "loss": round(epistemic_loss, 3),
                "comparison": comparison_result.to_dict(),
            }
            self._comp_runtime.loss = epistemic_loss

            compare_result = {
                "loss": round(epistemic_loss, 3),
                "reward": round(reward, 3),
                "predicted": predicted_state,
                "actual": actual_states,
                "predicted_snapshot": predicted_snapshot,
                "actual_snapshot": actual_snapshot,
                "graph_id": predicted_envelope.get("graph_id") or actual_envelope.get("graph_id"),
                "comparison": comparison_result.to_dict(),
            }

            await self._persist_compare_snapshot(compare_result, predicted_snapshot, actual_snapshot)
            await self._publish("cognitive.compare.completed", compare_result)
            return compare_result

        except Exception as e:
            logger.warning("[cognitive] compare failed: %s", e)
            return {"loss": 0.5, "reward": 0.5, "error": str(e)[:200]}

    async def _persist_compare_snapshot(
        self,
        compare_result: dict[str, Any],
        predicted_snapshot: dict[str, Any],
        actual_snapshot: dict[str, Any],
    ) -> None:
        if self._persistence is None:
            return
        from src.monkey_brain.persistence.events import EventType, PersistenceEvent

        run_id = str(compare_result.get("graph_id", ""))
        event = PersistenceEvent(
            event_type=EventType.METRIC_RECORDED,
            entity_type="comparison_snapshot",
            entity_id=run_id,
            data={
                "run_id": run_id,
                "graph_id": compare_result.get("graph_id", ""),
                "predicted_snapshot": predicted_snapshot,
                "actual_snapshot": actual_snapshot,
                "comparison": compare_result,
                "source": "cognitive_runtime",
            },
            source="cognitive_runtime",
        )
        await self._persistence.persist(event)

    async def _reflect(self, epoch_results: list, act_result: dict) -> None:
        """Reflect on learning: what did I learn? mistakes? corrections?"""
        await self._publish("cognitive.reflect.started", {})

        if not epoch_results:
            return

        last = epoch_results[-1]
        perplexity = last.get("perplexity", 1.0)
        failed_nodes = last.get("failed_nodes", [])

        mistakes = []
        if failed_nodes:
            mistakes.append(f"Failed nodes: {', '.join(failed_nodes)}")
        if perplexity > 0.5:
            mistakes.append(f"High loss: {perplexity:.3f}")
        if last.get("topological_loss", 0) > 0.3:
            mistakes.append(f"High topological loss: {last.get('topological_loss', 0):.3f}")

        corrections = []
        if failed_nodes:
            corrections.append("Retry failed nodes with different parameters")
        if perplexity > 0.5:
            corrections.append("Increase exploration rate")
        if last.get("topological_loss", 0) > 0.3:
            corrections.append("Sync simulation and execution graphs")

        if self._lemon:
            if perplexity > 0.5:
                self._lemon.alert(
                    name="cognitive.high_loss",
                    message=f"Loss {perplexity:.3f} exceeds threshold 0.5",
                    severity="warning",
                )
            if failed_nodes:
                self._lemon.alert(
                    name="cognitive.node_failures",
                    message=f"{len(failed_nodes)} nodes failed: {', '.join(failed_nodes)}",
                    severity="warning",
                )
            if last.get("synced"):
                self._lemon.alert(
                    name="cognitive.graph_synced",
                    message="Simulation and execution graphs synced",
                    severity="info",
                )

        await self._publish(
            "cognitive.reflect.completed",
            {
                "perplexity": perplexity,
                "mistakes": mistakes,
                "corrections": corrections,
                "failed_nodes": failed_nodes,
            },
        )

    def _publish_learned_knowledge(self, policy_delta: dict[str, float]) -> None:
        """Publish learned transitions as BeliefProposals for cross-runtime exchange."""
        if not policy_delta:
            return
        try:
            from src.monkey_brain.kernel.compile.exchange import KnowledgeExchange
            from src.monkey_brain.kernel.compile.trust import TrustNetwork

            transitions = []
            for agent, reward in policy_delta.items():
                transitions.append((agent, agent, reward))

            if not transitions:
                return

            exchange = KnowledgeExchange(TrustNetwork(), signing_key=b"trust-signing-key")
            proposal = exchange.publish("local", "belief", "cognitive", transitions)
            if proposal:
                import os

                peers = os.getenv("EXCHANGE_PEERS", "").split(",")
                peers = [p.strip() for p in peers if p.strip()]
                if peers:
                    from src.monkey_brain.kernel.compile.network import ExchangeClient

                    client = ExchangeClient()
                    for peer_url in peers:
                        try:
                            result = client.send_proposal(proposal, peer_url)
                            logger.debug(
                                "[exchange] sent to %s: %s",
                                peer_url,
                                result.get("status"),
                            )
                        except Exception as exc:
                            logger.debug("[exchange] failed to send to %s: %s", peer_url, exc)
                else:
                    logger.debug(
                        "[exchange] published %d transitions (local only)",
                        len(transitions),
                    )
        except Exception as exc:
            logger.debug("[exchange] publish skipped: %s", exc)

    def _audit_record(
        self,
        event_type: str,
        action: str,
        actor: str = "",
        outcome: str = "success",
        details: dict | None = None,
    ) -> None:
        """Delegate audit to AuditService."""
        if self._audit:
            self._audit.record(event_type, action, actor, outcome, details)
