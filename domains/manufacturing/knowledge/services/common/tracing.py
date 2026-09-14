from __future__ import annotations

import json
import logging
import logging.handlers
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _json_default(value: Any) -> str:
    return str(value)


def _local_trace_logger(service_name: str) -> logging.Logger:
    trace_dir = Path(os.getenv("TRACE_DIR", os.getenv("LOG_TRACE_DIR", "logs/traces")))
    trace_dir.mkdir(parents=True, exist_ok=True)

    trace_logger = logging.getLogger(f"{service_name}.route_traces")
    trace_logger.setLevel(logging.INFO)
    trace_logger.propagate = False

    trace_path = trace_dir / f"{service_name}.jsonl"
    existing = [
        handler
        for handler in trace_logger.handlers
        if isinstance(handler, logging.handlers.RotatingFileHandler)
        and Path(handler.baseFilename) == trace_path
    ]
    if not existing:
        handler = logging.handlers.RotatingFileHandler(
            trace_path,
            maxBytes=_env_int("TRACE_MAX_BYTES", 25 * 1024 * 1024),
            backupCount=_env_int("TRACE_BACKUP_COUNT", 5),
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        trace_logger.handlers = [handler]

    return trace_logger


def _elk_trace_logger(service_name: str) -> logging.Logger:
    trace_logger = logging.getLogger(f"{service_name}.route_traces")
    trace_logger.setLevel(logging.INFO)
    trace_logger.handlers = []
    trace_logger.propagate = True
    return trace_logger


def _configure_sentry(service_name: str):
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        logger.warning(
            "TRACE_MODE=sentry requested, but SENTRY_DSN is not configured; using local traces"
        )
        return None

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
    except ImportError:
        logger.warning(
            "TRACE_MODE=sentry requested, but sentry-sdk is not installed; using local traces"
        )
        return None

    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv(
            "SENTRY_ENVIRONMENT", os.getenv("ENVIRONMENT", "development")
        ),
        release=os.getenv("SENTRY_RELEASE"),
        traces_sample_rate=_env_float("SENTRY_TRACES_SAMPLE_RATE", 1.0),
        profiles_sample_rate=_env_float("SENTRY_PROFILES_SAMPLE_RATE", 0.0),
        integrations=[FastApiIntegration()],
        send_default_pii=os.getenv("SENTRY_SEND_DEFAULT_PII", "false").lower()
        == "true",
    )
    sentry_sdk.set_tag("service", service_name)
    return sentry_sdk


def install_route_tracing(app: FastAPI, service_name: str) -> None:
    backend = os.getenv("TRACE_MODE", os.getenv("TRACE_BACKEND", "elk")).strip().lower()
    if backend in {"off", "none", "disabled"}:
        return

    sentry_sdk = _configure_sentry(service_name) if backend == "sentry" else None
    trace_logger = (
        None
        if sentry_sdk
        else (
            _local_trace_logger(service_name)
            if backend == "local"
            else _elk_trace_logger(service_name)
        )
    )

    @app.middleware("http")
    async def route_trace_interceptor(request: Request, call_next):
        trace_id = (
            request.headers.get("x-trace-id")
            or request.headers.get("x-request-id")
            or str(uuid.uuid4())
        )
        start = time.perf_counter()
        started_at = datetime.now(timezone.utc)
        client_host = request.client.host if request.client else "-"

        trace: dict[str, Any] = {
            "trace_id": trace_id,
            "service": service_name,
            "started_at": started_at.isoformat(),
            "method": request.method,
            "path": request.url.path,
            "query": str(request.url.query),
            "client_host": client_host,
            "user_agent": request.headers.get("user-agent", ""),
        }

        try:
            response = await call_next(request)
            trace["status_code"] = response.status_code
            response.headers["x-trace-id"] = trace_id
            return response
        except Exception as exc:
            trace["error"] = {
                "type": exc.__class__.__name__,
                "message": str(exc),
            }
            if sentry_sdk:
                sentry_sdk.set_context("route_trace", trace)
                sentry_sdk.capture_exception(exc)
            raise
        finally:
            trace["finished_at"] = datetime.now(timezone.utc).isoformat()
            trace["duration_ms"] = round((time.perf_counter() - start) * 1000, 2)

            if sentry_sdk:
                sentry_sdk.set_tag("trace_id", trace_id)
                sentry_sdk.set_context("route_trace", trace)
                if trace.get("status_code", 200) >= 500 and "error" not in trace:
                    sentry_sdk.capture_message(
                        f"{service_name} {request.method} {request.url.path} returned {trace['status_code']}",
                        level="error",
                    )
            elif trace_logger:
                trace_logger.info(
                    "route trace",
                    extra={
                        "event": "route_trace",
                        "trace_type": "route",
                        **trace,
                    },
                )
