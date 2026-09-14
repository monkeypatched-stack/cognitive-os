"""Bridge between the actor-relationship REST shape and actor-owned Affiliations.

Actor-Centric Affiliation Architecture (docs/compose/specs/2026-07-25-actor-centric-
affiliation-design.md) moved relationship ownership from the Society (the legacy
ActorRelationship/RelationshipType in kernel/society/domain.py) onto the Actor via
AffiliationManager. This module lets the /actors/{id}/relationships endpoints keep
their existing request/response shape while storing data in each actor's own
AffiliationManager instead of Society.relationships.

Every relationship edge is stored as one Affiliation on the source actor's manager
and, when the target is a live actor, a mirrored Affiliation on the target's manager
— so either side can list it, matching the old Society-owned model's either-side
visibility. `_rel_source_actor_id`/`_rel_target_actor_id` in metadata always record
the true original direction, regardless of which manager holds a given copy.
"""

from __future__ import annotations
from typing import Any
from .affiliation import Affiliation

_SOURCE_KEY = "_rel_source_actor_id"
_TARGET_KEY = "_rel_target_actor_id"


def relationship_affiliation_id(source_id: str, target_id: str, relationship_type: str) -> str:
    """Stable ID shared by the source's and target's copies of one edge."""
    return f"rel:{source_id}:{target_id}:{relationship_type}"


def make_relationship_affiliation(
    *,
    source_id: str,
    source_name: str,
    target_id: str,
    target_name: str,
    relationship_type: str,
    strength: float,
    metadata: dict[str, Any],
    owner_id: str,
) -> Affiliation:
    """Build the Affiliation for one side of a relationship edge.

    `owner_id` is whichever actor's manager this copy will be added to
    (source or target); the resulting Affiliation always points at the
    *other* party.
    """
    points_at, points_at_name = (target_id, target_name) if owner_id == source_id else (source_id, source_name)
    return Affiliation(
        affiliation_id=relationship_affiliation_id(source_id, target_id, relationship_type),
        affiliation_type=relationship_type,
        target_id=points_at,
        target_name=points_at_name,
        trust_level=strength,
        metadata={_SOURCE_KEY: source_id, _TARGET_KEY: target_id, **metadata},
    )


def is_relationship_affiliation(a: Affiliation) -> bool:
    return _SOURCE_KEY in a.metadata


def affiliation_to_relationship_dict(actor_id: str, a: Affiliation) -> dict[str, Any]:
    """Reconstruct the original {source,target,type,strength,metadata} shape,
    regardless of which side (source's or target's manager) stored this copy."""
    meta = dict(a.metadata)
    source_id = meta.pop(_SOURCE_KEY, actor_id)
    target_id = meta.pop(_TARGET_KEY, a.target_id)
    return {
        "source_actor_id": source_id,
        "target_actor_id": target_id,
        "relationship_type": a.affiliation_type,
        "strength": a.trust_level,
        "metadata": meta,
    }
