"""Testing — test execution and validation framework.

Provides:
- TestRunner: executes tests
- PerformanceTester: benchmarks
- Profiler: comprehensive profiling
- TestReporter: generates reports
"""

from plasticity.testing.runner import (
    TestRunner,
    TestCase,
    TestResult,
    TestSuiteResult,
    TestType,
    TestStatus,
)
from plasticity.testing.performance import PerformanceTester, PerformanceResult
from plasticity.testing.profiler import Profiler, ProfilerReport, PerformanceBudget
from plasticity.testing.reporter import TestReporter

__all__ = [
    "TestRunner",
    "TestCase",
    "TestResult",
    "TestSuiteResult",
    "TestType",
    "TestStatus",
    "PerformanceTester",
    "PerformanceResult",
    "Profiler",
    "ProfilerReport",
    "PerformanceBudget",
    "TestReporter",
]
