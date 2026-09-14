"""Integration: the ASSEMBLED exchange server (configure_exchange) over HTTP.

Drives a CA-signed proposal through the whole mounted pipeline — certificate verification
against the CA anchor, recipient trust/permission policy, and transport auth — exactly as
main.py wires it, but with an isolated CA store and trust network.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.compile import Relationship, TrustNetwork
from src.monkey_brain.kernel.compile.network import configure_exchange


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOS_CA_STORE", str(tmp_path / "ca.json"))
    monkeypatch.setenv("AGENTOS_TENANT_ID", "default")
    monkeypatch.setenv("AGENTOS_EXCHANGE_AUTH_REQUIRED", "false")
    monkeypatch.delenv("AGENTOS_EXCHANGE_TOKEN", raising=False)
    # server side: alice may publish beliefs to this runtime ("default")
    net = TrustNetwork()
    net.connect("alice", "default", Relationship.MENTOR)
    _server_ex, server, _flusher = configure_exchange(net)
    # sender side: same CA (same env) so its cert verifies against the server's anchor
    sender, _s, _f = configure_exchange(TrustNetwork())

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(server.create_router(), prefix="/api/v1/agentos")
    return TestClient(app, raise_server_exceptions=False), sender


def _post(client, prop, headers=None):
    return client.post(
        "/api/v1/agentos/exchange/proposal",
        json={
            "proposal": {
                "origin": prop.origin,
                "kind": prop.kind,
                "domain": prop.domain,
                "transitions": [list(t) for t in prop.transitions],
                "signature": prop.signature,
                "certificate": prop.certificate,
            },
            "recipient_tenant": "default",
        },
        headers=headers or {},
    )


def test_ca_signed_proposal_accepted_end_to_end(wired):
    client, sender = wired
    prop = sender.publish("alice", "belief", "api", [("A", "B", 1.0)])
    assert prop.certificate is not None  # CA issued a cert on publish
    r = _post(client, prop)
    assert r.status_code == 200 and r.json()["status"] == "accepted"


def test_untrusted_origin_rejected_end_to_end(wired):
    client, sender = wired
    prop = sender.publish("stranger", "belief", "api", [("A", "B", 1.0)])
    r = _post(client, prop)
    assert r.status_code == 200 and r.json()["status"] == "rejected"


def test_transport_auth_enforced_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOS_CA_STORE", str(tmp_path / "ca.json"))
    monkeypatch.setenv("AGENTOS_TENANT_ID", "default")
    monkeypatch.setenv("AGENTOS_EXCHANGE_TOKEN", "tok-123")
    net = TrustNetwork()
    net.connect("alice", "default", Relationship.MENTOR)
    _ex, server, _fl = configure_exchange(net)
    sender, _s, _f = configure_exchange(TrustNetwork())

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(server.create_router(), prefix="/api/v1/agentos")
    client = TestClient(app, raise_server_exceptions=False)
    prop = sender.publish("alice", "belief", "api", [("A", "B", 1.0)])
    assert _post(client, prop).status_code == 401  # no token
    assert _post(client, prop, {"Authorization": "Bearer tok-123"}).status_code == 200  # ok
