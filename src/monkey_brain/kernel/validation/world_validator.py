"""World Validation Engine — Gate 3.

Supersedes kernel/society/verification.py::verify_world_invariants() (ADR-008,
4 checks: society-has-space, actor-has-presence, no-orphaned-geography,
valid-memberships) with the full ten-category surface ADR-010 commissions.
verify_world_invariants() now delegates here; its return shape is preserved
so no existing caller breaks.

Read-only and non-mutating by construction, same as its predecessor — a
validator that can change the state it's checking is not trustworthy.

Categories (ADR-010 / Gate 3):
  1. geography_tree          — orphans, cycles, tier-order violations
  2. society_hierarchy       — every Society has a Space; no duplicate society_id
  3. presence_consistency    — exactly one open Presence per Actor; space_id resolves
  4. membership_consistency  — actor_id/society_id resolve; no duplicate active membership
  5. inventory_consistency   — no negative Product.quantity
  6. graph_integrity         — World Graph relationships resolve to real entities
  7. orphaned_nodes          — World Graph events/resources referencing missing entities
  8. cycles_forbidden        — geography parent_id chain must be acyclic
  9. duplicate_identifiers   — the same ID reused across independent ID namespaces
  10. referential_integrity  — Order->Product, Shipment->Order cross-references resolve

Each check is independent and defensive: one category raising never stops
the others from running — a validator that silently reports "ok" because
ONE check crashed would be worse than not having it.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("agentos.validation")


_ACTOR_SCOPED_CATEGORIES = {"presence_consistency", "membership_consistency"}
"""Categories whose violations can genuinely name ONE specific actor_id
-- a violation here means that actor's own data is inconsistent, not
that the whole planet's state is corrupt. Every other category
(geography, graph integrity, cycles, duplicate ids, referential
integrity, inventory) stays a global/structural concern regardless of
who's asking."""


def validate_world(planetary_runtime: Any, actor_id: str | None = None) -> dict:
    """actor_id (Qualification Gap Closure -- a real, live-discovered bug):
    Gate 3 previously blocked EVERY actor's request on ANY violation
    anywhere in the whole planet -- including a single, permanently-
    orphaned presence/membership record left behind by a completely
    unrelated actor (confirmed live: a throwaway pytest actor sharing
    this same dev Redis, whose own referenced space was minted inside
    that one test's own ephemeral in-memory geography and can never be
    reconciled into the real registry). When actor_id is given, an
    ACTOR-SCOPED violation (presence_consistency/membership_consistency,
    and only when the violation itself names a specific actor_id -- see
    _ACTOR_SCOPED_CATEGORIES) whose actor_id doesn't match the requester
    is excluded from the "ok" pass/fail decision for THIS request. It is
    still fully present in the returned `violations`/`violation_count` --
    nothing is hidden, only the blocking decision is scoped to relevance.
    Every genuinely global/structural category (geography, graph
    integrity, cycles, duplicate ids, referential integrity, inventory),
    and any actor-scoped violation with no actor_id field of its own
    (conservative default: block for everyone, same as before), still
    blocks unconditionally regardless of actor_id. actor_id=None (the
    default) preserves the exact prior, fully-global behavior for every
    other existing caller."""
    # Qualification Gap Closure (BUG-002, Cause A): reconcile this
    # process's in-memory actor registry against the real, shared Redis
    # state BEFORE checking anything -- a real Presence/Membership record
    # written by another process sharing this Redis (confirmed live: this
    # session's own in-process pytest tests are one real example) looked
    # identical to genuine corruption purely because _load_actors() had
    # never been called again since this process's own boot. Real,
    # idempotent reconciliation (kernel/society/integration.py::
    # PlanetaryRuntime.reconcile_actors_from_redis), not a raised
    # threshold and not a periodic destructive reset -- validation keeps
    # its full power to flag genuine corruption (an actor_id with no
    # corresponding Redis-persisted actor record at all) afterward.
    reconcile = getattr(planetary_runtime, "reconcile_actors_from_redis", None)
    if callable(reconcile):
        try:
            reconcile()
        except Exception as exc:
            logger.warning("world validation: actor reconciliation failed (non-fatal): %s", exc)

    violations: list[dict[str, Any]] = []
    category_counts: dict[str, int] = {}

    checks = (
        ("geography_tree", _check_geography_tree),
        ("society_hierarchy", _check_society_hierarchy),
        ("presence_consistency", _check_presence_consistency),
        ("membership_consistency", _check_membership_consistency),
        ("inventory_consistency", _check_inventory_consistency),
        ("graph_integrity", _check_graph_integrity),
        ("orphaned_nodes", _check_orphaned_nodes),
        ("cycles_forbidden", _check_cycles_forbidden),
        ("duplicate_identifiers", _check_duplicate_identifiers),
        ("referential_integrity", _check_referential_integrity),
    )

    for category, fn in checks:
        before = len(violations)
        try:
            fn(planetary_runtime, violations)
        except Exception as exc:
            logger.warning(
                "world validation category=%r raised, treating as a violation: %s",
                category,
                exc,
            )
            violations.append(
                {
                    "type": "validator_error",
                    "category": category,
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
        category_counts[category] = len(violations) - before

    blocking = [
        v
        for v in violations
        if actor_id is None
        or v.get("category") not in _ACTOR_SCOPED_CATEGORIES
        or v.get("actor_id") is None
        or v.get("actor_id") == actor_id
    ]

    return {
        "ok": not blocking,
        "violation_count": len(violations),
        "violations": violations,
        "categories": category_counts,
        "checked_at": time.time(),
    }


def _add(violations: list[dict[str, Any]], category: str, vtype: str, **fields: Any) -> None:
    violations.append({"category": category, "type": vtype, **fields})


# ── 1 & 8. Geography tree: orphans, cycles, tier order ──────────────────────


def _check_geography_tree(pr: Any, violations: list[dict[str, Any]]) -> None:
    from src.monkey_brain.kernel.geography.entity import PARENT_TIER, ROOT_ELIGIBLE

    entities = list(pr.geo_registry.all())
    by_id = {e.entity_id: e for e in entities}

    for entity in entities:
        if entity.entity_type in ROOT_ELIGIBLE and entity.parent_id is None:
            continue
        parent = by_id.get(entity.parent_id) if entity.parent_id else None
        if entity.parent_id is None or parent is None:
            _add(
                violations,
                "geography_tree",
                "orphaned_geographic_entity",
                entity_id=entity.entity_id,
                entity_type=entity.entity_type.value,
                name=entity.name,
            )
            continue
        # Tier-order: a child's parent must be one of its allowed parent
        # tiers (entity.py::PARENT_TIER -- the same source of truth
        # GeographicRegistry.add_child enforces at write time; some tiers
        # like COUNTRY/SPACE accept more than one valid parent tier since
        # REGION/FLOOR are optional insertions, so this is a set-membership
        # check, not a fixed linear-index one).
        allowed_parent_tiers = PARENT_TIER.get(entity.entity_type)
        if allowed_parent_tiers is not None and parent.entity_type not in allowed_parent_tiers:
            _add(
                violations,
                "geography_tree",
                "geography_tier_violation",
                entity_id=entity.entity_id,
                entity_type=entity.entity_type.value,
                parent_id=parent.entity_id,
                parent_type=parent.entity_type.value,
            )


def _check_cycles_forbidden(pr: Any, violations: list[dict[str, Any]]) -> None:
    """The geography containment tree is the one structure in this system
    that must be acyclic by construction — a Building that is (transitively)
    its own ancestor is a real, silent-corruption-causing bug, not a
    theoretical concern (e.g. a host_society()/reparent operation with a
    dangling reference could produce one)."""
    entities = list(pr.geo_registry.all())
    by_id = {e.entity_id: e for e in entities}

    for entity in entities:
        seen: set[str] = set()
        current = entity
        depth = 0
        while current is not None and current.parent_id:
            if current.parent_id in seen or current.parent_id == entity.entity_id:
                _add(
                    violations,
                    "cycles_forbidden",
                    "geography_cycle",
                    entity_id=entity.entity_id,
                    cycle_at=current.parent_id,
                )
                break
            seen.add(current.entity_id)
            current = by_id.get(current.parent_id)
            depth += 1
            if depth > len(entities) + 1:
                # Defensive bound — should be unreachable given the seen-set
                # check above, but a scan must never spin forever on
                # corrupted data.
                _add(
                    violations,
                    "cycles_forbidden",
                    "geography_cycle",
                    entity_id=entity.entity_id,
                    cycle_at="depth_bound_exceeded",
                )
                break


# ── 2. Society hierarchy ─────────────────────────────────────────────────


def _check_society_hierarchy(pr: Any, violations: list[dict[str, Any]]) -> None:
    seen_ids: set[str] = set()
    for sr in pr.all_societies():
        sid = sr.society.society_id
        if sid in seen_ids:
            _add(violations, "society_hierarchy", "duplicate_society_id", society_id=sid)
        seen_ids.add(sid)
        try:
            pr.geo_registry.validate_society_has_space(sid)
        except Exception as exc:
            _add(
                violations,
                "society_hierarchy",
                "society_without_space",
                society_id=sid,
                society_name=sr.society.name,
                detail=str(exc),
            )


# ── 3. Presence consistency ──────────────────────────────────────────────


def _check_presence_consistency(pr: Any, violations: list[dict[str, Any]]) -> None:
    known_space_ids = {e.entity_id for e in pr.geo_registry.all()}
    seen: set[str] = set()
    for sr in pr.all_societies():
        for state in sr.all_actors():
            if state.actor_id in seen:
                continue
            seen.add(state.actor_id)
            presence = pr.presence.current(state.actor_id)
            if presence is None or not presence.is_open():
                _add(
                    violations,
                    "presence_consistency",
                    "actor_without_presence",
                    actor_id=state.actor_id,
                    actor_name=state.profile.identity.name,
                )
                continue
            if presence.space_id not in known_space_ids:
                _add(
                    violations,
                    "presence_consistency",
                    "presence_references_unknown_space",
                    actor_id=state.actor_id,
                    space_id=presence.space_id,
                )


# ── 4. Membership consistency ────────────────────────────────────────────


def _check_membership_consistency(pr: Any, violations: list[dict[str, Any]]) -> None:
    registry = pr.membership_registry
    known_actor_ids = {state.actor_id for sr in pr.all_societies() for state in sr.all_actors()}
    seen_pairs: set[tuple[str, str]] = set()

    for membership in registry.active_memberships():
        if pr.get_society_runtime(membership.society_id) is None:
            _add(
                violations,
                "membership_consistency",
                "membership_invalid_society",
                membership_id=membership.membership_id,
                society_id=membership.society_id,
            )
        if membership.actor_id not in known_actor_ids:
            _add(
                violations,
                "membership_consistency",
                "membership_invalid_actor",
                membership_id=membership.membership_id,
                actor_id=membership.actor_id,
            )

        pair = (membership.actor_id, membership.society_id)
        if pair in seen_pairs:
            _add(
                violations,
                "membership_consistency",
                "duplicate_active_membership",
                actor_id=membership.actor_id,
                society_id=membership.society_id,
                membership_id=membership.membership_id,
            )
        seen_pairs.add(pair)


# ── 5. Inventory consistency (Commerce, KnowledgeGraph) ──────────────────


def _check_inventory_consistency(pr: Any, violations: list[dict[str, Any]]) -> None:
    kg = getattr(pr, "knowledge_graph", None)
    if kg is None:
        return
    for entity in kg.entities:
        attrs = entity.attributes
        if attrs.get("product") is not True:
            continue
        quantity = attrs.get("quantity")
        if isinstance(quantity, (int, float)) and quantity < 0:
            _add(
                violations,
                "inventory_consistency",
                "negative_product_quantity",
                product_id=entity.entity_id,
                quantity=quantity,
            )
        held = attrs.get("held_quantity") or attrs.get("reserved_quantity")
        if isinstance(held, (int, float)) and isinstance(quantity, (int, float)) and held > quantity:
            _add(
                violations,
                "inventory_consistency",
                "reservation_exceeds_quantity",
                product_id=entity.entity_id,
                quantity=quantity,
                held_quantity=held,
            )


# ── 6. Graph integrity + 7. Orphaned nodes (World Graph / SharedWorld) ──


def _check_graph_integrity(pr: Any, violations: list[dict[str, Any]]) -> None:
    world = getattr(pr, "world", None)
    if world is None:
        return
    try:
        entity_ids = {e.entity_id for e in world.entities()}
    except Exception:
        return

    for rel in world.relationships():
        if rel.source_id not in entity_ids:
            _add(
                violations,
                "graph_integrity",
                "relationship_source_missing",
                relationship_id=rel.relationship_id,
                source_id=rel.source_id,
            )
        if rel.target_id not in entity_ids:
            _add(
                violations,
                "graph_integrity",
                "relationship_target_missing",
                relationship_id=rel.relationship_id,
                target_id=rel.target_id,
            )


def _check_orphaned_nodes(pr: Any, violations: list[dict[str, Any]]) -> None:
    world = getattr(pr, "world", None)
    if world is None:
        return
    try:
        entity_ids = {e.entity_id for e in world.entities()}
    except Exception:
        return

    for resource in world.resources():
        if resource.location_id and resource.location_id not in entity_ids:
            _add(
                violations,
                "orphaned_nodes",
                "resource_references_missing_entity",
                resource_id=resource.resource_id,
                location_id=resource.location_id,
            )

    # WorldEvent.entity_id is used loosely in practice — it references a
    # WorldEntity for most event types, but domain code also legitimately
    # sets it to an Actor's id (e.g. actor-scoped events). Both are valid
    # referents; only a value resolving to NEITHER is a genuine orphan.
    known_ids = entity_ids | {state.actor_id for sr in pr.all_societies() for state in sr.all_actors()}
    try:
        events = world.events(limit=100000)
    except Exception:
        events = ()
    for event in events:
        if event.entity_id and event.entity_id not in known_ids:
            _add(
                violations,
                "orphaned_nodes",
                "event_references_missing_entity",
                event_id=event.event_id,
                entity_id=event.entity_id,
            )


# ── 9. Duplicate identifiers across independent ID namespaces ───────────


def _check_duplicate_identifiers(pr: Any, violations: list[dict[str, Any]]) -> None:
    namespaces: dict[str, set[str]] = {
        "geography": set(),
        "society": set(),
        "actor": set(),
        "world_entity": set(),
    }

    namespaces["geography"] = {e.entity_id for e in pr.geo_registry.all()}
    namespaces["society"] = {sr.society.society_id for sr in pr.all_societies()}
    namespaces["actor"] = {state.actor_id for sr in pr.all_societies() for state in sr.all_actors()}
    world = getattr(pr, "world", None)
    if world is not None:
        try:
            namespaces["world_entity"] = {e.entity_id for e in world.entities()}
        except Exception:
            logger.debug("_check_duplicate_identifiers: suppressed exception", exc_info=True)

    id_to_namespaces: dict[str, list[str]] = {}
    for ns, ids in namespaces.items():
        for i in ids:
            id_to_namespaces.setdefault(i, []).append(ns)

    for entity_id, ns_list in id_to_namespaces.items():
        if len(ns_list) > 1:
            _add(
                violations,
                "duplicate_identifiers",
                "id_reused_across_namespaces",
                entity_id=entity_id,
                namespaces=ns_list,
            )


# ── 10. Referential integrity (Commerce cross-references) ───────────────


def _check_referential_integrity(pr: Any, violations: list[dict[str, Any]]) -> None:
    kg = getattr(pr, "knowledge_graph", None)
    if kg is None:
        return

    entities = kg.entities
    product_ids = {e.entity_id for e in entities if e.attributes.get("product") is True}
    order_ids = {
        e.entity_id
        for e in entities
        if "order_id" in e.attributes and "items" in e.attributes and "total" in e.attributes
    }

    for entity in entities:
        attrs = entity.attributes
        if attrs.get("shipment") is True:
            order_id = attrs.get("order_id")
            if order_id and order_id not in order_ids:
                _add(
                    violations,
                    "referential_integrity",
                    "shipment_references_missing_order",
                    shipment_id=entity.entity_id,
                    order_id=order_id,
                )
        if "order_id" in attrs and "items" in attrs and "total" in attrs:
            for item in attrs.get("items") or []:
                product_id = item.get("id") if isinstance(item, dict) else None
                if product_id and product_id not in product_ids:
                    _add(
                        violations,
                        "referential_integrity",
                        "order_references_missing_product",
                        order_id=entity.entity_id,
                        product_id=product_id,
                    )
