"""WorkloadIdentityProvider — the canonical CognitiveOS abstraction around
SPIFFE/SPIRE (Runtime Approval Gate follow-on: workload identity layer).

CRITICAL PRINCIPLE (do not lose sight of this while reading or extending
this module): SPIFFE/SPIRE answers "who is this workload?" It does NOT
answer "what is this workload allowed to do?" A WorkloadIdentity returned
from here is cryptographic PROOF OF IDENTITY only -- it carries no
permissions, no approval mode, no execution authority. Authorization is,
and remains, GovernanceEngine + OPA's job (kernel/governance.py,
opa/policies/agentos_governance.rego); ApprovalArtifact remains the sole
record of a granted authorization (kernel/approval.py). This module must
never grow an is_allowed()/can_execute()-shaped method -- that would make
SPIFFE a second, competing authorization system, which is explicitly out
of scope.

Layering (do not spread SPIRE SDK calls throughout the codebase): the real
pyspiffe Workload API call already lives in ONE place --
packages/cerebellum/cerebellum/capabilities/security/spire_client.py's
fetch_svid()/spiffe_id_from_svid() (re-exported for services/ callers via
services/common/spire.py). This module is a KERNEL-layer facade around
THAT existing capability, not a second SPIFFE integration -- every caller
in src/monkey_brain should depend on WorkloadIdentityProvider, never
import spire_client or pyspiffe directly.

Resolution order (mirrors spire_client.fetch_svid exactly, since this
calls it directly):
  1. Real X.509-SVID via the SPIRE Workload API (pyspiffe, over
     SPIFFE_ENDPOINT_SOCKET) -- source="spire".
  2. SPIFFE_ID env var, ONLY under explicit insecure-dev mode and NEVER
     under production mode (spire_client.py's own guard) -- source="env".
  3. None -- no workload identity available. Callers MUST treat this as
     "unauthenticated," never fall back to a self-asserted agent_id/name.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agentos.workload_identity")

# Reuses the SAME trust-domain env var pipeline_attestation.py already
# established (services/common/pipeline_attestation.py::TRUST_DOMAIN) --
# one configurable trust domain for this deployment, not a second,
# differently-named variable for kernel-level workload identity.
DEFAULT_TRUST_DOMAIN = "cognitiveos.local"


def configured_trust_domain() -> str:
    """The trust domain this deployment's SPIRE Server issues under.

    Configurable, never hard-coded to a production assumption (Phase 2) --
    a local dev trust domain (DEFAULT_TRUST_DOMAIN) and a real production
    trust domain must be DIFFERENT values so a dev-issued SVID can never
    even parse as belonging to the production trust domain, let alone
    verify against its bundle.
    """
    return os.getenv("SPIFFE_TRUST_DOMAIN", DEFAULT_TRUST_DOMAIN).strip() or DEFAULT_TRUST_DOMAIN


def agent_spiffe_id(agent_id: str, *, trust_domain: str = "") -> str:
    """The canonical SPIFFE URI CognitiveOS mints for an agent/actor
    workload: spiffe://<trust-domain>/agent/<agent-id>.

    This is NEVER used as a substitute for real attestation -- it is the
    NAME a SPIRE registration entry is created under (an operator/deploy
    script's job, not this module's), and the value this module expects
    fetch_svid()'s real X.509-SVID to actually resolve to. Constructing
    this string proves nothing by itself; only a verified WorkloadIdentity
    (below) does.
    """
    domain = trust_domain or configured_trust_domain()
    return f"spiffe://{domain}/agent/{agent_id}"


class WorkloadIdentityError(RuntimeError):
    """Raised when a WorkloadIdentity is asked to be constructed from
    something other than a verified SPIFFE credential."""


@dataclass(frozen=True)
class WorkloadIdentity:
    """A verified workload identity -- the X.509-SVID facts this process
    actually holds right now, nothing more.

    Every field here must trace back to fetch_svid()'s real result
    (pyspiffe's X509Context/X509Svid, or the guarded env override). Never
    construct this from request JSON, a message's claimed sender, an
    agent_id string, LLM output, or any other agent-controlled input --
    see WorkloadIdentityProvider.get_current_identity()'s own docstring
    for the one sanctioned construction path.
    """

    spiffe_id: str
    trust_domain: str
    source: str
    """"spire" (real Workload API SVID) or "env" (guarded local-dev
    override) -- callers that require cryptographic proof (Phase 8 mTLS,
    Phase 22's SPIFFE_ID_COMES_FROM_VERIFIED_CREDENTIAL) must check this
    is "spire", not merely that spiffe_id is non-empty."""
    cert_pem: str | None = None
    fetched_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.spiffe_id or not self.spiffe_id.strip():
            raise WorkloadIdentityError("spiffe_id is required")
        if not self.spiffe_id.startswith("spiffe://"):
            raise WorkloadIdentityError(f"not a SPIFFE URI: {self.spiffe_id!r}")
        if self.source not in ("spire", "env"):
            raise WorkloadIdentityError(f"unknown identity source: {self.source!r}")

    @property
    def is_cryptographically_verified(self) -> bool:
        """True only for a real X.509-SVID fetched from the SPIRE Workload
        API. False for the guarded local-dev env override -- that source
        proves nothing cryptographically, it is a name a developer typed."""
        return self.source == "spire"

    def path_segment(self) -> str:
        """The part of the SPIFFE URI after '<scheme>://<trust-domain>' --
        e.g. "/agent/lending-decision". Used to compare identity SHAPE
        without the trust domain, when a caller has already separately
        verified the trust domain matches."""
        prefix = f"spiffe://{self.trust_domain}"
        return self.spiffe_id[len(prefix) :] if self.spiffe_id.startswith(prefix) else self.spiffe_id


class WorkloadIdentityProvider:
    """The one sanctioned entrypoint for obtaining this process's own
    workload identity. Every kernel caller depends on THIS, never on
    spire_client/pyspiffe directly (module docstring).
    """

    def __init__(self, *, socket_path: str = "", trust_domain: str = "") -> None:
        self._socket_path = socket_path
        self._trust_domain = trust_domain or configured_trust_domain()
        self._cached: WorkloadIdentity | None = None

    async def get_current_identity(self) -> WorkloadIdentity | None:
        """Fetch (or return a cached) verified identity for THIS process.

        Returns None when no identity is available -- the caller's
        responsibility, always, is to treat that as unauthenticated, never
        to substitute a self-asserted name (agent_id, actor_id, message
        sender). Short-TTL caching is deliberately NOT implemented here
        beyond a single fetched value per provider instance -- SVID
        rotation (Phase 19) is SPIRE's job; a long-lived provider instance
        that never refetches would silently outlive its own SVID's
        rotation. Callers that run for a long time should construct a
        fresh WorkloadIdentityProvider (or call get_x509_svid() directly)
        per use rather than holding one forever.
        """
        svid = await self.get_x509_svid()
        if svid is None:
            return None
        spiffe_id = svid.get("spiffe_id") or ""
        if not spiffe_id:
            return None
        try:
            identity = WorkloadIdentity(
                spiffe_id=spiffe_id,
                trust_domain=self._trust_domain,
                source=svid.get("source", "spire"),
                cert_pem=svid.get("cert_pem"),
            )
        except WorkloadIdentityError as exc:
            logger.warning("fetch_svid() returned an invalid identity, discarding: %s", exc)
            return None
        self._cached = identity
        return identity

    async def get_x509_svid(self) -> dict[str, Any] | None:
        """The raw SVID dict (spiffe_id/cert_pem/key_pem/bundle_pem/source)
        as fetch_svid() returns it -- exposed for a caller that needs the
        certificate material itself (e.g. mTLS setup), not just the
        identity. Never logs or returns key_pem to anything that isn't
        directly configuring a TLS context with it (Phase 21/18)."""
        try:
            from cerebellum.capabilities.security.spire_client import fetch_svid
        except ImportError:
            try:
                from services.common.spire import fetch_svid  # type: ignore[no-redef]
            except ImportError:
                logger.debug("get_x509_svid: no spire client available (cerebellum or services.common)")
                return None
        try:
            return await fetch_svid(socket_path=self._socket_path)
        except Exception as exc:
            logger.warning("get_x509_svid: fetch_svid() raised: %s", exc)
            return None

    async def get_trust_bundle(self) -> str | None:
        """The trust bundle (CA certs) this workload's SVID chains to, PEM
        encoded. KNOWN LIMITATION: spire_client.fetch_svid()'s pyspiffe
        path does not currently populate bundle_pem (its X509Context has
        a real x509_bundle_set(), but nothing in spire_client.py extracts
        it yet) -- this returns whatever fetch_svid() actually supplies
        rather than fabricating bundle material another way. A caller
        needing real trust-bundle verification today should configure its
        TLS layer against SPIRE's own bundle file/API directly (e.g. the
        spire-agent's federatesWith / bundle endpoint), not depend on this
        method returning a value yet.
        """
        svid = await self.get_x509_svid()
        return (svid or {}).get("bundle_pem")


_default_provider: WorkloadIdentityProvider | None = None


def get_workload_identity_provider() -> WorkloadIdentityProvider:
    global _default_provider
    if _default_provider is None:
        _default_provider = WorkloadIdentityProvider()
    return _default_provider


def reset_workload_identity_provider_for_tests() -> None:
    global _default_provider
    _default_provider = None
