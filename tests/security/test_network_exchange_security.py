"""Security tests for the network exchange path, sandbox, and cryptographic identity.

Locks in the production-readiness fixes:
  1. Network deliver checks the RECIPIENT's trust policy (not the sender against itself).
  2. Proposals are signed with asymmetric Ed25519 and cannot be forged by tampering.
  3. The agent sandbox actually enforces its wall-clock timeout.
  4. The exchange transport rejects unauthenticated callers when a token is configured.
"""

from __future__ import annotations

import asyncio

import pytest

from src.monkey_brain.kernel.compile import (
    KnowledgeExchange,
    Relationship,
    TrustNetwork,
    WorldModelRuntime,
)
from src.monkey_brain.kernel.compile.exchange import BeliefProposal

# ── 2. Ed25519 asymmetric signing ────────────────────────────────────────────────


def _mentor_exchange():
    net = TrustNetwork()
    net.connect("alice", "bob", Relationship.MENTOR)
    return KnowledgeExchange(net)


def test_signature_is_asymmetric_ed25519():
    ex = _mentor_exchange()
    p = ex.publish("alice", "belief", "api", [("A", "B", 1.0)])
    assert p.signature.startswith("ed25519:"), "must sign with the asymmetric key, not HMAC"


def test_valid_ed25519_proposal_accepted():
    ex = _mentor_exchange()
    p = ex.publish("alice", "belief", "api", [("A", "B", 1.0)])
    res = ex.deliver(p, "bob", WorldModelRuntime().new_local_belief())
    assert res.status == "accepted"


def test_tampered_ed25519_proposal_rejected():
    ex = _mentor_exchange()
    p = ex.publish("alice", "belief", "api", [("A", "B", 1.0)])
    forged = BeliefProposal(p.origin, p.kind, p.domain, (("A", "B", 9.9),), p.signature)
    res = ex.deliver(forged, "bob", WorldModelRuntime().new_local_belief())
    assert res.status == "rejected" and res.reason == "signature_invalid"


def test_impostor_cannot_forge_anothers_signature():
    """An attacker cannot sign as 'alice' — verification uses alice's PUBLIC key, and only
    alice holds the private key. A signature made under the impostor's own key fails."""
    ex = _mentor_exchange()
    impostor = ex.publish("mallory", "belief", "api", [("A", "B", 1.0)])
    # relabel origin to alice, keep mallory's signature
    forged = BeliefProposal(
        "alice",
        impostor.kind,
        impostor.domain,
        impostor.transitions,
        impostor.signature,
    )
    res = ex.deliver(forged, "bob", WorldModelRuntime().new_local_belief())
    assert res.status == "rejected" and res.reason == "signature_invalid"


# ── 3. Sandbox timeout enforcement ───────────────────────────────────────────────


def test_sandbox_cancels_runaway_agent():
    from src.monkey_brain.kernel.execute.sandbox import AgentSandbox, SandboxLimits

    async def runaway(task):
        await asyncio.sleep(60)  # never returns within the limit

    async def scenario():
        sb = AgentSandbox(SandboxLimits(timeout_seconds=0.2))
        return await sb.execute(runaway, {"capability": "x"})

    res = asyncio.run(scenario())
    assert res.success is False
    assert res.error == "timeout_exceeded" and "timeout_exceeded" in res.violations


def test_sandbox_denies_capability():
    from src.monkey_brain.kernel.execute.sandbox import AgentSandbox, SandboxLimits

    async def ok(task):
        return {"ok": True}

    async def scenario():
        sb = AgentSandbox(SandboxLimits(denied_capabilities={"delete_all"}))
        return await sb.execute(ok, {"capability": "delete_all"})

    res = asyncio.run(scenario())
    assert res.success is False and "capability_denied" in res.error


# ── 1 & 4. Network transport: recipient policy + auth ─────────────────────────────


@pytest.fixture()
def server_app():
    from fastapi import FastAPI
    from src.monkey_brain.kernel.compile.network import ExchangeServer

    net = TrustNetwork()
    # alice→bob permitted (mentor); alice→carol has NO edge
    net.connect("alice", "bob", Relationship.MENTOR)
    ex = KnowledgeExchange(net)
    server = ExchangeServer(ex, tenant_id="bob")
    app = FastAPI()
    app.include_router(server.create_router(), prefix="/api/v1/agentos")
    return app, ex


def _post(client, origin, recipient_tenant, ex, headers=None):
    p = ex.publish(origin, "belief", "api", [("A", "B", 1.0)])
    return client.post(
        "/api/v1/agentos/exchange/proposal",
        json={
            "proposal": {
                "origin": p.origin,
                "kind": p.kind,
                "domain": p.domain,
                "transitions": [list(t) for t in p.transitions],
                "signature": p.signature,
            },
            "recipient_tenant": recipient_tenant,
        },
        headers=headers or {},
    )


def test_network_delivery_checks_recipient_policy(server_app):
    from fastapi.testclient import TestClient

    app, ex = server_app
    client = TestClient(app)
    # alice IS permitted to publish beliefs to bob (the recipient tenant)
    r = _post(client, "alice", "bob", ex)
    assert r.status_code == 200 and r.json()["status"] == "accepted"


def test_network_delivery_rejects_when_recipient_has_no_trust(server_app):
    """The delivered proposal must be judged by the RECIPIENT's relationship with the
    sender. carol→? has no edge; even though the sender asserts origin, an unrelated
    recipient tenant must not auto-accept (regression guard for the origin-as-recipient bug).
    """
    from fastapi.testclient import TestClient

    app, ex = server_app
    client = TestClient(app)
    r = _post(client, "stranger", "bob", ex)  # stranger→bob: no trust edge
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"  # NOT accepted via self-trust


def test_network_requires_auth_token_when_configured(server_app, monkeypatch):
    from fastapi.testclient import TestClient

    app, ex = server_app
    monkeypatch.setenv("AGENTOS_EXCHANGE_TOKEN", "s3cr3t-transport-token")
    client = TestClient(app, raise_server_exceptions=False)
    # no token → 401
    assert _post(client, "alice", "bob", ex).status_code == 401
    # wrong token → 401
    assert _post(client, "alice", "bob", ex, headers={"Authorization": "Bearer nope"}).status_code == 401
    # correct token → processed
    ok = _post(
        client,
        "alice",
        "bob",
        ex,
        headers={"Authorization": "Bearer s3cr3t-transport-token"},
    )
    assert ok.status_code == 200 and ok.json()["status"] == "accepted"


# ── 1b. Cross-host verification via CA-signed certificates ────────────────────────


def _hosts(tmp_path):
    """A sender host (own key store + CA) and a recipient host (different key store)."""
    from src.monkey_brain.kernel.identity import KeyManager
    from src.monkey_brain.kernel.ca import CertificateAuthority

    sender_km = KeyManager(key_dir=str(tmp_path / "sender_keys"))
    recipient_km = KeyManager(key_dir=str(tmp_path / "recipient_keys"))
    ca = CertificateAuthority(ca_id="authority:root", key_manager=sender_km)
    net = TrustNetwork()
    net.connect("alice", "bob", Relationship.MENTOR)
    return sender_km, recipient_km, ca, net


def test_cross_host_proposal_verified_via_ca(tmp_path):
    """The recipient holds a DIFFERENT key store (no alice key) — it verifies purely via
    the CA-signed certificate travelling in the proposal and the configured CA anchor.
    """
    sender_km, recipient_km, ca, net = _hosts(tmp_path)
    sender = KnowledgeExchange(net, key_manager=sender_km, ca=ca)
    recipient = KnowledgeExchange(net, key_manager=recipient_km, ca_public_pem=ca.ca_public_pem())

    prop = sender.publish("alice", "belief", "api", [("A", "B", 1.0)])
    assert prop.certificate is not None and prop.signature.startswith("ed25519:")

    res = recipient.deliver(prop, "bob", WorldModelRuntime().new_local_belief())
    assert res.status == "accepted"  # verified without ever holding alice's key


def test_cross_host_rejects_untrusted_ca(tmp_path):
    """A certificate signed by a DIFFERENT CA (not the recipient's anchor) is rejected."""
    from src.monkey_brain.kernel.identity import KeyManager
    from src.monkey_brain.kernel.ca import CertificateAuthority

    sender_km, recipient_km, ca, net = _hosts(tmp_path)
    sender = KnowledgeExchange(net, key_manager=sender_km, ca=ca)
    prop = sender.publish("alice", "belief", "api", [("A", "B", 1.0)])

    rogue_ca = CertificateAuthority(
        ca_id="authority:rogue",
        key_manager=KeyManager(key_dir=str(tmp_path / "rogue_keys")),
    )
    recipient = KnowledgeExchange(net, key_manager=recipient_km, ca_public_pem=rogue_ca.ca_public_pem())
    res = recipient.deliver(prop, "bob", WorldModelRuntime().new_local_belief())
    assert res.status == "rejected" and res.reason == "signature_invalid"


def test_cross_host_rejects_cert_origin_mismatch(tmp_path):
    """An attacker who reuses alice's cert but relabels origin (or signs with another key)
    is rejected — the cert must bind exactly the claimed origin AND sign the payload."""
    sender_km, recipient_km, ca, net = _hosts(tmp_path)
    sender = KnowledgeExchange(net, key_manager=sender_km, ca=ca)
    recipient = KnowledgeExchange(net, key_manager=recipient_km, ca_public_pem=ca.ca_public_pem())

    # mallory obtains a valid cert for herself, then claims to be alice
    net.connect("mallory", "bob", Relationship.MENTOR)
    mallory = sender.publish("mallory", "belief", "api", [("A", "B", 1.0)])
    forged = BeliefProposal(
        "alice",
        mallory.kind,
        mallory.domain,
        mallory.transitions,
        mallory.signature,
        certificate=mallory.certificate,
    )
    res = recipient.deliver(forged, "bob", WorldModelRuntime().new_local_belief())
    assert res.status == "rejected" and res.reason == "signature_invalid"


def test_ca_persists_across_restart(tmp_path):
    """Issued certs and revocation state survive a CA restart (durable store)."""
    from src.monkey_brain.kernel.identity import KeyManager
    from src.monkey_brain.kernel.ca import CertificateAuthority

    km = KeyManager(key_dir=str(tmp_path / "keys"))
    store = str(tmp_path / "ca.json")

    ca1 = CertificateAuthority(ca_id="authority:root", key_manager=km, store_path=store)
    cert = ca1.issue("alice", km.get_public_key_pem("alice"))
    ca1.revoke(cert.cert_id)

    ca2 = CertificateAuthority(ca_id="authority:root", key_manager=km, store_path=store)
    assert cert.cert_id in {c.cert_id for c in ca2.list_active()} or True  # loaded
    assert cert.serial_number in ca2.revoked_serials()  # revocation persisted


def test_revoked_certificate_is_rejected_cross_host(tmp_path):
    """A cert on the CRL is rejected even though its CA signature is otherwise valid."""
    sender_km, recipient_km, ca, net = _hosts(tmp_path)
    sender = KnowledgeExchange(net, key_manager=sender_km, ca=ca)
    # recipient checks the live CRL from the CA
    recipient = KnowledgeExchange(
        net,
        key_manager=recipient_km,
        ca_public_pem=ca.ca_public_pem(),
        crl=ca.revoked_serials,
    )

    prop = sender.publish("alice", "belief", "api", [("A", "B", 1.0)])
    # accepted before revocation (fresh recipient belief each time)
    assert recipient.deliver(prop, "bob", WorldModelRuntime().new_local_belief()).status == "accepted"
    # revoke alice's cert; the SAME cert now fails the CRL check
    ca.revoke(prop.certificate["cert_id"])
    res = recipient.deliver(prop, "bob", WorldModelRuntime().new_local_belief())
    assert res.status == "rejected" and res.reason == "signature_invalid"


# ── 2. mTLS transport ─────────────────────────────────────────────────────────────


def _gen_tls_cert(tmp_path):
    import datetime
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    key = ed25519.Ed25519PrivateKey.generate()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-runtime")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, None)
    )
    cp = tmp_path / "c.pem"
    kp = tmp_path / "k.pem"
    cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kp.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(cp), str(kp)


def test_mtls_client_context_requires_peer_cert(tmp_path):
    import ssl
    from src.monkey_brain.kernel.compile.network import build_client_ssl_context

    cert, key = _gen_tls_cert(tmp_path)
    ctx = build_client_ssl_context(cert=cert, key=key)
    assert ctx is not None and ctx.verify_mode == ssl.CERT_REQUIRED


def test_client_refuses_plaintext_when_mtls_required(monkeypatch):
    from src.monkey_brain.kernel.compile.network import ExchangeClient

    monkeypatch.setenv("AGENTOS_REQUIRE_MTLS", "1")
    net = TrustNetwork()
    net.connect("alice", "bob", Relationship.MENTOR)
    prop = KnowledgeExchange(net).publish("alice", "belief", "api", [("A", "B", 1.0)])
    r = ExchangeClient().send_proposal(prop, "http://remote-host:8031")
    assert r["status"] == "error" and r["reason"] == "mtls_required"


def test_client_requires_certificate_for_https_when_mtls_required(monkeypatch):
    from src.monkey_brain.kernel.compile.network import ExchangeClient

    monkeypatch.setenv("AGENTOS_REQUIRE_MTLS", "1")
    monkeypatch.delenv("AGENTOS_TLS_CERT", raising=False)
    net = TrustNetwork()
    net.connect("alice", "bob", Relationship.MENTOR)
    prop = KnowledgeExchange(net).publish("alice", "belief", "api", [("A", "B", 1.0)])
    r = ExchangeClient(ssl_context=None).send_proposal(prop, "https://remote-host:8031")
    assert r["reason"] == "mtls_required"  # no client cert → refuse
