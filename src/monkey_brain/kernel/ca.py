"""Certificate Authority — stub for future CA integration.

Provides the interface for certificate-based identity verification.
Currently uses self-signed certificates; designed for plugging in
a real CA (Let's Encrypt, internal PKI, SPIRE) later.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

logger = logging.getLogger("agentos.ca")


@dataclass
class Certificate:
    """A runtime certificate binding identity to a public key."""

    cert_id: str = field(default_factory=lambda: str(uuid4()))
    runtime_id: str = ""
    public_key_pem: str = ""
    subject: str = ""
    issuer: str = "self-signed"
    not_before: float = field(default_factory=time.time)
    not_after: float = 0.0  # 0 = no expiry
    serial_number: str = ""
    extensions: dict[str, Any] = field(default_factory=dict)
    revoked: bool = False
    revoked_at: float = 0.0
    signature: str = ""  # issuer's Ed25519 signature over signing_body()
    chain: list = field(default_factory=list)  # issuer certs up to root (leaf-issuer first)
    is_ca: bool = False  # basic constraints: may this cert sign other certs?

    def signing_body(self) -> bytes:
        """Canonical bytes the issuer signs — binds runtime_id ↔ public key ↔ issuer ↔
        is_ca ↔ validity. Including issuer + is_ca prevents a signature being replayed under a
        different claimed authority OR a leaf being promoted to a signing CA."""
        return json.dumps(
            {
                "runtime_id": self.runtime_id,
                "public_key_pem": self.public_key_pem,
                "subject": self.subject,
                "issuer": self.issuer,
                "is_ca": self.is_ca,
                "not_before": self.not_before,
                "not_after": self.not_after,
                "serial_number": self.serial_number,
                # name constraint is SIGNED so an intermediate's certifying scope can't be widened
                "name_constraint": (self.extensions or {}).get("name_constraint"),
            },
            sort_keys=True,
        ).encode()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Certificate":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @property
    def is_valid(self) -> bool:
        if self.revoked:
            return False
        now = time.time()
        if self.not_after > 0 and now > self.not_after:
            return False
        return now >= self.not_before

    def fingerprint(self) -> str:
        """SHA-256 fingerprint of the certificate."""
        blob = json.dumps(
            {
                "runtime_id": self.runtime_id,
                "public_key_pem": self.public_key_pem,
                "subject": self.subject,
                "issuer": self.issuer,
                "not_before": self.not_before,
                "not_after": self.not_after,
            },
            sort_keys=True,
        ).encode()
        return hashlib.sha256(blob).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "cert_id": self.cert_id,
            "runtime_id": self.runtime_id,
            "public_key_pem": self.public_key_pem,
            "subject": self.subject,
            "issuer": self.issuer,
            "not_before": self.not_before,
            "not_after": self.not_after,
            "serial_number": self.serial_number,
            "revoked": self.revoked,
            "revoked_at": self.revoked_at,
            "signature": self.signature,
            "chain": self.chain,
            "extensions": self.extensions,
            "is_ca": self.is_ca,
        }


class CertificateAuthority:
    """Manages runtime certificates.

    Currently self-signed; designed for plugging in a real CA.
    """

    def __init__(
        self,
        ca_id: str = "authority:root",
        key_manager: Any = None,
        store_path: str | None = None,
        namespace: str = "",
    ) -> None:
        self._certs: dict[str, Certificate] = {}  # cert_id -> cert
        self._by_runtime: dict[str, list[str]] = {}  # runtime_id -> [cert_ids]
        self._ca_id = ca_id
        self._store_path = store_path
        # Names this CA is permitted to certify (prefix). "" = unrestricted (root). An
        # intermediate can only be given a namespace within its parent's — enforced below.
        self._namespace = namespace
        self._subordinates: list["CertificateAuthority"] = []
        from src.monkey_brain.kernel.identity import get_key_manager

        self._km = key_manager or get_key_manager()
        self._ca_key = self._km.get_or_create(ca_id)  # the CA's own Ed25519 signing key
        # This CA's identity + chain to root. A root CA is self-signed; certify_ca() makes
        # this an INTERMEDIATE by replacing the chain with [our-cert, ...parent chain].
        self._identity_cert = self._issue_cert(
            ca_id, self.ca_public_pem(), name_constraint=namespace or None, is_ca=True
        ).to_dict()
        self._chain = [self._identity_cert]
        if store_path and os.path.exists(store_path):
            self._load()

    def _issue_cert(
        self,
        runtime_id: str,
        public_key_pem: str,
        *,
        subject: str = "",
        validity_days: float = 365,
        name_constraint: str | None = None,
        is_ca: bool = False,
    ) -> Certificate:
        from src.monkey_brain.kernel.identity import sign_bytes

        # Name-constraint enforcement: this CA may only certify names within its namespace
        # (its own identity cert is exempt — that binding comes from the parent).
        if self._namespace and not is_ca and not runtime_id.startswith(self._namespace):
            raise ValueError(f"{self._ca_id} may not certify {runtime_id!r} outside namespace {self._namespace!r}")
        ext = {"name_constraint": name_constraint} if name_constraint else {}
        cert = Certificate(
            runtime_id=runtime_id,
            public_key_pem=public_key_pem,
            subject=subject or runtime_id,
            issuer=self._ca_id,
            extensions=ext,
            is_ca=is_ca,
            not_after=time.time() + (validity_days * 86400) if validity_days > 0 else 0,
            serial_number=uuid4().hex,
        )
        cert.signature = sign_bytes(cert.signing_body(), self._ca_key)
        return cert

    def certify_ca(self, child: "CertificateAuthority", *, validity_days: float = 365) -> "CertificateAuthority":
        """Make `child` a SUBORDINATE of this CA (the pyramid): this CA signs the child CA's
        identity, and the child's certs now chain up through us to our root. The child's
        namespace must fall within ours (a national CA can't delegate global authority).
        """
        if self._namespace and not child._namespace.startswith(self._namespace):
            raise ValueError(f"subordinate namespace {child._namespace!r} not within {self._namespace!r}")
        child_cert = self._issue_cert(
            child._ca_id,
            child.ca_public_pem(),
            name_constraint=child._namespace or None,
            validity_days=validity_days,
            is_ca=True,
        )
        child._identity_cert = child_cert.to_dict()
        child._chain = [child._identity_cert] + self._chain
        self._certs[child_cert.cert_id] = child_cert
        self._by_runtime.setdefault(child._ca_id, []).append(child_cert.cert_id)
        self._subordinates.append(child)
        self._save()
        logger.info(
            "[ca] %s certified subordinate CA %s (ns=%s)",
            self._ca_id,
            child._ca_id,
            child._namespace,
        )
        return child

    _BUNDLE_FIELDS = (
        "issuer",
        "issued_at",
        "expires_at",
        "revoked_serials",
        "cross_certs",
    )

    def export_bundle(self, *, cross_certs: "list | None" = None, ttl_days: float = 1.0) -> dict:
        """Publish a SIGNED trust bundle — the aggregate CRL plus any cross-certs — that
        remote verifiers can fetch and load without holding a reference to this CA. Signed by
        the CA key and time-bounded so a stale or tampered bundle is rejected on load.
        """
        from src.monkey_brain.kernel.identity import sign_bytes

        now = time.time()
        body = {
            "issuer": self._ca_id,
            "issued_at": now,
            "expires_at": now + ttl_days * 86400,
            "revoked_serials": sorted(self.aggregate_crl()),
            "cross_certs": [c.to_dict() if isinstance(c, Certificate) else c for c in (cross_certs or [])],
        }
        blob = json.dumps(body, sort_keys=True).encode("utf-8")
        body["signature"] = sign_bytes(blob, self._ca_key)
        return body

    def cross_sign(self, other: "CertificateAuthority", *, validity_days: float = 365) -> Certificate:
        """Vouch for a PEER root's identity — a trust BRIDGE (not subordination). Runtimes
        anchored on THIS root can then verify chains rooted under `other` by presenting the
        returned cross-certificate. This is how independent international authorities (UN,
        ISO, W3C) recognize each other without a single global super-root."""
        bridge = self._issue_cert(
            other._ca_id,
            other.ca_public_pem(),
            name_constraint=other._namespace or None,
            is_ca=True,
            validity_days=validity_days,
        )
        bridge.chain = list(self._chain)  # bridge chains up to THIS (trusted) root
        self._certs[bridge.cert_id] = bridge
        self._by_runtime.setdefault(other._ca_id, []).append(bridge.cert_id)
        self._save()
        logger.info("[ca] %s cross-signed peer root %s", self._ca_id, other._ca_id)
        return bridge

    # ── persistence: certs + revocation survive restart ─────────────────────────────

    def _load(self) -> None:
        try:
            with open(self._store_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:
            logger.warning("[ca] failed to load store %s: %s", self._store_path, exc)
            return
        for cd in data.get("certs", []):
            cert = Certificate.from_dict(cd)
            self._certs[cert.cert_id] = cert
            self._by_runtime.setdefault(cert.runtime_id, []).append(cert.cert_id)
        logger.info("[ca] loaded %d certs from %s", len(self._certs), self._store_path)

    def _save(self) -> None:
        if not self._store_path:
            return
        from src.monkey_brain.kernel.identity import _atomic_write

        blob = json.dumps({"certs": [c.to_dict() for c in self._certs.values()]}, sort_keys=True).encode("utf-8")
        _atomic_write(self._store_path, blob, mode=0o600)

    def revoked_serials(self, *, recursive: bool = False) -> set[str]:
        """The revocation list (CRL): serials of revoked certs. With `recursive`, aggregate
        the whole subtree so a leaf revoked at the community tier is visible in the root's
        CRL — the distribution a hierarchy needs (verifiers anchor on the root, so they must
        see revocations from every authority beneath it)."""
        crl = {c.serial_number for c in self._certs.values() if c.revoked}
        if recursive:
            for sub in self._subordinates:
                crl |= sub.revoked_serials(recursive=True)
        return crl

    def aggregate_crl(self) -> set[str]:
        """The full-subtree CRL — pass (as a callable) to a recipient's exchange so leaf and
        intermediate revocations anywhere below this authority are enforced."""
        return self.revoked_serials(recursive=True)

    def ca_public_pem(self) -> str:
        """The CA's public key — the trust anchor recipients must be configured with."""
        return self._km.get_public_key_pem(self._ca_id)

    def issue(
        self,
        runtime_id: str,
        public_key_pem: str,
        *,
        subject: str = "",
        validity_days: float = 365,
    ) -> Certificate:
        """Issue a certificate binding runtime_id → public_key, SIGNED by the CA key and
        carrying this CA's chain to root, so any host holding the ROOT's public key can
        verify it without prior knowledge of runtime_id or the intermediate authorities.
        """
        cert = self._issue_cert(runtime_id, public_key_pem, subject=subject, validity_days=validity_days)
        cert.chain = list(self._chain)  # issuer → … → root
        self._certs[cert.cert_id] = cert
        self._by_runtime.setdefault(runtime_id, []).append(cert.cert_id)
        self._save()
        logger.info(
            "[ca] issued cert %s for %s (issuer=%s, chain_len=%d)",
            cert.cert_id[:8],
            runtime_id,
            self._ca_id,
            len(cert.chain),
        )
        return cert

    def verify(self, runtime_id: str, cert_id: str) -> bool:
        """Verify a certificate is valid for a runtime."""
        cert = self._certs.get(cert_id)
        if not cert:
            return False
        return cert.runtime_id == runtime_id and cert.is_valid

    def revoke(self, cert_id: str) -> bool:
        """Revoke a certificate."""
        cert = self._certs.get(cert_id)
        if not cert:
            return False
        cert.revoked = True
        cert.revoked_at = time.time()
        self._save()
        logger.info("[ca] revoked cert %s", cert_id[:8])
        return True

    def get_cert(self, cert_id: str) -> Certificate | None:
        return self._certs.get(cert_id)

    def get_certs_for(self, runtime_id: str) -> list[Certificate]:
        ids = self._by_runtime.get(runtime_id, [])
        return [self._certs[cid] for cid in ids if cid in self._certs]

    def list_active(self) -> list[Certificate]:
        return [c for c in self._certs.values() if c.is_valid]

    def cleanup_expired(self) -> int:
        """Remove expired certificates."""
        expired = [cid for cid, c in self._certs.items() if not c.is_valid]
        for cid in expired:
            del self._certs[cid]
        return len(expired)


def verify_certificate(
    cert: "Certificate | dict[str, Any]",
    ca_public_pem: str,
    revoked_serials: "set[str] | None" = None,
) -> bool:
    """Verify a certificate against a trusted CA public key (the anchor).

    Proves the CA attested `runtime_id → public_key` and the cert is currently valid and
    not revoked. This is what makes cross-host verification possible: a recipient needs
    only the CA's public key plus the CRL, never prior knowledge of the origin runtime.
    """
    if isinstance(cert, dict):
        cert = Certificate.from_dict(cert)
    if not cert.is_valid or not cert.signature:
        return False
    if revoked_serials and cert.serial_number in revoked_serials:
        return False  # revoked (CRL)
    from src.monkey_brain.kernel.identity import verify_bytes

    try:
        return verify_bytes(cert.signing_body(), cert.signature, ca_public_pem)
    except Exception:
        return False


def verify_certificate_chain(
    cert: "Certificate | dict[str, Any]",
    trusted_root_pems: "set[str]",
    revoked_serials: "set[str] | None" = None,
    *,
    max_depth: int = 8,
    cross_certs: "list | None" = None,
) -> bool:
    """Walk a certificate chain to a trusted root (the pyramid).

    Each cert must be signed by the next authority up AND fall within that authority's
    name-constraint namespace; the topmost must be self-signed and its public key present in
    `trusted_root_pems`. Every cert must be valid and unrevoked, and the chain no longer than
    `max_depth` (a bounded walk resists chain-bomb DoS). A recipient therefore needs only the
    ROOT's public key to verify a runtime it has never seen — but an intermediate can never
    certify outside its delegated scope.
    """
    from src.monkey_brain.kernel.identity import verify_bytes

    if isinstance(cert, dict):
        cert = Certificate.from_dict(cert)
    chain = [Certificate.from_dict(c) if isinstance(c, dict) else c for c in (cert.chain or [])]
    if len(chain) > max_depth:
        return False

    def _ok(c: "Certificate") -> bool:
        if not c.is_valid or not c.signature:
            return False
        if revoked_serials and c.serial_number in revoked_serials:
            return False
        return True

    try:
        current = cert
        for issuer in chain:  # leaf → … → (issuer just below root)
            if not _ok(current) or current.issuer != issuer.runtime_id:
                return False
            if not issuer.is_ca:  # basic constraints: a leaf may NOT
                return False  # sign certs — blocks leaf-as-CA forgery
            nc = (issuer.extensions or {}).get("name_constraint")
            if nc and not current.runtime_id.startswith(nc):  # issuer's delegated scope
                return False
            if not verify_bytes(current.signing_body(), current.signature, issuer.public_key_pem):
                return False
            current = issuer
        # `current` is now the chain root: must be self-signed and internally consistent
        if not _ok(current):
            return False
        if not verify_bytes(current.signing_body(), current.signature, current.public_key_pem):
            return False
        if current.public_key_pem in trusted_root_pems:  # directly anchored
            return True
        # Cross-signing: a trusted root bridges this (otherwise-untrusted) peer root by
        # vouching for its exact identity + public key, itself chaining to a trusted root.
        for bridge in cross_certs or []:
            b = Certificate.from_dict(bridge) if isinstance(bridge, dict) else bridge
            if (
                b.runtime_id == current.runtime_id
                and b.public_key_pem == current.public_key_pem
                and _ok(b)
                and verify_certificate_chain(b, trusted_root_pems, revoked_serials, max_depth=max_depth)
            ):
                return True
        return False
    except Exception:
        return False


def verify_bundle(bundle: dict, ca_public_pem: str) -> bool:
    """Verify a published trust bundle against the issuer's public key and its freshness."""
    from src.monkey_brain.kernel.identity import verify_bytes

    sig = bundle.get("signature", "")
    if not sig:
        return False
    now = time.time()
    if bundle.get("expires_at", 0) and now > bundle["expires_at"]:
        return False  # stale — reject
    if bundle.get("issued_at", 0) > now + 300:  # future-dated beyond clock skew — reject
        return False
    body = {k: bundle[k] for k in CertificateAuthority._BUNDLE_FIELDS if k in bundle}
    blob = json.dumps(body, sort_keys=True).encode("utf-8")
    try:
        return verify_bytes(blob, sig, ca_public_pem)
    except Exception:
        return False


class TrustStore:
    """A recipient-side, refreshable view of revocations and cross-certs, populated from
    SIGNED bundles published by trusted authorities. Hand its callables to a KnowledgeExchange
    (crl=store.crl, cross_certs=store.cross_certs) so the live exchange always sees the latest
    revocations and bridges — without holding any CA reference. Re-loading an issuer's bundle
    replaces that issuer's contribution (so un-revocation / bridge removal propagate).
    """

    def __init__(self) -> None:
        self._sources: dict[str, tuple[set, list]] = {}  # issuer -> (crl subset, cross subset)
        self._crl: set[str] = set()
        self._cross: list = []

    def load_bundle(self, bundle: dict, ca_public_pem: str) -> bool:
        if not verify_bundle(bundle, ca_public_pem):
            return False
        self._sources[bundle["issuer"]] = (
            set(bundle.get("revoked_serials", [])),
            list(bundle.get("cross_certs", [])),
        )
        self._rebuild()
        logger.info(
            "[truststore] loaded bundle from %s (%d revoked, %d bridges)",
            bundle["issuer"],
            len(self._crl),
            len(self._cross),
        )
        return True

    def _rebuild(self) -> None:
        crl: set[str] = set()
        cross: list = []
        for crl_subset, cross_subset in self._sources.values():
            crl |= crl_subset
            cross.extend(cross_subset)
        self._crl, self._cross = crl, cross

    def crl(self) -> set[str]:
        return self._crl

    def cross_certs(self) -> list:
        return self._cross


_default_ca: CertificateAuthority | None = None


def get_ca() -> CertificateAuthority:
    global _default_ca
    if _default_ca is None:
        _default_ca = CertificateAuthority()
    return _default_ca
