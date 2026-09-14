"""Epistemic State — multi-dimensional knowledge confidence model.

The transition function becomes:
    (s_t, k_t, a_t) → (s_{t+1}, A_{t+1}, k_{t+1})

where:
    s = world state
    a = action
    A = future action space
    k = epistemic state (what the system knows and how confident it is)

Knowledge Packs become weighted priors.  The simulator predicts Next State
while the Knowledge Pack predicts Expected State.  If they disagree,
knowledge loss increases.  If they agree, knowledge confidence increases.

Confidence is not a single number — it is a multi-dimensional vector:

    C_knowledge = f(P, S, F, Co, V, Sim)

where:
    P = Provenance      — source reliability (0.98 for ISO SOP, 0.31 for random blog)
    S = Semantic consistency — internal coherence with other knowledge
    F = Freshness       — temporal relevance (decays over time)
    Co = Completeness   — coverage of the domain
    V = External validation — verified by independent sources
    Sim = Simulation success — historically accurate predictions
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class KnowledgeConfidence:
    """Multi-dimensional confidence vector for a knowledge source.

    Each dimension ∈ [0, 1].  Composite confidence is a weighted geometric mean.
    """

    provenance: float = 0.5  # source reliability
    semantic_consistency: float = 0.5  # internal coherence
    freshness: float = 0.5  # temporal relevance
    completeness: float = 0.5  # domain coverage
    validation: float = 0.5  # external verification
    simulation_success: float = 0.5  # historical accuracy

    # Weight vector — how much each dimension matters
    _weights: dict[str, float] = field(
        default_factory=lambda: {
            "provenance": 0.25,
            "semantic_consistency": 0.20,
            "freshness": 0.15,
            "completeness": 0.15,
            "validation": 0.15,
            "simulation_success": 0.10,
        }
    )

    @property
    def composite(self) -> float:
        """Weighted geometric mean of all dimensions.

        Geometric mean penalises low dimensions more than arithmetic mean —
        a knowledge pack with freshness=0.1 cannot compensate by having
        provenance=1.0.
        """
        dims = {
            "provenance": max(self.provenance, 1e-9),
            "semantic_consistency": max(self.semantic_consistency, 1e-9),
            "freshness": max(self.freshness, 1e-9),
            "completeness": max(self.completeness, 1e-9),
            "validation": max(self.validation, 1e-9),
            "simulation_success": max(self.simulation_success, 1e-9),
        }
        log_sum = sum(w * math.log(d) for dim, d in dims.items() if (w := self._weights.get(dim, 0.1)) > 0)
        return math.exp(log_sum)

    @property
    def knowledge_loss(self) -> float:
        """L_knowledge = 1 - C_knowledge.

        High loss means the system is uncertain about this knowledge.
        """
        return 1.0 - self.composite

    def decay(self, half_life_steps: int = 100, steps: int = 1) -> KnowledgeConfidence:
        """Decay freshness over time.  Returns a new instance."""
        decay_factor = 0.5 ** (steps / half_life_steps)
        return KnowledgeConfidence(
            provenance=self.provenance,
            semantic_consistency=self.semantic_consistency,
            freshness=self.freshness * decay_factor,
            completeness=self.completeness,
            validation=self.validation,
            simulation_success=self.simulation_success,
            _weights=self._weights,
        )

    def boost(self, dimension: str, amount: float = 0.1) -> KnowledgeConfidence:
        """Boost a specific dimension (e.g. after a successful simulation)."""
        current = getattr(self, dimension, 0.5)
        new_val = min(1.0, current + amount)
        return KnowledgeConfidence(
            provenance=self.provenance,
            semantic_consistency=self.semantic_consistency,
            freshness=self.freshness,
            completeness=self.completeness,
            validation=self.validation,
            simulation_success=self.simulation_success,
            _weights=self._weights,
        ).replace(dimension, new_val)

    def replace(self, dimension: str, value: float) -> KnowledgeConfidence:
        """Return a copy with one dimension replaced."""
        return KnowledgeConfidence(
            **{
                "provenance": self.provenance,
                "semantic_consistency": self.semantic_consistency,
                "freshness": self.freshness,
                "completeness": self.completeness,
                "validation": self.validation,
                "simulation_success": self.simulation_success,
                "_weights": self._weights,
                dimension: value,
            }
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "provenance": self.provenance,
            "semantic_consistency": self.semantic_consistency,
            "freshness": self.freshness,
            "completeness": self.completeness,
            "validation": self.validation,
            "simulation_success": self.simulation_success,
            "composite": self.composite,
            "knowledge_loss": self.knowledge_loss,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> KnowledgeConfidence:
        return cls(
            provenance=d.get("provenance", 0.5),
            semantic_consistency=d.get("semantic_consistency", 0.5),
            freshness=d.get("freshness", 0.5),
            completeness=d.get("completeness", 0.5),
            validation=d.get("validation", 0.5),
            simulation_success=d.get("simulation_success", 0.5),
        )


@dataclass
class BeliefState:
    """DEPRECATED — use cortex.epistemic.BeliefState instead.

    Scheduled for deletion. cortex.epistemic.BeliefState is the canonical implementation
    (richer: list[KnowledgeItem], UncertaintyEstimate decomposition, SimulationOutcome.update).
    This copy remains only for backward compat with kernel/rl/* internal modules.

    B_t = (K, C, U) — the belief state.

    Knowledge        — what the system knows
    Confidence       — how sure it is (multi-dimensional)
    Uncertainty      — what it doesn't know

    This is the Bayesian filtering formulation.
    Beliefs are updated by evidence, not by assertion.
    """

    knowledge: dict[str, Any] = field(default_factory=dict)  # K_t
    confidence: KnowledgeConfidence = field(default_factory=KnowledgeConfidence)  # C_t
    uncertainty: dict[str, float] = field(default_factory=dict)  # U_t — per-dimension uncertainty

    @property
    def composite_confidence(self) -> float:
        return self.confidence.composite

    @property
    def composite_uncertainty(self) -> float:
        """Mean uncertainty across all dimensions."""
        if not self.uncertainty:
            return 1.0 - self.composite_confidence
        return sum(self.uncertainty.values()) / len(self.uncertainty)

    def update_from_evidence(self, evidence: dict[str, Any]) -> BeliefState:
        """Bayesian update: P(B|e) ∝ P(e|B) * P(B)."""
        new_knowledge = {**self.knowledge, **evidence}
        new_confidence = self.confidence  # updated by caller via boost/decay
        new_uncertainty = dict(self.uncertainty)
        for key in evidence:
            if key in new_uncertainty:
                new_uncertainty[key] *= 0.7  # evidence reduces uncertainty
        return BeliefState(
            knowledge=new_knowledge,
            confidence=new_confidence,
            uncertainty=new_uncertainty,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "knowledge": self.knowledge,
            "confidence": self.confidence.to_dict(),
            "uncertainty": self.uncertainty,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BeliefState:
        return cls(
            knowledge=d.get("knowledge", {}),
            confidence=KnowledgeConfidence.from_dict(d.get("confidence", {})),
            uncertainty=d.get("uncertainty", {}),
        )


@dataclass
class GoalState:
    """G_t — active intent / objective.

    The same world state evolves differently depending on the current goal.
    A robot navigating to charge behaves differently from one inspecting equipment,
    even when S_t is identical.
    """

    goal: str = ""
    sub_goals: list[str] = field(default_factory=list)
    priority: float = 0.5
    deadline_steps: int = -1  # -1 = no deadline
    constraints: list[str] = field(default_factory=list)
    success_criteria: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return bool(self.goal)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "sub_goals": self.sub_goals,
            "priority": self.priority,
            "constraints": self.constraints,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GoalState:
        return cls(
            goal=d.get("goal", ""),
            sub_goals=d.get("sub_goals", []),
            priority=d.get("priority", 0.5),
            constraints=d.get("constraints", []),
        )


@dataclass
class EpistemicState:
    """DEPRECATED — use EpistemicPredictiveState from cortex.epa instead.

    Scheduled for deletion. EpistemicPredictiveState is the canonical cognitive state.
    This copy remains only for backward compat with LossEngine, SimulationEngine,
    and kernel/rl/* internal modules during the migration period.

    E_t = (S_t, B_t, A_t, M_t) — the complete cognitive state.

    Universal latent dynamics model:
        f(E_t, G_t, a_t) → E_{t+1}

    Every action updates:
        S  — the world
        B  — the beliefs (knowledge + confidence + uncertainty)
        A  — the affordances (available actions)
        M  — the mesh (agent organization)

    Conditioned on G_t (active goal).
    """

    world: dict[str, Any] = field(default_factory=dict)  # S_t
    belief: BeliefState = field(default_factory=BeliefState)  # B_t = (K, C, U)
    affordances: list[str] = field(default_factory=list)  # A_t — available actions
    mesh: dict[str, Any] = field(default_factory=dict)  # M_t — agent organization
    goal: GoalState = field(default_factory=GoalState)  # G_t — active intent

    # Convenience accessors
    @property
    def knowledge(self) -> dict[str, Any]:
        return self.belief.knowledge

    @property
    def confidence(self) -> float:
        return self.belief.composite_confidence

    @property
    def uncertainty(self) -> float:
        return self.belief.composite_uncertainty

    @property
    def knowledge_loss(self) -> float:
        return 1.0 - self.confidence

    def record_prediction(self, predicted: dict, actual: dict) -> float:
        """Record prediction error and update belief confidence."""
        error = _state_distance(predicted, actual)
        if error < 0.1:
            self.belief.confidence = self.belief.confidence.replace(
                "simulation_success",
                min(1.0, self.belief.confidence.simulation_success + 0.05),
            )
        elif error > 0.5:
            self.belief.confidence = self.belief.confidence.replace(
                "simulation_success",
                max(0.0, self.belief.confidence.simulation_success - 0.1),
            )
        return error

    def step(self) -> EpistemicState:
        """Advance one time step — decay belief freshness."""
        decayed = self.belief.confidence.decay(steps=1)
        return EpistemicState(
            world=dict(self.world),
            belief=BeliefState(
                knowledge=dict(self.belief.knowledge),
                confidence=decayed,
                uncertainty=dict(self.belief.uncertainty),
            ),
            affordances=list(self.affordances),
            mesh=dict(self.mesh),
            goal=GoalState(
                goal=self.goal.goal,
                sub_goals=list(self.goal.sub_goals),
                priority=self.goal.priority,
                constraints=list(self.goal.constraints),
            ),
        )

    def apply_capability_effects(
        self,
        world_changes: dict[str, Any] | None = None,
        knowledge_gained: dict[str, Any] | None = None,
        confidence_delta: float = 0.0,
        new_actions: list[str] | None = None,
        removed_actions: list[str] | None = None,
    ) -> EpistemicState:
        """Apply four-dimensional capability effects to epistemic state."""
        new_world = dict(self.world)
        if world_changes:
            new_world.update(world_changes)

        new_belief = BeliefState(
            knowledge={**self.belief.knowledge, **(knowledge_gained or {})},
            confidence=self.belief.confidence.replace(
                "simulation_success",
                max(
                    0.0,
                    min(
                        1.0,
                        self.belief.confidence.simulation_success + confidence_delta,
                    ),
                ),
            ),
            uncertainty=dict(self.belief.uncertainty),
        )

        new_affordances = list(self.affordances)
        if new_actions:
            new_affordances.extend(new_actions)
        if removed_actions:
            new_affordances = [a for a in new_affordances if a not in removed_actions]

        return EpistemicState(
            world=new_world,
            belief=new_belief,
            affordances=new_affordances,
            mesh=dict(self.mesh),
            goal=self.goal,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "world": self.world,
            "belief": self.belief.to_dict(),
            "affordances": self.affordances,
            "mesh": self.mesh,
            "goal": self.goal.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EpistemicState:
        return cls(
            world=d.get("world", {}),
            belief=BeliefState.from_dict(d.get("belief", {})),
            affordances=d.get("affordances", []),
            mesh=d.get("mesh", {}),
            goal=GoalState.from_dict(d.get("goal", {})),
        )


@dataclass
class EpistemicTransition:
    """Extended transition: (s, k, a) → (s', A', k').

    Carries both world-state and epistemic-state through the transition.
    """

    state: dict[str, Any] = field(default_factory=dict)
    epistemic: EpistemicState = field(default_factory=EpistemicState)
    action: str = ""
    reward: float = 0.0
    next_state: dict[str, Any] = field(default_factory=dict)
    next_epistemic: EpistemicState = field(default_factory=EpistemicState)
    next_actions: list[str] = field(default_factory=list)  # A_{t+1}
    done: bool = False
    knowledge_loss: float = 0.0
    world_loss: float = 0.0
    total_loss: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "epistemic": self.epistemic.to_dict(),
            "action": self.action,
            "reward": self.reward,
            "next_state": self.next_state,
            "next_epistemic": self.next_epistemic.to_dict(),
            "next_actions": self.next_actions,
            "done": self.done,
            "knowledge_loss": self.knowledge_loss,
            "world_loss": self.world_loss,
            "total_loss": self.total_loss,
        }


def _state_distance(s1: dict, s2: dict) -> float:
    """Compute normalised distance between two state dicts.

    Returns 0.0 (identical) to 1.0 (completely different).
    """
    if not s1 and not s2:
        return 0.0
    all_keys = set(s1.keys()) | set(s2.keys())
    if not all_keys:
        return 0.0
    matches = sum(1 for k in all_keys if s1.get(k) == s2.get(k))
    return 1.0 - (matches / len(all_keys))


def knowledge_loss(predicted_state: dict, actual_state: dict, epistemic: EpistemicState) -> float:
    """Compute knowledge loss from prediction error and epistemic confidence.

    L_knowledge = g(1-P, 1-F, 1-C, 1-V) * world_error

    High epistemic uncertainty amplifies world error.
    High epistemic confidence dampens world error.
    """
    world_error = _state_distance(predicted_state, actual_state)
    k_conf = epistemic.confidence

    # Knowledge loss is world error weighted by epistemic uncertainty
    # If confidence is high (1.0), knowledge_loss = world_error
    # If confidence is low (0.0), knowledge_loss = world_error * 2 (capped at 1.0)
    return min(1.0, world_error * (2.0 - k_conf))


def total_loss(
    world_loss: float,
    knowledge_loss_val: float,
    world_weight: float = 0.6,
    knowledge_weight: float = 0.4,
) -> float:
    """Combined loss: L_total = w_world * L_world + w_knowledge * L_knowledge."""
    return world_weight * world_loss + knowledge_weight * knowledge_loss_val
