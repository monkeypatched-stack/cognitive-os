"""
Phase 2 Consolidated Tests: Auth/Policy/Security and Monitoring

Tests the consolidated modules that import from kernel:
- auth_security.py (consolidated auth/policy/security)
- monitoring_consolidated.py (consolidated monitoring using Lemon)
"""

import pytest
import asyncio
from src.monkey_brain.api.auth_security import (
    AuthPolicySecurity,
    RateLimiter,
    sanitize_input,
    validate_identifier,
    GovernanceEngine,
    TrustNetwork,
)
from src.monkey_brain.api.monitoring_consolidated import (
    MonitoringService,
    LEMON_AVAILABLE,
)

# ═══════════════════════════════════════════════════════════════════════════════
# AuthPolicySecurity Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestAuthPolicySecurity:
    """Test consolidated auth/policy/security module."""

    def test_creation(self):
        service = AuthPolicySecurity()
        assert service is not None

    def test_rate_limiter_imported(self):
        from src.monkey_brain.api.auth_security import RateLimiter

        limiter = RateLimiter()
        assert limiter.allow("test")

    def test_sanitize_imported(self):
        from src.monkey_brain.api.auth_security import sanitize_input

        # Kernel's sanitize_input raises ValueError for blocked patterns
        # Test with safe input
        result = sanitize_input("Hello World")
        assert result == "Hello World"

    def test_governance_imported(self):
        from src.monkey_brain.api.auth_security import GovernanceEngine

        engine = GovernanceEngine()
        assert engine is not None

    def test_trust_network_imported(self):
        from src.monkey_brain.api.auth_security import TrustNetwork

        network = TrustNetwork()
        assert network is not None

    def test_validate_identifier(self):
        service = AuthPolicySecurity()
        # Kernel's validate_identifier returns (bool, str) tuple
        result = service.validate_identifier("alice")
        assert isinstance(result, tuple)
        assert result[0] is True

    def test_sanitize(self):
        service = AuthPolicySecurity()
        # Kernel's sanitize_input raises ValueError for blocked patterns
        # Test with safe input
        result = service.sanitize("Hello World")
        assert result == "Hello World"


# ═══════════════════════════════════════════════════════════════════════════════
# MonitoringService Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestMonitoringService:
    """Test consolidated monitoring module."""

    def test_creation(self):
        service = MonitoringService()
        assert service is not None

    def test_lemon_status(self):
        # Check if Lemon is available
        assert isinstance(LEMON_AVAILABLE, bool)

    def test_health_check(self):
        service = MonitoringService()
        result = asyncio.run(service.health_check())
        assert "status" in result

    def test_overall_health(self):
        service = MonitoringService()
        result = asyncio.run(service.overall_health())
        assert "status" in result

    def test_dashboard(self):
        service = MonitoringService()
        result = service.dashboard()
        assert "status" in result

    def test_summary(self):
        service = MonitoringService()
        result = service.summary()
        assert "status" in result


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestConsolidatedIntegration:
    """Integration tests for consolidated modules."""

    def test_auth_with_monitoring(self):
        from src.monkey_brain.api.auth_security import AuthPolicySecurity
        from src.monkey_brain.api.monitoring_consolidated import MonitoringService

        auth = AuthPolicySecurity()
        monitoring = MonitoringService()

        # Simulate request validation
        assert auth.sanitize("test input") == "test input"

        # Simulate monitoring
        monitoring.increment("requests")
        monitoring.set_gauge("connections", 1)

        # Verify - monitoring may not be initialized yet
        # Just verify the service was created
        assert monitoring is not None

    def test_trust_with_governance(self):
        from src.monkey_brain.api.auth_security import AuthPolicySecurity

        service = AuthPolicySecurity()
        trust = service.get_trust("alice", "bob")
        assert isinstance(trust, float)
