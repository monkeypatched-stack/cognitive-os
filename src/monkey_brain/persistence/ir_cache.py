"""IntentIR and GoalIR LRU Cache — Phase 1.5 Performance Optimization.

Phase 4: Improved LRU eviction + TTL support.

Caches compiled Intent/Goal IR to avoid reparsing.
Reduces latency by avoiding repeated compilation for identical requests.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict
from typing import Any, NamedTuple

logger = logging.getLogger("agentos.ir_cache")


class CacheEntry(NamedTuple):
    """Cache entry with value and timestamp."""

    value: Any
    timestamp: float


class IRCache:
    """LRU cache for Intent/Goal IR objects with TTL support."""

    def __init__(self, maxsize: int = 500, ttl_seconds: int = 3600):
        """Initialize IR cache with proper LRU eviction.

        Args:
            maxsize: Maximum number of cached IR objects
            ttl_seconds: Time-to-live for cache entries (3600s = 1 hour default)
        """
        self._maxsize = maxsize
        self._ttl = ttl_seconds
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    @staticmethod
    def _hash_input(question: str, domain: str = "") -> str:
        """Generate hash for caching key.

        Args:
            question: User question/query
            domain: Optional domain hint

        Returns:
            Hex hash of (question, domain)
        """
        key = f"{question}:{domain}"
        return hashlib.md5(key.encode()).hexdigest()

    def get_intent_ir(self, question: str, domain: str = "") -> Any | None:
        """Get cached IntentIR if available.

        Args:
            question: User question
            domain: Domain hint

        Returns:
            Cached IntentIR or None
        """
        cache_key = self._hash_input(question, domain)

        if cache_key in self._cache:
            entry = self._cache[cache_key]
            # Check if entry expired
            if time.time() - entry.timestamp > self._ttl:
                del self._cache[cache_key]
                self._misses += 1
                logger.debug("[ir_cache] IntentIR expired: %s", cache_key[:8])
                return None

            # Move to end for LRU ordering
            self._cache.move_to_end(cache_key)
            self._hits += 1
            logger.debug(
                "[ir_cache] IntentIR hit: %s (hits=%d, ratio=%.1f%%)",
                cache_key[:8],
                self._hits,
                (100 * self._hits / (self._hits + self._misses) if (self._hits + self._misses) > 0 else 0),
            )
            return entry.value

        self._misses += 1
        return None

    def set_intent_ir(self, question: str, domain: str, intent_ir: Any) -> None:
        """Cache IntentIR with LRU eviction.

        Args:
            question: User question
            domain: Domain hint
            intent_ir: Compiled IntentIR object
        """
        cache_key = self._hash_input(question, domain)

        # If key already exists, remove it (will be re-added at end)
        if cache_key in self._cache:
            del self._cache[cache_key]

        # Enforce size limit: evict least recently used (first item)
        if len(self._cache) >= self._maxsize:
            lru_key = next(iter(self._cache))
            del self._cache[lru_key]
            self._evictions += 1
            logger.debug(
                "[ir_cache] LRU eviction: %s (evictions=%d)",
                lru_key[:8],
                self._evictions,
            )

        # Store with timestamp for TTL checking
        self._cache[cache_key] = CacheEntry(value=intent_ir, timestamp=time.time())
        logger.debug(
            "[ir_cache] Cached IntentIR: %s (size=%d/%d)",
            cache_key[:8],
            len(self._cache),
            self._maxsize,
        )

    def get_goal_ir(self, question: str, intent_type: str, domain: str = "") -> Any | None:
        """Get cached GoalIR if available.

        Args:
            question: User question
            intent_type: Intent type
            domain: Domain

        Returns:
            Cached GoalIR or None
        """
        cache_key = self._hash_input(f"{intent_type}:{question}", domain)

        if cache_key in self._cache:
            entry = self._cache[cache_key]
            # Check if entry expired
            if time.time() - entry.timestamp > self._ttl:
                del self._cache[cache_key]
                self._misses += 1
                return None

            # Move to end for LRU ordering
            self._cache.move_to_end(cache_key)
            self._hits += 1
            return entry.value

        self._misses += 1
        return None

    def set_goal_ir(self, question: str, intent_type: str, domain: str, goal_ir: Any) -> None:
        """Cache GoalIR with LRU eviction.

        Args:
            question: User question
            intent_type: Intent type
            domain: Domain
            goal_ir: Compiled GoalIR object
        """
        cache_key = self._hash_input(f"{intent_type}:{question}", domain)

        # If key already exists, remove it (will be re-added at end)
        if cache_key in self._cache:
            del self._cache[cache_key]

        # Enforce size limit: evict least recently used (first item)
        if len(self._cache) >= self._maxsize:
            lru_key = next(iter(self._cache))
            del self._cache[lru_key]
            self._evictions += 1
            logger.debug(
                "[ir_cache] LRU eviction: %s (evictions=%d)",
                lru_key[:8],
                self._evictions,
            )

        # Store with timestamp for TTL checking
        self._cache[cache_key] = CacheEntry(value=goal_ir, timestamp=time.time())
        logger.debug(
            "[ir_cache] Cached GoalIR: %s (size=%d/%d)",
            cache_key[:8],
            len(self._cache),
            self._maxsize,
        )

    def clear(self) -> None:
        """Clear entire cache."""
        self._cache.clear()
        logger.info("[ir_cache] Cache cleared")

    def statistics(self) -> dict[str, Any]:
        """Get cache statistics including LRU and TTL info.

        Returns:
            Dict with hit/miss counts, rates, and eviction stats
        """
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0.0

        return {
            "cache_size": len(self._cache),
            "max_size": self._maxsize,
            "hits": self._hits,
            "misses": self._misses,
            "total": total,
            "hit_rate": f"{hit_rate:.1f}%",
            "evictions": self._evictions,
            "ttl_seconds": self._ttl,
        }


# Global singleton cache instance
_global_ir_cache: IRCache | None = None


def get_ir_cache() -> IRCache:
    """Get or create global IR cache singleton.

    Returns:
        Global IRCache instance
    """
    global _global_ir_cache
    if _global_ir_cache is None:
        _global_ir_cache = IRCache(maxsize=500)
        logger.info("[ir_cache] Global IR cache initialized (maxsize=500)")
    return _global_ir_cache


def reset_ir_cache() -> None:
    """Reset global IR cache (for testing)."""
    global _global_ir_cache
    if _global_ir_cache:
        _global_ir_cache.clear()
    _global_ir_cache = None
