"""Test Runner — executes tests and collects results.

Supports:
- Unit tests
- Integration tests
- End-to-end tests
- Regression tests
- Performance tests
"""

from __future__ import annotations

import logging

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable
from uuid import uuid4

logger = logging.getLogger("plasticity.testing.runner")


class TestType(str, Enum):
    UNIT = "unit"
    INTEGRATION = "integration"
    E2E = "e2e"
    REGRESSION = "regression"
    PERFORMANCE = "performance"
    STRESS = "stress"


class TestStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class TestCase:
    """A single test case."""

    test_id: str = field(default_factory=lambda: f"test-{uuid4().hex[:8]}")
    name: str = ""
    test_type: TestType = TestType.UNIT
    description: str = ""
    function: Any = None
    input_data: dict[str, Any] = field(default_factory=dict)
    expected_output: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    timeout_ms: float = 5000.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TestResult:
    """Result of a single test."""

    test_id: str = ""
    name: str = ""
    status: TestStatus = TestStatus.PENDING
    latency_ms: float = 0.0
    error: str | None = None
    assertions: list[dict[str, Any]] = field(default_factory=list)
    output: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class TestSuiteResult:
    """Result of a test suite execution."""

    suite_id: str = field(default_factory=lambda: f"suite-{uuid4().hex[:8]}")
    name: str = ""
    results: list[TestResult] = field(default_factory=list)
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    total_latency_ms: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


class TestRunner:
    """Executes tests and collects results.

    Supports:
    - Unit tests
    - Integration tests
    - End-to-end tests
    - Regression tests
    - Performance tests
    """

    def __init__(self):
        self._test_cases: list[TestCase] = []
        self._results: list[TestResult] = []
        self._suites: list[TestSuiteResult] = []
        self._lemon = None

    def set_lemon(self, lemon) -> None:
        self._lemon = lemon

    def register(
        self,
        name: str,
        function: Callable,
        test_type: TestType = TestType.UNIT,
        input_data: dict[str, Any] | None = None,
        expected_output: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Register a test case."""
        test = TestCase(
            name=name,
            test_type=test_type,
            function=function,
            input_data=input_data or {},
            expected_output=expected_output or {},
            tags=tags or [],
        )
        self._test_cases.append(test)
        return test.test_id

    async def run(self, test: TestCase) -> TestResult:
        """Run a single test."""
        result = TestResult(test_id=test.test_id, name=test.name)

        start = time.monotonic()

        try:
            if test.function:
                if asyncio.iscoroutinefunction(test.function):
                    output = await test.function(test.input_data)
                else:
                    output = test.function(test.input_data)
                result.output = output if isinstance(output, dict) else {"result": output}

            result.status = TestStatus.PASSED
            result.latency_ms = (time.monotonic() - start) * 1000

            # Validate expected output
            if test.expected_output:
                for key, expected in test.expected_output.items():
                    actual = result.output.get(key)
                    passed = actual == expected
                    result.assertions.append(
                        {
                            "field": key,
                            "expected": expected,
                            "actual": actual,
                            "passed": passed,
                        }
                    )
                    if not passed:
                        result.status = TestStatus.FAILED

        except Exception as e:
            result.status = TestStatus.ERROR
            result.error = str(e)
            result.latency_ms = (time.monotonic() - start) * 1000

        self._results.append(result)

        if self._lemon:
            self._lemon.counter("testing.tests_run")
            self._lemon.counter(f"testing.tests_{result.status.value}")

        return result

    async def run_suite(self, name: str, tests: list[TestCase]) -> TestSuiteResult:
        """Run a suite of tests."""
        suite = TestSuiteResult(name=name)

        for test in tests:
            result = await self.run(test)
            suite.results.append(result)
            suite.total += 1
            if result.status == TestStatus.PASSED:
                suite.passed += 1
            elif result.status == TestStatus.FAILED:
                suite.failed += 1
            elif result.status == TestStatus.SKIPPED:
                suite.skipped += 1
            elif result.status == TestStatus.ERROR:
                suite.errors += 1
            suite.total_latency_ms += result.latency_ms

        self._suites.append(suite)
        return suite

    def get_results(self, status: TestStatus | None = None) -> list[TestResult]:
        if status:
            return [r for r in self._results if r.status == status]
        return list(self._results)

    def summary(self) -> dict:
        return {
            "total_tests": len(self._test_cases),
            "total_results": len(self._results),
            "suites": len(self._suites),
            "passed": sum(1 for r in self._results if r.status == TestStatus.PASSED),
            "failed": sum(1 for r in self._results if r.status == TestStatus.FAILED),
            "errors": sum(1 for r in self._results if r.status == TestStatus.ERROR),
        }
