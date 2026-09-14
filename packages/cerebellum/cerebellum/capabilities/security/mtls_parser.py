"""mTLS cert parser — cerebellum capability.

Owned here per INV-003. services/common/mtls.py is a thin re-export.

Parses PEM-encoded X.509 client certificates forwarded by TLS terminators
(nginx, envoy, ALB) via X-Client-Cert / X-SSL-Client-Cert / X-Forwarded-Client-Cert.
Optionally validates the cert against a CA bundle.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import unquote
from typing import Any

logger = logging.getLogger(__name__)

_CA_CERT_PATH = os.getenv("MTLS_CA_CERT_PATH", "")
_CERT_HEADERS = ("X-Client-Cert", "X-SSL-Client-Cert", "X-Forwarded-Client-Cert")


def extract_cert_header(headers: dict[str, str]) -> str | None:
    """Return the first non-empty cert header value, URL-decoded."""
    for h in _CERT_HEADERS:
        val = headers.get(h) or headers.get(h.lower())
        if val:
            decoded = unquote(val)
            if decoded.startswith("-----BEGIN"):
                return decoded
    return None


def parse_pem(pem: str) -> dict[str, Any] | None:
    """Parse a PEM cert into a metadata dict. Returns None on failure."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes

        cert = x509.load_pem_x509_certificate(pem.encode())
        san_list: list[str] = []
        try:
            ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
            for name in ext.value:
                san_list.append(str(name.value))
        except x509.ExtensionNotFound:
            pass
        return {
            "subject": cert.subject.rfc4514_string(),
            "issuer": cert.issuer.rfc4514_string(),
            "san": san_list,
            "fingerprint": cert.fingerprint(hashes.SHA256()).hex(),
            "not_valid_before": cert.not_valid_before_utc.isoformat(),
            "not_valid_after": cert.not_valid_after_utc.isoformat(),
            "_raw": cert,
        }
    except Exception as exc:
        logger.debug("Failed to parse client cert PEM: %s", exc)
        return None


def ca_valid(cert_info: dict[str, Any], ca_path: str = "") -> bool:
    """Validate cert against a CA cert file. Returns True when no CA is configured."""
    path = ca_path or _CA_CERT_PATH
    if not path:
        return True
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives.asymmetric import padding

        with open(path, "rb") as f:
            ca = x509.load_pem_x509_certificate(f.read())
        client = cert_info["_raw"]
        ca.public_key().verify(
            client.signature,
            client.tbs_certificate_bytes,
            padding.PKCS1v15(),
            client.signature_hash_algorithm,
        )
        return True
    except Exception as exc:
        logger.warning("Client cert CA validation failed: %s", exc)
        return False


def process_request_cert(headers: dict[str, str]) -> dict[str, Any] | None:
    """Full pipeline: extract header → parse PEM → return cert_info or None."""
    pem = extract_cert_header(headers)
    if not pem:
        return None
    return parse_pem(pem)
