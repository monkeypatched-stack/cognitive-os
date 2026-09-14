"""Multi-root cross-signing — independent international authorities recognizing each other.

Two peer roots (UN, ISO). A runtime is certified under ISO. A recipient that anchors ONLY on
UN accepts it iff UN has cross-signed ISO and the recipient holds that bridge certificate.
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


def _roots(km):
    un = CertificateAuthority("authority:un", key_manager=km)
    iso = CertificateAuthority("authority:iso", key_manager=km)
    return un, iso


def test_cross_signed_peer_root_accepted(tmp_path):
    km = KeyManager(key_dir=str(tmp_path / "k"))
    un, iso = _roots(km)
    leaf = iso.issue("iso:member", km.get_public_key_pem("iso:member"))
    un_only = {un.ca_public_pem()}

    # Without a bridge, a UN-anchored verifier does NOT trust an ISO-rooted chain
    assert not verify_certificate_chain(leaf.to_dict(), un_only)

    # UN cross-signs ISO → recipient holding that bridge now trusts ISO-rooted chains
    bridge = un.cross_sign(iso)
    assert verify_certificate_chain(leaf.to_dict(), un_only, cross_certs=[bridge.to_dict()])


def test_forged_bridge_does_not_extend_trust(tmp_path):
    """A bridge not actually signed by the trusted root can't extend trust."""
    km = KeyManager(key_dir=str(tmp_path / "k"))
    un, iso = _roots(km)
    rogue = CertificateAuthority("authority:rogue", key_manager=km)
    leaf = iso.issue("iso:member", km.get_public_key_pem("iso:member"))
    # rogue (untrusted) "vouches" for ISO — must not help against a UN anchor
    fake_bridge = rogue.cross_sign(iso)
    assert not verify_certificate_chain(leaf.to_dict(), {un.ca_public_pem()}, cross_certs=[fake_bridge.to_dict()])


def test_cross_signed_proposal_accepted_cross_host(tmp_path):
    sender_km = KeyManager(key_dir=str(tmp_path / "s"))
    recipient_km = KeyManager(key_dir=str(tmp_path / "r"))
    un = CertificateAuthority("authority:un", key_manager=sender_km)
    iso = CertificateAuthority("authority:iso", key_manager=sender_km)
    bridge = un.cross_sign(iso)

    net = TrustNetwork()
    net.connect("iso:alice", "bob", Relationship.MENTOR)
    sender = KnowledgeExchange(net, key_manager=sender_km, ca=iso)  # certified under ISO
    recipient = KnowledgeExchange(
        net,
        key_manager=recipient_km,
        ca_public_pem=un.ca_public_pem(),  # trusts only UN
        cross_certs=[bridge.to_dict()],
    )  # + the UN→ISO bridge
    prop = sender.publish("iso:alice", "belief", "api", [("A", "B", 1.0)])
    res = recipient.deliver(prop, "bob", WorldModelRuntime().new_local_belief())
    assert res.status == "accepted"
