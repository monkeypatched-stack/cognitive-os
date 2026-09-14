"""
Work Order Batch Manager

Manages groups of work orders for the same batch.
Tracks batch execution progress and aggregates metrics.
Provides batch-level queries and operations.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BatchMetrics:
    """Metrics for a batch of work orders."""

    def __init__(self, batch_id: str):
        self.batch_id = batch_id
        self.total_work_orders = 0
        self.created_work_orders = 0
        self.assigned_work_orders = 0
        self.in_progress_work_orders = 0
        self.completed_work_orders = 0
        self.rejected_work_orders = 0
        self.on_hold_work_orders = 0

        self.total_estimated_minutes = 0.0
        self.total_actual_minutes = 0.0
        self.completed_estimated_minutes = 0.0
        self.completed_actual_minutes = 0.0

        self.variance_minutes_sum = 0.0
        self.variance_percent_sum = 0.0
        self.variance_count = 0

        self.created_at = datetime.now(timezone.utc)
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.last_updated_at = datetime.now(timezone.utc)

    def add_work_order(
        self,
        status: str,
        estimated_minutes: int,
        actual_minutes: Optional[float] = None,
    ):
        """Add a work order to metrics."""
        self.total_work_orders += 1
        self.total_estimated_minutes += estimated_minutes

        if status == "created":
            self.created_work_orders += 1
        elif status == "assigned":
            self.assigned_work_orders += 1
        elif status == "in_progress":
            self.in_progress_work_orders += 1
        elif status == "completed":
            self.completed_work_orders += 1
            self.completed_estimated_minutes += estimated_minutes
            if actual_minutes:
                self.completed_actual_minutes += actual_minutes
        elif status == "rejected":
            self.rejected_work_orders += 1
        elif status == "on_hold":
            self.on_hold_work_orders += 1

        self.last_updated_at = datetime.now(timezone.utc)

    def record_variance(
        self,
        estimated_minutes: int,
        actual_minutes: float,
    ):
        """Record a time variance."""
        variance_minutes = actual_minutes - estimated_minutes
        variance_percent = (variance_minutes / estimated_minutes) * 100

        self.variance_minutes_sum += variance_minutes
        self.variance_percent_sum += variance_percent
        self.variance_count += 1

    def get_completion_percent(self) -> float:
        """Get completion percentage."""
        if self.total_work_orders == 0:
            return 0.0
        return (self.completed_work_orders / self.total_work_orders) * 100

    def get_average_variance_percent(self) -> float:
        """Get average variance percentage."""
        if self.variance_count == 0:
            return 0.0
        return self.variance_percent_sum / self.variance_count

    def get_average_variance_minutes(self) -> float:
        """Get average variance in minutes."""
        if self.variance_count == 0:
            return 0.0
        return self.variance_minutes_sum / self.variance_count

    def mark_started(self):
        """Mark batch as started."""
        if not self.started_at:
            self.started_at = datetime.now(timezone.utc)

    def mark_completed(self):
        """Mark batch as completed."""
        if not self.completed_at:
            self.completed_at = datetime.now(timezone.utc)

    def get_elapsed_time_minutes(self) -> Optional[float]:
        """Get elapsed time since batch started."""
        if not self.started_at:
            return None
        end_time = self.completed_at or datetime.now(timezone.utc)
        return (end_time - self.started_at).total_seconds() / 60

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "batch_id": self.batch_id,
            "total_work_orders": self.total_work_orders,
            "created": self.created_work_orders,
            "assigned": self.assigned_work_orders,
            "in_progress": self.in_progress_work_orders,
            "completed": self.completed_work_orders,
            "rejected": self.rejected_work_orders,
            "on_hold": self.on_hold_work_orders,
            "completion_percent": self.get_completion_percent(),
            "total_estimated_minutes": self.total_estimated_minutes,
            "total_actual_minutes": self.total_actual_minutes,
            "completed_estimated_minutes": self.completed_estimated_minutes,
            "completed_actual_minutes": self.completed_actual_minutes,
            "average_variance_percent": self.get_average_variance_percent(),
            "average_variance_minutes": self.get_average_variance_minutes(),
            "total_variance_count": self.variance_count,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "elapsed_time_minutes": self.get_elapsed_time_minutes(),
            "last_updated_at": self.last_updated_at.isoformat(),
        }


class WorkOrderBatchGroup:
    """A group of work orders for a batch."""

    def __init__(self, batch_id: str, execution_id: str):
        self.batch_id = batch_id
        self.execution_id = execution_id
        self.work_orders: Dict[str, Dict[str, Any]] = {}
        self.work_order_ids: List[str] = []
        self.created_at = datetime.now(timezone.utc)

    def add_work_order(self, work_order_id: str, work_order_data: Dict[str, Any]):
        """Add a work order to the group."""
        if work_order_id not in self.work_orders:
            self.work_order_ids.append(work_order_id)
        self.work_orders[work_order_id] = work_order_data

    def remove_work_order(self, work_order_id: str) -> bool:
        """Remove a work order from the group."""
        if work_order_id in self.work_orders:
            del self.work_orders[work_order_id]
            self.work_order_ids.remove(work_order_id)
            return True
        return False

    def get_work_order(self, work_order_id: str) -> Optional[Dict[str, Any]]:
        """Get a work order from the group."""
        return self.work_orders.get(work_order_id)

    def get_work_orders_by_status(self, status: str) -> List[Dict[str, Any]]:
        """Get all work orders with a specific status."""
        return [wo for wo in self.work_orders.values() if wo.get("status") == status]

    def get_work_orders_by_step(self, step_id: str) -> List[Dict[str, Any]]:
        """Get all work orders for a specific step."""
        return [wo for wo in self.work_orders.values() if wo.get("step_id") == step_id]

    def size(self) -> int:
        """Get number of work orders in group."""
        return len(self.work_orders)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "batch_id": self.batch_id,
            "execution_id": self.execution_id,
            "work_order_count": len(self.work_orders),
            "work_order_ids": self.work_order_ids,
            "created_at": self.created_at.isoformat(),
        }


class WorkOrderBatchManager:
    """
    Manages groups of work orders for batch execution.

    Responsibilities:
    - Track batch execution progress
    - Aggregate metrics across work orders
    - Provide batch-level queries
    - Support bulk operations
    """

    def __init__(self, batch_id: str, execution_id: str):
        self.batch_id = batch_id
        self.execution_id = execution_id
        self.batch_group = WorkOrderBatchGroup(batch_id, execution_id)
        self.metrics = BatchMetrics(batch_id)
        self.created_at = datetime.now(timezone.utc)

    def add_work_order(
        self,
        work_order_id: str,
        step_id: str,
        status: str,
        estimated_duration_minutes: int,
        **kwargs,
    ) -> bool:
        """Add a work order to the batch."""
        try:
            work_order_data = {
                "work_order_id": work_order_id,
                "step_id": step_id,
                "status": status,
                "estimated_duration_minutes": estimated_duration_minutes,
                **kwargs,
            }

            self.batch_group.add_work_order(work_order_id, work_order_data)
            self.metrics.add_work_order(status, estimated_duration_minutes)

            logger.debug(f"Added work order {work_order_id} to batch {self.batch_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to add work order: {e}")
            return False

    def update_work_order_status(
        self,
        work_order_id: str,
        new_status: str,
        actual_duration_minutes: Optional[float] = None,
    ) -> bool:
        """Update status of a work order in the batch."""
        try:
            work_order = self.batch_group.get_work_order(work_order_id)
            if not work_order:
                logger.warning(
                    f"Work order {work_order_id} not found in batch {self.batch_id}"
                )
                return False

            old_status = work_order.get("status")
            work_order["status"] = new_status
            work_order["updated_at"] = datetime.now(timezone.utc)

            # Update metrics
            if (
                actual_duration_minutes
                and old_status != "completed"
                and new_status == "completed"
            ):
                estimated = work_order.get("estimated_duration_minutes", 0)
                self.metrics.record_variance(estimated, actual_duration_minutes)

            logger.debug(
                f"Updated work order {work_order_id} status: {old_status} → {new_status}"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to update work order status: {e}")
            return False

    def mark_batch_started(self):
        """Mark batch as started."""
        self.metrics.mark_started()
        logger.info(f"Batch {self.batch_id} marked as started")

    def mark_batch_completed(self):
        """Mark batch as completed."""
        self.metrics.mark_completed()
        logger.info(f"Batch {self.batch_id} marked as completed")

    def get_batch_progress(self) -> Dict[str, Any]:
        """Get overall batch progress."""
        return {
            "batch_id": self.batch_id,
            "execution_id": self.execution_id,
            "completion_percent": self.metrics.get_completion_percent(),
            "total_work_orders": self.metrics.total_work_orders,
            "completed": self.metrics.completed_work_orders,
            "in_progress": self.metrics.in_progress_work_orders,
            "pending": (
                self.metrics.created_work_orders + self.metrics.assigned_work_orders
            ),
            "rejected": self.metrics.rejected_work_orders,
            "on_hold": self.metrics.on_hold_work_orders,
        }

    def get_batch_performance(self) -> Dict[str, Any]:
        """Get batch performance metrics."""
        return {
            "batch_id": self.batch_id,
            "execution_id": self.execution_id,
            "total_estimated_minutes": self.metrics.total_estimated_minutes,
            "total_actual_minutes": self.metrics.total_actual_minutes,
            "average_variance_percent": self.metrics.get_average_variance_percent(),
            "average_variance_minutes": self.metrics.get_average_variance_minutes(),
            "total_variances_tracked": self.metrics.variance_count,
            "elapsed_time_minutes": self.metrics.get_elapsed_time_minutes(),
        }

    def get_step_performance(self, step_id: str) -> Dict[str, Any]:
        """Get performance metrics for a specific step."""
        step_work_orders = self.batch_group.get_work_orders_by_step(step_id)

        if not step_work_orders:
            return {
                "step_id": step_id,
                "work_order_count": 0,
                "completed": 0,
                "in_progress": 0,
            }

        completed = sum(1 for wo in step_work_orders if wo.get("status") == "completed")
        in_progress = sum(
            1 for wo in step_work_orders if wo.get("status") == "in_progress"
        )
        estimated_total = sum(
            wo.get("estimated_duration_minutes", 0) for wo in step_work_orders
        )
        actual_total = sum(
            wo.get("actual_duration_minutes", 0)
            for wo in step_work_orders
            if wo.get("actual_duration_minutes")
        )

        return {
            "step_id": step_id,
            "work_order_count": len(step_work_orders),
            "completed": completed,
            "in_progress": in_progress,
            "total_estimated_minutes": estimated_total,
            "total_actual_minutes": actual_total,
        }

    def get_critical_work_orders(self) -> List[Dict[str, Any]]:
        """Get work orders that are over time."""
        critical = []

        for work_order in self.batch_group.work_orders.values():
            if work_order.get("status") == "in_progress":
                actual = work_order.get("actual_duration_minutes", 0)
                estimated = work_order.get("estimated_duration_minutes", 1)

                if actual > estimated * 1.5:  # Over 50% of estimated time
                    critical.append(work_order)

        return critical

    def get_pending_work_orders(self) -> List[Dict[str, Any]]:
        """Get all pending work orders (created or assigned)."""
        pending_statuses = ["created", "assigned"]
        pending = []

        for work_order in self.batch_group.work_orders.values():
            if work_order.get("status") in pending_statuses:
                pending.append(work_order)

        return pending

    def get_completed_work_orders(self) -> List[Dict[str, Any]]:
        """Get all completed work orders."""
        return self.batch_group.get_work_orders_by_status("completed")

    def get_batch_summary(self) -> Dict[str, Any]:
        """Get complete batch summary."""
        return {
            "batch_id": self.batch_id,
            "execution_id": self.execution_id,
            "created_at": self.created_at.isoformat(),
            "metrics": self.metrics.to_dict(),
            "progress": self.get_batch_progress(),
            "performance": self.get_batch_performance(),
            "critical_work_orders": len(self.get_critical_work_orders()),
            "pending_work_orders": len(self.get_pending_work_orders()),
            "completed_work_orders": len(self.get_completed_work_orders()),
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "batch_id": self.batch_id,
            "execution_id": self.execution_id,
            "created_at": self.created_at.isoformat(),
            "batch_group": self.batch_group.to_dict(),
            "metrics": self.metrics.to_dict(),
            "progress": self.get_batch_progress(),
            "performance": self.get_batch_performance(),
        }
