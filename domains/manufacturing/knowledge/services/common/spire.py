"""SPIRE client — thin re-export of cerebellum.capabilities.security.spire_client (INV-003)."""

from __future__ import annotations

from typing import Any

try:
    from cerebellum.capabilities.security.spire_client import (  # noqa: F401
        fetch_svid,
        spiffe_id_from_svid,
    )
except ImportError:
    import os as _os

    _SOCKET = _os.getenv("SPIFFE_ENDPOINT_SOCKET", "")
    _STATIC_ID = _os.getenv("SPIFFE_ID", "")

    async def fetch_svid() -> dict[str, Any] | None:  # type: ignore[misc]
        # Mirrors cerebellum.capabilities.security.spire_client.fetch_svid's
        # own production/insecure-dev guard on this exact fallback -- see
        # that module for why an unguarded SPIFFE_ID env var is a real
        # self-asserted-identity risk in production.
        if _STATIC_ID:
            try:
                from src.monkey_brain.kernel.production_gates import (
                    insecure_dev_mode,
                    production_mode_enabled,
                )

                if production_mode_enabled() or not insecure_dev_mode():
                    return None
            except Exception:
                return None
            return {
                "spiffe_id": _STATIC_ID,
                "source": "env",
                "cert_pem": None,
                "key_pem": None,
                "bundle_pem": None,
            }
        return None

    def spiffe_id_from_svid(svid: dict[str, Any] | None) -> str | None:  # type: ignore[misc]
        return (svid or {}).get("spiffe_id")
