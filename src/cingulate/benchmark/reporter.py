"""Report Generator — generates benchmark reports.

Generates:
- Console reports
- JSON reports
- Summary reports
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


class ReportGenerator:
    """Generates benchmark reports.

    Responsibilities:
    - Format results for console
    - Export results to JSON
    - Generate summaries
    """

    def __init__(self):
        self._reports: list[dict[str, Any]] = []

    def console_report(self, results: list[dict[str, Any]], title: str = "Benchmark Report") -> str:
        """Generate a console-formatted report."""
        lines = []
        lines.append("=" * 70)
        lines.append(f"  {title}")
        lines.append(f"  Generated: {datetime.now(timezone.utc).isoformat()}")
        lines.append("=" * 70)
        lines.append("")

        total = len(results)
        passed = sum(1 for r in results if r.get("status") == "passed")
        failed = sum(1 for r in results if r.get("status") == "failed")
        errors = sum(1 for r in results if r.get("status") == "error")

        lines.append(f"  Summary: {passed}/{total} passed, {failed} failed, {errors} errors")
        lines.append(f"  Pass Rate: {passed / total * 100:.1f}%" if total else "  Pass Rate: N/A")
        lines.append("")
        lines.append("-" * 70)

        for r in results:
            status = r.get("status", "unknown")
            name = r.get("name", "unnamed")
            latency = r.get("latency_ms", 0)

            if status == "passed":
                icon = "✓"
            elif status == "failed":
                icon = "✗"
            else:
                icon = "!"

            lines.append(f"  {icon} {name:<40} {latency:>8.2f}ms  {status}")

        lines.append("-" * 70)

        # Print Q&A for each result
        lines.append("")
        lines.append("  DETAILED RESULTS")
        lines.append("-" * 70)

        for r in results:
            if r.get("status") in ("passed", "failed"):
                lines.append("")
                lines.append(f"  Q: {r.get('input', {}).get('question', r.get('name', 'N/A'))}")
                lines.append(
                    f"  A: {r.get('actual', {}).get('answer', r.get('actual_output', {}).get('intent', 'N/A'))}"
                )
                lines.append(f"  Expected: {r.get('expected', {})}")
                lines.append(f"  Status: {r.get('status')}")

                if r.get("metrics"):
                    lines.append(f"  Metrics: {json.dumps(r['metrics'], indent=2)}")

        lines.append("=" * 70)

        report = "\n".join(lines)
        self._reports.append(
            {
                "type": "console",
                "content": report,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        return report

    def json_report(self, results: list[dict[str, Any]], title: str = "Benchmark Report") -> dict[str, Any]:
        """Generate a JSON report."""
        report = {
            "title": title,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total": len(results),
                "passed": sum(1 for r in results if r.get("status") == "passed"),
                "failed": sum(1 for r in results if r.get("status") == "failed"),
                "errors": sum(1 for r in results if r.get("status") == "error"),
            },
            "results": results,
        }
        self._reports.append({"type": "json", "content": report})
        return report

    def get_reports(self) -> list[dict[str, Any]]:
        return list(self._reports)
