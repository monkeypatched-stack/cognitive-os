"""BeliefState — the canonical cognitive data model.

The BeliefState is the central data structure shared by every stage of the
cognitive lifecycle. It replaces loosely typed dictionaries with a well-defined
semantic model.

Stages communicate through BeliefState:
    Observe  → writes facts and observations from world
    Believe  → updates confidence, detects conflicts
    Plan     → reads facts + intent + goal, writes plan
    Execute  → reads plan, writes actions and observations
    Learn    → updates hypotheses, confidence, learned_updates
    Predict  → reads hypotheses + confidence, writes predictions
    Commit   → finalizes the cognitive state

This is NOT the same as ActorBelief (compile/actor_belief.py).
ActorBelief is a tensor-based belief model for the world graph.
BeliefState is the pipeline's cognitive state abstraction.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict, is_dataclass
from typing import Any


def _json_safe(value: Any) -> Any:
    """Recursively convert a value into something json.dumps can handle,
    without changing what it means.

    Real, confirmed-live bug this exists to fix: kernel/pipeline/
    belief_runtime.py's grounding step stashes real
    kernel/pipeline/planning/domain.py::RetrievedItem dataclass instances
    directly into BeliefState.metadata["relevant_knowledge"] (a plain
    dict[str, Any] field with no fixed shape — never itself run through
    asdict() the way every other BeliefState field below is). to_dict()
    passed self.metadata straight through unconverted, so
    checkpoint_actor_belief's json.dumps(belief.to_dict()) failed on
    every single real tick ("Object of type RetrievedItem is not JSON
    serializable", logged as non-fatal — so this was silently discarding
    every actor's canonical belief checkpoint instead of persisting it,
    confirmed live via an empty actor_state Mongo collection despite many
    real completed ticks). Walks lists/tuples/dicts recursively (metadata
    has no fixed depth, unlike this class's other, structured fields) and
    converts any dataclass instance found via dataclasses.asdict() — the
    same conversion this file's other fields already get, just applied
    generically since metadata's actual contents aren't known in advance.
    """
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (set, frozenset)):
        # Same class of bug this function already exists to fix (see
        # docstring), caught again live this session: belief_runtime.py
        # once stashed a frozenset directly into belief.metadata
        # ("_resolved_permissions") — json.dumps(belief.to_dict())
        # failed on every tick that touched it, silently discarding the
        # actor's belief checkpoint (non-fatal logging hid it). Fixed at
        # that one call site by switching to a tuple, but that only
        # prevents THAT call site from reintroducing it — sorted() (not
        # insertion order, which a set doesn't have) keeps this
        # deterministic for any other metadata value of this type,
        # present or future.
        return sorted(_json_safe(v) for v in value)
    return value


# ════════════════════════════════════════════════════════════════════════════
# Supporting Types — frozen value objects (defined before BeliefState)
# ════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Intent:
    """Classified intent from the compiler."""
    type: str = ""
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Goal:
    """Resolved goal from the compiler."""
    name: str = ""
    description: str = ""
    success_criteria: tuple[str, ...] = ()
    optimization_objective: str = ""
    """'cost', 'speed', 'reliability', or '' for default balanced scoring."""


@dataclass(frozen=True)
class Observation:
    """A raw observation from the world."""
    entity: str = ""
    description: str = ""
    source: str = "world"
    confidence: float = 1.0
    observed_at: float = field(default_factory=time.time)
    correlation_id: str = ""
    """Id of the logical operation this observation was made during, when
    known. No causation_id here for the same reason as the other
    Observation definition (kernel/pipeline/observations.py) — a raw
    world observation generally has no in-system cause to point at."""


@dataclass(frozen=True)
class Fact:
    """A grounded fact about the world."""
    entity: str = ""
    attribute: str = ""
    value: Any = None
    confidence: float = 1.0
    source: str = "observation"
    observed_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Hypothesis:
    """An inferred belief that may or may not be true."""
    claim: str = ""
    confidence: float = 0.5
    evidence: tuple[str, ...] = ()
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Assumption:
    """Something taken for granted unless challenged."""
    statement: str = ""
    confidence: float = 0.8
    source: str = "default"
    created_at: float = field(default_factory=time.time)


@dataclass
class Uncertainty:
    """Quantified uncertainty about the belief state."""
    confidence: float = 0.5
    confidence_by_source: dict[str, float] = field(default_factory=dict)
    entropy: float = 0.0


@dataclass
class WorkingMemoryEntry:
    """A temporary entry in working memory."""
    key: str = ""
    value: Any = None
    expires_at: float = 0.0


@dataclass(frozen=True)
class LongTermMemoryEntry:
    """A retained entry across reasoning cycles."""
    key: str = ""
    value: Any = None
    confidence: float = 1.0
    stored_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Plan:
    """A structured plan produced by the planning engine.

    Plans are semantic objects — they describe WHAT to do,
    not HOW to execute. Execution is a separate concern.
    """
    goal: str = ""
    """What the plan aims to achieve."""
    preconditions: tuple[str, ...] = ()
    """What must be true before execution."""
    steps: tuple[PlanStep, ...] = ()
    """Ordered plan steps."""
    expected_outcomes: tuple[str, ...] = ()
    """What we expect to happen after execution."""
    cost: float = 0.0
    """Estimated resource cost. [0.0, 1.0]"""
    confidence: float = 0.0
    """Plan confidence. [0.0, 1.0]"""
    risk: float = 0.0
    """Risk level. [0.0, 1.0]"""
    start_state: str = ""
    """Starting state."""
    goal_state: str = ""
    """Goal state."""
    planner: str = "default"
    """Which planning engine produced this plan."""
    metadata: dict[str, Any] = field(default_factory=dict)
    """Plan-specific metadata."""


@dataclass(frozen=True)
class PlanStep:
    """A single step in a plan."""
    action: str = ""
    """What to do (e.g. 'find_milk', 'add_to_cart')."""
    description: str = ""
    """Human-readable description."""
    preconditions: tuple[str, ...] = ()
    """What must be true before this step."""
    expected_outcome: str = ""
    """What this step is expected to achieve."""
    cost: float = 0.0
    """Estimated cost of this step. [0.0, 1.0]"""
    confidence: float = 0.0
    """Confidence in this step. [0.0, 1.0]"""
    required_permission: str = ""
    """Optional "resource:action" string the planner declares this step
    needs (e.g. "household_wallet:spend") — empty (the default) means no
    check. The kernel enforces this generically at execution time
    (_execute_plan, belief_runtime.py) without any idea what the string
    means; deciding which steps need which permission is the planner's
    job, same as everything else about the plan."""
    parameters: dict[str, Any] = field(default_factory=dict)
    """Structured, capability-specific arguments the planner chose for this
    step (e.g. {"selection": [{"id": "...", "qty": 1}]} for a step whose
    capability exposes candidates and expects an explicit business
    decision — see ProductSelectionCapability). The kernel passes this
    through to the Action unexamined (belief_runtime.py's _execute_plan);
    it has no idea what any key means, same principle as
    required_permission above. Empty (the default) means the planner made
    no structured decision for this step — a capability that needs one
    and doesn't get it reports its own decision_required outcome."""
    depends_on: tuple[int, ...] = ()
    """0-based indices of OTHER steps in this same plan.steps tuple that
    must have succeeded before this step can meaningfully be attempted.
    Empty (the default — every plan before this field existed, and any
    plan the LLM planner doesn't explicitly reason about dependencies for)
    means no explicit dependency was declared; kernel/pipeline/prediction/
    risk.py::RiskEngine treats that as a no-op, computing this step's
    probability exactly as it always has. When populated, a step whose
    declared dependency did NOT succeed contributes 0.0 to the plan's
    overall success probability instead of its own raw transition
    probability — it genuinely cannot happen if its prerequisite didn't.
    Distinct from `preconditions` (free-text WORLD-STATE facts like
    "at_location") — this is a structural reference to another step in
    THIS plan, not a fact about the world."""


@dataclass(frozen=True)
class PlanEvaluation:
    """Result of evaluating a plan."""
    plan: Plan
    """The evaluated plan."""
    feasible: bool = True
    """Whether the plan can be executed."""
    goal_satisfied: bool = False
    """Whether the plan satisfies the goal."""
    violations: tuple[str, ...] = ()
    """Constraint violations found."""
    score: float = 0.0
    """Evaluation score. [0.0, 1.0]"""


@dataclass(frozen=True)
class Prediction:
    """A predicted future state."""
    description: str = ""
    confidence: float = 0.5
    based_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class LearnedUpdate:
    """Knowledge acquired during a reasoning cycle."""
    what: str = ""
    evidence: tuple[str, ...] = ()
    confidence: float = 0.5
    learned_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class BeliefSnapshot:
    """Immutable snapshot of a belief state at a point in time."""
    version: int = 0
    actor_id: str = ""
    tenant_id: str = ""
    intent_type: str = ""
    goal_name: str = ""
    facts_count: int = 0
    hypotheses_count: int = 0
    observations_count: int = 0
    confidence: float = 0.0
    plan_steps: int = 0
    predictions_count: int = 0
    learned_count: int = 0
    timestamp: float = field(default_factory=time.time)


# ════════════════════════════════════════════════════════════════════════════
# BeliefState — the canonical cognitive model
# ════════════════════════════════════════════════════════════════════════════

def _normalize_goal_key(name: str) -> str:
    """Thin wrapper over kernel/pipeline/planning/goal_key.py::canonicalize_goal
    — the SAME normalization every other goal-scoped lookup in the planning/
    prediction pipeline now uses (Current Plan, plan-skip counters,
    TransitionModel), so a standing plan/failure-history for one goal can
    never be mistaken for another's. Lazy-imported (not a module-top import)
    to avoid a real circular import: kernel/pipeline/planning/__init__.py
    eagerly imports integration.py, which imports belief_state.py (this
    module) — importing any kernel.pipeline.planning submodule at this
    module's top level would cycle back here before BeliefState is defined.
    This is the same lazy-import convention belief_runtime.py already uses
    for its own kernel.pipeline.planning.* imports."""
    from src.monkey_brain.kernel.pipeline.planning.goal_key import canonicalize_goal
    return canonicalize_goal(name)


@dataclass
class BeliefState:
    """The actor's internal cognitive model.

    Represents what the actor currently believes about:
    - itself (identity)
    - the world (observations, facts)
    - its goals and intentions
    - uncertainty
    - learned knowledge
    - predictions
    - working memory

    Mutable within a single execution cycle.
    Owned by CognitiveState. Never global shared state.
    """

    # ── Identity ──────────────────────────────────────────────────────────

    actor_id: str = ""
    tenant_id: str = "default"

    # ── Intent & Goal ─────────────────────────────────────────────────────
    # Temporal Presence & Actor Timeline Model refactor: goal is no longer a
    # stored, wholesale-overwritten field ("actor.current_goal" is exactly
    # the anti-pattern the refactor targets) — it is DERIVED below (the
    # `goal` property) from kernel/timeline's append-only GoalTimeline.
    # Every existing caller (`belief.goal`, `state.goal` via
    # execution_state.py's CognitiveState.goal delegate) keeps working
    # unchanged; only the storage underneath moved from a mutable field to
    # a timeline query.

    intent: Intent = field(default_factory=Intent)

    # ── World View (read-only reference) ──────────────────────────────────

    world_view: Any = None

    # ── Observations ──────────────────────────────────────────────────────

    observations: list[Observation] = field(default_factory=list)

    # ── Facts ─────────────────────────────────────────────────────────────

    facts: list[Fact] = field(default_factory=list)

    # ── Hypotheses ────────────────────────────────────────────────────────

    hypotheses: list[Hypothesis] = field(default_factory=list)

    # ── Assumptions ───────────────────────────────────────────────────────

    assumptions: list[Assumption] = field(default_factory=list)

    # ── Uncertainty ───────────────────────────────────────────────────────

    uncertainty: Uncertainty = field(default_factory=Uncertainty)

    # ── Memory ────────────────────────────────────────────────────────────

    working_memory: list[WorkingMemoryEntry] = field(default_factory=list)
    long_term_memory: list[LongTermMemoryEntry] = field(default_factory=list)

    # ── Plan ──────────────────────────────────────────────────────────────

    plan: Plan = field(default_factory=Plan)

    # ── Predictions ───────────────────────────────────────────────────────

    predictions: list[Prediction] = field(default_factory=list)

    # ── Learned Updates ───────────────────────────────────────────────────

    learned_updates: list[LearnedUpdate] = field(default_factory=list)

    # ── Metadata ──────────────────────────────────────────────────────────

    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Versioning ────────────────────────────────────────────────────────

    version: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def goal(self) -> Goal:
        """Current goal, derived from the actor's GoalTimeline (most
        recent GoalRecord) — not a stored field. See the module-level note
        above `intent`/`goal`'s old field declarations.

        A most-recent record with status != "active" (completed/cancelled)
        means the actor has no current goal, not that the same goal is
        still live. Before this check, _mark_goal_completed() (compile/
        cognitive_actor.py) appended an honest "completed" GoalRecord after
        every successful run, but nothing ever read that status back --
        .current() just returns the most recent record regardless of it,
        so the SAME goal.name (unchanged) kept reading as "current" one
        record later. Caught live: a one-shot /prompt goal ("buy 2 liters
        of milk") never went idle after succeeding -- the autonomous loop
        kept re-planning and re-executing (and re-charging the actor's
        real wallet for) the identical "completed" purchase indefinitely,
        every single tick, forever."""
        from src.monkey_brain.kernel.timeline.entry import TimelineKind
        from src.monkey_brain.kernel.timeline.store import TimelineStore
        record = TimelineStore().current(self.actor_id, TimelineKind.GOAL)
        if record is None or getattr(record, "status", "active") != "active":
            return Goal()
        return Goal(
            name=record.name, description=record.description,
            success_criteria=record.success_criteria,
            optimization_objective=record.optimization_objective,
        )

    @goal.setter
    def goal(self, value: Goal) -> None:
        """Backward-compat: some callers (execution_state.py's
        CognitiveState.goal setter) assign a whole Goal object directly
        rather than calling update_goal() — route it through the same
        idempotent timeline-append path."""
        self.update_goal(
            name=value.name, description=value.description,
            success_criteria=list(value.success_criteria),
            optimization_objective=value.optimization_objective,
        )

    # ══════════════════════════════════════════════════════════════════════
    # Semantic API
    # ══════════════════════════════════════════════════════════════════════

    def add_observation(self, entity: str, description: str,
                        source: str = "world", confidence: float = 1.0) -> None:
        self.observations.append(Observation(
            entity=entity, description=description,
            source=source, confidence=confidence,
        ))
        self._touch()

    def add_fact(self, entity: str, attribute: str, value: Any,
                 confidence: float = 1.0, source: str = "observation") -> None:
        self.facts.append(Fact(
            entity=entity, attribute=attribute, value=value,
            confidence=confidence, source=source,
        ))
        self._touch()

    def add_hypothesis(self, claim: str, confidence: float = 0.5,
                       evidence: list[str] | None = None) -> None:
        # Hypothesis.evidence is tuple[str, ...] — same class of bug as
        # update_plan()'s (see its own comment): passing the caller's
        # list straight through violates the dataclass's own declared
        # type from the moment of construction, not only on a later
        # serialize/deserialize round trip.
        self.hypotheses.append(Hypothesis(
            claim=claim, confidence=confidence, evidence=tuple(evidence or ()),
        ))
        self._touch()

    def add_assumption(self, statement: str, confidence: float = 0.8,
                       source: str = "default") -> None:
        self.assumptions.append(Assumption(
            statement=statement, confidence=confidence, source=source,
        ))
        self._touch()

    def update_intent(self, intent_type: str, confidence: float = 1.0,
                      metadata: dict[str, Any] | None = None) -> None:
        self.intent = Intent(type=intent_type, confidence=confidence, metadata=metadata or {})
        self._touch()

    def update_goal(self, name: str, description: str = "",
                    success_criteria: list[str] | None = None,
                    optimization_objective: str = "") -> None:
        """Append a new GoalRecord to the actor's GoalTimeline — replaces
        the old "self.goal = Goal(...)" wholesale overwrite. _update_beliefs
        (belief_runtime.py) calls this every tick even when the goal hasn't
        changed, so this is idempotent: a no-op (no new record) when the
        incoming goal matches the currently open GoalRecord exactly, so the
        timeline doesn't fill with duplicate rows for an unchanged goal —
        "state transitions create timeline updates," not every tick.

        Two real gaps this closes: an autonomous tick with no standing
        goal called this with name="" and still appended an empty
        GoalRecord (the UI could only label it "Unnamed goal" — fixed by
        skipping entirely below); and the equality check was exact
        string match, so "Buy 1L milk." / "buy 1L milk" / "buy 1L of
        milk" from separate real requests each appended their own
        record forever instead of being recognized as the same real
        goal — fixed via _normalize_goal_key() (surface-form only, not
        semantic dedup — see its own docstring)."""
        if not name.strip():
            return
        current = self.goal
        criteria = tuple(success_criteria or ())
        # Real gap this closed: description was never part of this
        # equality check, so a one-off /prompt triggering question (which
        # lives entirely in `description` -- the standing goal `name`
        # never changes between requests) never updated self.goal once
        # ANY prior request had set one. Confirmed live: "buy avocados"
        # then "buy frozen cheese pizza" moments later both planned
        # against "buy avocados" -- the pizza request's own grounding,
        # prompt, and plan all silently reused the avocados description,
        # because this check saw the same name/criteria/objective and
        # returned before description was ever compared or applied.
        # _normalize_goal_key still only applies to `name` (surface-form
        # variance in the SAME semantic goal, per this method's own
        # docstring) -- description needs exact comparison, since two
        # different one-off questions are never "the same goal" just
        # because they happen to share punctuation-normalized text.
        if (_normalize_goal_key(current.name) == _normalize_goal_key(name)
                and current.description == description
                and current.success_criteria == criteria
                and current.optimization_objective == optimization_objective):
            return
        from src.monkey_brain.kernel.timeline.entry import TimelineKind
        from src.monkey_brain.kernel.timeline.store import TimelineStore
        TimelineStore().record(
            TimelineKind.GOAL, actor_id=self.actor_id, name=name, description=description,
            success_criteria=criteria, optimization_objective=optimization_objective,
            status="active",
        )
        self._touch()

    def update_plan(self, steps: list[str], start_state: str = "",
                    goal_state: str = "") -> None:
        # Plan.steps is tuple[PlanStep, ...] — every real production
        # planner (llm_planner.py, current_plan_store.py::plan_from_dict)
        # already constructs real PlanStep objects. Storing bare action
        # strings here instead violated that contract silently (no type
        # error at construction time — dataclasses don't validate field
        # types) until the round trip through to_dict()/from_dict() broke:
        # asdict() correctly serializes a PlanStep into a dict, but a bare
        # string serializes as itself, so from_dict()'s
        # `PlanStep(**s)` crashed with "argument after ** must be a
        # mapping, not str" the moment this plan was ever persisted and
        # restored.
        self.plan = Plan(
            steps=tuple(PlanStep(action=s, description=s) for s in steps),
            start_state=start_state, goal_state=goal_state,
        )
        self._touch()

    def record_prediction(self, description: str, confidence: float = 0.5,
                          based_on: list[str] | None = None) -> None:
        # Prediction.based_on is tuple[str, ...] — same construction-time
        # type-contract gap as add_hypothesis/update_plan above.
        self.predictions.append(Prediction(
            description=description, confidence=confidence, based_on=tuple(based_on or ()),
        ))
        self._touch()

    def record_learning(self, what: str, evidence: list[str] | None = None,
                        confidence: float = 0.5) -> None:
        # LearnedUpdate.evidence is tuple[str, ...] — same gap.
        self.learned_updates.append(LearnedUpdate(
            what=what, evidence=tuple(evidence or ()), confidence=confidence,
        ))
        self._touch()

    def add_to_working_memory(self, key: str, value: Any,
                               ttl_seconds: float = 300.0) -> None:
        self.working_memory.append(WorkingMemoryEntry(
            key=key, value=value, expires_at=time.time() + ttl_seconds,
        ))
        self._touch()

    # ══════════════════════════════════════════════════════════════════════
    # Maintenance
    # ══════════════════════════════════════════════════════════════════════

    def decay_and_prune(
        self,
        decay_rate: float = 0.05,
        hypothesis_prune_threshold: float = 0.2,
        fact_prune_threshold: float = 0.1,
        max_hypotheses: int = 50,
        fact_confidence_floor: float = 0.7,
        max_observations: int = 200,
        max_learned_updates: int = 200,
        max_predictions: int = 200,
        max_facts: int = 200,
    ) -> dict[str, int]:
        """Decay confidence on facts and prune stale hypotheses/facts.

        Called each tick to prevent unbounded accumulation and simulate
        real-world belief degradation (world changes → old beliefs fade).

        Phase 5 stress (GS-5300): this function itself went unwired for
        who knows how long (its own docstring already said "called each
        tick", but nothing in the pipeline ever did) — see _commit's own
        comment for how that surfaced. While wiring it in, three MORE
        fields with the identical unbounded-growth shape turned up:
        observations/working_memory/learned_updates all only ever grow
        via their own add_*()/record_learning() methods, with no cap or
        expiry enforcement anywhere. working_memory entries already
        carry a real expires_at (a TTL nothing ever checked); observations/
        learned_updates have no confidence concept to decay by, so they're
        capped by count instead (keep the most recent N) rather than by a
        decayed score.

        Actor Runtime review, Phase 6 (scalability): facts had the SAME
        unbounded-growth exposure as those three, missed by the Phase 5
        stress fix above — confirmed live: 50 re-observations of the
        SAME (entity, attribute) (the realistic, common case — a standing
        fact like "milk price is $3.99" re-confirmed every cycle) produced
        50 retained facts, zero pruned, because a freshly re-observed fact
        never decays low enough to cross fact_prune_threshold, and unlike
        hypotheses, facts had no hard count-cap fallback for exactly that
        case. max_facts below closes it, using the same "keep the most
        recent N" convention observations/learned_updates/predictions
        already use (not confidence-sorted, like hypotheses — a global
        confidence sort could unfairly evict one entity's entire fact set
        merely because another entity's facts happen to carry a higher
        baseline confidence).

        Args:
            decay_rate: multiplicative decay per tick (0.05 = 5% per tick)
            hypothesis_prune_threshold: remove hypotheses below this confidence
            fact_prune_threshold: remove facts below this confidence
            max_hypotheses: hard cap on hypothesis count
            fact_confidence_floor: facts never decay below this (keeps planning alive)
            max_observations: hard cap on observation count (keeps the most recent)
            max_learned_updates: hard cap on learned_updates count (keeps the most recent)
            max_facts: hard cap on fact count (keeps the most recent)

        Returns:
            Counts of pruned items for logging.
        """
        now = time.time()
        pruned_hypotheses = 0
        pruned_facts = 0

        new_facts = []
        for fact in self.facts:
            age_ticks = max(1, (now - fact.observed_at) / 10.0)
            decayed = fact.confidence * ((1.0 - decay_rate) ** age_ticks)
            # Prune decision uses the true decayed value, not the
            # floor-clamped one — fact_confidence_floor only puts a floor on
            # the confidence a SURVIVING fact is stored with, so it must not
            # also protect that same fact from ever crossing
            # fact_prune_threshold (floor 0.7 >= threshold 0.1 by default,
            # which made every fact permanently unprunable when both used
            # the clamped value).
            clamped = max(decayed, fact_confidence_floor)
            if decayed >= fact_prune_threshold:
                new_facts.append(Fact(
                    entity=fact.entity, attribute=fact.attribute,
                    value=fact.value, confidence=round(clamped, 4),
                    source=fact.source, observed_at=fact.observed_at,
                ))
            else:
                pruned_facts += 1
        self.facts = new_facts

        # Hard count cap (Phase 6 fix): a fact re-observed every tick
        # never decays low enough to be caught by fact_prune_threshold
        # above, and unlike hypotheses this list had no fallback cap --
        # this is what actually bounds it in that case.
        if len(self.facts) > max_facts:
            pruned_facts += len(self.facts) - max_facts
            self.facts = self.facts[-max_facts:]

        new_hypotheses = []
        for hyp in self.hypotheses:
            age_ticks = max(1, (now - hyp.created_at) / 10.0)
            decayed = hyp.confidence * ((1.0 - decay_rate) ** age_ticks)
            if decayed >= hypothesis_prune_threshold:
                new_hypotheses.append(Hypothesis(
                    claim=hyp.claim, confidence=round(decayed, 4),
                    evidence=hyp.evidence, created_at=hyp.created_at,
                ))
            else:
                pruned_hypotheses += 1
        self.hypotheses = new_hypotheses

        if len(self.hypotheses) > max_hypotheses:
            self.hypotheses.sort(key=lambda h: h.confidence, reverse=True)
            pruned_hypotheses += len(self.hypotheses) - max_hypotheses
            self.hypotheses = self.hypotheses[:max_hypotheses]

        # working_memory: an entry's own expires_at is a real TTL nothing
        # ever enforced — expired entries are simply gone now, not kept
        # around forever past their own stated lifetime.
        pruned_working_memory = 0
        surviving_memory = [w for w in self.working_memory if w.expires_at > now]
        pruned_working_memory = len(self.working_memory) - len(surviving_memory)
        self.working_memory = surviving_memory

        # observations/learned_updates have no confidence/decay concept
        # of their own — capped by count (keep the most recent) instead.
        pruned_observations = max(0, len(self.observations) - max_observations)
        if pruned_observations:
            self.observations = self.observations[-max_observations:]

        pruned_learned_updates = max(0, len(self.learned_updates) - max_learned_updates)
        if pruned_learned_updates:
            self.learned_updates = self.learned_updates[-max_learned_updates:]

        # predictions: recorded once per plan step, every tick, with no
        # cap anywhere — the single biggest offender measured live: 1500
        # ticks (11-12 predictions/tick) grew this to 18,000 entries,
        # and deep-copying the belief object for the Predict stage's own
        # counterfactual simulation (which clones its input every tick)
        # alone took 61ms by that point — the dominant share of the
        # ~35x per-tick latency growth this whole fix targets.
        pruned_predictions = max(0, len(self.predictions) - max_predictions)
        if pruned_predictions:
            self.predictions = self.predictions[-max_predictions:]

        self._touch()
        return {
            "pruned_facts": pruned_facts, "pruned_hypotheses": pruned_hypotheses,
            "pruned_working_memory": pruned_working_memory,
            "pruned_observations": pruned_observations,
            "pruned_learned_updates": pruned_learned_updates,
            "pruned_predictions": pruned_predictions,
        }

    # ══════════════════════════════════════════════════════════════════════
    # Query API
    # ══════════════════════════════════════════════════════════════════════

    def recall(self, query: str) -> list[Fact]:
        q = query.lower()
        return [f for f in self.facts if q in str(f.value).lower() or q in f.entity.lower()]

    def confidence(self) -> float:
        return self.uncertainty.confidence

    def confidence_for(self, source: str) -> float:
        return self.uncertainty.confidence_by_source.get(source, self.uncertainty.confidence)

    # ══════════════════════════════════════════════════════════════════════
    # Snapshot & Serialization
    # ══════════════════════════════════════════════════════════════════════

    def snapshot(self) -> BeliefSnapshot:
        return BeliefSnapshot(
            version=self.version, actor_id=self.actor_id, tenant_id=self.tenant_id,
            intent_type=self.intent.type, goal_name=self.goal.name,
            facts_count=len(self.facts), hypotheses_count=len(self.hypotheses),
            observations_count=len(self.observations),
            confidence=self.uncertainty.confidence,
            plan_steps=len(self.plan.steps),
            predictions_count=len(self.predictions),
            learned_count=len(self.learned_updates),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version, "actor_id": self.actor_id, "tenant_id": self.tenant_id,
            "intent": asdict(self.intent), "goal": asdict(self.goal),
            "observations": [asdict(o) for o in self.observations],
            "facts": [asdict(f) for f in self.facts],
            "hypotheses": [asdict(h) for h in self.hypotheses],
            "assumptions": [asdict(a) for a in self.assumptions],
            "uncertainty": asdict(self.uncertainty),
            "plan": asdict(self.plan),
            "predictions": [asdict(p) for p in self.predictions],
            "learned_updates": [asdict(l) for l in self.learned_updates],
            # Full entries (Step 14 — Canonical Belief System): restore_actor_belief()
            # round-trips this dict through ActorStateStore, so memory content itself
            # must survive, not just its size. The *_count keys are kept alongside for
            # existing consumers (e.g. api/routes/actors.py) that only display counts.
            "working_memory": [asdict(w) for w in self.working_memory],
            "long_term_memory": [asdict(l) for l in self.long_term_memory],
            "working_memory_count": len(self.working_memory),
            "long_term_memory_count": len(self.long_term_memory),
            "metadata": _json_safe(self.metadata),
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BeliefState":
        """Reconstruct a BeliefState from to_dict()'s output — the inverse,
        used to restore an actor's belief across a process restart (Step 14).

        `goal` is deliberately NOT restored here: it is a derived property
        backed by the actor's own GoalTimeline (kernel/timeline), which is
        already durably persisted independent of this object — see the
        `goal` property above. It re-derives itself correctly the moment
        it's next read; storing it here would create a second, redundant
        copy of state that already has one canonical home."""
        plan_data = dict(data.get("plan") or {})
        # PlanStep's own tuple-typed fields (preconditions, depends_on)
        # must be coerced back from list explicitly — json.dumps/loads
        # (the real persistence round trip via ActorStateStore) turns
        # every tuple into a list, and PlanStep(**s) below does not
        # re-coerce them itself (dataclasses don't validate/convert field
        # types at construction). Left uncoerced, every restart silently
        # replaced these with plain lists instead of tuples — mirrors the
        # same fix current_plan_store.py::plan_from_dict already applies
        # to its own PlanStep reconstruction, for the identical reason.
        plan_data["steps"] = tuple(
            PlanStep(**{
                **s,
                "preconditions": tuple(s.get("preconditions", ()) or ()),
                "depends_on": tuple(s.get("depends_on", ()) or ()),
            })
            for s in plan_data.get("steps", ())
        )
        plan_data["preconditions"] = tuple(plan_data.get("preconditions", ()))
        plan_data["expected_outcomes"] = tuple(plan_data.get("expected_outcomes", ()))

        return cls(
            actor_id=data.get("actor_id", ""),
            tenant_id=data.get("tenant_id", "default"),
            intent=Intent(**data.get("intent", {})),
            observations=[Observation(**o) for o in data.get("observations", ())],
            facts=[Fact(**f) for f in data.get("facts", ())],
            hypotheses=[
                Hypothesis(**{**h, "evidence": tuple(h.get("evidence", ()) or ())})
                for h in data.get("hypotheses", ())
            ],
            assumptions=[Assumption(**a) for a in data.get("assumptions", ())],
            uncertainty=Uncertainty(**data.get("uncertainty", {})),
            working_memory=[WorkingMemoryEntry(**w) for w in data.get("working_memory", ())],
            long_term_memory=[LongTermMemoryEntry(**l) for l in data.get("long_term_memory", ())],
            plan=Plan(**plan_data),
            predictions=[
                Prediction(**{**p, "based_on": tuple(p.get("based_on", ()) or ())})
                for p in data.get("predictions", ())
            ],
            learned_updates=[
                LearnedUpdate(**{**l, "evidence": tuple(l.get("evidence", ()) or ())})
                for l in data.get("learned_updates", ())
            ],
            metadata=data.get("metadata", {}),
            version=data.get("version", 0),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )

    def summary(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id, "tenant_id": self.tenant_id,
            "intent": self.intent.type, "goal": self.goal.name,
            "observations": len(self.observations), "facts": len(self.facts),
            "hypotheses": len(self.hypotheses), "assumptions": len(self.assumptions),
            "confidence": round(self.uncertainty.confidence, 3),
            "plan_steps": len(self.plan.steps), "predictions": len(self.predictions),
            "learned": len(self.learned_updates),
            "working_memory": len(self.working_memory), "version": self.version,
        }

    def _touch(self) -> None:
        self.updated_at = time.time()
        self.version += 1
