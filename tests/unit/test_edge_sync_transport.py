"""SyncTransport abstraction (kernel/edge/sync_transport.py) -- proves the
transport boundary is explicit (InProcessSyncTransport vs
NetworkSyncTransport), that NetworkSyncTransport's retry/timeout/auth/
malformed-response handling is real (exercised against httpx.MockTransport,
not a live server), and that EdgeSyncClient needs ZERO changes to consume
either transport via TransportSyncSource."""
from __future__ import annotations

import json

import httpx
import pytest

from src.monkey_brain.kernel.edge.local_store import EdgeLocalStore
from src.monkey_brain.kernel.edge.policy_cache import EdgePolicyCache, issue_policy_snapshot
from src.monkey_brain.kernel.edge.sync import EdgeSyncClient
from src.monkey_brain.kernel.edge.sync_transport import (
    InProcessSyncTransport,
    NetworkSyncTransport,
    SyncTransportAuthenticationError,
    SyncTransportMalformedResponseError,
    SyncTransportUnavailableError,
    TransportSyncSource,
)


class _FakeBackend:
    def get_epoch(self) -> int:
        return 5

    def get_policy_snapshots(self, *, since_epoch: int) -> list[dict]:
        if since_epoch >= 5:
            return []
        import time

        return [{
            "principal": "p1", "action": "a", "resource": "r",
            "approval_mode": "AUTO_APPROVE", "issued_at": time.time(), "expires_at": time.time() + 300,
        }]

    def get_world_projection(self, *, keys: tuple[str, ...]) -> dict:
        return {k: {"value": 1, "version": "v1"} for k in keys}

    def get_revocations(self, *, since_epoch: int) -> list[dict]:
        return []

    def acknowledge(self, *, stream: str, epoch: int) -> None:
        self.last_ack = (stream, epoch)


class TestInProcessSyncTransport:
    def test_delegates_every_method_to_the_backend(self):
        backend = _FakeBackend()
        transport = InProcessSyncTransport(backend)
        assert transport.get_epoch() == 5
        assert transport.get_policy_snapshots(since_epoch=0)[0]["principal"] == "p1"
        assert transport.get_world_projection(keys=("k1",))["k1"]["value"] == 1
        assert transport.get_revocations(since_epoch=0) == []
        transport.acknowledge(stream="policy", epoch=5)
        assert backend.last_ack == ("policy", 5)


def _mock_transport_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestNetworkSyncTransportHappyPath:
    def test_get_epoch_round_trips(self):
        def handler(request):
            return httpx.Response(200, json={"epoch": 7})

        transport = NetworkSyncTransport(_mock_transport_client(handler), "https://control-plane.example")
        assert transport.get_epoch() == 7

    def test_get_policy_snapshots_round_trips(self):
        def handler(request):
            assert request.url.params["since_epoch"] == "3"
            return httpx.Response(200, json=[{"principal": "p1", "action": "a", "resource": "r"}])

        transport = NetworkSyncTransport(_mock_transport_client(handler), "https://control-plane.example")
        result = transport.get_policy_snapshots(since_epoch=3)
        assert result == [{"principal": "p1", "action": "a", "resource": "r"}]


class TestNetworkSyncTransportFailureHandling:
    def test_transient_connection_errors_are_retried_then_succeed(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(200, json={"epoch": 1})

        transport = NetworkSyncTransport(
            _mock_transport_client(handler), "https://control-plane.example",
            max_retries=5, backoff_seconds=0.001,
        )
        assert transport.get_epoch() == 1
        assert calls["n"] == 3

    def test_retries_exhausted_raises_unavailable(self):
        def handler(request):
            raise httpx.ConnectError("connection refused", request=request)

        transport = NetworkSyncTransport(
            _mock_transport_client(handler), "https://control-plane.example",
            max_retries=3, backoff_seconds=0.001,
        )
        with pytest.raises(SyncTransportUnavailableError):
            transport.get_epoch()

    def test_401_raises_authentication_error_without_retrying(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(401, json={"error": "unauthorized"})

        transport = NetworkSyncTransport(_mock_transport_client(handler), "https://control-plane.example", max_retries=5)
        with pytest.raises(SyncTransportAuthenticationError):
            transport.get_epoch()
        assert calls["n"] == 1, "auth failure must not be retried like a transient error"

    def test_403_raises_authentication_error(self):
        def handler(request):
            return httpx.Response(403, json={"error": "forbidden"})

        transport = NetworkSyncTransport(_mock_transport_client(handler), "https://control-plane.example")
        with pytest.raises(SyncTransportAuthenticationError):
            transport.get_epoch()

    def test_malformed_json_raises_malformed_response_error(self):
        def handler(request):
            return httpx.Response(200, content=b"not json{{{")

        transport = NetworkSyncTransport(_mock_transport_client(handler), "https://control-plane.example")
        with pytest.raises(SyncTransportMalformedResponseError):
            transport.get_epoch()

    def test_epoch_response_missing_epoch_field_is_malformed(self):
        def handler(request):
            return httpx.Response(200, json={"not_epoch": 1})

        transport = NetworkSyncTransport(_mock_transport_client(handler), "https://control-plane.example")
        with pytest.raises(SyncTransportMalformedResponseError):
            transport.get_epoch()

    def test_policy_snapshots_response_not_a_list_is_malformed(self):
        def handler(request):
            return httpx.Response(200, json={"oops": "should be a list"})

        transport = NetworkSyncTransport(_mock_transport_client(handler), "https://control-plane.example")
        with pytest.raises(SyncTransportMalformedResponseError):
            transport.get_policy_snapshots(since_epoch=0)

    def test_5xx_is_retried_then_raises_unavailable_if_never_recovers(self):
        def handler(request):
            return httpx.Response(500, json={"error": "internal"})

        transport = NetworkSyncTransport(
            _mock_transport_client(handler), "https://control-plane.example",
            max_retries=2, backoff_seconds=0.001,
        )
        with pytest.raises(SyncTransportUnavailableError):
            transport.get_epoch()

    def test_acknowledge_failure_is_best_effort_and_never_raises(self):
        def handler(request):
            raise httpx.ConnectError("down", request=request)

        transport = NetworkSyncTransport(_mock_transport_client(handler), "https://control-plane.example", max_retries=1)
        transport.acknowledge(stream="policy", epoch=1)  # must not raise


class TestTransportSyncSourceAdaptsToControlPlaneSyncSource:
    def test_fetch_snapshots_deserializes_into_real_signed_policy_snapshots(self):
        transport = InProcessSyncTransport(_FakeBackend())
        source = TransportSyncSource(transport)
        snapshots = source.fetch_snapshots(since_epoch=0)
        assert len(snapshots) == 1
        assert snapshots[0].principal == "p1"

    def test_current_epoch_delegates_to_transport(self):
        source = TransportSyncSource(InProcessSyncTransport(_FakeBackend()))
        assert source.current_epoch() == 5

    def test_edge_sync_client_needs_zero_changes_to_consume_a_transport_backed_source(self, tmp_path):
        """The actual point of this module: EdgeSyncClient (kernel/edge/
        sync.py) is constructed and used EXACTLY as before -- only the
        ControlPlaneSyncSource it's given differs."""
        store = EdgeLocalStore(str(tmp_path / "edge.db"))
        cache = EdgePolicyCache(store)
        source = TransportSyncSource(InProcessSyncTransport(_FakeBackend()))
        client = EdgeSyncClient(store, cache, source)

        result = client.sync_policy()
        assert result.applied == 1
        assert result.new_epoch == 5
