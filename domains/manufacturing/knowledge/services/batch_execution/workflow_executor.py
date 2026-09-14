"""
Batch Workflow Executor - Executes batch production workflows step-by-step.

This module handles the actual execution of batch workflows defined by the
logical planner and DAG builder. It:
- Executes workflow steps sequentially
- Validates quality checkpoints
- Tracks execution state
- Manages step dependencies
- Records results and issues
- Handles errors and retries
"""

import asyncio
import logging
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

logger = logging.getLogger(__name__)


class StepStatus(Enum):
    """Status of a workflow step."""

    PENDING = "pending"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    QC_CHECK = "qc_check"
    QC_PASSED = "qc_passed"
    QC_FAILED = "qc_failed"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class QCCheckpointStatus(Enum):
    """Status of a quality checkpoint."""

    PENDING = "pending"
    EXECUTING = "executing"
    PASSED = "passed"
    FAILED = "failed"
    WARNED = "warned"


class WorkflowExecutionStatus(Enum):
    """Status of a workflow execution."""

    CREATED = "created"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class QCCheckpoint:
    """A quality checkpoint in a workflow step."""

    def __init__(self, checkpoint_id: str, name: str, description: str):
        self.checkpoint_id = checkpoint_id
        self.name = name
        self.description = description
        self.status = QCCheckpointStatus.PENDING
        self.result = None
        self.started_at = None
        self.completed_at = None
        self.error = None

    async def execute(self) -> bool:
        """Execute this quality checkpoint."""
        self.status = QCCheckpointStatus.EXECUTING
        self.started_at = datetime.utcnow()

        try:
            # Simulate QC checkpoint execution
            # In production, this would call actual QC validation logic
            await asyncio.sleep(0.1)  # Simulate execution time

            # Mark as passed (can be configured to fail for testing)
            self.status = QCCheckpointStatus.PASSED
            self.result = True
            self.completed_at = datetime.utcnow()
            return True

        except Exception as e:
            self.status = QCCheckpointStatus.FAILED
            self.error = str(e)
            self.result = False
            self.completed_at = datetime.utcnow()
            return False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "checkpoint_id": self.checkpoint_id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "result": self.result,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "error": self.error,
        }


class WorkflowStep:
    """A single step in a batch workflow."""

    def __init__(
        self,
        step_id: str,
        name: str,
        description: str,
        owner_role: str,
        estimated_duration_minutes: int,
        dependencies: Optional[List[str]] = None,
        quality_checkpoints: Optional[List[Dict[str, str]]] = None,
    ):
        self.step_id = step_id
        self.name = name
        self.description = description
        self.owner_role = owner_role
        self.estimated_duration_minutes = estimated_duration_minutes
        self.dependencies = dependencies or []

        # Create QC checkpoints
        self.qc_checkpoints = []
        for cp in quality_checkpoints or []:
            checkpoint = QCCheckpoint(
                checkpoint_id=cp.get("id", f"qc_{self.step_id}_{uuid4().hex[:8]}"),
                name=cp.get("name", ""),
                description=cp.get("description", ""),
            )
            self.qc_checkpoints.append(checkpoint)

        # Execution tracking
        self.status = StepStatus.PENDING
        self.started_at = None
        self.completed_at = None
        self.duration_seconds = 0
        self.error = None
        self.result = {}

    async def execute(self) -> bool:
        """Execute this step and all its quality checkpoints."""
        self.status = StepStatus.IN_PROGRESS
        self.started_at = datetime.utcnow()

        try:
            # Simulate step execution
            execution_time = self.estimated_duration_minutes / 10  # Scale for testing
            await asyncio.sleep(execution_time)

            # Execute quality checkpoints
            self.status = StepStatus.QC_CHECK
            all_passed = True

            for checkpoint in self.qc_checkpoints:
                passed = await checkpoint.execute()
                if not passed:
                    all_passed = False
                    logger.warning(
                        f"QC checkpoint failed in step {self.step_id}: {checkpoint.name}"
                    )

            # Mark as completed
            self.completed_at = datetime.utcnow()
            self.duration_seconds = (
                self.completed_at - self.started_at
            ).total_seconds()

            if all_passed:
                self.status = StepStatus.COMPLETED
                self.result = {"success": True, "qc_passed": True}
                return True
            else:
                self.status = StepStatus.QC_FAILED
                self.result = {"success": False, "qc_passed": False}
                return False

        except Exception as e:
            self.status = StepStatus.FAILED
            self.error = str(e)
            self.completed_at = datetime.utcnow()
            self.duration_seconds = (
                self.completed_at - self.started_at
            ).total_seconds()
            logger.error(f"Step execution failed: {self.step_id}: {e}")
            return False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "step_id": self.step_id,
            "name": self.name,
            "description": self.description,
            "owner_role": self.owner_role,
            "estimated_duration_minutes": self.estimated_duration_minutes,
            "dependencies": self.dependencies,
            "status": self.status.value,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "duration_seconds": self.duration_seconds,
            "qc_checkpoints": [cp.to_dict() for cp in self.qc_checkpoints],
            "qc_passed": all(
                cp.status == QCCheckpointStatus.PASSED for cp in self.qc_checkpoints
            ),
            "error": self.error,
            "result": self.result,
        }


class BatchWorkflowExecution:
    """Manages execution of a complete batch workflow."""

    def __init__(
        self,
        batch_id: str,
        workflow_id: str,
        batch_details: Dict[str, Any],
        workflow_steps: List[Dict[str, Any]],
    ):
        self.batch_id = batch_id
        self.workflow_id = workflow_id
        self.execution_id = f"exec_{batch_id}_{uuid4().hex[:8]}"
        self.batch_details = batch_details
        self.status = WorkflowExecutionStatus.CREATED

        # Create workflow steps
        self.steps: List[WorkflowStep] = []
        self.steps_by_id: Dict[str, WorkflowStep] = {}

        for step_data in workflow_steps:
            step = WorkflowStep(
                step_id=step_data.get("step_id"),
                name=step_data.get("name"),
                description=step_data.get("description"),
                owner_role=step_data.get("operator_role"),
                estimated_duration_minutes=step_data.get(
                    "estimated_duration_minutes", 0
                ),
                dependencies=step_data.get("dependencies", []),
                quality_checkpoints=[
                    {"name": cp, "description": f"Quality check: {cp}"}
                    for cp in step_data.get("quality_checkpoints", [])
                ],
            )
            self.steps.append(step)
            self.steps_by_id[step.step_id] = step

        # Execution tracking
        self.created_at = datetime.utcnow()
        self.started_at = None
        self.completed_at = None
        self.current_step_idx = -1
        self.execution_log: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, Any]] = []

    def _log_event(self, level: str, message: str, **kwargs):
        """Log an execution event."""
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": level,
            "message": message,
            "execution_id": self.execution_id,
            **kwargs,
        }
        self.execution_log.append(event)
        logger.log(
            getattr(logging, level),
            f"[{self.execution_id}] {message}",
        )

    def _can_execute_step(self, step: WorkflowStep) -> bool:
        """Check if a step's dependencies are satisfied."""
        for dep_id in step.dependencies:
            dep_step = self.steps_by_id.get(dep_id)
            if not dep_step or dep_step.status != StepStatus.COMPLETED:
                return False
        return True

    async def execute(self) -> bool:
        """Execute the entire workflow."""
        self.status = WorkflowExecutionStatus.READY
        self._log_event("INFO", "Workflow execution ready", batch_id=self.batch_id)

        self.status = WorkflowExecutionStatus.RUNNING
        self.started_at = datetime.utcnow()
        self._log_event(
            "INFO",
            "Workflow execution started",
            batch_id=self.batch_id,
            step_count=len(self.steps),
        )

        try:
            # Execute steps sequentially
            for idx, step in enumerate(self.steps):
                self.current_step_idx = idx

                # Check dependencies
                if not self._can_execute_step(step):
                    self._log_event(
                        "ERROR",
                        f"Step {step.step_id} dependencies not satisfied",
                        step_id=step.step_id,
                    )
                    step.status = StepStatus.SKIPPED
                    continue

                # Execute step
                self._log_event(
                    "INFO",
                    f"Starting step {idx + 1}/{len(self.steps)}: {step.name}",
                    step_id=step.step_id,
                    step_index=idx,
                )

                try:
                    success = await step.execute()

                    if success:
                        self._log_event(
                            "INFO",
                            f"Step completed: {step.name}",
                            step_id=step.step_id,
                            duration_seconds=step.duration_seconds,
                            qc_passed=True,
                        )
                    else:
                        self._log_event(
                            "WARNING",
                            f"Step QC check failed: {step.name}",
                            step_id=step.step_id,
                            qc_passed=False,
                        )
                        # Continue execution but mark as QC failure

                except Exception as e:
                    self._log_event(
                        "ERROR",
                        f"Step execution error: {step.name}",
                        step_id=step.step_id,
                        error=str(e),
                    )
                    self.errors.append(
                        {
                            "step_id": step.step_id,
                            "error": str(e),
                            "timestamp": datetime.utcnow().isoformat(),
                        }
                    )
                    # Continue to next step

            # Determine final status
            all_completed = all(
                s.status in [StepStatus.COMPLETED, StepStatus.QC_PASSED]
                for s in self.steps
            )
            any_qc_failed = any(s.status == StepStatus.QC_FAILED for s in self.steps)
            any_failed = any(s.status == StepStatus.FAILED for s in self.steps)

            self.completed_at = datetime.utcnow()

            if all_completed and not any_failed:
                self.status = WorkflowExecutionStatus.COMPLETED
                self._log_event(
                    "INFO",
                    "Workflow execution completed successfully",
                    total_duration_seconds=(
                        self.completed_at - self.started_at
                    ).total_seconds(),
                    qc_issues=any_qc_failed,
                )
                return True
            elif any_failed:
                self.status = WorkflowExecutionStatus.FAILED
                self._log_event(
                    "ERROR",
                    "Workflow execution failed",
                    error_count=len(self.errors),
                )
                return False
            else:
                self.status = WorkflowExecutionStatus.COMPLETED
                self._log_event(
                    "WARNING",
                    "Workflow execution completed with QC issues",
                    qc_issues=True,
                )
                return True

        except Exception as e:
            self.status = WorkflowExecutionStatus.FAILED
            self.completed_at = datetime.utcnow()
            self._log_event("ERROR", f"Workflow execution failed: {e}", error=str(e))
            self.errors.append(
                {
                    "workflow": "execution",
                    "error": str(e),
                    "timestamp": datetime.utcnow().isoformat(),
                }
            )
            return False

    def get_summary(self) -> Dict[str, Any]:
        """Get execution summary."""
        total_duration = 0
        if self.completed_at and self.started_at:
            total_duration = (self.completed_at - self.started_at).total_seconds()

        completed_steps = [s for s in self.steps if s.status == StepStatus.COMPLETED]
        qc_passed = sum(
            1
            for s in self.steps
            if all(cp.status == QCCheckpointStatus.PASSED for cp in s.qc_checkpoints)
        )
        total_qc_checkpoints = sum(len(s.qc_checkpoints) for s in self.steps)
        passed_checkpoints = sum(
            sum(1 for cp in s.qc_checkpoints if cp.status == QCCheckpointStatus.PASSED)
            for s in self.steps
        )

        return {
            "execution_id": self.execution_id,
            "batch_id": self.batch_id,
            "workflow_id": self.workflow_id,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "total_duration_seconds": total_duration,
            "total_steps": len(self.steps),
            "completed_steps": len(completed_steps),
            "steps_with_qc_passed": qc_passed,
            "total_qc_checkpoints": total_qc_checkpoints,
            "passed_qc_checkpoints": passed_checkpoints,
            "qc_pass_rate": (
                f"{(passed_checkpoints / total_qc_checkpoints * 100):.1f}%"
                if total_qc_checkpoints > 0
                else "N/A"
            ),
            "error_count": len(self.errors),
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "execution_id": self.execution_id,
            "batch_id": self.batch_id,
            "workflow_id": self.workflow_id,
            "batch_details": self.batch_details,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "total_duration_seconds": (
                (self.completed_at - self.started_at).total_seconds()
                if self.completed_at and self.started_at
                else 0
            ),
            "steps": [step.to_dict() for step in self.steps],
            "summary": self.get_summary(),
            "execution_log": self.execution_log,
            "errors": self.errors,
        }
