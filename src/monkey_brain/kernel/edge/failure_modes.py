"""Failure-mode classification for an edge/device/robot node (Section 18).

A pure lookup table plus a classify() function -- this module makes no
decisions and takes no action itself. It exists so that every place in
kernel/edge/ that has to answer "what do I do when X is unreachable"
answers it the SAME way, instead of each caller inventing its own ad-hoc
judgment. The actual mechanics for each outcome already exist elsewhere
in kernel/edge/ (EdgeLocalStore for cached data, EdgePolicyCache for
policy, LocalGovernanceEvaluator for local allow/deny, EdgeSyncClient for
reconciliation) -- this module only names which one applies to which
failure, per this task's five-way classification:

    LOCAL_CONTINUE  -- proceed using local state/cache; no functional
                       degradation the actor needs to know about.
    LOCAL_DEGRADE   -- proceed, but using stale/partial local data; the
                       actor should treat the result as lower-confidence.
    ESCALATE        -- cannot be resolved locally; must reach the control
                       plane (and block or defer until it can).
    DEFER           -- do not act now; retry later (transient condition
                       expected to clear on its own, e.g. a network
                       partition), no local substitute is safe.
    DENY            -- fail closed; never proceed locally without proof
                       of authority (revoked/expired delegation, a
                       required central authorization that isn't cached).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FailureMode(Enum):
    CONTROL_PLANE_UNAVAILABLE = "control_plane_unavailable"
    OPA_UNAVAILABLE = "opa_unavailable"
    REDIS_UNAVAILABLE = "redis_unavailable"
    MONGO_UNAVAILABLE = "mongo_unavailable"
    NEO4J_UNAVAILABLE = "neo4j_unavailable"
    OLLAMA_UNAVAILABLE = "ollama_unavailable"
    ELASTICSEARCH_UNAVAILABLE = "elasticsearch_unavailable"
    MOSS_UNAVAILABLE = "moss_unavailable"
    NETWORK_PARTITION = "network_partition"
    STALE_POLICY = "stale_policy"
    REVOKED_DELEGATION = "revoked_delegation"
    EXPIRED_DELEGATION = "expired_delegation"
    STALE_WORLD_STATE = "stale_world_state"
    LOCAL_DB_CORRUPTION = "local_db_corruption"
    ACTOR_RESTART = "actor_restart"


class FailureResponse(Enum):
    LOCAL_CONTINUE = "LOCAL_CONTINUE"
    LOCAL_DEGRADE = "LOCAL_DEGRADE"
    ESCALATE = "ESCALATE"
    DEFER = "DEFER"
    DENY = "DENY"


@dataclass(frozen=True)
class FailureClassification:
    mode: FailureMode
    response: FailureResponse
    mechanism: str
    rationale: str


_TABLE: dict[FailureMode, FailureClassification] = {
    FailureMode.CONTROL_PLANE_UNAVAILABLE: FailureClassification(
        FailureMode.CONTROL_PLANE_UNAVAILABLE, FailureResponse.LOCAL_DEGRADE,
        "EdgePolicyCache.get_valid() + LocalGovernanceEvaluator",
        "a validly-signed, unexpired policy snapshot lets local governance keep operating; "
        "anything the local cache cannot decide falls through to ESCALATE (deferred, see EdgeSyncClient.reconcile_after_partition)",
    ),
    FailureMode.OPA_UNAVAILABLE: FailureClassification(
        FailureMode.OPA_UNAVAILABLE, FailureResponse.LOCAL_DEGRADE,
        "local_policy_decision short-circuits the live OPA call in _authorize_and_gate",
        "identical to CONTROL_PLANE_UNAVAILABLE for the OPA-shaped part of governance specifically; "
        "insecure_dev_mode relaxation is a TEST-ONLY substitute, never a production answer to this",
    ),
    FailureMode.REDIS_UNAVAILABLE: FailureClassification(
        FailureMode.REDIS_UNAVAILABLE, FailureResponse.LOCAL_DEGRADE,
        "EdgeLocalStore (SQLite) as the L2 substitute for whatever Redis normally served",
        "Redis is a fast projection/idempotency cache, not the source of authority; "
        "losing it costs latency (falls back to L2/L3), not correctness",
    ),
    FailureMode.MONGO_UNAVAILABLE: FailureClassification(
        FailureMode.MONGO_UNAVAILABLE, FailureResponse.LOCAL_CONTINUE,
        "AuditLog's own best-effort audit path + kernel/delegation.py's best-effort _audit_delegation_event",
        "audit-intent recording is fail-closed for the MUTATION itself (ensure_governed still requires "
        "an audit intent to exist), but a down audit BACKEND must not block execution outright -- "
        "the actual synchronous stall this causes (Error #15 in this pass's profiling) is a "
        "remaining bottleneck, not a policy this table is asked to fix",
    ),
    FailureMode.NEO4J_UNAVAILABLE: FailureClassification(
        FailureMode.NEO4J_UNAVAILABLE, FailureResponse.LOCAL_DEGRADE,
        "EdgeLocalStore world_projection namespace + kernel/edge/freshness.classify_freshness",
        "Neo4j is authoritative for world state but a locally cached projection, if still FRESH or "
        "STALE_BUT_USABLE per its freshness_requirement, is sufficient for read-only reasoning; "
        "any write intended for Neo4j itself must ESCALATE (deferred) once queued for sync, never fabricated locally",
    ),
    FailureMode.OLLAMA_UNAVAILABLE: FailureClassification(
        FailureMode.OLLAMA_UNAVAILABLE, FailureResponse.LOCAL_DEGRADE,
        "EmbeddingStore.embed() returns None on failure (kernel/semantic_memory.py) "
        "-> SittingFaceKnowledgeRetriever falls back to keyword retrieval, never a fabricated embedding",
        "semantic (vector) retrieval degrades to keyword retrieval with truthful retrieval_method provenance; "
        "it never silently invents a vector (the MD5-fallback bug this task's predecessor removed)",
    ),
    FailureMode.ELASTICSEARCH_UNAVAILABLE: FailureClassification(
        FailureMode.ELASTICSEARCH_UNAVAILABLE, FailureResponse.LOCAL_DEGRADE,
        "CachedSittingFaceRetriever serves a fresh-enough cached report if one exists; otherwise "
        "SittingFaceKnowledgeRetriever's own no-external-knowledge path",
        "no external knowledge for this tick is a degraded answer, not a blocked one -- the actor's own "
        "world-state/plan reasoning does not depend on external knowledge being available",
    ),
    FailureMode.MOSS_UNAVAILABLE: FailureClassification(
        FailureMode.MOSS_UNAVAILABLE, FailureResponse.LOCAL_DEGRADE,
        "MossSemanticMemory.query() catches every error and returns {'results': []} "
        "(kernel/edge/moss_retrieval.py); SittingFaceKnowledgeRetriever then has simply "
        "found no vector hits this cycle, same as an empty EmbeddingStore result",
        "Moss (an OPTIONAL, narrowed-scope retrieval backend -- see "
        "docs/CLOUD_EDGE_ACTOR_ARCHITECTURE.md Section 18's MossDB scope decision) is never "
        "on the default path and never authoritative for anything; its absence costs "
        "retrieval quality, never correctness or security",
    ),
    FailureMode.NETWORK_PARTITION: FailureClassification(
        FailureMode.NETWORK_PARTITION, FailureResponse.LOCAL_DEGRADE,
        "EdgeSyncClient -- operate on last-synced policy/world snapshots until reconcile_after_partition succeeds",
        "this is the edge layer's central purpose: continue operating within the bounds of what was already "
        "verified and cached; anything requiring FRESH central authority not already cached must ESCALATE (blocked, deferred)",
    ),
    FailureMode.STALE_POLICY: FailureClassification(
        FailureMode.STALE_POLICY, FailureResponse.ESCALATE,
        "EdgePolicyCache.get_valid() returns None past TTL -> caller must resync or fall through to live authorization",
        "an expired policy snapshot is deliberately NOT treated as LOCAL_DEGRADE -- policy (who is allowed to do "
        "what) is exactly the kind of authority the edge is forbidden from extending past its verified window",
    ),
    FailureMode.REVOKED_DELEGATION: FailureClassification(
        FailureMode.REVOKED_DELEGATION, FailureResponse.DENY,
        "DelegationStore revocation cascade + VerifiedDelegationCache.invalidate_delegation()",
        "a revoked delegation is a security fact, not a staleness fact -- there is no local-continue "
        "substitute for authority that has been explicitly withdrawn",
    ),
    FailureMode.EXPIRED_DELEGATION: FailureClassification(
        FailureMode.EXPIRED_DELEGATION, FailureResponse.DENY,
        "verify_delegation_chain's own expiry check + VerifiedDelegationCache's TTL "
        "(never cached past the delegation's own expires_at)",
        "identical reasoning to REVOKED_DELEGATION: expiry is a hard boundary the edge must never "
        "extend on its own authority, regardless of how well-connected or disconnected it currently is",
    ),
    FailureMode.STALE_WORLD_STATE: FailureClassification(
        FailureMode.STALE_WORLD_STATE, FailureResponse.LOCAL_DEGRADE,
        "kernel/edge/freshness.classify_freshness -> STALE_BUT_USABLE vs STALE_MUST_REFRESH per freshness_requirement",
        "most world-state reads tolerate a bounded staleness window; a mutation whose "
        "freshness_requirement is stricter (STALE_MUST_REFRESH) escalates instead, per that entry's own tier",
    ),
    FailureMode.LOCAL_DB_CORRUPTION: FailureClassification(
        FailureMode.LOCAL_DB_CORRUPTION, FailureResponse.ESCALATE,
        "EdgeLocalStore raises EdgeLocalStoreError for the corrupt row only, treating it as absent; "
        "callers then have no local answer and must reach the control plane",
        "corruption is scoped to the single (namespace, key) row, not the whole store -- proven by "
        "test_edge_local_store.py::test_corrupt_entry_does_not_break_other_reads",
    ),
    FailureMode.ACTOR_RESTART: FailureClassification(
        FailureMode.ACTOR_RESTART, FailureResponse.LOCAL_CONTINUE,
        "EdgeLocalStore is a durable single SQLite file -- proven by "
        "test_edge_local_store.py::test_data_survives_reopening_the_same_db_file / test_sync_state_survives_restart",
        "a restart is not a failure of the store, only of the in-process caches "
        "(BoundedTTLCache/VerifiedDelegationCache/CachedContextConstructionEngine/CachedSittingFaceRetriever), "
        "which correctly re-populate as cold-start misses -- the durable local_store and sync_state survive intact",
    ),
}


def classify(mode: FailureMode) -> FailureClassification:
    return _TABLE[mode]


def all_classifications() -> tuple[FailureClassification, ...]:
    return tuple(_TABLE.values())
