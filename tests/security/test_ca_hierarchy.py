"""Federated CA hierarchy — the pyramid as real PKI.

root → national → enterprise → community → personal. A recipient that trusts ONLY the root
can verify a personal runtime it has never seen, certified through intermediate authorities;
a revoked intermediate invalidates its whole subtree.
"""

from __future__ import annotations

from src.monkey_brain.kernel.identity import KeyManager
from src.monkey_brain.kernel.ca import CertificateAuthority, verify_certificate_chain
from src.monkey_brain.kernel.compile import (
    KnowledgeExchange,
    Relationship,
    TrustNetwork,
    WorldModelRuntime,
)


def _pyramid(km):
    root = CertificateAuthority("authority:root", key_manager=km)
    nat = root.certify_ca(CertificateAuthority("authority:national", key_manager=km))
    ent = nat.certify_ca(CertificateAuthority("authority:enterprise", key_manager=km))
    com = ent.certify_ca(CertificateAuthority("authority:community", key_manager=km))
    return root, nat, ent, com


def test_four_tier_chain_verifies_to_root(tmp_path):
    km = KeyManager(key_dir=str(tmp_path / "k"))
    root, nat, ent, com = _pyramid(km)
    leaf = com.issue("person:alice", km.get_public_key_pem("person:alice"))
    assert len(leaf.chain) == 4  # community→enterprise→national→root
    assert verify_certificate_chain(leaf.to_dict(), {root.ca_public_pem()})


def test_chain_to_untrusted_root_rejected(tmp_path):
    km = KeyManager(key_dir=str(tmp_path / "k"))
    root, nat, ent, com = _pyramid(km)
    leaf = com.issue("person:alice", km.get_public_key_pem("person:alice"))
    other_root = CertificateAuthority("authority:rogue", key_manager=km)
    assert not verify_certificate_chain(leaf.to_dict(), {other_root.ca_public_pem()})


def test_revoked_intermediate_invalidates_subtree(tmp_path):
    km = KeyManager(key_dir=str(tmp_path / "k"))
    root, nat, ent, com = _pyramid(km)
    leaf = com.issue("person:bob", km.get_public_key_pem("person:bob"))
    roots = {root.ca_public_pem()}
    assert verify_certificate_chain(leaf.to_dict(), roots)
    # root revokes the national intermediate → everything beneath it is untrusted
    nat_cert_id = root.get_certs_for("authority:national")[0].cert_id
    root.revoke(nat_cert_id)
    assert not verify_certificate_chain(leaf.to_dict(), roots, revoked_serials=root.revoked_serials())


def test_pyramid_proposal_accepted_cross_host(tmp_path):
    sender_km = KeyManager(key_dir=str(tmp_path / "s"))
    recipient_km = KeyManager(key_dir=str(tmp_path / "r"))  # DIFFERENT host, no sender keys
    root = CertificateAuthority("authority:root", key_manager=sender_km)
    nat = root.certify_ca(CertificateAuthority("authority:national", key_manager=sender_km))
    com = nat.certify_ca(CertificateAuthority("authority:community", key_manager=sender_km))

    net = TrustNetwork()
    net.connect("person:alice", "bob", Relationship.MENTOR)
    sender = KnowledgeExchange(net, key_manager=sender_km, ca=com)
    recipient = KnowledgeExchange(net, key_manager=recipient_km, ca_public_pem=root.ca_public_pem())

    prop = sender.publish("person:alice", "belief", "api", [("A", "B", 1.0)])
    assert len(prop.certificate["chain"]) == 3  # community→national→root
    res = recipient.deliver(prop, "bob", WorldModelRuntime().new_local_belief())
    assert res.status == "accepted"  # trusts only the root, verifies through the chain
