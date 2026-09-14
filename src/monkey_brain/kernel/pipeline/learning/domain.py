"""Learning domain model (Step 10.1) — formal, algorithm-independent learning types.

This module defines WHAT a learning experience, signal, and result are. It
does not decide HOW to capture experiences, compute reward, update beliefs,
evolve the world model, or select a learning strategy — those are Steps
10.2-10.6. All types are immutable value objects, mirroring the planning
(kernel/pipeline/planning/domain.py) and execution
(kernel/pipeline/execution_runtime/domain.py) domain models.

goal/plan/execution on LearningExperience are deliberately typed Any, not
planning.domain.Plan / execution_runtime.domain.ExecutionResult. A learning
experience must be capturable from ANY cognitive cycle — including the
default LLMPlanner/ActionExecutor path, whose
belief_state.Plan and execution.py ExecutionResult are neither of those
richer types. Hard-typing to the new pipeline's own types would make
experience capture (Step 10.2) only work for cycles that happen to use
IntegratedPlanningEngine/IntegratedExecutionEngine — the opposite of "every
cognitive cycle should produce a reusable experience." The same Any-typing
choice contracts.CompiledRequest already makes for its own intent/goal
fields, for the identical reason.

LearningPolicy here is pure data (name, strategy, parameters) — matching
every other type in this module and the same "data now, behavior later"
split Step 8.1's ValidationStatus (preceding Step 8.4's ConstraintEngine)
and Step 9.1's RetryPolicy/RetryStrategy (preceding Step 9.4's
RetryExecutor) already used. Step 10.6 builds the actual behavioral
contract ("LearningPolicy.learn(experience)") and concrete strategy
implementations on top of this descriptor.

Do not modify ExecutionEngine (execution.py) or CognitiveRuntime
(belief_runtime.py) — confirmed by the ownership-boundary test in this
step's test file, the same discipline every Step 8/9 step before an
explicit integration step followed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class LearningEvent:
    """A factual record of something notable that happened during a
    cognitive cycle — e.g. "plan selected", "step succeeded". The raw event
    log; LearningSignal (below) is the derived, forward-looking distillation
    of events into something a learning algorithm can act on.
    """

    event_id: str = field(default_factory=lambda: uuid4().hex)
    event_type: str = ""
    description: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LearningObservation:
    """One observed fact worth learning from — the learning stage's own
    input shape, parallel to belief_state.Observation/Fact but scoped to
    what Step 10.4 (belief learning) and Step 10.5 (world evolution) consume.
    """

    entity: str = ""
    attribute: str = ""
    value: Any = None
    confidence: float = 1.0
    source: str = ""
    observed_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class LearningSignal:
    """A directional nudge derived from an outcome for a learning algorithm
    to act on — e.g. "increase confidence in Store A stocking milk". Not a
    conclusion itself: Steps 10.3-10.5 (reward/belief/world engines) consume
    signals; this module only describes their shape.
    """

    signal_id: str = field(default_factory=lambda: uuid4().hex)
    kind: str = ""
    """e.g. "reward" | "belief" | "world" | "policy" — which downstream
    engine this signal is meant for. Free-form, not an enum: new signal
    kinds shouldn't require a model change."""
    subject: str = ""
    """What the signal is about — an entity, operator name, belief key, etc."""
    direction: float = 0.0
    """[-1.0, 1.0]: negative discourages, positive reinforces."""
    strength: float = 0.5
    """[0.0, 1.0]: how strongly to weight this signal."""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LearningOutcome:
    """What happened, distilled to what matters for learning — goal
    achievement, cost, duration, errors. A summary view, not a copy of the
    full execution result (kept as Any on LearningExperience separately for
    anyone who needs the raw data).
    """

    goal_achieved: bool = False
    partial: bool = False
    cost: float = 0.0
    duration_seconds: float = 0.0
    errors: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Provenance:
    """Where a piece of learned knowledge came from — referenced by
    LearningExperience and (Step 10.4) individual belief updates. Richer
    than a bare source string because Step 10.4 explicitly needs
    "provenance tracking" as a capability, not just a label.
    """

    actor_id: str = ""
    tenant_id: str = "default"
    run_id: str = ""
    source: str = ""
    """e.g. "execution_outcome" | "user_feedback" | "world_observation\""""
    recorded_at: float = field(default_factory=time.time)


def scoped_goal_signature(goal_name: str, tenant_id: str = "") -> str:
    """Tenant-scope a goal signature. CapabilityPromotionTracker
    (capability_promotion.py) keys promotion streaks by this string alone,
    with no other tenant check anywhere in that path — two tenants whose
    actors happen to pursue a goal with the same name would otherwise
    share one promotion streak, and an operator could activate a
    candidate whose verified recipe was built from a different tenant's
    plan. actor_id is deliberately NOT included: promotion is meant to
    generalize a verified pattern across actors within one tenant (see
    CapabilityPromotionTracker's own docstring, "actors within a society
    tick concurrently")."""
    return f"{tenant_id or 'default'}::{goal_name}"


def experience_tenant_id(experience: Any) -> str:
    """Best-effort `.provenance.tenant_id` off something that might not
    fully be a LearningExperience (capability_promotion.py's
    extract_recipe_from_experience takes `experience: Any`) -- a shared
    accessor so this getattr chain isn't retyped slightly differently at
    each call site. A real LearningExperience's `provenance` is never
    actually None (it's a default_factory field, so phi.py's
    _goal_signature can use this too) -- the getattr chain here only
    guards a non-LearningExperience caller."""
    return getattr(getattr(experience, "provenance", None), "tenant_id", "") or ""


@dataclass(frozen=True)
class LearningExperience:
    """One complete, immutable record of a cognitive cycle, ready to learn
    from. The central type this whole model exists to produce (Step 10.2).
    """

    experience_id: str = field(default_factory=lambda: uuid4().hex)
    goal: Any = None
    plan: Any = None
    execution: Any = None
    observations: tuple[LearningObservation, ...] = ()
    outcome: LearningOutcome = field(default_factory=LearningOutcome)
    reward: float = 0.0
    confidence: float = 0.0
    timestamp: float = field(default_factory=time.time)
    provenance: Provenance = field(default_factory=Provenance)
    events: tuple[LearningEvent, ...] = ()
    signals: tuple[LearningSignal, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LearningPolicy:
    """A named learning strategy descriptor — pure data. See module
    docstring for why this isn't yet the behavioral Protocol Step 10.6
    builds.
    """

    name: str = "default"
    strategy: str = "reinforcement"
    """e.g. "reinforcement" | "supervised" | "bayesian" | "rule_based" |
    "human_feedback" | "llm_assisted" — free-form, not an enum, so Step
    10.6 can introduce new strategies without a model change here."""
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LearningContext:
    """The learning stage's own working scope: the experience being learned
    from, plus which policy governs how. Distinct from contracts.RuntimeContext
    (booted infra), planning.domain.PlanningContext, and
    execution_runtime.domain.ExecutionContext — each stage has its own scope,
    not a shared grab-bag.
    """

    experience: LearningExperience = field(default_factory=LearningExperience)
    policy: LearningPolicy = field(default_factory=LearningPolicy)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LearningResult:
    """What the learning stage produced — Step 10.7's enhanced Learn stage
    output: the reward, which signals were applied, whether beliefs/world
    were updated, and why.
    """

    experience_id: str = ""
    reward: float = 0.0
    signals_applied: tuple[LearningSignal, ...] = ()
    belief_updated: bool = False
    world_updated: bool = False
    rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
