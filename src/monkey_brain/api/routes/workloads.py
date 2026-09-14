"""Workload Compose route — compose workloads from steps and templates.

POST /workloads/compose     — compose a workload from steps and/or templates
GET  /workloads/templates   — list available workload templates
GET  /workloads/{id}        — get a composed workload by ID
POST /workloads/validate    — validate a workload's dependency graph
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.idempotency import idempotent
from src.introspection.lemon import get_lemon

logger = logging.getLogger("agentos.workloads")

router = APIRouter()


class StepDefinition(BaseModel):
    step_id: str = ""
    capability_name: str = ""
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ComposeRequest(BaseModel):
    steps: list[StepDefinition] | None = None
    templates: list[str] | None = None
    workload_id: str = ""
    auto_wire: bool = True


@router.post("/workloads/compose", tags=["Workloads"])
@idempotent("workloads.compose_workload")
async def compose_workload(
    payload: ComposeRequest,
    user_id: str = Depends(require_permission("perm-execute-plan")),
) -> JSONResponse:
    """Compose a workload from step definitions and/or template names.

    Templates: "self-healing"
    Steps: list of {step_id, capability_name, inputs, outputs, dependencies}
    auto_wire: chain steps linearly when no explicit dependencies
    """
    from src.monkey_brain.kernel.plan.workload.composer import WorkloadComposer

    lemon = get_lemon()
    if lemon:
        lemon.counter("api.workloads.compose")

    from src.monkey_brain.kernel.plan.workload.dag import DAGValidationError

    composer = WorkloadComposer()
    try:
        workload = composer.compose(
            steps=[s.model_dump() for s in payload.steps] if payload.steps else None,
            templates=payload.templates,
            workload_id=payload.workload_id,
            auto_wire=payload.auto_wire,
        )
        dag = workload.build_dag()  # validates; raises DAGValidationError if invalid

        if lemon:
            lemon.counter("api.workloads.compose.success")
            lemon.gauge("api.workloads.compose.steps", len(workload.steps))

        return JSONResponse(
            {
                "workload_id": workload.workload_id,
                "steps": [
                    {
                        "step_id": s.step_id,
                        "capability_name": s.capability_name,
                        "inputs": s.inputs,
                        "outputs": s.outputs,
                        "dependencies": s.dependencies,
                    }
                    for s in workload.steps
                ],
                "step_count": len(workload.steps),
                "dag": dag.summary(),
                "metadata": workload.metadata,
            }
        )

    except DAGValidationError as exc:
        if lemon:
            lemon.counter("api.workloads.compose.invalid")
        return JSONResponse(
            status_code=422,
            content={"error": "Invalid workload graph", "errors": exc.errors},
        )
    except Exception as e:
        logger.error("Compose failed: %s", e)
        if lemon:
            lemon.counter("api.workloads.compose.error")
        return JSONResponse(
            status_code=500,
            content={"error": "Compose failed", "detail": str(e)},
        )


@router.get("/workloads/templates", tags=["Workloads"])
async def list_templates(
    user_id: str = Depends(require_permission("perm-view-agents")),
) -> JSONResponse:
    """List available workload templates."""
    templates = [
        {
            "name": "self-healing",
            "description": "Compile/test/repair loop with bounded iteration",
            "steps": ["generate", "compile", "test", "analyze", "repair", "verify"],
        },
    ]
    return JSONResponse({"templates": templates, "total": len(templates)})


@router.post("/workloads/validate", tags=["Workloads"])
@idempotent("workloads.validate_workload")
async def validate_workload(
    payload: ComposeRequest,
    user_id: str = Depends(require_permission("perm-execute-plan")),
) -> JSONResponse:
    """Validate a workload's dependency graph without creating it."""
    from src.monkey_brain.kernel.plan.workload.composer import WorkloadComposer

    from src.monkey_brain.kernel.plan.workload.dag import (
        WorkloadDAG,
        DAGValidationError,
    )

    composer = WorkloadComposer()
    try:
        workload = composer.compose(
            steps=[s.model_dump() for s in payload.steps] if payload.steps else None,
            templates=payload.templates,
            workload_id=payload.workload_id or "validation",
            auto_wire=payload.auto_wire,
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": "Compose failed", "detail": str(e)},
        )

    try:
        dag = WorkloadDAG.from_steps(workload.steps)
        return JSONResponse(
            {
                "valid": True,
                "errors": [],
                "step_count": len(workload.steps),
                "dag": dag.summary(),
            }
        )
    except DAGValidationError as exc:
        return JSONResponse(
            {
                "valid": False,
                "errors": exc.errors,
                "step_count": len(workload.steps),
            }
        )
