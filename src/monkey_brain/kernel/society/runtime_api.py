"""Public three-tier runtime contracts.

These protocols are deliberately small.  They describe the only runtime
objects that application code should need to know about:

    Planet -> Society -> Actor

The cognitive implementation below ``Actor`` is intentionally absent from
these contracts.  Concrete runtimes may expose richer internal behavior, but
the hierarchy is enforced through these narrow ownership-facing interfaces.
"""

from __future__ import annotations

from typing import Any, Protocol, Sequence, runtime_checkable


@runtime_checkable
class Actor(Protocol):
    """Public contract for one managed actor runtime."""

    actor_id: str

    async def tick(self) -> Any:
        """Run one complete actor cycle."""
        ...

    def summary(self) -> dict[str, Any]:
        """Return an actor-safe status projection."""
        ...


@runtime_checkable
class Society(Protocol):
    """Public contract for a society that owns actor runtimes."""

    @property
    def society_id(self) -> str: ...

    def get_actor(self, actor_id: str) -> Actor | None: ...

    def actor_runtimes(self) -> Sequence[Actor]: ...

    async def tick(self) -> Any: ...

    def create_team(self, name: str, description: str = "") -> Any:
        """Create a Team — the tier beneath Society (Planet -> Country ->
        City -> Society -> Team -> Actor)."""
        ...

    def team_for_actor(self, actor_id: str) -> Any | None: ...

    async def tick_team(self, team_id: str) -> Any:
        """Tick every member actor of a team."""
        ...


@runtime_checkable
class Planet(Protocol):
    """Public contract for the planetary coordinator."""

    def get_society_runtime(self, society_id: str) -> Society | None: ...

    def all_societies(self) -> Sequence[Society]: ...

    def create_country(self, name: str, description: str = "") -> Any:
        """Create a Country — the tier beneath Planet (Planet -> Country ->
        City -> Society -> Team -> Actor)."""
        ...

    def create_city(self, name: str, country_id: str, description: str = "") -> Any | None: ...

    def get_country_for_society(self, society_id: str) -> Any | None: ...

    async def tick_city(self, city_id: str) -> Any:
        """Tick every society under a city, cascading to every actor."""
        ...

    async def tick_country(self, country_id: str) -> Any:
        """Tick every city under a country, cascading to every society and actor."""
        ...
