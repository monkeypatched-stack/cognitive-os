"""GeographicEntityRuntime — one generic, recursive runtime class for all 8
physical-hierarchy tiers (Planet -> Country -> State -> County -> City ->
Street -> Building -> Space), instead of 8 near-identical Runtime classes.

Prompt 4 — Recursive Geographic Tick. Traversal at a Space is no longer

    Space
        Society
            Actor

(that nesting was already retired by Prompt 2/3's sibling model — Actors
are not children of Societies). It is instead:

    Space
      |
      v
    tick Actors                    (every Actor PresenceTimeline reports
      |                             physically present at this Space)
      v
    tick Associated Societies      (every Society GeographicRegistry.
      |                             societies_at_or_above reports for
      |                             this Space — hosted here or at any
      |                             ancestor)
      v
    update Temporary Memberships   (MembershipGovernor reconciliation —
      |                             see reconcile below)
      v
    publish membership events      (TemporaryMembershipGranted/Revoked,
      |                             to the ContextStream — MembershipGovernor
      |                             already does this on every movement;
      |                             reconciliation covers drift a tick
      |                             catches that movement events alone
      |                             wouldn't, e.g. a Society's hosting
      |                             changing while Actors are already
      |                             present)
      v
    continue planetary cycle       (recurse into child entities, same as
                                     before)

The geographic tick should:
  - tick the Space itself (via the optional entity_processor hook)
  - tick every physically present Actor at that Space (deduplicated
    against actors already ticked this same traversal via their home
    Society, so no Actor's cognition runs twice in one cycle)
  - tick every Society hosted directly at that Space (entity.
    hosted_society_ids, unchanged from before this prompt) — a Society
    hosted at an ancestor tier is ticked once, when the recursion reaches
    that ancestor's own node, not redundantly re-ticked at every
    descendant Space; societies_at_or_above (ancestor-inclusive) is used
    for reconciliation below, not for deciding what gets ticked where,
    since re-running the SAME society's cognition at every Space beneath
    it would be wasted, duplicated work
  - reconcile temporary memberships for every physically present Actor
    (MembershipGovernor.reconcile — ancestor-inclusive, grants/revokes to
    match current presence + current Space-Society association, publishing
    lifecycle events for any drift found; catches drift a movement event
    alone wouldn't, e.g. a Society's hosting topology changing while an
    Actor stays put)
  - produce a complete GeoResult (this module's GeographicTickResult,
    unchanged in shape plus a new temporary_memberships_reconciled count)
    summarizing all of the above, then recurse into every child entity
    exactly as before.

For a targeted actor tick, society selection is deterministic: an explicit
``society_id`` in the prompt request (or its ``context``) takes precedence;
otherwise the first effective membership hosted by the current entity is
selected in registry order. A membership lookup is preferred, with an active-
actor scan as the compatibility fallback when no lookup is injected. Other
societies are skipped for that targeted actor, preventing duplicate cognition
when the actor belongs to multiple societies. Untargeted geographic cycles
continue to tick every hosted society.

The legacy CityTickResult/CountryTickResult adapters (kernel/society/
integration.py) remain unchanged — they wrap GeographicTickResult for
backward-compat callers and need no changes here.

Prompt 5 — GeoResult Refactor. GeographicTickResult (the "GeoResult") now
also carries membership information directly: observed_spaces,
observed_actors, observed_societies, temporary_memberships,
effective_memberships, active_actors — all rolled up recursively across
the whole subtree, same as the existing actors_ticked_total/
interactions_routed_total counts. The point: a caller holding a GeoResult
already has everything it needs to reason about who belongs where: no
later stage should iterate through societies to re-derive membership —
query the dicts this result already carries instead.

Prompt 6 — Active Actor Refactor. Clarifies active_actors: an Active
Actor is an Actor PHYSICALLY OBSERVED during the current planetary cycle
— i.e. reachable via PresenceTimeline.occupants() at some observed Space
— regardless of whether it holds a permanent membership, a temporary
membership, or neither, in whatever Society happens to be ticking.
Activity is based on presence, not governance: an Actor whose home
Society is hosted at this Space gets ticked (and so, before this
clarification, counted as "active") purely because of organizational
membership even when physically located somewhere else entirely — that
is a governance-driven tick, not evidence of presence, and active_actors
must not conflate the two.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from uuid import uuid4

from src.monkey_brain.kernel.geography.entity import GeographicEntityType
from src.monkey_brain.kernel.geography.registry import GeographicRegistry

logger = logging.getLogger("agentos.geography.runtime")

SocietyLookup = Callable[[str], Any | None]
EntityProcessor = Callable[[Any], Any | Awaitable[Any]]
# Duck-typed, same reason SocietyLookup takes Any instead of importing
# SocietyRuntime: this module documents itself as independent of the
# organizational hierarchy, so it depends on shapes, not concrete types
# from kernel/timeline or kernel/society.
PresenceLookup = Any
"""Anything exposing .occupants(entity_id) -> Iterable[str] — in practice
kernel/timeline/presence.py::PresenceTimeline."""
ActorTicker = Callable[[str], Awaitable[bool]]
MembershipReconciler = Callable[[str], int]
MembershipLookup = Callable[[str], tuple[str, ...]]
"""actor_id -> its current societies (temporary or effective, depending on
which lookup) — in practice kernel/society/membership.py::MembershipGovernor.
temporary_societies_for_actor / .effective_societies, frozenset wrapped in
tuple() by the caller that injects it."""


@dataclass(frozen=True)
class GeographicTickResult:
    """Result of ticking one geographic entity — every society hosted
    there, plus every descendant entity recursively. Mirrors CityTickResult/
    CountryTickResult's shape (this session's now-superseded 2-tier
    version), generalized to one type for all 8 tiers."""
    cycle_id: str = field(default_factory=lambda: uuid4().hex)
    entity_id: str = ""
    entity_type: GeographicEntityType | None = None
    societies_ticked: tuple[str, ...] = ()
    children_ticked: tuple[str, ...] = ()
    actors_ticked_total: int = 0
    interactions_routed_total: int = 0
    actor_execution_result: Any = None
    temporary_memberships_reconciled: int = 0
    """Count of temporary membership grants/revokes MembershipGovernor.
    reconcile() made this tick (Prompt 4) — 0 whenever no PresenceLookup/
    MembershipReconciler was injected, or nothing had drifted."""

    # Prompt 5 — GeoResult Refactor: membership info exposed directly, so
    # no later stage needs to iterate societies to determine who belongs
    # where. All six roll up recursively across this entity's whole subtree.
    observed_spaces: tuple[str, ...] = ()
    """Every Space-tier entity_id visited in this subtree."""
    observed_actors: tuple[str, ...] = ()
    """Every Actor physically present at any observed Space, whether or
    not its cognition actually ran this tick (see active_actors)."""
    observed_societies: tuple[str, ...] = ()
    """Every Society either ticked here, or associated with an observed
    Space (GeographicRegistry.societies_at_or_above) — broader than
    societies_ticked, which only counts societies whose cognition ran."""
    active_actors: tuple[str, ...] = ()
    """Prompt 6 — Active Actor Refactor: every Actor PHYSICALLY OBSERVED
    (present at an observed Space) during this cycle — presence-based, not
    governance-based. Not the same population as "every Actor whose
    cognition ran this tick" (a Society ticks every member regardless of
    where they physically are); membership (permanent or temporary, or
    neither) plays no part in this field."""
    temporary_memberships: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """actor_id -> its current temporary societies, for every observed
    Actor — MembershipGovernor.temporary_societies_for_actor(), pre-
    computed so callers never recompute it from presence + hosting."""
    effective_memberships: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """actor_id -> permanent UNION temporary societies, for every observed
    Actor — MembershipGovernor.effective_societies(), precomputed."""

    # Prompt 8 — Lemon Observability: rolled-up cycle-wide counts, unlike
    # societies_ticked/children_ticked above (which only ever reflect THIS
    # node's own direct hosting, not the whole subtree) — these back the
    # "geographic entities ticked" / "societies ticked" metrics, which need
    # a true whole-traversal total.
    entities_ticked_total: int = 0
    """This entity plus every descendant entity visited in this subtree
    (0 only for the "entity not found" early-return case — every real
    result includes at least itself)."""
    societies_ticked_total: int = 0
    """Every society-tick() invocation across this whole subtree (societies_
    ticked at this node, plus every descendant's own societies_ticked_total)."""

    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)


class GeographicEntityRuntime:
    """Ticks one geographic entity: its physically present Actors, every
    Society associated with it (societies_at_or_above — hosted here or at
    any ancestor, same is_active/has-active-actors filter PlanetaryRuntime.
    _run_cycle() applies), a temporary-membership reconciliation pass, then
    every child entity recursively via a fresh GeographicEntityRuntime —
    one class, all 8 tiers. Targeted actor ticks select one relevant society
    deterministically; see the module docstring for the full traversal and
    selection policy."""

    def __init__(self, registry: GeographicRegistry, entity_id: str,
                 society_lookup: SocietyLookup,
                 entity_processor: EntityProcessor | None = None,
                 presence: PresenceLookup | None = None,
                 actor_ticker: ActorTicker | None = None,
                 membership_reconciler: MembershipReconciler | None = None,
                 temporary_membership_lookup: MembershipLookup | None = None,
                 effective_membership_lookup: MembershipLookup | None = None) -> None:
        self._registry = registry
        self.entity_id = entity_id
        self._society_lookup = society_lookup
        self._entity_processor = entity_processor
        self._presence = presence
        self._actor_ticker = actor_ticker
        self._membership_reconciler = membership_reconciler
        self._temporary_membership_lookup = temporary_membership_lookup
        self._effective_membership_lookup = effective_membership_lookup

    async def tick(self, *, actor_id: str | None = None,
                   prompt_request: Any = None,
                   _ticked_actor_ids: set[str] | None = None) -> GeographicTickResult:
        start = time.time()
 
        # dedupe actor_ids through the whole traversal, so no Actor's cognition runs
        # twice in one planetary cycle. 

        ticked_actor_ids = _ticked_actor_ids if _ticked_actor_ids is not None else set()

        # get the geographic entity
        entity = self._registry.get(self.entity_id)
        if entity is None:
            return GeographicTickResult(entity_id=self.entity_id)

        # NOTE: Don't understand where the entity processor is
        if self._entity_processor is not None:
            processed = self._entity_processor(entity)
            if hasattr(processed, "__await__"):
                await processed

        societies_ticked: list[str] = []
        actors_total = 0
        interactions_total = 0
        actor_execution_result = None
        temporary_memberships_reconciled = 0

        observed_spaces: set[str] = set()
        observed_actor_ids: set[str] = set()
        observed_society_ids: set[str] = set()
        active_actor_ids: set[str] = set()
        temporary_memberships: dict[str, tuple[str, ...]] = {}
        effective_memberships: dict[str, tuple[str, ...]] = {}

        # A targeted actor tick must run in one relevant society. Prefer an
        # explicit society carried by the request; otherwise use the actor's
        # effective memberships, retaining the registry's deterministic host
        # order. This also avoids running the same targeted cognition once per
        # society when an actor has multiple memberships.
        selected_society_id: str | None = None
        if actor_id is not None:
            requested_society_id = None
            if isinstance(prompt_request, dict):
                requested_society_id = prompt_request.get("society_id")
                if requested_society_id is None:
                    context = prompt_request.get("context")
                    if isinstance(context, dict):
                        requested_society_id = context.get("society_id")
            else:
                requested_society_id = getattr(prompt_request, "society_id", None)
                if requested_society_id is None:
                    context = getattr(prompt_request, "context", None)
                    requested_society_id = getattr(context, "society_id", None)

            hosted_society_ids = entity.hosted_society_ids
            if requested_society_id in hosted_society_ids:
                selected_society_id = requested_society_id

            # If no explicit society was requested, try to find one of the actor's
            # effective memberships that is hosted here. This avoids scanning
            # every society for every actor when a membership lookup is available.
            elif self._effective_membership_lookup is not None:
                memberships = set(self._effective_membership_lookup(actor_id))
                selected_society_id = next(
                    (society_id for society_id in hosted_society_ids
                     if society_id in memberships),
                    None,
                )
            else:
                # Preserve compatibility for callers that do not inject a
                # membership lookup, without scanning societies more than
                # necessary.
                for society_id in hosted_society_ids:
                    society_runtime = self._society_lookup(society_id)
                    if society_runtime is None:
                        continue
                    if any(a.actor_id == actor_id
                           for a in society_runtime.active_actors()):
                        selected_society_id = society_id
                        break

        if entity.entity_type == GeographicEntityType.SPACE:
            observed_spaces.add(entity.entity_id)
            # find the societies at or above this space and add them to the observed_society_ids set
            observed_society_ids.update(self._registry.societies_at_or_above(entity.entity_id))

        # get society runtime by id
        #
        # Deliberately SERIAL, unlike the occupant-actor loop below --
        # Society architecture review, Phase 4: do not parallelize this
        # with asyncio.gather. Each iteration reads the ACCUMULATED
        # ticked_actor_ids set (`exclude_actor_ids = frozenset(ticked_actor_ids)`)
        # produced by every PRIOR society this entity hosts, then updates
        # that same set before the next society's tick — specifically so
        # an actor who is a member of two societies hosted at the same
        # entity is ticked at most once per cycle. Running these
        # concurrently would let two societies both read the same
        # not-yet-updated ticked_actor_ids and double-tick that actor —
        # exactly the DANGEROUS race this loop exists to prevent. Safe to
        # leave serial: unlike the occupant loop (one entry per present
        # actor, each with its own multi-second LLM call), an entity
        # typically hosts very few societies, so this loop's own
        # contribution to cycle time is small relative to the
        # already-parallelized occupant-actor cost.
        for society_id in entity.hosted_society_ids:
            if actor_id is not None and society_id != selected_society_id:
                continue
            society_runtime = self._society_lookup(society_id)
            if society_runtime is None:
                continue
            if not getattr(society_runtime, "is_active", True):
                continue
            society_active_actors = society_runtime.active_actors()
            if not society_active_actors:
                continue
            try:

                # frozen set of actor ids that have been ticked in this society, 
                # to be passed to the society tick method to avoid ticking the 
                # same actor multiple times in the same cycle
                exclude_actor_ids = frozenset(ticked_actor_ids)

                # update the ticked_actor_ids set with the actor ids that have been 
                # ticked in this society
                ticked_actor_ids.update(a.actor_id for a in society_active_actors)

                # tick the society
                tick_result = await society_runtime.tick(
                    target_actor_id=actor_id,
                    prompt_request=prompt_request,
                    exclude_actor_ids=frozenset(exclude_actor_ids),
                )

                # append the ticked socitey to the array
                societies_ticked.append(society_id)
                observed_society_ids.add(society_id)

                # find the number of actors ticked in the society
                actors_total += tick_result.actors_ticked
                interactions_total += tick_result.interactions_routed
                if tick_result.actor_execution_result is not None:
                    actor_execution_result = tick_result.actor_execution_result
            except Exception as e:
                logger.error("Society %s tick failed hosted at %s: %s", society_id, self.entity_id, e)

        # tick every Actor physically present at this entity, and
        # reconcile its temporary memberships.
        #
        # Performance (docs/adr/016-performance-gate9.md,
        # docs/adr/019-runtime-performance-audit.md): this was previously
        # a serial `for occupant_id in ...: await self._actor_ticker(...)`
        # loop -- each actor's own LLM-backed cognitive tick had to fully
        # finish before the next actor's could even start, turning N
        # actors' already-substantial individual latency (measured:
        # 21.9s-57.8s per LLM call alone) into a serial SUM for the whole
        # cycle. Every occupant of one Space is physically present in
        # exactly one place at a time (this module's own docstring), so
        # no two occupants ever touch the same ticked_actor_ids/
        # temporary_memberships/effective_memberships entry or the same
        # actors_total/active_actor_ids slot -- each _tick_occupant() call
        # below only ever reads shared state that's already fully settled
        # before this loop starts (ticked_actor_ids from the society-wide
        # bulk tick above) and only ever WRITES entries keyed by its own
        # occupant_id, so running them concurrently via asyncio.gather
        # changes nothing about correctness, only about how much of their
        # wall-clock time overlaps.
        async def _tick_occupant(occupant_id: str) -> tuple[str, bool, bool, int, tuple[str, ...], tuple[str, ...]]:
            """One occupant's complete per-tick work, in the exact same
            order/semantics as the original serial loop body. Returns
            (occupant_id, already_ticked, newly_ticked, reconciled_count,
            temporary_memberships, effective_memberships)."""
            already_ticked = occupant_id in ticked_actor_ids
            newly_ticked = False

            # an Actor is active if physically present, regardless of whether its cognition ran this tick.
            # This is a presence-based, not governance-based, definition of activity.
            if already_ticked:
                pass

            elif occupant_id == actor_id:
                # if the actor_id is the same as the occupant_id, then tick the actor
                try:
                    newly_ticked = bool(await self._actor_ticker(occupant_id))
                except Exception as e:
                    logger.error("Actor %s tick failed at %s: %s", occupant_id, self.entity_id, e)
                    newly_ticked = False

            elif self._actor_ticker is not None:
                # otherwise, tick the registered Actor for the society
                try:
                    newly_ticked = bool(await self._actor_ticker(occupant_id))
                except Exception as e:
                    logger.error("Actor %s tick failed at %s: %s", occupant_id, self.entity_id, e)
                    newly_ticked = False

            # reconcile temporary memberships for the physically present Actor, if a MembershipReconciler was injected. This is a presence-based reconciliation, not governance-based.
            reconciled = 0
            if self._membership_reconciler is not None:
                try:
                    reconciled = self._membership_reconciler(occupant_id) or 0
                except Exception as e:
                    logger.error("Membership reconciliation failed for %s at %s: %s",
                                 occupant_id, self.entity_id, e)

            # precompute membership so no later stage has to.
            temp_memberships: tuple[str, ...] = ()
            if self._temporary_membership_lookup is not None:
                temp_memberships = tuple(self._temporary_membership_lookup(occupant_id))
            eff_memberships: tuple[str, ...] = ()
            if self._effective_membership_lookup is not None:
                eff_memberships = tuple(self._effective_membership_lookup(occupant_id))

            return occupant_id, already_ticked, newly_ticked, reconciled, temp_memberships, eff_memberships

        if self._presence is not None:
            occupant_ids = self._presence.occupants(self.entity_id)
            observed_actor_ids.update(occupant_ids)

            tick_results = await asyncio.gather(*(
                _tick_occupant(occupant_id) for occupant_id in occupant_ids
            ))

            for occupant_id, already_ticked, newly_ticked, reconciled, temp_memberships, eff_memberships in tick_results:
                if already_ticked:
                    active_actor_ids.add(occupant_id)
                else:
                    ticked_actor_ids.add(occupant_id)
                    if newly_ticked:
                        actors_total += 1
                        active_actor_ids.add(occupant_id)

                temporary_memberships_reconciled += reconciled
                if self._temporary_membership_lookup is not None:
                    temporary_memberships[occupant_id] = temp_memberships
                if self._effective_membership_lookup is not None:
                    effective_memberships[occupant_id] = eff_memberships

        # find the child societies and tick them too
        #
        # Deliberately SERIAL, same reasoning as the societies loop above
        # (Society architecture review, Phase 4): every child recursion
        # shares this SAME `ticked_actor_ids` set (`_ticked_actor_ids=
        # ticked_actor_ids` below), read-then-updated across siblings, so
        # an actor present under two different children of this entity is
        # ticked at most once per cycle. Parallelizing sibling children
        # via asyncio.gather would let two children both observe the
        # actor as not-yet-ticked before either writes it, double-ticking
        # it — the same DANGEROUS race the societies loop's own comment
        # describes. Unlike that loop, this one COULD matter more at
        # scale (a high branching-factor entity, e.g. a City with many
        # Streets, pays this serially), but the dominant cost at every
        # tier — each present actor's own LLM-backed cognitive tick — is
        # already parallelized inside each child's own occupant loop
        # regardless of recursion order. Safely parallelizing sibling
        # recursion would require replacing this shared, sequentially-
        # mutated set with a two-phase collect-then-dedupe-then-tick
        # protocol — a genuine redesign of the double-tick-prevention
        # mechanism, intentionally out of scope here without a live
        # measurement showing child fan-out (not per-actor LLM latency)
        # is the actual bottleneck.
        children_ticked: list[str] = []
        entities_ticked_total = 1
        societies_ticked_total = len(societies_ticked)
        for child in self._registry.children_of(self.entity_id):
            child_runtime = GeographicEntityRuntime(
                self._registry, 
                child.entity_id, 
                self._society_lookup,
                self._entity_processor, 
                self._presence, 
                self._actor_ticker,
                self._membership_reconciler, 
                self._temporary_membership_lookup,
                self._effective_membership_lookup,
            )
            child_result = await child_runtime.tick(
                actor_id=actor_id,
                prompt_request=prompt_request,
                _ticked_actor_ids=ticked_actor_ids,
            )
            children_ticked.append(child.entity_id)
            actors_total += child_result.actors_ticked_total
            interactions_total += child_result.interactions_routed_total
            temporary_memberships_reconciled += child_result.temporary_memberships_reconciled
            observed_spaces.update(child_result.observed_spaces)
            observed_actor_ids.update(child_result.observed_actors)
            observed_society_ids.update(child_result.observed_societies)
            active_actor_ids.update(child_result.active_actors)
            temporary_memberships.update(child_result.temporary_memberships)
            effective_memberships.update(child_result.effective_memberships)
            entities_ticked_total += child_result.entities_ticked_total
            societies_ticked_total += child_result.societies_ticked_total
            if child_result.actor_execution_result is not None:
                actor_execution_result = child_result.actor_execution_result

        return GeographicTickResult(
            entity_id=self.entity_id,
            entity_type=entity.entity_type,
            societies_ticked=tuple(societies_ticked),
            children_ticked=tuple(children_ticked),
            actors_ticked_total=actors_total,
            interactions_routed_total=interactions_total,
            actor_execution_result=actor_execution_result,
            temporary_memberships_reconciled=temporary_memberships_reconciled,
            observed_spaces=tuple(sorted(observed_spaces)),
            observed_actors=tuple(sorted(observed_actor_ids)),
            observed_societies=tuple(sorted(observed_society_ids)),
            active_actors=tuple(sorted(active_actor_ids)),
            temporary_memberships=temporary_memberships,
            effective_memberships=effective_memberships,
            entities_ticked_total=entities_ticked_total,
            societies_ticked_total=societies_ticked_total,
            duration_ms=(time.time() - start) * 1000,
        )
