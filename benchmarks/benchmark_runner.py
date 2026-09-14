"""Benchmark Runner and Reporting System

Executes benchmarks and generates comprehensive reports.
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import asdict

from benchmarks.benchmark_framework import BenchmarkMetrics
from benchmarks.phase_benchmarks import (
    Phase8Benchmarks,
    Phase9Benchmarks,
    Phase11Benchmarks,
    Phase12Benchmarks,
    IntegrationBenchmarks,
    ComparisonBenchmarks,
)


class BenchmarkReport:
    """Generates and manages benchmark reports"""

    def __init__(self, output_dir: str = "./benchmark_results"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.results: List[BenchmarkMetrics] = []
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None

    async def run_phase8_benchmarks(self) -> None:
        """Run Phase 8 benchmarks"""
        print("\n" + "=" * 80)
        print("PHASE 8: AUTONOMOUS ACTORS BENCHMARKS")
        print("=" * 80)

        benchmarks = [
            ("Cognitive Cycle", await Phase8Benchmarks.benchmark_cognitive_cycle()),
            ("Actor Scheduler", await Phase8Benchmarks.benchmark_actor_scheduler()),
        ]

        for name, suite in benchmarks:
            print(f"\n{name}:")
            results = await suite.run_all()
            self.results.extend(results)
            suite.print_summary()

    async def run_phase9_benchmarks(self) -> None:
        """Run Phase 9 benchmarks"""
        print("\n" + "=" * 80)
        print("PHASE 9: EVENT-DRIVEN OBSERVATION BENCHMARKS")
        print("=" * 80)

        benchmarks = [
            (
                "Event Subscription",
                await Phase9Benchmarks.benchmark_event_subscription(),
            ),
            ("Event Dispatch", await Phase9Benchmarks.benchmark_event_dispatch()),
            ("Belief Updates", await Phase9Benchmarks.benchmark_belief_update()),
        ]

        for name, suite in benchmarks:
            print(f"\n{name}:")
            results = await suite.run_all()
            self.results.extend(results)
            suite.print_summary()

    async def run_phase11_benchmarks(self) -> None:
        """Run Phase 11 benchmarks"""
        print("\n" + "=" * 80)
        print("PHASE 11: MULTI-AGENT COLLABORATION BENCHMARKS")
        print("=" * 80)

        benchmarks = [
            (
                "Capability Broadcast",
                await Phase11Benchmarks.benchmark_capability_broadcast(),
            ),
            (
                "Goal Coordination",
                await Phase11Benchmarks.benchmark_goal_coordination(),
            ),
            ("Trust Tracking", await Phase11Benchmarks.benchmark_trust_tracking()),
        ]

        for name, suite in benchmarks:
            print(f"\n{name}:")
            results = await suite.run_all()
            self.results.extend(results)
            suite.print_summary()

    async def run_phase12_benchmarks(self) -> None:
        """Run Phase 12 benchmarks"""
        print("\n" + "=" * 80)
        print("PHASE 12: DISTRIBUTED EXECUTION BENCHMARKS")
        print("=" * 80)

        benchmarks = [
            ("Actor Placement", await Phase12Benchmarks.benchmark_actor_placement()),
            ("Device Sync", await Phase12Benchmarks.benchmark_device_sync()),
            (
                "Gossip Propagation",
                await Phase12Benchmarks.benchmark_gossip_propagation(),
            ),
        ]

        for name, suite in benchmarks:
            print(f"\n{name}:")
            results = await suite.run_all()
            self.results.extend(results)
            suite.print_summary()

    async def run_integration_benchmarks(self) -> None:
        """Run integration benchmarks"""
        print("\n" + "=" * 80)
        print("INTEGRATION BENCHMARKS")
        print("=" * 80)

        benchmarks = [
            ("Full Pipeline", await IntegrationBenchmarks.benchmark_full_pipeline()),
            (
                "Multi-Actor Coordination",
                await IntegrationBenchmarks.benchmark_multi_actor_coordination(),
            ),
            ("Stress Test", await IntegrationBenchmarks.benchmark_stress_many_actors()),
            (
                "Endurance Test",
                await IntegrationBenchmarks.benchmark_endurance_continuous(),
            ),
        ]

        for name, suite in benchmarks:
            print(f"\n{name}:")
            results = await suite.run_all()
            self.results.extend(results)
            suite.print_summary()

    async def run_comparison_benchmarks(self) -> None:
        """Run v2.0 vs v3.0 comparison benchmarks"""
        print("\n" + "=" * 80)
        print("v2.0 vs v3.0 COMPARISON BENCHMARKS")
        print("=" * 80)

        print("\nv2.0 (Polling-Based Observation):")
        v2_suite = await ComparisonBenchmarks.benchmark_v2_observation()
        v2_results = await v2_suite.run_all()
        self.results.extend(v2_results)
        v2_suite.print_summary()

        print("\nv3.0 (Event-Driven Observation):")
        v3_suite = await ComparisonBenchmarks.benchmark_v3_observation()
        v3_results = await v3_suite.run_all()
        self.results.extend(v3_results)
        v3_suite.print_summary()

        # Calculate improvement
        if v2_results and v3_results:
            v2_latency = v2_results[0].mean_ms
            v3_latency = v3_results[0].mean_ms
            improvement = (
                (v2_latency - v3_latency) / v2_latency * 100 if v2_latency > 0 else 0
            )
            print(f"\n✓ Improvement: {improvement:.0f}% faster")

    async def run_all(self) -> None:
        """Run all benchmarks"""
        self.start_time = datetime.now()

        print("╔" + "=" * 78 + "╗")
        print("║" + " " * 78 + "║")
        print("║" + "COMPREHENSIVE v3.0 PLATFORM BENCHMARKS".center(78) + "║")
        print("║" + " " * 78 + "║")
        print("╚" + "=" * 78 + "╝")

        await self.run_phase8_benchmarks()
        await self.run_phase9_benchmarks()
        await self.run_phase11_benchmarks()
        await self.run_phase12_benchmarks()
        await self.run_integration_benchmarks()
        await self.run_comparison_benchmarks()

        self.end_time = datetime.now()

    def generate_json_report(self) -> str:
        """Generate JSON report"""
        report = {
            "metadata": {
                "timestamp": self.start_time.isoformat() if self.start_time else None,
                "duration_seconds": (
                    (self.end_time - self.start_time).total_seconds()
                    if self.start_time and self.end_time
                    else 0
                ),
                "total_benchmarks": len(self.results),
            },
            "benchmarks": [m.to_dict() for m in self.results],
            "summary": self._generate_summary(),
        }

        report_path = (
            self.output_dir
            / f"benchmark_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)

        return str(report_path)

    def generate_markdown_report(self) -> str:
        """Generate Markdown report"""
        lines = [
            "# Comprehensive v3.0 Platform Benchmarks\n",
            f"**Generated:** {self.start_time.isoformat()}\n",
            f"**Total Duration:** {(self.end_time - self.start_time).total_seconds():.2f}s\n",
            f"**Benchmarks Run:** {len(self.results)}\n",
            "\n## Benchmark Results by Phase\n",
        ]

        # Group by phase
        phases = {}
        for result in self.results:
            phase = self._extract_phase(result.name)
            if phase not in phases:
                phases[phase] = []
            phases[phase].append(result)

        for phase, results in sorted(phases.items()):
            lines.append(f"\n### {phase}\n")

            for result in results:
                lines.append(f"\n**{result.name}** ({result.benchmark_type.value})\n")
                lines.append("```")

                if result.benchmark_type.value == "latency":
                    lines.append(f"Mean:   {result.mean_ms:.2f}ms")
                    lines.append(f"Min:    {result.min_ms:.2f}ms")
                    lines.append(f"Max:    {result.max_ms:.2f}ms")
                    lines.append(f"P95:    {result.p95_ms:.2f}ms")
                    lines.append(f"P99:    {result.p99_ms:.2f}ms")

                elif result.benchmark_type.value == "throughput":
                    lines.append(f"Throughput: {result.ops_per_second:.0f} ops/sec")
                    lines.append(f"Operations: {result.operations}")

                elif result.benchmark_type.value == "scalability":
                    lines.append(f"Efficiency:  {result.scaling_efficiency:.1%}")
                    lines.append(f"Items:       {result.items_processed}")

                if result.errors > 0:
                    lines.append(
                        f"Errors:      {result.errors} ({result.error_rate:.1%})"
                    )

                lines.append("```")

        # Summary section
        lines.append("\n## Performance Summary\n")
        summary = self._generate_summary()
        for key, value in summary.items():
            lines.append(f"- **{key}:** {value}\n")

        report_path = (
            self.output_dir
            / f"benchmark_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        )
        with open(report_path, "w") as f:
            f.write("\n".join(lines))

        return str(report_path)

    def _generate_summary(self) -> Dict[str, Any]:
        """Generate performance summary"""
        if not self.results:
            return {}

        latency_results = [
            r for r in self.results if r.benchmark_type.value == "latency"
        ]
        throughput_results = [
            r for r in self.results if r.benchmark_type.value == "throughput"
        ]

        summary = {}

        if latency_results:
            avg_latency = sum(r.mean_ms for r in latency_results) / len(latency_results)
            summary["Average Latency"] = f"{avg_latency:.2f}ms"

            p99_latencies = [r.p99_ms for r in latency_results if r.p99_ms > 0]
            if p99_latencies:
                summary["Max P99 Latency"] = f"{max(p99_latencies):.2f}ms"

        if throughput_results:
            total_throughput = sum(r.ops_per_second for r in throughput_results)
            summary["Total Throughput"] = f"{total_throughput:.0f} ops/sec"

        total_ops = sum(r.operations for r in self.results if r.operations > 0)
        if total_ops > 0:
            summary["Total Operations"] = f"{total_ops:,}"

        return summary

    def _extract_phase(self, name: str) -> str:
        """Extract phase from benchmark name"""
        if "Phase 8" in name or "Cognitive Cycle" in name or "Scheduler" in name:
            return "Phase 8: Autonomous Actors"
        elif "Phase 9" in name or "Event" in name or "Belief" in name:
            return "Phase 9: Event-Driven Observation"
        elif (
            "Phase 11" in name
            or "Capability" in name
            or "Goal" in name
            or "Trust" in name
        ):
            return "Phase 11: Multi-Agent Collaboration"
        elif (
            "Phase 12" in name
            or "Placement" in name
            or "Sync" in name
            or "Gossip" in name
        ):
            return "Phase 12: Distributed Execution"
        elif (
            "Full" in name
            or "Integration" in name
            or "Coordination" in name
            or "Stress" in name
            or "Endurance" in name
        ):
            return "Integration & Stress"
        else:
            return "Comparison"

    def print_final_summary(self) -> None:
        """Print final benchmark summary"""
        print("\n" + "╔" + "=" * 78 + "╗")
        print("║" + "BENCHMARK EXECUTION COMPLETE".center(78) + "║")
        print("╚" + "=" * 78 + "╝\n")

        summary = self._generate_summary()
        print("Performance Summary:")
        for key, value in summary.items():
            print(f"  • {key}: {value}")

        print(f"\nTotal Benchmarks: {len(self.results)}")
        print(
            f"Total Duration: {(self.end_time - self.start_time).total_seconds():.2f}s"
        )

        print("\nReports generated:")
        print(f"  • JSON: {self.output_dir}/benchmark_report_*.json")
        print(f"  • Markdown: {self.output_dir}/benchmark_report_*.md")


async def main():
    """Run benchmark suite"""
    runner = BenchmarkReport()
    await runner.run_all()
    runner.print_final_summary()

    # Generate reports
    json_report = runner.generate_json_report()
    md_report = runner.generate_markdown_report()

    print(f"\n✓ Reports generated:")
    print(f"  JSON: {json_report}")
    print(f"  Markdown: {md_report}")


if __name__ == "__main__":
    asyncio.run(main())
