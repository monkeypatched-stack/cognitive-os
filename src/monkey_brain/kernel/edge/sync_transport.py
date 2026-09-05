"""Explicit transport boundary for edge <-> control-plane synchronization
(Section 2 of the edge gap-closure pass).

This does NOT replace kernel/edge/sync.py::EdgeSyncClient or its
ControlPlaneSyncSource Protocol -- EdgeSyncClient's own idempotency
(_should_apply's epoch comparison), reconciliation, and revocation
application logic are untouched. This module only makes explicit WHAT
MOVES BYTES for a ControlPlaneSyncSource implementation:

    SyncTransport (Protocol)
        |-- InProcessSyncTransport   -- direct Python calls (tests, single-process deploys)
        \\-- NetworkSyncTransport     -- real authenticated HTTP, with retries/timeouts

TransportSyncSource adapts either transport into the EXISTING
ControlPlaneSyncSource Protocol EdgeSyncClient already consumes, so
EdgeSyncClient itself requires zero changes to use a real network
transport.

Identity: NetworkSyncTransport takes an already-configured httpx.Client
(dependency injection) rather than performing its own identity
resolution -- see build_mtls_httpx_client_from_workload_identity() below,
which is the ONE place SPIFFE/workload identity (kernel/workload_identity.py)
is used to build that client. No second identity system is introduced.

Honesty: NetworkSyncTransport's retry/timeout/error-typing logic is real
and independently testable against a fake HTTP server (see
tests/unit/test_edge_network_sync_transport.py), but this environment has
no live control-plane sync endpoint to validate against end-to-end --
that requires a real deployment (Section 11's "requires environment-
specific validation").
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

from src.monkey_brain.kernel.edge.policy_cache import SignedPolicySnapshot

logger = logging.getLogger("agentos.edge.sync_transport")


class SyncTransportError(RuntimeError):
    """Base for every transport-level failure -- callers (EdgeSyncClient
    call sites) catch this to mean "treat as NETWORK_PARTITION / stay on
    cached state" (kernel/edge/failure_modes.py), never as "no updates.\""""


class SyncTransportAuthenticationError(SyncTransportError):
    """The control plane rejected this node's credentials. Never treated
    as "no data available" -- an edge node must not keep silently retrying
    forever on a credential problem without this being surfaced/escalated
    (Section 18: authentication failure classifies as ESCALATE, not
    LOCAL_DEGRADE, since continuing to trust stale cached authority
    indefinitely because auth is broken is exactly the failure mode
    Section 12's final invariant forbids)."""


class SyncTransportMalformedResponseError(SyncTransportError):
    """The control plane's response could not be parsed into the expected
    shape. Section 3's own invariant -- "the edge must never interpret an
    unverified remote update as authoritative" -- means a malformed
    response is DISCARDED, never partially applied."""


class SyncTransportUnavailableError(SyncTransportError):
    """All retries exhausted (timeout, connection refused, DNS failure) --
    the transport-level classification of a network partition."""


class SyncTransport(Protocol):
    """The raw byte/JSON-moving contract. Deliberately untyped-JSON in,
    untyped-JSON out -- TransportSyncSource below is what turns this into
    the SignedPolicySnapshot/dict shapes EdgeSyncClient actually
    consumes; a transport itself does not know about policy semantics."""

    def get_epoch(self) -> int: ...

    def get_policy_snapshots(self, *, since_epoch: int) -> list[dict[str, Any]]: ...

    def get_world_projection(self, *, keys: tuple[str, ...]) -> dict[str, Any]: ...

    def get_revocations(self, *, since_epoch: int) -> list[dict[str, Any]]: ...

    def acknowledge(self, *, stream: str, epoch: int) -> None: ...


@dataclass
class InProcessSyncTransport:
    """Direct in-process calls against an object exposing the same four
    get_* methods plus acknowledge -- the existing test/single-process
    path (what ControlPlaneSyncSource implementations used exclusively
    before this module existed). No serialization, no network, no
    retries needed -- a same-process call either succeeds or raises like
    any other Python call."""

    backend: Any

    def get_epoch(self) -> int:
        return self.backend.get_epoch()

    def get_policy_snapshots(self, *, since_epoch: int) -> list[dict[str, Any]]:
        return self.backend.get_policy_snapshots(since_epoch=since_epoch)

    def get_world_projection(self, *, keys: tuple[str, ...]) -> dict[str, Any]:
        return self.backend.get_world_projection(keys=keys)

    def get_revocations(self, *, since_epoch: int) -> list[dict[str, Any]]:
        return self.backend.get_revocations(since_epoch=since_epoch)

    def acknowledge(self, *, stream: str, epoch: int) -> None:
        self.backend.acknowledge(stream=stream, epoch=epoch)


class NetworkSyncTransport:
    """Real authenticated HTTP transport. Takes an already-configured
    httpx.Client (see build_mtls_httpx_client_from_workload_identity) so
    this class owns none of the identity/mTLS setup itself -- only
    request execution, retries, timeouts, and response validation.
    """

    def __init__(
        self, client: Any, base_url: str, *,
        max_retries: int = 3, backoff_seconds: float = 0.5, timeout_seconds: float = 10.0,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._timeout_seconds = timeout_seconds

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        import httpx

        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                response = self._client.get(
                    f"{self._base_url}{path}", params=params, timeout=self._timeout_seconds,
                )
            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
                last_exc = exc
                if attempt < self._max_retries - 1:
                    time.sleep(self._backoff_seconds * (2 ** attempt))
                continue

            if response.status_code in (401, 403):
                raise SyncTransportAuthenticationError(
                    f"control plane rejected credentials: HTTP {response.status_code}",
                )
            if response.status_code >= 500:
                last_exc = SyncTransportError(f"control plane error: HTTP {response.status_code}")
                if attempt < self._max_retries - 1:
                    time.sleep(self._backoff_seconds * (2 ** attempt))
                continue
            if response.status_code >= 400:
                raise SyncTransportMalformedResponseError(f"HTTP {response.status_code}: {response.text[:200]}")

            try:
                return response.json()
            except Exception as exc:
                raise SyncTransportMalformedResponseError(f"non-JSON response: {exc}") from exc

        raise SyncTransportUnavailableError(
            f"exhausted {self._max_retries} attempts against {self._base_url}{path}: {last_exc}",
        )

    def get_epoch(self) -> int:
        data = self._get("/edge-sync/epoch")
        if not isinstance(data, dict) or "epoch" not in data:
            raise SyncTransportMalformedResponseError(f"epoch response missing 'epoch' field: {data!r}")
        try:
            return int(data["epoch"])
        except (TypeError, ValueError) as exc:
            raise SyncTransportMalformedResponseError(f"epoch field not an int: {data!r}") from exc

    def get_policy_snapshots(self, *, since_epoch: int) -> list[dict[str, Any]]:
        data = self._get("/edge-sync/policy-snapshots", params={"since_epoch": since_epoch})
        if not isinstance(data, list):
            raise SyncTransportMalformedResponseError(f"policy-snapshots response is not a list: {type(data)}")
        return data

    def get_world_projection(self, *, keys: tuple[str, ...]) -> dict[str, Any]:
        data = self._get("/edge-sync/world-projection", params={"keys": ",".join(keys)})
        if not isinstance(data, dict):
            raise SyncTransportMalformedResponseError(f"world-projection response is not a dict: {type(data)}")
        return data

    def get_revocations(self, *, since_epoch: int) -> list[dict[str, Any]]:
        data = self._get("/edge-sync/revocations", params={"since_epoch": since_epoch})
        if not isinstance(data, list):
            raise SyncTransportMalformedResponseError(f"revocations response is not a list: {type(data)}")
        return data

    def acknowledge(self, *, stream: str, epoch: int) -> None:
        try:
            self._client.post(
                f"{self._base_url}/edge-sync/ack", json={"stream": stream, "epoch": epoch},
                timeout=self._timeout_seconds,
            )
        except Exception:
            # Best-effort, matching kernel/edge/sync.py::acknowledge_sync's
            # own existing "no-op transport" tolerance -- a failed ack
            # never blocks or fails the sync it's acknowledging.
            logger.debug("NetworkSyncTransport.acknowledge: best-effort ack failed", exc_info=True)


class TransportSyncSource:
    """Adapts a SyncTransport into the EXISTING ControlPlaneSyncSource
    Protocol (kernel/edge/sync.py) -- the ONLY new code EdgeSyncClient
    needs to consume a real network transport is constructing one of
    these; EdgeSyncClient's own class is untouched."""

    def __init__(self, transport: SyncTransport) -> None:
        self._transport = transport

    def current_epoch(self) -> int:
        return self._transport.get_epoch()

    def fetch_snapshots(self, *, since_epoch: int) -> list[SignedPolicySnapshot]:
        raw = self._transport.get_policy_snapshots(since_epoch=since_epoch)
        snapshots = []
        for item in raw:
            try:
                snapshots.append(SignedPolicySnapshot(**item))
            except TypeError as exc:
                raise SyncTransportMalformedResponseError(f"malformed policy snapshot: {item!r} ({exc})") from exc
        return snapshots

    def fetch_world_projection(self, *, keys: tuple[str, ...]) -> dict[str, dict[str, Any]]:
        return self._transport.get_world_projection(keys=keys)

    def fetch_revocations(self, *, since_epoch: int) -> list[dict[str, Any]]:
        """Not part of ControlPlaneSyncSource's original Protocol (policy
        DENY snapshots already carry revocation semantics via epoch
        supersession) -- exposed here as an explicit extension point for
        a future dedicated revocation stream, per Section 2's own list."""
        return self._transport.get_revocations(since_epoch=since_epoch)

    def acknowledge(self, *, stream: str, epoch: int) -> None:
        self._transport.acknowledge(stream=stream, epoch=epoch)


async def build_mtls_httpx_client_from_workload_identity(*, verify: str | bool = True) -> Any:
    """The ONE integration point with the existing SPIFFE/workload
    identity architecture (kernel/workload_identity.py) -- reused, not
    reinvented. Resolves this node's real X.509 SVID and returns an
    httpx.Client configured for mTLS with it.

    Honesty: get_x509_svid()/get_current_identity() require a running
    SPIFFE Workload API (SPIRE agent socket) to return real material --
    in any environment without one (this development environment
    included) this raises WorkloadIdentityError, which callers must treat
    as "cannot build a real network transport right now," never papered
    over with a fake identity.
    """
    import ssl
    import tempfile
    import os

    from src.monkey_brain.kernel.workload_identity import get_workload_identity_provider, WorkloadIdentityError

    svid = await get_workload_identity_provider().get_x509_svid()
    if svid is None:
        raise WorkloadIdentityError("no X.509 SVID available -- is a SPIRE agent running?")

    cert_pem = svid.get("cert_pem") or svid.get("certificate")
    key_pem = svid.get("key_pem") or svid.get("private_key")
    if not cert_pem or not key_pem:
        raise WorkloadIdentityError("X.509 SVID response missing cert/key material")

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    cert_fd, cert_path = tempfile.mkstemp(suffix=".pem")
    key_fd, key_path = tempfile.mkstemp(suffix=".pem")
    try:
        os.write(cert_fd, cert_pem.encode() if isinstance(cert_pem, str) else cert_pem)
        os.write(key_fd, key_pem.encode() if isinstance(key_pem, str) else key_pem)
        os.close(cert_fd)
        os.close(key_fd)
        os.chmod(key_path, 0o600)
        ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
    finally:
        os.unlink(cert_path)
        os.unlink(key_path)

    trust_bundle = await get_workload_identity_provider().get_trust_bundle()
    if trust_bundle:
        ctx.load_verify_locations(cadata=trust_bundle)
    elif isinstance(verify, str):
        ctx.load_verify_locations(cafile=verify)

    import httpx
    return httpx.Client(verify=ctx)
