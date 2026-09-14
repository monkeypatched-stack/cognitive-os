"""
Batch Planning Agent - Production Agent

Handles:
1. Batch creation with unique identification
2. Query unique batches by product, size, route, template version
3. Batch execution planning and tracking
4. Integration with manufacturing workflows
"""

import logging
import time
from typing import Any, Optional
from uuid import uuid4

from src.monkey_brain.execution.agents.protocol import AgentProtocol, ExecutionContext
from services.production.models.batch import (
    Batch,
    BatchExecution,
    BatchQuery,
    BatchQueryResult,
    BatchRoute,
    BatchSize,
    BatchStatus,
    BatchTemplate,
    Product,
)

logger = logging.getLogger(__name__)


class BatchPlanningAgent(AgentProtocol):
    """
    Production Batch Planning Agent - First-class entity.

    Capabilities:
    - create_batch: Create new batch with unique identification
    - query_batches: Query unique batches by product, size, route, template
    - start_batch_execution: Begin batch execution
    - update_batch_execution: Track execution progress
    - complete_batch_execution: Finalize batch execution
    - get_batch_details: Get complete batch information
    """

    def __init__(
        self,
        agent_id: str = "batch_planning_agent_v1",
        batch_store: Optional[dict] = None,
    ):
        """Initialize Batch Planning Agent.

        Args:
            agent_id: Unique agent identifier
            batch_store: Optional in-memory batch storage (for testing)
        """
        self._agent_id = agent_id
        self.batch_store: dict[str, Batch] = batch_store or {}
        self.execution_store: dict[str, BatchExecution] = {}
        self.execution_log: list[dict] = []

    @property
    def agent_id(self) -> str:
        """Agent identifier."""
        return self._agent_id

    @property
    def capabilities(self) -> list[str]:
        """List of agent capabilities."""
        return [
            "create_batch",
            "query_batches",
            "update_batch",
            "delete_batch",
            "hold_batch",
            "close_batch",
            "start_batch_execution",
            "update_batch_execution",
            "complete_batch_execution",
            "get_batch_details",
            "list_batches_by_status",
            "validate_batch_config",
        ]

    async def execute(
        self,
        node_id: str,
        operator_type: str,
        context: ExecutionContext,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a batch planning operation.

        Args:
            node_id: Workflow node ID
            operator_type: Operation type (must be in capabilities)
            context: Execution context
            inputs: Operation inputs

        Returns:
            Operation result
        """
        self.execution_log.append(
            {
                "timestamp": time.time(),
                "operator_type": operator_type,
                "node_id": node_id,
            }
        )

        try:
            if operator_type == "create_batch":
                return await self._create_batch(inputs, context)
            elif operator_type == "query_batches":
                return await self._query_batches(inputs, context)
            elif operator_type == "update_batch":
                return await self._update_batch(inputs, context)
            elif operator_type == "delete_batch":
                return await self._delete_batch(inputs, context)
            elif operator_type == "hold_batch":
                return await self._hold_batch(inputs, context)
            elif operator_type == "close_batch":
                return await self._close_batch(inputs, context)
            elif operator_type == "start_batch_execution":
                return await self._start_execution(inputs, context)
            elif operator_type == "update_batch_execution":
                return await self._update_execution(inputs, context)
            elif operator_type == "complete_batch_execution":
                return await self._complete_execution(inputs, context)
            elif operator_type == "get_batch_details":
                return await self._get_batch_details(inputs, context)
            elif operator_type == "list_batches_by_status":
                return await self._list_by_status(inputs, context)
            elif operator_type == "validate_batch_config":
                return await self._validate_config(inputs, context)
            else:
                return {
                    "status": "error",
                    "message": f"Unknown operator: {operator_type}",
                }

        except Exception as e:
            logger.error(f"Batch operation failed: {e}")
            return {"status": "error", "message": str(e)}

    async def can_execute(self, operator_type: str) -> bool:
        """Check if agent can execute operation."""
        return operator_type in self.capabilities

    async def _create_batch(
        self, inputs: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        """Create a new production batch.

        Required inputs:
        - lot_number: Unique lot number
        - product_id: Product ID
        - product_name: Product name
        - sku: Stock keeping unit
        - size_quantity: Batch quantity
        - size_unit: Quantity unit (kg, L, units, etc.)
        - route_id: Manufacturing route ID
        - route_name: Route name
        - template_id: Process template ID
        - template_name: Template name
        - template_version: Template version
        """
        try:
            # Create product
            product = Product(
                product_id=inputs["product_id"],
                product_name=inputs["product_name"],
                sku=inputs["sku"],
                product_code=inputs.get("product_code"),
                category=inputs.get("category"),
            )

            # Create batch size
            size = BatchSize(
                quantity=inputs["size_quantity"],
                unit=inputs["size_unit"],
            )

            # Create route
            route = BatchRoute(
                route_id=inputs["route_id"],
                route_name=inputs["route_name"],
                version=inputs.get("route_version", "1.0"),
                description=inputs.get("route_description"),
            )

            # Create template
            template = BatchTemplate(
                template_id=inputs["template_id"],
                template_name=inputs["template_name"],
                version=inputs["template_version"],
                revision=inputs.get("template_revision"),
            )

            # Create batch
            batch = Batch(
                lot_number=inputs["lot_number"],
                product=product,
                size=size,
                route=route,
                template=template,
                created_by=context.user_id,
                workstation_id=inputs.get("workstation_id"),
            )

            # Store batch
            self.batch_store[batch.batch_id] = batch

            logger.info(f"Batch created: {batch.get_identifier()}")

            return {
                "status": "success",
                "batch_id": batch.batch_id,
                "lot_number": batch.lot_number,
                "identifier": batch.get_identifier(),
                "message": f"Batch {batch.lot_number} created successfully",
            }

        except Exception as e:
            logger.error(f"Batch creation failed: {e}")
            return {"status": "error", "message": str(e)}

    async def _query_batches(
        self, inputs: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        """Query unique batches by criteria.

        Inputs:
        - product_id: Required - Product ID
        - size_quantity: Optional - Batch quantity
        - size_unit: Optional - Quantity unit
        - route_id: Optional - Route ID
        - template_version: Optional - Template version
        - status: Optional - Batch status
        """
        try:
            start_time = time.time()

            query = BatchQuery(
                product_id=inputs["product_id"],
                size_quantity=inputs.get("size_quantity"),
                size_unit=inputs.get("size_unit"),
                route_id=inputs.get("route_id"),
                template_version=inputs.get("template_version"),
                status=inputs.get("status"),
            )

            # Filter batches
            matching = []
            for batch in self.batch_store.values():
                if batch.product.product_id != query.product_id:
                    continue

                if query.size_quantity and batch.size.quantity != query.size_quantity:
                    continue

                if query.size_unit and batch.size.unit != query.size_unit:
                    continue

                if query.route_id and batch.route.route_id != query.route_id:
                    continue

                if (
                    query.template_version
                    and batch.template.version != query.template_version
                ):
                    continue

                if query.status and batch.status != query.status:
                    continue

                matching.append(batch)

            query_time = (time.time() - start_time) * 1000  # ms

            result = BatchQueryResult(
                total_count=len(matching),
                matching_batches=matching,
                query_time_ms=query_time,
            )

            return {
                "status": "success",
                "query": query.model_dump(),
                "total_count": result.total_count,
                "batches": [b.to_dict() for b in matching],
                "query_time_ms": query_time,
                "message": f"Found {result.total_count} matching batch(es)",
            }

        except Exception as e:
            logger.error(f"Batch query failed: {e}")
            return {"status": "error", "message": str(e)}

    async def _update_batch(
        self, inputs: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        """Update an existing batch.

        Inputs:
        - batch_id: Required - Batch ID to update
        - product_name: Optional - New product name
        - size_quantity: Optional - New quantity
        - size_unit: Optional - New unit
        - route_name: Optional - New route name
        - template_version: Optional - New template version
        - workstation_id: Optional - New workstation
        - change_reason: Optional - Reason for update
        """
        try:
            batch_id = inputs["batch_id"]
            if batch_id not in self.batch_store:
                return {"status": "error", "message": f"Batch {batch_id} not found"}

            batch = self.batch_store[batch_id]
            changes = []

            if "product_name" in inputs:
                batch.product.product_name = inputs["product_name"]
                changes.append("product_name")

            if "size_quantity" in inputs:
                batch.size.quantity = inputs["size_quantity"]
                changes.append("size_quantity")

            if "size_unit" in inputs:
                batch.size.unit = inputs["size_unit"]
                changes.append("size_unit")

            if "route_name" in inputs:
                batch.route.route_name = inputs["route_name"]
                changes.append("route_name")

            if "template_version" in inputs:
                batch.template.version = inputs["template_version"]
                changes.append("template_version")

            if "workstation_id" in inputs:
                batch.workstation_id = inputs["workstation_id"]
                changes.append("workstation_id")

            if not changes:
                return {"status": "error", "message": "No fields provided for update"}

            batch.updated_at = __import__("datetime").datetime.utcnow()
            batch.updated_by = context.user_id

            logger.info(f"Batch {batch.lot_number} updated: {', '.join(changes)}")

            return {
                "status": "success",
                "batch_id": batch_id,
                "lot_number": batch.lot_number,
                "changes": changes,
                "change_reason": inputs.get("change_reason"),
                "message": f"Batch {batch.lot_number} updated successfully",
            }

        except Exception as e:
            logger.error(f"Batch update failed: {e}")
            return {"status": "error", "message": str(e)}

    async def _delete_batch(
        self, inputs: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        """Delete/cancel/void a batch.

        Inputs:
        - batch_id: Required - Batch ID to delete
        - reason: Required - Reason for deletion
        - disposition: Optional - Material disposition (scrap, rework, quarantine)
        """
        try:
            batch_id = inputs["batch_id"]
            if batch_id not in self.batch_store:
                return {"status": "error", "message": f"Batch {batch_id} not found"}

            batch = self.batch_store[batch_id]

            if batch.status == BatchStatus.IN_PROGRESS:
                return {
                    "status": "error",
                    "message": f"Cannot delete batch {batch.lot_number}: batch is in progress",
                }

            reason = inputs.get("reason", "No reason provided")
            disposition = inputs.get("disposition", "scrap")

            deleted_batch = self.batch_store.pop(batch_id)

            logger.info(
                f"Batch {deleted_batch.lot_number} deleted by {context.user_id}: {reason}"
            )

            return {
                "status": "success",
                "batch_id": batch_id,
                "lot_number": deleted_batch.lot_number,
                "deleted_by": context.user_id,
                "reason": reason,
                "disposition": disposition,
                "message": f"Batch {deleted_batch.lot_number} deleted successfully",
            }

        except Exception as e:
            logger.error(f"Batch delete failed: {e}")
            return {"status": "error", "message": str(e)}

    async def _hold_batch(
        self, inputs: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        """Hold/pause/suspend a batch.

        Inputs:
        - batch_id: Required - Batch ID to hold
        - reason: Required - Reason for hold
        - hold_duration_hours: Optional - Expected hold duration in hours
        - conditions: Optional - Holding conditions (temperature, etc.)
        """
        try:
            batch_id = inputs["batch_id"]
            if batch_id not in self.batch_store:
                return {"status": "error", "message": f"Batch {batch_id} not found"}

            batch = self.batch_store[batch_id]

            if batch.status == BatchStatus.COMPLETED:
                return {
                    "status": "error",
                    "message": f"Cannot hold batch {batch.lot_number}: batch is already completed",
                }

            if batch.status == BatchStatus.ON_HOLD:
                return {
                    "status": "error",
                    "message": f"Batch {batch.lot_number} is already on hold",
                }

            reason = inputs.get("reason", "No reason provided")
            hold_duration = inputs.get("hold_duration_hours")
            conditions = inputs.get("conditions", {})

            batch.status = BatchStatus.ON_HOLD
            batch.updated_at = __import__("datetime").datetime.utcnow()
            batch.updated_by = context.user_id

            logger.info(
                f"Batch {batch.lot_number} placed on hold by {context.user_id}: {reason}"
            )

            return {
                "status": "success",
                "batch_id": batch_id,
                "lot_number": batch.lot_number,
                "previous_status": "in_progress",
                "new_status": "on_hold",
                "held_by": context.user_id,
                "reason": reason,
                "hold_duration_hours": hold_duration,
                "conditions": conditions,
                "message": f"Batch {batch.lot_number} placed on hold successfully",
            }

        except Exception as e:
            logger.error(f"Batch hold failed: {e}")
            return {"status": "error", "message": str(e)}

    async def _close_batch(
        self, inputs: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        """Close/finalize/release a batch.

        Inputs:
        - batch_id: Required - Batch ID to close
        - final_quantity: Optional - Final quantity produced
        - quantity_defective: Optional - Defective quantity
        - yield_percentage: Optional - Calculated yield
        - release_approved_by: Optional - QA approver
        """
        try:
            batch_id = inputs["batch_id"]
            if batch_id not in self.batch_store:
                return {"status": "error", "message": f"Batch {batch_id} not found"}

            batch = self.batch_store[batch_id]

            if batch.status == BatchStatus.ON_HOLD:
                return {
                    "status": "error",
                    "message": f"Cannot close batch {batch.lot_number}: batch is on hold",
                }

            if batch.status == BatchStatus.CLOSED:
                return {
                    "status": "error",
                    "message": f"Batch {batch.lot_number} is already closed",
                }

            final_quantity = inputs.get("final_quantity")
            quantity_defective = inputs.get("quantity_defective")
            yield_pct = inputs.get("yield_percentage")
            release_approved_by = inputs.get("release_approved_by")

            if final_quantity is not None:
                batch.quantity_produced = final_quantity

            if quantity_defective is not None:
                batch.quantity_waste = quantity_defective

            if yield_pct is not None:
                batch.yield_percentage = yield_pct
            elif final_quantity and quantity_defective is not None:
                batch.yield_percentage = max(
                    0,
                    (final_quantity - quantity_defective) / final_quantity * 100,
                )

            batch.status = BatchStatus.CLOSED
            batch.actual_end = __import__("datetime").datetime.utcnow()
            batch.updated_at = batch.actual_end
            batch.updated_by = context.user_id

            logger.info(f"Batch {batch.lot_number} closed by {context.user_id}")

            return {
                "status": "success",
                "batch_id": batch_id,
                "lot_number": batch.lot_number,
                "final_status": "closed",
                "closed_by": context.user_id,
                "release_approved_by": release_approved_by,
                "quantity_produced": batch.quantity_produced,
                "quantity_waste": batch.quantity_waste,
                "yield_percentage": batch.yield_percentage,
                "message": f"Batch {batch.lot_number} closed successfully",
            }

        except Exception as e:
            logger.error(f"Batch close failed: {e}")
            return {"status": "error", "message": str(e)}

    async def _start_execution(
        self, inputs: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        """Start batch execution.

        Inputs:
        - batch_id: Required - Batch ID
        - workstation_id: Optional - Workstation where batch runs
        - operator_id: Optional - Operator executing batch
        """
        try:
            batch_id = inputs["batch_id"]
            if batch_id not in self.batch_store:
                return {"status": "error", "message": f"Batch {batch_id} not found"}

            batch = self.batch_store[batch_id]

            # Create execution
            execution = BatchExecution(
                batch_id=batch_id,
                batch_lot_number=batch.lot_number,
                workstation_id=inputs.get("workstation_id") or batch.workstation_id,
                operator_id=inputs.get("operator_id") or batch.operator_id,
                status="running",
            )

            # Store execution
            self.execution_store[execution.execution_id] = execution

            # Update batch status
            batch.status = BatchStatus.IN_PROGRESS
            batch.actual_start = execution.started_at

            logger.info(f"Execution started for batch {batch.lot_number}")

            return {
                "status": "success",
                "execution_id": execution.execution_id,
                "batch_id": batch_id,
                "message": f"Execution started for batch {batch.lot_number}",
            }

        except Exception as e:
            logger.error(f"Start execution failed: {e}")
            return {"status": "error", "message": str(e)}

    async def _update_execution(
        self, inputs: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        """Update batch execution progress.

        Inputs:
        - execution_id: Required - Execution ID
        - progress_percentage: Optional - Progress (0-100)
        - quantity_produced: Optional - Quantity produced so far
        - quality_checks_passed: Optional - List of passed checks
        - quality_checks_failed: Optional - List of failed checks
        """
        try:
            execution_id = inputs["execution_id"]
            if execution_id not in self.execution_store:
                return {
                    "status": "error",
                    "message": f"Execution {execution_id} not found",
                }

            execution = self.execution_store[execution_id]

            # Update fields
            if "progress_percentage" in inputs:
                execution.progress_percentage = inputs["progress_percentage"]

            if "quantity_produced" in inputs:
                execution.quantity_produced = inputs["quantity_produced"]

            if "quality_checks_passed" in inputs:
                execution.quality_checks_passed = inputs["quality_checks_passed"]

            if "quality_checks_failed" in inputs:
                execution.quality_checks_failed = inputs["quality_checks_failed"]

            execution.updated_at = __import__("datetime").datetime.utcnow()

            logger.info(f"Execution {execution_id} updated")

            return {
                "status": "success",
                "execution_id": execution_id,
                "progress": execution.progress_percentage,
                "message": "Execution updated successfully",
            }

        except Exception as e:
            logger.error(f"Update execution failed: {e}")
            return {"status": "error", "message": str(e)}

    async def _complete_execution(
        self, inputs: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        """Complete batch execution.

        Inputs:
        - execution_id: Required - Execution ID
        - status: Required - "completed" or "failed"
        - quantity_produced: Optional - Final quantity
        - quantity_defective: Optional - Defective items
        - supervisor_approval: Optional - Supervisor approval
        """
        try:
            execution_id = inputs["execution_id"]
            if execution_id not in self.execution_store:
                return {
                    "status": "error",
                    "message": f"Execution {execution_id} not found",
                }

            execution = self.execution_store[execution_id]
            batch_id = execution.batch_id

            if batch_id not in self.batch_store:
                return {"status": "error", "message": f"Batch {batch_id} not found"}

            batch = self.batch_store[batch_id]

            # Update execution
            execution.status = inputs.get("status", "completed")
            execution.completed_at = __import__("datetime").datetime.utcnow()
            execution.quantity_produced = inputs.get("quantity_produced")
            execution.quantity_defective = inputs.get("quantity_defective")
            execution.supervisor_approval = inputs.get("supervisor_approval")

            if execution.started_at and execution.completed_at:
                execution.duration_seconds = (
                    execution.completed_at - execution.started_at
                ).total_seconds()

            # Update batch
            batch.status = (
                BatchStatus.COMPLETED
                if execution.status == "completed"
                else BatchStatus.FAILED
            )
            batch.quantity_produced = execution.quantity_produced
            batch.quantity_waste = execution.quantity_defective
            batch.actual_end = execution.completed_at

            # Calculate yield
            if execution.quantity_produced and execution.quantity_defective is not None:
                batch.yield_percentage = max(
                    0,
                    (execution.quantity_produced - execution.quantity_defective)
                    / execution.quantity_produced
                    * 100,
                )

            logger.info(
                f"Execution {execution_id} completed with status {execution.status}"
            )

            return {
                "status": "success",
                "execution_id": execution_id,
                "batch_id": batch_id,
                "execution_status": execution.status,
                "duration_seconds": execution.duration_seconds,
                "yield_percentage": batch.yield_percentage,
                "message": f"Batch execution completed successfully",
            }

        except Exception as e:
            logger.error(f"Complete execution failed: {e}")
            return {"status": "error", "message": str(e)}

    async def _get_batch_details(
        self, inputs: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        """Get complete batch details.

        Inputs:
        - batch_id: Required - Batch ID
        """
        try:
            batch_id = inputs["batch_id"]
            if batch_id not in self.batch_store:
                return {"status": "error", "message": f"Batch {batch_id} not found"}

            batch = self.batch_store[batch_id]

            # Get associated executions
            executions = [
                e.model_dump()
                for e in self.execution_store.values()
                if e.batch_id == batch_id
            ]

            return {
                "status": "success",
                "batch": batch.to_dict(),
                "executions": executions,
                "message": "Batch details retrieved successfully",
            }

        except Exception as e:
            logger.error(f"Get batch details failed: {e}")
            return {"status": "error", "message": str(e)}

    async def _list_by_status(
        self, inputs: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        """List batches by status.

        Inputs:
        - status: Required - Batch status to filter
        """
        try:
            status = BatchStatus(inputs["status"])

            batches = [
                b.to_dict() for b in self.batch_store.values() if b.status == status
            ]

            return {
                "status": "success",
                "filter_status": status,
                "count": len(batches),
                "batches": batches,
                "message": f"Found {len(batches)} batches with status {status}",
            }

        except Exception as e:
            logger.error(f"List by status failed: {e}")
            return {"status": "error", "message": str(e)}

    async def _validate_config(
        self, inputs: dict[str, Any], context: ExecutionContext
    ) -> dict[str, Any]:
        """Validate batch configuration.

        Inputs:
        - product_id: Required
        - size_quantity: Required
        - route_id: Required
        - template_version: Required
        """
        try:
            required_fields = [
                "product_id",
                "size_quantity",
                "route_id",
                "template_version",
            ]
            missing = [f for f in required_fields if f not in inputs]

            if missing:
                return {
                    "status": "error",
                    "message": f"Missing required fields: {missing}",
                }

            return {
                "status": "success",
                "valid": True,
                "message": "Batch configuration is valid",
            }

        except Exception as e:
            logger.error(f"Validate config failed: {e}")
            return {"status": "error", "message": str(e)}
