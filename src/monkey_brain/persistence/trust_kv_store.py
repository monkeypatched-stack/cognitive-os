"""TrustKVStore — JSON file-backed trust storage replacing Neo4j.

Stores trust edges as a JSON file with the structure:
{
    "edges": {
        "src->dst": {
            "src": str,
            "dst": str,
            "relationship": str,
            "permissions": list[str],
            "trust_score": float,
            "knowledge_scope": list[str],
            "policy_scope": list[str],
            "expiration": float,
            "reputation": float,
            "delegation_chain": list[str],
            "revoked": bool,
            "revoked_at": float,
            "revoked_by": str,
            "updated_at": float
        }
    }
}

Thread-safe: all mutations are guarded by a lock.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("agentos.trust_kv_store")


class TrustKVStore:
    """JSON file-backed trust storage.

    Replaces Neo4j trust persistence with a simple key-value store.
    Thread-safe: all mutations are guarded by a lock.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path or os.getenv("TRUST_STORE_PATH", "data/trust.json"))
        self._edges: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Load from disk if not yet loaded. Thread-safe via lock."""
        with self._lock:
            if not self._loaded:
                self._load()

    def _load(self) -> None:
        """Load trust edges from JSON file."""
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                self._edges = data.get("edges", {})
                self._loaded = True
                logger.info(
                    "TrustKVStore: loaded %d edges from %s",
                    len(self._edges),
                    self._path,
                )
            except Exception as exc:
                logger.warning("TrustKVStore: load failed: %s", exc)
                self._edges = {}
                self._loaded = True
        else:
            self._edges = {}
            self._loaded = True

    def _save(self) -> None:
        """Save trust edges to JSON file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {"edges": self._edges}
        tmp = self._path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2, default=str))
            tmp.replace(self._path)
        except Exception as exc:
            logger.warning("TrustKVStore: save failed: %s", exc)
            if tmp.exists():
                tmp.unlink()

    def _edge_key(self, src: str, dst: str) -> str:
        """Generate a unique key for a trust edge."""
        return f"{src}->{dst}"

    # ── Read operations ──────────────────────────────────────────────────

    def load_edges(self) -> list[dict[str, Any]]:
        """Load all non-revoked trust edges."""
        self._ensure_loaded()
        with self._lock:
            return [dict(edge) for edge in self._edges.values() if not edge.get("revoked", False)]

    def get_edge(self, src: str, dst: str) -> dict[str, Any] | None:
        """Get a specific trust edge."""
        self._ensure_loaded()
        with self._lock:
            key = self._edge_key(src, dst)
            edge = self._edges.get(key)
            if edge and not edge.get("revoked", False):
                return dict(edge)
            return None

    def get_edges_from(self, src: str) -> list[dict[str, Any]]:
        """Get all trust edges from a source."""
        self._ensure_loaded()
        with self._lock:
            return [
                dict(edge)
                for key, edge in self._edges.items()
                if edge.get("src") == src and not edge.get("revoked", False)
            ]

    def get_edges_to(self, dst: str) -> list[dict[str, Any]]:
        """Get all trust edges to a destination."""
        self._ensure_loaded()
        with self._lock:
            return [
                dict(edge)
                for key, edge in self._edges.items()
                if edge.get("dst") == dst and not edge.get("revoked", False)
            ]

    # ── Write operations ─────────────────────────────────────────────────

    def save_edge(
        self,
        src: str,
        dst: str,
        relationship: str = "colleague",
        permissions: list[str] | None = None,
        trust_score: float = 0.5,
        knowledge_scope: list[str] | None = None,
        policy_scope: list[str] | None = None,
        expiration: float = 0.0,
        reputation: float = 0.5,
        delegation_chain: list[str] | None = None,
    ) -> None:
        """Save a trust edge."""
        self._ensure_loaded()
        key = self._edge_key(src, dst)
        edge = {
            "src": src,
            "dst": dst,
            "relationship": relationship,
            "permissions": permissions or [],
            "trust_score": trust_score,
            "knowledge_scope": knowledge_scope or [],
            "policy_scope": policy_scope or [],
            "expiration": expiration,
            "reputation": reputation,
            "delegation_chain": delegation_chain or [],
            "revoked": False,
            "revoked_at": 0.0,
            "revoked_by": "",
            "updated_at": time.time(),
        }
        with self._lock:
            self._edges[key] = edge
        self._save()
        logger.info("TrustKVStore: saved edge %s -> %s", src, dst)

    def revoke_edge(self, src: str, dst: str, revoked_by: str = "") -> None:
        """Mark a trust edge as revoked."""
        self._ensure_loaded()
        key = self._edge_key(src, dst)
        with self._lock:
            if key in self._edges:
                self._edges[key]["revoked"] = True
                self._edges[key]["revoked_at"] = time.time()
                self._edges[key]["revoked_by"] = revoked_by
                self._edges[key]["updated_at"] = time.time()
        self._save()
        logger.info("TrustKVStore: revoked edge %s -> %s", src, dst)

    def update_reputation(self, src: str, dst: str, delta: float) -> None:
        """Update reputation for a trust edge."""
        self._ensure_loaded()
        key = self._edge_key(src, dst)
        with self._lock:
            if key in self._edges:
                current = self._edges[key].get("reputation", 0.5)
                self._edges[key]["reputation"] = max(0.0, min(1.0, current + delta))
                self._edges[key]["updated_at"] = time.time()
        self._save()

    # ── Compatibility with GraphStore interface ───────────────────────────

    async def save_trust_edge(self, **kwargs) -> None:
        """Async compatibility wrapper for GraphStore interface."""
        self.save_edge(**kwargs)

    async def revoke_trust_edge(self, src: str, dst: str, revoked_by: str = "") -> None:
        """Async compatibility wrapper for GraphStore interface."""
        self.revoke_edge(src, dst, revoked_by)

    async def load_trust_edges(self) -> list[dict[str, Any]]:
        """Async compatibility wrapper for GraphStore interface."""
        return self.load_edges()
