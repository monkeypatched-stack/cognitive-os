"""Deployment Readiness Checklist — Phase 5 Production Hardening.

Verifies system is ready for production deployment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Any
from enum import Enum

logger = logging.getLogger("agentos.deployment_readiness")


class CheckState(Enum):
    """State of a readiness check."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ReadinessCheck:
    """Single readiness check."""

    name: str
    category: str
    state: CheckState
    message: str = ""
    critical: bool = False


class DeploymentReadiness:
    """Verifies production readiness with comprehensive checklist."""

    def __init__(self) -> None:
        self.checks: list[ReadinessCheck] = []
        self.check_functions: dict[str, Callable] = {}

    def register_check(self, name: str, category: str, func: Callable, critical: bool = False) -> None:
        """Register a readiness check.

        Args:
            name: Check name
            category: Category (e.g., "database", "security")
            func: Function that returns (passed: bool, message: str)
            critical: Whether check must pass for deployment
        """
        self.check_functions[name] = (category, func, critical)
        logger.info(
            "[deployment_readiness] Registered check: %s (category=%s, critical=%s)",
            name,
            category,
            critical,
        )

    def run_checks(self) -> list[ReadinessCheck]:
        """Run all registered checks."""
        self.checks = []

        for name, (category, func, critical) in self.check_functions.items():
            try:
                passed, message = func()
                state = CheckState.PASSED if passed else CheckState.FAILED
                self.checks.append(
                    ReadinessCheck(
                        name=name,
                        category=category,
                        state=state,
                        message=message,
                        critical=critical,
                    )
                )
            except Exception as exc:
                self.checks.append(
                    ReadinessCheck(
                        name=name,
                        category=category,
                        state=CheckState.FAILED,
                        message=f"Check execution failed: {exc}",
                        critical=critical,
                    )
                )
                logger.error("[deployment_readiness] Check %s failed: %s", name, exc)

        return self.checks

    def is_production_ready(self) -> bool:
        """Check if system is production-ready."""
        if not self.checks:
            self.run_checks()

        # All critical checks must pass
        for check in self.checks:
            if check.critical and check.state != CheckState.PASSED:
                return False

        return True

    def summary(self) -> dict[str, Any]:
        """Get readiness summary."""
        if not self.checks:
            self.run_checks()

        by_category = {}
        for check in self.checks:
            if check.category not in by_category:
                by_category[check.category] = {"passed": 0, "warning": 0, "failed": 0}
            by_category[check.category][check.state.value] += 1

        failed_checks = [c for c in self.checks if c.state == CheckState.FAILED]
        critical_failures = [c for c in failed_checks if c.critical]

        return {
            "production_ready": self.is_production_ready(),
            "total_checks": len(self.checks),
            "passed": sum(1 for c in self.checks if c.state == CheckState.PASSED),
            "warnings": sum(1 for c in self.checks if c.state == CheckState.WARNING),
            "failed": len(failed_checks),
            "critical_failures": len(critical_failures),
            "by_category": by_category,
            "failed_checks": [{"name": c.name, "message": c.message, "critical": c.critical} for c in failed_checks],
        }


# Standard production readiness checks
def setup_standard_checks(readiness: DeploymentReadiness) -> None:
    """Setup standard production readiness checks."""

    # Database checks
    def check_database() -> tuple[bool, str]:
        """Verify database connection."""
        try:
            from src.monkey_brain.persistence.db_pool import get_db_pool

            db = get_db_pool()
            if db and db._pool:
                return True, "Database pool connected"
            return False, "Database pool not initialized"
        except Exception as e:
            return False, f"Database check failed: {e}"

    readiness.register_check("database_connection", "database", check_database, critical=True)

    # Scheduler checks
    def check_scheduler() -> tuple[bool, str]:
        """Verify scheduler initialized."""
        try:
            from src.monkey_brain.kernel.compile.scheduler_registry import (
                get_scheduler_registry,
            )

            registry = get_scheduler_registry()
            if registry.get_active():
                return True, "Scheduler active"
            return False, "No active scheduler"
        except Exception as e:
            return False, f"Scheduler check failed: {e}"

    readiness.register_check("scheduler_active", "scheduler", check_scheduler, critical=True)

    # Error recovery checks
    def check_error_recovery() -> tuple[bool, str]:
        """Verify error recovery registered."""
        try:
            from src.monkey_brain.kernel.compile.error_recovery import (
                get_error_recovery_registry,
            )

            registry = get_error_recovery_registry()
            if registry:
                return True, "Error recovery initialized"
            return False, "Error recovery not initialized"
        except Exception as e:
            return False, f"Error recovery check failed: {e}"

    readiness.register_check("error_recovery", "resilience", check_error_recovery, critical=True)

    # Metrics checks
    def check_metrics() -> tuple[bool, str]:
        """Verify metrics collection active."""
        try:
            from src.monkey_brain.kernel.compile.metrics import get_health_checker

            checker = get_health_checker()
            if checker:
                return True, "Metrics collection active"
            return False, "Metrics not initialized"
        except Exception as e:
            return False, f"Metrics check failed: {e}"

    readiness.register_check("metrics_collection", "monitoring", check_metrics, critical=False)

    # Degradation checks
    def check_degradation() -> tuple[bool, str]:
        """Verify graceful degradation configured."""
        try:
            from src.monkey_brain.kernel.compile.degradation import (
                get_degradation_manager,
            )

            manager = get_degradation_manager()
            if manager:
                return True, "Degradation manager active"
            return False, "Degradation manager not initialized"
        except Exception as e:
            return False, f"Degradation check failed: {e}"

    readiness.register_check("degradation_configured", "resilience", check_degradation, critical=False)

    # Multi-tenancy checks
    def check_multi_tenancy() -> tuple[bool, str]:
        """Verify multi-tenancy support."""
        try:
            from src.actor.tenant_middleware import TenantContext

            if TenantContext:
                return True, "Multi-tenancy middleware available"
            return False, "Multi-tenancy not configured"
        except Exception as e:
            return False, f"Multi-tenancy check failed: {e}"

    readiness.register_check("multi_tenancy", "security", check_multi_tenancy, critical=True)

    # Persistence checks
    def check_persistence() -> tuple[bool, str]:
        """Verify persistence layer ready."""
        try:
            from src.monkey_brain.persistence.actor_state_store import ActorStateStore
            from src.monkey_brain.persistence.db_pool import get_db_pool

            db = get_db_pool()
            store = ActorStateStore(db)
            if store:
                return True, "Persistence layer initialized"
            return False, "Persistence layer failed"
        except Exception as e:
            return False, f"Persistence check failed: {e}"

    readiness.register_check("persistence_layer", "database", check_persistence, critical=True)


# Singleton instance
_deployment_readiness: DeploymentReadiness | None = None


def get_deployment_readiness() -> DeploymentReadiness:
    """Get or create singleton deployment readiness checker."""
    global _deployment_readiness
    if _deployment_readiness is None:
        _deployment_readiness = DeploymentReadiness()
        setup_standard_checks(_deployment_readiness)
    return _deployment_readiness


def reset_deployment_readiness() -> None:
    """Reset deployment readiness (for testing)."""
    global _deployment_readiness
    _deployment_readiness = None
