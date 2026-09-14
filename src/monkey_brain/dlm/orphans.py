"""Orphan Detector — detects orphaned and dangling objects.

Automatically detects:
- Orphaned entities
- Broken relationships
- Unused files
- Dangling references
- Incomplete workflows
- Invalid sessions
"""

from __future__ import annotations

import logging

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

logger = logging.getLogger("monkey_brain.dlm.orphans")


@dataclass
class Orphan:
    """An orphaned object."""

    orphan_id: str = field(default_factory=lambda: f"orphan-{uuid4().hex[:8]}")
    object_type: str = ""
    object_id: str = ""
    reason: str = ""
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrphanScanResult:
    """Result of an orphan scan."""

    scan_id: str = field(default_factory=lambda: f"scan-{uuid4().hex[:8]}")
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    orphans_found: int = 0
    orphans: list[Orphan] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_id": self.scan_id,
            "timestamp": self.timestamp,
            "orphans_found": self.orphans_found,
            "orphans": [{"type": o.object_type, "id": o.object_id, "reason": o.reason} for o in self.orphans],
        }


class OrphanDetector:
    """Detects orphaned and dangling objects.

    Responsibilities:
    - Scan for orphaned entities
    - Detect broken relationships
    - Find unused objects
    - Report orphans for cleanup
    """

    def __init__(self):
        self._detectors: list[Callable] = []
        self._scan_history: list[OrphanScanResult] = []

    def register_detector(self, detector: Callable) -> None:
        """Register an orphan detector function."""
        self._detectors.append(detector)

    def scan(self, context: dict[str, Any] | None = None) -> OrphanScanResult:
        """Run orphan detection scan."""
        result = OrphanScanResult()
        context = context or {}

        for detector in self._detectors:
            try:
                orphans = detector(context)
                if orphans:
                    result.orphans.extend(orphans)
            except Exception as e:
                result.errors.append(f"Detector failed: {e}")

        result.orphans_found = len(result.orphans)
        self._scan_history.append(result)

        if len(self._scan_history) > 50:
            self._scan_history = self._scan_history[-25:]

        return result

    def get_history(self, limit: int = 10) -> list[OrphanScanResult]:
        return self._scan_history[-limit:]

    def summary(self) -> dict:
        total_orphans = sum(r.orphans_found for r in self._scan_history)
        return {
            "total_scans": len(self._scan_history),
            "total_orphans_found": total_orphans,
            "detectors_registered": len(self._detectors),
        }
