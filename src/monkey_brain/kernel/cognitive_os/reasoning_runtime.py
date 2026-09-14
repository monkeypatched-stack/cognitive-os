"""ReasoningRuntime — the Reasoning half of a genuinely decoupled
CognitiveOS split (Observe->Believe->Plan->Predict), owned by the actor's
cognitive engine (ComparisonIntegratedPolicy) and exposed via
CognitiveOS.reasoning.

Fully decoupled from ExecutionRuntime: `reason(state)` runs ONLY the
observe/believe/plan/predict stage functions and returns the resulting
CognitiveState (with `plan`/`prediction_result`/`belief` populated) as its
real output — ExecutionRuntime.execute() consumes that state as its input.
Neither side shares a live internal engine object; the only handoff is the
CognitiveState itself, which kernel/pipeline/execution_state.py already
defines with exactly the fields this handoff needs.

Decision Engine: evaluate_goals/match_capabilities/check_resources/
synthesize are the four methods CognitiveOS used to own directly (dead code
in the tick pipeline — the real Plan/Predict stages never called them).
Moved here verbatim (same dataclasses, same logic) since this is genuinely
a Reasoning responsibility; CognitiveOS keeps one-line delegating wrappers
for backward compatibility (tests import these dataclasses and call these
methods directly on a CognitiveOS instance with no engine/pipeline
involved at all).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.monkey_brain.kernel.pipeline.cognitive_policy import StageFn, run_stages
from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState

logger = logging.getLogger("agentos.cognitive_os.reasoning_runtime")


@dataclass
class GoalEvaluation:
    """Result of evaluating a goal against current beliefs and capabilities."""

    goal_type_id: str
    achievable: bool
    blockers: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    required_resources: tuple[str, ...] = ()
    confidence: float = 0.0  # how confident we are this goal can be achieved


@dataclass
class CapabilityMatch:
    """Result of matching a capability to a goal."""

    capability_type_id: str
    goal_type_id: str
    proficiency: float = 0.0
    available: bool = True
    blockers: tuple[str, ...] = ()


@dataclass
class ResourceCheck:
    """Result of checking if a resource is available."""

    resource_type_id: str
    available: bool
    quantity: float = 0.0
    required: float = 0.0
    deficit: float = 0.0


@dataclass
class DecisionSynthesis:
    """Result of synthesizing ontology types into a decision."""

    selected_goal: str | None = None
    selected_capabilities: tuple[str, ...] = ()
    required_resources: tuple[str, ...] = ()
    trust_actions: tuple[str, ...] = ()  # trust updates needed
    confidence: float = 0.0
    reasoning: str = ""


_GOAL_CAPABILITY_MAP = {
    "wealth": ["investment", "accounting", "analysis"],
    "safety": ["reasoning", "planning", "communication"],
    "health": ["caregiving", "diagnosis", "treatment"],
    "mastery": ["teaching", "research", "analysis"],
    "accomplishment": ["planning", "coding", "automation"],
    "expression": ["writing", "design", "communication"],
    "discovery": ["research", "analysis", "data_processing"],
    "order": ["leadership", "coordination", "negotiation"],
    "legacy": ["leadership", "teaching", "innovation"],
}


class DecisionEngine:
    """Which goals are achievable, which capabilities can help, what
    resources are needed, and the resulting decision — actor-scoped (reads
    goal_states/beliefs/capabilities/resources/affiliations directly off a
    bound actor), so it's bound lazily via bind_actor() rather than
    constructed with one, matching CognitiveOS's own bind-once contract."""

    def __init__(self, actor: Any = None) -> None:
        self._actor = actor

    def bind_actor(self, actor: Any) -> None:
        self._actor = actor

    def evaluate_goals(self) -> list[GoalEvaluation]:
        """Evaluate which goals are achievable given current beliefs and capabilities."""
        if self._actor is None:
            return []

        results = []
        goal_states = getattr(self._actor, "goal_states", [])
        beliefs = getattr(self._actor, "beliefs", [])
        capabilities = getattr(self._actor, "capabilities", [])
        resources = getattr(self._actor, "resources", [])

        for goal in goal_states:
            if not goal.active:
                continue

            blockers = []
            req_caps = []
            req_resources = []
            confidence = 0.5

            cap_matches = self._match_for_goal(goal.goal_type_id, capabilities)
            for match in cap_matches:
                req_caps.append(match.capability_type_id)
                if not match.available:
                    blockers.append(f"capability_unavailable:{match.capability_type_id}")
                else:
                    confidence = max(confidence, match.proficiency)

            for res in resources:
                if res.quantity > 0:
                    confidence = max(confidence, 0.3)

            if not beliefs:
                blockers.append("no_beliefs")

            results.append(
                GoalEvaluation(
                    goal_type_id=goal.goal_type_id,
                    achievable=len(blockers) == 0,
                    blockers=tuple(blockers),
                    required_capabilities=tuple(req_caps),
                    required_resources=tuple(req_resources),
                    confidence=confidence,
                )
            )

        return results

    def _match_for_goal(self, goal_type_id: str, capabilities: list) -> list[CapabilityMatch]:
        """Match capabilities to a goal type."""
        required = _GOAL_CAPABILITY_MAP.get(goal_type_id, [])
        matches = []
        for cap_type in required:
            available = any(c.capability_type_id == cap_type and c.available for c in capabilities)
            proficiency = max(
                (c.proficiency for c in capabilities if c.capability_type_id == cap_type),
                default=0.0,
            )
            matches.append(
                CapabilityMatch(
                    capability_type_id=cap_type,
                    goal_type_id=goal_type_id,
                    proficiency=proficiency,
                    available=available,
                )
            )
        return matches

    def match_capabilities(self) -> list[CapabilityMatch]:
        """Match all capabilities to all active goals."""
        if self._actor is None:
            return []

        goals = getattr(self._actor, "goal_states", [])
        capabilities = getattr(self._actor, "capabilities", [])

        matches = []
        for goal in goals:
            if goal.active:
                matches.extend(self._match_for_goal(goal.goal_type_id, capabilities))
        return matches

    def check_resources(self, required: dict[str, float] = None) -> list[ResourceCheck]:
        """Check if the actor has required resources."""
        if self._actor is None:
            return []

        if required is None:
            required = {}

        resources = getattr(self._actor, "resources", [])
        results = []

        for res_type, req_qty in required.items():
            available = sum(r.quantity for r in resources if r.resource_type_id == res_type)
            deficit = max(0.0, req_qty - available)
            results.append(
                ResourceCheck(
                    resource_type_id=res_type,
                    available=available >= req_qty,
                    quantity=available,
                    required=req_qty,
                    deficit=deficit,
                )
            )

        return results

    def synthesize(self) -> DecisionSynthesis:
        """Synthesize goal evaluation, capability matching, resource checking,
        and trust assessment into a coherent decision."""
        if self._actor is None:
            return DecisionSynthesis(reasoning="No actor bound")

        goal_evals = self.evaluate_goals()
        cap_matches = self.match_capabilities()

        achievable = [g for g in goal_evals if g.achievable]
        if not achievable:
            return DecisionSynthesis(
                reasoning="No achievable goals",
                confidence=0.0,
            )

        best_goal = min(
            achievable,
            key=lambda g: (
                -g.confidence,
                (
                    getattr(self._actor, "_goal_states", [])[0].priority
                    if getattr(self._actor, "_goal_states", [])
                    else 50
                ),
            ),
        )

        needed_caps = [
            m.capability_type_id for m in cap_matches if m.goal_type_id == best_goal.goal_type_id and m.available
        ]

        trust_actions = []
        affiliations = getattr(self._actor, "_affiliations", [])
        if affiliations:
            for aff in getattr(affiliations, "_affiliations", {}).values():
                trust = affiliations.get_trust(aff.target_id)
                if trust < 0.5:
                    trust_actions.append(f"build_trust:{aff.target_id}")

        return DecisionSynthesis(
            selected_goal=best_goal.goal_type_id,
            selected_capabilities=tuple(needed_caps),
            trust_actions=tuple(trust_actions),
            confidence=best_goal.confidence,
            reasoning=f"Selected {best_goal.goal_type_id} (confidence={best_goal.confidence:.2f})",
        )


class ReasoningRuntime:
    """Observe->Believe->Plan->Predict, owned by the actor's cognitive
    engine (ComparisonIntegratedPolicy.configure()). Produces a CognitiveState
    carrying the plan and a genuine blind prediction — ExecutionRuntime
    consumes it, with no engine object shared between the two."""

    def __init__(
        self,
        stages: list[tuple[str, StageFn]],
        *,
        planning_engine: Any = None,
        transition_model: Any = None,
        learning_policy: Any = None,
        society_activation: Any = None,
    ) -> None:
        self._stages = stages
        self.planning = planning_engine
        self.prediction = transition_model
        self.learning_policy = learning_policy
        self.decision_engine = DecisionEngine()
        self._actor: Any = None
        self._society_activation = society_activation
        """Society as Organizational Context refactor: an optional
        kernel/society/activation.py::SocietyActivationEngine. When set,
        reason() splices one activation call between the 'believe' and
        'plan' stages — "ReasoningRuntime becomes responsible for society
        activation," per spec, with zero changes to CognitivePolicy.
        configure()'s signature. When None (the default — most existing/
        test call sites), reason() behaves exactly as before this refactor."""

    def bind_actor(self, actor: Any) -> None:
        """Attach the CognitiveOS-bound actor so decision_engine/world_model
        can read its goal/belief/capability/resource state. Called by
        CognitiveOS.reasoning on each access — safe because each
        CognitiveActor gets its own private engine by default."""
        self._actor = actor
        self.decision_engine.bind_actor(actor)

    @property
    def world_model(self) -> Any:
        """The actor's own private belief/world state (SparseTransitionTensor)
        — distinct from the Planetary WorldModelRuntime, which an actor only
        ever reads through a read-only view, never owns."""
        if self._actor is None:
            return None
        return getattr(self._actor, "belief", None)

    @property
    def counterfactuals(self) -> Any:
        """The real, tick-integrated CounterfactualEngine — constructed
        lazily since it needs the transition model, which may be updated
        across ticks."""
        from src.monkey_brain.kernel.pipeline.prediction.counterfactuals import (
            CounterfactualEngine,
        )

        return CounterfactualEngine(self.prediction)

    async def reason(self, state: CognitiveState) -> CognitiveState:
        """Run Observe->Believe->Plan->Predict and return the resulting
        state — the real, produced output ExecutionRuntime.execute()
        consumes as input.

        When a SocietyActivationEngine is bound, splices one activation
        call between 'believe' (the goal is now known — belief_runtime.py's
        _update_beliefs sets it) and 'plan' (the first stage that could use
        activated societies' merged policies as constraints), storing the
        SocietyActivationResult on state.belief.metadata["activated_societies"]
        — the same metadata-passing convention already used for
        transition_model. No activation engine bound: identical to the
        pre-refactor single run_stages() call, byte-for-byte."""
        if self._society_activation is None or self._actor is None:
            return await run_stages(state, self._stages)

        believe_index = next((i for i, (name, _) in enumerate(self._stages) if name == "believe"), None)
        if believe_index is None:
            return await run_stages(state, self._stages)

        state = await run_stages(state, self._stages[: believe_index + 1])
        actor_id = getattr(self._actor, "entity_id", None) or getattr(self._actor, "id", None)
        goal = state.belief.goal
        goal_text = f"{goal.name} {goal.description}".strip()
        if actor_id:
            try:
                result = self._society_activation.activate_for_goal(actor_id, goal_text)
                state.belief.metadata["activated_societies"] = result
            except Exception as e:
                logger.error("Society activation failed for actor %s: %s", actor_id, e)
        return await run_stages(state, self._stages[believe_index + 1 :])
