"""Compliance Engine — validates compliance with policies and constitutions.

Validates:
- Constitutional compliance
- Policy compliance
- Security compliance
- Data compliance
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class ComplianceCheck:
    """A single compliance check result."""

    check_id: str = field(default_factory=lambda: f"check-{uuid4().hex[:8]}")
    policy_id: str = ""
    subsystem: str = ""
    rule: str = ""
    passed: bool = False
    message: str = ""
    severity: str = "medium"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ComplianceReport:
    """Compliance report for a subsystem."""

    report_id: str = field(default_factory=lambda: f"comp-{uuid4().hex[:8]}")
    subsystem: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    checks: list[ComplianceCheck] = field(default_factory=list)
    total_checks: int = 0
    passed_checks: int = 0
    failed_checks: int = 0
    compliance_score: float = 0.0


class ComplianceEngine:
    """Validates compliance with policies and constitutions.

    Responsibilities:
    - Validate constitutional compliance
    - Validate policy compliance
    - Generate compliance reports
    """

    def __init__(self):
        self._checks: list[ComplianceCheck] = []
        self._reports: list[ComplianceReport] = []

    def check(
        self,
        subsystem: str,
        rule: str,
        passed: bool,
        message: str = "",
        severity: str = "medium",
    ) -> ComplianceCheck:
        """Run a compliance check."""
        check = ComplianceCheck(
            subsystem=subsystem,
            rule=rule,
            passed=passed,
            message=message,
            severity=severity,
        )
        self._checks.append(check)
        return check

    def validate_subsystem(self, subsystem: str, rules: list[dict[str, Any]]) -> ComplianceReport:
        """Validate a subsystem against a set of rules."""
        report = ComplianceReport(subsystem=subsystem)

        for rule in rules:
            check = self.check(
                subsystem=subsystem,
                rule=rule.get("name", ""),
                passed=rule.get("passed", False),
                message=rule.get("message", ""),
                severity=rule.get("severity", "medium"),
            )
            report.checks.append(check)

        report.total_checks = len(report.checks)
        report.passed_checks = sum(1 for c in report.checks if c.passed)
        report.failed_checks = report.total_checks - report.passed_checks
        report.compliance_score = report.passed_checks / report.total_checks if report.total_checks else 0.0

        self._reports.append(report)
        return report

    def get_reports(self, subsystem: str | None = None) -> list[ComplianceReport]:
        if subsystem:
            return [r for r in self._reports if r.subsystem == subsystem]
        return list(self._reports)

    def summary(self) -> dict:
        return {
            "total_checks": len(self._checks),
            "passed": sum(1 for c in self._checks if c.passed),
            "failed": sum(1 for c in self._checks if not c.passed),
            "reports": len(self._reports),
        }
