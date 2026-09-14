from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id",
    default="-",
)


class RequestContextFilter(logging.Filter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    def filter(self, record: logging.LogRecord) -> bool:
        record.service_name = self.service_name
        record.request_id = _request_id.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "@timestamp": datetime.fromtimestamp(
                record.created, timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": getattr(record, "service_name", "-"),
            "request_id": getattr(record, "request_id", "-"),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "process": record.process,
            "thread": record.threadName,
        }
        for key in (
            "http_method",
            "http_path",
            "http_status",
            "duration_ms",
            "client_host",
            "event",
            "trace_id",
            "trace_type",
            "started_at",
            "finished_at",
            "method",
            "path",
            "query",
            "status_code",
            "user_agent",
            "reasoning_trace_id",
            "reasoning_stage",
            "reasoning_summary",
            "input_summary",
            "output_summary",
            "tool_calls",
            "metadata",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


class LogstashTCPHandler(logging.handlers.SocketHandler):
    """Send newline-delimited JSON logs to Logstash TCP input."""

    def makePickle(self, record: logging.LogRecord) -> bytes:
        return (self.format(record) + "\n").encode("utf-8")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _build_handlers(service_name: str) -> list[logging.Handler]:
    mode = os.getenv("LOG_MODE", os.getenv("LOG_BACKEND", "console")).strip().lower()
    level = os.getenv("LOG_LEVEL", "INFO").upper()

    if mode == "elk":
        host = os.getenv("ELK_HOST", os.getenv("LOGSTASH_HOST", "localhost"))
        port = _env_int("ELK_PORT", _env_int("LOGSTASH_PORT", 5000))
        handler: logging.Handler = LogstashTCPHandler(host, port)
        handler.setFormatter(JsonFormatter())
        handler.setLevel(level)
        return [handler]

    log_dir = Path(os.getenv("LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / f"{service_name}.log",
        maxBytes=_env_int("LOG_MAX_BYTES", 10 * 1024 * 1024),
        backupCount=_env_int("LOG_BACKUP_COUNT", 5),
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(service_name)s] "
            "[request_id=%(request_id)s] %(name)s: %(message)s"
        )
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(file_handler.formatter)
    return [file_handler, stream_handler]


def configure_service_logging(service_name: str) -> logging.Logger:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    handlers = _build_handlers(service_name)
    context_filter = RequestContextFilter(service_name)

    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(level)

    for handler in handlers:
        handler.addFilter(context_filter)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.handlers = handlers
        logger.propagate = False
        logger.setLevel(level)

    logger = logging.getLogger(service_name)
    logger.info(
        "Logging configured",
        extra={
            "event": "logging_configured",
            "log_mode": os.getenv("LOG_MODE", os.getenv("LOG_BACKEND", "elk")),
        },
    )
    return logger


def install_request_logging(app: FastAPI, service_name: str) -> None:
    logger = logging.getLogger(f"{service_name}.requests")

    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        token = _request_id.set(request_id)
        start = time.perf_counter()
        client_host = request.client.host if request.client else "-"
        try:
            response = await call_next(request)
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.info(
                "%s %s completed with %s in %.2fms",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
                extra={
                    "event": "http_request",
                    "http_method": request.method,
                    "http_path": request.url.path,
                    "http_status": response.status_code,
                    "duration_ms": duration_ms,
                    "client_host": client_host,
                },
            )
            response.headers["x-request-id"] = request_id
            return response
        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.exception(
                "%s %s failed in %.2fms",
                request.method,
                request.url.path,
                duration_ms,
                extra={
                    "event": "http_request_error",
                    "http_method": request.method,
                    "http_path": request.url.path,
                    "duration_ms": duration_ms,
                    "client_host": client_host,
                },
            )
            raise
        finally:
            _request_id.reset(token)
