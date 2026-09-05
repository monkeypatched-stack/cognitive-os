"""Actor Runtime review, Phase 3 (lifecycle/migration/recovery): proves
"a stale runtime cannot safely become the authoritative actor" (invariant
#13) -- specifically, that the OLD node's own SocietyRuntime genuinely
refuses to run cognition for an actor it has suspended for migration, not
merely that its registry record reports a stale status while still being
callable.

tests/scenarios/test_actor_runtime_artifact.py::test_03 already proves
identity/status/location survive a real migrate_actor() + reconcile()
round trip. This file does NOT reuse that fixture: while implementing
this test, `pr_cloud.lifecycle.reconcile(aid).action == "start"` was
found to fail with action="skipped_lease_held" in the full pytest
context for that file's specific node-registration/scheduler fixture
combination (test_03 itself fails the SAME way, in complete isolation,
confirmed via a bare reproduction script that succeeds outside pytest --
a genuine, pre-existing bug in that file's test fixtures, unrelated to
migration/lifecycle logic itself and out of this phase's scope; 13 of
that file's 23 tests currently fail this way). This file instead uses
the same minimal, already-proven-reliable PlanetaryRuntime + register_actor
pattern this review's Phase 1/2 tests already used successfully, and
sets the SUSPENDED state directly to the exact effect
ActorLifecycleController._do_suspend() produces
(state.is_active = False; state.status = ActorStatus.SUSPENDED) --
proving the real guard in tick_one_actor(), without depending on the
separately-broken reconcile fixture.
"""
from __future__ import annotations

import asyncio

import pytest

from src.monkey_brain.kernel.society.domain import ActorIdentity, ActorProfile, ActorStatus, ActorType
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime


class _FakeRedis:
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


class TestStaleRuntimeCannotContinueActingAfterSuspension:
    @pytest.mark.asyncio
    async def test_old_node_refuses_to_tick_a_suspended_migrated_actor(self):
        pr = PlanetaryRuntime()
        pr._redis = _FakeRedis()
        state = pr.register_actor(
            ActorProfile(identity=ActorIdentity(name="MigratingActor", actor_type=ActorType.HUMAN)),
        )
        aid = state.actor_id
        sr = pr._home_society_runtime(aid)
        registry_state = sr.get_actor(aid)

        entered = []

        async def _would_be_cognition(prompt_request=None):
            entered.append(1)
            return "ok"

        monkeypatch_target = registry_state.actor_runtime._cognitive_os
        monkeypatch_target.tick = _would_be_cognition

        # Confirm cognition genuinely runs BEFORE suspension -- a
        # meaningful negative control, not just an untested assumption.
        result_before = await sr.tick_one_actor(aid)
        assert result_before is True
        assert entered == [1]

        # The exact effect ActorLifecycleController._do_suspend() produces
        # (kernel/society/actor_lifecycle_controller.py:340-341) --
        # checkpoint-before-suspend already happened in the real flow;
        # this test's own concern is what happens to tick_one_actor()
        # AFTER that flip, not the checkpoint ordering itself (covered by
        # tests/unit/test_multi_replica_safety.py's lease-fence tests).
        registry_state.is_active = False
        registry_state.status = ActorStatus.SUSPENDED

        result_after = await sr.tick_one_actor(aid)

        assert result_after is None, "the old node must refuse to tick an actor it has suspended for migration"
        assert entered == [1], "no NEW cognition may run after suspension -- the list must not grow"

    @pytest.mark.asyncio
    async def test_resuming_the_same_actor_allows_ticking_again(self):
        """The guard is a real, reversible state check, not a one-way
        kill switch -- resuming (the real recovery path) must restore
        normal ticking without needing a new actor_id."""
        pr = PlanetaryRuntime()
        pr._redis = _FakeRedis()
        state = pr.register_actor(
            ActorProfile(identity=ActorIdentity(name="ResumingActor", actor_type=ActorType.HUMAN)),
        )
        aid = state.actor_id
        sr = pr._home_society_runtime(aid)
        registry_state = sr.get_actor(aid)

        entered = []

        async def _would_be_cognition(prompt_request=None):
            entered.append(1)
            return "ok"

        registry_state.actor_runtime._cognitive_os.tick = _would_be_cognition

        registry_state.is_active = False
        registry_state.status = ActorStatus.SUSPENDED
        assert await sr.tick_one_actor(aid) is None

        registry_state.is_active = True
        registry_state.status = ActorStatus.ACTIVE

        result = await sr.tick_one_actor(aid)

        assert result is True
        assert entered == [1], "resume ticks the SAME actor identity again, never a new one"
