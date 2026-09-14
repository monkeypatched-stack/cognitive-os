"""Legal Compliance — GDPR, HIPAA, and regulatory compliance hooks.

Provides a framework for checking whether operations comply with
regulatory requirements.  NOT a legal advice module — it provides
hooks for policy enforcement, not legal interpretation.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("agentos.compliance")


class Regulation(str, Enum):
    GDPR = "gdpr"
    HIPAA = "hipaa"
    SOC2 = "soc2"
    ISO27001 = "iso27001"
    CCPA = "ccpa"
    CUSTOM = "custom"


@dataclass
class ComplianceRule:
    """A single compliance rule."""

    rule_id: str
    regulation: Regulation
    description: str
    check_fn: str = ""  # dotted path to check function
    severity: str = "high"  # critical | high | medium | low
    enabled: bool = True


@dataclass
class ComplianceResult:
    """Result of a compliance check."""

    rule_id: str
    regulation: str
    passed: bool
    message: str = ""
    timestamp: float = field(default_factory=time.time)


class RegulatoryComplianceEngine:
    """Evaluates operations against regulatory compliance rules.

    Provides hooks for GDPR (data minimization, right to erasure),
    HIPAA (PHI protection), SOC2 (audit trails), etc.
    """

    def __init__(self, max_results: int = 10_000) -> None:
        self._rules: dict[str, ComplianceRule] = {}
        # Retained results were UNBOUNDED — check() appends one per rule per call and nothing
        # ever trimmed the list. Bound the retained window; keep LIFETIME counters separately
        # so summary() stays accurate rather than reporting only the recent window.
        self._results: list[ComplianceResult] = []
        self._max_results = max_results
        self._total_checks = 0
        self._total_passed = 0
        self._register_defaults()

    def _register_defaults(self) -> None:
        """Register built-in compliance rules."""
        self.register(
            ComplianceRule(
                rule_id="gdpr_data_minimization",
                regulation=Regulation.GDPR,
                description="Only collect data necessary for the stated purpose",
                severity="high",
            )
        )
        self.register(
            ComplianceRule(
                rule_id="gdpr_right_to_erasure",
                regulation=Regulation.GDPR,
                description="Data must be erasable on request",
                severity="critical",
            )
        )
        self.register(
            ComplianceRule(
                rule_id="hipaa_phi_protection",
                regulation=Regulation.HIPAA,
                description="PHI must be encrypted at rest and in transit",
                severity="critical",
            )
        )
        self.register(
            ComplianceRule(
                rule_id="soc2_audit_trail",
                regulation=Regulation.SOC2,
                description="All actions must be auditable",
                severity="high",
            )
        )
        self.register(
            ComplianceRule(
                rule_id="soc2_access_control",
                regulation=Regulation.SOC2,
                description="Access must be authorized and logged",
                severity="high",
            )
        )

    def register(self, rule: ComplianceRule) -> None:
        self._rules[rule.rule_id] = rule

    def check(self, operation: str, context: dict[str, Any] | None = None) -> list[ComplianceResult]:
        """Check an operation against all applicable rules."""
        results = []
        for rule in self._rules.values():
            if not rule.enabled:
                continue
            passed = self._evaluate_rule(rule, operation, context or {})
            result = ComplianceResult(
                rule_id=rule.rule_id,
                regulation=rule.regulation.value,
                passed=passed,
                message=("" if passed else f"Operation '{operation}' violates {rule.rule_id}"),
            )
            results.append(result)
            self._results.append(result)
            self._total_checks += 1
            if passed:
                self._total_passed += 1
        if len(self._results) > self._max_results:
            self._results = self._results[-(self._max_results // 2) :]  # bounded retention
        return results

    def _evaluate_rule(self, rule: ComplianceRule, operation: str, context: dict[str, Any]) -> bool:
        """Evaluate a single rule.  Returns True if compliant."""
        # Built-in checks
        if rule.rule_id == "gdpr_data_minimization":
            # Check that no unnecessary data is being collected
            data_fields = context.get("data_fields", [])
            purpose = context.get("purpose", "")
            return len(data_fields) <= 10 or bool(purpose)  # simple heuristic

        if rule.rule_id == "gdpr_right_to_erasure":
            # Check that erasure is possible
            return context.get("erasure_possible", True)

        if rule.rule_id == "hipaa_phi_protection":
            # Check that PHI is encrypted
            has_phi = context.get("contains_phi", False)
            encrypted = context.get("encrypted", True)
            return not has_phi or encrypted

        if rule.rule_id == "soc2_audit_trail":
            # Check that action is auditable
            return context.get("audited", True)

        if rule.rule_id == "soc2_access_control":
            # Check that access is authorized
            return context.get("authorized", True)

        # Custom rules pass by default (need custom check_fn)
        return True

    def get_results(self, regulation: Regulation | None = None, limit: int = 100) -> list[ComplianceResult]:
        results = self._results
        if regulation:
            results = [r for r in results if r.regulation == regulation.value]
        return results[-limit:]

    def summary(self) -> dict[str, Any]:
        # Lifetime counters — NOT len(self._results), which is now a bounded window.
        total = self._total_checks
        passed = self._total_passed
        return {
            "total_checks": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / max(total, 1), 3),
            "rules_registered": len(self._rules),
            "retained_results": len(self._results),
        }


_default_engine: ComplianceEngine | None = None


def get_compliance_engine() -> ComplianceEngine:
    global _default_engine
    if _default_engine is None:
        _default_engine = ComplianceEngine()
    return _default_engine
