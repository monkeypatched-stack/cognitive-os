"""Edge performance caches: BoundedTTLCache primitive,
VerifiedDelegationCache, CachedContextConstructionEngine."""
from __future__ import annotations

import time

import pytest

from src.monkey_brain.kernel.edge.local_cache import BoundedTTLCache


class TestBoundedTTLCache:
    def test_hit_and_miss(self):
        c = BoundedTTLCache(max_size=10, default_ttl_seconds=10)
        assert c.get("k", version_key="v1") is None
        c.put("k", "value", version_key="v1")
        assert c.get("k", version_key="v1") == "value"

    def test_version_mismatch_is_a_miss_not_a_stale_hit(self):
        c = BoundedTTLCache(max_size=10, default_ttl_seconds=10)
        c.put("k", "value", version_key="v1")
        assert c.get("k", version_key="v2") is None
        # and it's evicted, not left dangling
        assert c.get("k", version_key="v1") is None

    def test_ttl_expiry(self):
        c = BoundedTTLCache(max_size=10, default_ttl_seconds=0.01)
        c.put("k", "value", version_key="v1")
        time.sleep(0.02)
        assert c.get("k", version_key="v1") is None

    def test_lru_eviction_bounds_size(self):
        c = BoundedTTLCache(max_size=2, default_ttl_seconds=10)
        c.put("a", 1, version_key="v1")
        c.put("b", 2, version_key="v1")
        c.put("c", 3, version_key="v1")
        assert c.get("a", version_key="v1") is None
        assert c.size == 2

    def test_invalidate_version_drops_only_matching_entries(self):
        c = BoundedTTLCache(max_size=10, default_ttl_seconds=10)
        c.put("a", 1, version_key="v1")
        c.put("b", 2, version_key="v2")
        removed = c.invalidate_version("v1")
        assert removed == 1
        assert c.get("a", version_key="v1") is None
        assert c.get("b", version_key="v2") == 2

    def test_stats_hit_rate(self):
        c = BoundedTTLCache(max_size=10, default_ttl_seconds=10)
        c.put("a", 1, version_key="v1")
        c.get("a", version_key="v1")
        c.get("missing", version_key="v1")
        stats = c.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5


class TestVerifiedDelegationCache:
    @pytest.fixture()
    def delegation(self):
        import io
        import contextlib

        from src.monkey_brain.kernel.delegation import issue_delegation

        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            return issue_delegation(issuer="A", delegate="C", capabilities=("grocery.purchase",))

    def test_cache_hit_returns_equivalent_result(self, delegation):
        from src.monkey_brain.kernel.edge.delegation_cache import VerifiedDelegationCache

        cache = VerifiedDelegationCache()
        r1 = cache.verify(chain=(delegation,), authenticated_delegate="C")
        r2 = cache.verify(chain=(delegation,), authenticated_delegate="C")
        assert r1.authorized is True
        assert r2.authorized is True
        assert cache.stats()["hits"] == 1

    def test_wrong_delegate_is_not_served_from_cache(self, delegation):
        from src.monkey_brain.kernel.edge.delegation_cache import VerifiedDelegationCache

        cache = VerifiedDelegationCache()
        cache.verify(chain=(delegation,), authenticated_delegate="C")
        result = cache.verify(chain=(delegation,), authenticated_delegate="mallory")
        assert result.authorized is False

    def test_epoch_change_forces_reverification(self, delegation):
        from src.monkey_brain.kernel.edge.delegation_cache import VerifiedDelegationCache

        cache = VerifiedDelegationCache()
        cache.verify(chain=(delegation,), authenticated_delegate="C", current_authority_epoch=0)
        assert cache.stats()["misses"] == 1
        cache.verify(chain=(delegation,), authenticated_delegate="C", current_authority_epoch=1)
        assert cache.stats()["misses"] == 2  # epoch bump forces a fresh verification, not a stale hit

    def test_invalidate_delegation_clears_cache(self, delegation):
        from src.monkey_brain.kernel.edge.delegation_cache import VerifiedDelegationCache

        cache = VerifiedDelegationCache()
        cache.verify(chain=(delegation,), authenticated_delegate="C")
        cache.invalidate_delegation(delegation.delegation_id)
        assert cache.stats()["size"] == 0

    def test_never_caches_past_the_delegations_own_expiry(self, delegation):
        """The delegation itself expires in ~24h by default; verify the
        cache TTL is bounded by min(cache_ttl, time_until_expiry), not
        the delegation's own long lifetime."""
        from src.monkey_brain.kernel.edge.delegation_cache import (
            VerifiedDelegationCache,
            _MAX_CACHE_TTL_SECONDS,
        )

        cache = VerifiedDelegationCache()
        cache.verify(chain=(delegation,), authenticated_delegate="C")
        key = cache._key((delegation,), "C")
        entry = cache._cache._entries[key]
        assert entry.expires_at <= time.time() + _MAX_CACHE_TTL_SECONDS + 0.5


class TestCachedContextConstructionEngine:
    @pytest.fixture()
    def wrapped(self, monkeypatch):
        monkeypatch.setenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")
        from src.monkey_brain.kernel.edge.context_cache import CachedContextConstructionEngine
        from src.monkey_brain.kernel.pipeline.planning.context_engine import ContextConstructionEngine
        from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

        engine = ContextConstructionEngine(planetary_runtime=PlanetaryRuntime())
        return CachedContextConstructionEngine(engine)

    def test_first_build_is_a_miss(self, wrapped):
        from src.monkey_brain.kernel.pipeline.belief_state import Goal

        goal = Goal(name="g", description="check inventory")
        _, hit = wrapped.build("actor-1", goal, world_state_version="v1", policy_version="p1", knowledge_version="k1")
        assert hit is False

    def test_repeated_build_with_same_versions_is_a_hit(self, wrapped):
        from src.monkey_brain.kernel.pipeline.belief_state import Goal

        goal = Goal(name="g", description="check inventory")
        wrapped.build("actor-1", goal, world_state_version="v1", policy_version="p1", knowledge_version="k1")
        _, hit = wrapped.build("actor-1", goal, world_state_version="v1", policy_version="p1", knowledge_version="k1")
        assert hit is True

    def test_world_state_version_change_forces_rebuild(self, wrapped):
        from src.monkey_brain.kernel.pipeline.belief_state import Goal

        goal = Goal(name="g", description="check inventory")
        wrapped.build("actor-1", goal, world_state_version="v1", policy_version="p1", knowledge_version="k1")
        _, hit = wrapped.build("actor-1", goal, world_state_version="v2", policy_version="p1", knowledge_version="k1")
        assert hit is False

    def test_policy_version_change_forces_rebuild(self, wrapped):
        from src.monkey_brain.kernel.pipeline.belief_state import Goal

        goal = Goal(name="g", description="check inventory")
        wrapped.build("actor-1", goal, world_state_version="v1", policy_version="p1", knowledge_version="k1")
        _, hit = wrapped.build("actor-1", goal, world_state_version="v1", policy_version="p2", knowledge_version="k1")
        assert hit is False

    def test_different_goal_is_a_different_cache_key(self, wrapped):
        from src.monkey_brain.kernel.pipeline.belief_state import Goal

        goal1 = Goal(name="g1", description="check inventory for widgets")
        goal2 = Goal(name="g2", description="check inventory for gadgets")
        wrapped.build("actor-1", goal1, world_state_version="v1", policy_version="p1", knowledge_version="k1")
        _, hit = wrapped.build("actor-1", goal2, world_state_version="v1", policy_version="p1", knowledge_version="k1")
        assert hit is False
