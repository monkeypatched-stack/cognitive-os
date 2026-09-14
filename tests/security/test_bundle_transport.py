"""Bundle transport — publish endpoint + background refresher.

The authority serves its signed bundle at /trust/bundle; a remote verifier's BundleRefresher
fetches and loads it into a TrustStore, so revocations converge across hosts over the wire.
"""

from __future__ import annotations

from src.monkey_brain.kernel.identity import KeyManager
from src.monkey_brain.kernel.ca import CertificateAuthority, TrustStore, verify_bundle
from src.monkey_brain.kernel.compile import KnowledgeExchange, TrustNetwork
from src.monkey_brain.kernel.compile.network import ExchangeServer, BundleRefresher


def _server_app(ca):
    from fastapi import FastAPI

    ex = KnowledgeExchange(TrustNetwork(), ca=ca, ca_public_pem=ca.ca_public_pem())
    server = ExchangeServer(ex, tenant_id="default", ca=ca)
    app = FastAPI()
    app.include_router(server.create_router(), prefix="/api/v1/agentos")
    return app


def test_trust_bundle_endpoint_serves_loadable_bundle(tmp_path):
    from fastapi.testclient import TestClient

    km = KeyManager(key_dir=str(tmp_path / "k"))
    ca = CertificateAuthority("authority:root", key_manager=km)
    client = TestClient(_server_app(ca))
    r = client.get("/api/v1/agentos/trust/bundle")
    assert r.status_code == 200
    bundle = r.json()
    assert verify_bundle(bundle, ca.ca_public_pem())
    assert TrustStore().load_bundle(bundle, ca.ca_public_pem())


def test_bundle_refresher_converges_store(tmp_path):
    km = KeyManager(key_dir=str(tmp_path / "k"))
    root = CertificateAuthority("authority:root", key_manager=km)
    com = root.certify_ca(CertificateAuthority("authority:community", key_manager=km))
    leaf = com.issue("alice", km.get_public_key_pem("alice"))

    # a fetcher standing in for the HTTP GET against /trust/bundle
    class _FakeClient:
        def fetch(self, url):
            return root.export_bundle()

    store = TrustStore()
    refresher = BundleRefresher(
        store,
        [("http://root/trust/bundle", root.ca_public_pem())],
        client=_FakeClient(),
    )
    assert refresher.refresh_once() == 1
    assert leaf.serial_number not in store.crl()  # nothing revoked yet

    com.revoke(leaf.cert_id)  # revoke deep in the tree
    assert refresher.refresh_once() == 1  # republish + refetch
    assert leaf.serial_number in store.crl()  # remote store converged


def test_refresher_ignores_bundle_from_wrong_authority(tmp_path):
    km = KeyManager(key_dir=str(tmp_path / "k"))
    root = CertificateAuthority("authority:root", key_manager=km)
    impostor = CertificateAuthority("authority:impostor", key_manager=km)

    class _FakeClient:
        def fetch(self, url):
            return impostor.export_bundle()  # signed by the wrong key

    store = TrustStore()
    # anchor is root, but the served bundle is signed by impostor → rejected
    refresher = BundleRefresher(store, [("http://x", root.ca_public_pem())], client=_FakeClient())
    assert refresher.refresh_once() == 0
