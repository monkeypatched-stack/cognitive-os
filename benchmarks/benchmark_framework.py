"""Comprehensive Benchmark Framework for v3.0 Platform

Measures performance, scalability, and resource usage across all phases.
"""

import asyncio
import time
import psutil
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Callable, Any, Optional, Tuple
from datetime import datetime
from enum import Enum


class BenchmarkType(str, Enum):
    """Types of benchmarks"""

    LATENCY = "latency"
    THROUGHPUT = "throughput"
    MEMORY = "memory"
    CPU = "cpu"
    SCALABILITY = "scalability"
    STRESS = "stress"
    LOAD = "load"
    ENDURANCE = "endurance"


@dataclass
class BenchmarkMetrics:
    """Collected metrics from a benchmark"""

    name: str
    benchmark_type: BenchmarkType
    duration_seconds: float

    # Latency metrics
    min_ms: float = 0.0
    max_ms: float = 0.0
    mean_ms: float = 0.0
    median_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0

    # Throughput metrics
    operations: int = 0
    ops_per_second: float = 0.0

    # Resource metrics
    memory_mb: float = 0.0
    peak_memory_mb: float = 0.0
    cpu_percent: float = 0.0

    # Scalability metrics
    items_processed: int = 0
    scaling_efficiency: float = 0.0  # 0.0-1.0

    # Error tracking
    errors: int = 0
    error_rate: float = 0.0

    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "name": self.name,
            "type": self.benchmark_type.value,
            "duration_s": self.duration_seconds,
            "latency": {
                "min_ms": self.min_ms,
                "max_ms": self.max_ms,
                "mean_ms": self.mean_ms,
                "median_ms": self.median_ms,
                "p95_ms": self.p95_ms,
                "p99_ms": self.p99_ms,
            },
            "throughput": {
                "operations": self.operations,
                "ops_per_sec": self.ops_per_second,
            },
            "resources": {
                "memory_mb": self.memory_mb,
                "peak_memory_mb": self.peak_memory_mb,
                "cpu_percent": self.cpu_percent,
            },
            "scalability": {
                "items_processed": self.items_processed,
                "scaling_efficiency": self.scaling_efficiency,
            },
            "errors": {
                "count": self.errors,
                "rate": self.error_rate,
            },
            "timestamp": self.timestamp.isoformat(),
        }


class LatencyBenchmark:
    """Measures operation latency"""

    def __init__(self, name: str, operation: Callable):
        self.name = name
        self.operation = operation
        self.measurements: List[float] = []

    async def run(self, iterations: int = 1000) -> BenchmarkMetrics:
        """Run latency benchmark"""
        start_time = time.time()
        process = psutil.Process()

        mem_before = process.memory_info().rss / 1024 / 1024
        peak_memory = mem_before

        errors = 0

        for _ in range(iterations):
            try:
                iter_start = time.time()

                if asyncio.iscoroutinefunction(self.operation):
                    await self.operation()
                else:
                    self.operation()

                iter_time = (time.time() - iter_start) * 1000  # ms
                self.measurements.append(iter_time)

                current_mem = process.memory_info().rss / 1024 / 1024
                peak_memory = max(peak_memory, current_mem)

            except Exception as e:
                errors += 1

        duration = time.time() - start_time
        mem_after = process.memory_info().rss / 1024 / 1024

        # Calculate statistics
        sorted_measurements = sorted(self.measurements)

        return BenchmarkMetrics(
            name=self.name,
            benchmark_type=BenchmarkType.LATENCY,
            duration_seconds=duration,
            min_ms=min(sorted_measurements) if sorted_measurements else 0,
            max_ms=max(sorted_measurements) if sorted_measurements else 0,
            mean_ms=statistics.mean(sorted_measurements) if sorted_measurements else 0,
            median_ms=(
                statistics.median(sorted_measurements) if sorted_measurements else 0
            ),
            p95_ms=(
                sorted_measurements[int(len(sorted_measurements) * 0.95)]
                if len(sorted_measurements) > 1
                else 0
            ),
            p99_ms=(
                sorted_measurements[int(len(sorted_measurements) * 0.99)]
                if len(sorted_measurements) > 1
                else 0
            ),
            operations=iterations,
            memory_mb=mem_after - mem_before,
            peak_memory_mb=peak_memory,
            errors=errors,
            error_rate=errors / iterations if iterations > 0 else 0,
        )


class ThroughputBenchmark:
    """Measures operation throughput"""

    def __init__(self, name: str, operation: Callable):
        self.name = name
        self.operation = operation

    async def run(self, duration_seconds: float = 10.0) -> BenchmarkMetrics:
        """Run throughput benchmark"""
        start_time = time.time()
        process = psutil.Process()

        mem_before = process.memory_info().rss / 1024 / 1024
        peak_memory = mem_before

        operations = 0
        errors = 0

        while time.time() - start_time < duration_seconds:
            try:
                if asyncio.iscoroutinefunction(self.operation):
                    await self.operation()
                else:
                    self.operation()

                operations += 1

                current_mem = process.memory_info().rss / 1024 / 1024
                peak_memory = max(peak_memory, current_mem)

            except Exception as e:
                errors += 1

        actual_duration = time.time() - start_time
        mem_after = process.memory_info().rss / 1024 / 1024

        return BenchmarkMetrics(
            name=self.name,
            benchmark_type=BenchmarkType.THROUGHPUT,
            duration_seconds=actual_duration,
            operations=operations,
            ops_per_second=operations / actual_duration if actual_duration > 0 else 0,
            memory_mb=mem_after - mem_before,
            peak_memory_mb=peak_memory,
            errors=errors,
            error_rate=errors / operations if operations > 0 else 0,
        )


class ScalabilityBenchmark:
    """Measures scalability across different scales"""

    def __init__(self, name: str, operation: Callable[[int], float]):
        self.name = name
        self.operation = operation  # Takes scale parameter, returns time in ms

    async def run(self, scales: List[int] = None) -> BenchmarkMetrics:
        """Run scalability benchmark"""
        if scales is None:
            scales = [10, 50, 100, 500, 1000]

        start_time = time.time()
        measurements: Dict[int, float] = {}

        for scale in scales:
            try:
                if asyncio.iscoroutinefunction(self.operation):
                    duration_ms = await self.operation(scale)
                else:
                    duration_ms = self.operation(scale)

                measurements[scale] = duration_ms
            except Exception as e:
                measurements[scale] = float("inf")

        # Calculate scaling efficiency (ideal: linear)
        if len(measurements) >= 2:
            scales_list = sorted(measurements.keys())
            times_list = [measurements[s] for s in scales_list]

            # Linear scaling: time should increase linearly with scale
            # Efficiency = 1.0 for perfect linear, <1.0 for super-linear
            base_time = times_list[0]
            base_scale = scales_list[0]

            efficiency_scores = []
            for scale, time_ms in zip(scales_list[1:], times_list[1:]):
                expected_time = base_time * (scale / base_scale)
                actual_time = time_ms
                efficiency = (
                    min(1.0, expected_time / actual_time) if actual_time > 0 else 0
                )
                efficiency_scores.append(efficiency)

            avg_efficiency = (
                statistics.mean(efficiency_scores) if efficiency_scores else 0
            )
        else:
            avg_efficiency = 0

        duration = time.time() - start_time

        return BenchmarkMetrics(
            name=self.name,
            benchmark_type=BenchmarkType.SCALABILITY,
            duration_seconds=duration,
            items_processed=sum(scales),
            scaling_efficiency=avg_efficiency,
        )


class StressBenchmark:
    """Stress tests with extreme loads"""

    def __init__(self, name: str, operation: Callable):
        self.name = name
        self.operation = operation

    async def run(
        self, concurrent: int = 100, iterations: int = 1000
    ) -> BenchmarkMetrics:
        """Run stress benchmark"""
        start_time = time.time()
        process = psutil.Process()

        mem_before = process.memory_info().rss / 1024 / 1024
        peak_memory = mem_before

        operations = 0
        errors = 0

        async def worker():
            nonlocal operations, errors, peak_memory
            for _ in range(iterations // concurrent + 1):
                try:
                    if asyncio.iscoroutinefunction(self.operation):
                        await self.operation()
                    else:
                        self.operation()

                    operations += 1

                    current_mem = process.memory_info().rss / 1024 / 1024
                    peak_memory = max(peak_memory, current_mem)

                except Exception as e:
                    errors += 1

        # Run concurrent operations
        tasks = [worker() for _ in range(concurrent)]
        await asyncio.gather(*tasks, return_exceptions=True)

        actual_duration = time.time() - start_time
        mem_after = process.memory_info().rss / 1024 / 1024

        return BenchmarkMetrics(
            name=self.name,
            benchmark_type=BenchmarkType.STRESS,
            duration_seconds=actual_duration,
            operations=operations,
            ops_per_second=operations / actual_duration if actual_duration > 0 else 0,
            memory_mb=mem_after - mem_before,
            peak_memory_mb=peak_memory,
            errors=errors,
            error_rate=errors / operations if operations > 0 else 0,
        )


class EnduranceBenchmark:
    """Long-running endurance tests"""

    def __init__(self, name: str, operation: Callable):
        self.name = name
        self.operation = operation

    async def run(self, duration_seconds: int = 300) -> BenchmarkMetrics:
        """Run endurance benchmark"""
        start_time = time.time()
        process = psutil.Process()

        mem_samples = []
        cpu_samples = []
        operations = 0
        errors = 0

        while time.time() - start_time < duration_seconds:
            try:
                if asyncio.iscoroutinefunction(self.operation):
                    await self.operation()
                else:
                    self.operation()

                operations += 1

                # Sample resources
                mem_mb = process.memory_info().rss / 1024 / 1024
                mem_samples.append(mem_mb)

                cpu_percent = process.cpu_percent(interval=0.1)
                cpu_samples.append(cpu_percent)

            except Exception as e:
                errors += 1

        actual_duration = time.time() - start_time

        return BenchmarkMetrics(
            name=self.name,
            benchmark_type=BenchmarkType.ENDURANCE,
            duration_seconds=actual_duration,
            operations=operations,
            ops_per_second=operations / actual_duration if actual_duration > 0 else 0,
            memory_mb=statistics.mean(mem_samples) if mem_samples else 0,
            peak_memory_mb=max(mem_samples) if mem_samples else 0,
            cpu_percent=statistics.mean(cpu_samples) if cpu_samples else 0,
            errors=errors,
            error_rate=errors / operations if operations > 0 else 0,
        )


class BenchmarkSuite:
    """Collection of benchmarks to run together"""

    def __init__(self):
        self.benchmarks: List[
            Tuple[
                BenchmarkType,
                LatencyBenchmark
                | ThroughputBenchmark
                | ScalabilityBenchmark
                | StressBenchmark
                | EnduranceBenchmark,
            ]
        ] = []
        self.results: List[BenchmarkMetrics] = []

    def add_latency_benchmark(self, name: str, operation: Callable) -> None:
        """Add latency benchmark"""
        self.benchmarks.append(
            (BenchmarkType.LATENCY, LatencyBenchmark(name, operation))
        )

    def add_throughput_benchmark(self, name: str, operation: Callable) -> None:
        """Add throughput benchmark"""
        self.benchmarks.append(
            (BenchmarkType.THROUGHPUT, ThroughputBenchmark(name, operation))
        )

    def add_scalability_benchmark(self, name: str, operation: Callable) -> None:
        """Add scalability benchmark"""
        self.benchmarks.append(
            (BenchmarkType.SCALABILITY, ScalabilityBenchmark(name, operation))
        )

    def add_stress_benchmark(self, name: str, operation: Callable) -> None:
        """Add stress benchmark"""
        self.benchmarks.append((BenchmarkType.STRESS, StressBenchmark(name, operation)))

    def add_endurance_benchmark(self, name: str, operation: Callable) -> None:
        """Add endurance benchmark"""
        self.benchmarks.append(
            (BenchmarkType.ENDURANCE, EnduranceBenchmark(name, operation))
        )

    async def run_all(self) -> List[BenchmarkMetrics]:
        """Run all benchmarks"""
        for bench_type, benchmark in self.benchmarks:
            print(f"Running {bench_type.value} benchmark: {benchmark.name}...")

            if isinstance(benchmark, LatencyBenchmark):
                metrics = await benchmark.run()
            elif isinstance(benchmark, ThroughputBenchmark):
                metrics = await benchmark.run()
            elif isinstance(benchmark, ScalabilityBenchmark):
                metrics = await benchmark.run()
            elif isinstance(benchmark, StressBenchmark):
                metrics = await benchmark.run()
            elif isinstance(benchmark, EnduranceBenchmark):
                metrics = await benchmark.run()
            else:
                continue

            self.results.append(metrics)
            print(f"  ✓ Complete")

        return self.results

    def print_summary(self) -> None:
        """Print benchmark results summary"""
        print("\n" + "=" * 80)
        print("BENCHMARK RESULTS SUMMARY")
        print("=" * 80)

        for metrics in self.results:
            print(f"\n{metrics.name} ({metrics.benchmark_type.value})")
            print(f"  Duration: {metrics.duration_seconds:.2f}s")

            if metrics.benchmark_type == BenchmarkType.LATENCY:
                print(
                    f"  Latency: {metrics.mean_ms:.2f}ms (min={metrics.min_ms:.2f}, max={metrics.max_ms:.2f})"
                )
                print(f"  P95: {metrics.p95_ms:.2f}ms, P99: {metrics.p99_ms:.2f}ms")
                print(f"  Operations: {metrics.operations}")

            elif metrics.benchmark_type == BenchmarkType.THROUGHPUT:
                print(f"  Throughput: {metrics.ops_per_second:.0f} ops/sec")
                print(f"  Operations: {metrics.operations}")

            elif metrics.benchmark_type == BenchmarkType.SCALABILITY:
                print(f"  Scaling Efficiency: {metrics.scaling_efficiency:.1%}")
                print(f"  Items Processed: {metrics.items_processed}")

            elif metrics.benchmark_type == BenchmarkType.STRESS:
                print(
                    f"  Throughput: {metrics.ops_per_second:.0f} ops/sec (under stress)"
                )
                print(f"  Operations: {metrics.operations}")

            elif metrics.benchmark_type == BenchmarkType.ENDURANCE:
                print(f"  Throughput: {metrics.ops_per_second:.0f} ops/sec (sustained)")
                print(
                    f"  Avg Memory: {metrics.memory_mb:.1f}MB, Peak: {metrics.peak_memory_mb:.1f}MB"
                )
                print(f"  Avg CPU: {metrics.cpu_percent:.1f}%")

            if metrics.errors > 0:
                print(f"  Errors: {metrics.errors} ({metrics.error_rate:.1%})")

        print("\n" + "=" * 80)

    def get_report(self) -> Dict[str, Any]:
        """Get full benchmark report"""
        return {
            "timestamp": datetime.now().isoformat(),
            "benchmarks": [m.to_dict() for m in self.results],
            "summary": {
                "total_benchmarks": len(self.results),
                "total_duration_seconds": sum(m.duration_seconds for m in self.results),
            },
        }
