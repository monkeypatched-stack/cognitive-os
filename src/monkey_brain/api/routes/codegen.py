"""Microservice code generation endpoints.

POST /codegen/microservice          — generate from spec → JSON {files: {path: content}}
POST /codegen/microservice?fmt=zip  — generate → ZIP download
GET  /codegen/microservice/schema   — MicroserviceSpec JSON schema
POST /codegen/microservice/validate — validate spec, return errors without generating
"""

from __future__ import annotations
import logging

import io
import zipfile
from typing import Any

from fastapi import APIRouter, Query, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError
from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.idempotency import idempotent

logger = logging.getLogger("monkey_brain.api.routes.codegen")

router = APIRouter()


@router.post("/microservice", summary="Generate microservice from spec", response_model=None)
@idempotent("codegen.generate_microservice")
async def generate_microservice(
    body: dict[str, Any],
    fmt: str = Query(default="json", enum=["json", "zip"], description="Response format"),
    user_id: str = Depends(require_permission("perm-execute-codegen")),
) -> JSONResponse | StreamingResponse:
    """Generate a complete, production-ready FastAPI microservice.

    **Input** — a MicroserviceSpec JSON object:

    ```json
    {
      "name": "products",
      "domain": "inventory",
      "description": "Product catalog",
      "db": {"type": "postgresql"},
      "fields": [
        {"name": "sku",   "type": "str",   "unique": true, "index": true},
        {"name": "name",  "type": "str"},
        {"name": "price", "type": "float", "gt": 0},
        {"name": "stock", "type": "int",   "default": 0},
        {"name": "status","type": "str",   "enum": ["active","archived"]}
      ],
      "timestamps": true,
      "soft_delete": true,
      "auth": {"enabled": true, "type": "bearer"},
      "pagination": {"default_limit": 20, "max_limit": 100}
    }
    ```

    **Output** — all files needed to run the service:
    `main.py`, `models.py`, `schemas.py`, `database.py`, `crud.py`,
    `routes.py`, `dependencies.py`, `exceptions.py`,
    `Dockerfile`, `docker-compose.yml`, `pyproject.toml`, `.env.example`,
    and Alembic migrations for SQL backends.
    """
    from src.monkey_brain.codegen import MicroserviceSpec, generate

    try:
        spec = MicroserviceSpec.model_validate(body)
    except ValidationError as exc:
        return JSONResponse(
            status_code=422,
            content={"detail": "Invalid spec", "errors": exc.errors()},
        )

    files = generate(spec)

    if fmt == "zip":
        return _zip_response(spec.name, files)

    return JSONResponse(
        {
            "name": spec.name,
            "db": spec.db.type,
            "files": list(files.keys()),
            "file_count": len(files),
            "contents": files,
        }
    )


@router.get("/microservice/schema", summary="MicroserviceSpec JSON schema")
async def get_schema(
    user_id: str = Depends(require_permission("perm-view-codegen")),
) -> JSONResponse:
    """Return the full JSON schema for MicroserviceSpec.

    Use this to build a UI form or validate specs before submission.
    """
    from src.monkey_brain.codegen import MicroserviceSpec

    return JSONResponse(MicroserviceSpec.model_json_schema())


@router.post("/microservice/validate", summary="Validate spec without generating")
@idempotent("codegen.validate_spec")
async def validate_spec(
    body: dict[str, Any],
    user_id: str = Depends(require_permission("perm-execute-codegen")),
) -> JSONResponse:
    """Validate a MicroserviceSpec and return errors without generating code."""
    from src.monkey_brain.codegen import MicroserviceSpec

    try:
        spec = MicroserviceSpec.model_validate(body)
        return JSONResponse(
            {
                "valid": True,
                "name": spec.name,
                "db": spec.db.type,
                "fields": len(spec.fields),
                "auth": spec.auth.type,
                "estimated_files": _estimate_file_count(spec),
            }
        )
    except ValidationError as exc:
        return JSONResponse(
            status_code=422,
            content={"valid": False, "errors": exc.errors()},
        )


@router.get("/microservice/example", summary="Example spec for a product catalog")
async def get_example(
    user_id: str = Depends(require_permission("perm-view-codegen")),
) -> JSONResponse:
    """Return a ready-to-use example spec."""
    return JSONResponse(
        {
            "name": "products",
            "domain": "inventory",
            "description": "Product catalog microservice",
            "version": "1.0.0",
            "port": 8000,
            "db": {
                "type": "postgresql",
                "url_env": "DATABASE_URL",
                "pool_size": 10,
                "max_overflow": 20,
            },
            "fields": [
                {
                    "name": "sku",
                    "type": "str",
                    "unique": True,
                    "index": True,
                    "description": "Stock keeping unit",
                },
                {"name": "name", "type": "str", "min_length": 1, "max_length": 200},
                {"name": "price", "type": "float", "gt": 0},
                {"name": "stock", "type": "int", "default": 0, "ge": 0},
                {
                    "name": "category",
                    "type": "str",
                    "enum": ["electronics", "clothing", "food", "other"],
                },
                {"name": "active", "type": "bool", "default": True},
            ],
            "timestamps": True,
            "soft_delete": True,
            "auth": {"enabled": True, "type": "bearer"},
            "pagination": {"default_limit": 20, "max_limit": 100},
        }
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _zip_response(name: str, files: dict[str, str]) -> StreamingResponse:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}-service.zip"'},
    )


def _estimate_file_count(spec: Any) -> int:
    base = 9  # main, models, schemas, database, crud, routes, dependencies, exceptions, .env.example
    base += 3  # Dockerfile, docker-compose, pyproject
    if spec.is_sql:
        base += 3  # alembic.ini, alembic/env.py, alembic/versions/.gitkeep
    return base
