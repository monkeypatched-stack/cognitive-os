"""Signed trust-bundle distribution — CRL and cross-certs shareable across real hosts.

A remote verifier holds NO CA reference; it refreshes a signed bundle and its exchange then
enforces the published revocations and honors the published cross-signatures.
"""

from __future__ import annotations

import copy

from src.monkey_brain.kernel.identity import KeyManager
from src.monkey_brain.kernel.ca import CertificateAuthority, TrustStore, verify_bundle
from src.monkey_brain.kernel.compile import (
    KnowledgeExchange,
    Relationship,
    TrustNetwork,
    WorldModelRuntime,
)


def test_bundle_signature_and_freshness(tmp_path):
    km = KeyManager(key_dir=str(tmp_path / "k"))
    root = CertificateAuthority("authority:root", key_manager=km)
    bundle = root.export_bundle()
    assert verify_bundle(bundle, root.ca_public_pem())
    # tampered CRL → signature fails
    bad = copy.deepcopy(bundle)
    bad["revoked_serials"] = ["forged-serial"]
    assert not verify_bundle(bad, root.ca_public_pem())
    # stale bundle → rejected
    stale = copy.deepcopy(bundle)
    stale["expires_at"] = 1.0
    assert not verify_bundle(stale, root.ca_public_pem())


def test_published_revocation_enforced_by_remote_verifier(tmp_path):
    sender_km = KeyManager(key_dir=str(tmp_path / "s"))
    recipient_km = KeyManager(key_dir=str(tmp_path / "r"))
    root = CertificateAuthority("authority:root", key_manager=sender_km)
    com = root.certify_ca(CertificateAuthority("authority:community", key_manager=sender_km))

    net = TrustNetwork()
    net.connect("alice", "bob", Relationship.MENTOR)
    sender = KnowledgeExchange(net, key_manager=sender_km, ca=com)

    store = TrustStore()  # remote verifier: no CA ref
    store.load_bundle(root.export_bundle(), root.ca_public_pem())
    recipient = KnowledgeExchange(
        net,
        key_manager=recipient_km,
        ca_public_pem=root.ca_public_pem(),
        crl=store.crl,
        cross_certs=store.cross_certs,
    )

    prop = sender.publish("alice", "belief", "api", [("A", "B", 1.0)])
    assert recipient.deliver(prop, "bob", WorldModelRuntime().new_local_belief()).status == "accepted"

    # revoke at the community CA, republish the bundle, refresh the store → now rejected
    com.revoke(prop.certificate["cert_id"])
    store.load_bundle(root.export_bundle(), root.ca_public_pem())
    assert prop.certificate["chain"]  # (cert carries its chain)
    res = recipient.deliver(prop, "bob", WorldModelRuntime().new_local_belief())
    assert res.status == "rejected" and res.reason == "signature_invalid"


def test_published_cross_cert_enables_peer_trust(tmp_path):
    sender_km = KeyManager(key_dir=str(tmp_path / "s"))
    recipient_km = KeyManager(key_dir=str(tmp_path / "r"))
    un = CertificateAuthority("authority:un", key_manager=sender_km)
    iso = CertificateAuthority("authority:iso", key_manager=sender_km)
    bridge = un.cross_sign(iso)

    net = TrustNetwork()
    net.connect("iso:alice", "bob", Relationship.MENTOR)
    sender = KnowledgeExchange(net, key_manager=sender_km, ca=iso)

    store = TrustStore()
    # UN publishes a bundle carrying the UN→ISO bridge; recipient trusts only UN
    store.load_bundle(un.export_bundle(cross_certs=[bridge]), un.ca_public_pem())
    recipient = KnowledgeExchange(
        net,
        key_manager=recipient_km,
        ca_public_pem=un.ca_public_pem(),
        crl=store.crl,
        cross_certs=store.cross_certs,
    )

    prop = sender.publish("iso:alice", "belief", "api", [("A", "B", 1.0)])
    assert recipient.deliver(prop, "bob", WorldModelRuntime().new_local_belief()).status == "accepted"
