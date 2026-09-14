"""Timeline entries — the append-only replacement for mutable "current
state" fields (Temporal Presence & Actor Timeline Model refactor).

Nothing is ever overwritten: every significant change to an actor's
location, memberships, goals, beliefs, executions, relationships, or
activity becomes a new, immutable TimelineEntry. Current state is derived
by querying the most recent valid record (see kernel/timeline/store.py's
TimelineStore.current()), never stored as a separately-mutated field.

One shared base + 7 typed subclasses — mirrors kernel/geography/entity.py's
own "one common base, N typed tiers" shape, generalized here across
timeline KINDS instead of geographic TIERS. Only kernel/timeline/store.py's
TimelineStore constructs these (see the exclusive-constructor conformance
check in scripts/check_architecture_conformance.py) — callers append
through TimelineStore.append()/PresenceTimeline.move_actor(), not by
instantiating these dataclasses directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class TimelineKind(Enum):
    PRESENCE = "presence"
    MEMBERSHIP = "membership"
    GOAL = "goal"
    BELIEF = "belief"
    EXECUTION = "execution"
    RELATIONSHIP = "relationship"
    ACTIVITY = "activity"
    INTENT = "intent"
    PLAN = "plan"
    DECISION = "decision"


@dataclass(frozen=True)
class TimelineEntry:
    """Common shape every timeline entry shares, regardless of kind."""

    entry_id: str = field(default_factory=lambda: uuid4().hex)
    actor_id: str = ""
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    """None = still open/current — the record's validity extends to "now"
    until a later entry closes it (see TimelineStore.close())."""
    confidence: float = 1.0
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""
    """Id of the logical end-to-end operation this entry belongs to (e.g.
    the cognitive tick's execution_id, or a negotiation's transaction_id).
    First-class/typed here (promoted out of the untyped metadata dict) so
    it's queryable without parsing metadata; metadata["execution_id"] is
    kept as-is wherever existing writers already set it, unchanged."""
    causation_id: str = ""
    """Id of the immediate record that caused this entry (e.g. a
    CommunicationDecision.decision_id or a negotiation round's trace_id).
    Left empty rather than fabricated when no concrete cause is in scope."""

    def is_open(self) -> bool:
        return self.end_time is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "actor_id": self.actor_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "confidence": self.confidence,
            "source": self.source,
            "metadata": dict(self.metadata),
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
        }


@dataclass(frozen=True)
class Presence(TimelineEntry):
    """Actor LOCATED_IN Space, valid_time=[start_time, end_time or now)."""

    space_id: str = ""
    activity: str = ""

    @property
    def duration(self) -> float:
        """Seconds spent at this Space: end_time - start_time if closed,
        else time.time() - start_time for a still-open (current) visit."""
        end = self.end_time if self.end_time is not None else time.time()
        return end - self.start_time

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(space_id=self.space_id, activity=self.activity, duration=self.duration)
        return d


@dataclass(frozen=True)
class MembershipRecord(TimelineEntry):
    """Actor MEMBER_OF Society, with Roles — supersedes the non-temporal
    version this session originally built in kernel/society/membership.py.

    Membership as a First-Class Runtime Resource refactor: membership_id
    is stable across this membership's ENTIRE lifecycle (distinct from
    entry_id, which identifies one row) — every lifecycle event (role
    assigned/removed, trust updated, delegation granted/revoked,
    suspended/resumed/terminated) closes the previous row for this
    membership_id and appends a new one with the updated field(s) and
    metadata["event"] naming what changed. is_open()/end_time still means
    "is this the current row for this membership_id," exactly like
    Presence — just keyed by membership_id instead of "one open row per
    actor," since an actor can hold several concurrent memberships."""

    membership_id: str = ""
    society_id: str = ""
    team_id: str = ""
    roles: tuple[str, ...] = ()
    status: str = "active"
    """active | suspended | terminated."""
    permissions: tuple[str, ...] = ()
    trust_score: float = 0.5
    reason: str = ""
    """Why the membership/role/status changed, when applicable."""

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(
            membership_id=self.membership_id,
            society_id=self.society_id,
            team_id=self.team_id,
            roles=list(self.roles),
            status=self.status,
            permissions=list(self.permissions),
            trust_score=self.trust_score,
            reason=self.reason,
        )
        return d


@dataclass(frozen=True)
class GoalRecord(TimelineEntry):
    """Replaces kernel/pipeline/belief_state.py::BeliefState.goal as a
    mutable field — see kernel/pipeline/belief_state.py's goal @property."""

    name: str = ""
    description: str = ""
    success_criteria: tuple[str, ...] = ()
    optimization_objective: str = ""
    priority: int = 0
    status: str = "active"
    """active | completed | cancelled — lifecycle is append-only: a status
    change is a NEW GoalRecord, never an edit to a prior one."""

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(
            name=self.name,
            description=self.description,
            success_criteria=list(self.success_criteria),
            optimization_objective=self.optimization_objective,
            priority=self.priority,
            status=self.status,
        )
        return d


@dataclass(frozen=True)
class BeliefRecord(TimelineEntry):
    """One observed/fused belief hypothesis — additive history layer
    alongside kernel/society/belief.py::BeliefFusion's existing per-subject
    current-hypothesis view (which is unchanged by this refactor)."""

    subject: str = ""
    predicate: str = ""
    value: Any = None

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(subject=self.subject, predicate=self.predicate, value=self.value)
        return d


@dataclass(frozen=True)
class ExecutionRecord(TimelineEntry):
    """One completed tick's execution — naturally append-only already
    (a fresh ExecutionResult per tick, never mutated); this is what
    persists it instead of discarding it after the tick."""

    goal: str = ""
    plan_summary: tuple[str, ...] = ()
    outcome: str = ""
    """success | failure | partial."""
    failure_reason: str = ""
    """The first failed step's real "{action}: {error}" — kept for
    backward compatibility; step_failures below is the complete list."""
    capabilities_used: tuple[str, ...] = ()
    step_failures: tuple[str, ...] = ()
    """Every failed step this execution attempted, each "{action}: {error}"
    — for a partial outcome, failure_reason alone only ever showed the
    first one, silently dropping the rest."""

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(
            goal=self.goal,
            plan_summary=list(self.plan_summary),
            outcome=self.outcome,
            failure_reason=self.failure_reason,
            capabilities_used=list(self.capabilities_used),
            step_failures=list(self.step_failures),
        )
        return d


@dataclass(frozen=True)
class IntentRecord(TimelineEntry):
    """Cognitive State refactor: kernel/pipeline/belief_state.py::Intent
    (type/confidence/metadata) is set every tick and discarded once that
    tick's CognitiveState goes out of scope — this persists it. confidence
    is inherited from the base TimelineEntry, not duplicated here."""

    intent_type: str = ""
    entities: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    priority: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(
            intent_type=self.intent_type,
            entities=list(self.entities),
            constraints=list(self.constraints),
            priority=self.priority,
        )
        return d


@dataclass(frozen=True)
class PlanRecord(TimelineEntry):
    """Cognitive State refactor: kernel/pipeline/belief_state.py::Plan is a
    single field on CognitiveState, wholesale-overwritten every tick with
    no history kept anywhere — this persists one PlanRecord per tick that
    produced a plan. confidence (base) carries the plan's own confidence,
    matching Plan.confidence."""

    plan_id: str = field(default_factory=lambda: uuid4().hex)
    goal: str = ""
    steps: tuple[str, ...] = ()
    """One capability/action name per PlanStep, in order — the runtime
    execution mechanism (Level 3: EvaluateStrategy, BroadcastToAffiliation,
    ...). Implementation detail; the Execution Graph is where this
    belongs, not a user-facing plan."""
    step_descriptions: tuple[str, ...] = ()
    """One PlanStep.description per step, same order as steps — the
    planner's own plain-business-language explanation of what that step
    accomplishes (Level 2: "Check if the product is in stock", "Reserve
    one unit for this order"). This — not steps — is the domain plan an
    Actor Inspector should show a user."""
    node_count: int = 0
    completed_nodes: int = 0
    cost: float = 0.0
    risk: float = 0.0
    status: str = "generated"
    """generated | completed | failed | partial."""
    result: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(
            plan_id=self.plan_id,
            goal=self.goal,
            steps=list(self.steps),
            step_descriptions=list(self.step_descriptions),
            node_count=self.node_count,
            completed_nodes=self.completed_nodes,
            cost=self.cost,
            risk=self.risk,
            status=self.status,
            result=self.result,
        )
        return d


@dataclass(frozen=True)
class DecisionRecord(TimelineEntry):
    """Cognitive State refactor: persists kernel/society/integration.py::
    PlanetaryRuntime._build_negotiation_trace's result — computed
    correctly from real negotiation/coordination actions each request,
    previously attached only to the transient HTTP response
    (execution_scope["negotiation"]) and then discarded. Only populated
    for actors that actually went through that coordination path — no
    DecisionRecord yet means no such decision was made, not a gap.
    confidence (base) doubles as decision confidence."""

    selected_strategy: str = ""
    reason: str = ""
    utility: float = 0.0
    evidence: tuple[str, ...] = ()
    candidates: tuple[dict[str, Any], ...] = ()
    """Every candidate strategy considered, with its own utility/probability
    — straight from _build_negotiation_trace's candidate_strategies."""

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(
            selected_strategy=self.selected_strategy,
            reason=self.reason,
            utility=self.utility,
            evidence=list(self.evidence),
            candidates=[dict(c) for c in self.candidates],
        )
        return d


@dataclass(frozen=True)
class RelationshipRecord(TimelineEntry):
    """Typed adapter view over kernel/relationships/__init__.py::
    RelationshipHistoryEntry — see TimelineQueryEngine.replay(), which
    needs one common entry shape across all 7 kinds. Not a second store:
    RelationshipGraph stays the source of truth for relationships."""

    kind: str = ""
    target_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(kind=self.kind, target_id=self.target_id)
        return d


@dataclass(frozen=True)
class ActivityRecord(TimelineEntry):
    """Actor PERFORMING <activity>, independent of location — may span
    multiple Presence records (e.g. "driving" spans several Spaces)."""

    activity: str = ""
    presence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d.update(activity=self.activity, presence_ids=list(self.presence_ids))
        return d


# kind -> concrete dataclass — the sole construction map, mirroring
# kernel/geography/registry.py::_ENTITY_CLASSES exactly.
ENTRY_CLASSES: dict[TimelineKind, type[TimelineEntry]] = {
    TimelineKind.PRESENCE: Presence,
    TimelineKind.MEMBERSHIP: MembershipRecord,
    TimelineKind.GOAL: GoalRecord,
    TimelineKind.BELIEF: BeliefRecord,
    TimelineKind.EXECUTION: ExecutionRecord,
    TimelineKind.RELATIONSHIP: RelationshipRecord,
    TimelineKind.ACTIVITY: ActivityRecord,
    TimelineKind.INTENT: IntentRecord,
    TimelineKind.PLAN: PlanRecord,
    TimelineKind.DECISION: DecisionRecord,
}
