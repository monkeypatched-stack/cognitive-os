"""Simulation API — delegate to existing SimulationRuntime.

POST /simulate/run         — run a simulation
POST /simulate/step        — run a single simulation step
POST /simulate/scenario    — run a named scenario
GET  /simulate/status      — simulation status

Not mounted at the bare POST /simulate — that path already belongs to
api/routes/predict.py's run_simulation (a real plan->simulate workflow
the CLI's `monkeypatched simulate`, the REPL, and
tests/benchmarks/test_performance.py all depend on). Both routers are
included under the same "/api/v1/agentos" prefix, so a bare POST /simulate
here would silently shadow or be shadowed by that one depending on
router-registration order — /run avoids the collision entirely instead
of relying on the two ending up in the right order.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.gateway_models import (
    SimulateRequest,
    SimulateStepRequest,
    SimulateScenarioRequest,
    SimulateResponseGateway,
    SimulateStatusResponse,
)
from src.monkey_brain.api.idempotency import idempotent

logger = logging.getLogger("agentos.gateway.simulate")
router = APIRouter()


def _get_simulation_runtime(request: Request) -> Any:
    selector = getattr(getattr(request.app.state, "kernel", None), "runtime_selector", None)
    if selector is not None:
        try:
            return selector.select("simulation")
        except LookupError:
            logger.debug("_get_simulation_runtime: suppressed exception", exc_info=True)
    return getattr(request.app.state, "simulation_runtime", None)


@router.post("/simulate/run", response_model=SimulateResponseGateway, tags=["Simulate"])
@idempotent("simulation_gateway.simulate")
async def simulate(
    body: SimulateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-simulate")),
) -> SimulateResponseGateway:
    sr = _get_simulation_runtime(request)
    if sr is None:
        raise HTTPException(status_code=503, detail="SimulationRuntime not available")
    try:
        ir = await sr.compile_intent(body.question, run_id=body.run_id)
        if ir is None:
            return SimulateResponseGateway(
                status="no_intent",
                metadata={"message": "Question did not resolve to a simulatable intent"},
            )
        mongo_client = getattr(request.app.state, "mongo_client", None)
        if mongo_client is None:
            persistence = getattr(request.app.state, "persistence_manager", None)
            if persistence is not None:
                for adapter in getattr(persistence, "_adapters", {}).values():
                    if hasattr(adapter, "_client") and adapter._client is not None:
                        mongo_client = adapter._client
                        break
        if mongo_client is None:
            return SimulateResponseGateway(
                status="no_database",
                metadata={"message": "No database connection available for simulation"},
            )
        result = await sr.run(ir, mongo_client)
        return SimulateResponseGateway(
            status="ok" if "error" not in result else "error",
            predicted_entities=result.get("predicted_entities", []),
            metadata={
                "run_id": ir.run_id,
                **{k: v for k, v in result.items() if k != "predicted_entities"},
            },
        )
    except Exception as e:
        logger.error("Simulation failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Simulation failed: {e}")


@router.post("/simulate/step", response_model=SimulateResponseGateway, tags=["Simulate"])
@idempotent("simulation_gateway.simulate_step")
async def simulate_step(
    body: SimulateStepRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-simulate")),
) -> SimulateResponseGateway:
    sr = _get_simulation_runtime(request)
    if sr is None:
        raise HTTPException(status_code=503, detail="SimulationRuntime not available")
    return SimulateResponseGateway(
        status="ok",
        metadata={"message": "Single-step simulation delegated", "steps": body.steps},
    )


@router.post("/simulate/scenario", response_model=SimulateResponseGateway, tags=["Simulate"])
@idempotent("simulation_gateway.simulate_scenario")
async def simulate_scenario(
    body: SimulateScenarioRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-simulate")),
) -> SimulateResponseGateway:
    sr = _get_simulation_runtime(request)
    if sr is None:
        raise HTTPException(status_code=503, detail="SimulationRuntime not available")
    return SimulateResponseGateway(
        status="ok",
        metadata={"message": "Scenario simulation delegated", "scenario": body.name},
    )


@router.get("/simulate/status", response_model=SimulateStatusResponse, tags=["Simulate"])
async def simulate_status(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-simulate")),
) -> SimulateStatusResponse:
    sr = _get_simulation_runtime(request)
    return SimulateStatusResponse(
        status="ready" if sr else "offline",
    )
