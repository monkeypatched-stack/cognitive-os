"""Network Transport — HTTP-based knowledge exchange between runtimes.

Replaces in-process-only exchange with network-capable transport.
Runtimes can now send and receive BeliefProposals over HTTP.

Architecture:
    Sender:   ExchangeClient.send_proposal() → POST /exchange/proposal
    Receiver: ExchangeServer receives → KnowledgeExchange.deliver() → MergeQueue
    Flusher:  Background task calls MergeQueue.flush() periodically
"""

# NOTE: deliberately NOT using `from __future__ import annotations`. FastAPI resolves the
# route's parameter types from real annotation objects; stringified annotations of the
# locally-defined Pydantic models fail to resolve and every request 422s.

import hmac
import json
import logging
import os
import ssl
import threading
import time
from typing import Any

import urllib.request
import urllib.error

from src.monkey_brain.kernel.compile.exchange import (
    KnowledgeExchange,
    BeliefProposal,
    MergeQueue,
    is_shareable,
)
from src.monkey_brain.kernel.compile.trust import TrustNetwork

logger = logging.getLogger("agentos.exchange.network")


def _exchange_token() -> str:
    """Shared transport bearer for runtime-to-runtime calls (AGENTOS_EXCHANGE_TOKEN).

    This authenticates the TRANSPORT (which runtime is on the wire); proposal integrity is
    still enforced by the per-proposal Ed25519 signature in the exchange pipeline. Empty =
    unauthenticated (dev only). Production must set this AND terminate TLS at the edge —
    a real deployment should upgrade this to mTLS / signed request headers.
    """
    return os.getenv("AGENTOS_EXCHANGE_TOKEN", "")


def _require_mtls() -> bool:
    return os.getenv("AGENTOS_REQUIRE_MTLS", "").lower() in ("1", "true", "yes", "on")


def _secure_mode() -> bool:
    return os.getenv("AGENTOS_SECURE_MODE", "").lower() in ("1", "true", "yes", "on")


_EXCHANGE_AUTH_DISABLED_WARNED = False


def _exchange_auth_required() -> bool:
    """Whether the exchange endpoint must reject unauthenticated proposals. Secure by
    default — mirrors api/dependencies.py::auth_required()'s exact polarity: enforcement
    is ON unless AGENTOS_EXCHANGE_AUTH_REQUIRED is explicitly set to a false value. A
    deployment that simply never configures AGENTOS_EXCHANGE_TOKEN gets a closed
    endpoint (503, "not configured"), not an open one that only logs a warning.
    """
    global _EXCHANGE_AUTH_DISABLED_WARNED
    required = os.getenv("AGENTOS_EXCHANGE_AUTH_REQUIRED", "true").strip().lower() not in (
        "false",
        "0",
        "no",
        "off",
    )
    if not required and not _EXCHANGE_AUTH_DISABLED_WARNED:
        _EXCHANGE_AUTH_DISABLED_WARNED = True
        logger.warning(
            "AGENTOS_EXCHANGE_AUTH_REQUIRED is disabled — the exchange endpoint accepts "
            "unauthenticated proposals. Never run this way outside local development.",
        )
    return required


def secure_mode_preflight(*, strict: bool | None = None) -> list:
    """Validate the deployment's security posture and return the list of issues.

    A pilot should NOT silently run open. With AGENTOS_SECURE_MODE=1 (or strict=True) an
    insecure configuration raises so boot fails fast; otherwise the issues are logged so an
    operator can see exactly what is not yet hardened.
    """
    strict = _secure_mode() if strict is None else strict
    issues: list[str] = []
    if not os.getenv("AGENTOS_CA_STORE"):
        issues.append("AGENTOS_CA_STORE unset — cross-host certificate verification disabled")
    if not _exchange_token():
        issues.append("AGENTOS_EXCHANGE_TOKEN unset — exchange transport is unauthenticated")
    if not _require_mtls():
        issues.append("AGENTOS_REQUIRE_MTLS unset — plaintext transport permitted")
    if not os.getenv("AGENTOS_KEY_PASSWORD"):
        issues.append(
            "AGENTOS_KEY_PASSWORD unset — signing keys use an auto-generated local "
            "passphrase, not a managed secret (KMS/HSM)"
        )
    for issue in issues:
        logger.warning("[secure-preflight] %s", issue)
    if strict and issues:
        raise RuntimeError("AGENTOS_SECURE_MODE set but configuration is insecure: " + "; ".join(issues))
    if not issues:
        logger.info("[secure-preflight] all checks passed — secure posture")
    return issues


def build_client_ssl_context(
    cert: str | None = None, key: str | None = None, ca: str | None = None
) -> ssl.SSLContext | None:
    """Build a mutual-TLS client context: verify the server against `ca` AND present our
    own client certificate (mTLS) so the server can authenticate this runtime. Falls back
    to env (AGENTOS_TLS_CERT / _KEY / _CA). Returns None if no TLS material is configured.
    """
    cert = cert or os.getenv("AGENTOS_TLS_CERT", "")
    key = key or os.getenv("AGENTOS_TLS_KEY", "")
    ca = ca or os.getenv("AGENTOS_TLS_CA", "")
    if not (cert and key):
        return None
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=ca or None)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_cert_chain(certfile=cert, keyfile=key)  # our client identity (mTLS)
    return ctx


def build_server_ssl_context(
    cert: str | None = None, key: str | None = None, ca: str | None = None
) -> ssl.SSLContext | None:
    """Build a server context that REQUIRES and verifies client certificates (mTLS) — pass
    to uvicorn/hypercorn at the TLS edge so only runtimes with a CA-issued client cert connect.
    """
    cert = cert or os.getenv("AGENTOS_TLS_CERT", "")
    key = key or os.getenv("AGENTOS_TLS_KEY", "")
    ca = ca or os.getenv("AGENTOS_TLS_CA", "")
    if not (cert and key):
        return None
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH, cafile=ca or None)
    ctx.verify_mode = ssl.CERT_REQUIRED  # reject clients without a valid cert
    ctx.load_cert_chain(certfile=cert, keyfile=key)
    return ctx


class ExchangeClient:
    """Sends BeliefProposals to remote runtimes via HTTP.

    Usage:
        client = ExchangeClient()
        result = await client.send_proposal(
            proposal, "http://remote-runtime:8031", recipient_tenant="tenant-b"
        )
    """

    def __init__(self, timeout: float = 30.0, ssl_context: ssl.SSLContext | None = None) -> None:
        self._timeout = timeout
        # mTLS context (explicit or from env); presents our client cert and verifies theirs.
        self._ssl = ssl_context or build_client_ssl_context()

    def post_json(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        """Send an authenticated JSON POST to a peer runtime — the same
        mTLS / plaintext-guard / bearer-token discipline send_proposal()
        applies, generalized to any endpoint/payload shape (not just
        BeliefProposal), so every peer-to-peer sender in this runtime goes
        through one real, security-checked transport instead of each
        caller building its own raw urllib request and silently skipping
        these checks (see kernel/replication.py::KnowledgeReplicator,
        which constructed an ExchangeClient for exactly this purpose but
        never called it — MB cleanup finding).
        """
        is_plaintext = (
            url.startswith("http://")
            and not url.startswith("http://127.0.0.1")
            and not url.startswith("http://localhost")
        )
        if is_plaintext:
            if _require_mtls():
                return {
                    "status": "error",
                    "reason": "mtls_required",
                    "detail": "AGENTOS_REQUIRE_MTLS set but target is plaintext http",
                }
            logger.warning(
                "[exchange] sending to PLAINTEXT http %s — use https:// in production (payload is in cleartext)",
                url,
            )
        elif _require_mtls() and self._ssl is None:
            return {
                "status": "error",
                "reason": "mtls_required",
                "detail": "AGENTOS_REQUIRE_MTLS set but no client certificate configured",
            }
        payload = json.dumps(body).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        token = _exchange_token()
        if token:
            # Never send the bearer over plaintext http (credential leak) — only over TLS or
            # to a loopback address.
            if url.startswith("https://") or "127.0.0.1" in url or "localhost" in url:
                headers["Authorization"] = f"Bearer {token}"
            else:
                logger.warning(
                    "[exchange] withholding bearer token — target %s is plaintext http",
                    url,
                )
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")

        try:
            ctx = self._ssl if url.startswith("https://") else None
            with urllib.request.urlopen(req, timeout=self._timeout, context=ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="replace")
            logger.warning("[exchange] HTTP %d from %s: %s", e.code, url, body_text[:200])
            return {
                "status": "error",
                "reason": f"http_{e.code}",
                "detail": body_text[:200],
            }
        except Exception as e:
            logger.warning("[exchange] failed to reach %s: %s", url, e)
            return {"status": "error", "reason": "connection_failed", "detail": str(e)}

    def send_proposal(
        self,
        proposal: BeliefProposal,
        target_url: str,
        recipient_tenant: str = "default",
    ) -> dict[str, Any]:
        """Send a proposal to a remote runtime's exchange endpoint.

        Returns the remote's ExchangeResult as a dict.
        """
        url = f"{target_url.rstrip('/')}/api/v1/agentos/exchange/proposal"
        return self.post_json(
            url,
            {
                "proposal": {
                    "origin": proposal.origin,
                    "kind": proposal.kind,
                    "domain": proposal.domain,
                    "transitions": [list(t) for t in proposal.transitions],
                    "signature": proposal.signature,
                    "certificate": proposal.certificate,
                },
                "recipient_tenant": recipient_tenant,
            },
        )

    def send_batch(
        self,
        proposals: list[BeliefProposal],
        target_url: str,
        recipient_tenant: str = "default",
    ) -> list[dict[str, Any]]:
        """Send multiple proposals to a remote runtime."""
        results = []
        for prop in proposals:
            result = self.send_proposal(prop, target_url, recipient_tenant)
            results.append(result)
        return results


class ExchangeServer:
    """HTTP endpoint for receiving BeliefProposals from remote runtimes.

    Mount as a FastAPI router:
        app.include_router(exchange_server.router, prefix="/api/v1/agentos")
    """

    def __init__(
        self,
        exchange: KnowledgeExchange,
        tenant_id: str = "default",
        ca: Any = None,
        cross_certs: Any = None,
    ) -> None:
        self._exchange = exchange
        self._tenant_id = tenant_id
        self._ca = ca  # when set, publishes a signed trust bundle
        self._cross = cross_certs
        self._received_count = 0
        self._accepted_count = 0
        self._rejected_count = 0

    def create_router(self):
        """Create a FastAPI router for the exchange endpoint."""
        from fastapi import APIRouter, Request
        from pydantic import BaseModel, Field

        router = APIRouter()

        class ProposalRequest(BaseModel):
            proposal: dict[str, Any]
            recipient_tenant: str = "default"

        class ProposalResponse(BaseModel):
            status: str
            reason: str = ""
            merged: int = 0
            conflicts: list = Field(default_factory=list)

        @router.post("/exchange/proposal", response_model=ProposalResponse)
        async def receive_proposal(body: ProposalRequest, request: Request):
            """Receive a BeliefProposal from a remote runtime."""
            self._received_count += 1

            # Transport auth: when a token is configured, the caller must present it
            # (constant-time compare). Secure by default when unconfigured — reject
            # rather than silently accept, matching auth_required()'s established
            # pattern; only an explicit AGENTOS_EXCHANGE_AUTH_REQUIRED=false opt-out
            # (trusted-network-only dev setups) restores the old accept-and-warn path.
            expected = _exchange_token()
            if expected:
                auth = request.headers.get("authorization", "")
                presented = auth[7:] if auth.lower().startswith("bearer ") else ""
                if not hmac.compare_digest(presented, expected):
                    self._rejected_count += 1
                    from fastapi import HTTPException

                    raise HTTPException(status_code=401, detail="unauthorized")
            elif _exchange_auth_required():
                self._rejected_count += 1
                from fastapi import HTTPException

                raise HTTPException(
                    status_code=503,
                    detail="exchange transport not configured — set AGENTOS_EXCHANGE_TOKEN",
                )
            else:
                logger.warning(
                    "[exchange] receiving proposals UNAUTHENTICATED — set "
                    "AGENTOS_EXCHANGE_TOKEN (and TLS) in production"
                )

            try:
                p = body.proposal
                proposal = BeliefProposal(
                    origin=p.get("origin", ""),
                    kind=p.get("kind", ""),
                    domain=p.get("domain", ""),
                    transitions=tuple(tuple(t) for t in p.get("transitions", [])),
                    signature=p.get("signature", ""),
                    certificate=p.get("certificate"),
                )
            except Exception as e:
                self._rejected_count += 1
                return ProposalResponse(status="rejected", reason=f"malformed_proposal: {e}")

            if not is_shareable(proposal.kind, proposal.domain):
                self._rejected_count += 1
                return ProposalResponse(status="rejected", reason="never_share")

            # Deliver through the exchange pipeline (trust, policy, agreement checks).
            # The RECIPIENT is this runtime's tenant — NOT proposal.origin. Passing origin
            # would check the sender's relationship with itself and bypass trust governance.
            from src.monkey_brain.kernel.compile.world_tensor import get_world

            recipient = body.recipient_tenant or self._tenant_id
            recipient_belief = get_world(recipient)

            result = self._exchange.deliver(
                proposal,
                recipient,
                recipient_belief,
            )

            if result.status == "accepted":
                self._accepted_count += 1
            else:
                self._rejected_count += 1

            return ProposalResponse(
                status=result.status,
                reason=result.reason,
                merged=result.merged,
                conflicts=list(result.conflicts) if result.conflicts else [],
            )

        @router.get("/exchange/stats")
        async def exchange_stats():
            """Return exchange statistics."""
            return {
                "received": self._received_count,
                "accepted": self._accepted_count,
                "rejected": self._rejected_count,
                "queue_pending": self._exchange.queue.pending(),
            }

        if self._ca is not None:

            @router.get("/trust/bundle")
            async def trust_bundle():
                """Publish this authority's SIGNED trust bundle (aggregate CRL + cross-certs)
                for remote verifiers to fetch and load — CRL/OCSP-style distribution."""
                cross = self._cross() if callable(self._cross) else self._cross
                return self._ca.export_bundle(cross_certs=cross or [])

        return router


class BundleClient:
    """Fetches signed trust bundles from an authority over HTTP(S) (mTLS when configured)."""

    def __init__(self, timeout: float = 15.0, ssl_context: "ssl.SSLContext | None" = None) -> None:
        self._timeout = timeout
        self._ssl = ssl_context or build_client_ssl_context()

    def fetch(self, url: str) -> dict:
        headers = {"Accept": "application/json"}
        token = _exchange_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, method="GET", headers=headers)
        ctx = self._ssl if url.startswith("https://") else None
        with urllib.request.urlopen(req, timeout=self._timeout, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))


class BundleRefresher:
    """Background thread that periodically fetches signed bundles from trusted authorities and
    refreshes a TrustStore, so revocations and cross-certs converge across hosts.

    `sources` is a list of (bundle_url, authority_public_pem). Each bundle is verified against
    its authority's key before it can touch the store.
    """

    def __init__(
        self,
        store: Any,
        sources: list,
        *,
        interval: float = 300.0,
        client: "BundleClient | None" = None,
    ) -> None:
        self._store = store
        self._sources = sources
        self._interval = interval
        self._client = client or BundleClient()
        self._thread: threading.Thread | None = None
        self._running = False

    def refresh_once(self) -> int:
        loaded = 0
        for url, anchor in self._sources:
            try:
                bundle = self._client.fetch(url)
                if self._store.load_bundle(bundle, anchor):
                    loaded += 1
            except Exception as exc:
                logger.warning("[trust] bundle refresh from %s failed: %s", url, exc)
        return loaded

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="bundle-refresher")
        self._thread.start()
        logger.info(
            "[trust] bundle refresher started (%d sources, interval=%.0fs)",
            len(self._sources),
            self._interval,
        )

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=self._interval * 2)
            self._thread = None

    def _run(self) -> None:
        self.refresh_once()  # prime immediately
        while self._running:
            time.sleep(self._interval)
            if not self._running:
                break
            self.refresh_once()


def configure_exchange(network: "TrustNetwork | None" = None):
    """Assemble a production-configured exchange from env — CA-backed identity, revocation,
    and transport auth all wired in. Returns (exchange, server, flusher).

    Env:
      AGENTOS_CA_STORE      persistent CA store path → enables cert issuance + verification
      AGENTOS_CA_ID         CA identity (default 'authority:root')
      AGENTOS_TENANT_ID     this runtime's tenant (default 'default')
      AGENTOS_EXCHANGE_TOKEN transport bearer (see _exchange_token)
      EXCHANGE_MAX_QUEUE / EXCHANGE_FLUSH_INTERVAL
    """
    # Durable trust relationships (survive restart) when AGENTOS_TRUST_STORE is set.
    net = network if network is not None else TrustNetwork(store_path=os.getenv("AGENTOS_TRUST_STORE") or None)
    ca = anchor = crl = None
    store = os.getenv("AGENTOS_CA_STORE", "")
    if store:
        from src.monkey_brain.kernel.ca import CertificateAuthority

        ca = CertificateAuthority(ca_id=os.getenv("AGENTOS_CA_ID", "authority:root"), store_path=store)
        anchor = ca.ca_public_pem()
        crl = ca.aggregate_crl  # live CRL: whole-subtree revocations
        logger.info("[exchange] CA-backed identity enabled (store=%s)", store)
    else:
        logger.warning(
            "[exchange] no AGENTOS_CA_STORE — cross-host cert verification disabled (in-process identity only)"
        )
    ex = KnowledgeExchange(
        net,
        ca=ca,
        ca_public_pem=anchor,
        crl=crl,
        max_queue=int(os.getenv("EXCHANGE_MAX_QUEUE", "10000")),
    )
    server = ExchangeServer(ex, tenant_id=os.getenv("AGENTOS_TENANT_ID", "default"), ca=ca)
    flusher = ExchangeFlusher(ex.queue, interval=float(os.getenv("EXCHANGE_FLUSH_INTERVAL", "5")))
    return ex, server, flusher


class ExchangeFlusher:
    """Background thread that periodically flushes the MergeQueue.

    Call start() after the server is running.  stop() on shutdown.
    """

    def __init__(self, queue: MergeQueue, interval: float = 5.0) -> None:
        self._queue = queue
        self._interval = interval
        self._thread: threading.Thread | None = None
        self._running = False
        self._flush_count = 0

    def start(self) -> None:
        """Start the background flusher."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="exchange-flusher")
        self._thread.start()
        logger.info("[exchange] flusher started (interval=%.1fs)", self._interval)

    def stop(self) -> None:
        """Stop the background flusher."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=self._interval * 2)
            self._thread = None
        logger.info("[exchange] flusher stopped (flushed=%d)", self._flush_count)

    def _run(self) -> None:
        """Flush loop — runs in background thread."""
        while self._running:
            time.sleep(self._interval)
            if not self._running:
                break
            try:
                if self._queue.pending() > 0:
                    self._flush()
            except Exception as exc:
                logger.warning("[exchange] flush error: %s", exc)

    def _flush(self) -> None:
        """Flush the queue — apply accepted proposals to the world tensor."""
        from src.monkey_brain.kernel.compile.world_tensor import get_tenant_world

        tw = get_tenant_world()

        def tenant_of(recipient: str) -> str:
            return recipient  # recipient IS the tenant_id

        count = self._queue.flush(tw, tenant_of)
        if count > 0:
            self._flush_count += count
            logger.info("[exchange] flushed %d transitions (total=%d)", count, self._flush_count)
