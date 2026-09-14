"""MovementPerturbationEngine — Prompt 7, Context-Driven Perturbation
Engine. A new CLASS of perturbation alongside kernel/society/world.py::
SharedWorld.perturb()'s existing numeric-attribute/random-event noise:
instead of mutating world state directly, a movement perturbation forces
one or more Actors out of their current Space (e.g. "Fire -> Actors
evacuate").

The entire point of this class is what it does NOT do: it contains no
membership logic and no ContextStream publishing of its own. It calls the
exact same move_actor() write path a voluntary, actor-initiated move
uses, so everything downstream of a move already happens for free:

    Fire
      |
      v
    Actors evacuate               (this class picks WHO and WHERE)
      |
      v
    PresenceTimeline updated       (move_actor -> PresenceTimeline.move_actor)
      |
      v
    Temporary memberships revoked  (MembershipGovernor, subscribed to
      |                             PresenceTimeline movement events —
      v                             Prompt 3/4, untouched by this class)
    New temporary memberships
    granted
      |
      v
    ContextStream updated          (MembershipGovernor._publish +
                                     PlanetaryRuntime.move_actor's own
                                     WORLD_UPDATE publish — Prompt 3/4,
                                     untouched by this class)

If PresenceTimeline/MembershipGovernor's own movement handling ever
changes, this class needs no changes to stay correct — that is the
"rely on the same movement and membership lifecycle... rather than
implementing special-case logic" requirement made concrete.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable

from src.monkey_brain.kernel.geography.entity import GeographicEntityType
from src.monkey_brain.kernel.geography.registry import GeographicRegistry
from src.monkey_brain.kernel.timeline.presence import PresenceTimeline

logger = logging.getLogger("agentos.movement_perturbation")

MoveActor = Callable[[str, str, str], Any]
"""(actor_id, space_id, activity) -> truthy on success — in practice
kernel/society/integration.py::PlanetaryRuntime.move_actor, the SAME
write path normal actor-initiated movement uses."""

CAUSES = ("fire", "flood", "structural_failure", "security_incident", "chemical_spill")


class MovementPerturbationEngine:
    """Stochastically evacuates every Actor out of one currently-occupied
    Space per triggered perturbation, relocating them to a different Space
    via the injected move_actor callable — never PresenceTimeline or
    MembershipGovernor directly, so this class stays a thin trigger, not a
    second implementation of movement's consequences."""

    def __init__(
        self,
        move_actor: MoveActor,
        presence: PresenceTimeline,
        geo_registry: GeographicRegistry,
    ) -> None:
        self._move_actor = move_actor
        self._presence = presence
        self._geo_registry = geo_registry

    def perturb(self, event_chance: float = 0.1) -> list[dict[str, Any]]:
        """With probability event_chance, pick one occupied Space at
        random, evacuate every Actor currently there to a different Space,
        and return one perturbation record per Actor actually moved (empty
        if nothing fired, nothing was occupied, or no evacuation
        destination existed)."""
        if random.random() >= event_chance:
            return []

        occupancy = self._presence.current_occupancy()
        if not occupancy:
            return []

        occupied_spaces: dict[str, list[str]] = {}
        for actor_id, space_id in occupancy.items():
            occupied_spaces.setdefault(space_id, []).append(actor_id)

        space_id = random.choice(list(occupied_spaces.keys()))
        destination_id = self._evacuation_destination(space_id)
        if destination_id is None:
            return []

        cause = random.choice(CAUSES)
        timestamp = time.time()
        perturbations: list[dict[str, Any]] = []
        for actor_id in occupied_spaces[space_id]:
            try:
                moved = self._move_actor(actor_id, destination_id, f"evacuating {cause}")
            except Exception as e:
                logger.error("Movement perturbation failed to move %s: %s", actor_id, e)
                continue
            if moved:
                perturbations.append(
                    {
                        "perturbation": "movement",
                        "cause": cause,
                        "actor_id": actor_id,
                        "from_space_id": space_id,
                        "to_space_id": destination_id,
                        "timestamp": timestamp,
                    }
                )
        return perturbations

    def _evacuation_destination(self, space_id: str) -> str | None:
        """A different Space to evacuate to: prefer a sibling Space under
        the same parent (same Building — the nearest plausible refuge),
        falling back to any other known Space if there is no sibling."""
        space = self._geo_registry.get(space_id)
        if space is None:
            return None
        if space.parent_id is not None:
            siblings = [
                e
                for e in self._geo_registry.children_of(space.parent_id)
                if e.entity_type == GeographicEntityType.SPACE and e.entity_id != space_id
            ]
            if siblings:
                return random.choice(siblings).entity_id
        others = [e for e in self._geo_registry.all(GeographicEntityType.SPACE) if e.entity_id != space_id]
        return random.choice(others).entity_id if others else None
