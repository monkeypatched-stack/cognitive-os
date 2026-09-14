"""Knowledge exchange — beliefs as PROPOSALS, not truths (ADR 0021).

Nothing enters a runtime's world directly, not even from a trusted friend. A published
belief update (ΔW) is a candidate that the recipient validates against local policy and
its own comparator before anything is merged:

    Receive → Verify (signature) → Trust score → Policy check → Simulate/Compare
            → Accept / Reject / Quarantine → Merge Queue → BATCH World Update

Accepted proposals wait in a merge queue and are applied only via a batch world update
(the world's single mutation path), keeping the world versioned. Conflicts are recorded,
never silently overwritten. Sensitive classes (journal, credentials, health, …) never
leave a runtime.
"""

from __future__ import annotations

import json
import logging
import os
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable

from src.monkey_brain.kernel.compile import _obs
from src.monkey_brain.kernel.compile.lifecycle import sign_checkpoint, verify_checkpoint
from src.monkey_brain.kernel.compile.tensor import Feature, SparseTransitionTensor
from src.monkey_brain.kernel.compile.trust import Perm, TrustNetwork

logger = logging.getLogger("agentos.compile.exchange")

# Knowledge classes that may be shared, and domains that NEVER leave a runtime.
SHAREABLE_KINDS = {
    "observation",
    "belief",
    "experience",
    "workflow",
    "execution_graph",
    "ontology",
    "policy",
    "tool",
    "api",
}
NEVER_SHARE = {
    "journal",
    "secret",
    "secrets",
    "credential",
    "credentials",
    "password",
    "health",
    "medical",
    "private",
    "financial",
    "finance",
    "conversation",
    "conversations",
}


# A proposal's kind determines which permission on the origin→recipient edge governs it
# (the publisher's grant to send that class of knowledge).
_KIND_PERM: dict[str, str] = {
    "belief": Perm.PUBLISH_BELIEFS,
    "observation": Perm.PUBLISH_OBSERVATIONS,
    "experience": Perm.PUBLISH_EXPERIENCES,
    "workflow": Perm.SHARE_WORKFLOWS,
    "execution_graph": Perm.SHARE_EXECUTION_GRAPHS,
    "ontology": Perm.SHARE_ONTOLOGY,
    "policy": Perm.SHARE_EXECUTION_GRAPHS,
    "tool": Perm.SHARE_WORKFLOWS,
    "api": Perm.PUBLISH_BELIEFS,
}


def is_shareable(kind: str, domain: str) -> bool:
    d = (domain or "").lower()
    if any(bad in d for bad in NEVER_SHARE):
        return False
    return kind in SHAREABLE_KINDS


@dataclass(frozen=True)
class BeliefProposal:
    """A signed candidate knowledge update published by a runtime."""

    origin: str
    kind: str  # observation | belief | workflow | execution_graph | ontology | ...
    domain: str
    transitions: tuple  # tuple of (src, dst, reward)
    signature: str = ""
    proposal_id: str = ""  # idempotency key (auto-generated if empty)
    certificate: dict | None = None  # CA-signed cert binding origin→public key (cross-host)

    def __post_init__(self):
        if not self.proposal_id:
            from uuid import uuid4

            object.__setattr__(self, "proposal_id", str(uuid4()))

    def payload(self) -> dict:
        return {
            "origin": self.origin,
            "kind": self.kind,
            "domain": self.domain,
            "transitions": sorted(self.transitions),
        }


@dataclass(frozen=True)
class ExchangeResult:
    status: str  # accepted | rejected | quarantined
    reason: str
    merged: int = 0
    conflicts: tuple = ()


class MergeQueue:
    """Accepted proposals wait here; a batch flush applies them — nothing enters the
    world directly. Deduplicates by proposal_id over a BOUNDED window (so the dedup set can't
    grow without limit).

    Thread-safe: all mutations are guarded by a lock."""

    def __init__(self, max_size: int = 10000, dedup_window: int = 50000) -> None:
        import threading

        self._lock = threading.Lock()
        self._q: list[tuple[str, BeliefProposal]] = []
        self._seen: "OrderedDict[str, bool]" = OrderedDict()  # bounded LRU of proposal_ids
        self._max = max_size
        self._dedup_window = dedup_window

    def is_full(self) -> bool:
        with self._lock:
            return len(self._q) >= self._max

    def enqueue(self, recipient: str, proposal: BeliefProposal) -> str:
        """Returns 'queued', 'duplicate', or 'full'. Bounded to resist proposal-flooding."""
        with self._lock:
            if proposal.proposal_id in self._seen:
                return "duplicate"
            if len(self._q) >= self._max:
                _obs.event("trust.queue_full", recipient=recipient, size=len(self._q))
                return "full"
            self._q.append((recipient, proposal))
            self._seen[proposal.proposal_id] = True
            while len(self._seen) > self._dedup_window:
                self._seen.popitem(last=False)  # evict oldest — bounds memory
            return "queued"

    def pending(self) -> int:
        with self._lock:
            return len(self._q)

    def flush(self, world_runtime: Any, tenant_of: Callable[[str], str]) -> int:
        """Apply all queued proposals via a BATCH world update — the world's only
        mutation path. Returns transitions applied."""
        with self._lock:
            snapshot = list(self._q)
            self._q.clear()

        by_tenant: dict[str, list[dict]] = {}
        for recipient, proposal in snapshot:
            tenant = tenant_of(recipient)
            for s, d, r in proposal.transitions:
                by_tenant.setdefault(tenant, []).append({"src": s, "dst": d, "domain": proposal.domain, "reward": r})
        applied = 0
        for tenant, trans in by_tenant.items():
            world_runtime.batch_update(tenant, trans, source="trust_merge")
            applied += len(trans)
        _obs.event("trust.merge_flush", applied=applied)
        return applied


class KnowledgeExchange:
    """The trust-governed belief-proposal pipeline.

    Uses Ed25519 signing via the identity module.  Checks runtime
    agreements before allowing knowledge exchange.
    """

    def __init__(
        self,
        network: TrustNetwork,
        *,
        signing_key: bytes = b"trust-signing-key",
        trust_threshold: float = 0.5,
        max_queue: int = 10000,
        key_manager: Any = None,
        ca: Any = None,
        ca_public_pem: str | None = None,
        crl: Any = None,
        cross_certs: Any = None,
    ) -> None:
        self._net = network
        self._key = signing_key
        self._threshold = trust_threshold
        self.queue = MergeQueue(max_size=max_queue)
        # `crl` is a live set of revoked serial numbers (or a callable returning one) the
        # recipient checks incoming certs against — revocation without a round-trip to the CA.
        self._crl = crl
        # `cross_certs` are bridge certificates from trusted roots vouching for peer roots —
        # lets this runtime accept proposals certified under a different (cross-signed) root.
        self._cross = cross_certs
        # Cross-host identity: `ca` issues certs on publish (sender side); `ca_public_pem`
        # is the trust anchor used to verify incoming certs (recipient side). A per-host
        # `key_manager` lets the sender and recipient hold DIFFERENT key stores — proving
        # verification needs only the CA anchor, not shared key state.
        self._km = key_manager
        self._ca = ca
        self._ca_anchor = ca_public_pem

    def _key_manager(self):
        if self._km is None:
            from src.monkey_brain.kernel.identity import get_key_manager

            self._km = get_key_manager()
        return self._km

    # ── publish (with never-share filter + signing) ──────────────────────────────

    def publish(self, origin: str, kind: str, domain: str, transitions: Iterable[tuple]) -> BeliefProposal | None:
        """Create a signed belief proposal.

        Uses Ed25519 signing via the identity module when available,
        falls back to HMAC for backward compatibility.
        """
        if not is_shareable(kind, domain):
            _obs.event("trust.publish_blocked", origin=origin, kind=kind, domain=domain)
            logger.info(
                "[exchange] %s blocked from sharing %s/%s (never-share)",
                origin,
                kind,
                domain,
            )
            return None
        prop = BeliefProposal(origin, kind, domain, tuple(tuple(t) for t in transitions))
        signature = self._sign(prop.payload(), origin)
        # Attach a CA-signed cert (origin→public key) so recipients on other hosts can
        # verify without prior knowledge of this runtime — only the CA's public key.
        cert = None
        if self._ca is not None:
            try:
                pub = self._key_manager().get_public_key_pem(origin)
                cert = self._ca.issue(origin, pub).to_dict()
            except Exception as exc:
                logger.debug("[exchange] cert issue failed for %s: %s", origin, exc)
        prop = replace(prop, signature=signature, certificate=cert)
        _obs.event(
            "trust.publish",
            origin=origin,
            kind=kind,
            domain=domain,
            n=len(prop.transitions),
        )
        return prop

    # ── signing (scheme-tagged: Ed25519 asymmetric, HMAC fallback) ───────────────

    @staticmethod
    def _blob(payload: dict) -> bytes:
        return json.dumps(payload, sort_keys=True, default=str).encode("utf-8")

    def _sign(self, payload: dict, origin: str) -> str:
        """Sign with the origin's Ed25519 private key (asymmetric — a verifier cannot
        forge). Tag the scheme so the recipient verifies with exactly what was used. Falls
        back to HMAC only if the identity module is unavailable."""
        try:
            from src.monkey_brain.kernel.identity import sign_bytes

            key = self._key_manager().get_or_create(origin)
            return "ed25519:" + sign_bytes(self._blob(payload), key)
        except Exception as exc:  # identity unavailable → shared-key HMAC
            logger.debug("[exchange] Ed25519 sign unavailable (%s); HMAC fallback", exc)
            return "hmac:" + sign_checkpoint(payload, self._key)

    def _verify(
        self,
        payload: dict,
        signature: str,
        origin: str,
        certificate: dict | None = None,
    ) -> bool:
        """Verify a proposal's signature.

        Cross-host: when a CA anchor is configured and the proposal carries a certificate,
        verify the cert against the anchor, bind it to `origin`, then check the signature
        against the CERTIFIED public key — no prior knowledge of the origin required. Else
        fall back to the in-process path (local KeyManager / shared HMAC).
        """
        scheme, _, sig = signature.partition(":")
        if self._ca_anchor and certificate:
            from src.monkey_brain.kernel.ca import (
                verify_certificate,
                verify_certificate_chain,
            )
            from src.monkey_brain.kernel.identity import verify_bytes

            crl = self._crl() if callable(self._crl) else self._crl
            cross = self._cross() if callable(self._cross) else self._cross
            if certificate.get("chain"):  # federated: walk the chain to root
                ok = verify_certificate_chain(
                    certificate,
                    {self._ca_anchor},
                    revoked_serials=crl,
                    cross_certs=cross,
                )
            else:  # flat single-CA
                ok = verify_certificate(certificate, self._ca_anchor, revoked_serials=crl)
            if not ok:
                return False
            if certificate.get("runtime_id") != origin:  # cert must bind THIS origin
                return False
            if scheme != "ed25519":
                return False
            try:
                return verify_bytes(self._blob(payload), sig, certificate.get("public_key_pem", ""))
            except Exception:
                return False
        if scheme == "ed25519":
            try:
                from src.monkey_brain.kernel.identity import verify_bytes

                pub = self._key_manager().get_public_key_pem(origin)
                return verify_bytes(self._blob(payload), sig, pub)
            except Exception as exc:
                logger.debug("[exchange] Ed25519 verify failed for %s: %s", origin, exc)
                return False
        if scheme == "hmac":
            return verify_checkpoint(payload, sig, self._key)
        return verify_checkpoint(payload, signature, self._key)  # legacy untagged

    # ── deliver (the proposal-as-candidate pipeline) ─────────────────────────────

    def deliver(
        self,
        proposal: BeliefProposal,
        recipient: str,
        recipient_belief: SparseTransitionTensor,
        *,
        required_perm: str | None = None,
        actor_getter: Any = None,
    ) -> ExchangeResult:
        """Validate a proposal for a recipient. On accept it is QUEUED for a batch merge
        (never written to the world directly). Reject/quarantine leave the world untouched.

        Args:
            proposal: The belief proposal being delivered
            recipient: The recipient (tenant) ID
            recipient_belief: The recipient's belief tensor (for conflict checking)
            required_perm: Optional permission requirement override
            actor_getter: Optional callable(recipient_id) → ActorRuntime to queue proposals in Layer 2
        """
        perm = required_perm or _KIND_PERM.get(proposal.kind, Perm.PUBLISH_BELIEFS)
        # 1. Verify signature — tamper-evident, against the origin's public key (Ed25519),
        # via the CA-signed certificate when present (cross-host).
        if not self._verify(
            proposal.payload(),
            proposal.signature,
            proposal.origin,
            proposal.certificate,
        ):
            return self._reject(proposal, recipient, "signature_invalid")
        # 2. Never-share guard (defense in depth on the receiving side)
        if not is_shareable(proposal.kind, proposal.domain):
            return self._reject(proposal, recipient, "never_share")
        # 3. Agreement check (opt-in). When TRUST_REQUIRE_AGREEMENTS is set, an explicit
        # agreement covering this knowledge kind must exist between origin and recipient.
        # Default OFF: the relationship policy + trust score below remain the gate, so the
        # network is usable without pre-registering an agreement for every pair.
        if os.getenv("TRUST_REQUIRE_AGREEMENTS", "").lower() in ("1", "true", "yes"):
            try:
                from src.monkey_brain.kernel.agreements import get_agreement_store

                store = get_agreement_store()
                if store.covers_knowledge(proposal.origin, recipient, proposal.kind) is None:
                    return self._reject(proposal, recipient, "no_active_agreement")
            except Exception:
                logger.debug("deliver: suppressed exception", exc_info=True)  # Agreement module not wired — skip check
        # 4. Trust score
        if self._net.trust(proposal.origin, recipient) < self._threshold:
            return self._reject(proposal, recipient, "trust_below_threshold")
        # 5. Policy check — does the relationship grant this class of exchange?
        if not self._net.permits(proposal.origin, recipient, perm):
            return self._reject(proposal, recipient, "not_permitted")
        # 5+6. Simulate / Compare — conflict with the recipient's current belief, no mutation
        conflicts = self._compare(recipient_belief, proposal)
        if conflicts:
            _obs.event(
                "trust.quarantine",
                origin=proposal.origin,
                recipient=recipient,
                conflicts=len(conflicts),
            )
            return ExchangeResult("quarantined", "conflict_with_local_belief", conflicts=tuple(conflicts))
        # 7. Accept → observation queue (not direct world write)
        # Proposal goes to actor's observation queue for belief formation (Layer 2: fusion)
        # The fusion function will apply trust weighting when integrating the observation
        outcome = self.queue.enqueue(recipient, proposal)
        if outcome == "full":
            return self._reject(proposal, recipient, "queue_full")
        if outcome == "duplicate":  # idempotent redelivery — already accepted
            return ExchangeResult("accepted", "duplicate", merged=0)

        # LAYER 1 → LAYER 2 HANDOFF: Queue proposal in actor's observation queue
        # The actor will fuse this observation (with trust weighting) in its next decision
        if actor_getter is not None:
            try:
                actor = actor_getter(recipient)
                if actor is not None:
                    actor.accept_proposal(proposal)
            except Exception as e:
                logger.debug("[exchange] actor handoff failed for %s: %s", recipient, e)

        _obs.event(
            "trust.accept",
            origin=proposal.origin,
            recipient=recipient,
            n=len(proposal.transitions),
        )
        return ExchangeResult("accepted", "queued_for_fusion", merged=len(proposal.transitions))

    # ── helpers ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _compare(belief: SparseTransitionTensor, proposal: BeliefProposal) -> list[dict]:
        """Comparator: find edges the proposal would change (same edge, materially
        different reward) — recorded as conflicts, never silently overwritten. No mutation.
        """
        existing = set(belief)
        conflicts: list[dict] = []
        for s, d, r in proposal.transitions:
            if (s, d) in existing:
                cur = belief.feature(s, d, Feature.REWARD)
                if abs(cur - float(r)) > 1e-6:
                    conflicts.append({"edge": (s, d), "local": cur, "proposed": float(r)})
        return conflicts

    def _reject(self, proposal: BeliefProposal, recipient: str, reason: str) -> ExchangeResult:
        _obs.event("trust.reject", origin=proposal.origin, recipient=recipient, reason=reason)
        logger.info(
            "[exchange] rejected proposal from %s to %s: %s",
            proposal.origin,
            recipient,
            reason,
        )
        return ExchangeResult("rejected", reason)
