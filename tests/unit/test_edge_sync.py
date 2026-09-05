"""EdgeSyncClient synchronization protocol tests
(kernel/edge/sync.py): initial/incremental/idempotent/out-of-order/
reconnect/revocation propagation/epoch advancement."""
from __future__ import annotations

import pytest

from src.monkey_brain.kernel.edge.local_store import EdgeLocalStore
from src.monkey_brain.kernel.edge.policy_cache import EdgePolicyCache, issue_policy_snapshot
from src.monkey_brain.kernel.edge.sync import EdgeSyncClient, POLICY_STREAM


class FakeControlPlane:
    def __init__(self) -> None:
        self.epoch = 0
        self.snapshots = []
        self.world: dict[str, dict] = {}

    def issue(self, **kwargs) -> None:
        self.epoch += 1
        kwargs.setdefault("authority_epoch", self.epoch)
        self.snapshots.append(issue_policy_snapshot(**kwargs))

    def current_epoch(self) -> int:
        return self.epoch

    def fetch_snapshots(self, *, since_epoch: int):
        return [s for s in self.snapshots if s.authority_epoch >= since_epoch]

    def fetch_world_projection(self, *, keys: tuple[str, ...]):
        return {k: self.world[k] for k in keys if k in self.world}


@pytest.fixture()
def store(tmp_path):
    s = EdgeLocalStore(str(tmp_path / "edge.db"))
    yield s
    s.close()


@pytest.fixture()
def cache(store):
    return EdgePolicyCache(store)


@pytest.fixture()
def control_plane():
    return FakeControlPlane()


@pytest.fixture()
def client(store, cache, control_plane):
    return EdgeSyncClient(store, cache, control_plane)


class TestInitialSync:
    def test_first_sync_is_marked_full_snapshot(self, client, control_plane):
        control_plane.issue(principal="p1", action="capability.A", resource="r1", policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"})
        result = client.sync_policy()
        assert result.full_snapshot is True
        assert result.applied == 1
        assert result.new_epoch == 1

    def test_initial_sync_populates_the_local_cache(self, client, control_plane, cache):
        control_plane.issue(principal="p1", action="capability.A", resource="r1", policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"})
        client.sync_policy()
        got, _, _ = cache.get_valid(principal="p1", action="capability.A", resource="r1", authenticated_principal="p1")
        assert got is not None


class TestIncrementalSync:
    def test_second_sync_is_not_marked_full_snapshot(self, client, control_plane):
        control_plane.issue(principal="p1", action="capability.A", resource="r1", policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"})
        client.sync_policy()
        control_plane.issue(principal="p1", action="capability.B", resource="r2", policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"})
        result = client.sync_policy()
        assert result.full_snapshot is False
        assert result.new_epoch == 2


class TestIdempotentReplay:
    def test_replaying_the_same_sync_twice_is_safe(self, client, control_plane, cache):
        control_plane.issue(principal="p1", action="capability.A", resource="r1", policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"})
        client.sync_policy()
        client.sync_policy()
        client.sync_policy()
        got, _, _ = cache.get_valid(principal="p1", action="capability.A", resource="r1", authenticated_principal="p1")
        assert got is not None
        assert got.authority_epoch == 1


class TestOutOfOrderUpdateHandling:
    def test_older_epoch_snapshot_cannot_overwrite_a_newer_cached_one(self, client, control_plane, store, cache):
        control_plane.issue(principal="p1", action="capability.A", resource="r1", policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"})
        client.sync_policy()
        newer_epoch = control_plane.epoch

        from src.monkey_brain.kernel.edge.policy_cache import issue_policy_snapshot
        stale_snapshot = issue_policy_snapshot(
            principal="p1", action="capability.A", resource="r1",
            policy_decision={"allowed": False, "approval_mode": "DENY"},
            authority_epoch=newer_epoch - 1 if newer_epoch > 1 else 0,
        )
        assert client._should_apply(stale_snapshot) is False


class TestReconnectAfterPartition:
    def test_reconcile_after_partition_catches_up_from_last_known_epoch(self, client, control_plane, cache):
        control_plane.issue(principal="p1", action="capability.A", resource="r1", policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"})
        client.sync_policy()

        # Simulate a long partition: several more control-plane changes
        # happen while this node is disconnected.
        control_plane.issue(principal="p1", action="capability.B", resource="r2", policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"})
        control_plane.issue(principal="p1", action="capability.C", resource="r3", policy_decision={"allowed": False, "approval_mode": "DENY"})

        result = client.reconcile_after_partition()
        assert result.new_epoch == control_plane.epoch
        got_b, _, _ = cache.get_valid(principal="p1", action="capability.B", resource="r2", authenticated_principal="p1")
        got_c, _, _ = cache.get_valid(principal="p1", action="capability.C", resource="r3", authenticated_principal="p1")
        assert got_b is not None and got_b.approval_mode == "AUTO_APPROVE"
        # A DENY snapshot is still a confident, validly-cached decision --
        # get_valid returns it (never silently drops a DENY as if it were
        # "unknown"); it is local_governance.py's job to turn a DENY
        # snapshot into a refusal, not policy_cache's.
        assert got_c is not None and got_c.approval_mode == "DENY"


class TestRevocationPropagation:
    def test_epoch_advance_invalidates_a_previously_valid_snapshot(self, client, control_plane, cache):
        control_plane.issue(principal="p1", action="capability.A", resource="r1", policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"})
        client.sync_policy()
        got_before, fresh_before, _ = cache.get_valid(
            principal="p1", action="capability.A", resource="r1", authenticated_principal="p1",
            current_authority_epoch=client.current_local_epoch(),
        )
        assert got_before is not None

        # Revocation: control plane advances its epoch without reissuing
        # a snapshot for this exact key -- the edge node's own tracked
        # epoch must still be able to invalidate the old snapshot once it
        # learns of the new epoch (e.g. via a lightweight epoch-only sync
        # tick, modeled here directly).
        control_plane.epoch += 1
        got_after, fresh_after, reason = cache.get_valid(
            principal="p1", action="capability.A", resource="r1", authenticated_principal="p1",
            current_authority_epoch=control_plane.epoch,
        )
        assert got_after is None


class TestPolicyEpochAdvancement:
    def test_local_epoch_tracks_the_control_planes_epoch_after_sync(self, client, control_plane):
        control_plane.issue(principal="p1", action="capability.A", resource="r1", policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"})
        control_plane.issue(principal="p1", action="capability.B", resource="r2", policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"})
        client.sync_policy()
        assert client.current_local_epoch() == control_plane.epoch
