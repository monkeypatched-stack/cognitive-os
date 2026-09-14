"""Agent Mesh — ephemeral agents, persistent knowledge."""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from src.knowledge.pack import KnowledgePack

logger = logging.getLogger(__name__)


def sign_payload(payload: dict[str, Any], agent_id: str) -> str:
    """Sign a payload using the identity module (Ed25519)."""
    import json

    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    try:
        from src.monkey_brain.kernel.identity import get_key_manager, sign_bytes

        km = get_key_manager()
        key = km.get_or_create(agent_id)
        return sign_bytes(blob, key)
    except Exception:
        import hashlib

        return hashlib.sha256(blob).hexdigest()


def verify_signature(payload: dict[str, Any], agent_id: str, signature: str) -> bool:
    """Verify a payload signature using the identity module."""
    import json

    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    try:
        from src.monkey_brain.kernel.identity import get_key_manager, verify_bytes

        km = get_key_manager()
        pub_pem = km.get_public_key_pem(agent_id)
        return verify_bytes(blob, signature, pub_pem)
    except Exception:
        return False


class AgentState(StrEnum):
    SPAWNED = "spawned"
    RUNNING = "running"
    IDLE = "idle"
    MERGING = "merging"
    TERMINATED = "terminated"


class AgentRole(StrEnum):
    PLANNER = "planner"
    EXECUTOR = "executor"
    VERIFIER = "verifier"
    ADVERSARIAL = "adversarial"
    SPECIALIST = "specialist"
    WORKER = "worker"


@dataclass
class AgentSpec:
    role: AgentRole = AgentRole.WORKER
    capabilities: list[str] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    knowledge_domain: str = ""
    solver_class: str = ""
    max_lifetime_steps: int = 100
    metadata: dict[str, Any] = field(default_factory=dict)
    agent_type: str = ""


@dataclass
class AgentMetrics:
    tasks_completed: int = 0
    tasks_failed: int = 0
    knowledge_produced: int = 0
    knowledge_consumed: int = 0
    avg_latency_ms: float = 0.0
    total_latency_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        total = self.tasks_completed + self.tasks_failed
        return self.tasks_completed / max(total, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "success_rate": self.success_rate,
            "knowledge_produced": self.knowledge_produced,
            "avg_latency_ms": self.avg_latency_ms,
        }


class Agent:
    def __init__(self, agent_id: str, spec: AgentSpec, knowledge_pack: KnowledgePack):
        self.id = agent_id
        self.spec = spec
        self.state = AgentState.SPAWNED
        self.metrics = AgentMetrics()
        self.knowledge_pack = knowledge_pack
        self._steps = 0
        self._sandbox = None  # lazy-initialized AgentSandbox

    def _get_sandbox(self):
        """Lazy-initialize sandbox with limits matching agent type."""
        if self._sandbox is None:
            from src.monkey_brain.kernel.execute.sandbox import create_sandbox

            self._sandbox = create_sandbox(self.spec.agent_type or "default")
        return self._sandbox

    async def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        import time

        t0 = time.monotonic()
        self.state = AgentState.RUNNING
        self._steps += 1
        result = {"agent_id": self.id, "role": self.spec.role.value}

        sandbox = self._get_sandbox()
        sandbox._step_count = self._steps
        sandbox._start_time = t0

        # Check timeout
        if sandbox.check_timeout():
            result["success"] = False
            result["error"] = "timeout_exceeded"
            result["sandbox_violations"] = ["timeout_exceeded"]
            self.metrics.tasks_failed += 1
            self.state = AgentState.IDLE
            return result

        # Check step limit
        if sandbox.check_step_limit():
            result["success"] = False
            result["error"] = "step_limit_exceeded"
            result["sandbox_violations"] = ["step_limit_exceeded"]
            self.metrics.tasks_failed += 1
            self.state = AgentState.IDLE
            return result

        try:
            # Enforce the sandbox wall-clock timeout for real — a runaway agent (async) is
            # cancelled at the limit rather than merely failing a point-in-time check.
            import asyncio

            result["output"] = await asyncio.wait_for(self._execute_task(task), timeout=sandbox._limits.timeout_seconds)
            result["success"] = True
            self.metrics.tasks_completed += 1
        except (asyncio.TimeoutError, TimeoutError):
            result["success"] = False
            result["error"] = "timeout_exceeded"
            result["sandbox_violations"] = ["timeout_exceeded"]
            self.metrics.tasks_failed += 1
        except Exception as e:
            result["success"] = False
            result["error"] = str(e)
            self.metrics.tasks_failed += 1
        elapsed = (time.monotonic() - t0) * 1000
        self.metrics.total_latency_ms += elapsed
        self.metrics.avg_latency_ms = self.metrics.total_latency_ms / self._steps
        self.state = AgentState.IDLE
        if self._steps >= self.spec.max_lifetime_steps:
            await self.terminate()
        return result

    async def _execute_task(self, task: dict) -> dict:
        return {
            "task_type": task.get("type", "general"),
            "status": "completed",
            "agent": self.id,
        }

    def sign_contribution(self, contribution: dict[str, Any]) -> dict[str, Any]:
        """Sign a contribution (solution, prediction) with this agent's identity."""
        signature = sign_payload(contribution, self.id)
        return {**contribution, "_agent_id": self.id, "_signature": signature}

    @staticmethod
    def verify_contribution(contribution: dict[str, Any]) -> bool:
        """Verify an agent's signature on a contribution."""
        agent_id = contribution.get("_agent_id", "")
        signature = contribution.get("_signature", "")
        if not agent_id or not signature:
            return False
        payload = {k: v for k, v in contribution.items() if not k.startswith("_")}
        return verify_signature(payload, agent_id, signature)

    async def terminate(self) -> None:
        self.state = AgentState.TERMINATED

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.spec.role.value,
            "state": self.state.value,
            "steps": self._steps,
            "metrics": self.metrics.to_dict(),
        }


class ExecutionPool:
    def __init__(self, shared_knowledge: KnowledgePack | None = None):
        self._agents: dict[str, Agent] = {}
        self._shared_knowledge = shared_knowledge or KnowledgePack()
        self._next_id = 0
        self._spawn_log: list[dict] = []
        self._terminate_log: list[dict] = []
        self._max_log = 1000

    @property
    def active_agents(self) -> list[Agent]:
        return [a for a in self._agents.values() if a.state not in (AgentState.TERMINATED,)]

    @property
    def agent_count(self) -> int:
        return len(self.active_agents)

    def spawn(self, spec: AgentSpec, capability_bus=None) -> Agent:
        self._next_id += 1
        agent_id = f"agent-{self._next_id}"
        if not spec.agent_type:
            spec.agent_type = spec.role.value
        agent = Agent(agent_id, spec, self._shared_knowledge)
        self._agents[agent_id] = agent
        resolved_caps = list(spec.capabilities)
        if capability_bus is not None and spec.required_capabilities:
            for cap_name in spec.required_capabilities:
                cap = capability_bus._capabilities.get(cap_name)
                if cap is not None:
                    resolved_caps.append(cap_name)
                else:
                    logger.warning(
                        "Required capability '%s' not found for agent %s",
                        cap_name,
                        agent_id,
                    )
            spec.capabilities = resolved_caps
        self._spawn_log.append(
            {
                "agent_id": agent_id,
                "role": spec.role.value,
                "capabilities": resolved_caps,
                "required_capabilities": spec.required_capabilities,
            }
        )
        if len(self._spawn_log) > self._max_log:
            self._spawn_log = self._spawn_log[-self._max_log :]
        return agent

    def spawn_for_load(self, count: int = 1) -> list[Agent]:
        return [self.spawn(AgentSpec(role=AgentRole.WORKER)) for _ in range(count)]

    def spawn_for_knowledge(self, domain: str) -> Agent:
        return self.spawn(
            AgentSpec(
                role=AgentRole.SPECIALIST,
                knowledge_domain=domain,
                capabilities=[f"knowledge:{domain}"],
            )
        )

    def spawn_for_solver(self, solver_class: str) -> Agent:
        return self.spawn(
            AgentSpec(
                role=AgentRole.SPECIALIST,
                solver_class=solver_class,
                capabilities=[f"solver:{solver_class}"],
            )
        )

    def spawn_adversarial(self, count: int = 5) -> list[Agent]:
        return [self.spawn(AgentSpec(role=AgentRole.ADVERSARIAL, max_lifetime_steps=20)) for _ in range(count)]

    async def assign_task(self, task: dict[str, Any]) -> dict[str, Any]:
        for agent in self.active_agents:
            if agent.state == AgentState.IDLE:
                if not agent.spec.capabilities or any(
                    c in task.get("required_capabilities", []) for c in agent.spec.capabilities
                ):
                    return await agent.execute(task)
        agent = self.spawn(AgentSpec(role=AgentRole.WORKER))
        return await agent.execute(task)

    async def merge_all_knowledge(self) -> None:
        for agent in self._agents.values():
            if agent.state == AgentState.TERMINATED:
                self._terminate_log.append(
                    {
                        "agent_id": agent.id,
                        "knowledge_produced": agent.metrics.knowledge_produced,
                    }
                )
                if len(self._terminate_log) > self._max_log:
                    self._terminate_log = self._terminate_log[-self._max_log :]

    def get_mesh_state(self) -> dict[str, Any]:
        return {
            "active_agents": self.agent_count,
            "total_spawned": self._next_id,
            "total_terminated": len(self._terminate_log),
            "agents": [a.to_dict() for a in self.active_agents],
            "roles": self._count_by_role(),
            "knowledge_items": self._shared_knowledge.size,
        }

    def _count_by_role(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for agent in self.active_agents:
            counts[agent.spec.role.value] = counts.get(agent.spec.role.value, 0) + 1
        return counts

    def sync_from_bus(self, bus: Any) -> None:
        agent_bus = bus.agent_bus
        for agent_type, bindings in agent_bus._agent_caps.items():
            caps = [b.capability_name for b in bindings]
            providers = list({b.provider_name for b in bindings})
            for agent in self.active_agents:
                if agent.spec.role.value == agent_type or agent_type in agent.spec.capabilities:
                    agent.spec.metadata["bus_capabilities"] = caps
                    agent.spec.metadata["bus_providers"] = providers

    def to_dict(self) -> dict[str, Any]:
        return self.get_mesh_state()
