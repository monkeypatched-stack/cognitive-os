"""Gateway request/response models — Pydantic schemas for the Runtime Gateway façade.

Every endpoint's contract lives here. The gateway layer owns validation and
serialization; cognitive logic stays in the runtimes.
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field


def serialize_beliefs(belief_state: Any) -> dict[str, Any]:
    """Flatten a BeliefState's entries into {subject: {confidence, predicate}}."""
    beliefs: dict[str, Any] = {}
    if belief_state is not None and hasattr(belief_state, "beliefs"):
        for entry in belief_state.beliefs:
            best = entry.best_hypothesis
            beliefs[entry.subject] = {
                "confidence": round(best.confidence, 3) if best else 0,
                "predicate": best.predicate if best else "",
            }
    return beliefs


# ── Planet ─────────────────────────────────────────────────────────────────


class PlanetResponse(BaseModel):
    society_id: str = ""
    society_name: str = ""
    world_version: int = 0
    actor_count: int = 0
    active_actors: int = 0
    cycle_count: int = 0
    context_events: int = 0
    interactions: int = 0
    reputations: int = 0
    policies: int = 0
    federations: int = 0
    society_registry_count: int = 0


class PlanetStatusResponse(BaseModel):
    status: str = "healthy"
    uptime_s: float = 0.0
    cycle_count: int = 0
    societies: int = 0
    actors: int = 0


class PlanetStatisticsResponse(BaseModel):
    total_actors: int = 0
    active_actors: int = 0
    total_societies: int = 0
    total_interactions: int = 0
    total_reputations: int = 0
    total_policies: int = 0
    total_federations: int = 0
    world_version: int = 0


# ── Societies ──────────────────────────────────────────────────────────────


class SocietyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    description: str = ""
    society_type: str = "generic"
    activation_tags: list[str] = Field(default_factory=list)
    always_active: bool = False
    subscribed_events: list[str] = Field(default_factory=list)


class SocietyUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class SocietyResponse(BaseModel):
    society_id: str = ""
    name: str = ""
    description: str = ""
    society_type: str = "generic"
    activation_tags: list[str] = Field(default_factory=list)
    always_active: bool = False
    subscribed_events: list[str] = Field(default_factory=list)
    actor_count: int = 0
    active_actors: int = 0
    tick_count: int = 0
    interaction_count: int = 0
    shared_goal_count: int = 0
    policy_count: int = 0
    is_active: bool = True


class SocietyStatusResponse(BaseModel):
    society_id: str = ""
    name: str = ""
    status: str = "healthy"
    actor_count: int = 0
    active_actors: int = 0
    tick_count: int = 0
    world_version: int = 0


class SocietyContextResponse(BaseModel):
    society_id: str = ""
    events: list[dict[str, Any]] = Field(default_factory=list)
    event_count: int = 0


class SocietyBeliefsResponse(BaseModel):
    society_id: str = ""
    actors: list[dict[str, Any]] = Field(default_factory=list)


class SocietyResourcesResponse(BaseModel):
    society_id: str = ""
    resources: list[dict[str, Any]] = Field(default_factory=list)


class SocietyMembersResponse(BaseModel):
    society_id: str = ""
    # Each entry: actor_id/name/actor_type/status/is_active/is_temporary —
    # is_temporary distinguishes a presence-derived coordination
    # participant (MembershipGovernor, ENTER/LEAVE a hosting Space) from
    # a real SocietyMembershipRegistry membership.
    members: list[dict[str, Any]] = Field(default_factory=list)


class SocietyCommunicationLogResponse(BaseModel):
    society_id: str = ""
    # Real AffiliationCommunicationRouter.CommunicationDecision entries —
    # actor-to-actor messages this society's runtime actually routed
    # (or denied), not a synthesized eligibility graph.
    entries: list[dict[str, Any]] = Field(default_factory=list)


class SharedGoalRequest(BaseModel):
    goal: str = Field(..., min_length=1)


class SharedGoalsResponse(BaseModel):
    society_id: str = ""
    goals: list[str] = Field(default_factory=list)
    count: int = 0


class PolicyRequest(BaseModel):
    policy: str = Field(..., min_length=1)


class PoliciesResponse(BaseModel):
    society_id: str = ""
    policies: list[str] = Field(default_factory=list)
    count: int = 0


class GovernancePolicyCreateRequest(BaseModel):
    """A real GovernancePolicy (kernel/society/governance.py), registered
    on a society's SocietyGovernanceEngine — distinct from PolicyRequest
    above, which only appends a plain string to Society.policies. This is
    the representation PlanningContext.active_policies (and therefore the
    planner's prompt) actually reads via SocietyActivationEngine."""

    name: str = Field(..., min_length=1)
    description: str = ""
    policy_type: str = "guideline"
    rules: list[str] = Field(default_factory=list)
    scope: str = ""
    priority: int = 0
    enabled: bool = True


class GovernancePolicyResponse(BaseModel):
    policy_id: str = ""
    name: str = ""
    description: str = ""
    policy_type: str = ""
    rules: list[str] = Field(default_factory=list)
    scope: str = ""
    priority: int = 0
    enabled: bool = True


class GovernancePoliciesResponse(BaseModel):
    society_id: str = ""
    policies: list[GovernancePolicyResponse] = Field(default_factory=list)
    count: int = 0


class ActorPermissionGrantRequest(BaseModel):
    """Grants a real, per-actor Permission (kernel/society/governance.py)
    on a society's SocietyGovernanceEngine — this is what
    Membership.resolve_permissions() actually reads (combined with the
    membership's own permissions tuple), and therefore what the planner's
    prompt sees. expires_at=0 (default) means never expires."""

    actor_id: str = Field(..., min_length=1)
    resource: str = Field(..., min_length=1)
    action: str = Field(..., min_length=1)
    expires_at: float = 0.0


class ActorPermissionResponse(BaseModel):
    permission_id: str = ""
    actor_id: str = ""
    resource: str = ""
    action: str = ""
    expires_at: float = 0.0


class ActorPermissionsResponse(BaseModel):
    society_id: str = ""
    actor_id: str = ""
    permissions: list[ActorPermissionResponse] = Field(default_factory=list)
    count: int = 0


class ActorMoveRequest(BaseModel):
    space_id: str = Field(..., min_length=1)
    activity: str = ""
    confidence: float = 1.0
    source: str = ""


class TeamCreateRequest(BaseModel):
    society_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    description: str = ""


class TeamMemberAddRequest(BaseModel):
    actor_id: str = Field(..., min_length=1)


class GeoLocationLinkRequest(BaseModel):
    # None clears the link (entity has no real coordinates); a real
    # WorldLocation id sets/replaces it — see PlanetaryRuntime.
    # set_geo_world_location for the validation that id must resolve.
    world_location_id: str | None = None


class GeoFromAddressRequest(BaseModel):
    # Address components from a real geocoding result (e.g. Nominatim's
    # address breakdown) — see PlanetaryRuntime.create_geo_from_address
    # for how missing intermediate tiers (many countries have no real
    # "state"/"county") are handled.
    country: str = Field(..., min_length=1)
    state: str = ""
    county: str = ""
    city: str = ""
    street: str = ""
    building_name: str = ""
    latitude: float
    longitude: float
    display_address: str = ""
    # Passed straight through to WorldLocation.attributes — e.g. a real
    # polygon under attributes["polygon"] when the geocoder returned
    # real way/relation geometry (Nominatim's polygon_geojson=1), never
    # a fabricated shape for a plain point address.
    attributes: dict[str, Any] = Field(default_factory=dict)


class ActorRelationshipCreateRequest(BaseModel):
    source_actor_id: str = Field(..., min_length=1)
    target_actor_id: str = Field(..., min_length=1)
    relationship_type: str = "peer"
    strength: float = 1.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActorRelationshipResponse(BaseModel):
    source_actor_id: str = ""
    target_actor_id: str = ""
    relationship_type: str = ""
    strength: float = 1.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActorAddressCreateRequest(BaseModel):
    actor_id: str = Field(..., min_length=1)
    address_type: str = ""
    value: str = ""
    is_primary: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActorAddressResponse(BaseModel):
    address_id: str = ""
    actor_id: str = ""
    address_type: str = ""
    value: str = ""
    is_primary: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Memberships (Membership as a First-Class Runtime Resource refactor) ────


class MembershipCreateRequest(BaseModel):
    actor_id: str = Field(..., min_length=1)
    society_id: str = Field(..., min_length=1)
    team_id: str = ""
    role: str = "member"
    permissions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MembershipUpdateRequest(BaseModel):
    team_id: str | None = None
    permissions: list[str] | None = None
    metadata: dict[str, Any] | None = None


class MembershipResponse(BaseModel):
    membership_id: str = ""
    actor_id: str = ""
    society_id: str = ""
    team_id: str = ""
    roles: list[str] = Field(default_factory=list)
    status: str = "active"
    start_time: float = 0.0
    end_time: float | None = None
    permissions: list[str] = Field(default_factory=list)
    trust_score: float = 0.5
    metadata: dict[str, Any] = Field(default_factory=dict)


class RoleAssignRequest(BaseModel):
    role: str = Field(..., min_length=1)


class TrustUpdateRequest(BaseModel):
    trust_score: float = Field(..., ge=0.0, le=1.0)
    factors: dict[str, float] = Field(default_factory=dict)


class TrustResponse(BaseModel):
    actor_id: str = ""
    trust_score: float = 0.5
    evidence_count: int = 0
    factors: dict[str, float] = Field(default_factory=dict)


class ReputationResponse(BaseModel):
    actor_id: str = ""
    evidence_count: int = 0
    audit_entries: int = 0


class DelegationCreateRequest(BaseModel):
    delegate_actor_id: str = Field(..., min_length=1)
    permissions: list[str] = Field(default_factory=list)
    valid_until: float | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class DelegationResponse(BaseModel):
    delegation_id: str = ""
    membership_id: str = ""
    delegate_actor_id: str = ""
    permissions: list[str] = Field(default_factory=list)
    valid_from: float = 0.0
    valid_until: float | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    revoked: bool = False
    revoked_at: float | None = None


class SocietyActivationRequest(BaseModel):
    actor_id: str = Field(..., min_length=1)
    goal: str = ""


class ActivatedSocietyResponse(BaseModel):
    society_id: str = ""
    reason: str = ""


class SocietyActivationResponse(BaseModel):
    actor_id: str = ""
    goal: str = ""
    activated: list[ActivatedSocietyResponse] = Field(default_factory=list)
    activated_policy_names: list[str] = Field(default_factory=list)


# ── Actors ─────────────────────────────────────────────────────────────────


class ActorCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    actor_type: str = "ai_agent"
    description: str = ""
    capabilities: list[dict[str, Any]] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    policies: list[str] = Field(default_factory=list)
    trust_level: float = 0.5
    ownership: str = ""
    society_id: str = ""
    objective: str = ""
    """Optimization objective: 'cost', 'speed', 'reliability', or '' for default."""
    metadata: dict[str, Any] = Field(default_factory=dict)
    """Free-form actor metadata. Convention: metadata["strategy"] =
    {"preferences": {...}, "resources": {...}, "risk_tolerance": 0.5,
    "negotiation_policy": "cooperative"|"competitive"|"balanced"} for
    the Game-Theoretic Reasoning runtime (kernel/domains/negotiation.py)."""


class ActorUpdateRequest(BaseModel):
    name: str | None = None
    actor_type: str | None = None
    description: str | None = None
    capabilities: list[dict[str, Any]] | None = None
    goals: list[str] | None = None
    policies: list[str] | None = None
    trust_level: float | None = None
    ownership: str | None = None
    objective: str | None = None
    metadata: dict[str, Any] | None = None
    """Merged (not replaced) into the actor's existing profile.metadata —
    see ActorCreateRequest.metadata above for the strategy-preferences
    convention EvaluateStrategyCapability (kernel/domains/grocery.py)
    reads. Real gap this closes: metadata could only ever be set at
    actor creation; an already-existing actor (e.g. one seeded before
    scripts/seed_world.py's HUMAN_STRATEGY_METADATA was added) had no
    way to acquire it afterward."""


class ActorResponse(BaseModel):
    actor_id: str = ""
    name: str = ""
    actor_type: str = ""
    description: str = ""
    status: str = "active"
    cycle_count: int = 0
    is_active: bool = True
    societies: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    policies: list[str] = Field(default_factory=list)
    objective: str = ""
    trust_level: float = 0.5
    ownership: str = ""


class ActorBeliefsResponse(BaseModel):
    actor_id: str = ""
    beliefs: dict[str, Any] = Field(default_factory=dict)


class ActorMemoryResponse(BaseModel):
    actor_id: str = ""
    memory: list[dict[str, Any]] = Field(default_factory=list)


class ActorGoalsResponse(BaseModel):
    actor_id: str = ""
    goals: list[str] = Field(default_factory=list)


class ActorAddGoalRequest(BaseModel):
    goal: str = Field(..., min_length=1)
    priority: float = 0.0
    # Exact previous goal text to remove first — the Assistant's Update
    # Goal action sets this to the string it persisted last time, so
    # refining a goal replaces the old queued entry instead of leaving
    # near-duplicates ("buy 2L milk...", "buy 1L milk...") stacking up.
    replace_goal: str | None = None


class ActorAffiliationsResponse(BaseModel):
    actor_id: str = ""
    # Every real Affiliation this actor holds (employment/education/
    # family/extended/relationship) — unlike GET /actors/{id}/relationships,
    # not filtered down to the relationship subtype only.
    affiliations: list[dict[str, Any]] = Field(default_factory=list)


class ActorAffiliationCreateRequest(BaseModel):
    affiliation_type: str = Field(..., min_length=1)
    target_id: str = Field(..., min_length=1)
    target_name: str = ""
    trust_level: float = Field(default=0.5, ge=0.0, le=1.0)
    valid_from: str = ""
    valid_until: str = ""
    permissions: list[str] = Field(default_factory=list)
    policies: list[str] = Field(default_factory=list)
    priority: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActorAffiliationUpdateRequest(BaseModel):
    affiliation_type: str | None = Field(default=None, min_length=1)
    target_id: str | None = Field(default=None, min_length=1)
    target_name: str | None = None
    trust_level: float | None = Field(default=None, ge=0.0, le=1.0)
    valid_from: str | None = None
    valid_until: str | None = None
    permissions: list[str] | None = None
    policies: list[str] | None = None
    priority: int | None = None
    metadata: dict[str, Any] | None = None


# ── Cognitive State (Promote Cognitive State to a First-Class Runtime
# Model) — real, persisted artifacts (Intent/Plan/Decision Timeline
# kinds, categorized Memory) as opposed to ActorBeliefsResponse/
# ActorGoalsResponse/ActorMemoryResponse above, which predate this and
# stay as-is for backward compatibility.


class ActorIntentResponse(BaseModel):
    actor_id: str = ""
    intent: dict[str, Any] | None = None


class ActorPlansResponse(BaseModel):
    actor_id: str = ""
    plans: list[dict[str, Any]] = Field(default_factory=list)


class ActorDecisionsResponse(BaseModel):
    actor_id: str = ""
    decisions: list[dict[str, Any]] = Field(default_factory=list)


class ActorExecutionHistoryResponse(BaseModel):
    actor_id: str = ""
    # Each entry: real ExecutionRecord fields, plus reward/actor_loss/
    # duration_ms merged in at read time from the nearest cognitive_tick
    # memory entry for the same actor — omitted (not fabricated) when no
    # match is found within tolerance.
    executions: list[dict[str, Any]] = Field(default_factory=list)


class ActorMemoryCategoryResponse(BaseModel):
    actor_id: str = ""
    category: str = ""
    """semantic | episodic | conversation."""
    items: list[dict[str, Any]] = Field(default_factory=list)


class ActorCognitiveStateResponse(BaseModel):
    """The sprint's own 'New Actor State Model' document structure, in
    one response — composes the same helpers the granular routes above
    use, not a separate computation."""

    actor_id: str = ""
    identity: dict[str, Any] = Field(default_factory=dict)
    presence: dict[str, Any] | None = None
    affiliations: list[dict[str, Any]] = Field(default_factory=list)
    current_society: dict[str, Any] | None = None
    intent: dict[str, Any] | None = None
    goals: list[dict[str, Any]] = Field(default_factory=list)
    goal_history: list[dict[str, Any]] = Field(default_factory=list)
    goal_executions: list[dict[str, Any]] = Field(default_factory=list)
    beliefs: list[dict[str, Any]] = Field(default_factory=list)
    current_plan: dict[str, Any] | None = None
    plan_history: list[dict[str, Any]] = Field(default_factory=list)
    decision: dict[str, Any] | None = None
    decision_history: list[dict[str, Any]] = Field(default_factory=list)
    plan_decision: dict[str, Any] | None = None
    """The most recent plan-hysteresis DecisionRecord specifically
    (metadata.decision_kind == "plan_hysteresis") — additive; `decision`
    above keeps meaning "the most recent decision of any kind" for
    existing consumers."""
    memory_semantic: list[dict[str, Any]] = Field(default_factory=list)
    memory_episodic: list[dict[str, Any]] = Field(default_factory=list)
    memory_conversation: list[dict[str, Any]] = Field(default_factory=list)
    execution_history: list[dict[str, Any]] = Field(default_factory=list)


class ActorCapabilitiesResponse(BaseModel):
    actor_id: str = ""
    capabilities: list[dict[str, Any]] = Field(default_factory=list)


class ActorStatusResponse(BaseModel):
    actor_id: str = ""
    status: str = "active"
    cycle_count: int = 0
    tick_count: int = 0
    enabled: bool = True


class ExperienceRecordRequest(BaseModel):
    kind: str = ""
    text: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    # "private" (default) or "shared" — Cognitive Network (CCB-600): a
    # "shared" experience becomes visible to other actors in the same
    # Society via ContextConstructionEngine's shared-memory pass, gated by
    # co-membership (kernel/pipeline/planning/context_engine.py).
    visibility: str = "private"


class ExperienceRecordResponse(BaseModel):
    actor_id: str = ""
    node_id: str = ""


class ActorTickRequest(BaseModel):
    start: str = ""
    goal: str = ""
    reward: float = 1.0


class ActorTickResponse(BaseModel):
    actor_id: str = ""
    tick_count: int = 0
    result: dict[str, Any] = Field(default_factory=dict)


# ── Runtime ────────────────────────────────────────────────────────────────


class RuntimeResponse(BaseModel):
    name: str = "cognitive"
    status: str = "healthy"
    version: str = "2.0.0"
    components: dict[str, str] = Field(default_factory=dict)


class RuntimeStatusResponse(BaseModel):
    status: str = "healthy"
    cognitive: str = "pending"
    simulation: str = "pending"
    comparator: str = "pending"
    society: str = "pending"


class RuntimeHealthResponse(BaseModel):
    overall: str = "healthy"
    components: dict[str, str] = Field(default_factory=dict)


class RuntimeConfigurationResponse(BaseModel):
    auth_required: bool = True
    rate_limit_rps: float = 100.0
    cors_origins: list[str] = Field(default_factory=list)


class RuntimeMetricsResponse(BaseModel):
    total_requests: int = 0
    total_cycles: int = 0
    avg_latency_ms: float = 0.0


# ── Simulation ─────────────────────────────────────────────────────────────


class SimulateRequest(BaseModel):
    question: str = Field(..., min_length=1)
    run_id: str | None = None
    mode: str = "default"


class SimulateStepRequest(BaseModel):
    question: str = Field(..., min_length=1)
    steps: int = 1


class SimulateScenarioRequest(BaseModel):
    name: str = Field(..., min_length=1)
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class SimulateResponseGateway(BaseModel):
    status: str = "ok"
    predicted_entities: list[dict[str, Any]] = Field(default_factory=list)
    loss: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class SimulateStatusResponse(BaseModel):
    status: str = "idle"
    last_run: float = 0.0
    total_simulations: int = 0


# ── Comparator ─────────────────────────────────────────────────────────────


class CompareRequest(BaseModel):
    question: str = Field(..., min_length=1)
    predicted: dict[str, Any] = Field(default_factory=dict)
    actual: dict[str, Any] = Field(default_factory=dict)


class CompareEpistemicLossRequest(BaseModel):
    predicted: dict[str, Any] = Field(default_factory=dict)
    actual: dict[str, Any] = Field(default_factory=dict)


class CompareResponseGateway(BaseModel):
    status: str = "ok"
    loss: float = 0.0
    details: dict[str, Any] = Field(default_factory=dict)


class CompareHistoryResponse(BaseModel):
    comparisons: list[dict[str, Any]] = Field(default_factory=list)
    total: int = 0


# ── Learning ───────────────────────────────────────────────────────────────


class LearnRequest(BaseModel):
    experience: dict[str, Any] = Field(default_factory=dict)
    learning_type: str = "shared_experience"


class LearnUpdateRequest(BaseModel):
    actor_id: str = Field(..., min_length=1)
    updates: dict[str, Any] = Field(default_factory=dict)


class LearnTrainRequest(BaseModel):
    data: list[dict[str, Any]] = Field(default_factory=list)
    epochs: int = 1


class LearnResponse(BaseModel):
    status: str = "ok"
    result: dict[str, Any] = Field(default_factory=dict)


class LearnStatisticsResponse(BaseModel):
    total_experiences: int = 0
    total_reputations: int = 0
    total_improvements: int = 0


class LearningEventResponse(BaseModel):
    execution_id: str = ""
    actor_id: str = ""
    goal_key: str = ""
    action_key: str = ""
    success: bool = False
    previous: dict[str, Any] | None = None
    updated: dict[str, Any] = Field(default_factory=dict)
    recorded_at: float = 0.0


class LearningEventsResponse(BaseModel):
    events: list[LearningEventResponse] = Field(default_factory=list)


class ActorTransitionEntry(BaseModel):
    goal_key: str = ""
    action_key: str = ""
    transitions: list[dict[str, Any]] = Field(default_factory=list)


class ActorTransitionsResponse(BaseModel):
    actor_id: str = ""
    transitions: list[ActorTransitionEntry] = Field(default_factory=list)


# ── World ──────────────────────────────────────────────────────────────────


class WorldResponse(BaseModel):
    version: int = 0
    entities: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    resources: list[dict[str, Any]] = Field(default_factory=list)


class WorldContextResponse(BaseModel):
    events: list[dict[str, Any]] = Field(default_factory=list)
    event_count: int = 0


class WorldBeliefsResponse(BaseModel):
    actors: list[dict[str, Any]] = Field(default_factory=list)


class WorldEntitiesResponse(BaseModel):
    entities: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0


class WorldRelationshipsResponse(BaseModel):
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0


class WorldQueryRequest(BaseModel):
    query: str = Field(..., min_length=1)
    filters: dict[str, Any] = Field(default_factory=dict)


class WorldQueryResponse(BaseModel):
    results: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0


class WorldEntityCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    entity_type: str = "entity"
    attributes: dict[str, Any] = Field(default_factory=dict)
    state: dict[str, Any] = Field(default_factory=dict)
    provenance: str = ""
    owner_society_id: str = ""
    """Empty (default) = globally visible. Set to restrict observation to
    actors with an active Membership in that society."""


class WorldEntityUpdateRequest(BaseModel):
    attributes: dict[str, Any] = Field(default_factory=dict)
    state: dict[str, Any] = Field(default_factory=dict)
    owner_society_id: str | None = None


class WorldRelationshipCreateRequest(BaseModel):
    source_id: str = Field(..., min_length=1)
    target_id: str = Field(..., min_length=1)
    kind: str = "related_to"
    attributes: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0


class WorldEventCreateRequest(BaseModel):
    event_type: str = "state_change"
    entity_id: str = ""
    description: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0
    source_actor_id: str = ""


class WorldResourceCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    resource_type: str = ""
    quantity: float = 0.0
    unit: str = ""
    location_id: str = ""
    owner_id: str = ""


class WorldResourceUpdateRequest(BaseModel):
    name: str | None = None
    resource_type: str | None = None
    quantity: float | None = None
    unit: str | None = None
    location_id: str | None = None
    owner_id: str | None = None


class WorldLocationCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    address: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    attributes: dict[str, Any] = Field(default_factory=dict)


class WorldLocationUpdateRequest(BaseModel):
    name: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    attributes: dict[str, Any] | None = None


# ── Discovery ──────────────────────────────────────────────────────────────


class ProviderResponse(BaseModel):
    providers: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0


class CapabilityResponse(BaseModel):
    capabilities: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0


class AgentResponse(BaseModel):
    agents: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0


class ModelResponse(BaseModel):
    models: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0


# ── Admin ──────────────────────────────────────────────────────────────────


class BootRequest(BaseModel):
    force: bool = False


class BootResponse(BaseModel):
    status: str = "booted"
    components: dict[str, str] = Field(default_factory=dict)


class ShutdownResponse(BaseModel):
    status: str = "shutdown"


class ReloadResponse(BaseModel):
    status: str = "reloaded"
    modules_reloaded: int = 0


class LogsResponse(BaseModel):
    logs: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0


class ConfigurationResponse(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


class VersionResponse(BaseModel):
    version: str = "2.0.0"
    build: str = ""
    runtime: str = "monkeybrain"


# ── Backup / Restore (Gate 6 — Persistence) ─────────────────────────────


class BackupResponse(BaseModel):
    schema_version: int = 0
    created_at: float = 0.0
    available: bool = True
    reason: str = ""
    key_count: int = 0
    keys: dict[str, Any] = Field(default_factory=dict)


class RestoreRequest(BaseModel):
    backup: dict[str, Any] = Field(..., description="A BackupResponse previously returned by POST /backup")
    overwrite: bool = Field(
        False,
        description="Refuse to touch a key that already exists unless explicitly true — "
        "restore is for repopulating an empty environment, not silently clobbering a live one.",
    )


class RestoreResponse(BaseModel):
    restored: bool = False
    reason: str = ""
    keys_written: int = 0
    keys_skipped_existing: int = 0
    keys_failed: int = 0
    note: str = ""


# ── Commerce (Gate 2 — Production API typing pass, 2026-08-03) ─────────────
# Request models are strict (required fields validated); response models use
# extra="allow" because several routes return dataclasses.asdict() of
# Cart/CartResult/ProductDetail whose exact field set is owned by
# kernel/domains/commerce.py, not this file — declaring the known fields
# here documents the contract for OpenAPI without risking silently dropping
# a real field the dataclass adds later.


class MerchantCreateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    merchant_id: str = Field(..., min_length=1)
    store_name: str = Field(..., min_length=1)
    delivery_fee: float = 0.0
    address: str = ""


class MerchantResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    success: bool = True
    store_id: str = ""


class RiderCreateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str = Field(..., min_length=1)
    status: str = "available"
    # None (not 0.0) is the real "unconstrained" default
    # select_delivery_riders() (logistics.py) already applies when a
    # rider's attributes has no "capacity" key at all — sending a
    # literal 0.0 would instead cap every rider at zero units and make
    # every delivery fail with "no combination of available riders can
    # carry the full order".
    capacity: float | None = None
    rating: float = 5.0
    estimated_minutes: float = 30.0
    refrigerated: bool = False


class RiderResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    success: bool = True
    rider_id: str = ""


class WalletCreateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    owner: str = Field(..., min_length=1)
    name: str = ""
    account_type: str = "debit"
    balance: float = 0.0
    wallet_id: str = ""


class WalletResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    success: bool = True
    wallet_id: str = ""


class ProductCreateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    store_id: str = Field(..., min_length=1)
    merchant_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    price: float = Field(..., ge=0)
    quantity: int = Field(0, ge=0)


class PantryItemCreateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str = Field(..., min_length=1)
    quantity: int = Field(0, ge=0)
    owner_id: str | None = None
    household_members: list[str] | None = None


class PantryItemResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    success: bool = True
    pantry_id: str = ""


class ProductUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    merchant_id: str = Field(..., min_length=1)


class OrganizationUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")


class ProductResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    success: bool = True
    product_id: str = ""


class BrowseProductsResponse(BaseModel):
    success: bool = True
    products: list[dict[str, Any]] = Field(default_factory=list)


class PromotionCreateRequest(BaseModel):
    merchant_id: str = Field(..., min_length=1)
    sale_price: float = Field(..., ge=0)
    starts_at: float | None = None
    ends_at: float | None = None


class ReviewCreateRequest(BaseModel):
    actor_id: str = Field(..., min_length=1)
    rating: float = Field(..., ge=0, le=5)
    comment: str = ""


class CartItemAddRequest(BaseModel):
    product_id: str = Field(..., min_length=1)
    quantity: int = Field(1, ge=1)


class CouponApplyRequest(BaseModel):
    code: str = Field(..., min_length=1)
    store_id: str | None = None


class CartResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    actor_id: str = ""
    items: list[dict[str, Any]] = Field(default_factory=list)


class InventoryReserveRequest(BaseModel):
    product_id: str = Field(..., min_length=1)
    actor_id: str = Field(..., min_length=1)
    qty: int = Field(1, ge=1)
    hold_seconds: float = Field(5.0, gt=0)


class InventoryConfirmRequest(BaseModel):
    product_id: str = Field(..., min_length=1)
    actor_id: str = Field(..., min_length=1)


class InventoryBackorderRequest(BaseModel):
    product_id: str = Field(..., min_length=1)
    actor_id: str = Field(..., min_length=1)
    qty: int = Field(1, ge=1)


class InventoryFulfillBackordersRequest(BaseModel):
    product_id: str = Field(..., min_length=1)


class InventoryActionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    success: bool = True
    message: str = ""


# ── Orders ───────────────────────────────────────────────────────────────


class OrderCreateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    actor_id: str = Field(..., min_length=1)
    items: list[dict[str, Any]] = Field(..., min_length=1)
    question: str = "deliver my order"
    resume_order_id: str | None = None


class OrderResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    success: bool = True
    order_id: str = ""


class OrderPaymentRequest(BaseModel):
    actor_id: str = Field(..., min_length=1)
    total: float | None = None


class OrderCancelRequest(BaseModel):
    actor_id: str | None = None


class OrderConfirmReceiptRequest(BaseModel):
    actor_id: str | None = None


class OrderReturnRequest(BaseModel):
    actor_id: str | None = None
    reason: str = ""


class OrderReturnApproveRequest(BaseModel):
    approved_by: str | None = None


class OrderRefundRequest(BaseModel):
    amount: float | None = Field(None, ge=0)
    reason: str = ""
    refunded_by: str | None = None


# ── Fulfillment ──────────────────────────────────────────────────────────


class PickRequest(BaseModel):
    store_id: str = Field(..., min_length=1)


class PackRequest(BaseModel):
    items: list[dict[str, Any]] = Field(..., min_length=1)
    box_capacity: int = Field(12, ge=1)


class ShipmentCreateRequest(BaseModel):
    order_id: str = Field(..., min_length=1)
    packages: list[dict[str, Any]] = Field(..., min_length=1)
    rider_id: str | None = None
    carrier: str | None = None


class ShipmentResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    success: bool = True
    shipment_id: str = ""


class ShipmentLostRequest(BaseModel):
    reported_by: str | None = None


class ShipmentDelayRequest(BaseModel):
    reason: str = Field(..., min_length=1)
    new_eta: float | None = None


# ── Events ───────────────────────────────────────────────────────────────


class EventCreateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str = Field(..., min_length=1)
    space_id: str | None = None
    description: str = ""


class EventResponse(BaseModel):
    success: bool = True
    event: dict[str, Any] = Field(default_factory=dict)
    evacuated: list[dict[str, Any]] = Field(default_factory=list)
    coordination_trace: list[dict[str, Any]] = Field(default_factory=list)
    execution_scope: dict[str, Any] = Field(default_factory=dict)


class AskActorRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    question: str = Field(..., min_length=1)
    from_actor_id: str | None = None
    from_actor_name: str | None = None


class AskActorResponse(BaseModel):
    success: bool = True
    question: str = ""
    answer: str = ""
    actor_id: str = ""
    actor_name: str = ""
    society_id: str = ""
    society_name: str = ""
    from_actor_id: str | None = None
    from_actor_name: str | None = None


class ActorChatWebResult(BaseModel):
    title: str = ""
    url: str = ""


class ActorChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    message: str = Field(..., min_length=1)


class ActorChatResponse(BaseModel):
    answer: str = ""
    # Which real source actually grounded the answer — "knowledge_graph"
    # (this actor's own real KG facts matched the question), "web_search"
    # (Tavily results, only reached when the KG had nothing), or
    # "general_knowledge" (neither had anything — a direct, ungrounded
    # LLM answer, honestly labeled as such rather than silently passed
    # off as grounded).
    source: str = "general_knowledge"
    facts_used: list[str] = Field(default_factory=list)
    web_results: list[ActorChatWebResult] = Field(default_factory=list)
    timestamp: float = 0.0
    correlation_id: str = ""
    causation_id: str = ""


class GoalDraft(BaseModel):
    """A structured goal being conversationally refined in Goal mode —
    never persisted on its own; only Create/Update Goal (which reuse
    POST /actors/{id}/goals) turn a draft into a real, queued goal."""

    objective: str = ""
    actor: str = ""
    constraints: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    success_conditions: list[str] = Field(default_factory=list)


class GoalDraftRequest(BaseModel):
    message: str = Field(..., min_length=1)
    current_draft: GoalDraft | None = None


class GoalDraftResponse(BaseModel):
    draft: GoalDraft
    update_summary: str = ""


class WebSearchChatRequest(BaseModel):
    query: str = Field(..., min_length=1)


class WebSearchChatSource(BaseModel):
    title: str = ""
    url: str = ""


class WebSearchChatResponse(BaseModel):
    answer: str = ""
    sources: list[WebSearchChatSource] = Field(default_factory=list)


class ExecutionChatMessage(BaseModel):
    role: str
    content: str


class ExecutionChatEvidence(BaseModel):
    type: str
    label: str
    ref: str = ""


class ExecutionChatRequest(BaseModel):
    """The Execution Debugger's chat copilot. `context` is the SAME
    ContextSnapshotDto + SemanticMemoryDto blob the debugger UI already
    fetched and is rendering — sent as-is rather than re-derived
    server-side, so the chat can never disagree with what's on screen,
    and so it works identically for the client-side demo execution
    (which has no real backend record to look up by ID)."""

    model_config = ConfigDict(extra="allow")
    execution_id: str
    actor_id: str = ""
    actor_name: str = ""
    goal: str = ""
    status: str = ""
    question: str = Field(..., min_length=1)
    history: list[ExecutionChatMessage] = Field(default_factory=list)
    selected_context: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class ExecutionChatResponse(BaseModel):
    answer: str
    evidence: list[ExecutionChatEvidence] = Field(default_factory=list)


class TransactionRequest(BaseModel):
    objective: str = Field(..., min_length=1)
    max_steps: int = Field(default=8, ge=1, le=32)


class TransactionStepResponse(BaseModel):
    step_number: int
    target_actor_id: str
    message: str
    trace: dict[str, Any] | None = None
    next_action: str
    next_action_reason: str
    strategic_context: dict[str, Any] | None = None
    """Game-theoretic grounding for this round's decision (suggested_action,
    equilibrium, utilities, rationale) — see
    TransactionCoordinator._strategic_context(). None when unavailable."""
    timestamp: float = 0.0


class TransactionResponse(BaseModel):
    transaction_id: str
    originating_actor_id: str
    objective: str
    status: str
    steps: list[TransactionStepResponse] = Field(default_factory=list)
    societies_involved: list[str] = Field(default_factory=list)
    affiliates_contacted: list[str] = Field(default_factory=list)
    duration_ms: float = 0.0
    final_outcome: str = ""
    timestamp: float = 0.0
    stream_url: str = ""
    """WS /ws/transactions/{transaction_id} — where step 9's live
    negotiation events (transaction_started/negotiation_trace/
    step_completed/transaction_completed) were streamed to while this
    ran; by the time this HTTP response returns, the transaction is
    already over, so this is only useful if the caller subscribed
    before/while POSTing."""


# ── Presence ─────────────────────────────────────────────────────────────


class OccupancyResponse(BaseModel):
    occupancy: list[dict[str, Any]] = Field(default_factory=list)


class PresenceRecordResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    actor_id: str = ""
    space_id: str = ""


class SpaceOccupantsResponse(BaseModel):
    space_id: str = ""
    timestamp: float | None = None
    actor_ids: list[str] = Field(default_factory=list)


class PresenceHistoryResponse(BaseModel):
    actor_id: str = ""
    history: list[dict[str, Any]] = Field(default_factory=list)


class SpacePresenceHistoryResponse(BaseModel):
    space_id: str = ""
    history: list[dict[str, Any]] = Field(default_factory=list)


# ── Verify ───────────────────────────────────────────────────────────────


class VerifyReportResponse(BaseModel):
    ok: bool = True
    violation_count: int = 0
    violations: list[dict[str, Any]] = Field(default_factory=list)
    categories: dict[str, int] = Field(default_factory=dict)
    checked_at: float = 0.0
