"""SPIRE workload API driver — cerebellum capability.

Owned here per INV-003. services/common/spire.py is a thin re-export.

Resolution order:
  1. pyspiffe Workload API over Unix socket
  2. SPIFFE_ID env var (dev override)
  3. None (caller falls back to token-based identity)
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_SOCKET = os.getenv("SPIFFE_ENDPOINT_SOCKET", "")
_STATIC_ID = os.getenv("SPIFFE_ID", "")


async def fetch_svid(socket_path: str = "", spiffe_id_override: str = "") -> dict[str, Any] | None:
    """Fetch the current X.509 SVID for this workload."""
    sock = socket_path or _SOCKET
    if sock:
        svid = await _try_pyspiffe(sock)
        if svid:
            return svid

    static = spiffe_id_override or _STATIC_ID
    if static:
        # SPIFFE/SPIRE Runtime Identity: this env-var override is an
        # explicit, narrow LOCAL-DEV convenience (no SPIRE Agent socket
        # available) -- the same shape as production_gates.py's own
        # insecure_dev_mode() escape hatch, and it must be gated by the
        # SAME two conditions that pattern already enforces everywhere
        # else in this codebase: require an explicit opt-in, and refuse
        # outright when production mode is also set, rather than silently
        # accepting a self-declared identity string in production. Before
        # this check existed, a leftover SPIFFE_ID env var from a dev
        # config would have been silently treated as a verified workload
        # identity in ANY environment, including production -- exactly
        # the self-asserted-identity failure mode this whole workload-
        # identity layer exists to close.
        try:
            from src.monkey_brain.kernel.production_gates import (
                insecure_dev_mode,
                production_mode_enabled,
            )

            if production_mode_enabled():
                logger.error(
                    "SPIFFE_ID env override is set but COGNITIVEOS_PRODUCTION_MODE "
                    "is also set -- refusing to treat a self-declared identity "
                    "string as a verified workload identity in production."
                )
                return None
            if not insecure_dev_mode():
                logger.error(
                    "SPIFFE_ID env override is set but COGNITIVEOS_ALLOW_INSECURE_DEV_MODE "
                    "is not -- refusing an unverified identity fallback outside explicit local dev."
                )
                return None
        except Exception:
            # production_gates unavailable (e.g. this package used outside
            # monkey_brain entirely) -- fail closed, same convention as
            # every other production_gates-guarded check in this codebase
            # when the gate itself cannot be evaluated.
            logger.error("SPIFFE_ID env override set but production_gates unavailable to verify dev-mode -- refusing.")
            return None
        logger.warning(
            "Using SPIFFE_ID env override (%r) instead of a real SPIRE Workload "
            "API SVID -- local development only, never valid in production.",
            static,
        )
        return {
            "spiffe_id": static,
            "source": "env",
            "cert_pem": None,
            "key_pem": None,
            "bundle_pem": None,
        }

    return None


async def _try_pyspiffe(socket_path: str) -> dict[str, Any] | None:
    try:
        from pyspiffe.workloadapi.default_workload_api_client import (
            DefaultWorkloadApiClient,
        )
        import asyncio

        loop = asyncio.get_event_loop()

        def _get():
            with DefaultWorkloadApiClient(spiffe_socket=socket_path) as client:
                ctx = client.fetch_x509_context()
                return ctx.default_svid()

        result = await loop.run_in_executor(None, _get)
        return {
            "spiffe_id": str(result.spiffe_id),
            "cert_pem": result.cert_chain_pem.decode(),
            "key_pem": result.private_key_pem.decode(),
            "bundle_pem": None,
            "source": "spire",
        }
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("pyspiffe fetch failed: %s", exc)
    return None


def spiffe_id_from_svid(svid: dict[str, Any] | None) -> str | None:
    return (svid or {}).get("spiffe_id")
