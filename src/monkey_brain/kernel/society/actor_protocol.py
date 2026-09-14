"""ActorProtocol — the formal contract for anything registered as an actor.

The actor's private implementation interface. Actors never depend on
SocietyRuntime or PlanetaryRuntime (architectural invariant).

Required fields/methods:
    entity_id: str
    tick(): run one cognitive cycle
    set_world(world): receive shared world reference

NOT part of this contract (architectural boundary):
    set_society_runtime() — forbidden in actor code
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ActorProtocol(Protocol):
    """Minimum interface required by the private ActorRuntime implementation."""

    entity_id: str

    async def tick(self) -> Any:
        """Run one complete cognitive cycle and return its result."""
        ...

    def set_world(self, world: Any) -> None:
        """Receive a reference to the shared world."""
        ...
