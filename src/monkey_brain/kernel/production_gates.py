"""Security gates — enabled by default; opt *out* only for explicit local-dev.

COGNITIVEOS_PRODUCTION_MODE may still strengthen operational posture, but it is
not the switch that turns fundamental security on.

Unsafe local-dev relaxations require COGNITIVEOS_ALLOW_INSECURE_DEV_MODE=true
and are rejected when production mode is also set.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("agentos.production_gates")
_INSECURE_DEV_WARNED = False


def _raw(name: str) -> str:
    return os.getenv(name, "").strip().lower()


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("true", "1", "yes", "on")


def _falsey_explicit(name: str) -> bool:
    return _raw(name) in ("false", "0", "no", "off")


def production_mode_enabled() -> bool:
    return _truthy("COGNITIVEOS_PRODUCTION_MODE")


def insecure_dev_mode() -> bool:
    """Narrow, explicit local-dev bypass. Never combines with production mode."""
    global _INSECURE_DEV_WARNED
    requested = _truthy("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE")
    if requested and production_mode_enabled():
        raise RuntimeError("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE cannot be combined with COGNITIVEOS_PRODUCTION_MODE")
    if requested and not _INSECURE_DEV_WARNED:
        _INSECURE_DEV_WARNED = True
        logger.warning(
            "COGNITIVEOS_ALLOW_INSECURE_DEV_MODE is enabled — security-critical "
            "controls (OPA required, Redis required, fail-closed idempotency, "
            "world mutation block, MFA) are relaxed. Never use this outside "
            "local development."
        )
    return requested


def require_redis() -> bool:
    if insecure_dev_mode():
        return _truthy("REQUIRE_REDIS")
    return True


def require_opa() -> bool:
    if insecure_dev_mode():
        return _truthy("OPA_REQUIRED")
    return True


def idempotency_fail_closed() -> bool:
    if insecure_dev_mode():
        return _truthy("IDEMPOTENCY_FAIL_CLOSED")
    return True


def block_direct_world_api_mutations() -> bool:
    """Reject SharedWorld CRUD mutations except in explicit insecure local-dev.

    ALLOW_DIRECT_WORLD_API is honored only together with insecure-dev mode and
    never when production mode is on.
    """
    if production_mode_enabled():
        return True
    if insecure_dev_mode() and _truthy("ALLOW_DIRECT_WORLD_API"):
        return False
    return not insecure_dev_mode()


def capability_dispatch_dedup_enabled() -> bool:
    if insecure_dev_mode():
        return _truthy("CAPABILITY_DISPATCH_DEDUP")
    return True


def mfa_required() -> bool:
    """Platform MFA requirement. Default on; only insecure-dev may disable."""
    if insecure_dev_mode() and _falsey_explicit("COGNITIVEOS_MFA_REQUIRED"):
        return False
    if insecure_dev_mode() and not os.getenv("COGNITIVEOS_MFA_REQUIRED"):
        return False
    return not _falsey_explicit("COGNITIVEOS_MFA_REQUIRED")


def opa_enforce_execution() -> bool:
    """Execution-layer OPA (GoalExecutor._authorize). Default on."""
    if insecure_dev_mode() and not _truthy("AGENTOS_OPA_ENFORCE"):
        return False
    if _falsey_explicit("AGENTOS_OPA_ENFORCE") and insecure_dev_mode():
        return False
    return True


def validate_production_gates(*, redis_available: bool, opa_configured: bool) -> None:
    """Raise RuntimeError when a required security dependency is missing."""
    # Combining flags is always illegal, even if individual gates would pass.
    if _truthy("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE") and production_mode_enabled():
        raise RuntimeError("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE cannot be combined with COGNITIVEOS_PRODUCTION_MODE")
    errors: list[str] = []
    if require_redis() and not redis_available:
        errors.append("Redis is mandatory but unavailable")
    if require_opa() and not opa_configured:
        errors.append("OPA_URL must be set for governance")
    if _falsey_explicit("AGENTOS_AUTH_REQUIRED") and not insecure_dev_mode():
        errors.append("AGENTOS_AUTH_REQUIRED=false requires COGNITIVEOS_ALLOW_INSECURE_DEV_MODE")
    if errors:
        raise RuntimeError("; ".join(errors))
