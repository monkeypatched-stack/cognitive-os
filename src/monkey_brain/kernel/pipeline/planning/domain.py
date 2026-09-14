"""Planning domain model (Step 8.1) — formal, algorithm-independent planning types.

This module defines WHAT a plan, goal, operator, and constraint are. It does not
decide HOW to plan — no decomposition, candidate generation, scoring, or execution
logic lives here (that's Steps 8.2-8.8). All types are immutable value objects.

Relationship to existing types:
    belief_state.Goal / Plan / PlanStep are the lightweight, informal shapes
    LLMPlanner (llm_planner.py) produces today. The types here are
    richer, formal versions of the same concepts, intentionally coexisting rather
    than replacing them — that integration is Step 8.7's job (via PlanningEngine),
    not a rewrite of belief_state.py. Import whichever you need explicitly; if a
    call site needs both, alias one (the same pattern already used elsewhere in
    this package for the two same-named CognitiveRuntime classes).

    contracts.RuntimeContext is a bag of booted infrastructure handles (world,
    actor, capabilities, ...). PlanningContext below is a different thing: the
    planning phase's own working set (goal, subgoals, available operators,
    constraints) — not a general-purpose runtime handle bag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class ValidationStatus(Enum):
    """Coarse validation state of a Plan.

    Deliberately lightweight — the full validation *report* (violations, per-
    constraint reasons) is Step 8.4's deliverable (a Constraint evaluator +
    Validation report), not this step's.
    """

    UNVALIDATED = "unvalidated"
    VALID = "valid"
    INVALID = "invalid"


@dataclass(frozen=True)
class Goal:
    """A top-level objective the planner is trying to achieve."""

    goal_id: str = field(default_factory=lambda: uuid4().hex)
    name: str = ""
    description: str = ""
    completion_criteria: tuple[str, ...] = ()
    priority: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SubGoal:
    """A decomposition of a Goal into a smaller objective.

    depends_on holds the goal_ids of sibling SubGoals that must complete first
    (e.g. "buy eggs" and "buy bread" may both be independent of each other but
    depend on nothing; a hypothetical "bake bread" subgoal would depend on
    "buy flour"). Step 8.3's decomposition engine populates these; this module
    only defines the shape.
    """

    goal_id: str = field(default_factory=lambda: uuid4().hex)
    parent_goal_id: str = ""
    name: str = ""
    description: str = ""
    completion_criteria: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    priority: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanningOperator:
    """A pure description of an action the planner can use to build a plan.

    Pure data — no execution method. Step 8.2 builds the concrete operator
    library (AcquireItem, Navigate, QueryInventory, Wait, Notify,
    ReserveResource) as instances of this shape; execution itself remains
    ExecutionEngine's job, untouched by this model.
    """

    name: str = ""
    description: str = ""
    preconditions: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()
    estimated_cost: float = 0.0
    estimated_duration: float = 0.0
    required_capabilities: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanningConstraint:
    """A condition a Plan must (hard) or should (soft) satisfy.

    kind is free-form (e.g. "budget", "time", "policy", "capability",
    "inventory", "safety", "permissions" — Step 8.4's list); this module makes
    no assumption about which kinds exist or how they're evaluated.
    """

    constraint_id: str = field(default_factory=lambda: uuid4().hex)
    kind: str = ""
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    hard: bool = True


@dataclass(frozen=True)
class PlanStep:
    """One ordered step within a Plan.

    operator is optional: a step can stand on its own (as produced by a simple
    planner) or reference the PlanningOperator that generated it, once Step 8.7
    wires the operator library into plan generation.
    """

    step_id: str = field(default_factory=lambda: uuid4().hex)
    sequence: int = 0
    operator: PlanningOperator | None = None
    description: str = ""
    preconditions: tuple[str, ...] = ()
    expected_outcome: str = ""
    estimated_cost: float = 0.0
    estimated_duration: float = 0.0
    confidence: float = 0.0


@dataclass(frozen=True)
class Plan:
    """A complete, structured plan: goal, ordered steps, and estimates.

    trace is a plain sequence of human-readable messages recorded while this
    plan was produced. The richer PlanningTrace model (candidates considered,
    rejections, rationale) is Step 8.8's explicit deliverable — not invented
    early here.
    """

    plan_id: str = field(default_factory=lambda: uuid4().hex)
    goal: Goal = field(default_factory=Goal)
    steps: tuple[PlanStep, ...] = ()
    estimated_cost: float = 0.0
    estimated_duration: float = 0.0
    confidence: float = 0.0
    constraints: tuple[PlanningConstraint, ...] = ()
    validation_status: ValidationStatus = ValidationStatus.UNVALIDATED
    metadata: dict[str, Any] = field(default_factory=dict)
    trace: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanCandidate:
    """A Plan under consideration among several alternatives.

    score/rejected/rejection_reason are structural placeholders: Step 8.5
    enumerates candidates (score stays None), Step 8.6 scores them.
    """

    candidate_id: str = field(default_factory=lambda: uuid4().hex)
    plan: Plan = field(default_factory=Plan)
    score: float | None = None
    rejected: bool = False
    rejection_reason: str = ""


@dataclass(frozen=True)
class RetrievedItem:
    """Explainability unit (Context-Aware Personalized Planning refactor) —
    every fact PlanningContext retrieves carries its own provenance, so
    "why did the planner pick this" is always answerable: source,
    confidence, timestamp, retrieval_score. evidence_ids is available for
    an item to reference related entries it was derived from; retrieval
    itself never collapses distinct items into a summary — every
    retrieved item is preserved so its specific content stays available
    to the planner."""

    content: str = ""
    item_type: str = ""
    """"experience" | "conversation" | "execution" | "knowledge" |
    "relationship" | ... — free-form, mirrors PlanningConstraint.kind's
    existing free-form-string precedent in this same module."""
    source: str = ""
    confidence: float = 1.0
    timestamp: float = 0.0
    retrieval_score: float = 0.0
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanningContext:
    """The sole input to the planner (Context-Aware Personalized Planning
    refactor) — assembled by kernel/pipeline/planning/context_engine.py::
    ContextConstructionEngine from an actor's timelines (kernel/timeline/),
    organizational context (kernel/society/), semantic memory
    (kernel/learn/memory/), and knowledge graph (kernel/knowledge_graph.py).

    goal is a belief_state.py::Goal (aliased BeliefGoal below), not this
    module's own Goal — LLMPlanner.plan() has always read
    goal.name/description/success_criteria/optimization_objective (the
    belief_state shape), and PlanningContext must interoperate with the
    live planner, not just this module's own (separately unused)
    Goal/SubGoal/PlanningOperator/IntegratedPlanningEngine family.

    subgoals/available_operators (this module's own richer, still-unused
    types) are kept for IntegratedPlanningEngine's benefit — untouched by
    this refactor.
    """

    actor_id: str = ""
    goal: Any = field(default_factory=Goal)  # belief_state.py::Goal (BeliefGoal) or this
    # module's own Goal (the bare-construction default)
    intent: str = ""
    subgoals: tuple[SubGoal, ...] = ()
    available_operators: tuple[PlanningOperator, ...] = ()
    constraints: tuple[PlanningConstraint, ...] = ()
    current_beliefs: tuple[Any, ...] = ()  # kernel/timeline BeliefRecord entries
    current_location: Any = None  # kernel/timeline Presence entry, or None
    current_society_context: Any = None  # SocietyActivationResult
    current_team_context: Any = None  # Team, or None
    active_policies: tuple[Any, ...] = ()  # GovernancePolicy tuple
    available_capabilities: tuple[str, ...] = ()
    available_resources: tuple[Any, ...] = ()  # WorldResource tuple
    actor_profile: Any = None  # ActorProfile
    relevant_experiences: tuple[RetrievedItem, ...] = ()
    relevant_conversations: tuple[RetrievedItem, ...] = ()
    relevant_executions: tuple[RetrievedItem, ...] = ()
    relevant_knowledge: tuple[RetrievedItem, ...] = ()
    relevant_external_knowledge: tuple[RetrievedItem, ...] = ()
    """SittingFace / external reference knowledge — NOT authoritative world state.
    Populated by ContextConstructionEngine via SittingFaceKnowledgeRetriever.
    Distinct from relevant_knowledge (knowledge graph entities)."""
    relevant_relationships: tuple[RetrievedItem, ...] = ()
    relevant_locations: tuple[str, ...] = ()
    relevant_objects: tuple[str, ...] = ()
    trust_scores: dict[str, float] = field(default_factory=dict)
    reputation: dict[str, float] = field(default_factory=dict)
    relevant_goals: tuple[Any, ...] = ()  # kernel/timeline GoalRecord entries
    relevant_context_events: tuple[RetrievedItem, ...] = ()
    """Context Grounding: real, recent SocietyContextStream ContextEvents
    (kernel/society/context_stream.py) for this actor — closes the gap
    ContextConstructionEngine.build() previously left explicit (its own
    "never queries ContextStream" comment, in the pipeline's runtime
    execution module — not imported here; this module stays model-only).
    Populated by ContextConstructionEngine._retrieve_context_stream() with
    whatever doesn't belong in incoming_messages/negotiation_updates below."""
    incoming_messages: tuple[RetrievedItem, ...] = ()
    """Real-Time World Changes refactor (Context Stream spec): the subset
    of this actor's recent SocietyContextStream events that are message
    sends (SocietyRuntime.send_message's own published shape) — a
    first-class field rather than folded anonymously into
    relevant_context_events, matching the spec's "incoming messages" as a
    distinct Context Stream category. Populated by
    ContextConstructionEngine._retrieve_context_stream(), re-bucketing
    events already fetched for relevant_context_events — no new query."""
    negotiation_updates: tuple[RetrievedItem, ...] = ()
    """Real-Time World Changes refactor (Context Stream spec): the subset
    of this actor's recent SocietyContextStream events that carry a
    transaction_id (TransactionCoordinator._stream_event's own published
    shape) — the spec's "negotiation updates" as a distinct Context
    Stream category. Same re-bucketing-not-requerying provenance as
    incoming_messages above."""
    environmental_context: dict[str, Any] = field(default_factory=dict)
    temporal_context: dict[str, Any] = field(default_factory=dict)
    risk_assessment: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_legacy(cls, belief: Any, goal: Any, runtime_context: Any = None) -> "PlanningContext":
        """Bridges LLMPlanner.plan()'s pre-refactor (belief, goal,
        context) call shape into a minimal PlanningContext, so every
        existing call site (tests/unit/test_pipeline_planning.py,
        test_planning_integration.py, test_planning_trace.py,
        IntegratedPlanningEngine's fallback) keeps working unchanged —
        see llm_planner.py::LLMPlanner.plan()'s
        dual-accepting signature."""
        metadata = dict(getattr(belief, "metadata", {}) or {})
        # Escape hatch, not a new public field: LLMPlanner's
        # _find_relevant_facts reads belief.facts (Fact{entity, attribute,
        # value, confidence, source}), a different shape than this
        # dataclass's relevant_knowledge/current_beliefs (RetrievedItem/
        # timeline-entry shapes, populated only by ContextConstructionEngine
        # for the NEW path). Stashing the raw belief here lets plan()'s
        # legacy branch reproduce the exact pre-refactor behavior byte-for-
        # byte instead of lossily re-deriving it.
        metadata["_legacy_belief"] = belief
        return cls(
            actor_id=getattr(belief, "actor_id", ""),
            goal=goal,
            metadata=metadata,
        )
