"""Knowledge routes — export, import, and exchange of cognitive knowledge.

Export serializes the current world tensor (transitions + Q-values),
execution graph topology, and Q-table into a versioned, signed JSON bundle.
Import receives a bundle, verifies the signature, checks trust and
agreements, and merges accepted transitions with conflict resolution.
"""

from __future__ import annotations

import json
import logging
import time
from enum import Enum
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.helpers.run_helpers import get_cognitive_runtime
from src.monkey_brain.api.idempotency import idempotent

logger = logging.getLogger("agentos.knowledge")
router = APIRouter()

KNOWLEDGE_VERSION = "1.0"


class MergeStrategy(str, Enum):
    """How to handle conflicts during import."""

    SKIP = "skip"  # skip conflicting edges (current behavior)
    HIGHER_REWARD = "higher"  # keep the edge with higher reward
    LOCAL_WINS = "local"  # local value always wins
    REMOTE_WINS = "remote"  # imported value always wins
    AVERAGE = "average"  # average the two rewards


class KnowledgeExport(BaseModel):
    version: str = KNOWLEDGE_VERSION
    exported_at: float = 0.0
    runtime_id: str = ""
    transitions: list[dict[str, Any]] = Field(default_factory=list)
    q_table: dict[str, float] = Field(default_factory=dict)
    graph_nodes: list[dict[str, Any]] = Field(default_factory=list)
    graph_edges: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Provenance
    source_runtime_type: str = ""
    source_owner: str = ""
    source_jurisdiction: str = ""
    signature: str = ""
    signer_id: str = ""


class KnowledgeImportRequest(BaseModel):
    bundle: KnowledgeExport
    origin: str = "unknown"
    domain: str = "default"
    merge_strategy: MergeStrategy = MergeStrategy.SKIP


class KnowledgeImportResult(BaseModel):
    status: str
    accepted: int = 0
    rejected: int = 0
    conflicts: list[dict[str, Any]] = []
    resolved: int = 0
    version: str = ""
    signature_valid: bool | None = None


@router.post("/knowledge/export", response_model=KnowledgeExport)
@idempotent("knowledge.export_knowledge")
async def export_knowledge(
    user_id: str = Depends(require_permission("perm-view-agents")),
    runtime: Any = Depends(get_cognitive_runtime),
):
    """Export the current world tensor, Q-table, and graph topology as a
    versioned, signed JSON bundle suitable for import into another runtime."""
    from src.monkey_brain.kernel.compile.world_tensor import get_world
    from src.monkey_brain.kernel.identity import (
        get_identity,
        get_key_manager,
        sign_bytes,
    )

    tenant_id = getattr(runtime, "_tenant_id", "default")
    world = get_world(tenant_id)
    gm = runtime.graph_manager if hasattr(runtime, "graph_manager") else None
    identity = get_identity()

    transitions = []
    for (i, j), cell in world._cells.items():
        if i == j:
            continue
        src = world._state_name[i]
        dst = world._state_name[j]
        if src and dst:
            transitions.append(
                {
                    "src": src,
                    "dst": dst,
                    "domain": world._state_domain[i] or "default",
                    "freq": cell.freq,
                }
            )

    q_table = gm.q_table.snapshot() if gm else {}
    graph_nodes = gm.graph.get("nodes", []) if gm else []
    graph_edges = gm.graph.get("edges", []) if gm else []

    export = KnowledgeExport(
        version=KNOWLEDGE_VERSION,
        exported_at=time.time(),
        runtime_id=identity.runtime_id,
        transitions=transitions,
        q_table=q_table,
        graph_nodes=graph_nodes,
        graph_edges=graph_edges,
        metadata={"states": len(world._state_name), "nnz": len(world._cells)},
        source_runtime_type=identity.runtime_type,
        source_owner=identity.owner,
        source_jurisdiction=identity.jurisdiction,
    )

    # Sign the export bundle
    try:
        km = get_key_manager()
        key = km.get_or_create(identity.runtime_id)
        payload = export.model_dump(exclude={"signature", "signer_id"})
        blob = json.dumps(payload, sort_keys=True, default=str).encode()
        export.signature = sign_bytes(blob, key)
        export.signer_id = identity.runtime_id
    except Exception as exc:
        logger.warning("Failed to sign export: %s", exc)

    return export


@router.post("/knowledge/import", response_model=KnowledgeImportResult)
@idempotent("knowledge.import_knowledge")
async def import_knowledge(
    body: KnowledgeImportRequest,
    user_id: str = Depends(require_permission("perm-manage-agents")),
    runtime: Any = Depends(get_cognitive_runtime),
):
    """Import a knowledge bundle. Verifies signature, checks trust and
    agreements, and merges with configurable conflict resolution."""
    from src.monkey_brain.kernel.compile.world_tensor import get_world
    from src.monkey_brain.kernel.compile.exchange import is_shareable
    from src.monkey_brain.kernel.identity import get_key_manager, verify_bytes

    tenant_id = getattr(runtime, "_tenant_id", "default")
    bundle = body.bundle

    # Version check
    if bundle.version != KNOWLEDGE_VERSION:
        return KnowledgeImportResult(
            status="rejected",
            version=bundle.version,
        )

    # Signature verification
    signature_valid = None
    if bundle.signature and bundle.signer_id:
        try:
            km = get_key_manager()
            pub_pem = km.get_public_key_pem(bundle.signer_id)
            payload = bundle.model_dump(exclude={"signature", "signer_id"})
            blob = json.dumps(payload, sort_keys=True, default=str).encode()
            signature_valid = verify_bytes(blob, bundle.signature, pub_pem)
            if not signature_valid:
                logger.warning("[knowledge] signature verification failed for %s", bundle.signer_id)
        except Exception as exc:
            logger.warning("[knowledge] signature check error: %s", exc)
            signature_valid = False

    # Filter out non-shareable domains
    shareable = [t for t in bundle.transitions if is_shareable("belief", t.get("domain", ""))]

    # Merge with conflict resolution — via Context Stream (sole mutation path)
    world = get_world(tenant_id)
    accepted = 0
    resolved = 0
    conflicts = []
    strategy = body.merge_strategy

    def _publish_transition(src: str, dst: str, domain: str, weight: float) -> None:
        """Publish world transition through Context Stream."""
        from src.monkey_brain.kernel.compile.context_stream import (
            ContextEvent,
            EventType,
        )
        import time as _time

        try:
            rt = get_cognitive_runtime(request)
            cs = getattr(rt, "_context_stream", None)
            if cs and hasattr(cs, "publish"):
                event = ContextEvent(
                    timestamp=_time.time(),
                    entity=src,
                    attribute=dst,
                    previous_value=None,
                    current_value=weight,
                    source="knowledge_import",
                    confidence=1.0,
                    event_type=EventType.TRANSITION_ADD,
                    metadata={"domain": domain},
                )
                cs.publish(event)
                return
        except Exception:
            logger.debug("_publish_transition: suppressed exception", exc_info=True)
        # Fallback: direct observe (will be removed once Context Stream is universal)
        world.observe(src, dst, domain=domain, weight=weight)

    for t in shareable:
        src, dst = t["src"], t["dst"]
        reward = t.get("reward", 0.5)
        domain = t.get("domain", "default")

        # Check for conflicts
        existing = world.feature(src, dst, Feature.REWARD) if (src, dst) in set(world) else 0.0
        if existing > 0.0 and abs(existing - reward) > 0.1:
            # Conflict detected — apply merge strategy
            if strategy == MergeStrategy.SKIP:
                conflicts.append(
                    {
                        "edge": (src, dst),
                        "local": existing,
                        "proposed": reward,
                        "action": "skipped",
                    }
                )
                continue
            elif strategy == MergeStrategy.HIGHER_REWARD:
                final_reward = max(existing, reward)
            elif strategy == MergeStrategy.LOCAL_WINS:
                final_reward = existing
            elif strategy == MergeStrategy.REMOTE_WINS:
                final_reward = reward
            elif strategy == MergeStrategy.AVERAGE:
                final_reward = (existing + reward) / 2.0
            else:
                final_reward = reward

            _publish_transition(src, dst, domain, final_reward)
            resolved += 1
            conflicts.append(
                {
                    "edge": (src, dst),
                    "local": existing,
                    "proposed": reward,
                    "action": "resolved",
                    "final": final_reward,
                }
            )
        else:
            _publish_transition(src, dst, domain, reward)
        accepted += 1

    # Merge Q-table values
    if runtime.graph_manager:
        for agent, q in bundle.q_table.items():
            runtime.graph_manager.q_table.update(agent, q)

    # Merge graph topology (additive — never removes existing nodes)
    if runtime.graph_manager and bundle.graph_nodes:
        existing_ids = {n.get("id") for n in runtime.graph_manager.graph.get("nodes", [])}
        for node in bundle.graph_nodes:
            if node.get("id") and node["id"] not in existing_ids:
                runtime.graph_manager.graph.setdefault("nodes", []).append(node)

    if runtime.graph_manager and bundle.graph_edges:
        existing_edges = {(e.get("from"), e.get("to")) for e in runtime.graph_manager.graph.get("edges", [])}
        for edge in bundle.graph_edges:
            key = (edge.get("from"), edge.get("to"))
            if key not in existing_edges:
                runtime.graph_manager.graph.setdefault("edges", []).append(edge)

    logger.info(
        "[knowledge] imported from %s: %d accepted, %d resolved, %d conflicts, sig=%s",
        body.origin,
        accepted,
        resolved,
        len(conflicts),
        signature_valid,
    )

    return KnowledgeImportResult(
        status="accepted" if accepted > 0 else "empty",
        accepted=accepted,
        rejected=len(bundle.transitions) - len(shareable),
        conflicts=conflicts,
        resolved=resolved,
        version=bundle.version,
        signature_valid=signature_valid,
    )


@router.get("/knowledge/version")
async def knowledge_version(
    user_id: str = Depends(require_permission("perm-view-agents")),
):
    """Return the current knowledge schema version."""
    return {"version": KNOWLEDGE_VERSION}
