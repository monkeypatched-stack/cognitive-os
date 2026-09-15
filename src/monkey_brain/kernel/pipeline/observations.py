"""Observation & Belief Fusion — the perception pipeline.

Observation is external. Belief is internal.
The runtime owns the transformation between the two.

    ObservationProvider
        ↓
    ObservationSet
        ↓
    BeliefFusion
        ↓
    BeliefState

The runtime must depend only on interfaces, never implementations.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Protocol, Any

logger = logging.getLogger("agentos.pipeline.observations")


# ════════════════════════════════════════════════════════════════════════════
# Observation Model — immutable evidence from the world
# ════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Provenance:
    """Where an observation came from and how it was produced."""

    source: str = "unknown"
    """Origin system (e.g. 'camera', 'sensor', 'llm', 'api', 'human')."""
    method: str = "unknown"
    """How the observation was produced (e.g. 'visual_detection', 'query', 'inference')."""
    reliability: float = 1.0
    """Historical reliability of this source. [0.0, 1.0]"""
    metadata: dict[str, Any] = field(default_factory=dict)
    """Source-specific metadata (sensor ID, model version, etc.)."""


@dataclass(frozen=True)
class Observation:
    """A single immutable observation about the world.

    Observations are evidence. They are not beliefs.
    They represent what was perceived, not what is concluded.

    Immutable — once created, never modified.
    """

    entity: str = ""
    """What was observed (e.g. 'milk', 'temperature', 'user_intent')."""
    attribute: str = ""
    """What aspect of the entity (e.g. 'quantity', 'value', 'presence')."""
    value: Any = None
    """The observed value."""
    confidence: float = 1.0
    """How confident we are in this observation. [0.0, 1.0]"""
    provenance: Provenance = field(default_factory=Provenance)
    """Where this observation came from."""
    timestamp: float = field(default_factory=time.time)
    """When this observation was made."""
    correlation_id: str = ""
    """Id of the logical operation (e.g. execution_id) this observation
    was made during, when known. causation_id is intentionally not added
    here: raw/perceived observations generally have no in-system upstream
    cause to point at, and fabricating one would violate the "don't
    fabricate ids" rule."""


@dataclass(frozen=True)
class ObservationSet:
    """A batch of observations from the world at a point in time.

    Created by an ObservationProvider. Consumed by BeliefFusion.
    The runtime never creates these — it only reads them.
    """

    observations: tuple[Observation, ...] = ()
    """All observations in this set."""
    actor_id: str = ""
    """Which actor these observations are for."""
    timestamp: float = field(default_factory=time.time)
    """When the observations were collected."""

    def is_empty(self) -> bool:
        return len(self.observations) == 0

    def by_source(self) -> dict[str, list[Observation]]:
        """Group observations by source."""
        result: dict[str, list[Observation]] = {}
        for obs in self.observations:
            result.setdefault(obs.provenance.source, []).append(obs)
        return result

    def by_entity(self) -> dict[str, list[Observation]]:
        """Group observations by entity."""
        result: dict[str, list[Observation]] = {}
        for obs in self.observations:
            result.setdefault(obs.entity, []).append(obs)
        return result

    def high_confidence(self, threshold: float = 0.7) -> list[Observation]:
        """Return observations above confidence threshold."""
        return [obs for obs in self.observations if obs.confidence >= threshold]


# ════════════════════════════════════════════════════════════════════════════
# Observation Provider — abstraction for observation acquisition
# ════════════════════════════════════════════════════════════════════════════


class ObservationProvider(Protocol):
    """Protocol for anything that produces observations.

    The runtime depends only on this interface.
    Implementations can be:
    - CameraStream (visual observations)
    - SensorFusion (multi-modal)
    - LLMQuery (reasoning-based observations)
    - APIBridge (external system observations)
    - SimulationEngine (simulated observations)
    - HumanInput (human-provided observations)
    - WorldPolling (polling the world tensor)
    """

    def observe(self, actor_id: str, world: Any) -> ObservationSet:
        """Collect observations about the world for this actor."""
        ...


_aux_registry_lock = threading.Lock()
_aux_providers: dict[tuple[str, str], Any] = {}
"""Auxiliary per-actor observation sources, keyed by (actor_id, source_id).

Real gap this closes: CognitiveRuntime (kernel/pipeline/belief_runtime.py)
takes exactly ONE `observation_provider` — the canonical per-actor tick
(SocietyRuntime.tick_one_actor -> CognitiveActor.tick -> BeliefFormation ->
build_comparison_integrated_runtime(), see that factory's own docstring for
the exact call chain) always defaults it to a bare WorldPollingProvider(),
constructed once and cached on the actor's CognitiveActor for the actor's
whole lifetime. A LiveKitVoiceObservationProvider or
LiveKitVideoObservationProvider (both, like this class, real
ObservationProvider Protocol implementations — kernel/edge/livekit_adapter.py,
kernel/edge/livekit_video_adapter.py) starts LATER, dynamically, tied to an
already-running actor_id when a human joins a LiveKit room — there is no
constructor-time hook left to swap in a composite provider for an actor
whose engine was already lazily built and cached. This registry is the
runtime attachment point instead, the exact same shape
kernel/edge/drone_state.py's actor_id -> adapter registry already uses for
the identical problem (telemetry that starts flowing after the actor
exists) — extended to (actor_id, source_id) since, unlike one drone adapter
per actor, an actor can have voice AND video active at once.
"""


def register_observation_source(actor_id: str, source_id: str, provider: Any) -> None:
    """Attach an additional ObservationProvider-shaped object (must expose
    `observe(actor_id, world) -> ObservationSet`, duck-typed against the
    Protocol above, not isinstance-checked) whose output WorldPollingProvider
    will fold into every observe() call for this actor from now on. Called by
    VoiceCommandRuntime.start()/VideoCommandRuntime.start() with their own
    LiveKit*ObservationProvider instance; unregistered on stop()."""
    with _aux_registry_lock:
        _aux_providers[(actor_id, source_id)] = provider


def unregister_observation_source(actor_id: str, source_id: str) -> None:
    with _aux_registry_lock:
        _aux_providers.pop((actor_id, source_id), None)


def _aux_providers_for(actor_id: str) -> list[Any]:
    with _aux_registry_lock:
        return [p for (aid, _sid), p in _aux_providers.items() if aid == actor_id]


class WorldPollingProvider:
    """Default observation provider that polls the world tensor.

    Reads world states and transitions as observations.
    Sufficient for basic reasoning; real implementations will
    use cameras, sensors, LLMs, etc.

    Also folds in any auxiliary sources registered for this actor via
    register_observation_source() above (voice/video/future sensors) — this
    is the ONE observation_provider a real actor's canonical CognitiveRuntime
    actually consults every tick, so this is the one place a new source must
    reach to influence live belief/replanning, not a parallel pipeline.
    """

    def observe(self, actor_id: str, world: Any) -> ObservationSet:
        """Poll the world for observations."""
        observations = []
        provenance = Provenance(source="world_polling", method="tensor_read", reliability=0.9)

        # Drone telemetry (kernel/edge/drone_state.py): independent of the
        # world tensor below, so this runs even when world is None — a
        # robot actor's own telemetry is not part of the shared world
        # state, it's this specific actor's own sensed reality. Never
        # raises, never fabricates: no adapter registered for this
        # actor_id, or its telemetry has gone stale, means no drone
        # observations this tick, not fake ones.
        try:
            from src.monkey_brain.kernel.edge.drone_state import get_drone_adapter, is_fresh

            adapter = get_drone_adapter(actor_id)
            if adapter is not None:
                state = adapter.latest_state()
                if is_fresh(state):
                    drone_provenance = Provenance(source="px4_ros", method="telemetry", reliability=0.95)
                    for attribute, value in (
                        ("armed", state.armed),
                        ("position_x", state.position_x),
                        ("position_y", state.position_y),
                        ("position_z", state.position_z),
                        ("heading", state.heading),
                        ("battery", state.battery),
                        ("flight_mode", state.flight_mode),
                        ("gps_state", state.gps_state),
                    ):
                        if value is not None:
                            observations.append(
                                Observation(
                                    entity=actor_id,
                                    attribute=attribute,
                                    value=value,
                                    confidence=drone_provenance.reliability,
                                    provenance=drone_provenance,
                                )
                            )
        except Exception:
            logger.debug("observe: drone telemetry suppressed exception", exc_info=True)

        # Auxiliary sources (voice/video/future sensors), registered
        # dynamically via register_observation_source() above — same
        # never-crash contract as drone telemetry: one broken source is
        # logged and skipped, never allowed to blank out every other
        # source or fail the tick.
        for source in _aux_providers_for(actor_id):
            try:
                aux_set = source.observe(actor_id, world)
                observations.extend(aux_set.observations)
            except Exception:
                logger.debug("observe: auxiliary source suppressed exception", exc_info=True)

        if world is None:
            return ObservationSet(observations=tuple(observations), actor_id=actor_id)

        try:
            if hasattr(world, "entities"):
                for entity in world.entities():
                    # Real gap this closes: bookkeeping/marker entities —
                    # EpisodicTrace (MemoryManager.record_experience's own
                    # KG-indexed copy of an experience/execution/
                    # conversation; see kernel/learn/memory/manager.py) and
                    # purchase_log (grocery.py's per-(buyer,product)
                    # duplicate-purchase marker, attributes.get(
                    # "purchase_log") — the same real marker
                    # check_recent_duplicate_purchase already uses) — were
                    # being swept into "exists"/attribute observations
                    # exactly like a real Product or Store, and BeliefFusion
                    # then persisted them as durable beliefs (confirmed
                    # live: "experience: Completed goal: ...: known" and
                    # "Purchase log: Large Eggs: known" both showing up in
                    # Semantic Memory). Neither is real observable world
                    # state — they're audit/memory bookkeeping — so
                    # WorldPollingProvider must not treat them as a fact
                    # about the world the same way _may_explore_entity
                    # (context_engine.py) already excludes EpisodicTrace
                    # from the separate KG-keyword-retrieval path.
                    if entity.attributes.get("label") == "EpisodicTrace" or entity.attributes.get("purchase_log"):
                        continue
                    # Same real problem, a third real shape of it (found
                    # auditing every actor's beliefs live): EVERY real
                    # transactional event entity (grocery.py's "Grocery
                    # Order", "Partial Checkout", etc. — kg.add_entity(...,
                    # EntityType.EVENT, ...)) shares the same literal name
                    # across every instance ("Grocery Order" for every
                    # order any actor ever placed), so BeliefFusion doesn't
                    # just add noise here — it collides every actor's whole
                    # order history into one misleading "Grocery Order = <
                    # whichever order happened to be observed most
                    # recently>" belief. An event that already happened is
                    # not a durable fact about ongoing world state (that's
                    # exactly the "Knowledge Graph = entities/relationships"
                    # vs "Semantic Memory = durable beliefs" distinction the
                    # debugger itself draws) — checked by raw string value,
                    # not an EntityType import, since this module's own
                    # docstring requires it depend on interfaces only, never
                    # a specific KG implementation's types.
                    if getattr(entity, "entity_type", None) == "event":
                        continue
                    observations.append(
                        Observation(
                            entity=entity.name or entity.entity_id,
                            attribute="exists",
                            value=True,
                            confidence=entity.confidence,
                            provenance=provenance,
                        )
                    )
                    for attr, val in entity.attributes.items():
                        observations.append(
                            Observation(
                                entity=entity.name or entity.entity_id,
                                attribute=attr,
                                value=val,
                                confidence=entity.confidence,
                                provenance=provenance,
                            )
                        )

            if hasattr(world, "relationships"):
                for rel in world.relationships():
                    observations.append(
                        Observation(
                            entity=rel.source_id,
                            attribute=f"relates_to:{rel.target_id}",
                            value=rel.kind.value,
                            confidence=rel.confidence,
                            provenance=provenance,
                        )
                    )

            if hasattr(world, "events"):
                for event in world.events(limit=10):
                    observations.append(
                        Observation(
                            entity=event.entity_id or "world",
                            attribute="event",
                            value=event.description,
                            confidence=event.confidence,
                            provenance=provenance,
                        )
                    )

            if not observations and hasattr(world, "states"):
                states = world.states()
                observations.append(
                    Observation(
                        entity="world",
                        attribute="state_count",
                        value=len(states),
                        confidence=0.95,
                        provenance=provenance,
                    )
                )
                if hasattr(world, "nnz"):
                    observations.append(
                        Observation(
                            entity="world",
                            attribute="transition_count",
                            value=world.nnz(),
                            confidence=0.95,
                            provenance=provenance,
                        )
                    )
        except Exception:
            logger.debug("observe: suppressed exception", exc_info=True)

        return ObservationSet(
            observations=tuple(observations),
            actor_id=actor_id,
        )


# ════════════════════════════════════════════════════════════════════════════
# Belief Fusion — transforms observations into beliefs
# ════════════════════════════════════════════════════════════════════════════


class BeliefFusion:
    """Deterministic belief fusion engine.

    Transforms observations into beliefs by applying deterministic rules:

    1. New observation → new fact (with provenance)
    2. Existing fact + identical observation → increase confidence
    3. Conflicting observation → create competing hypothesis
    4. Lower confidence observations never overwrite higher confidence beliefs
    5. Preserve observation provenance

    The engine updates BeliefState without exposing its internal structure.
    """

    def update(self, belief: Any, observations: ObservationSet) -> Any:
        """Fuse observations into the belief state.

        Args:
            belief: The current BeliefState to update
            observations: New observations to fuse

        Returns:
            The updated BeliefState (same instance, mutated in place)
        """
        if observations.is_empty():
            return belief

        for obs in observations.observations:
            self._fuse_observation(belief, obs)

        return belief

    def _fuse_observation(self, belief: Any, obs: Observation) -> None:
        """Apply deterministic fusion rules for a single observation."""
        # Record the observation
        belief.add_observation(
            entity=obs.entity,
            description=f"{obs.attribute}={obs.value}",
            source=obs.provenance.source,
            confidence=obs.confidence,
        )

        # Check for existing fact on same entity+attribute
        existing = self._find_fact(belief, obs.entity, obs.attribute)

        if existing is None:
            # Rule 1: New observation → new fact
            belief.add_fact(
                entity=obs.entity,
                attribute=obs.attribute,
                value=obs.value,
                confidence=obs.confidence,
                source=obs.provenance.source,
            )
        elif existing.value == obs.value:
            # Rule 2: Identical value → increase confidence (blend)
            blended = min(1.0, (existing.confidence + obs.confidence) / 2)
            self._replace_fact(belief, existing, obs, blended)
        elif existing.confidence < obs.confidence:
            # Rule 4: Higher confidence observation replaces lower
            self._replace_fact(belief, existing, obs, obs.confidence)
        else:
            # Rule 3: Conflicting observation → create hypothesis
            belief.add_hypothesis(
                claim=(
                    f"Conflict: {obs.entity}.{obs.attribute} "
                    f"observed as {obs.value} (conf={obs.confidence:.2f}) "
                    f"but known as {existing.value} (conf={existing.confidence:.2f})"
                ),
                confidence=min(existing.confidence, obs.confidence),
                evidence=[
                    f"fact:{obs.entity}.{obs.attribute}={existing.value}",
                    f"obs:{obs.entity}.{obs.attribute}={obs.value}",
                ],
            )

    def _find_fact(self, belief: Any, entity: str, attribute: str):
        """Find an existing fact by entity and attribute."""
        for fact in belief.facts:
            if fact.entity == entity and fact.attribute == attribute:
                return fact
        return None

    def _replace_fact(self, belief: Any, old_fact: Any, obs: Observation, new_confidence: float) -> None:
        """Replace an existing fact with an updated version."""
        # Remove old fact
        belief.facts = [f for f in belief.facts if f is not old_fact]
        # Add new fact with updated confidence
        belief.add_fact(
            entity=obs.entity,
            attribute=obs.attribute,
            value=obs.value,
            confidence=new_confidence,
            source=obs.provenance.source,
        )
