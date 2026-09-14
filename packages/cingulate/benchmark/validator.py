"""Result Validator — validates benchmark results.

Validates:
- Input/Output correctness
- State changes
- Persistence changes
- Response format
- Performance thresholds
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidationResult:
    """Result of a validation check."""

    check_name: str = ""
    passed: bool = False
    expected: Any = None
    actual: Any = None
    message: str = ""
    severity: str = "error"  # error | warning | info


class ResultValidator:
    """Validates benchmark results.

    Responsibilities:
    - Validate expected vs actual outputs
    - Check performance thresholds
    - Verify state consistency
    """

    def __init__(self):
        self._validations: list[ValidationResult] = []

    def validate_equal(self, name: str, expected: Any, actual: Any) -> ValidationResult:
        """Validate equality."""
        result = ValidationResult(
            check_name=name,
            expected=expected,
            actual=actual,
            passed=expected == actual,
            message=(
                f"Expected {expected}, got {actual}" if expected != actual else "OK"
            ),
        )
        self._validations.append(result)
        return result

    def validate_contains(
        self, name: str, haystack: Any, needle: Any
    ) -> ValidationResult:
        """Validate containment."""
        result = ValidationResult(
            check_name=name,
            expected=needle,
            actual=haystack,
            passed=needle in haystack if haystack else False,
            message=(
                f"Expected {needle} in {haystack}"
                if needle not in (haystack or [])
                else "OK"
            ),
        )
        self._validations.append(result)
        return result

    def validate_latency(
        self, name: str, latency_ms: float, max_ms: float
    ) -> ValidationResult:
        """Validate latency threshold."""
        result = ValidationResult(
            check_name=name,
            expected=f"< {max_ms}ms",
            actual=f"{latency_ms:.2f}ms",
            passed=latency_ms <= max_ms,
            message=(
                f"Latency {latency_ms:.2f}ms exceeds {max_ms}ms"
                if latency_ms > max_ms
                else "OK"
            ),
            severity="warning",
        )
        self._validations.append(result)
        return result

    def validate_success_rate(
        self, name: str, passed: int, total: int, min_rate: float = 0.9
    ) -> ValidationResult:
        """Validate success rate."""
        rate = passed / total if total else 0
        result = ValidationResult(
            check_name=name,
            expected=f">= {min_rate*100}%",
            actual=f"{rate*100:.1f}%",
            passed=rate >= min_rate,
            message=(
                f"Success rate {rate*100:.1f}% below {min_rate*100}%"
                if rate < min_rate
                else "OK"
            ),
        )
        self._validations.append(result)
        return result

    def get_results(self) -> list[ValidationResult]:
        return list(self._validations)

    def summary(self) -> dict:
        total = len(self._validations)
        passed = sum(1 for v in self._validations if v.passed)
        return {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / total, 3) if total else 0,
        }
