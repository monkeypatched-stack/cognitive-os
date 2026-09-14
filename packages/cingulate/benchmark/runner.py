"""Benchmark Runner — executes benchmarks and validates results.

Every benchmark executes against deterministic grounded data.
Expected outputs are known before execution.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class BenchmarkResult:
    """Result of a single benchmark execution."""

    benchmark_id: str = ""
    name: str = ""
    status: str = "pending"  # pending | passed | failed | error
    input_data: dict[str, Any] = field(default_factory=dict)
    expected_output: dict[str, Any] = field(default_factory=dict)
    actual_output: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: float = 0.0
    error: str | None = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_id": self.benchmark_id,
            "name": self.name,
            "status": self.status,
            "input": self.input_data,
            "expected": self.expected_output,
            "actual": self.actual_output,
            "metrics": self.metrics,
            "assertions": self.assertions,
            "latency_ms": round(self.latency_ms, 2),
            "error": self.error,
        }


class BenchmarkRunner:
    """Executes benchmarks and validates results.

    Responsibilities:
    - Execute benchmarks
    - Validate against expected outputs
    - Collect performance metrics
    - Generate reports
    """

    def __init__(self):
        self._results: list[BenchmarkResult] = []
        self._benchmarks: list[dict[str, Any]] = []

    def register(
        self, name: str, input_data: dict, expected: dict, validator: Any = None
    ) -> str:
        """Register a benchmark."""
        benchmark_id = f"bench-{uuid4().hex[:8]}"
        self._benchmarks.append(
            {
                "id": benchmark_id,
                "name": name,
                "input_data": input_data,
                "expected": expected,
                "validator": validator,
            }
        )
        return benchmark_id

    async def run(
        self, name: str, fn: Any, input_data: dict[str, Any], expected: dict[str, Any]
    ) -> BenchmarkResult:
        """Run a single benchmark."""
        start = time.monotonic()

        result = BenchmarkResult(
            benchmark_id=f"bench-{uuid4().hex[:8]}",
            name=name,
            input_data=input_data,
            expected_output=expected,
        )

        try:
            actual = await fn(input_data)
            result.actual_output = actual
            result.latency_ms = (time.monotonic() - start) * 1000

            # Validate
            assertions = self._validate(expected, actual)
            result.assertions = assertions

            all_passed = all(a["passed"] for a in assertions)
            result.status = "passed" if all_passed else "failed"

            result.metrics = {
                "latency_ms": result.latency_ms,
                "assertions_total": len(assertions),
                "assertions_passed": sum(1 for a in assertions if a["passed"]),
                "assertions_failed": sum(1 for a in assertions if not a["passed"]),
            }

        except Exception as e:
            result.status = "error"
            result.error = str(e)
            result.latency_ms = (time.monotonic() - start) * 1000

        self._results.append(result)
        return result

    def _validate(
        self, expected: dict[str, Any], actual: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Validate expected vs actual output."""
        assertions = []

        for key, expected_value in expected.items():
            actual_value = actual.get(key)

            if isinstance(expected_value, dict):
                # Deep comparison for nested dicts
                for sub_key, sub_expected in expected_value.items():
                    sub_actual = (
                        actual_value.get(sub_key)
                        if isinstance(actual_value, dict)
                        else None
                    )
                    passed = (
                        sub_actual == sub_expected if sub_expected is not None else True
                    )
                    assertions.append(
                        {
                            "field": f"{key}.{sub_key}",
                            "expected": sub_expected,
                            "actual": sub_actual,
                            "passed": passed,
                        }
                    )
            else:
                passed = (
                    actual_value == expected_value
                    if expected_value is not None
                    else True
                )
                assertions.append(
                    {
                        "field": key,
                        "expected": expected_value,
                        "actual": actual_value,
                        "passed": passed,
                    }
                )

        return assertions

    def get_results(self, status: str | None = None) -> list[BenchmarkResult]:
        if status:
            return [r for r in self._results if r.status == status]
        return list(self._results)

    def summary(self) -> dict:
        total = len(self._results)
        passed = sum(1 for r in self._results if r.status == "passed")
        failed = sum(1 for r in self._results if r.status == "failed")
        errors = sum(1 for r in self._results if r.status == "error")
        avg_latency = sum(r.latency_ms for r in self._results) / total if total else 0

        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "pass_rate": round(passed / total, 3) if total else 0,
            "avg_latency_ms": round(avg_latency, 2),
        }
