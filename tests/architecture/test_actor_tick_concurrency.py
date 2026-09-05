"""Actor Runtime review, Phase 2 (Actor state/concurrency): proves the
one concurrency invariant Section 9 of the review asked to verify --
"two simultaneous prompts" for the SAME actor_id must never both reach
real cognition.

Verified by inspection before writing this: kernel/society/runtime.py::
SocietyRuntime.tick_one_actor() acquires a per-actor Redis lease
(planetary.acquire_actor_lease, a real SET NX EX) BEFORE calling
_coordinate_actor() -> ActorRuntime.tick(), and returns None (skipping
the tick entirely) if the lease is already held. acquire_actor_lease()
itself is a plain synchronous method with no `await` inside it, so two
"concurrent" asyncio tasks calling it cannot interleave mid-acquisition
regardless of same-process or cross-process origin -- this is what makes
the guard correct for BOTH the cross-node split-brain case (already
covered by tests/unit/test_multi_replica_safety.py's lease-fence tests)
and the same-process "two simultaneous prompts for one actor" case this
file adds.

This is a genuinely different question from tests/unit/test_geography.py
::TestOccupantTicksRunConcurrently (which proves DIFFERENT actors tick
concurrently without serializing) -- this proves the SAME actor_id never
gets two concurrent cognitive executions.
"""
from __future__ import annotations

import asyncio

import pytest

from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
from src.monkey_brain.kernel.society.domain import ActorIdentity, ActorProfile, ActorType


class _FakeRedis:
    """Real SET NX EX semantics -- the exact property this guard depends on."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def incr(self, key: str) -> int:
        current = int(self._store.get(key, "0") or 0) + 1
        self._store[key] = str(current)
        return current

    def delete(self, key: str) -> int:
        return 1 if self._store.pop(key, None) is not None else 0

    def eval(self, script: str, numkeys: int, *args):
        """release_actor_lease's compare-and-delete Lua script -- only
        delete if the stored token still matches the caller's own token
        (never release a lease another acquisition already overwrote)."""
        key, expected = args[0], args[1]
        if self._store.get(key) == expected:
            del self._store[key]
            return 1
        return 0


class TestConcurrentTicksOnTheSameActorAreSerialized:
    @pytest.mark.asyncio
    async def test_two_simultaneous_ticks_for_one_actor_only_one_reaches_cognition(self, monkeypatch):
        pr = PlanetaryRuntime()
        pr._redis = _FakeRedis()
        state = pr.register_actor(
            ActorProfile(identity=ActorIdentity(name="Concurrent", actor_type=ActorType.HUMAN)),
        )
        actor_id = state.actor_id
        sr = pr._home_society_runtime(actor_id)
        registry_state = sr.get_actor(actor_id)

        entered = []

        async def _slow_tick(prompt_request=None):
            entered.append(1)
            await asyncio.sleep(0.05)
            return "ok"

        monkeypatch.setattr(registry_state.actor_runtime._cognitive_os, "tick", _slow_tick)

        results = await asyncio.gather(
            sr.tick_one_actor(actor_id), sr.tick_one_actor(actor_id),
        )

        assert len(entered) == 1, "only one of two simultaneous ticks for the same actor may reach cognition"
        assert results.count(True) == 1
        assert results.count(None) == 1

    @pytest.mark.asyncio
    async def test_lease_is_released_after_the_tick_so_the_next_one_can_proceed(self, monkeypatch):
        pr = PlanetaryRuntime()
        pr._redis = _FakeRedis()
        state = pr.register_actor(
            ActorProfile(identity=ActorIdentity(name="Sequential", actor_type=ActorType.HUMAN)),
        )
        actor_id = state.actor_id
        sr = pr._home_society_runtime(actor_id)
        registry_state = sr.get_actor(actor_id)

        entered = []

        async def _fast_tick(prompt_request=None):
            entered.append(1)
            return "ok"

        monkeypatch.setattr(registry_state.actor_runtime._cognitive_os, "tick", _fast_tick)

        first = await sr.tick_one_actor(actor_id)
        second = await sr.tick_one_actor(actor_id)

        assert first is True
        assert second is True
        assert len(entered) == 2, "a completed tick must release its lease so a later, non-overlapping tick can proceed"

    @pytest.mark.asyncio
    async def test_different_actors_are_never_serialized_against_each_other(self, monkeypatch):
        """The lease is per-actor_id -- two DIFFERENT actors ticking
        simultaneously must not contend for the same lease key."""
        pr = PlanetaryRuntime()
        pr._redis = _FakeRedis()
        state_a = pr.register_actor(
            ActorProfile(identity=ActorIdentity(name="A", actor_type=ActorType.HUMAN)),
        )
        state_b = pr.register_actor(
            ActorProfile(identity=ActorIdentity(name="B", actor_type=ActorType.HUMAN)),
        )
        sr = pr._home_society_runtime(state_a.actor_id)
        entered = []

        async def _slow_tick(prompt_request=None):
            entered.append(1)
            await asyncio.sleep(0.05)
            return "ok"

        monkeypatch.setattr(sr.get_actor(state_a.actor_id).actor_runtime._cognitive_os, "tick", _slow_tick)
        monkeypatch.setattr(sr.get_actor(state_b.actor_id).actor_runtime._cognitive_os, "tick", _slow_tick)

        results = await asyncio.gather(
            sr.tick_one_actor(state_a.actor_id), sr.tick_one_actor(state_b.actor_id),
        )

        assert len(entered) == 2
        assert results == [True, True]
