"""Trust Persistence — saves/loads TrustNetwork to/from Neo4j via GraphStore.

Thread-safe.  On kernel boot, loads all edges from Neo4j into TrustNetwork.
On trust operations, persists changes to Neo4j.
"""

from __future__ import annotations

import logging
from typing import Any

from src.monkey_brain.kernel.compile.trust import TrustNetwork, TrustEdge, Relationship

logger = logging.getLogger("agentos.trust.persist")


class TrustPersistence:
    """Adapter between TrustNetwork and GraphStore for durable trust storage."""

    def __init__(self, graph_store: Any, trust_network: TrustNetwork) -> None:
        self._store = graph_store
        self._net = trust_network

    async def load(self) -> int:
        """Load all trust edges from Neo4j into the TrustNetwork. Returns count loaded."""
        if not self._store or not self._store.is_connected():
            return 0
        try:
            edges = await self._store.load_trust_edges()
            for e in edges:
                try:
                    rel = Relationship(e.get("relationship", "colleague"))
                    self._net.connect(
                        e["src"],
                        e["dst"],
                        rel,
                        trust=e.get("trust_score", 0.5),
                        permissions=set(e.get("permissions", [])),
                        knowledge_scope=set(e.get("knowledge_scope", [])),
                        policy_scope=set(e.get("policy_scope", [])),
                        expiration=e.get("expiration", 0.0),
                        delegation_chain=e.get("delegation_chain", []),
                    )
                    # Restore reputation
                    if e.get("reputation", 0.5) != 0.5:
                        self._net.update_reputation(e["src"], e["dst"], e["reputation"] - 0.5)
                except Exception as exc:
                    logger.warning(
                        "[trust] failed to load edge %s→%s: %s",
                        e.get("src"),
                        e.get("dst"),
                        exc,
                    )
            logger.info("[trust] loaded %d edges from Neo4j", len(edges))
            return len(edges)
        except Exception as exc:
            logger.warning("[trust] load failed: %s", exc)
            return 0

    async def save_edge(self, edge: TrustEdge) -> None:
        """Persist a trust edge to Neo4j."""
        if not self._store or not self._store.is_connected():
            return
        try:
            await self._store.save_trust_edge(
                src=edge.src,
                dst=edge.dst,
                relationship=edge.relationship.value,
                permissions=sorted(edge.permissions),
                trust_score=edge.trust_score,
                knowledge_scope=sorted(edge.knowledge_scope),
                policy_scope=sorted(edge.policy_scope),
                expiration=edge.expiration,
                reputation=edge.reputation,
                delegation_chain=edge.delegation_chain,
            )
        except Exception as exc:
            logger.warning("[trust] save failed for %s→%s: %s", edge.src, edge.dst, exc)

    async def revoke_edge(self, src: str, dst: str, revoked_by: str = "") -> None:
        """Persist a trust revocation to Neo4j."""
        if not self._store or not self._store.is_connected():
            return
        try:
            await self._store.revoke_trust_edge(src, dst, revoked_by)
        except Exception as exc:
            logger.warning("[trust] revoke failed for %s→%s: %s", src, dst, exc)
