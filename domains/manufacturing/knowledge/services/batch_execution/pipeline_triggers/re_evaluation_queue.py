"""
Re-evaluation Queue for Pipeline Triggers

Manages pending re-evaluations with:
- Priority-based processing (variance severity)
- Deduplication of similar requests
- Batch processing capabilities
- Metrics on queue depth and processing time
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set
from uuid import uuid4

logger = logging.getLogger(__name__)


class ReEvaluationPriority(Enum):
    """Priority levels for re-evaluation requests."""

    LOW = "low"  # Minor variances, can batch
    MEDIUM = "medium"  # Moderate variances, timely processing
    HIGH = "high"  # Significant variances, prompt processing
    CRITICAL = "critical"  # Critical variances, immediate processing


class ReEvaluationReason(Enum):
    """Reasons for triggering re-evaluation."""

    TIME_VARIANCE_UNDER = "time_variance_under"  # Completed faster
    TIME_VARIANCE_OVER = "time_variance_over"  # Took longer
    TIME_VARIANCE_CRITICAL = "time_variance_critical"  # Significantly over
    QC_FAILED = "qc_failed"  # Quality check failed
    RESOURCE_CONSTRAINT = "resource_constraint"  # Resource became unavailable
    MANUAL_TRIGGER = "manual_trigger"  # Manually requested


@dataclass
class ReEvaluationRequest:
    """A request to re-evaluate workflow parameters."""

    request_id: str = field(default_factory=lambda: f"REEVAL-{uuid4().hex[:12]}")
    work_order_id: str = ""
    batch_id: str = ""
    execution_id: str = ""
    reason: ReEvaluationReason = ReEvaluationReason.TIME_VARIANCE_OVER
    variance_data: Dict[str, Any] = field(default_factory=dict)
    priority: ReEvaluationPriority = ReEvaluationPriority.MEDIUM
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    scheduled_for: Optional[datetime] = None
    attempted_count: int = 0
    max_attempts: int = 3
    is_processed: bool = False
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def get_dedup_key(self) -> str:
        """Generate deduplication key based on work order and reason."""
        return f"{self.work_order_id}:{self.reason.value}"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "request_id": self.request_id,
            "work_order_id": self.work_order_id,
            "batch_id": self.batch_id,
            "execution_id": self.execution_id,
            "reason": self.reason.value,
            "priority": self.priority.value,
            "created_at": self.created_at.isoformat(),
            "scheduled_for": (
                self.scheduled_for.isoformat() if self.scheduled_for else None
            ),
            "attempted_count": self.attempted_count,
            "max_attempts": self.max_attempts,
            "is_processed": self.is_processed,
            "error": self.error,
        }


class ReEvaluationQueue:
    """
    Queue for managing pending re-evaluations.

    Provides:
    - Priority-based dequeuing
    - Deduplication to prevent cascading retriggers
    - Batch processing capabilities
    - Metrics and monitoring
    """

    def __init__(self, max_queue_size: int = 10000):
        self.max_queue_size = max_queue_size
        self.requests: Dict[str, ReEvaluationRequest] = {}
        self.queue_order: List[str] = []  # Ordered by priority + created_at
        self.dedup_keys: Set[str] = set()  # Track active dedup keys
        self.processed_requests: Dict[str, ReEvaluationRequest] = {}
        self.metrics = {
            "total_enqueued": 0,
            "total_processed": 0,
            "total_failed": 0,
            "total_deduplicated": 0,
            "processing_times": [],
        }

    def enqueue(self, request: ReEvaluationRequest) -> bool:
        """
        Enqueue a re-evaluation request.

        Returns True if enqueued, False if deduplicated or queue full.
        """
        # Check for deduplication
        dedup_key = request.get_dedup_key()

        if dedup_key in self.dedup_keys:
            logger.info(
                f"Re-evaluation request {request.request_id} deduplicated "
                f"(already pending: {dedup_key})"
            )
            self.metrics["total_deduplicated"] += 1
            return False

        # Check queue size
        if len(self.requests) >= self.max_queue_size:
            logger.error(
                f"Re-evaluation queue full ({self.max_queue_size}), "
                f"dropping request {request.request_id}"
            )
            return False

        # Add to queue
        self.requests[request.request_id] = request
        self.queue_order.append(request.request_id)
        self.dedup_keys.add(dedup_key)

        logger.info(
            f"Re-evaluation request {request.request_id} enqueued "
            f"(priority: {request.priority.value}, reason: {request.reason.value})"
        )

        self.metrics["total_enqueued"] += 1
        return True

    def dequeue(self, batch_size: int = 1) -> List[ReEvaluationRequest]:
        """
        Dequeue highest priority requests.

        Returns requests sorted by priority (CRITICAL > HIGH > MEDIUM > LOW)
        and then by created_at (FIFO within priority).
        """
        if not self.queue_order:
            return []

        # Sort queue by priority and created_at
        sorted_requests = sorted(
            [self.requests[req_id] for req_id in self.queue_order],
            key=lambda r: (
                self._priority_sort_key(r.priority),
                r.created_at,
            ),
        )

        # Take batch_size requests
        dequeued = sorted_requests[:batch_size]

        # Remove from queue
        for request in dequeued:
            self.queue_order.remove(request.request_id)
            dedup_key = request.get_dedup_key()
            self.dedup_keys.discard(dedup_key)

        return dequeued

    def mark_processed(self, request_id: str, result: Dict[str, Any]):
        """Mark a request as successfully processed."""
        if request_id not in self.requests:
            logger.warning(f"Request {request_id} not found in queue")
            return

        request = self.requests.pop(request_id)
        request.is_processed = True
        request.result = result

        # Remove from queue order and dedup keys
        if request_id in self.queue_order:
            self.queue_order.remove(request_id)
        dedup_key = request.get_dedup_key()
        self.dedup_keys.discard(dedup_key)

        self.processed_requests[request_id] = request
        self.metrics["total_processed"] += 1

        logger.info(f"Re-evaluation request {request_id} marked as processed")

    def mark_failed(self, request_id: str, error: str, retry: bool = True):
        """Mark a request as failed, optionally retry."""
        if request_id not in self.requests:
            logger.warning(f"Request {request_id} not found in queue")
            return

        request = self.requests[request_id]
        request.attempted_count += 1
        request.error = error

        if retry and request.attempted_count < request.max_attempts:
            # Re-schedule for retry
            request.scheduled_for = datetime.now(timezone.utc) + timedelta(
                seconds=60 * request.attempted_count
            )  # Exponential backoff
            logger.info(
                f"Re-evaluation request {request_id} scheduled for retry "
                f"(attempt {request.attempted_count}/{request.max_attempts})"
            )
        else:
            # Max retries exceeded, move to failed
            self.requests.pop(request_id)

            # Remove from queue order and dedup keys
            if request_id in self.queue_order:
                self.queue_order.remove(request_id)
            dedup_key = request.get_dedup_key()
            self.dedup_keys.discard(dedup_key)

            self.processed_requests[request_id] = request
            self.metrics["total_failed"] += 1
            logger.error(
                f"Re-evaluation request {request_id} failed after "
                f"{request.attempted_count} attempts: {error}"
            )

    def peek(self) -> Optional[ReEvaluationRequest]:
        """Peek at the highest priority request without dequeuing."""
        if not self.queue_order:
            return None

        sorted_requests = sorted(
            [self.requests[req_id] for req_id in self.queue_order],
            key=lambda r: (
                self._priority_sort_key(r.priority),
                r.created_at,
            ),
        )

        return sorted_requests[0] if sorted_requests else None

    def get_pending_count(self, priority: Optional[ReEvaluationPriority] = None) -> int:
        """Get count of pending requests, optionally filtered by priority."""
        if priority is None:
            return len(self.queue_order)

        return sum(
            1
            for req_id in self.queue_order
            if self.requests[req_id].priority == priority
        )

    def get_pending_requests(
        self,
        work_order_id: Optional[str] = None,
        priority: Optional[ReEvaluationPriority] = None,
    ) -> List[ReEvaluationRequest]:
        """Get pending requests, optionally filtered."""
        requests = [self.requests[req_id] for req_id in self.queue_order]

        if work_order_id:
            requests = [r for r in requests if r.work_order_id == work_order_id]

        if priority:
            requests = [r for r in requests if r.priority == priority]

        return sorted(
            requests, key=lambda r: (self._priority_sort_key(r.priority), r.created_at)
        )

    def get_processing_history(
        self, work_order_id: Optional[str] = None, limit: int = 100
    ) -> List[ReEvaluationRequest]:
        """Get history of processed requests."""
        requests = list(self.processed_requests.values())

        if work_order_id:
            requests = [r for r in requests if r.work_order_id == work_order_id]

        return sorted(requests, key=lambda r: r.created_at, reverse=True)[:limit]

    def get_metrics(self) -> Dict[str, Any]:
        """Get queue metrics."""
        avg_processing_time = (
            sum(self.metrics["processing_times"])
            / len(self.metrics["processing_times"])
            if self.metrics["processing_times"]
            else 0
        )

        return {
            "pending_count": len(self.queue_order),
            "pending_by_priority": {
                "critical": self.get_pending_count(ReEvaluationPriority.CRITICAL),
                "high": self.get_pending_count(ReEvaluationPriority.HIGH),
                "medium": self.get_pending_count(ReEvaluationPriority.MEDIUM),
                "low": self.get_pending_count(ReEvaluationPriority.LOW),
            },
            "total_enqueued": self.metrics["total_enqueued"],
            "total_processed": self.metrics["total_processed"],
            "total_failed": self.metrics["total_failed"],
            "total_deduplicated": self.metrics["total_deduplicated"],
            "avg_processing_time_ms": f"{avg_processing_time:.1f}",
            "queue_capacity_used": f"{(len(self.queue_order) / self.max_queue_size * 100):.1f}%",
        }

    def clear(self):
        """Clear all pending requests."""
        cleared_count = len(self.queue_order)
        self.requests.clear()
        self.queue_order.clear()
        self.dedup_keys.clear()
        logger.info(f"Re-evaluation queue cleared ({cleared_count} requests)")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "pending_requests": [
                self.requests[req_id].to_dict() for req_id in self.queue_order
            ],
            "pending_count": len(self.queue_order),
            "metrics": self.get_metrics(),
        }

    @staticmethod
    def _priority_sort_key(priority: ReEvaluationPriority) -> int:
        """Convert priority to sort key (lower = higher priority)."""
        priority_map = {
            ReEvaluationPriority.CRITICAL: 0,
            ReEvaluationPriority.HIGH: 1,
            ReEvaluationPriority.MEDIUM: 2,
            ReEvaluationPriority.LOW: 3,
        }
        return priority_map.get(priority, 99)
