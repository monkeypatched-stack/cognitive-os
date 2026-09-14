"""ResourceManager — manages all external resources with health tracking,
retry policies, graceful degradation, and configuration validation.

Resources are typed as Required | Optional. Failure of optional resources
never prevents kernel boot. Required resource failures raise immediately.

Health states:
    READY       — connected and healthy
    DEGRADED    — partially working (e.g. read-only mode)
    UNAVAILABLE — not connected (network/auth issue)
    FAILED      — initialization crashed (bad config)
    DISABLED    — explicitly disabled by operator
    STARTING    — connection in progress

Retry: exponential backoff with configurable ceiling.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("agentos.resource_manager")


# ── Health & Error Models ────────────────────────────────────────────────────


class ResourceState(StrEnum):
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    DISABLED = "disabled"


class ErrorCategory(StrEnum):
    CONFIGURATION = "configuration"
    AUTHENTICATION = "authentication"
    NETWORK = "network"
    DEPENDENCY_MISSING = "dependency_missing"
    OPTIONAL_MISSING = "optional_missing"
    INTERNAL = "internal"


@dataclass
class ResourceHealth:
    name: str
    state: ResourceState = ResourceState.STARTING
    reason: str = ""
    category: ErrorCategory | None = None
    required: bool = False
    retry_count: int = 0
    next_retry_at: float = 0.0
    last_checked_at: float = 0.0
    latency_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.state == ResourceState.READY

    @property
    def icon(self) -> str:
        return {
            ResourceState.READY: "✓",
            ResourceState.DEGRADED: "⚠",
            ResourceState.UNAVAILABLE: "✗",
            ResourceState.FAILED: "✗",
            ResourceState.DISABLED: "—",
            ResourceState.STARTING: "…",
        }.get(self.state, "?")


@dataclass
class ResourceConfig:
    name: str
    required: bool = True
    enabled: bool = True
    max_retries: int = 5
    initial_backoff_sec: float = 1.0
    max_backoff_sec: float = 300.0
    backoff_multiplier: float = 3.0
    timeout_sec: float = 10.0
    dependencies: list[str] = field(default_factory=list)
    config_keys: list[str] = field(default_factory=list)


# ── Resource Protocol ────────────────────────────────────────────────────────


@runtime_checkable
class Resource(Protocol):
    """Every external resource must implement this protocol."""

    @property
    def name(self) -> str: ...

    async def initialize(self) -> ResourceHealth: ...

    async def health(self) -> ResourceHealth: ...

    async def shutdown(self) -> None: ...

    @property
    def config(self) -> ResourceConfig: ...


# ── Retry Backoff ────────────────────────────────────────────────────────────


class BackoffRetryPolicy:
    """Backoff with ceiling — 1s, 5s, 30s, 60s, 300s (the sprint spec's exact
    example sequence), clamped at `maximum` for any attempt past the last
    step rather than continuing to grow via a multiplier formula."""

    _STEPS: tuple[float, ...] = (1.0, 5.0, 30.0, 60.0, 300.0)

    def __init__(
        self,
        initial: float = 1.0,
        maximum: float = 300.0,
        multiplier: float = 3.0,
        max_retries: int = 5,
    ) -> None:
        self.initial = initial
        self.maximum = maximum
        self.multiplier = multiplier
        self.max_retries = max_retries

    def delay(self, attempt: int) -> float:
        if attempt < len(self._STEPS):
            return min(self._STEPS[attempt], self.maximum)
        return self.maximum

    def should_retry(self, attempt: int) -> bool:
        return attempt < self.max_retries


# ── Config Validator ─────────────────────────────────────────────────────────


@dataclass
class ConfigIssue:
    resource: str
    key: str
    category: ErrorCategory
    message: str


class ConfigValidator:
    """Validates configuration before resource initialization."""

    @staticmethod
    def validate(resources: list[ResourceConfig]) -> list[ConfigIssue]:
        issues: list[ConfigIssue] = []
        for rc in resources:
            if not rc.enabled:
                continue
            for key in rc.config_keys:
                import os

                val = os.environ.get(key, "").strip()
                if not val and rc.required:
                    issues.append(
                        ConfigIssue(
                            resource=rc.name,
                            key=key,
                            category=ErrorCategory.CONFIGURATION,
                            message=f"Required config {key} is not set",
                        )
                    )
                elif not val and not rc.required:
                    pass  # optional — no issue
        return issues


# ── Resource Manager ─────────────────────────────────────────────────────────


class ResourceManager:
    """Manages all external resources with health tracking and graceful degradation."""

    def __init__(self) -> None:
        self._resources: dict[str, Resource] = {}
        self._health: dict[str, ResourceHealth] = {}
        self._configs: dict[str, ResourceConfig] = {}
        self._retry_policies: dict[str, BackoffRetryPolicy] = {}
        self._initialized: bool = False
        self._background_task: asyncio.Task | None = None

    def register(self, resource: Resource, config: ResourceConfig | None = None) -> None:
        """Register a resource. Config can be provided or inferred from resource.config."""
        cfg = config or resource.config
        self._resources[resource.name] = resource
        self._configs[resource.name] = cfg
        self._retry_policies[resource.name] = BackoffRetryPolicy(
            initial=cfg.initial_backoff_sec,
            maximum=cfg.max_backoff_sec,
            multiplier=cfg.backoff_multiplier,
            max_retries=cfg.max_retries,
        )
        self._health[resource.name] = ResourceHealth(
            name=resource.name,
            state=ResourceState.DISABLED if not cfg.enabled else ResourceState.STARTING,
            required=cfg.required,
        )

    def validate_config(self) -> list[ConfigIssue]:
        """Validate all registered resources' configuration before init."""
        configs = [cfg for cfg in self._configs.values() if cfg.enabled]
        return ConfigValidator.validate(configs)

    async def initialize_all(self) -> dict[str, ResourceHealth]:
        """Initialize all resources. Required failures raise. Optional failures degrade gracefully."""

        # validate the config
        issues = self.validate_config()
        if issues:
            for issue in issues:
                logger.warning(
                    "[resource] %s: %s — %s",
                    issue.resource,
                    issue.category,
                    issue.message,
                )
                self._health[issue.resource] = ResourceHealth(
                    name=issue.resource,
                    state=ResourceState.FAILED,
                    reason=issue.message,
                    category=issue.category,
                    required=self._configs.get(issue.resource, ResourceConfig(name=issue.resource)).required,
                )

        # get disabled resources
        for name, resource in self._resources.items():
            cfg = self._configs[name]
            if not cfg.enabled:
                self._health[name] = ResourceHealth(
                    name=name,
                    state=ResourceState.DISABLED,
                    required=cfg.required,
                )
                continue

            # get the failed resources
            if self._health[name].state == ResourceState.FAILED:
                if cfg.required:
                    raise RuntimeError(f"Required resource {name} failed config validation")
                continue

            # Required resources get their full configured retry budget
            # inline (boot should wait for Mongo/Redis to come up). Optional
            # resources get a single attempt here — no blocking sleeps
            # during boot — and start_background_retry() picks up the full
            # retry budget afterward without holding up startup.
            health = await self._try_initialize(
                resource,
                name,
                max_retries_override=None if cfg.required else 0,
            )
            self._health[name] = health

            if health.state == ResourceState.FAILED and cfg.required:
                raise RuntimeError(f"Required resource {name} failed: {health.reason}")

        self._initialized = True
        return dict(self._health)

    async def _try_initialize(
        self,
        resource: Resource,
        name: str,
        max_retries_override: int | None = None,
    ) -> ResourceHealth:
        """Initialize a resource with retry on transient failures.

        max_retries_override lets callers cap (or lift) the retry budget for
        this one call without touching the resource's registered
        BackoffRetryPolicy — used by initialize_all() to keep boot fast for
        optional resources (override=0, single attempt) while
        start_background_retry() reuses the full configured budget.
        """
        cfg = self._configs[name]
        policy = self._retry_policies[name]
        max_retries = policy.max_retries if max_retries_override is None else max_retries_override

        def _should_retry(attempt: int) -> bool:
            return attempt < max_retries

        # for attempt in the number or retries
        for attempt in range(max_retries + 1):
            start = time.time()
            try:
                health = await asyncio.wait_for(
                    resource.initialize(),
                    timeout=cfg.timeout_sec,
                )
                health.latency_ms = (time.time() - start) * 1000
                health.last_checked_at = time.time()
                health.retry_count = attempt

                # if ready or degraded just log it
                if health.state == ResourceState.READY or health.state == ResourceState.DEGRADED:
                    if attempt > 0:
                        logger.info("[resource] %s recovered after %d retries", name, attempt)
                    return health

                # if failed then retry
                if health.state == ResourceState.FAILED:
                    if not _should_retry(attempt):
                        return health
                    delay = policy.delay(attempt)
                    health.next_retry_at = time.time() + delay
                    logger.debug(
                        "[resource] %s init returned FAILED, retrying in %.1fs (attempt %d/%d)",
                        name,
                        delay,
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue

                # if unavaialable then retry
                if health.state == ResourceState.UNAVAILABLE:
                    if not cfg.required:
                        health.category = ErrorCategory.OPTIONAL_MISSING
                        return health
                    if not _should_retry(attempt):
                        return health
                    delay = policy.delay(attempt)
                    health.next_retry_at = time.time() + delay
                    logger.debug(
                        "[resource] %s UNAVAILABLE, retrying in %.1fs (attempt %d/%d)",
                        name,
                        delay,
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue

                return health

            except asyncio.TimeoutError:
                health = ResourceHealth(
                    name=name,
                    state=ResourceState.UNAVAILABLE,
                    reason=f"Connection timed out after {cfg.timeout_sec}s",
                    category=ErrorCategory.NETWORK,
                    required=cfg.required,
                    retry_count=attempt,
                )
                if not _should_retry(attempt):
                    return health
                delay = policy.delay(attempt)
                health.next_retry_at = time.time() + delay
                await asyncio.sleep(delay)

            except Exception as exc:
                category = self._classify_error(exc)
                health = ResourceHealth(
                    name=name,
                    state=ResourceState.FAILED,
                    reason=str(exc)[:200],
                    category=category,
                    required=cfg.required,
                    retry_count=attempt,
                )
                if not _should_retry(attempt):
                    return health
                delay = policy.delay(attempt)
                health.next_retry_at = time.time() + delay
                await asyncio.sleep(delay)

        return ResourceHealth(
            name=name,
            state=ResourceState.FAILED,
            reason=f"Exhausted {max_retries} retries",
            required=cfg.required,
            retry_count=max_retries,
        )

    def start_background_retry(self, interval_sec: float = 10.0) -> None:
        """Start a fire-and-forget task that keeps retrying any resource not
        currently READY/DEGRADED/DISABLED, using each resource's full
        configured retry budget (unlike initialize_all()'s single boot-time
        attempt for optional resources) — so a resource that's down at boot
        can still recover later without anyone polling for it, and without
        ever blocking startup.
        """
        if self._background_task is not None and not self._background_task.done():
            return
        self._background_task = asyncio.create_task(self._background_retry_loop(interval_sec))

    async def stop_background_retry(self) -> None:
        if self._background_task is None:
            return
        self._background_task.cancel()
        try:
            await self._background_task
        except asyncio.CancelledError:
            pass
        self._background_task = None

    # Categories that can never resolve on their own — retrying them forever
    # every interval_sec just spams logs (observed: a permanently-uninstalled
    # optional dep like mem0ai logging a warning every ~10s indefinitely).
    # Installing a package or fixing a config file needs a deploy, not a retry.
    _PERMANENT_CATEGORIES = (
        ErrorCategory.DEPENDENCY_MISSING,
        ErrorCategory.OPTIONAL_MISSING,
        ErrorCategory.CONFIGURATION,
    )

    async def _background_retry_loop(self, interval_sec: float) -> None:
        gave_up: set[str] = set()
        while True:
            await asyncio.sleep(interval_sec)
            now = time.time()
            for name, resource in self._resources.items():
                health = self._health.get(name)
                if health is None or health.state in (
                    ResourceState.READY,
                    ResourceState.DEGRADED,
                    ResourceState.DISABLED,
                ):
                    continue
                if health.category in self._PERMANENT_CATEGORIES:
                    if name not in gave_up:
                        gave_up.add(name)
                        logger.info(
                            "[resource] %s giving up on background retry — %s is not something "
                            "a retry can fix (reason: %s)",
                            name,
                            health.category.value,
                            health.reason,
                        )
                    continue
                if health.next_retry_at and health.next_retry_at > now:
                    continue
                try:
                    new_health = await self._try_initialize(resource, name)
                except Exception as exc:
                    logger.debug("[resource] background retry crashed for %s: %s", name, exc)
                    continue
                if new_health.state != health.state:
                    logger.info(
                        "[resource] %s transitioned %s -> %s",
                        name,
                        health.state.value,
                        new_health.state.value,
                    )
                self._health[name] = new_health
                gave_up.discard(name)

    def _classify_error(self, exc: Exception) -> ErrorCategory:
        msg = str(exc).lower()
        exc_type = type(exc).__name__

        if "config" in msg or "missing" in msg or "not set" in msg:
            return ErrorCategory.CONFIGURATION
        if "auth" in msg or "401" in msg or "unauthorized" in msg or "credential" in msg:
            return ErrorCategory.AUTHENTICATION
        if "connect" in msg or "timeout" in msg or "network" in msg or "dns" in msg:
            return ErrorCategory.NETWORK
        if "import" in msg or "module" in msg or "not installed" in msg:
            return ErrorCategory.DEPENDENCY_MISSING
        if "filenotfound" in exc_type.lower() or "no such file" in msg:
            return ErrorCategory.CONFIGURATION
        return ErrorCategory.INTERNAL

    async def health_all(self) -> dict[str, ResourceHealth]:
        """Check health of all resources."""
        for name, resource in self._resources.items():
            if self._health[name].state in (
                ResourceState.DISABLED,
                ResourceState.FAILED,
            ):
                continue
            try:
                health = await asyncio.wait_for(resource.health(), timeout=5.0)
                health.last_checked_at = time.time()
                self._health[name] = health
            except Exception as exc:
                self._health[name] = ResourceHealth(
                    name=name,
                    state=ResourceState.UNAVAILABLE,
                    reason=str(exc)[:200],
                    category=self._classify_error(exc),
                    required=self._configs[name].required,
                )
        return dict(self._health)

    def get_health(self, name: str) -> ResourceHealth | None:
        return self._health.get(name)

    def is_ready(self, name: str) -> bool:
        h = self._health.get(name)
        return h is not None and h.state == ResourceState.READY

    def all_ready(self) -> bool:
        return all(
            h.state in (ResourceState.READY, ResourceState.DEGRADED, ResourceState.DISABLED)
            for h in self._health.values()
            if self._configs.get(h.name, ResourceConfig(name="")).required
        )

    def summary(self) -> list[dict[str, Any]]:
        """Produce a resource health summary."""
        result = []
        for name in self._resources:
            h = self._health.get(name, ResourceHealth(name=name, state=ResourceState.STARTING))
            cfg = self._configs.get(name, ResourceConfig(name=name))
            result.append(
                {
                    "name": name,
                    "state": h.state.value,
                    "icon": h.icon,
                    "reason": h.reason,
                    "category": h.category.value if h.category else None,
                    "required": cfg.required,
                    "enabled": cfg.enabled,
                    "retry_count": h.retry_count,
                }
            )
        return result

    async def shutdown_all(self) -> None:
        await self.stop_background_retry()
        for name, resource in reversed(list(self._resources.items())):
            try:
                await asyncio.wait_for(resource.shutdown(), timeout=5.0)
            except Exception:
                logger.debug("shutdown_all: suppressed exception", exc_info=True)
            self._health[name] = ResourceHealth(
                name=name,
                state=ResourceState.DISABLED,
                required=self._configs[name].required,
            )
