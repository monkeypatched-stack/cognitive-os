"""CA hierarchy hardening — name constraints, subtree CRL aggregation, chain depth.

A national CA cannot certify runtimes outside its namespace; a leaf revoked deep in the
tree surfaces in the root's aggregated CRL; over-long chains are rejected.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.identity import KeyManager
from src.monkey_brain.kernel.ca import CertificateAuthority, verify_certificate_chain


def _gov(km):
    root = CertificateAuthority("authority:root", key_manager=km)  # unrestricted
    us = root.certify_ca(CertificateAuthority("authority:us", key_manager=km, namespace="us:"))
    eu = root.certify_ca(CertificateAuthority("authority:eu", key_manager=km, namespace="eu:"))
    return root, us, eu


# ── name constraints ─────────────────────────────────────────────────────────────


def test_ca_cannot_certify_outside_its_namespace(tmp_path):
    km = KeyManager(key_dir=str(tmp_path / "k"))
    _root, us, _eu = _gov(km)
    us.issue("us:alice", km.get_public_key_pem("us:alice"))  # within scope: OK
    with pytest.raises(ValueError):
        us.issue("eu:mallory", km.get_public_key_pem("eu:mallory"))  # outside scope: refused


def test_subordinate_namespace_must_be_within_parent(tmp_path):
    km = KeyManager(key_dir=str(tmp_path / "k"))
    _root, us, _eu = _gov(km)
    # us (namespace "us:") cannot delegate an out-of-scope namespace to a child
    with pytest.raises(ValueError):
        us.certify_ca(CertificateAuthority("authority:us:rogue", key_manager=km, namespace="eu:"))


def test_forged_out_of_namespace_cert_fails_verification(tmp_path):
    """Even if a hostile intermediate mints a cert outside its scope, a verifier anchored on
    the root rejects it via the SIGNED name constraint."""
    km = KeyManager(key_dir=str(tmp_path / "k"))
    root, us, _eu = _gov(km)
    # bypass the issuing guard to simulate a malicious CA, then verify against root
    forged = us._issue_cert("eu:victim", km.get_public_key_pem("eu:victim"), is_ca=True)
    forged.chain = list(us._chain)
    assert not verify_certificate_chain(forged.to_dict(), {root.ca_public_pem()})


# ── subtree CRL aggregation ──────────────────────────────────────────────────────


def test_leaf_revocation_surfaces_in_root_aggregate_crl(tmp_path):
    km = KeyManager(key_dir=str(tmp_path / "k"))
    root, us, _eu = _gov(km)
    leaf = us.issue("us:alice", km.get_public_key_pem("us:alice"))
    roots = {root.ca_public_pem()}
    assert verify_certificate_chain(leaf.to_dict(), roots, revoked_serials=root.aggregate_crl())
    us.revoke(leaf.cert_id)  # revoked at the leaf's CA
    crl = root.aggregate_crl()  # root aggregates the whole subtree
    assert leaf.serial_number in crl
    assert not verify_certificate_chain(leaf.to_dict(), roots, revoked_serials=crl)


# ── chain depth guard ────────────────────────────────────────────────────────────


def test_overlong_chain_rejected(tmp_path):
    km = KeyManager(key_dir=str(tmp_path / "k"))
    root = CertificateAuthority("authority:root", key_manager=km)
    ca = root
    for i in range(6):
        ca = ca.certify_ca(CertificateAuthority(f"authority:l{i}", key_manager=km))
    leaf = ca.issue("person:deep", km.get_public_key_pem("person:deep"))
    roots = {root.ca_public_pem()}
    assert verify_certificate_chain(leaf.to_dict(), roots, max_depth=16)  # allowed when deep enough
    assert not verify_certificate_chain(leaf.to_dict(), roots, max_depth=3)  # bounded walk rejects
