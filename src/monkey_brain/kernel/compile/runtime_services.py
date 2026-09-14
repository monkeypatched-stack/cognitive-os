"""RuntimeServices — unified runtime services for the Cognitive Platform.

Orchestrates:
- Planner: pathfinding through world + belief + Φ
- Learning: Bellman updates, policy optimization
- Prediction: cloned world simulation
- Trust: observation weighting, reputation
- Memory: event history, recall, persistence
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("agentos.runtime_services")


@dataclass
class PlanResult:
    """Result of planning."""

    start_state: str
    goal_state: str
    path: list[str]
    confidence: float
    steps: int


@dataclass
class PredictionResult:
    """Result of prediction on cloned world."""

    predicted_state: str
    confidence: float
    steps_simulated: int


class RuntimeServices:
    """Unified runtime services for the Cognitive Platform.

    Provides Planner, Learning, Prediction, Trust, and Memory
    as cohesive services that operate on actor state.
    """

    def __init__(self, event_bus: Any = None, trust_runtime: Any = None) -> None:
        self._event_bus = event_bus
        self._planner = PlannerService(event_bus)
        self._learning = LearningService(event_bus)
        self._prediction = PredictionService(event_bus)
        self._trust = TrustService(event_bus, trust_runtime=trust_runtime)
        self._memory = MemoryService(event_bus)

    @property
    def planner(self) -> PlannerService:
        return self._planner

    @property
    def learning(self) -> LearningService:
        return self._learning

    @property
    def prediction(self) -> PredictionService:
        return self._prediction

    @property
    def trust(self) -> TrustService:
        return self._trust

    @property
    def memory(self) -> MemoryService:
        return self._memory

    def summary(self) -> dict:
        return {
            "planner": self._planner.summary(),
            "learning": self._learning.summary(),
            "prediction": self._prediction.summary(),
            "trust": self._trust.summary(),
            "memory": self._memory.summary(),
        }


class PlannerService:
    """Pathfinding through world + belief + Φ + constraints.

    Uses A* search with Bellman-guided heuristic when belief/Φ are available.
    Falls back to BFS when only the world graph is provided.
    """

    def __init__(self, event_bus: Any = None) -> None:
        self._event_bus = event_bus
        self._plans_computed = 0

    def plan(
        self,
        start: str,
        goal: str,
        world: Any,
        belief: Any = None,
        phi: Any = None,
        max_steps: int = 64,
    ) -> PlanResult:
        """Plan a path from start to goal using A* with Bellman heuristic."""
        import heapq

        self._plans_computed += 1

        # A* search with priority queue
        # Priority = g(n) + h(n) where g = steps so far, h = heuristic
        counter = 0  # tie-breaker for heap
        open_set = [(0, counter, start, [start])]
        visited: dict[str, float] = {}

        while open_set:
            f_score, _, current, path = heapq.heappop(open_set)

            if current == goal:
                confidence = self._compute_confidence(path, world, belief, phi)
                result = PlanResult(
                    start_state=start,
                    goal_state=goal,
                    path=path,
                    confidence=confidence,
                    steps=len(path) - 1,
                )
                if self._event_bus:
                    self._event_bus.publish("plan.ready", {"path": path}, source="planner")
                return result

            if len(path) > max_steps:
                continue

            g = len(path) - 1
            if current in visited and visited[current] <= g:
                continue
            visited[current] = g

            # Get successors from world graph + Φ
            successors = self._get_successors(current, world, phi)

            for dst, edge_weight in successors:
                if dst not in visited or visited.get(dst, float("inf")) > g + 1:
                    h = self._heuristic(dst, goal, belief, phi)
                    f = g + 1 + h
                    counter += 1
                    heapq.heappush(open_set, (f, counter, dst, path + [dst]))

        return PlanResult(start_state=start, goal_state=goal, path=[start], confidence=0.0, steps=0)

    def _get_successors(self, state: str, world: Any, phi: Any = None) -> list[tuple[str, float]]:
        """Get successors from world graph and Φ, merged and deduplicated."""
        successors: dict[str, float] = {}

        # World graph successors
        if world and hasattr(world, "successors"):
            for dst in world.successors(state):
                if isinstance(dst, tuple):
                    dst = dst[0]
                successors[dst] = max(successors.get(dst, 0.0), 0.8)

        # Φ successors (higher priority)
        if phi and hasattr(phi, "successors"):
            for dst in phi.successors(state):
                if isinstance(dst, tuple):
                    dst = dst[0]
                successors[dst] = max(successors.get(dst, 0.0), 0.9)

        return list(successors.items())

    def _heuristic(self, state: str, goal: str, belief: Any = None, phi: Any = None) -> float:
        """Heuristic: estimate cost from state to goal using belief/Φ knowledge."""
        # If belief has seen this transition, lower heuristic
        if belief and hasattr(belief, "_beliefs"):
            if (state, goal) in belief._beliefs:
                return 0.1
            # Check if any known successor leads toward goal
            for s, d in belief._beliefs:
                if s == state and d == goal:
                    return 0.2

        # If Φ has this transition
        if phi and hasattr(phi, "successors"):
            phi_succ = phi.successors(state)
            if goal in [s[0] if isinstance(s, tuple) else s for s in phi_succ]:
                return 0.1

        # Default: Manhattan-like distance estimate
        return 0.5

    def _compute_confidence(self, path: list[str], world: Any, belief: Any = None, phi: Any = None) -> float:
        """Compute confidence based on world transitions, beliefs, and Φ."""
        if len(path) < 2:
            return 0.5

        scores = []
        for i in range(len(path) - 1):
            src, dst = path[i], path[i + 1]

            # World transition weight
            if world and hasattr(world, "successors"):
                successors = world.successors(src)
                if dst in [s[0] if isinstance(s, tuple) else s for s in successors]:
                    scores.append(0.8)
                else:
                    scores.append(0.3)
            else:
                scores.append(0.5)

            # Belief confidence
            if belief and hasattr(belief, "_beliefs"):
                belief_weight = belief._beliefs.get((src, dst), 0.0)
                scores.append(min(1.0, 0.5 + belief_weight * 0.5))

            # Φ weight
            if phi and hasattr(phi, "successors"):
                phi_successors = phi.successors(src)
                if dst in [s[0] if isinstance(s, tuple) else s for s in phi_successors]:
                    scores.append(0.9)

        return sum(scores) / len(scores) if scores else 0.5

    def summary(self) -> dict:
        return {"plans_computed": self._plans_computed}


class LearningService:
    """Bellman updates, policy optimization."""

    def __init__(self, event_bus: Any = None) -> None:
        self._event_bus = event_bus
        self._updates = 0

    def bellman_update(
        self,
        belief: Any,
        state: str,
        action: str,
        reward: float,
        next_state: str,
        lr: float = 0.1,
        discount: float = 0.95,
    ) -> float:
        """Update Bellman policy. Returns new Q-value."""
        if belief is None:
            return 0.5

        if hasattr(belief, "update_bellman"):
            belief.update_bellman(state, action, reward, next_state, lr, discount)
            self._updates += 1
            q = belief.q_value(state, action)
            if self._event_bus:
                self._event_bus.publish(
                    "bellman.updated",
                    {"state": state, "action": action, "q": q},
                    source="learning",
                )
            return q

        return 0.5

    def compile_phi(self, belief: Any) -> Any:
        """Compile Bellman policy into sparse transition operator Φ."""
        if belief is None:
            return None
        if hasattr(belief, "compile_phi"):
            phi = belief.compile_phi()
            if phi and self._event_bus:
                self._event_bus.publish(
                    "phi.compiled",
                    {"entries": phi.nnz() if hasattr(phi, "nnz") else 0},
                    source="learning",
                )
            return phi
        return None

    def summary(self) -> dict:
        return {"bellman_updates": self._updates}


class PredictionService:
    """Prediction on cloned worlds."""

    def __init__(self, event_bus: Any = None) -> None:
        self._event_bus = event_bus
        self._predictions = 0

    def predict(self, gpu_world: Any, start_state: str, phi: Any = None, max_steps: int = 32) -> PredictionResult:
        """Predict next state by simulating on a cloned world."""
        if gpu_world is None:
            return PredictionResult(predicted_state=start_state, confidence=0.0, steps_simulated=0)

        cloned = gpu_world.clone() if hasattr(gpu_world, "clone") else gpu_world
        current = start_state
        self._predictions += 1

        for i in range(max_steps):
            successors = cloned.successors(current) if hasattr(cloned, "successors") else []
            if not successors:
                break
            current = successors[0] if isinstance(successors[0], str) else successors[0][0]

        result = PredictionResult(predicted_state=current, confidence=0.7, steps_simulated=i + 1)
        if self._event_bus:
            self._event_bus.publish(
                "prediction.ready",
                {"predicted": current, "steps": i + 1},
                source="prediction",
            )
        return result

    def summary(self) -> dict:
        return {"predictions": self._predictions}


class TrustService:
    """Trust weighting, reputation.

    Delegates to TrustRuntime when available for full trust graph,
    decay, and propagation. Falls back to simple dict when standalone.
    """

    def __init__(self, event_bus: Any = None, trust_runtime: Any = None) -> None:
        self._event_bus = event_bus
        self._trust_runtime = trust_runtime
        self._scores: dict[str, float] = {}

    def weight(self, source: str, base_weight: float = 1.0) -> float:
        if self._trust_runtime and hasattr(self._trust_runtime, "weight_observation"):
            return self._trust_runtime.weight_observation(base_weight, source)
        trust = self._scores.get(source, 0.5)
        return base_weight * trust

    def update(self, source: str, score: float) -> None:
        if self._trust_runtime and hasattr(self._trust_runtime, "learn_from_outcome"):
            self._trust_runtime.learn_from_outcome(source, outcome={"status": "good", "confidence": score})
        self._scores[source] = max(0.0, min(1.0, score))

    def score(self, source: str) -> float:
        if self._trust_runtime and hasattr(self._trust_runtime, "trust"):
            return self._trust_runtime.trust(source)
        return self._scores.get(source, 0.5)

    def summary(self) -> dict:
        return {
            "tracked_sources": len(self._scores),
            "has_trust_runtime": self._trust_runtime is not None,
        }


class MemoryService:
    """Event history, recall, persistence."""

    def __init__(self, event_bus: Any = None) -> None:
        self._event_bus = event_bus
        self._entries: list[dict] = []
        self._max_entries = 10000

    def store(self, entry: dict) -> None:
        entry["timestamp"] = time.time()
        self._entries.append(entry)
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries :]

    def recall(self, query: str, limit: int = 5) -> list[dict]:
        return [e for e in self._entries if query.lower() in str(e).lower()][-limit:]

    def recent(self, limit: int = 10) -> list[dict]:
        return self._entries[-limit:]

    def summary(self) -> dict:
        return {"entries": len(self._entries)}
