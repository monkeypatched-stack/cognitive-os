"""Error Recovery & Circuit Breaker — Phase 5 Production Hardening.

Provides:
- Circuit breaker pattern for fault isolation
- Automatic retry with exponential backoff
- Graceful degradation
- Error tracking and recovery metrics
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, TypeVar, Any

logger = logging.getLogger("agentos.error_recovery")

T = TypeVar("T")


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal: requests pass through
    OPEN = "open"  # Failing: requests rejected immediately
    HALF_OPEN = "half_open"  # Testing: allow single request to test


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration."""

    failure_threshold: int = 5  # Failures before opening
    recovery_timeout: int = 60  # Seconds before attempting recovery
    success_threshold: int = 2  # Successes needed to close from half-open
    half_open_max_calls: int = 3  # Max concurrent calls in half-open


@dataclass
class RetryConfig:
    """Retry configuration with exponential backoff."""

    max_retries: int = 3
    initial_delay: float = 0.1  # seconds
    max_delay: float = 10.0  # seconds
    backoff_multiplier: float = 2.0
    jitter: bool = True  # Add random jitter to prevent thundering herd


class CircuitBreaker:
    """Circuit breaker for fault isolation.

    Protects against cascading failures by:
    - Rejecting requests when service unhealthy
    - Attempting recovery periodically
    - Fast-failing instead of hanging
    """

    def __init__(self, name: str, config: CircuitBreakerConfig | None = None) -> None:
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0.0
        self.last_state_change = time.time()
        self.total_calls = 0
        self.total_failures = 0

    def call(self, func: Callable[..., T], *args, **kwargs) -> T:
        """Execute function with circuit breaker protection.

        Args:
            func: Function to execute
            *args, **kwargs: Arguments to pass to func

        Returns:
            Result of func()

        Raises:
            RuntimeError: If circuit is open
        """
        self.total_calls += 1

        # Check if we should attempt recovery
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.config.recovery_timeout:
                self._transition_to(CircuitState.HALF_OPEN)
            else:
                raise RuntimeError(f"[circuit_breaker] {self.name} is OPEN; fast-failing")

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    async def acall(self, func: Callable[..., Any], *args, **kwargs) -> T:
        """Async counterpart to call() — same open/half-open/closed logic,
        but awaits func instead of calling it synchronously, so a real
        async I/O call (e.g. ModelBackend.complete() against Ollama —
        see kernel/pipeline/llm_planner.py) stays genuinely cancellable
        by an enclosing asyncio.wait_for/task cancellation the whole way
        down to the socket operation, instead of running inside an
        asyncio.to_thread wrapper that a timeout can stop *waiting* on
        but never actually stop (docs/adr/019-runtime-performance-audit.md's
        Warehouse Worker investigation confirmed this live: a cancelled
        tick's own retry-loop output kept appearing in the log well after
        the tick had already been reported as timed out)."""
        self.total_calls += 1

        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.config.recovery_timeout:
                self._transition_to(CircuitState.HALF_OPEN)
            else:
                raise RuntimeError(f"[circuit_breaker] {self.name} is OPEN; fast-failing")

        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    def _on_success(self) -> None:
        """Handle successful call."""
        self.failure_count = 0

        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.config.success_threshold:
                self._transition_to(CircuitState.CLOSED)
                logger.info("[circuit_breaker] %s recovered to CLOSED", self.name)

    def _on_failure(self) -> None:
        """Handle failed call."""
        self.failure_count += 1
        self.total_failures += 1
        self.last_failure_time = time.time()
        self.success_count = 0

        if self.failure_count >= self.config.failure_threshold:
            self._transition_to(CircuitState.OPEN)
            logger.error(
                "[circuit_breaker] %s opened after %d failures",
                self.name,
                self.failure_count,
            )

    def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to new state."""
        old_state = self.state
        self.state = new_state
        self.last_state_change = time.time()
        logger.info("[circuit_breaker] %s: %s → %s", self.name, old_state.value, new_state.value)

    def health(self) -> dict[str, Any]:
        """Get circuit breaker health status."""
        uptime = time.time() - self.last_state_change
        failure_rate = (self.total_failures / self.total_calls * 100) if self.total_calls > 0 else 0.0

        return {
            "name": self.name,
            "state": self.state.value,
            "total_calls": self.total_calls,
            "total_failures": self.total_failures,
            "failure_rate": f"{failure_rate:.1f}%",
            "current_uptime": f"{uptime:.1f}s",
            "failure_count": self.failure_count,
        }


class RetryableFunction:
    """Wraps a function with retry logic and exponential backoff."""

    def __init__(self, func: Callable, config: RetryConfig | None = None) -> None:
        self.func = func
        self.config = config or RetryConfig()
        self.attempt_count = 0
        self.last_exception = None

    def __call__(self, *args, **kwargs) -> Any:
        """Execute function with retries."""
        self.attempt_count = 0
        self.last_exception = None

        for attempt in range(self.config.max_retries + 1):
            self.attempt_count = attempt + 1
            try:
                result = self.func(*args, **kwargs)
                logger.debug(
                    "[retry] %s succeeded on attempt %d",
                    self.func.__name__,
                    self.attempt_count,
                )
                return result
            except Exception as exc:
                self.last_exception = exc
                if attempt >= self.config.max_retries:
                    logger.error(
                        "[retry] %s failed after %d attempts",
                        self.func.__name__,
                        self.config.max_retries + 1,
                    )
                    raise

                # Calculate backoff delay
                delay = self._calculate_delay(attempt)
                logger.warning(
                    "[retry] %s failed (attempt %d/%d), retrying in %.2fs: %s",
                    self.func.__name__,
                    self.attempt_count,
                    self.config.max_retries + 1,
                    delay,
                    exc,
                )
                time.sleep(delay)

    def _calculate_delay(self, attempt: int) -> float:
        """Calculate backoff delay with optional jitter."""
        delay = min(
            self.config.initial_delay * (self.config.backoff_multiplier**attempt),
            self.config.max_delay,
        )

        if self.config.jitter:
            import random

            delay *= 0.5 + random.random()  # 50-150% of calculated delay

        return delay


class ErrorRecoveryRegistry:
    """Manages circuit breakers and retry policies for production resilience."""

    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self._retryables: dict[str, RetryableFunction] = {}

    def register_circuit_breaker(self, name: str, config: CircuitBreakerConfig | None = None) -> CircuitBreaker:
        """Register a circuit breaker."""
        breaker = CircuitBreaker(name, config)
        self._breakers[name] = breaker
        logger.info("[error_recovery] Registered circuit breaker: %s", name)
        return breaker

    def get_circuit_breaker(self, name: str) -> CircuitBreaker | None:
        """Get a circuit breaker by name."""
        return self._breakers.get(name)

    def register_retryable(self, name: str, func: Callable, config: RetryConfig | None = None) -> RetryableFunction:
        """Register a retryable function."""
        retryable = RetryableFunction(func, config)
        self._retryables[name] = retryable
        logger.info("[error_recovery] Registered retryable: %s", name)
        return retryable

    def health_check(self) -> dict[str, Any]:
        """Get health status of all circuit breakers."""
        return {
            "circuit_breakers": {name: breaker.health() for name, breaker in self._breakers.items()},
            "total_breakers": len(self._breakers),
            "open_breakers": sum(1 for b in self._breakers.values() if b.state == CircuitState.OPEN),
        }


# Singleton instance
_registry: ErrorRecoveryRegistry | None = None


def get_error_recovery_registry() -> ErrorRecoveryRegistry:
    """Get or create singleton error recovery registry."""
    global _registry
    if _registry is None:
        _registry = ErrorRecoveryRegistry()
    return _registry


def reset_error_recovery_registry() -> None:
    """Reset singleton (for testing)."""
    global _registry
    _registry = None
