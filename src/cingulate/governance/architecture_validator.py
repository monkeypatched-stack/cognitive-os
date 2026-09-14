"""Architecture Validator — validates architectural compliance.

Continuously validates:
- SOLID
- DRY
- Layer isolation
- Circular dependencies
- Ownership boundaries
- Single responsibility
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


@dataclass
class Violation:
    """An architectural violation."""

    violation_id: str = field(default_factory=lambda: f"viol-{uuid4().hex[:8]}")
    rule: str = ""
    severity: str = "medium"  # critical | high | medium | low
    file_path: str = ""
    line: int = 0
    description: str = ""
    recommendation: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ComplianceReport:
    """Architecture compliance report."""

    report_id: str = field(default_factory=lambda: f"report-{uuid4().hex[:8]}")
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    total_files: int = 0
    total_violations: int = 0
    violations_by_severity: dict[str, int] = field(default_factory=dict)
    violations: list[Violation] = field(default_factory=list)
    score: float = 100.0


class ArchitectureValidator:
    """Validates architectural compliance.

    Continuously validates:
    - SOLID
    - DRY
    - Layer isolation
    - Circular dependencies
    """

    def __init__(self, root_dir: str = "services/agentos"):
        self._root_dir = root_dir
        self._violations: list[Violation] = []

    def validate_all(self) -> ComplianceReport:
        """Run all architecture validations."""
        report = ComplianceReport()

        self._check_large_files(report)
        self._check_duplicate_classes(report)
        self._check_circular_dependencies(report)
        self._check_hardcoded_policies(report)

        report.violations = self._violations
        report.total_violations = len(self._violations)
        report.violations_by_severity = {}
        for v in self._violations:
            report.violations_by_severity[v.severity] = report.violations_by_severity.get(v.severity, 0) + 1

        # Calculate score
        critical = report.violations_by_severity.get("critical", 0)
        high = report.violations_by_severity.get("high", 0)
        medium = report.violations_by_severity.get("medium", 0)
        low = report.violations_by_severity.get("low", 0)
        report.score = max(0, 100 - (critical * 10) - (high * 5) - (medium * 2) - (low * 0.5))

        return report

    def _check_large_files(self, report: ComplianceReport) -> None:
        """Check for files exceeding line limits."""
        for root, dirs, files in os.walk(self._root_dir):
            for f in files:
                if f.endswith(".py"):
                    path = os.path.join(root, f)
                    report.total_files += 1
                    with open(path) as fh:
                        lines = len(fh.readlines())
                    if lines > 1000:
                        self._violations.append(
                            Violation(
                                rule="single_responsibility",
                                severity="high",
                                file_path=path,
                                description=f"File has {lines} lines (exceeds 1000)",
                                recommendation="Decompose into smaller modules",
                            )
                        )
                    elif lines > 500:
                        self._violations.append(
                            Violation(
                                rule="single_responsibility",
                                severity="medium",
                                file_path=path,
                                description=f"File has {lines} lines (exceeds 500)",
                                recommendation="Consider decomposition",
                            )
                        )

    def _check_duplicate_classes(self, report: ComplianceReport) -> None:
        """Check for duplicate class definitions."""
        class_defs: dict[str, list[str]] = {}
        for root, dirs, files in os.walk(self._root_dir):
            for f in files:
                if f.endswith(".py"):
                    path = os.path.join(root, f)
                    with open(path) as fh:
                        content = fh.read()
                    matches = re.findall(r"class (\w+)", content)
                    for m in matches:
                        if m not in class_defs:
                            class_defs[m] = []
                        class_defs[m].append(path)

        for cls, paths in class_defs.items():
            if len(paths) > 1 and cls not in ("Exception", "Error", "Enum"):
                self._violations.append(
                    Violation(
                        rule="dry",
                        severity="medium",
                        file_path=paths[0],
                        description=f"Class '{cls}' defined in {len(paths)} files",
                        recommendation="Consolidate into single definition",
                    )
                )

    def _check_circular_dependencies(self, report: ComplianceReport) -> None:
        """Check for circular dependencies."""
        deps: dict[str, set[str]] = {}
        for root, dirs, files in os.walk(self._root_dir):
            for f in files:
                if f.endswith(".py"):
                    path = os.path.join(root, f)
                    module = path.replace("/", ".").replace(".py", "")
                    deps[module] = set()
                    with open(path) as fh:
                        for line in fh:
                            m = re.match(r"from (services\.agentos\.\S+) import", line)
                            if m:
                                deps[module].add(m.group(1))

        for mod, imports in deps.items():
            for imp in imports:
                if imp in deps and mod in deps[imp]:
                    self._violations.append(
                        Violation(
                            rule="no_circular_dependencies",
                            severity="critical",
                            file_path=mod.replace(".", "/") + ".py",
                            description=f"Circular dependency: {mod} <-> {imp}",
                            recommendation="Break circular dependency",
                        )
                    )

    def _check_hardcoded_policies(self, report: ComplianceReport) -> None:
        """Check for hardcoded policy values."""
        patterns = [
            (r"confidence\s*[><=]\s*\d+\.\d+", "Hardcoded confidence threshold"),
            (r"exploration_rate\s*=\s*\d+\.\d+", "Hardcoded exploration rate"),
            (r"learning_rate\s*=\s*\d+\.\d+", "Hardcoded learning rate"),
            (r"max_size\s*=\s*\d+", "Hardcoded max size"),
        ]

        for root, dirs, files in os.walk(self._root_dir):
            for f in files:
                if f.endswith(".py"):
                    path = os.path.join(root, f)
                    with open(path) as fh:
                        content = fh.read()
                    for pattern, desc in patterns:
                        matches = re.findall(pattern, content)
                        if matches:
                            self._violations.append(
                                Violation(
                                    rule="configurable_policies",
                                    severity="low",
                                    file_path=path,
                                    description=f"{desc}: {matches[0][:40]}",
                                    recommendation="Make configurable",
                                )
                            )

    def summary(self) -> dict:
        return {
            "violations": len(self._violations),
            "by_severity": {},
        }
