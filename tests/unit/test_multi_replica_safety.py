"""Lease fence + capability dispatch dedup production safety tests."""

from __future__ import annotations

import src.monkey_brain.kernel.domains.grocery  # noqa: F401

from unittest.mock import MagicMock, patch

import pytest

from src.monkey_brain.kernel.pipeline.capability_dispatch_store import (
    complete_dispatch,
    load_cached_outcome,
    release_dispatch,
    reserve_dispatch,
)
from src.monkey_brain.kernel.pipeline.execution import Action
from src.monkey_brain.kernel.pipeline.action_executor import ActionExecutor
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
from src.monkey_brain.kernel.society.domain import (
    ActorIdentity,
    ActorProfile,
    ActorType,
)
from src.monkey_brain.persistence.actor_state_store import PersistedActorState


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._hashes: dict[str, dict[str, str]] = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    def hget(self, name: str, key: str) -> str | None:
        return self._hashes.get(name, {}).get(key)

    def hset(self, name: str, key: str, value: str) -> None:
        self._hashes.setdefault(name, {})[key] = value

    def incr(self, key: str) -> int:
        current = int(self._store.get(key, "0") or 0) + 1
        self._store[key] = str(current)
        return current

    def delete(self, key: str) -> int:
        if key in self._store:
            del self._store[key]
            return 1
        return 0


class _FakeActorStateStore:
    def __init__(self) -> None:
        self._docs: dict[tuple[str, str], PersistedActorState] = {}
        self.save_calls = 0

    def load(self, actor_id: str, tenant_id: str):
        return self._docs.get((tenant_id, actor_id))

    def save(self, state: PersistedActorState) -> None:
        self.save_calls += 1
        self._docs[(state.tenant_id, state.actor_id)] = state


class TestCapabilityDispatchStore:
    def test_reserve_complete_and_replay(self, monkeypatch):
        fake = _FakeRedis()
        monkeypatch.setattr(
            "src.monkey_brain.kernel.pipeline.capability_dispatch_store._get_client",
            lambda: fake,
        )
        assert reserve_dispatch("exec-1", "act-1") == "fresh"
        complete_dispatch(
            "exec-1",
            "act-1",
            {
                "action_id": "act-1",
                "success": True,
                "result": {"charged": True},
                "error": "",
                "latency_ms": 1.0,
            },
        )
        assert reserve_dispatch("exec-1", "act-1") == "cached"
        cached = load_cached_outcome("exec-1", "act-1")
        assert cached is not None
        assert cached["result"] == {"charged": True}

    def test_release_allows_retry_after_failure(self, monkeypatch):
        fake = _FakeRedis()
        monkeypatch.setattr(
            "src.monkey_brain.kernel.pipeline.capability_dispatch_store._get_client",
            lambda: fake,
        )
        assert reserve_dispatch("exec-2", "act-2") == "fresh"
        assert release_dispatch("exec-2", "act-2") is True
        assert reserve_dispatch("exec-2", "act-2") == "fresh"


class TestLeaseFenceCheckpoint:
    def test_checkpoint_skipped_when_fence_superseded(self):
        pr = PlanetaryRuntime()
        state = pr.register_actor(ActorProfile(identity=ActorIdentity(name="FenceTest", actor_type=ActorType.HUMAN)))
        sr = pr._home_society_runtime(state.actor_id)
        registry_state = sr.get_actor(state.actor_id)
        registry_state.last_lease_fence = 1

        fake_redis = MagicMock()
        fake_redis.get.return_value = "2"
        pr._redis = fake_redis

        store = _FakeActorStateStore()
        with patch.object(pr, "_get_actor_state_store", return_value=store):
            pr.checkpoint_actor_belief(state.actor_id)

        assert store.save_calls == 0


class TestObserveActorReconcileLease:
    def test_own_reconcile_lease_does_not_clear_staleness(self):
        import json
        import time as time_module

        import src.monkey_brain.kernel.domains.grocery  # noqa: F401

        redis = _FakeRedis()
        pr = PlanetaryRuntime()
        pr._redis = redis
        pr._node_id = "node-a"
        state = pr.register_actor(ActorProfile(identity=ActorIdentity(name="Stale", actor_type=ActorType.HUMAN)))
        aid = state.actor_id
        pr.lifecycle.reconcile(aid)

        raw = json.loads(redis.hget(pr._ACTORS_HASH_KEY, aid))
        raw["updated_at"] = time_module.time() - (pr._ACTOR_STALE_SECONDS + 100)
        redis.hset(pr._ACTORS_HASH_KEY, aid, json.dumps(raw))

        token = "node-a:1:reconcile-token"
        redis.set(f"monkeybrain:actor:lease:{aid}", token)

        observed = pr.observe_actor(aid)
        assert observed.is_stale is False

        observed_reconcile = pr.observe_actor(aid, reconcile_lease_token=token)
        assert observed_reconcile.is_stale is True


class TestActionExecutorDispatchDedup:
    @pytest.mark.asyncio
    async def test_replays_cached_outcome_without_second_invoke(self, monkeypatch):
        monkeypatch.setenv("CAPABILITY_DISPATCH_DEDUP", "true")
        fake = _FakeRedis()
        monkeypatch.setattr(
            "src.monkey_brain.kernel.pipeline.capability_dispatch_store._get_client",
            lambda: fake,
        )
        complete_dispatch(
            "exec-3",
            "act-3",
            {
                "action_id": "act-3",
                "success": True,
                "result": {"from_cache": True},
                "error": "",
                "latency_ms": 0.5,
            },
        )

        bus = MagicMock()
        bus.discover.return_value = MagicMock(handle=lambda _args: {"success": True, "from_live": True})
        executor = ActionExecutor(capability_bus=bus)
        action = Action(action_id="act-3", capability="TestCap", correlation_id="exec-3")
        outcome = await executor._execute_action(action, {"_execution_id": "exec-3"})

        assert outcome.success is True
        assert outcome.result == {"from_cache": True}
        bus.discover.assert_not_called()
