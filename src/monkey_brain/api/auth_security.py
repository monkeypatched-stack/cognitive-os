"""Consolidated Auth/Policy/Security Module — Single source of truth.

This module consolidates all authN, authZ, policy, and security functionality
by importing from the kernel's existing implementations.

DO NOT create new implementations here. Import from:
- kernel/identity.py (Ed25519 cryptographic identity)
- kernel/security.py (rate limiting, sanitization)
- kernel/governance.py (policy evaluation)
- kernel/compile/trust.py (trust network)
- services/auth/ (JWT, RBAC, MFA)
- services/common/opa.py (OPA policy)
- services/common/agent_auth.py (zero-trust principal)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agentos.auth_security")


# ═══════════════════════════════════════════════════════════════════════════════
# Re-export from kernel (DO NOT duplicate)
# ═══════════════════════════════════════════════════════════════════════════════

# Security
from src.monkey_brain.kernel.security import (
    RateLimiter,
    sanitize_input,
    validate_identifier,
)

# Governance
from src.monkey_brain.kernel.governance import (
    GovernanceEngine,
)

# Trust
from src.monkey_brain.kernel.compile.trust import (
    TrustNetwork,
)

# Identity
from src.monkey_brain.kernel.identity import (
    RuntimeIdentity,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Unified Auth/Policy/Security Interface
# ═══════════════════════════════════════════════════════════════════════════════


class AuthPolicySecurity:
    """Unified interface for authN, authZ, policy, and security.

    Composes existing kernel components into a single entry point.
    """

    def __init__(self):
        self._governance = GovernanceEngine()
        self._trust_network = TrustNetwork()
        self._rate_limiter = RateLimiter()
        self._identity: RuntimeIdentity | None = None

    async def initialize(self) -> None:
        """Initialize all components."""
        try:
            self._identity = RuntimeIdentity()
            logger.info("AuthPolicySecurity initialized")
        except Exception as e:
            logger.warning("AuthPolicySecurity initialization degraded: %s", e)

    # ── Authentication ──────────────────────────────────────────

    def validate_request(self, request: Any) -> tuple[bool, str]:
        """Validate incoming request."""
        # Rate limiting
        client_ip = getattr(request, "client", None)
        ip = client_ip.host if client_ip else "unknown"
        if not self._rate_limiter.allow(ip):
            return False, "Rate limit exceeded"

        return True, "OK"

    # ── Authorization ───────────────────────────────────────────

    def check_permission(self, principal: str, resource: str, action: str) -> bool:
        """Check if principal has permission for action on resource."""
        return self._governance.evaluate(principal, resource, action)

    def get_trust(self, source: str, target: str) -> float:
        """Get trust level between two entities."""
        return self._trust_network.trust(source, target)

    # ── Policy ──────────────────────────────────────────────────

    def evaluate_policy(self, context: dict[str, Any]) -> dict[str, Any]:
        """Evaluate policy for a given context."""
        return self._governance.evaluate(context)

    # ── Security ────────────────────────────────────────────────

    def sanitize(self, value: str) -> str:
        """Sanitize input."""
        return sanitize_input(value)

    def validate_identifier(self, identifier: str) -> bool:
        """Validate identifier format."""
        return validate_identifier(identifier)


# ═══════════════════════════════════════════════════════════════════════════════
# Gaps identified (to be implemented)
# ═══════════════════════════════════════════════════════════════════════════════

# 1. KnowledgeGraph → ContextEventStore wiring
#    Status: NOT WIRED
#    Action: Add on_publish callback to KnowledgeGraph that emits to ContextEventStore

# 2. Integration tests (HTTP calls to server)
#    Status: NOT IMPLEMENTED
#    Action: Add tests that make actual HTTP requests to running server

# 3. Profile/Account → ActorStateStore wiring
#    Status: NOT WIRED
#    Action: Add save/load methods that use ActorStateStore
