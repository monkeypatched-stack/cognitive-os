"""Tensor Compactor — automatic background GC for the world tensor.

Periodically checks tensor capacity and triggers eviction + compaction
when usage exceeds a threshold.  Prevents unbounded growth without
blocking the hot path.
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger("agentos.tensor_compactor")


class TensorCompactor:
    """Background compactor for SparseTransitionTensor.

    Runs periodically, checks capacity usage, and triggers
    evict() + compact() when threshold is exceeded.
    """

    def __init__(self, high_water: float = 0.8, interval: float = 60.0) -> None:
        self._high_water = high_water  # trigger compaction when usage > this
        self._interval = interval
        self._thread: threading.Thread | None = None
        self._running = False
        self._compaction_count = 0
        self._eviction_count = 0

    def start(self, tensor: Any) -> None:
        """Start the background compactor for a tensor."""
        if self._running:
            return
        self._running = True
        self._tensor = tensor
        self._thread = threading.Thread(target=self._run, daemon=True, name="tensor-compactor")
        self._thread.start()
        logger.info(
            "[compactor] started (high_water=%.0f%%, interval=%.0fs)",
            self._high_water * 100,
            self._interval,
        )

    def stop(self) -> None:
        """Stop the background compactor."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=self._interval * 2)
            self._thread = None
        logger.info(
            "[compactor] stopped (compactions=%d, evictions=%d)",
            self._compaction_count,
            self._eviction_count,
        )

    def compact_now(self) -> dict[str, int]:
        """Force an immediate compaction. Returns stats."""
        if not hasattr(self, "_tensor") or self._tensor is None:
            return {"evicted": 0, "compacted": 0}

        usage = self._tensor.capacity_usage
        if usage <= self._high_water:
            return {"evicted": 0, "compacted": 0}

        evicted = self._tensor.evict()
        compacted = self._tensor.compact()
        self._compaction_count += 1
        self._eviction_count += evicted

        logger.info(
            "[compactor] ran: evicted=%d, compacted=%d, usage=%.1f%%",
            evicted,
            compacted,
            self._tensor.capacity_usage * 100,
        )
        return {"evicted": evicted, "compacted": compacted}

    def _run(self) -> None:
        """Background loop."""
        while self._running:
            time.sleep(self._interval)
            if not self._running:
                break
            try:
                self.compact_now()
            except Exception as exc:
                logger.warning("[compactor] error: %s", exc)

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "compactions": self._compaction_count,
            "evictions": self._eviction_count,
            "running": self._running,
        }
