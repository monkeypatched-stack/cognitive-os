"""Live mTLS handshake — a real TLS server that REQUIRES a CA-issued client certificate.

Proves the SSL contexts enforce mutual auth over the wire (not just unit-level construction):
a client presenting a valid client cert connects; a client with no cert is rejected at the
TLS handshake.
"""

from __future__ import annotations

import datetime
import http.server
import ipaddress
import ssl
import threading
import urllib.request

import pytest

from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from src.monkey_brain.kernel.compile.network import (
    build_client_ssl_context,
    build_server_ssl_context,
)


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _write(path, key, cert):
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        + cert.public_bytes(serialization.Encoding.PEM)
    )
    return str(path)


def _ca():
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Root CA")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now() - datetime.timedelta(days=1))
        .not_valid_after(_now() + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _leaf(ca_key, ca_cert, cn, *, server, sans=None):
    key = ec.generate_private_key(ec.SECP256R1())
    b = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now() - datetime.timedelta(days=1))
        .not_valid_after(_now() + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([(ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH)]),
            critical=False,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
    )
    if sans:
        b = b.add_extension(x509.SubjectAlternativeName(sans), critical=False)
    return key, b.sign(ca_key, hashes.SHA256())


@pytest.fixture()
def mtls_server(tmp_path):
    ca_key, ca_cert = _ca()
    ca_path = tmp_path / "ca.pem"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    s_key, s_cert = _leaf(
        ca_key,
        ca_cert,
        "localhost",
        server=True,
        sans=[
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        ],
    )
    c_key, c_cert = _leaf(ca_key, ca_cert, "client-runtime", server=False)
    server_pem = _write(tmp_path / "server.pem", s_key, s_cert)
    client_pem = _write(tmp_path / "client.pem", c_key, c_cert)

    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *a):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), _H)
    ctx = build_server_ssl_context(server_pem, server_pem, str(ca_path))  # requires client cert
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield port, str(ca_path), client_pem
    finally:
        httpd.shutdown()


def test_client_with_cert_connects(mtls_server):
    port, ca_path, client_pem = mtls_server
    ctx = build_client_ssl_context(client_pem, client_pem, ca_path)
    with urllib.request.urlopen(f"https://localhost:{port}/", context=ctx, timeout=5) as r:
        assert r.status == 200


def test_client_without_cert_rejected(mtls_server):
    port, ca_path, _client_pem = mtls_server
    # trusts the server CA but presents NO client cert → server rejects at handshake
    noctx = ssl.create_default_context(cafile=ca_path)
    with pytest.raises(Exception):
        urllib.request.urlopen(f"https://localhost:{port}/", context=noctx, timeout=5)
