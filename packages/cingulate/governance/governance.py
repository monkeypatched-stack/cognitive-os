"""Governance Engine — constitutional authority of AgentOS.

Every subsystem is governed.
No subsystem is exempt.
"""

from __future__ import annotations

from typing import Any

from cingulate.governance.policy_registry import PolicyRegistry, Policy, PolicyCategory
from cingulate.governance.architecture_validator import (
    ArchitectureValidator,
    ComplianceReport as ArchReport,
)
from cingulate.governance.compliance import ComplianceEngine, ComplianceReport


class Governance:
    """Constitutional authority of AgentOS.

    Responsibilities:
    - Policy management
    - Architecture validation
    - Compliance checking
    - Audit trail
    """

    def __init__(self):
        self.policies = PolicyRegistry()
        self.architecture = ArchitectureValidator()
        self.compliance = ComplianceEngine()
        self._lemon = None

    def set_lemon(self, lemon) -> None:
        self._lemon = lemon

    # --- Policy Management ---

    def register_policy(self, policy: Policy) -> str:
        """Register a governance policy."""
        policy_id = self.policies.register(policy)
        if self._lemon:
            self._lemon.counter("governance.policies_registered")
        return policy_id

    def get_policy(self, policy_id: str) -> Policy | None:
        return self.policies.get(policy_id)

    def get_policies_by_category(self, category: PolicyCategory) -> list[Policy]:
        return self.policies.get_by_category(category)

    # --- Architecture Validation ---

    def validate_architecture(self) -> ArchReport:
        """Run architecture compliance audit."""
        report = self.architecture.validate_all()
        if self._lemon:
            self._lemon.counter("governance.architecture_audits")
            self._lemon.gauge("governance.violations", report.total_violations)
        return report

    # --- Compliance ---

    def check_compliance(
        self, subsystem: str, rule: str, passed: bool, message: str = ""
    ) -> Any:
        """Run a compliance check."""
        return self.compliance.check(subsystem, rule, passed, message)

    def validate_subsystem(
        self, subsystem: str, rules: list[dict[str, Any]]
    ) -> ComplianceReport:
        """Validate a subsystem against rules."""
        return self.compliance.validate_subsystem(subsystem, rules)

    # --- Summary ---

    def summary(self) -> dict:
        return {
            "policies": self.policies.summary(),
            "architecture": self.architecture.summary(),
            "compliance": self.compliance.summary(),
        }
