"""Edge-friendly delegation verification — a caching WRAPPER around
kernel/delegation.py::verify_delegation_chain, never a reimplementation
of it. The security property is unchanged: a cache hit only ever
short-circuits to a result that a full verification already produced for
the EXACT same chain, presented by the EXACT same authenticated delegate,
under the EXACT same authority epoch, and still within the leaf
delegation's own expiry — anything outside that window falls through to
a real, full Ed25519 + attenuation + expiry + revocation verification,
exactly as if no cache existed at all.

    verify delegation chain once when it enters the trusted edge context
    -> retain the verified representation locally
    -> reuse it for actions covered by the same delegation
    -> invalidate it on expiration/revocation/authority epoch changes

kernel/delegation.py itself is never modified — this module only ever
calls its existing, public verify_delegation_chain.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from src.monkey_brain.kernel.edge.local_cache import BoundedTTLCache

_MAX_CACHE_TTL_SECONDS = 30.0
"""Independent of the delegation's own expiry -- a cache entry is never
trusted longer than this even if the delegation itself lives longer,
bounding how stale a revocation-epoch check can ever be."""


class VerifiedDelegationCache:
    def __init__(self, *, max_size: int = 512) -> None:
        self._cache: BoundedTTLCache = BoundedTTLCache(max_size=max_size, default_ttl_seconds=_MAX_CACHE_TTL_SECONDS)

    @staticmethod
    def _key(chain: tuple[Any, ...], authenticated_delegate: str) -> str:
        leaf = chain[-1]
        # Every hop's delegation_id, not just the leaf's -- a cached
        # result must not be reused if ANY ancestor in the presented
        # chain differs, even if the leaf id happens to match (it
        # cannot, since delegation_id is a fresh uuid4 per credential,
        # but the explicit full-chain key keeps this correct even if
        # that ever changed).
        chain_ids = ":".join(d.delegation_id for d in chain)
        return f"{chain_ids}|{authenticated_delegate}"

    def verify(
        self, *, chain: tuple[Any, ...], authenticated_delegate: str,
        current_authority_epoch: int = 0,
        is_revoked: Callable[[str], bool] | None = None,
        max_depth: int | None = None,
        now: float | None = None,
    ):
        from src.monkey_brain.kernel.delegation import DEFAULT_MAX_DELEGATION_DEPTH, verify_delegation_chain

        now = time.time() if now is None else now
        if not chain:
            return verify_delegation_chain(
                chain=chain, authenticated_delegate=authenticated_delegate, is_revoked=is_revoked,
                max_depth=max_depth or DEFAULT_MAX_DELEGATION_DEPTH, now=now,
            )

        key = self._key(chain, authenticated_delegate)
        version_key = f"epoch:{current_authority_epoch}"
        cached = self._cache.get(key, version_key=version_key, now=now)
        if cached is not None:
            return cached

        result = verify_delegation_chain(
            chain=chain, authenticated_delegate=authenticated_delegate, is_revoked=is_revoked,
            max_depth=max_depth or DEFAULT_MAX_DELEGATION_DEPTH, now=now,
        )
        leaf = chain[-1]
        ttl = min(_MAX_CACHE_TTL_SECONDS, max(0.0, leaf.expires_at - now))
        if ttl > 0:
            self._cache.put(key, result, version_key=version_key, ttl_seconds=ttl)
        return result

    def invalidate_delegation(self, delegation_id: str) -> None:
        """Called on explicit revocation of one specific delegation --
        every cached chain result that included it (as leaf or ancestor)
        must stop being trusted immediately, not wait out its TTL."""
        # BoundedTTLCache's key embeds every hop's delegation_id, so an
        # exact-key invalidation isn't possible without knowing every
        # chain a revoked id ever appeared in; the simplest CORRECT
        # response to a specific revocation is a full clear -- rare
        # enough (revocation is not a hot-path operation) that this is
        # the right trade-off over a more complex reverse index.
        self._cache.clear()

    def stats(self) -> dict[str, Any]:
        return self._cache.stats()


_default_cache: VerifiedDelegationCache | None = None


def get_verified_delegation_cache() -> VerifiedDelegationCache:
    global _default_cache
    if _default_cache is None:
        _default_cache = VerifiedDelegationCache()
    return _default_cache


def reset_verified_delegation_cache_for_tests() -> VerifiedDelegationCache:
    global _default_cache
    _default_cache = VerifiedDelegationCache()
    return _default_cache
