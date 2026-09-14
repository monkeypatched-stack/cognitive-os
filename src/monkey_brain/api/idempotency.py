"""Idempotency — safe retries for mutating world-model endpoints.

Gate 2 (Production API) gap: a client retrying POST /orders/{id}/payment
after a dropped connection has no way to tell the server "this is the
same request, not a new one" — today that retry double-charges. This
module is the fix: a client sends an `Idempotency-Key` header on a
mutating request; a retry with the SAME key returns the cached result of
the first successful attempt instead of re-executing the operation. No
header means no idempotency guarantee — this is opt-in, matching the
header's role in every other REST API that supports it (Stripe, GitHub).

Backend (single-worker vs multi-worker) mirrors kernel/plan/goals/
run_store.py::RunStore exactly, for the same reason: /orders and
/payment may each land on a different uvicorn worker, so a process-local
cache alone would let a retry hit a worker that never saw the first
attempt and double-execute anyway. Selection: IDEMPOTENCY_STORE_BACKEND
= "auto" (default) uses Redis when REDIS_URL is set AND reachable, else
falls back to in-memory; "redis" forces Redis; "memory" forces
in-memory. Redis entries expire after IDEMPOTENCY_TTL_SECONDS (default 1
day) — a key reused after expiry is treated as a brand-new request,
which is the same tradeoff Stripe et al. make.

Concurrency: a bare check-then-store has a race — two copies of the
identical retry arriving close enough together could both miss the
cache and both execute. `reserve()` is an atomic "claim this key first"
step (Redis SETNX / a locked dict in-memory) so the second concurrent
attempt gets a 409 "already being processed" instead of executing
twice. Only a claimed key can later be completed via `store()`; a
handler that raises is never cached — replaying a transient failure
(503, a validation bug since fixed) must be allowed to actually retry,
only a successful, already-committed side effect must not repeat.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("agentos.idempotency")

DEFAULT_CAPACITY = int(os.getenv("IDEMPOTENCY_STORE_CAPACITY", "10000"))
DEFAULT_TTL_SECONDS = int(os.getenv("IDEMPOTENCY_TTL_SECONDS", "86400"))
# How long a "reserved but not yet completed" claim is honored before it's
# treated as abandoned (the worker that reserved it crashed mid-request) and
# a new attempt is allowed to reserve the same key again.
RESERVATION_TIMEOUT_SECONDS = float(os.getenv("IDEMPOTENCY_RESERVATION_TIMEOUT_SEC", "30"))

_IN_PROGRESS = "in_progress"
_COMPLETED = "completed"
_ABANDONED = "abandoned"


@dataclass(frozen=True)
class IdempotencyRecord:
    state: str  # _IN_PROGRESS | _COMPLETED | _ABANDONED
    request_hash: str
    response_body: dict[str, Any] | None = None
    reserved_at: float = 0.0


def _find_request_body(kwargs: dict[str, Any]) -> Any:
    """The route's Pydantic request body, regardless of its parameter
    name. Most decorated routes name it `body`, but that's a convention,
    not a guarantee — /prompt's body param is `payload`. Falls back to
    scanning every kwarg for the first Pydantic BaseModel instance
    (duck-typed via model_dump, matching this module's own existing
    convention at the call site below), skipping `request` itself."""
    body_obj = kwargs.get("body")
    if body_obj is not None:
        return body_obj
    for k, v in kwargs.items():
        if k in ("request", "user_id") or v is None:
            continue
        if hasattr(v, "model_dump"):
            return v
    return None


def _to_cacheable(result: Any) -> tuple[bool, Any]:
    """(cacheable, payload). Plain dicts cache as-is, unchanged from
    before. A Pydantic BaseModel instance (e.g. PromptResponse) now also
    caches — tagged with its concrete class so replay reconstructs the
    SAME type rather than returning a bare dict a caller expecting a
    model instance wouldn't get from a real (non-replayed) call. Replay
    bypasses the route function entirely, so FastAPI's own response_model
    coercion never runs on a replayed response — this has to do that
    reconstruction itself."""
    if isinstance(result, dict):
        return True, result
    if hasattr(result, "model_dump") and hasattr(type(result), "model_validate"):
        return True, {
            "__idempotent_pydantic__": True,
            "class": f"{type(result).__module__}:{type(result).__qualname__}",
            "data": result.model_dump(),
        }
    return False, None


def _from_cached(payload: Any) -> Any:
    """Inverse of _to_cacheable's Pydantic branch — only ever reconstructs
    a class this same decorator itself tagged and cached, never arbitrary
    caller input, so the dotted-path import is safe."""
    if isinstance(payload, dict) and payload.get("__idempotent_pydantic__"):
        import importlib

        module_name, qualname = payload["class"].split(":", 1)
        obj: Any = importlib.import_module(module_name)
        for part in qualname.split("."):
            obj = getattr(obj, part)
        return obj.model_validate(payload["data"])
    return payload


def request_fingerprint(method: str, path: str, body: dict[str, Any] | None) -> str:
    """A stable hash of what the client is actually asking for — used to
    detect the ONE unsafe case: the same Idempotency-Key reused for a
    genuinely different request (a client bug, not a retry)."""
    payload = json.dumps(
        {"method": method, "path": path, "body": body or {}},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# ── In-memory backend (single-worker default) ───────────────────────────────


class _InMemoryIdempotencyBackend:
    def __init__(self) -> None:
        self._records: OrderedDict[str, IdempotencyRecord] = OrderedDict()
        self._lock = threading.Lock()

    def ping(self) -> None:
        return None

    def _evict_locked(self) -> None:
        while len(self._records) > DEFAULT_CAPACITY:
            self._records.popitem(last=False)

    def reserve(self, key: str, request_hash: str) -> tuple[bool, IdempotencyRecord | None]:
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                if existing.state == _COMPLETED:
                    return False, existing
                if existing.state == _ABANDONED:
                    return False, existing
                if existing.state == _IN_PROGRESS:
                    if (time.time() - existing.reserved_at) < RESERVATION_TIMEOUT_SECONDS:
                        return False, existing
                    abandoned = IdempotencyRecord(
                        state=_ABANDONED,
                        request_hash=existing.request_hash,
                        reserved_at=existing.reserved_at,
                    )
                    self._records[key] = abandoned
                    return False, abandoned
            self._records[key] = IdempotencyRecord(
                state=_IN_PROGRESS, request_hash=request_hash, reserved_at=time.time()
            )
            self._records.move_to_end(key)
            self._evict_locked()
            return True, None

    def complete(self, key: str, request_hash: str, response_body: dict[str, Any]) -> None:
        with self._lock:
            self._records[key] = IdempotencyRecord(
                state=_COMPLETED,
                request_hash=request_hash,
                response_body=response_body,
                reserved_at=time.time(),
            )
            self._records.move_to_end(key)
            self._evict_locked()

    def release(self, key: str) -> None:
        """Drop a reservation without completing it — the handler raised, so
        the next attempt (retry or genuinely concurrent request) must be
        allowed to actually execute, not be told 'already processing' forever."""
        with self._lock:
            self._records.pop(key, None)


# ── Redis backend (shared, multi-worker-safe) ───────────────────────────────


class _RedisIdempotencyBackend:
    """Keys: idempotency:{key} -> JSON {state, request_hash, response_body}.
    Reservation uses SET NX so the claim itself is atomic across workers —
    the same guarantee run_store.py's Redis backend relies on for run_ids,
    just via SETNX instead of INCR since there's no ordering to preserve
    here, only mutual exclusion."""

    PREFIX = "idempotency:"

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: Any = None

    def _connect(self) -> Any:
        import redis  # redis-py; a declared dependency (pyproject.toml)

        return redis.from_url(
            self._url,
            decode_responses=True,
            socket_connect_timeout=float(os.getenv("REDIS_CONNECT_TIMEOUT_SEC", "5")),
            socket_timeout=float(os.getenv("REDIS_SOCKET_TIMEOUT_SEC", "5")),
            health_check_interval=int(os.getenv("REDIS_HEALTH_CHECK_INTERVAL_SEC", "30")),
        )

    def available(self) -> bool:
        try:
            self._client = self._connect()
            self._client.ping()
            return True
        except Exception as exc:
            logger.warning("Idempotency Redis backend unreachable: %s", exc)
            self._client = None
            return False

    @property
    def _r(self) -> Any:
        if self._client is None:
            self._client = self._connect()
        return self._client

    def _dump(
        self,
        state: str,
        request_hash: str,
        response_body: dict[str, Any] | None,
        reserved_at: float | None = None,
    ) -> str:
        return json.dumps(
            {
                "state": state,
                "request_hash": request_hash,
                "response_body": response_body,
                "reserved_at": reserved_at if reserved_at is not None else time.time(),
            },
            default=str,
        )

    def _load(self, raw: str | None) -> IdempotencyRecord | None:
        if not raw:
            return None
        d = json.loads(raw)
        return IdempotencyRecord(
            state=d["state"],
            request_hash=d["request_hash"],
            response_body=d.get("response_body"),
            reserved_at=float(d.get("reserved_at") or 0.0),
        )

    def reserve(self, key: str, request_hash: str) -> tuple[bool, IdempotencyRecord | None]:
        redis_key = self.PREFIX + key
        try:
            now = time.time()
            ok = self._r.set(
                redis_key,
                self._dump(_IN_PROGRESS, request_hash, None, now),
                nx=True,
                ex=DEFAULT_TTL_SECONDS,
            )
            if ok:
                return True, None
            existing = self._load(self._r.get(redis_key))
            if existing is not None and existing.state == _IN_PROGRESS:
                if (time.time() - existing.reserved_at) >= RESERVATION_TIMEOUT_SECONDS:
                    abandoned = IdempotencyRecord(
                        state=_ABANDONED,
                        request_hash=existing.request_hash,
                        reserved_at=existing.reserved_at,
                    )
                    self._r.set(
                        redis_key,
                        self._dump(
                            _ABANDONED,
                            existing.request_hash,
                            None,
                            existing.reserved_at,
                        ),
                        ex=DEFAULT_TTL_SECONDS,
                    )
                    return False, abandoned
            return False, existing
        except Exception as exc:
            from src.monkey_brain.kernel.production_gates import idempotency_fail_closed

            if idempotency_fail_closed():
                logger.error(
                    "key=%r Idempotency(redis).reserve failed — refusing execution (fail-closed): %s",
                    key,
                    exc,
                )
                return False, None
            logger.warning(
                "key=%r Idempotency(redis).reserve failed — allowing execution: %s",
                key,
                exc,
            )
            return True, None

    def complete(self, key: str, request_hash: str, response_body: dict[str, Any]) -> None:
        try:
            self._r.set(
                self.PREFIX + key,
                self._dump(_COMPLETED, request_hash, response_body),
                ex=DEFAULT_TTL_SECONDS,
            )
        except Exception as exc:
            logger.warning("key=%r Idempotency(redis).complete failed: %s", key, exc)

    def release(self, key: str) -> None:
        try:
            self._r.delete(self.PREFIX + key)
        except Exception as exc:
            logger.warning("key=%r Idempotency(redis).release failed: %s", key, exc)


def _redact(url: str) -> str:
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return url


class _UnavailableIdempotencyBackend:
    """Fail-closed stand-in when Redis (the shared gate) cannot be used."""

    unavailable = True

    def reserve(self, key: str, request_hash: str) -> tuple[bool, IdempotencyRecord | None]:
        return False, None

    def complete(self, key: str, request_hash: str, response_body: dict[str, Any]) -> None:
        return None

    def release(self, key: str) -> None:
        return None


def _make_backend() -> Any:
    from src.monkey_brain.kernel.production_gates import (
        idempotency_fail_closed,
        insecure_dev_mode,
    )

    choice = os.getenv("IDEMPOTENCY_STORE_BACKEND", "auto").strip().lower()
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        url = f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0"

    if choice == "memory":
        if idempotency_fail_closed() and not insecure_dev_mode():
            logger.error("IdempotencyStore: memory backend forbidden when fail-closed")
            return _UnavailableIdempotencyBackend()
        logger.info("IdempotencyStore: process-local memory backend (IDEMPOTENCY_STORE_BACKEND=memory)")
        return _InMemoryIdempotencyBackend()

    if choice in ("auto", "redis") and url:
        backend = _RedisIdempotencyBackend(url)
        if backend.available():
            logger.info(
                "IdempotencyStore: SHARED Redis backend at %s — multi-worker safe",
                _redact(url),
            )
            return backend
        if idempotency_fail_closed():
            logger.error(
                "IdempotencyStore: Redis at %s unreachable — refusing execution (fail-closed)",
                _redact(url),
            )
            return _UnavailableIdempotencyBackend()
        logger.info("IdempotencyStore: Redis unreachable — using process-local memory (insecure-dev)")
        return _InMemoryIdempotencyBackend()

    if idempotency_fail_closed() and not insecure_dev_mode():
        return _UnavailableIdempotencyBackend()
    return _InMemoryIdempotencyBackend()


class IdempotencyStore:
    """Singleton idempotency-key -> outcome store. Delegates to a
    process-local (default) or shared Redis backend; public API is
    identical either way."""

    _instance: "IdempotencyStore | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "IdempotencyStore":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._backend = _make_backend()
                    cls._instance = instance
        return cls._instance

    def is_available(self) -> bool:
        return not getattr(self._backend, "unavailable", False)

    def reserve(self, key: str, request_hash: str) -> tuple[bool, IdempotencyRecord | None]:
        """True + None if this key is newly claimed by the caller (go
        ahead and execute). False + the existing record if someone else
        already claimed it — either still running (state=in_progress) or
        already finished (state=completed, response_body populated)."""
        return self._backend.reserve(key, request_hash)

    def complete(self, key: str, request_hash: str, response_body: dict[str, Any]) -> None:
        self._backend.complete(key, request_hash, response_body)

    def release(self, key: str) -> None:
        self._backend.release(key)


def get_idempotency_store() -> IdempotencyStore:
    return IdempotencyStore()


# ── Route decorator ──────────────────────────────────────────────────────

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"


def idempotent(resource: str):
    """Apply to a mutating route, directly above the function definition
    and BELOW @router.post(...):

        @router.post("/orders", ...)
        @idempotent("orders.create")
        async def create_order(body: OrderCreateRequest, request: Request, user_id=Depends(...)):
            ...

    Opt-in in insecure-dev — a request with no Idempotency-Key header
    executes normally. Outside insecure-dev the header is required and
    the request is fail-closed (400) without it.

    Must be the INNERMOST decorator (directly above `async def`, below
    @router.*) — decorators apply bottom-up, so @router.post(...) needs
    to register the ALREADY-wrapped function to see its
    functools.wraps-copied signature and resolve Depends()/body/path
    params correctly; registering the raw function first (idempotent
    below router.post instead of above) would make @router.post capture
    the unwrapped handler and skip idempotency entirely. Verified against
    a live TestClient before this was wired into any real route.

    Relies on every route in this codebase consistently naming its
    Request parameter `request` and its auth dependency `user_id` — true
    of every route this is applied to (see ADR-009).
    """

    def decorator(fn):
        import functools

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            from fastapi import HTTPException, Request

            request: Request | None = kwargs.get("request")
            if request is None:
                return await fn(*args, **kwargs)

            key = request.headers.get(IDEMPOTENCY_KEY_HEADER)
            if not key:
                key = request.headers.get("X-Razorpay-Event-Id") or request.headers.get("X-Razorpay-Signature")
            if not key:
                from src.monkey_brain.kernel.production_gates import insecure_dev_mode

                if not insecure_dev_mode():
                    raise HTTPException(
                        status_code=400,
                        detail="Idempotency-Key header required for this mutating operation",
                    )
                return await fn(*args, **kwargs)

            user_id = kwargs.get("user_id", "")
            body_obj = _find_request_body(kwargs)
            body_dict = body_obj.model_dump() if hasattr(body_obj, "model_dump") else None
            scoped_key = f"{resource}:{user_id}:{key}"
            request_hash = request_fingerprint(request.method, request.url.path, body_dict)

            # Best-effort correlation for the audit trail below — the
            # decorator is generic across every route it's applied to, so
            # the Idempotency-Key itself (stable, caller-supplied) is the
            # one identifier guaranteed available regardless of which
            # route this wraps.
            from src.monkey_brain.kernel.pipeline.audit_trail import (
                record_decision_event,
            )
            from src.monkey_brain.kernel.compile import _obs

            store = get_idempotency_store()
            claimed, existing = store.reserve(scoped_key, request_hash)
            if not claimed:
                if existing is None:
                    from src.monkey_brain.kernel.production_gates import (
                        idempotency_fail_closed,
                    )

                    if idempotency_fail_closed():
                        raise HTTPException(
                            status_code=503,
                            detail="Idempotency store unavailable — request refused (fail-closed)",
                        )
                if existing is not None and existing.state == _ABANDONED:
                    _obs.counter("idempotency.requests.total", result="abandoned")
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Idempotency-Key {key!r} reservation expired after a crash; "
                            "reconciliation required — do not retry the effect"
                        ),
                    )
                if existing is not None and existing.state == _COMPLETED:
                    if existing.request_hash != request_hash:
                        record_decision_event(
                            "idempotency_conflict",
                            actor_id=str(user_id),
                            execution_id=key,
                            reason=f"Idempotency-Key {key!r} reused for a different request ({resource})",
                        )
                        _obs.counter("idempotency.requests.total", result="conflict")
                        raise HTTPException(
                            status_code=409,
                            detail=f"Idempotency-Key {key!r} was already used for a different request",
                        )
                    record_decision_event(
                        "idempotency_replay",
                        actor_id=str(user_id),
                        execution_id=key,
                        reason=f"Idempotency-Key {key!r} replayed cached result ({resource})",
                    )
                    _obs.counter("idempotency.requests.total", result="replay")
                    return _from_cached(existing.response_body)
                # Genuinely concurrent in-progress race — not literally
                # "conflict" (a different payload) or "replay" (nothing
                # completed yet), but the spec's result label set is
                # closed to new/replay/conflict; this is the closest fit
                # (the request could not proceed as a fresh execution).
                _obs.counter("idempotency.requests.total", result="conflict")
                raise HTTPException(
                    status_code=409,
                    detail=f"a request with Idempotency-Key {key!r} is already being processed",
                )

            _obs.counter("idempotency.requests.total", result="new")
            try:
                result = await fn(*args, **kwargs)
            except BaseException as exc:
                from src.monkey_brain.kernel.security_operation import (
                    UnknownOutcomeError,
                )

                if isinstance(exc, UnknownOutcomeError):
                    store.complete(
                        scoped_key,
                        request_hash,
                        {
                            "outcome": "unknown",
                            "reconciliation_required": True,
                            "operation_id": getattr(exc, "operation_id", ""),
                        },
                    )
                    raise HTTPException(
                        status_code=409,
                        detail="operation outcome unknown; reconciliation required; do not retry",
                    ) from exc
                store.release(scoped_key)
                raise
            cacheable, payload = _to_cacheable(result)
            if cacheable:
                store.complete(scoped_key, request_hash, payload)
            else:
                # Not a dict or Pydantic model — release rather than cache
                # something we can't safely replay.
                store.release(scoped_key)
            return result

        return wrapper

    return decorator
