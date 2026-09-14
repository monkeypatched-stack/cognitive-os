"""Plan staleness detection — is a cached/reused Plan still valid against
current world state, or was it computed against assumptions that have
since changed?

Reuses KnowledgeGraph's real per-entity version counter
(kernel/knowledge_graph.py::version_of/_entity_version, bumped on every
update_entity()) rather than inventing a parallel world-state version
system. A CurrentPlanRecord captures {entity_id: version} for every
entity its steps reference at the moment it becomes the standing plan
(current_plan_store.py::CurrentPlanRecord.entity_versions); this module
re-checks those versions against live KG state before either of the two
existing plan-reuse points (belief_runtime.py's incremental-scheduling
skip-gate, comparison/integration.py's hysteresis "keep" branch) is
allowed to replay the cached plan unchecked.

Deliberately domain-agnostic: PlanStep.parameters is a free-form dict
whose shape is set by whichever domain capability produced it (e.g.
grocery's {"selection": [{"id": ..., "qty": ...}]}), so entity references
are found by a generic recursive scan for "id" keys rather than importing
any domain module — this stays a kernel/pipeline-layer concern.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


def _iter_ids(value: Any) -> list[str]:
    """Recursively collect every string found under an "id" key, anywhere
    inside a PlanStep.parameters value — handles the common
    {"selection": [{"id": X, "qty": N}, ...]} shape and any nested
    variant of it, without assuming a specific domain's exact structure."""
    found: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "id" and isinstance(v, str) and v:
                found.append(v)
            else:
                found.extend(_iter_ids(v))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_iter_ids(item))
    return found


def referenced_entity_ids(plan: Any) -> tuple[str, ...]:
    """Every distinct entity id referenced anywhere in plan.steps[*].parameters,
    in first-seen order."""
    seen: dict[str, None] = {}
    for step in getattr(plan, "steps", ()) or ():
        for eid in _iter_ids(getattr(step, "parameters", {}) or {}):
            seen.setdefault(eid, None)
    return tuple(seen)


def capture_entity_versions(kg: Any, plan: Any) -> dict[str, int]:
    """{entity_id: version} for every entity in referenced_entity_ids(plan)
    that actually resolves in kg right now — an id that never resolves
    (e.g. a non-entity parameter that merely happened to be nested under
    an "id" key) is silently skipped rather than recorded as version 0,
    so it can never later be misread as "this entity existed at version 0
    and something created it since" (see check_plan_staleness's "unknown
    entity" branch for why that distinction matters)."""
    if kg is None:
        return {}
    versions: dict[str, int] = {}
    for eid in referenced_entity_ids(plan):
        entity = kg.get_entity(eid)
        if entity is not None:
            versions[eid] = kg.version_of(eid)
    return versions


@dataclass(frozen=True)
class StalenessReason:
    entity_id: str
    entity_name: str
    reason: str
    """Human-readable, e.g. "Whole Milk: quantity 5 -> 0" or
    "Whole Milk: no longer exists"."""
    recorded_version: int
    current_version: int | None
    """None when the entity no longer resolves at all."""


@dataclass(frozen=True)
class PlanStalenessResult:
    is_stale: bool
    reasons: tuple[StalenessReason, ...] = ()
    invalidated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_stale": self.is_stale,
            "invalidated_at": self.invalidated_at,
            "affected_assumptions": [
                {
                    "entity_id": r.entity_id,
                    "entity_name": r.entity_name,
                    "reason": r.reason,
                    "recorded_version": r.recorded_version,
                    "current_version": r.current_version,
                }
                for r in self.reasons
            ],
        }


def _describe_change(entity: Any, recorded_version: int, current_version: int) -> str:
    name = getattr(entity, "name", None) or "entity"
    attrs = getattr(entity, "attributes", {}) or {}
    quantity = attrs.get("quantity")
    if quantity is not None and quantity <= 0:
        return f"{name}: out of stock (quantity={quantity})"
    return f"{name}: changed since plan was cached (version {recorded_version} -> {current_version})"


def check_plan_staleness(kg: Any, record: Any) -> PlanStalenessResult:
    """Compare a CurrentPlanRecord's recorded entity_versions against live
    KG state. Fails CLOSED on ambiguity in one specific sense only: a
    referenced entity that no longer resolves at all is always reported
    stale (it cannot be re-validated). A record with no entity_versions
    at all (e.g. a plan that referenced no entities, or one persisted
    before this field existed) is never considered stale by this check —
    there is nothing to compare, not evidence of staleness."""
    entity_versions = getattr(record, "entity_versions", None) or {}
    if kg is None or not entity_versions:
        return PlanStalenessResult(is_stale=False)

    reasons: list[StalenessReason] = []
    for entity_id, recorded_version in entity_versions.items():
        entity = kg.get_entity(entity_id)
        if entity is None:
            reasons.append(
                StalenessReason(
                    entity_id=entity_id,
                    entity_name=entity_id,
                    reason="no longer exists",
                    recorded_version=recorded_version,
                    current_version=None,
                )
            )
            continue
        current_version = kg.version_of(entity_id)
        if current_version != recorded_version:
            reasons.append(
                StalenessReason(
                    entity_id=entity_id,
                    entity_name=getattr(entity, "name", entity_id),
                    reason=_describe_change(entity, recorded_version, current_version),
                    recorded_version=recorded_version,
                    current_version=current_version,
                )
            )

    return PlanStalenessResult(is_stale=bool(reasons), reasons=tuple(reasons))
