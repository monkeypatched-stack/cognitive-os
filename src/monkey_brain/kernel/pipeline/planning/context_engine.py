"""ContextConstructionEngine — assembles a rich, actor-specific
PlanningContext from every retrieval source built across this session
(Context-Aware Personalized Planning refactor).

    Goal -> Goal Embedding -> CognitiveMemory search -> metadata filtering
         -> Timeline retrieval -> World retrieval -> Organizational
            retrieval -> Knowledge exploration + graph expansion
         -> Policy resolution -> Ranking -> Deduplication -> PlanningContext

The Contextual Planner (LLMPlanner, kernel/pipeline/
llm_planner.py) never retrieves memories/knowledge/policies itself —
it only ever reads the PlanningContext this engine hands it. Every stage
below is a small, individually-testable private method.

This is also the component that functionally fulfills the actor-coordination
architecture spec's "SittingFace" role (retrieval-augmented grounding of an
LLM prompt from conversations/negotiations/experiences/organizational
knowledge, distinct from the authoritative Global World State graph) — the
real `src/sittingface` package is an unrelated somatic-chart/codegen
knowledge base, not this. See grounding_snippets() below for the narrow
entry point kernel/society/transaction.py uses.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

from src.monkey_brain.kernel.pipeline.planning.domain import (
    PlanningContext,
    RetrievedItem,
)
from src.monkey_brain.kernel.timeline.entry import TimelineKind

logger = logging.getLogger("agentos.pipeline.planning.context_engine")

# kernel/domains/grocery.py registers every capability -- grocery AND
# robot (kernel/domains/robot.py's Arm/Takeoff/Waypoint/Land/Heartbeat)
# -- on ONE shared CommerceCapabilityBus; vertical_router.py's own
# docstring calls this "the only vertical this codebase has" and
# documents a real second vertical as future work, not yet built.
# Confirmed live this is a genuinely different domain, not a cosmetic
# label difference: a robot actor's LLM prompt offered the full mixed
# list produced a plan that tried "ProductSelection" on a hallucinated
# "weapon_001" for what was really an "Arm the drone" step -- grocery
# and flight-control capabilities read as interchangeable options to a
# small model with no domain boundary drawn between them at all.
# _retrieve_available_capabilities below draws that boundary without
# requiring a second real VerticalRuntime/bus (a much larger change
# touching planner/plan_validator/context_projector wiring too) --
# same shared bus, but each actor's own PROMPT only ever lists the
# subset of names relevant to its own domain.
# Heartbeat (robot.py's own generic ROS liveness capability, built for
# the swarm-readiness audit) is NOT one of Px4RosExecutionAdapter's four
# implemented operations (robot.py's own docstring: "these expose
# exactly the four operations Px4RosExecutionAdapter.invoke() actually
# implements (Arm/Takeoff/Waypoint/Land)") -- confirmed live, offering
# it to a PX4-bound drone got "unsupported PX4 capability: Heartbeat"
# every time, since the adapter simply never claims to support that
# name. Left out of the offered set entirely: it isn't a flight action
# this mission needs, and it isn't compatible with this specific
# adapter regardless of what plan asks for it.
_ROBOT_CAPABILITY_NAMES = frozenset({"Arm", "Takeoff", "Waypoint", "Land"})
# Cross-domain primitives every actor legitimately needs regardless of
# domain, split by whether they need a real counterparty to succeed.
# SELF-CONTAINED ones never reference another actor by name/id, so
# there's nothing for a small model to hallucinate a target for.
# COORDINATION ones (AskActor, DelegateTask, ...) take a target_actor/
# counterparty parameter the model must supply verbatim -- confirmed
# live this is exactly where the same small-model-hallucination pattern
# (_ROBOT_CAPABILITY_NAMES's own "weapon_001" case, belief_runtime.py's
# "resource:drone_control" case) shows up a third time: a robot mission
# with no actual need to coordinate with anyone still got an unprompted
# "AskActor" step aimed at a fabricated "DronePilot_a1b2c3d4" that was
# never a real actor, which then blocked the whole dependency chain
# behind it (AskActorCapability's own target-resolution correctly
# rejects the fake name -- the damage is the plan built a real flight
# mission's success on top of a coordination step it never needed in
# the first place). A robot flying a direct, self-contained mission has
# no standing reason to delegate to or ask a colleague by default, so
# it's only ever offered the self-contained subset; non-robot actors
# still get the full set, coordination capabilities included.
_SELF_CONTAINED_UNIVERSAL_CAPABILITY_NAMES = frozenset(
    {
        "Counterfactual",
        "Explain",
        "AnswerQuestion",
        "ReportWorldPerturbation",
        "recall",
    }
)
_COORDINATION_UNIVERSAL_CAPABILITY_NAMES = frozenset(
    {
        "DelegationCheck",
        "SocietyQuery",
        "AskActor",
        "DelegateTask",
        "BroadcastToAffiliation",
        "RespondToInquiry",
        "EvaluateStrategy",
        "CompeteForResource",
        "RecordAgreement",
        "GetAgreements",
    }
)
_UNIVERSAL_CAPABILITY_NAMES = _SELF_CONTAINED_UNIVERSAL_CAPABILITY_NAMES | _COORDINATION_UNIVERSAL_CAPABILITY_NAMES

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "for",
        "to",
        "of",
        "and",
        "or",
        "in",
        "on",
        "at",
        "is",
        "are",
        "was",
        "were",
        "this",
        "that",
        "i",
        "we",
        "you",
        "please",
        "right",
        "now",
        "today",
        "immediately",
        "away",
        "with",
        "from",
        "by",
        "as",
        "it",
        "its",
        "be",
        "do",
        "does",
        "did",
        "goal",
        "completed",
        "failed",
        "complete",
        "partially",
        # Generic transactional verbs — the constant request-shape ("buy X",
        # "purchase X", "order X") across virtually every commerce goal in this
        # domain, not a distinguishing signal between e.g. a coffee request and
        # an eggs request.
        "buy",
        "buying",
        "purchase",
        "purchasing",
        "order",
        "ordering",
        "get",
        "acquire",
    }
)


def _content_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t and t not in _STOPWORDS}


class ContextConstructionEngine:
    def __init__(
        self,
        planetary_runtime: Any = None,
        memory_manager: Any = None,
        knowledge_graph: Any = None,
        timeline_store: Any = None,
        capability_bus: Any = None,
    ) -> None:
        self._planetary_runtime = planetary_runtime
        self._memory_manager = memory_manager
        self._knowledge_graph = knowledge_graph
        if timeline_store is None:
            from src.monkey_brain.kernel.timeline.store import TimelineStore

            timeline_store = TimelineStore()
        self._timeline_store = timeline_store
        self._capability_bus = capability_bus
        from src.monkey_brain.kernel.knowledge.sittingface_retrieval import (
            get_external_knowledge_retriever,
        )

        self._external_knowledge_retriever = get_external_knowledge_retriever()

    def set_external_knowledge_retriever(self, retriever: Any) -> None:
        self._external_knowledge_retriever = retriever

    def build(self, actor_id: str, goal: Any, execution_id: str = "") -> PlanningContext:
        goal_text = f"{getattr(goal, 'name', '')} {getattr(goal, 'description', '')}".strip()
        ext_report = self._external_knowledge_retriever.retrieve_sync(goal_text, cycle_id=execution_id)
        return self._build_context(
            actor_id,
            goal,
            execution_id=execution_id,
            goal_text=goal_text,
            external_knowledge=ext_report.to_retrieved_items(),
            external_metadata=ext_report.to_metadata(),
        )

    async def build_async(self, actor_id: str, goal: Any, execution_id: str = "") -> PlanningContext:
        goal_text = f"{getattr(goal, 'name', '')} {getattr(goal, 'description', '')}".strip()
        ext_report = await self._external_knowledge_retriever.retrieve(goal_text, cycle_id=execution_id)
        return self._build_context(
            actor_id,
            goal,
            execution_id=execution_id,
            goal_text=goal_text,
            external_knowledge=ext_report.to_retrieved_items(),
            external_metadata=ext_report.to_metadata(),
        )

    def _build_context(
        self,
        actor_id: str,
        goal: Any,
        *,
        execution_id: str,
        goal_text: str,
        external_knowledge: tuple = (),
        external_metadata: dict | None = None,
    ) -> PlanningContext:

        retrieval_started = time.perf_counter()
        retrieval_latency: dict[str, float] = {}

        # Minimal Lemon metrics layer: exactly the 5 sources the spec
        # names (knowledge_graph, semantic_memory, context_stream,
        # world_state, affiliation_graph) — this closure also retrieves
        # timeline/organizational/actor_profile/reachable_colleagues/
        # presence_history/capabilities, which the spec didn't ask to
        # instrument, so those stay untouched (no metric for every
        # retrieval stage, only the named ones).
        _GROUNDING_METRIC_SOURCES = frozenset(
            {
                "knowledge_graph",
                "semantic_memory",
                "context_stream",
                "world_state",
                "affiliation_graph",
            }
        )

        def retrieve(stage: str, fn):
            from src.monkey_brain.kernel.compile import _obs

            started = time.perf_counter()
            track = stage in _GROUNDING_METRIC_SOURCES
            try:
                value = fn()
            except Exception:
                if track:
                    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
                    _obs.counter("grounding.requests.total", source=stage, status="error")
                    _obs.histogram("grounding.duration_ms", elapsed_ms, source=stage)
                raise
            elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
            retrieval_latency[stage] = elapsed_ms
            if track:
                # value is always a fixed-arity tuple (unpacked by the
                # caller into named variables, e.g. (experiences,
                # conversations, executions)) — a bare `if value` is
                # always truthy regardless of whether the actual
                # retrieved data is empty, so "empty" means every
                # element inside it is itself empty/falsy.
                is_empty = isinstance(value, tuple) and all(not v for v in value)
                status = "empty" if is_empty else "success"
                _obs.counter("grounding.requests.total", source=stage, status=status)
                _obs.histogram("grounding.duration_ms", elapsed_ms, source=stage)
            return value

        experiences, conversations, executions = retrieve(
            "semantic_memory",
            lambda: self._search_memory(actor_id, goal_text, goal_name=getattr(goal, "name", "")),
        )
        current_location, current_beliefs, relevant_goals = retrieve(
            "timeline", lambda: self._retrieve_timeline(actor_id)
        )
        available_resources, relevant_locations, relevant_objects = retrieve(
            "world_state", lambda: self._retrieve_world(actor_id, goal_text)
        )
        current_society_context, current_team_context, active_policies = retrieve(
            "organizational", lambda: self._retrieve_organizational(actor_id, goal_text)
        )
        relevant_knowledge, relevant_relationships = retrieve(
            "knowledge_graph",
            lambda: self._explore_knowledge(actor_id, goal_text, current_beliefs),
        )
        incoming_messages, negotiation_updates, relevant_context_events = retrieve(
            "context_stream",
            lambda: self._retrieve_context_stream(actor_id, goal_text=goal_text, execution_id=execution_id),
        )
        actor_profile = retrieve("actor_profile", lambda: self._retrieve_actor_profile(actor_id))
        active_memberships = retrieve("affiliation_graph", lambda: self._retrieve_active_memberships(actor_id))
        reachable_colleagues = retrieve(
            "reachable_colleagues",
            lambda: self._retrieve_reachable_colleagues(actor_id),
        )
        presence_history = retrieve("presence_history", lambda: self._retrieve_presence_history(actor_id))
        available_capabilities = retrieve("capabilities", self._retrieve_available_capabilities)

        activated_ids = set(current_society_context.activated_society_ids()) if current_society_context else set()
        shared_goals = self._retrieve_shared_goals(activated_ids)
        shared_resources = self._retrieve_shared_resources(activated_ids)
        network_facts = self._retrieve_commerce_network(goal_text, activated_ids)

        experiences = self._rank_and_dedupe(experiences, activated_ids)
        conversations = self._rank_and_dedupe(conversations, activated_ids)
        executions = self._rank_and_dedupe(executions, activated_ids)
        relevant_knowledge = self._rank_and_dedupe(relevant_knowledge, activated_ids)
        relevant_relationships = self._rank_and_dedupe(relevant_relationships, activated_ids)

        return PlanningContext(
            actor_id=actor_id,
            goal=goal,
            current_beliefs=current_beliefs,
            current_location=current_location,
            current_society_context=current_society_context,
            current_team_context=current_team_context,
            active_policies=active_policies,
            available_capabilities=available_capabilities,
            available_resources=available_resources,
            actor_profile=actor_profile,
            relevant_experiences=experiences,
            relevant_conversations=conversations,
            relevant_executions=executions,
            relevant_knowledge=relevant_knowledge,
            relevant_external_knowledge=external_knowledge,
            relevant_relationships=relevant_relationships,
            relevant_context_events=relevant_context_events,
            incoming_messages=incoming_messages,
            negotiation_updates=negotiation_updates,
            relevant_locations=relevant_locations,
            relevant_objects=relevant_objects,
            relevant_goals=relevant_goals,
            temporal_context={"built_at": time.time()},
            metadata={
                "active_memberships": active_memberships,
                "reachable_colleagues": reachable_colleagues,
                "presence_history": presence_history,
                "shared_goals": shared_goals,
                "shared_resources": shared_resources,
                "commerce_network_facts": network_facts,
                "retrieval_latency_ms": retrieval_latency,
                "retrieval_total_latency_ms": round((time.perf_counter() - retrieval_started) * 1000, 3),
                "affiliation_lookup_performed": self._planetary_runtime is not None,
                **(external_metadata or {}),
            },
        )

    def grounding_snippets(self, actor_id: str, query_text: str, limit: int = 3) -> list[str]:
        """Negotiation-scoped grounding: this is the component functionally
        fulfilling the architecture spec's "SittingFace" role for actor
        coordination (retrieve relevant prior conversations/negotiations/
        experiences before an LLM invocation) — reused here via
        _search_memory rather than a full build() PlanningContext, since a
        transaction message only needs a few short content snippets, not an
        entire planning-cycle context assembly. TransactionCoordinator
        (kernel/society/transaction.py) calls this to ground the messages
        it sends to affiliates. Returns [] (non-fatal) if there's no memory
        manager configured or nothing relevant is found."""
        if not query_text:
            return []
        experiences, conversations, executions = self._search_memory(actor_id, query_text)
        items = sorted(
            experiences + conversations + executions,
            key=lambda item: item.retrieval_score,
            reverse=True,
        )
        return [item.content for item in items[:limit] if item.content]

    def context_stream_version(self) -> int:
        """Current SocietyContextStream version — a cheap, monotonic
        watermark (no I/O, just an in-memory counter) callers can store
        and later pass to has_new_relevant_activity() to ask "has
        anything actually changed since then." 0 if there's no
        PlanetaryRuntime (context stream unreachable) -- matches
        has_new_relevant_activity's own fail-open behavior in that case
        (see below)."""
        if self._planetary_runtime is None:
            return 0
        try:
            return self._planetary_runtime.context_stream.version
        except Exception:
            return 0

    def has_new_relevant_activity(self, actor_id: str, since_version: int) -> bool:
        """Incremental scheduling: is there any new Context Stream event,
        since since_version, that _retrieve_context_stream would actually
        have surfaced to this actor's grounding? Reuses that method's
        exact relevance filter (an INTERACTION addressed to this actor;
        a WORLD_UPDATE with real domain/perturbation significance) rather
        than a cruder "any event at all" check, which in an active
        multi-actor world would almost never stay quiet long enough to
        matter (every actor's own routine bookkeeping publishes
        constantly) — this checks specifically for the kind of event
        that would change what grounding produces for THIS actor.

        Fails open (returns True, i.e. "assume something changed, do a
        real replan") on any error or missing PlanetaryRuntime — a
        skip-gate must never be the thing that goes quiet when it breaks.
        """
        if self._planetary_runtime is None:
            return True
        try:
            from src.monkey_brain.kernel.society.context_stream import ContextEventType

            stream = self._planetary_runtime.context_stream
            from_version = since_version + 1
            if stream.replay(
                from_version=from_version,
                event_type=ContextEventType.INTERACTION,
                actor_id=actor_id,
            ):
                return True
            for event in stream.replay(from_version=from_version, event_type=ContextEventType.WORLD_UPDATE):
                payload = event.payload if isinstance(event.payload, dict) else {}
                if payload.get("domain_event") or payload.get("source") == "external_perturbation":
                    return True
            return False
        except Exception:
            logger.debug(
                "has_new_relevant_activity: check failed (fail-open -- treated as changed)",
                exc_info=True,
            )
            return True

    # ── Retrieval stages ──────────────────────────────────────────────

    def _search_memory(self, actor_id: str, goal_text: str, goal_name: str = "") -> tuple[tuple, tuple, tuple]:
        """CognitiveMemory (kernel/learn/memory) vector search, split into
        experience/conversation/execution buckets by each hit's recorded
        `kind`. Other kinds (e.g. "membership_event" bookkeeping written by
        the Membership registry's audit trail) are retrievable from
        CognitiveMemory for other purposes but are not planning-relevant
        experiences, so they are excluded here rather than silently
        defaulting into the experiences bucket.

        Cognitive Network (CCB-600): a second, bounded pass surfaces OTHER
        actors' experiences explicitly recorded with metadata["visibility"]
        == "shared" — but only ones the calling actor may receive per
        _may_receive_shared_experience (co-membership in at least one
        Society). Items from this pass are tagged source=
        "cognitive_memory_shared" and evidence_ids=("actor:<origin>",) so
        the planner/scoring layer can always tell "my memory" from "a peer's
        published experience" apart — never silently merged as if it were
        the actor's own.

        Distinctive-keyword gate: cosine similarity alone doesn't
        discriminate here — every experience this actor has ever recorded
        shares the SAME constant standing-goal boilerplate ("buy groceries
        efficiently ..."), which dominates a short-sentence SBERT embedding
        far more than the one distinguishing word (confirmed live: a
        "Purchase Ground Coffee" query's top-scored match was an unrelated
        "Purchase 1 dozen large eggs" experience, coffee ranked below it).
        goal_name is the actor's own constant standing-goal text — real
        distinctive tokens are whatever's left in goal_text once goal_name's
        words are removed (i.e. this tick's actual one-off request). When
        that's empty (a pure autonomous tick with no specific triggering
        question), there's nothing distinctive to gate on, so every result
        the vector search returns is kept, unchanged from before this
        existed."""
        if self._memory_manager is None or not goal_text:
            return (), (), ()

        own_nodes = self._memory_manager.search_episodic(goal_text, top_k=20, actor_id=actor_id)
        experiences, conversations, executions = self._bucket_memory_nodes(own_nodes, source="cognitive_memory")

        shared_nodes = self.search_shared_experiences(actor_id, goal_text, top_k=5)
        shared_experiences, shared_conversations, shared_executions = self._bucket_memory_nodes(
            shared_nodes,
            source="cognitive_memory_shared",
        )

        experiences = experiences + shared_experiences
        conversations = conversations + shared_conversations
        executions = executions + shared_executions

        distinctive = _content_tokens(goal_text) - _content_tokens(goal_name)
        if distinctive:
            experiences = [i for i in experiences if _content_tokens(i.content) & distinctive]
            conversations = [i for i in conversations if _content_tokens(i.content) & distinctive]
            executions = [i for i in executions if _content_tokens(i.content) & distinctive]

        return tuple(experiences), tuple(conversations), tuple(executions)

    def search_shared_experiences(self, actor_id: str, query_text: str, top_k: int = 5) -> list[Any]:
        """Cognitive Network (CCB-600): other actors' `visibility="shared"`
        experiences `actor_id` may currently receive, gated by
        _may_receive_shared_experience. Public so callers outside a full
        build() planning cycle (e.g. the GET /actors/{id}/experiences/shared
        route) can retrieve the same set _search_memory folds into
        PlanningContext, without duplicating the gate logic."""
        if self._memory_manager is None or not query_text:
            return []
        nodes = self._memory_manager.search_episodic(query_text, top_k=top_k, actor_id=None)
        return [n for n in nodes if self._is_receivable_shared_node(actor_id, n)]

    def _bucket_memory_nodes(self, nodes: Any, source: str) -> tuple[list, list, list]:
        experiences, conversations, executions = [], [], []
        for node in nodes:
            payload = node.payload
            kind = payload.get("kind", "experience")
            if kind not in ("experience", "conversation", "execution"):
                continue
            item = RetrievedItem(
                content=payload.get("text", ""),
                item_type=kind,
                # record_experience() (belief_runtime.py::
                # _record_episodic_experience) writes a real outcome-derived
                # confidence into metadata for post-execution experiences;
                # anything that never set one (e.g. membership.py's
                # join/leave events) keeps the previous always-1.0 default.
                # Same for `speaker` on real conversation turns (api/routes/
                # actors.py::ask_actor) — without it every conversation item
                # showed the generic retrieval-stage name ("cognitive_memory")
                # instead of who actually said it.
                source=str(payload.get("speaker", source)),
                confidence=float(payload.get("confidence", 1.0)),
                timestamp=payload.get("timestamp", 0.0),
                # Vector backends may return a signed similarity (the hash/BOW
                # fallback can produce a negative cosine score).  Planning
                # context exposes retrieval_score as a confidence-like,
                # non-negative signal; normalize at this boundary so ranking
                # and downstream consumers share one contract.
                retrieval_score=max(0.0, float(payload.get("_retrieval_score", 0.0))),
                evidence_ids=((f"actor:{payload.get('actor_id', '')}",) if source != "cognitive_memory" else ()),
            )
            if kind == "conversation":
                conversations.append(item)
            elif kind == "execution":
                executions.append(item)
            else:
                experiences.append(item)
        return experiences, conversations, executions

    def _is_receivable_shared_node(self, actor_id: str, node: Any) -> bool:
        payload = node.payload
        origin_actor_id = payload.get("actor_id")
        if not origin_actor_id or origin_actor_id == actor_id:
            return False  # actor's own memories are already covered by the private pass
        if payload.get("visibility") != "shared":
            return False
        return self._may_receive_shared_experience(actor_id, origin_actor_id)

    def _may_receive_shared_experience(self, recipient_actor_id: str, origin_actor_id: str) -> bool:
        """Cognitive Network (CCB-600) scope gate: an actor may receive
        another actor's `visibility="shared"` experience only if they
        currently share at least one active Society membership of a real
        organizational type (e.g. the same household) — deliberately
        narrower than the full TrustNetwork permission model
        (kernel/compile/trust.py's Perm.RECEIVE_EXPERIENCES), which governs
        stranger-to-stranger sharing across societies and is out of scope
        until a later phase actually needs it."""
        if self._planetary_runtime is None:
            return False
        registry = getattr(self._planetary_runtime, "membership_registry", None)
        if registry is None:
            return False
        recipient_societies = self._non_generic_societies(recipient_actor_id, registry)
        if not recipient_societies:
            return False
        origin_societies = self._non_generic_societies(origin_actor_id, registry)
        return bool(recipient_societies & origin_societies)

    def _non_generic_societies(self, actor_id: str, registry: Any) -> set[str]:
        """Every actor registered via PlanetaryRuntime.register_actor() is
        implicitly a member of that runtime's single home society
        (typically society_type=="generic", the Society dataclass default,
        e.g. "Planetary Society") — counting that toward co-membership
        would make every actor on the platform a "co-member" of every
        other actor by construction, defeating the gate entirely. Only
        membership in a real organizational relationship (household,
        company, retail_store, ...) counts."""
        result: set[str] = set()
        for society_id in registry.societies_for_actor(actor_id):
            society_runtime = self._planetary_runtime.get_society_runtime(society_id)
            if society_runtime is not None and society_runtime.society.society_type != "generic":
                result.add(society_id)
        return result

    def _retrieve_timeline(self, actor_id: str) -> tuple[Any, tuple, tuple]:
        current_location = self._timeline_store.current(actor_id, TimelineKind.PRESENCE)
        current_beliefs = self._timeline_store.query(actor_id, TimelineKind.BELIEF)
        goals = self._timeline_store.query(actor_id, TimelineKind.GOAL)
        relevant_goals = tuple(g for g in goals if g.status not in ("completed", "cancelled"))
        return current_location, current_beliefs, relevant_goals

    def _retrieve_world(self, actor_id: str = "", goal_text: str = "") -> tuple[tuple, tuple, tuple]:
        resources: tuple = ()
        locations: tuple = ()
        objects: tuple = ()
        if self._planetary_runtime is not None:
            try:
                world = self._planetary_runtime.world_model.semantic_world
                resources = world.resources()
                locations = tuple(e.name for e in world.entities() if e.name)
                objects = tuple(r.name for r in resources if r.name)
            except Exception:
                pass
        # Found live: SharedWorld (world_model.semantic_world above) is only
        # ever written to by the generic admin CRUD routes in
        # api/routes/world.py (add_world_entity/_resource/_location) — the
        # real grocery domain never calls those, so relevant_locations and
        # relevant_objects came back empty for every real execution even
        # though the actor's KnowledgeGraph (what grocery.py actually
        # populates) had real stores and products.
        if not locations and actor_id and self._planetary_runtime is not None:
            # Prefer the actor's own real affiliation graph (kernel/
            # affiliations — the same graph api/routes/actors.py::
            # _walk_affiliation_chain traverses) over a global KG dump: a
            # "commercial" affiliation (customer/supplier/vendor/partner/
            # franchise) names a real store THIS actor is actually grounded
            # to, not just any organization that exists anywhere.
            try:
                for sr in self._planetary_runtime.all_societies():
                    state = sr.get_actor(actor_id)
                    if state is None:
                        continue
                    affiliations = state.actor_runtime.affiliations if state.actor_runtime is not None else None
                    if affiliations is not None:
                        locations = tuple(
                            a.target_name for a in affiliations.all() if a.category == "commercial" and a.target_name
                        )
                    break
            except Exception:
                pass
        # Falls back to the KG — ORGANIZATION entities are stores/locations,
        # ASSET entities are products — when the actor has no affiliations
        # yet (or none commercial) and/or no resources were ever registered.
        if (not locations or not objects) and self._knowledge_graph is not None:
            try:
                kg_entities = self._knowledge_graph.entities
            except Exception:
                kg_entities = []
            if not locations:
                locations = tuple(
                    e.name
                    for e in kg_entities
                    if e.name and getattr(e.entity_type, "value", e.entity_type) == "organization"
                )
            if not objects:
                objects = tuple(
                    e.name for e in kg_entities if e.name and getattr(e.entity_type, "value", e.entity_type) == "asset"
                )
        # Relevance gate: without this, `objects` is the ENTIRE asset
        # catalog (every product across every category) whenever no
        # SharedWorld resources were registered — the common case for the
        # grocery domain (see the comment above). Confirmed live: a "Buy
        # 2L milk" execution's World State showed Ground Coffee, Large
        # Eggs, Sourdough Bread, Chicken Breast alongside the actually
        # relevant milk products. Locations are deliberately left
        # unfiltered — which store carries something is relevant
        # regardless of which specific product is being bought.
        distinctive = _content_tokens(goal_text)
        if distinctive and objects:
            objects = tuple(o for o in objects if _content_tokens(o) & distinctive)
        return resources, locations, objects

    def _retrieve_context_stream(
        self,
        actor_id: str,
        goal_text: str = "",
        execution_id: str = "",
    ) -> tuple[tuple[RetrievedItem, ...], tuple[RetrievedItem, ...], tuple[RetrievedItem, ...]]:
        """Context Grounding: real, recent SocietyContextStream events —
        closes the gap this module's own build() used to leave explicit
        ("never queries ContextStream — recent live events ... aren't
        available to planning").

        Real, empirically-found regression this method used to have and
        no longer does: it originally also queried ContextEventType.
        OBSERVATION and every WORLD_UPDATE unfiltered — confirmed live,
        that pulled in dozens of pure-bookkeeping events per tick ("Actor
        observed the world", "Actor moved to space X", "Society Y hosted
        by Z") with zero decision-relevant content, bloating the LLM
        prompt enough to make the local model's response time balloon.
        OBSERVATION is dropped entirely (an actor's own observations are
        already covered by current_beliefs/relevant_goals above — this
        was already the intent per the "own bookkeeping" framing, just
        not actually excluded before). WORLD_UPDATE is now filtered to
        events that actually carry business/domain significance — either
        a real domain_event tag (ActionExecutor._publish_action_event,
        e.g. "OrderCreated") or a real reported perturbation
        (ReportWorldPerturbationCapability, payload.source ==
        "external_perturbation") — excluding routine geography/membership
        setup noise, which is what "Actor registered"/"moved to space"/
        "hosted by" actually were.

        INTERACTION stays actor-filtered (a message to/from this actor).
        WORLD_UPDATE stays unfiltered by actor_id: a real perturbation is
        published with actor_id=<the reporter>, not the shopper about to
        plan around it — filtering by actor_id here would mean a real
        "Warehouse fire" event could never ground ANY other actor's
        planning, defeating the entire point of reporting it.

        Real-Time World Changes refactor (Context Stream spec): returns
        THREE buckets over the same fetched events (no new queries) —
        (incoming_messages, negotiation_updates, relevant_context_events)
        — instead of one generic list, so the spec's distinct "incoming
        messages" and "negotiation updates" categories are first-class
        PlanningContext fields rather than anonymous context events. An
        INTERACTION event is a message if its payload carries the
        AnswerQuestionCapability ask/answer shape (`question`), a
        negotiation update if it carries `interaction_id` (the older
        InteractionManager negotiation path) or `transaction_id` (the
        newer TransactionCoordinator path — see
        TransactionCoordinator._publish_negotiation_context_event); every
        other event (including WORLD_UPDATE) stays general grounding."""
        if self._planetary_runtime is None:
            return (), (), ()
        try:
            from src.monkey_brain.kernel.society.context_stream import ContextEventType

            stream = self._planetary_runtime.context_stream
            events: list[Any] = list(stream.replay(event_type=ContextEventType.INTERACTION, actor_id=actor_id))
            distinctive = _content_tokens(goal_text)
            for event in stream.replay(event_type=ContextEventType.WORLD_UPDATE):
                payload = event.payload if isinstance(event.payload, dict) else {}
                if payload.get("source") == "external_perturbation":
                    # Broadcast-relevant regardless of this specific goal's
                    # wording (a warehouse fire matters to every plan it
                    # touches, not just ones mentioning it by name) — kept
                    # unconditionally, same as before this filter existed.
                    events.append(event)
                elif payload.get("domain_event"):
                    # Real gap: this was every domain_event WORLD_UPDATE
                    # system-wide, unfiltered by relevance to what this
                    # actor is actually planning — confirmed live, a
                    # "Buy 2L milk" execution's grounding showed "Product
                    # listed: Wheat Bread"/"Butter"/"Chicken Breast" catalog
                    # noise alongside the actually-relevant milk events.
                    # Gated on the same distinctive-keyword overlap used
                    # for memory/world-object retrieval; skipped only when
                    # there's nothing distinctive to gate on (a pure
                    # autonomous tick), matching that same fallback.
                    if not distinctive or _content_tokens(event.description) & distinctive:
                        events.append(event)
            # Found live: ActionExecutor._publish_action_event already
            # publishes a real, per-step ContextEvent for every action
            # outcome (event_type=ACTION, description="{capability} failed:
            # {error}" for a failure) — but this method never replayed
            # ACTION events at all, so a real step failure had a real,
            # already-descriptive event sitting in the context stream that
            # never reached the debugger. Actor-filtered (like INTERACTION
            # above) since another actor's own step failure isn't this
            # actor's grounding; originally filtered to failures only, on
            # the reasoning that a successful step's ACTION event is
            # redundant with plan_summary/world_changes.
            #
            # Real gap that reasoning missed (found by comparing against
            # the demo Execution Debugger's own reference data, which
            # shows a purchase's own lifecycle — "Delivery agent assigned
            # to Order #ORD-1746", "Order #ORD-1746 marked delivered" —
            # as real Context Events): plan_summary/world_changes are
            # only shown for the CURRENT tick, so a later tick's grounding
            # never sees this actor's OWN recent order/payment/delivery
            # history, only pure lookups (ProductSelection has no
            # domain_event tag — resolve_domain_event returns None for it)
            # and other actors' catalog listings. A successful step with a
            # real domain_event tag (OrderCreated, PaymentCaptured, ...)
            # IS business-meaningful, unlike a bare "ProductSelection
            # succeeded" — so those are kept too, not just failures.
            #
            # Execution-filtered when execution_id is known: without this,
            # an OLDER attempt's own step-failure events (a different
            # execution_id, same actor) stayed mixed into a LATER
            # execution's grounding — confirmed live, a fresh execution's
            # Context Events showed a stale "OrderConfirmation failed: no
            # order to confirm" from a previous, unrelated attempt
            # alongside its own real failures. Falls back to actor-only
            # filtering when execution_id isn't available (e.g.
            # grounding_snippets' negotiation-scoped callers, which never
            # pass one).
            for event in stream.replay(event_type=ContextEventType.ACTION, actor_id=actor_id):
                payload = event.payload if isinstance(event.payload, dict) else {}
                if payload.get("success") is False:
                    # A failure is only relevant to THIS attempt — an
                    # older attempt's own step failure must not resurface
                    # in a later, unrelated execution's grounding (see
                    # comment above).
                    if not execution_id or payload.get("execution_id") == execution_id:
                        events.append(event)
                elif payload.get("success") is True and payload.get("domain_event"):
                    # Unlike a failure, a real business success (Order
                    # Created, PaymentAuthorized, ShipmentCreated, ...) IS
                    # this actor's own genuine history — the whole point
                    # of surfacing it is for a LATER tick to see it, so it
                    # deliberately is NOT execution-scoped the way
                    # failures are.
                    events.append(event)
        except Exception:
            return (), (), ()
        # Dedupe by event_id: an actor with temporary/dual society
        # membership can have the same real event reachable through more
        # than one of the queries above — real grounding evidence should
        # never list the same fact twice.
        seen_ids: set[str] = set()
        deduped = []
        for event in sorted(events, key=lambda e: e.timestamp, reverse=True):
            if event.event_id in seen_ids:
                continue
            seen_ids.add(event.event_id)
            deduped.append(event)
        # Capped at 15 per bucket, not 50 — real grounding, not a full
        # replay; a local LLM's prompt should stay small enough to answer
        # promptly.
        messages: list[RetrievedItem] = []
        negotiations: list[RetrievedItem] = []
        general: list[RetrievedItem] = []
        for event in deduped:
            payload = event.payload if isinstance(event.payload, dict) else {}
            if payload.get("source") == "external_perturbation":
                item_type = "external_perturbation"
            elif getattr(event.event_type, "value", event.event_type) == "action" and payload.get("success") is False:
                item_type = "step_failure"
            else:
                item_type = "context_event"
            content = event.description or event.event_type.value
            # Preserve the operator-reported impact fields when present. They
            # are part of the retrieved event, and are more useful to the
            # debugger than a second, invented explanation of the change.
            if item_type == "external_perturbation":
                affected = payload.get("entity_id") or payload.get("affected_entity_id")
                changes = payload.get("attributes") or payload.get("state")
                if affected:
                    content += f" [affected_entity={affected}]"
                if changes:
                    content += f" [changed={changes}]"
            item = RetrievedItem(
                content=content,
                item_type=item_type,
                source=event.provenance or "context_stream",
                confidence=event.confidence,
                timestamp=event.timestamp,
                evidence_ids=(event.event_id,),
            )
            has_pending_question = payload.get("question") and not payload.get("answer")
            if has_pending_question or (
                payload.get("from_actor_id") and payload.get("to_actor_id") and not payload.get("answer")
            ):
                bucket = messages
            elif payload.get("interaction_id") or payload.get("transaction_id"):
                bucket = negotiations
            elif payload.get("question") and payload.get("answer"):
                # Real gap this closes: an AskActor exchange that already
                # HAS its answer (AskActorCapability publishes both
                # question and answer together once resolved) was still
                # classified as a "pending incoming message" — which
                # means it was surfaced completely unfiltered, forever,
                # regardless of relevance to whatever this actor is
                # planning next. Confirmed live: a resolved "do we need
                # eggs?" exchange showed up under "Recent world events"
                # for a later, unrelated "buy milk" tick and visibly
                # biased the planner into selecting the eggs product
                # instead of milk. A genuinely pending question (no
                # answer yet) still goes to `messages` above, unfiltered
                # — the actor needs to see that regardless of topic.
                # Once resolved, it's just a historical record: gated by
                # the same distinctive-keyword relevance signal
                # _search_memory already applies to experiences/
                # conversations/executions, dropped entirely when
                # irrelevant rather than reclassified into `general`
                # (an unrelated past Q&A has no grounding value here).
                # Reuses the same `distinctive` computed above for the
                # WORLD_UPDATE filtering pass — one goal-relevance signal
                # for this whole method, not two.
                if not distinctive or _content_tokens(content) & distinctive:
                    bucket = general
                else:
                    continue
            else:
                bucket = general
            if len(bucket) < 15:
                bucket.append(item)
        return tuple(messages), tuple(negotiations), tuple(general)

    def _retrieve_organizational(self, actor_id: str, goal_text: str) -> tuple[Any, Any, tuple]:
        if self._planetary_runtime is None:
            return None, None, ()
        try:
            activation_result = self._planetary_runtime.activate_societies(actor_id, goal_text)
        except Exception:
            return None, None, ()
        teams = self._planetary_runtime.teams_for_actor(actor_id)
        team_context = teams[0] if teams else None
        return activation_result, team_context, activation_result.policy_bundle.policies

    def _explore_knowledge(self, actor_id: str, goal_text: str, current_beliefs: tuple = ()) -> tuple[tuple, tuple]:
        if self._knowledge_graph is None or not goal_text:
            return (), ()
        # Found live: a goal ending in punctuation ("Cancel my order.")
        # previously kept the trailing "." glued onto its last word via
        # plain .split() — "order." never matches the keyword index's
        # "order" entry (built via [a-zA-Z]+ tokenization, e.g.
        # grocery.py's own _keywords()), so the actor's own just-created
        # order silently never showed up in "Relevant knowledge" at all.
        # Real regex word extraction matches how the index itself is
        # built, not a stricter filter — same len>2 threshold as before.
        keywords = [w for w in re.findall(r"[a-zA-Z]+", goal_text.lower()) if len(w) > 2]
        entities = [
            e for e in self._knowledge_graph.entities_by_keywords(keywords) if self._may_explore_entity(actor_id, e)
        ]
        # Found live: entities_by_keywords is an unranked union over the
        # WHOLE graph, and an actor's own accumulated memory/order/execution
        # entities (KnowledgeGraphMemoryAdapter writes every recorded
        # experience into this same graph) keep re-matching the standing
        # goal's own recurring words on every tick — so the one product
        # actually being asked about could lose the entities[:10] cutoff to
        # unrelated history while grounding still "succeeds". Real,
        # business-rule-filtered, keyword-scored product candidates (the
        # same resolution ProductSelectionCapability itself uses at
        # execution time) are pulled to the front here so the LLM always has
        # a real id=... fact to copy instead of fabricating one.
        ranked_products = self._ranked_product_candidates(goal_text)
        ranked_ids = {e.entity_id for e in ranked_products}
        entities = ranked_products + [e for e in entities if e.entity_id not in ranked_ids]
        # Found live: a generic standing goal ("buy groceries efficiently")
        # shares no keyword with any KG entity name, so both matches above
        # come back empty — even though this same actor's Semantic Memory
        # "Durable Beliefs" card (api/routes/actors.py::_grouped_beliefs)
        # shows real entities, because THAT card is an all-time aggregate,
        # not scoped to this tick's goal text. Falling back to this actor's
        # own durable subjects keeps the two cards in agreement instead of
        # one silently showing a different scope than the other.
        if not entities:
            entities = self._durable_belief_entities(actor_id, current_beliefs)
        knowledge_items = []
        relationship_items = []
        for entity in entities[:10]:
            # id=... is load-bearing, not decorative: it's the only way the
            # planner can reference a REAL, valid entity id in a step's
            # "parameters" (e.g. ProductSelection's {"selection": [{"id":
            # ...}]}) instead of inventing one or a bare name the capability
            # can't resolve via kg.get_entity(). price is included when
            # present since it's commonly what a goal's stated constraint
            # (e.g. "under $100") needs to be checked against. quantity is
            # included the same way — without it, a request like "buy the
            # last available X" was structurally unanswerable: nothing told
            # the planner which of several identically-described options
            # was actually low on stock, so any pick "worked" only by
            # accident of also being the cheapest default.
            detail = f"id={entity.entity_id}"
            price = entity.attributes.get("price")
            if price is not None:
                detail += f", price=${price}"
            quantity = entity.attributes.get("quantity")
            if quantity is not None:
                detail += f", quantity={quantity}"
            knowledge_items.append(
                RetrievedItem(
                    content=f"{entity.name} ({entity.entity_type.value}, {detail})",
                    item_type="knowledge",
                    source="knowledge_graph",
                    confidence=1.0,
                    retrieval_score=1.0,
                    evidence_ids=(entity.entity_id,),
                )
            )
            for relationship in self._knowledge_graph.relationships_for(entity.entity_id):
                connected_id = (
                    relationship.target_id if relationship.source_id == entity.entity_id else relationship.source_id
                )
                connected = self._knowledge_graph.get_entity(connected_id)
                if connected is not None:
                    relationship_type = getattr(
                        relationship.relationship_type,
                        "value",
                        relationship.relationship_type,
                    )
                    relationship_items.append(
                        RetrievedItem(
                            content=(
                                f"{entity.name} -[{relationship_type}]-> {connected.name} "
                                f"(relationship_id={relationship.relationship_id})"
                            ),
                            item_type="relationship",
                            source="knowledge_graph",
                            confidence=0.8,
                            retrieval_score=0.5,
                            evidence_ids=(
                                relationship.relationship_id,
                                entity.entity_id,
                                connected.entity_id,
                            ),
                        )
                    )
        return tuple(knowledge_items), tuple(relationship_items)

    def _durable_belief_entities(self, actor_id: str, current_beliefs: tuple) -> list[Any]:
        """Fallback for _explore_knowledge when goal-text keyword matching
        finds nothing (a generic standing goal shares no keyword with any
        KG entity name — found live for actor Priya Sharma's real "buy
        groceries efficiently" goal). Resolves this actor's own DURABLE
        belief subjects (observed 2+ times — the same threshold
        api/routes/actors.py::_grouped_beliefs uses for "durable") back to
        the real KG entities they name, via the same entities_by_keywords
        index lookup used above — a belief's subject is always exactly
        entity.name (see kernel/compile/cognitive_actor.py's own belief-
        persistence comment on this). Keeps the Knowledge Graph card and
        the Semantic Memory "Durable Beliefs" card in agreement about what
        this actor currently knows, instead of one silently showing a
        different scope than the other. Bounded to the 10 most-observed
        subjects, one entity per subject — a fallback for "nothing
        goal-specific matched," not a second unfiltered dump."""
        if self._knowledge_graph is None or not current_beliefs:
            return []
        counts: dict[str, int] = {}
        for b in current_beliefs[-200:]:
            subject = getattr(b, "subject", "") or ""
            if subject:
                counts[subject] = counts.get(subject, 0) + 1
        durable_subjects = sorted(
            (s for s, c in counts.items() if c >= 2),
            key=lambda s: counts[s],
            reverse=True,
        )
        seen: set[str] = set()
        resolved: list[Any] = []
        for subject in durable_subjects[:10]:
            keywords = [w for w in re.findall(r"[a-zA-Z]+", subject.lower()) if len(w) > 2]
            for entity in self._knowledge_graph.entities_by_keywords(keywords):
                if entity.entity_id in seen or not self._may_explore_entity(actor_id, entity):
                    continue
                seen.add(entity.entity_id)
                resolved.append(entity)
                break
        return resolved

    def _ranked_product_candidates(self, goal_text: str, limit_per_item: int = 3) -> list[Any]:
        """Real product candidates for goal_text, ranked by keyword-match
        specificity — open_products()/_match_score() are the same
        resolution ProductSelectionCapability's own fallback path uses, run
        here up front so grounding surfaces a real id=... fact for the LLM
        to copy. Grocery-only for now (the sole vertical this codebase
        has); fails open to [] if unavailable, same idiom
        _retrieve_available_capabilities above already uses for the same
        reason.
        """
        try:
            from src.monkey_brain.kernel.domains.grocery import (
                open_products,
                _match_score,
                _split_requested_items,
            )
        except Exception:
            return []
        candidates: list[Any] = []
        seen: set[str] = set()
        for phrase in _split_requested_items(goal_text):
            try:
                products = open_products(self._knowledge_graph, item_phrase=phrase)
            except Exception:
                continue
            scored = sorted(
                (p for p in products if _match_score(p.name, phrase) > 0),
                key=lambda p: _match_score(p.name, phrase),
                reverse=True,
            )
            for product in scored[:limit_per_item]:
                if product.entity_id not in seen:
                    seen.add(product.entity_id)
                    candidates.append(product)
        return candidates

    def _may_explore_entity(self, actor_id: str, entity: Any) -> bool:
        """Domain-knowledge entities (stores, products, ...) are unfiltered
        here, same as always. EpisodicTrace entities (KnowledgeGraphMemory
        Adapter writes every MemoryManager.record_experience() call into
        this same graph — kernel/learn/memory/graph_adapter.py) are
        excluded outright, own or another actor's: relevant_knowledge is
        "what does the KG say about the world," not "what has this actor
        experienced" — that's _search_memory's job, which already has its
        own real privacy/visibility gate (_may_receive_shared_experience
        below) and its own dedicated experience/execution/conversation
        buckets in the debugger. Before this exclusion was unconditional,
        an actor's OWN experience entries (only another actor's were
        gated) leaked into their own Knowledge Graph card as nonsense
        entities like "experience: Completed goal: ..." — confirmed live.

        Same real contamination class, two more shapes of it — found
        live via _durable_belief_entities (below): purchase_log marker
        entities (grocery.py's per-(buyer,product) duplicate-purchase
        marker) and EntityType.EVENT entities (e.g. "Grocery Order",
        which shares one literal name across every order any actor ever
        placed) both leaked into Durable Beliefs as nonsense like
        "Purchase log: Ground Coffee: known". observations.py's
        WorldPollingProvider.observe() and grocery.py's
        AnswerQuestionCapability._gather_facts already exclude both;
        this is the third real caller of the same duck-typed checks,
        checked by raw attribute/string value per this module's own
        "depend on interfaces, never implementations" constraint."""
        if entity.attributes.get("label") == "EpisodicTrace" or entity.attributes.get("purchase_log"):
            return False
        if getattr(entity.entity_type, "value", entity.entity_type) == "event":
            return False
        return True

    def _retrieve_available_capabilities(self) -> tuple[str, ...]:
        """MB-3060: every capability name ActionExecutor can genuinely
        invoke — i.e. has a real .handle() method (belief_runtime.py's
        _execute_plan constructs Action(capability=step.action) and
        ActionExecutor does bus.discover(action.capability).handle(...));
        a DomainCapability like "commerce"/"logistics" only exposes
        .invoke(operation, ...), never .handle(), and would raise if the
        planner ever picked it, so those names are deliberately excluded
        here rather than offered as if they were real actions.

        Resolves the grocery vertical's own bus when no bus was
        explicitly injected (self._capability_bus) — the only vertical
        this codebase has, same default every other single-vertical call
        site (vertical_router.py) already uses. Returns () (silently,
        same as every other _retrieve_* method's "nothing available"
        case) if no vertical is registered at all.

        Domain-scoped from there: ACTOR_NODE_CLASS is set on whichever
        process is actually building this plan (actor_runtime.py's own
        env, e.g. "robot" for a drone's dedicated Pod) — and since
        api/routes/prompt.py's own forward-to-dedicated-Pod fix means a
        robot actor's cognition now always runs THERE, not on the shared
        central control plane, this env var reliably reflects the
        CURRENT actor's own domain at the exact point this method runs.
        A robot process only ever sees robot + universal names; every
        other process (grocery/human actors, the shared cloud control
        plane) sees everything except the robot-only ones — see this
        module's own _ROBOT_CAPABILITY_NAMES/_UNIVERSAL_CAPABILITY_NAMES
        comment for why a real second VerticalRuntime wasn't needed to
        fix this.
        """
        bus = self._capability_bus
        if bus is None:
            try:
                from src.monkey_brain.kernel.domains.vertical_router import (
                    resolve_vertical,
                )

                bus = resolve_vertical("grocery").bus
            except Exception:
                return ()
        names = (name for name in bus.names() if callable(getattr(bus.discover(name), "handle", None)))
        if os.environ.get("ACTOR_NODE_CLASS", "cloud") == "robot":
            return tuple(
                n for n in names if n in _ROBOT_CAPABILITY_NAMES or n in _SELF_CONTAINED_UNIVERSAL_CAPABILITY_NAMES
            )
        return tuple(n for n in names if n not in _ROBOT_CAPABILITY_NAMES)

    def _retrieve_actor_profile(self, actor_id: str) -> Any:
        if self._planetary_runtime is None:
            return None
        for sr in self._planetary_runtime.all_societies():
            state = sr.get_actor(actor_id)
            if state is not None:
                return state.profile
        return None

    def _retrieve_active_memberships(self, actor_id: str) -> tuple[dict[str, Any], ...]:
        """Membership as a First-Class Runtime Resource refactor: every
        active Membership's roles/permissions, so the Contextual Planner
        can read them from PlanningContext.metadata["active_memberships"]
        without ever querying the membership registry itself directly."""
        if self._planetary_runtime is None:
            return ()
        registry = getattr(self._planetary_runtime, "membership_registry", None)
        if registry is None:
            return ()
        result = []
        for m in registry.memberships_for_actor(actor_id):
            if not m.is_active():
                continue
            governance = self._planetary_runtime.governance_for(m.society_id)
            permissions = registry.resolve_permissions(m.membership_id, governance=governance)
            society_runtime = self._planetary_runtime.get_society_runtime(m.society_id)
            society_name = society_runtime.society.name if society_runtime is not None else ""
            result.append(
                {
                    "membership_id": m.membership_id,
                    "society_id": m.society_id,
                    "society_name": society_name,
                    "team_id": m.team_id,
                    "roles": list(m.roles),
                    "trust_score": m.trust_score,
                    "permissions": list(permissions),
                }
            )
        return tuple(result)

    def _retrieve_reachable_colleagues(self, actor_id: str) -> tuple[dict[str, Any], ...]:
        """Real gap this closes: the planner had no reliable way to name
        WHO an AskActor/BroadcastToAffiliation step should target except
        by copying a person's display name verbatim from elsewhere in the
        prompt — fragile against a local model writing "Raj" instead of
        the exact "Raj Sharma" AskActorCapability.handle() requires
        (confirmed live: an otherwise-correct AskActor step failed with
        "no actor named 'Raj' found", then got learned as real negative
        evidence, degrading later attempts too). actor_id is the real,
        unambiguous identifier AskActorCapability already resolves
        against internally (pr.resolve_communication takes actor_id on
        both sides) — this surfaces it directly instead of asking the
        model to reconstruct an exact name string. Scoped to actors this
        one actually shares an active society with (the same set
        AskActor's own reachability check honors), not every actor in
        the world.

        Shared with grocery.py::AskActorCapability, which uses the same
        function as a fuzzy-name-resolution fallback — see
        kernel/affiliations/reachability.py's own module docstring for
        why this is one function, not two independently-drifting copies."""
        from src.monkey_brain.kernel.affiliations.reachability import (
            reachable_colleagues,
        )

        return reachable_colleagues(self._planetary_runtime, actor_id)

    def _retrieve_presence_history(self, actor_id: str, limit: int = 5) -> tuple[dict[str, Any], ...]:
        """Presence History for LLM grounding (architecture spec: "commonly
        visited spaces; recurring movement patterns... before an LLM
        invocation"). Complements _retrieve_timeline's current_location (a
        single current-position snapshot) with the actor's recent visit
        history, enriched via PresenceTimeline.enriched_history (societies/
        nearby actors/goals per visit -- reused, not rebuilt). Bounded to
        the most recent `limit` visits, same "don't bloat the prompt"
        discipline _retrieve_context_stream documents above."""
        if self._planetary_runtime is None:
            return ()
        presence = getattr(self._planetary_runtime, "presence", None)
        if presence is None:
            return ()
        try:
            history = presence.enriched_history(actor_id)
        except Exception:
            return ()
        return tuple(history[-limit:])

    def _retrieve_shared_goals(self, activated_society_ids: set) -> tuple[str, ...]:
        """A Society's shared_goals (kernel/society/domain.py — e.g. a
        household's shared shopping list, GET/POST /societies/{id}/goals)
        for every society this goal activated. This is the "goal merging"
        mechanism: several actors in the same society/household all see the
        same shared list alongside their own individual goal, so the
        planner can reconcile the two rather than planning in isolation."""
        if self._planetary_runtime is None or not activated_society_ids:
            return ()
        seen: list[str] = []
        for sid in activated_society_ids:
            society_runtime = self._planetary_runtime.get_society_runtime(sid)
            if society_runtime is None:
                continue
            for g in society_runtime.society.shared_goals:
                if g not in seen:
                    seen.append(g)
        return tuple(seen)

    def _retrieve_shared_resources(self, activated_society_ids: set) -> dict[str, Any]:
        """Expose active societies' shared resource snapshots to planning.

        Household resources are merged by key in activation order.  The
        planner receives facts; it remains responsible for deciding how to
        use a budget, pantry, or shopping list.
        """
        if self._planetary_runtime is None or not activated_society_ids:
            return {}
        merged: dict[str, Any] = {}
        for sid in activated_society_ids:
            society_runtime = self._planetary_runtime.get_society_runtime(sid)
            if society_runtime is None:
                continue
            resources = society_runtime.shared_resources()
            for key, value in resources.items():
                if isinstance(value, dict) and isinstance(merged.get(key), dict):
                    merged[key] = {**merged[key], **value}
                else:
                    merged[key] = value
        return merged

    def _retrieve_commerce_network(
        self, goal_text: str, activated_society_ids: set[str] | None = None
    ) -> tuple[dict[str, Any], ...]:
        network = getattr(self._planetary_runtime, "commerce_network", None)
        if network is None:
            return ()
        society_id = next(iter(activated_society_ids or ()), "")
        federation_id = ""
        if society_id and hasattr(self._planetary_runtime, "federation_manager"):
            federations = self._planetary_runtime.federation_manager.federations_for_society(society_id)
            federation_id = federations[0].federation_id if federations else ""
        return network.planning_facts(goal_text, society_id, federation_id)

    # ── Ranking / Deduplication ───────────────────────────────────────

    def _score(self, item: RetrievedItem, activated_society_ids: set) -> float:
        now = time.time()
        age = max(0.0, now - item.timestamp) if item.timestamp else 0.0
        recency = 1.0 / (1.0 + age / (7 * 24 * 3600))  # decay over ~a week
        org_importance = 1.0 if item.source in activated_society_ids else 0.0
        return (
            0.3 * recency
            + 0.3 * item.retrieval_score
            + 0.2 * item.confidence
            + 0.1 * 0.0  # trust — not wired this pass
            + 0.1 * org_importance
        )

    def _rank_and_dedupe(
        self,
        items: tuple[RetrievedItem, ...],
        activated_society_ids: set,
    ) -> tuple[RetrievedItem, ...]:
        """Score every item and keep the highest-scored of each
        (item_type, content) pair. No compression: collapsing repeated
        items into a summary discards exactly the specific content
        (which store, what preference, what price) personalized planning
        depends on, so every distinct retrieved item is preserved."""
        if not items:
            return ()

        scored = [(self._score(i, activated_society_ids), i) for i in items]
        best_by_key: dict[tuple[str, str], tuple[float, RetrievedItem]] = {}
        for score, item in scored:
            key = (item.item_type, item.content)
            if key not in best_by_key or score > best_by_key[key][0]:
                best_by_key[key] = (score, item)

        ranked = sorted(best_by_key.values(), key=lambda pair: pair[0], reverse=True)
        return tuple(item for _, item in ranked)
