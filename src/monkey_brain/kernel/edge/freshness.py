"""Cache provenance and freshness — the shared vocabulary every edge-local
cache (belief, world-state projection, policy snapshot, semantic-memory
hit, capability metadata) uses to answer one question honestly: is this
cached copy of authoritative state still safe to use right now?

No cached authoritative object may silently masquerade as current truth.
Every `CacheProvenance` this module classifies must have come from a real
central source (a Redis/Mongo/Neo4j read, an OPA evaluation, a signed
policy snapshot) at the recorded `observed_at`/`version`/`authority_epoch`
— this module only ever judges freshness of provenance that already
exists; it never fabricates provenance for data that doesn't have any.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Freshness(Enum):
    """What the local runtime may safely infer about a cached object's
    relationship to current authoritative truth. Distinct from a plain
    boolean "expired" -- STALE_BUT_USABLE exists because some cached
    facts (e.g. a product's price observed 90s ago) remain acceptable to
    act on past their strict `expires_at` for read-shaped operations,
    while STALE_MUST_REFRESH means the opposite: never usable once past
    expiry, no matter how minor the operation. UNKNOWN is not the same
    as "stale" -- it means this module could not establish a
    trustworthy age/version for the object at all (missing provenance),
    which must be treated at least as conservatively as STALE_MUST_REFRESH."""
    FRESH = "fresh"
    STALE_BUT_USABLE = "stale_but_usable"
    STALE_MUST_REFRESH = "stale_must_refresh"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CacheProvenance:
    """Everything a local cache entry must retain to let a later reader
    decide, honestly, whether it's still safe to use -- Section 2's
    explicit minimum field set.

    freshness_requirement mirrors this codebase's existing OperationSafety
    split (kernel/pipeline/offline_safety.py): a caller who only needs
    SAFE_OFFLINE/REQUIRES_WORLD_STATE-grade freshness may accept
    STALE_BUT_USABLE data; a caller needing REQUIRES_AUTHORITY-grade
    freshness must only ever accept FRESH.
    """
    source: str
    """Where this was observed from, e.g. "neo4j:knowledge_graph",
    "opa:agentos/routes/allow", "mongo:actor_state". Never "cache" or
    "edge" -- provenance names the ORIGINAL authoritative source, not
    the fact that it passed through this cache."""
    version: str = ""
    """The authoritative source's own version/revision marker for this
    object, when one exists (e.g. a KnowledgeGraph entity's
    compare_and_swap version, a policy snapshot's version string).
    Empty when the source has no versioning concept -- freshness then
    relies on observed_at/expires_at alone."""
    observed_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    """None = no explicit expiry from the source; freshness_requirement
    alone (via max_age_seconds) determines staleness."""
    authority_epoch: int = 0
    """Monotonically increasing counter the control plane advances on
    revocation/policy change (kernel/edge/policy_cache.py). A cache
    entry's own authority_epoch must be compared against the LOCAL
    store's last-known control-plane epoch, not just its own
    observed_at/expires_at -- a revocation can invalidate an entry that
    hasn't technically "expired" yet."""
    signature: str = ""
    """Ed25519 signature (kernel/identity.py) over this entry's
    security-relevant fields, when the source is itself something that
    must be cryptographically verifiable at the edge (signed policy
    snapshots, delegation credentials) -- empty for plain cached reads
    (KG projections, semantic-memory hits) that carry no such proof."""
    freshness_requirement: str = "requires_world_state"
    """One of offline_safety.py's OperationSafety values (as a string, to
    avoid this module depending on pipeline/offline_safety.py — kept a
    plain string rather than importing the enum so this stays a leaf
    module with zero pipeline dependencies): "safe_offline" |
    "requires_world_state" | "requires_authority" | "requires_sync".
    Determines the max acceptable age when max_age_seconds must be
    inferred (see classify_freshness)."""


# How long a STALE_BUT_USABLE window can extend past expires_at, per
# freshness_requirement -- deliberately asymmetric: read-shaped
# ("requires_world_state") data degrades gracefully for a bounded window,
# authority-shaped ("requires_authority") data never does. Matches
# offline_safety.py's own SAFE_OFFLINE/REQUIRES_WORLD_STATE/
# REQUIRES_AUTHORITY/REQUIRES_SYNC risk tiers.
_GRACE_WINDOW_SECONDS: dict[str, float] = {
    "safe_offline": float("inf"),
    "requires_world_state": 300.0,
    "requires_authority": 0.0,
    "requires_sync": 0.0,
}


def classify_freshness(
    provenance: CacheProvenance | None,
    *,
    now: float | None = None,
    current_authority_epoch: int | None = None,
) -> Freshness:
    """Pure function: given a cache entry's provenance and the current
    time (and, when known, the control plane's current authority epoch),
    what is this entry allowed to be treated as?

    Fail-closed ordering: missing provenance -> UNKNOWN before anything
    else is checked. A revoked epoch always wins over a not-yet-expired
    timestamp -- Section 2's "do not allow stale cached data to silently
    masquerade as current" applies most sharply to revocation.
    """
    if provenance is None:
        return Freshness.UNKNOWN

    now = time.time() if now is None else now

    if current_authority_epoch is not None and provenance.authority_epoch < current_authority_epoch:
        return Freshness.STALE_MUST_REFRESH

    if provenance.expires_at is not None and now <= provenance.expires_at:
        return Freshness.FRESH

    grace = _GRACE_WINDOW_SECONDS.get(provenance.freshness_requirement, 0.0)
    if grace == float("inf"):
        # safe_offline: staleness never matters for this category,
        # regardless of whether (or how long ago) an expires_at was set.
        # Checked before the expires_at branch below so an infinite grace
        # window applies uniformly, not only to the no-expiry fallback.
        return Freshness.FRESH

    if provenance.expires_at is not None:
        if now <= provenance.expires_at + grace:
            return Freshness.STALE_BUT_USABLE
        return Freshness.STALE_MUST_REFRESH

    # No expires_at at all: fall back to age-based judgment against the
    # same grace window, treating "no expiry stated" as "expiry was
    # observed_at" for the purpose of the grace window only -- never
    # treated as FRESH forever merely because no expiry was ever set.
    age = now - provenance.observed_at
    if age <= grace:
        return Freshness.STALE_BUT_USABLE
    return Freshness.STALE_MUST_REFRESH


def is_usable(freshness: Freshness) -> bool:
    """Whether an entry at this freshness level may be used at all
    (FRESH or STALE_BUT_USABLE) -- STALE_MUST_REFRESH and UNKNOWN never
    are, regardless of caller."""
    return freshness in (Freshness.FRESH, Freshness.STALE_BUT_USABLE)


def provenance_to_dict(provenance: CacheProvenance) -> dict[str, Any]:
    return {
        "source": provenance.source,
        "version": provenance.version,
        "observed_at": provenance.observed_at,
        "expires_at": provenance.expires_at,
        "authority_epoch": provenance.authority_epoch,
        "signature": provenance.signature,
        "freshness_requirement": provenance.freshness_requirement,
    }


def provenance_from_dict(data: dict[str, Any]) -> CacheProvenance:
    return CacheProvenance(
        source=data.get("source", ""),
        version=data.get("version", ""),
        observed_at=float(data.get("observed_at", time.time())),
        expires_at=data.get("expires_at"),
        authority_epoch=int(data.get("authority_epoch", 0)),
        signature=data.get("signature", ""),
        freshness_requirement=data.get("freshness_requirement", "requires_world_state"),
    )
