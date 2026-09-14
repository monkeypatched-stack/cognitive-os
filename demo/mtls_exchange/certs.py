"""Generate the mTLS material for the demo: a CA plus a server and a client cert.

These are the TRANSPORT certificates (x509) that mutually authenticate the two runtime
processes at the TLS layer. They are separate from the exchange's Ed25519 CA that signs the
knowledge proposals themselves — the demo exercises both layers.
"""

from __future__ import annotations

import datetime
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _ca():
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MonkeyBrain Demo Root CA")])
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


def _write_cert(path: Path, cert):
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _write_key(path: Path, key):
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


def generate(out_dir: str) -> dict:
    """Write ca.crt, server.crt/key, client.crt/key into out_dir; return the paths."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    ca_key, ca_cert = _ca()
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
    c_key, c_cert = _leaf(ca_key, ca_cert, "sender-runtime", server=False)
    paths = {
        "ca": d / "ca.crt",
        "server_cert": d / "server.crt",
        "server_key": d / "server.key",
        "client_cert": d / "client.crt",
        "client_key": d / "client.key",
    }
    _write_cert(paths["ca"], ca_cert)
    _write_cert(paths["server_cert"], s_cert)
    _write_key(paths["server_key"], s_key)
    _write_cert(paths["client_cert"], c_cert)
    _write_key(paths["client_key"], c_key)
    return {k: str(v) for k, v in paths.items()}


if __name__ == "__main__":
    import sys

    print(generate(sys.argv[1] if len(sys.argv) > 1 else "./certs"))
