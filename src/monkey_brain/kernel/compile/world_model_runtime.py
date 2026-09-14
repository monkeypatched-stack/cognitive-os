"""WorldModelRuntime — Layer 1: World Inference.

The global world model is the system's consensus estimate of reality.

    W_{t+1} = f(W_t, O₁, O₂, ..., Oₙ)

The global world model is ground truth. It is mutated ONLY in batches (adding a store, a
domain, new capabilities) — never by an actor's actions. Actors read from it and derive
their own LOCAL belief models (Layer 2), which their actions modify.

Per the product vision, an enterprise / institution / community operates one
WorldModelRuntime; individuals attach their ActorRuntime to it and exchange knowledge
under policy.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping

from src.monkey_brain.kernel.compile import _obs
from src.monkey_brain.kernel.compile.context import Context
from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor
from src.monkey_brain.kernel.compile.tenancy import TenantView, TenantWorld

logger = logging.getLogger("agentos.compile.world_runtime")


class WorldModelRuntime:
    """Owns the global world model and the only paths that mutate it (batches)."""

    def __init__(self, world: TenantWorld | None = None, *, semantic_world: Any = None) -> None:
        self._world = world or TenantWorld()
        # PlanetaryRuntime may inject the existing SharedWorld. This keeps the
        # semantic world identity stable while making this runtime the single
        # world-facing facade; no second SharedWorld is constructed.
        self._semantic_world = semantic_world

    @property
    def semantic_world(self) -> Any:
        """The injected semantic world, when used by PlanetaryRuntime."""
        return self._semantic_world

    # ── the only mutation path: batches ──────────────────────────────────────────

    def batch_update(
        self,
        tenant_id: str,
        transitions: Iterable[Mapping],
        *,
        shared: bool = False,
        source: str = "",
    ) -> int:
        """Batch-update the global world for a tenant (or the shared layer). This is the
        ONLY way the global model changes — actor actions never reach here."""
        rev = self._world.batch_update(tenant_id, transitions, shared=shared, source=source or "batch")
        logger.info(
            "[world_runtime] batch update tenant=%r shared=%s source=%r",
            tenant_id,
            shared,
            source,
        )
        _obs.event(
            "world.batch",
            tenant=tenant_id,
            shared=shared,
            source=source or "batch",
            revision=rev,
        )
        return rev

    def add_store(self, tenant_id: str, store: str, domain: str, transitions: Iterable[Mapping]) -> int:
        return self.batch_update(
            tenant_id,
            [{**t, "domain": t.get("domain", domain)} for t in transitions],
            source=f"store:{store}",
        )

    def promote_shared(self, tenant_id: str, src: str, dst: str, **kw) -> None:
        """Policy-governed knowledge exchange: publish a tenant's transition as a shared
        abstraction other runtimes may read (private weights stay local)."""
        self._world.promote_to_shared(tenant_id, src, dst, **kw)

    def share_domain(self, tenant_id: str, domain: str, *, ontology_only: bool = False) -> int:
        """Selective knowledge exchange: share only one domain (e.g. calendar, not
        journal). ontology_only shares structure without personal values."""
        count = self._world.share_domain(tenant_id, domain, ontology_only=ontology_only)
        _obs.event(
            "knowledge.share",
            tenant=tenant_id,
            domain=domain,
            ontology_only=ontology_only,
            count=count,
        )
        return count

    # ── read access (never mutated by callers) ───────────────────────────────────

    def view(self, context: Context) -> TenantView:
        """A read-only, tenant-scoped view of the global world for the caller's context."""
        return self._world.view(context)

    def new_local_belief(self) -> SparseTransitionTensor:
        """A fresh LOCAL belief model to inject into an ActorRuntime. Starts empty — the
        actor discovers the world through its own execution, folding what it observes into
        this belief. The actor's actions modify only this, never the global model."""
        return SparseTransitionTensor()

    def tenants(self) -> list[str]:
        return self._world.tenants()

    def summary(self) -> dict[str, Any]:
        return self._world.summary()

    # ── semantic world ownership facade ───────────────────────────────────────

    def _require_semantic_world(self) -> Any:
        if self._semantic_world is None:
            raise RuntimeError("semantic world is not attached")
        return self._semantic_world

    def add_entity(self, entity: Any) -> None:
        self._require_semantic_world().add_entity(entity)

    def update_entity(self, entity_id: str, **attributes: Any) -> Any:
        return self._require_semantic_world().update_entity(entity_id, **attributes)

    def remove_entity(self, entity_id: str) -> bool:
        return self._require_semantic_world().remove_entity(entity_id)

    def add_relationship(self, relationship: Any) -> None:
        self._require_semantic_world().add_relationship(relationship)

    def remove_relationship(self, relationship_id: str) -> bool:
        return self._require_semantic_world().remove_relationship(relationship_id)

    def record_event(self, event: Any) -> None:
        self._require_semantic_world().record_event(event)

    def remove_event(self, event_id: str) -> bool:
        return self._require_semantic_world().remove_event(event_id)

    def add_resource(self, resource: Any) -> None:
        self._require_semantic_world().add_resource(resource)

    def remove_resource(self, resource_id: str) -> bool:
        return self._require_semantic_world().remove_resource(resource_id)

    def add_location(self, location: Any) -> None:
        self._require_semantic_world().add_location(location)

    def update_location(self, location_id: str, **kwargs: Any) -> Any:
        return self._require_semantic_world().update_location(location_id, **kwargs)

    def remove_location(self, location_id: str) -> bool:
        return self._require_semantic_world().remove_location(location_id)

    def perturb(self, **kwargs: Any) -> Any:
        return self._require_semantic_world().perturb(**kwargs)

    def apply_learning(self, world_impact: Mapping[str, Any] | None) -> int:
        """Apply explicitly declared semantic world refinements.

        Learning remains actor/society-produced, but any world mutation is
        routed through this Planetary-owned facade. Unknown impact shapes are
        ignored for compatibility; this method does not invent a new learning
        model.
        """
        impact = dict(world_impact or {})
        updates = impact.get("entity_updates", ())
        applied = 0
        for update in updates:
            entity_id = update.get("entity_id")
            attributes = update.get("attributes", {})
            if entity_id and self.update_entity(entity_id, **attributes) is not None:
                applied += 1
        return applied
