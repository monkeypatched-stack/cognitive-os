"""DLM — Data Lifecycle Manager for AgentOS.

Manages the lifecycle of all runtime data.
Ensures AgentOS remains performant and storage-efficient.
"""

from __future__ import annotations

from typing import Any, Callable

from src.monkey_brain.dlm.ttl import TtlManager, TtlEntry
from src.monkey_brain.dlm.gc import GarbageCollector, CollectionResult
from src.monkey_brain.dlm.storage import StorageMonitor, StorageQuota, StorageSnapshot
from src.monkey_brain.dlm.orphans import OrphanDetector, OrphanScanResult


class Dlm:
    """Data Lifecycle Manager.

    Responsibilities:
    - TTL management
    - Garbage collection
    - Storage monitoring
    - Orphan detection
    - Lifecycle policies

    Safety:
    - Never removes protected data
    - Never removes permanent storage class
    - Logs all lifecycle operations
    """

    def __init__(self):
        self.ttl = TtlManager()
        self.gc = GarbageCollector(self.ttl)
        self.storage = StorageMonitor()
        self.orphans = OrphanDetector()
        self._lemon = None

    def set_lemon(self, lemon) -> None:
        """Set the Lemon observability manager."""
        self._lemon = lemon

    # --- TTL Management ---

    def register_object(
        self,
        data_type: str,
        object_id: str,
        ttl_seconds: int | None = None,
        **metadata: Any,
    ) -> TtlEntry:
        """Register an object with lifecycle tracking."""
        entry = self.ttl.register(data_type, object_id, ttl_seconds, **metadata)

        if self._lemon:
            self._lemon.counter("dlm.objects_registered", data_type=data_type)

        return entry

    def is_expired(self, entry_id: str) -> bool:
        return self.ttl.is_expired(entry_id)

    def get_expired(self, data_type: str | None = None) -> list[TtlEntry]:
        return self.ttl.get_expired(data_type)

    # --- Garbage Collection ---

    def collect(self, data_type: str | None = None) -> CollectionResult:
        """Run garbage collection."""
        if self._lemon:
            self._lemon.counter("dlm.gc_runs")
            self._lemon.info("Garbage collection started", component="dlm")

        result = self.gc.collect(data_type)

        if self._lemon:
            self._lemon.counter("dlm.objects_collected", count=result.entries_collected)
            if result.errors:
                self._lemon.counter("dlm.gc_errors", count=len(result.errors))
            self._lemon.info(
                f"Garbage collection completed: {result.entries_collected} objects",
                component="dlm",
            )

        return result

    def register_deleter(self, data_type: str, callback: Callable) -> None:
        self.gc.register_deleter(data_type, callback)

    # --- Storage Monitoring ---

    def set_quota(self, data_type: str, quota: StorageQuota) -> None:
        self.storage.set_quota(data_type, quota)

    def record_write(self, data_type: str, bytes_written: int) -> None:
        self.storage.record_write(data_type, bytes_written)
        if self._lemon:
            self._lemon.counter("dlm.writes", data_type=data_type)

    def record_delete(self, data_type: str, bytes_deleted: int) -> None:
        self.storage.record_delete(data_type, bytes_deleted)
        if self._lemon:
            self._lemon.counter("dlm.deletes", data_type=data_type)

    def check_storage(self) -> list[dict[str, Any]]:
        alerts = self.storage.check_quotas()
        if self._lemon:
            for alert in alerts:
                self._lemon.alert(f"storage_{alert['type']}", alert["message"])
        return alerts

    def storage_snapshot(self) -> StorageSnapshot:
        return self.storage.snapshot()

    # --- Orphan Detection ---

    def register_orphan_detector(self, detector: Callable) -> None:
        self.orphans.register_detector(detector)

    def scan_orphans(self, context: dict[str, Any] | None = None) -> OrphanScanResult:
        result = self.orphans.scan(context)
        if self._lemon:
            self._lemon.counter("dlm.orphans_found", count=result.orphans_found)
        return result

    # --- Summary ---

    def summary(self) -> dict:
        return {
            "ttl": self.ttl.summary(),
            "gc": self.gc.summary(),
            "storage": self.storage.summary(),
            "orphans": self.orphans.summary(),
        }
