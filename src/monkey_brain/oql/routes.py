"""OQL API routes — exposes OQL execution and validation.

POST /api/v1/agentos/oql/execute
POST /api/v1/agentos/oql/validate
GET  /api/v1/agentos/oql/stats
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.oql.query import Query, Operation

logger = logging.getLogger("agentos.oql.route")

router = APIRouter()


class OQLExecuteRequest(BaseModel):
    entity: str = Field(..., description="Entity type (e.g., Customer, Order)")
    operation: str = Field(
        ...,
        description="Operation (select, insert, update, delete, upsert, count, exists)",
    )
    filters: dict[str, Any] = Field(default_factory=dict)
    fields: list[str] = Field(default_factory=list)
    sort: list[list[str]] = Field(default_factory=list)
    limit: int | None = None
    offset: int = 0
    data: dict[str, Any] | None = None
    batch_data: list[dict[str, Any]] | None = None
    upsert_key: str | None = None


class OQLValidateRequest(BaseModel):
    entity: str
    operation: str
    filters: dict[str, Any] = Field(default_factory=dict)
    fields: list[str] = Field(default_factory=list)
    data: dict[str, Any] | None = None


def _get_engine(request: Request):
    return getattr(request.app.state, "_oql", None) or getattr(request.app.state, "oql", None)


@router.post("/oql/execute", tags=["OQL"])
async def execute_oql(
    body: OQLExecuteRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-action")),
):
    """Execute an OQL query."""
    engine = _get_engine(request)
    if not engine:
        return JSONResponse(status_code=503, content={"error": "OQL engine not initialized"})

    sort_clauses = []
    for s in body.sort:
        if len(s) == 2:
            from src.monkey_brain.oql.query import SortClause, SortDirection

            sort_clauses.append(SortClause(field=s[0], direction=SortDirection(s[1])))

    query = Query(
        entity=body.entity,
        operation=Operation(body.operation),
        filters=body.filters,
        fields=body.fields,
        sort=sort_clauses,
        limit=body.limit,
        offset=body.offset,
        data=body.data,
        batch_data=body.batch_data,
        upsert_key=body.upsert_key,
    )

    result = await engine.execute(query)
    return result.to_dict()


@router.post("/oql/validate", tags=["OQL"])
async def validate_oql(
    body: OQLValidateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-observability")),
):
    """Validate an OQL query without executing."""
    engine = _get_engine(request)
    if not engine:
        return JSONResponse(status_code=503, content={"error": "OQL engine not initialized"})

    from src.monkey_brain.oql.validator import QueryValidator

    validator = QueryValidator()

    query = Query(
        entity=body.entity,
        operation=Operation(body.operation),
        filters=body.filters,
        fields=body.fields,
        data=body.data,
    )

    errors = validator.validate(query)
    forbidden = validator.check_forbidden(query)

    return {
        "valid": len(errors) == 0 and len(forbidden) == 0,
        "errors": errors,
        "forbidden": forbidden,
    }


@router.get("/oql/stats", tags=["OQL"])
async def oql_stats(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-observability")),
):
    """Return OQL engine statistics."""
    engine = _get_engine(request)
    if not engine:
        return JSONResponse(status_code=503, content={"error": "OQL engine not initialized"})
    return engine.stats()
