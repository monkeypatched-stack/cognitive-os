"""Basic Constraints — a leaf certificate must NOT be usable as a signing CA.

Regression guard for the leaf-as-CA forgery: a holder of a valid leaf cert (and its key)
must not be able to mint certificates for other identities that verify to the trusted root.
"""

from __future__ import annotations

from src.monkey_brain.kernel.identity import KeyManager
from src.monkey_brain.kernel.ca import (
    CertificateAuthority,
    Certificate,
    verify_certificate_chain,
)
from src.monkey_brain.kernel.identity import sign_bytes


def test_leaf_cannot_forge_a_cert_for_another_identity(tmp_path):
    km = KeyManager(key_dir=str(tmp_path / "k"))
    root = CertificateAuthority("authority:root", key_manager=km)

    # Mallory legitimately holds a leaf cert (and her private key).
    mallory_key = km.get_or_create("org:mallory")
    mallory_leaf = root.issue("org:mallory", km.get_public_key_pem("org:mallory"))
    assert mallory_leaf.is_ca is False

    # She forges a cert for "org:ceo" signed with HER key, chained under her leaf.
    forged = Certificate(
        runtime_id="org:ceo",
        public_key_pem=km.get_public_key_pem("org:mallory"),
        subject="org:ceo",
        issuer="org:mallory",
    )
    forged.signature = sign_bytes(forged.signing_body(), mallory_key)
    forged.chain = [mallory_leaf.to_dict()] + list(mallory_leaf.chain)

    # The signatures all check out, but the chain must be REJECTED: mallory's leaf is not a CA.
    assert not verify_certificate_chain(forged.to_dict(), {root.ca_public_pem()})


def test_genuine_ca_hierarchy_still_verifies(tmp_path):
    km = KeyManager(key_dir=str(tmp_path / "k"))
    root = CertificateAuthority("authority:root", key_manager=km)
    nat = root.certify_ca(CertificateAuthority("authority:national", key_manager=km))
    leaf = nat.issue("person:alice", km.get_public_key_pem("person:alice"))
    # every issuer in this chain IS a CA → still valid
    assert verify_certificate_chain(leaf.to_dict(), {root.ca_public_pem()})
