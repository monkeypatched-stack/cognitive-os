"""Knowledge Replication — background sync of accepted knowledge across runtimes.

Periodically exports the local knowledge bundle and sends it to configured
peer runtimes.  Peer runtimes import and merge (with conflict resolution).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger("agentos.replication")


class KnowledgeReplicator:
    """Background replicator that syncs knowledge to peer runtimes.

    Uses ExchangeClient to send knowledge exports to peers.
    Configurable interval, peer list, and merge strategy.
    """

    def __init__(
        self,
        peers: list[str] | None = None,
        interval: float = 300.0,
        merge_strategy: str = "higher",
    ) -> None:
        self._peers = peers or []
        self._interval = interval
        self._merge_strategy = merge_strategy
        self._thread: threading.Thread | None = None
        self._running = False
        self._replication_count = 0
        self._last_replication: float = 0.0

    def set_peers(self, peers: list[str]) -> None:
        """Update the list of peer runtime URLs."""
        self._peers = list(peers)

    def start(self) -> None:
        """Start the background replication thread."""
        if self._running or not self._peers:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="knowledge-replicator")
        self._thread.start()
        logger.info(
            "[replication] started (peers=%d, interval=%.0fs)",
            len(self._peers),
            self._interval,
        )

    def stop(self) -> None:
        """Stop the background replication."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=self._interval * 2)
            self._thread = None
        logger.info("[replication] stopped (replicated=%d)", self._replication_count)

    def replicate_once(self) -> int:
        """Export knowledge and send to all peers. Returns number of successful replications."""
        if not self._peers:
            return 0

        # Build knowledge export
        export = self._build_export()
        if not export:
            return 0

        # Send to each peer
        from src.monkey_brain.kernel.compile.network import ExchangeClient

        client = ExchangeClient(timeout=30.0)
        success_count = 0

        for peer_url in self._peers:
            try:
                url = f"{peer_url.rstrip('/')}/api/v1/agentos/knowledge/import"
                result = client.post_json(
                    url,
                    {
                        "bundle": export,
                        "origin": export.get("runtime_id", "replication"),
                        "domain": "default",
                        "merge_strategy": self._merge_strategy,
                    },
                )
                if result.get("status") in ("accepted", "empty"):
                    success_count += 1
                    logger.debug("[replication] synced to %s: %s", peer_url, result.get("status"))
                elif result.get("status") == "error":
                    logger.debug(
                        "[replication] failed to sync to %s: %s",
                        peer_url,
                        result.get("detail"),
                    )
            except Exception as exc:
                logger.debug("[replication] failed to sync to %s: %s", peer_url, exc)

        self._replication_count += success_count
        self._last_replication = time.time()
        return success_count

    def _build_export(self) -> dict[str, Any]:
        """Build a knowledge export bundle from the current world tensor."""
        try:
            from src.monkey_brain.kernel.compile.world_tensor import get_tenant_world
            from src.monkey_brain.kernel.identity import get_identity

            tw = get_tenant_world()
            identity = get_identity()
            world = tw.view(identity.runtime_id)

            transitions = []
            for src, dst in world:
                from src.monkey_brain.kernel.compile.tensor import Feature

                transitions.append(
                    {
                        "src": src,
                        "dst": dst,
                        "domain": world.domain_of(src) or "default",
                        "reward": world.feature(src, dst, Feature.REWARD),
                    }
                )

            return {
                "version": "1.0",
                "exported_at": time.time(),
                "runtime_id": identity.runtime_id,
                "transitions": transitions,
                "q_table": {},
                "graph_nodes": [],
                "graph_edges": [],
                "metadata": {"replication": True, "source": "background_sync"},
            }
        except Exception as exc:
            logger.debug("[replication] export failed: %s", exc)
            return {}

    def _run(self) -> None:
        """Background loop."""
        while self._running:
            time.sleep(self._interval)
            if not self._running:
                break
            try:
                self.replicate_once()
            except Exception as exc:
                logger.warning("[replication] error: %s", exc)
