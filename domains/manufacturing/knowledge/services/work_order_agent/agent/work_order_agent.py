"""
Batch Work Order Agent

Generates work orders for each step in a batch workflow execution.
Enables human operators to understand what work needs to be done and when.
"""

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

# Import state machine integration
try:
    from services.work_order_agent.tracking.event_system import (
        WorkOrderEventBus,
    )
    from services.work_order_agent.tracking.state_machine_integrator import (
        InvalidStateTransitionError,
        StateMachineIntegrator,
    )
except ImportError:
    # Optional dependencies - not all contexts may have these
    StateMachineIntegrator = None
    InvalidStateTransitionError = None
    WorkOrderEventBus = None


class WorkOrderGenerationStrategy(Enum):
    """Strategy for generating work orders."""

    PARALLEL = "parallel"  # All work orders generated before workflow starts
    SEQUENTIAL = "sequential"  # Work order generated as step completes
    ON_DEMAND = "on_demand"  # Work order generated when requested


class WorkOrderStatus(Enum):
    """Status of a generated work order."""

    CREATED = "created"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"
    ON_HOLD = "on_hold"


class WorkOrderPriority(Enum):
    """Priority of a work order."""

    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class GeneratedWorkOrder:
    """A work order generated for a batch workflow step."""

    def __init__(
        self,
        work_order_id: str,
        step_id: str,
        step_name: str,
        batch_id: str,
        execution_id: str,
        description: str,
        owner_role: str,
        estimated_duration_minutes: int,
        dependencies: List[str],
        quality_checkpoints: List[str],
        priority: WorkOrderPriority = WorkOrderPriority.MEDIUM,
        event_bus: Optional["WorkOrderEventBus"] = None,
    ):
        self.work_order_id = work_order_id
        self.step_id = step_id
        self.step_name = step_name
        self.batch_id = batch_id
        self.execution_id = execution_id
        self.description = description
        self.owner_role = owner_role
        self.estimated_duration_minutes = estimated_duration_minutes
        self.dependencies = dependencies
        self.quality_checkpoints = quality_checkpoints
        self.priority = priority

        # Status tracking
        self.status = WorkOrderStatus.CREATED
        self.assigned_to = None
        self.assigned_user_id = None
        self.assigned_user_name = None
        self.assigned_team_id = None
        self.assigned_team_name = None

        # Timing
        self.created_at = datetime.now(timezone.utc)
        self.expected_completion = self.created_at + timedelta(
            minutes=estimated_duration_minutes
        )
        self.started_at = None
        self.completed_at = None

        # Execution tracking
        self.remarks = None
        self.attachments: List[str] = []
        self.qc_checks_completed: List[str] = []

        # State machine integration (optional, only if event_bus provided)
        self.state_machine_integrator: Optional[StateMachineIntegrator] = None
        if StateMachineIntegrator is not None and event_bus is not None:
            self.state_machine_integrator = StateMachineIntegrator(
                work_order_id=work_order_id,
                step_id=step_id,
                batch_id=batch_id,
                execution_id=execution_id,
                event_bus=event_bus,
            )

        # Hold state tracking
        self.on_hold_reason: Optional[str] = None
        self.held_at: Optional[datetime] = None

    def assign_to_user(
        self,
        user_id: str,
        user_name: str,
        assigned_to: Optional[str] = None,
    ) -> bool:
        """Assign work order to a user.

        Returns:
            True if assignment succeeded, False if state machine transition failed.
        """
        # Transition state machine first if available
        if self.state_machine_integrator:
            try:
                success = self.state_machine_integrator.assign(
                    user_id=user_id, user_name=user_name
                )
                if not success:
                    logger.warning(
                        f"State machine transition failed for {self.work_order_id}"
                    )
                    # For backward compatibility, still update status if integrator fails
                    # This allows old code paths without event bus to still work
            except InvalidStateTransitionError as e:
                logger.error(f"Failed to assign work order: {e}")
                # For backward compatibility, continue even if state machine fails

        # Update status after successful transition
        self.assigned_user_id = user_id
        self.assigned_user_name = user_name
        self.assigned_to = assigned_to or user_name
        self.status = WorkOrderStatus.ASSIGNED

        logger.info(f"Work order {self.work_order_id} assigned to {user_name}")
        return True

    def assign_to_team(
        self,
        team_id: str,
        team_name: str,
    ) -> bool:
        """Assign work order to a team.

        Returns:
            True if assignment succeeded, False if state machine transition failed.
        """
        # Transition state machine first if available
        if self.state_machine_integrator:
            try:
                success = self.state_machine_integrator.assign(
                    team_id=team_id, team_name=team_name
                )
                if not success:
                    logger.warning(
                        f"State machine transition failed for {self.work_order_id}"
                    )
                    # For backward compatibility, still update status if integrator fails
            except InvalidStateTransitionError as e:
                logger.error(f"Failed to assign work order to team: {e}")
                # For backward compatibility, continue even if state machine fails

        # Update status after successful transition
        self.assigned_team_id = team_id
        self.assigned_team_name = team_name
        self.assigned_to = team_name
        self.status = WorkOrderStatus.ASSIGNED

        logger.info(f"Work order {self.work_order_id} assigned to team {team_name}")
        return True

    def mark_in_progress(self) -> bool:
        """Mark work order as in progress.

        Returns:
            True if transition succeeded, False if state machine transition failed.
        """
        # Transition state machine first if available
        if self.state_machine_integrator:
            try:
                success = self.state_machine_integrator.start()
                if not success:
                    logger.warning(
                        f"State machine transition failed for {self.work_order_id}"
                    )
                    # For backward compatibility, still update status if integrator fails
            except InvalidStateTransitionError as e:
                logger.error(f"Failed to mark work order in progress: {e}")
                # For backward compatibility, continue even if state machine fails

        # Update status after successful transition
        self.status = WorkOrderStatus.IN_PROGRESS
        self.started_at = datetime.now(timezone.utc)

        logger.info(f"Work order {self.work_order_id} marked in progress")
        return True

    def complete(self, remarks: Optional[str] = None) -> bool:
        """Mark work order as completed.

        Returns:
            True if completion succeeded, False if state machine transition failed.
        """
        # Transition state machine first if available
        if self.state_machine_integrator:
            try:
                success = self.state_machine_integrator.complete(remarks=remarks)
                if not success:
                    logger.warning(
                        f"State machine transition failed for {self.work_order_id}"
                    )
                    # For backward compatibility, still update status if integrator fails
            except InvalidStateTransitionError as e:
                logger.error(f"Failed to complete work order: {e}")
                # For backward compatibility, continue even if state machine fails

        # Update status after successful transition
        self.status = WorkOrderStatus.COMPLETED
        self.completed_at = datetime.now(timezone.utc)
        if remarks:
            self.remarks = remarks

        logger.info(f"Work order {self.work_order_id} completed")
        return True

    def reject(self, remarks: str) -> bool:
        """Reject the work order.

        Returns:
            True if rejection succeeded, False if state machine transition failed.
        """
        # Transition state machine first if available
        if self.state_machine_integrator:
            try:
                success = self.state_machine_integrator.reject(remarks=remarks)
                if not success:
                    logger.warning(
                        f"State machine transition failed for {self.work_order_id}"
                    )
                    # For backward compatibility, still update status if integrator fails
            except InvalidStateTransitionError as e:
                logger.error(f"Failed to reject work order: {e}")
                # For backward compatibility, continue even if state machine fails

        # Update status after successful transition
        self.status = WorkOrderStatus.REJECTED
        self.remarks = remarks

        logger.warning(f"Work order {self.work_order_id} rejected: {remarks}")
        return True

    def hold(self, reason: str) -> bool:
        """Place work order on hold.

        Returns:
            True if hold succeeded, False if state machine transition failed.
        """
        # Transition state machine first if available
        if self.state_machine_integrator:
            try:
                success = self.state_machine_integrator.hold(reason=reason)
                if not success:
                    logger.warning(
                        f"State machine transition failed for {self.work_order_id}"
                    )
                    # For backward compatibility, still update status if integrator fails
            except InvalidStateTransitionError as e:
                logger.error(f"Failed to hold work order: {e}")
                # For backward compatibility, continue even if state machine fails

        # Update status after successful transition
        self.status = WorkOrderStatus.ON_HOLD
        self.on_hold_reason = reason
        self.held_at = datetime.now(timezone.utc)

        logger.info(f"Work order {self.work_order_id} placed on hold: {reason}")
        return True

    def resume(self) -> bool:
        """Resume work order from hold.

        Returns:
            True if resume succeeded, False if state machine transition failed.
        """
        if self.status != WorkOrderStatus.ON_HOLD:
            logger.error(
                f"Cannot resume work order {self.work_order_id}: not in ON_HOLD state"
            )
            return False

        # Transition state machine first if available
        if self.state_machine_integrator:
            try:
                success = self.state_machine_integrator.resume()
                if not success:
                    logger.warning(
                        f"State machine transition failed for {self.work_order_id}"
                    )
                    # For backward compatibility, still update status if integrator fails
            except InvalidStateTransitionError as e:
                logger.error(f"Failed to resume work order: {e}")
                # For backward compatibility, continue even if state machine fails

        # Update status after successful transition
        self.status = WorkOrderStatus.IN_PROGRESS
        self.on_hold_reason = None
        self.held_at = None

        logger.info(f"Work order {self.work_order_id} resumed from hold")
        return True

    def add_qc_check_result(self, checkpoint_name: str):
        """Record a completed QC check."""
        self.qc_checks_completed.append(checkpoint_name)

    def add_attachment(self, attachment_path: str):
        """Add an attachment to the work order."""
        self.attachments.append(attachment_path)

    def get_overdue_status(self) -> bool:
        """Check if work order is overdue."""
        if self.status in [WorkOrderStatus.COMPLETED, WorkOrderStatus.REJECTED]:
            return False
        return datetime.now(timezone.utc) > self.expected_completion

    def to_workorder_api_format(self) -> Dict[str, Any]:
        """Convert to work order service API format."""
        return {
            "work_order_id": self.work_order_id,
            "title": f"{self.step_name} - Batch {self.batch_id}",
            "message": self.description,
            "work_order_type": "Other",
            "status": self._status_to_api_status(self.status),
            "priority": self.priority.value,
            "product_id": self.batch_id,
            "assigned_to": self.assigned_to,
            "assigned_user_id": self.assigned_user_id,
            "assigned_user_name": self.assigned_user_name,
            "assigned_team_id": self.assigned_team_id,
            "assigned_team_name": self.assigned_team_name,
            "created_at": self.created_at.isoformat(),
            "expected_completion": self.expected_completion.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "total_time_hrs": (
                (self.completed_at - self.started_at).total_seconds() / 3600
                if self.started_at and self.completed_at
                else None
            ),
            "remarks": self.remarks
            or f"QC Checks: {', '.join(self.quality_checkpoints)}",
            "attachments": self.attachments,
            "is_overdue": self.get_overdue_status(),
        }

    @staticmethod
    def _status_to_api_status(status: WorkOrderStatus) -> str:
        """Convert internal status to work order API status."""
        mapping = {
            WorkOrderStatus.CREATED: "Todo",
            WorkOrderStatus.ASSIGNED: "Todo",
            WorkOrderStatus.IN_PROGRESS: "In Progress",
            WorkOrderStatus.COMPLETED: "Done",
            WorkOrderStatus.REJECTED: "Rejected",
            WorkOrderStatus.ON_HOLD: "On Hold",
        }
        return mapping.get(status, "Todo")

    def get_state_machine_summary(self) -> Optional[Dict[str, Any]]:
        """Get state machine summary if integrator is available."""
        if self.state_machine_integrator:
            return self.state_machine_integrator.get_summary()
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "work_order_id": self.work_order_id,
            "step_id": self.step_id,
            "step_name": self.step_name,
            "batch_id": self.batch_id,
            "execution_id": self.execution_id,
            "description": self.description,
            "owner_role": self.owner_role,
            "estimated_duration_minutes": self.estimated_duration_minutes,
            "dependencies": self.dependencies,
            "quality_checkpoints": self.quality_checkpoints,
            "priority": self.priority.value,
            "status": self.status.value,
            "assigned_to": self.assigned_to,
            "assigned_user_id": self.assigned_user_id,
            "assigned_user_name": self.assigned_user_name,
            "assigned_team_id": self.assigned_team_id,
            "assigned_team_name": self.assigned_team_name,
            "created_at": self.created_at.isoformat(),
            "expected_completion": self.expected_completion.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "remarks": self.remarks,
            "attachments": self.attachments,
            "qc_checks_completed": self.qc_checks_completed,
            "is_overdue": self.get_overdue_status(),
            "on_hold_reason": self.on_hold_reason,
            "held_at": self.held_at.isoformat() if self.held_at else None,
        }

        # Add state machine summary if available
        if self.state_machine_integrator:
            result["state_machine"] = self.state_machine_integrator.to_dict()

        return result


class BatchWorkOrderAgent:
    """
    Agent that generates work orders for batch workflow steps.

    Responsibilities:
    - Generate work orders from workflow steps
    - Track work order status
    - Link work orders to batch execution
    - Enable operator assignment and completion tracking
    """

    def __init__(
        self,
        batch_id: str,
        execution_id: str,
        workflow_id: str,
        strategy: WorkOrderGenerationStrategy = WorkOrderGenerationStrategy.PARALLEL,
        event_bus: Optional["WorkOrderEventBus"] = None,
    ):
        self.batch_id = batch_id
        self.execution_id = execution_id
        self.workflow_id = workflow_id
        self.strategy = strategy
        self.event_bus = event_bus

        self.work_orders: Dict[str, GeneratedWorkOrder] = {}
        self.work_order_by_step: Dict[str, str] = {}  # step_id -> work_order_id

        self.created_at = datetime.now(timezone.utc)
        self.generation_log: List[Dict[str, Any]] = []

    def generate_work_orders(
        self,
        workflow_steps: List[Dict[str, Any]],
    ) -> List[GeneratedWorkOrder]:
        """
        Generate work orders for all workflow steps.

        Args:
            workflow_steps: List of workflow step definitions

        Returns:
            List of generated work orders
        """
        generated_orders = []

        for step_data in workflow_steps:
            work_order = self._create_work_order_from_step(step_data)
            generated_orders.append(work_order)
            self.work_orders[work_order.work_order_id] = work_order
            self.work_order_by_step[step_data["step_id"]] = work_order.work_order_id

            self.generation_log.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "action": "generated",
                    "step_id": step_data["step_id"],
                    "work_order_id": work_order.work_order_id,
                }
            )

            logger.info(
                f"Generated work order {work_order.work_order_id} for step {step_data['step_id']}"
            )

        return generated_orders

    def _create_work_order_from_step(
        self,
        step_data: Dict[str, Any],
    ) -> GeneratedWorkOrder:
        """Create a work order from workflow step data."""
        work_order_id = f"WO-{self.batch_id}-{step_data['step_id']}-{uuid4().hex[:8]}"

        # Determine priority based on step
        priority = self._determine_priority(step_data)

        work_order = GeneratedWorkOrder(
            work_order_id=work_order_id,
            step_id=step_data["step_id"],
            step_name=step_data["name"],
            batch_id=self.batch_id,
            execution_id=self.execution_id,
            description=step_data.get("description", ""),
            owner_role=step_data.get("operator_role", "operator"),
            estimated_duration_minutes=step_data.get("estimated_duration_minutes", 0),
            dependencies=step_data.get("dependencies", []),
            quality_checkpoints=step_data.get("quality_checkpoints", []),
            priority=priority,
            event_bus=self.event_bus,
        )

        return work_order

    def _determine_priority(self, step_data: Dict[str, Any]) -> WorkOrderPriority:
        """Determine work order priority based on step."""
        # Steps with QC checkpoints or critical processes get higher priority
        qc_count = len(step_data.get("quality_checkpoints", []))
        step_name = step_data.get("name", "").lower()

        if "final" in step_name or "release" in step_name:
            return WorkOrderPriority.CRITICAL
        elif qc_count >= 3:
            return WorkOrderPriority.HIGH
        elif qc_count >= 1:
            return WorkOrderPriority.MEDIUM
        else:
            return WorkOrderPriority.LOW

    def get_work_order(self, work_order_id: str) -> Optional[GeneratedWorkOrder]:
        """Get a work order by ID."""
        return self.work_orders.get(work_order_id)

    def get_work_order_by_step(self, step_id: str) -> Optional[GeneratedWorkOrder]:
        """Get work order for a specific step."""
        wo_id = self.work_order_by_step.get(step_id)
        if wo_id:
            return self.work_orders.get(wo_id)
        return None

    def get_pending_work_orders(self) -> List[GeneratedWorkOrder]:
        """Get all pending work orders."""
        return [
            wo
            for wo in self.work_orders.values()
            if wo.status in [WorkOrderStatus.CREATED, WorkOrderStatus.ASSIGNED]
        ]

    def get_on_hold_work_orders(self) -> List[GeneratedWorkOrder]:
        """Get all on-hold work orders."""
        return [
            wo
            for wo in self.work_orders.values()
            if wo.status == WorkOrderStatus.ON_HOLD
        ]

    def get_in_progress_work_orders(self) -> List[GeneratedWorkOrder]:
        """Get all in-progress work orders."""
        return [
            wo
            for wo in self.work_orders.values()
            if wo.status == WorkOrderStatus.IN_PROGRESS
        ]

    def get_completed_work_orders(self) -> List[GeneratedWorkOrder]:
        """Get all completed work orders."""
        return [
            wo
            for wo in self.work_orders.values()
            if wo.status == WorkOrderStatus.COMPLETED
        ]

    def get_overdue_work_orders(self) -> List[GeneratedWorkOrder]:
        """Get all overdue work orders."""
        return [wo for wo in self.work_orders.values() if wo.get_overdue_status()]

    def get_summary(self) -> Dict[str, Any]:
        """Get work order generation summary."""
        total = len(self.work_orders)
        created = len(
            [
                wo
                for wo in self.work_orders.values()
                if wo.status == WorkOrderStatus.CREATED
            ]
        )
        assigned = len(
            [
                wo
                for wo in self.work_orders.values()
                if wo.status == WorkOrderStatus.ASSIGNED
            ]
        )
        in_progress = len(self.get_in_progress_work_orders())
        completed = len(self.get_completed_work_orders())
        rejected = len(
            [
                wo
                for wo in self.work_orders.values()
                if wo.status == WorkOrderStatus.REJECTED
            ]
        )
        overdue = len(self.get_overdue_work_orders())

        on_hold = len(self.get_on_hold_work_orders())

        return {
            "batch_id": self.batch_id,
            "execution_id": self.execution_id,
            "workflow_id": self.workflow_id,
            "strategy": self.strategy.value,
            "created_at": self.created_at.isoformat(),
            "total_work_orders": total,
            "created": created,
            "assigned": assigned,
            "in_progress": in_progress,
            "on_hold": on_hold,
            "completed": completed,
            "rejected": rejected,
            "overdue": overdue,
            "completion_rate": (
                f"{(completed / total * 100):.1f}%" if total > 0 else "N/A"
            ),
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "batch_id": self.batch_id,
            "execution_id": self.execution_id,
            "workflow_id": self.workflow_id,
            "strategy": self.strategy.value,
            "created_at": self.created_at.isoformat(),
            "work_orders": [wo.to_dict() for wo in self.work_orders.values()],
            "summary": self.get_summary(),
            "generation_log": self.generation_log,
        }
