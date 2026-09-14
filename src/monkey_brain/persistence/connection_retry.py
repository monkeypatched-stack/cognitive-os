"""Connection Retry and Health Check Utilities — shared across all stores.

Problem Solved:
    Many stores cached connection failures indefinitely:
    - On first connection failure, _client/_driver/_connected set to None/False
    - On next operation, checked cached state without retrying
    - Result: Transient network blips cause permanent service degradation

Solution:
    1. Only cache successful connections
    2. Leave _client/_driver as None if connection fails
    3. Next call automatically retries (lazy initialization)
    4. Supports explicit reconnect() for health checks
    5. All stores follow the same pattern (consistency)

Key Principle: "Try again on next call, never cache failure"

This pattern is now proven in payment_store.py (PendingPayment persistence).
Live deployment finding: a single transient Redis connection failure at boot
permanently disabled persistence for that process's lifetime. Fixed by only
caching successful connections.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, TypeVar, Optional
from functools import wraps

logger = logging.getLogger("agentos.persistence.connection_retry")

T = TypeVar("T")


class ConnectionRetryPolicy:
    """Configuration for connection retry behavior."""

    def __init__(
        self,
        initial_delay_sec: float = 0.1,
        max_delay_sec: float = 5.0,
        max_attempts: int = 3,
        backoff_multiplier: float = 2.0,
    ):
        self.initial_delay_sec = initial_delay_sec
        self.max_delay_sec = max_delay_sec
        self.max_attempts = max_attempts
        self.backoff_multiplier = backoff_multiplier

    def get_delay(self, attempt: int) -> float:
        """Get delay for retry attempt (exponential backoff)."""
        delay = self.initial_delay_sec * (self.backoff_multiplier**attempt)
        return min(delay, self.max_delay_sec)


# Default retry policy for all connections
DEFAULT_RETRY_POLICY = ConnectionRetryPolicy(
    initial_delay_sec=0.05,
    max_delay_sec=2.0,
    max_attempts=3,
    backoff_multiplier=2.0,
)


def retry_on_connection_error(
    func: Callable[..., T],
    policy: ConnectionRetryPolicy | None = None,
) -> Callable[..., T]:
    """Decorator for async/sync functions to retry on connection errors.

    Only retries on connection-related exceptions, not logic errors.

    Args:
        func: Function to wrap
        policy: ConnectionRetryPolicy (uses DEFAULT if None)

    Returns:
        Wrapped function with retry logic
    """
    policy = policy or DEFAULT_RETRY_POLICY

    async def async_wrapper(*args: Any, **kwargs: Any) -> T:
        last_exc = None
        for attempt in range(policy.max_attempts):
            try:
                return await func(*args, **kwargs)
            except (ConnectionError, TimeoutError, OSError) as exc:
                last_exc = exc
                if attempt < policy.max_attempts - 1:
                    delay = policy.get_delay(attempt)
                    logger.debug(
                        "Retry %d/%d for %s after %.2fs: %s",
                        attempt + 1,
                        policy.max_attempts,
                        func.__name__,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.warning(
                        "All %d retries exhausted for %s: %s",
                        policy.max_attempts,
                        func.__name__,
                        exc,
                    )
        raise last_exc or ConnectionError(f"{func.__name__} failed after retries")

    def sync_wrapper(*args: Any, **kwargs: Any) -> T:
        last_exc = None
        for attempt in range(policy.max_attempts):
            try:
                return func(*args, **kwargs)
            except (ConnectionError, TimeoutError, OSError) as exc:
                last_exc = exc
                if attempt < policy.max_attempts - 1:
                    delay = policy.get_delay(attempt)
                    logger.debug(
                        "Retry %d/%d for %s after %.2fs: %s",
                        attempt + 1,
                        policy.max_attempts,
                        func.__name__,
                        delay,
                        exc,
                    )
                    time.sleep(delay)
                else:
                    logger.warning(
                        "All %d retries exhausted for %s: %s",
                        policy.max_attempts,
                        func.__name__,
                        exc,
                    )
        raise last_exc or ConnectionError(f"{func.__name__} failed after retries")

    if asyncio.iscoroutinefunction(func):

        @wraps(func)
        async def wrapped_async(*args: Any, **kwargs: Any) -> T:
            return await async_wrapper(*args, **kwargs)

        return wrapped_async  # type: ignore
    else:

        @wraps(func)
        def wrapped_sync(*args: Any, **kwargs: Any) -> T:
            return sync_wrapper(*args, **kwargs)

        return wrapped_sync  # type: ignore


def ensure_connected(
    is_connected_fn: Callable[[], bool],
    connect_fn: Callable[[], None] | Callable[[], Any],
    logger_instance: Optional[logging.Logger] = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator to ensure connection before operation.

    Usage:
        @ensure_connected(self.is_connected, self.connect)
        def my_operation(self):
            ...

    Args:
        is_connected_fn: Function that checks if connected
        connect_fn: Function that performs connection
        logger_instance: Optional logger for messages

    Returns:
        Decorator function
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        if asyncio.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> T:
                log = logger_instance or logger
                if not is_connected_fn():
                    log.debug("Reconnecting before %s", func.__name__)
                    result = connect_fn()
                    if asyncio.iscoroutine(result):
                        await result
                return await func(*args, **kwargs)

            return async_wrapper  # type: ignore
        else:

            @wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> T:
                log = logger_instance or logger
                if not is_connected_fn():
                    log.debug("Reconnecting before %s", func.__name__)
                    connect_fn()
                return func(*args, **kwargs)

            return sync_wrapper  # type: ignore

    return decorator


class HealthCheckTracker:
    """Track connection health over time with exponential backoff."""

    def __init__(self, initial_check_interval_sec: float = 1.0, max_interval_sec: float = 60.0):
        self.initial_check_interval = initial_check_interval_sec
        self.max_interval = max_interval_sec
        self.last_check_time: float = 0.0
        self.check_failures: int = 0
        self.last_failure_time: float = 0.0

    def should_check_health(self) -> bool:
        """Return True if enough time has passed since last check."""
        interval = min(
            self.initial_check_interval * (2**self.check_failures),
            self.max_interval,
        )
        return (time.time() - self.last_check_time) > interval

    def record_success(self) -> None:
        """Reset failure count on successful check."""
        self.last_check_time = time.time()
        self.check_failures = 0
        logger.debug("Health check passed, resetting backoff")

    def record_failure(self) -> None:
        """Increment failure count and track time."""
        self.last_check_time = time.time()
        self.last_failure_time = self.last_check_time
        self.check_failures += 1
        logger.debug("Health check failed (failures=%d), backing off", self.check_failures)

    def get_backoff_interval(self) -> float:
        """Get current backoff interval."""
        return min(
            self.initial_check_interval * (2**self.check_failures),
            self.max_interval,
        )


def never_cache_failure(
    client_attr: str = "_client",
    connected_attr: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to ensure connection failures are never cached.

    This is the key pattern from payment_store.py:
    - Only cache _client when connection succeeds
    - On failure, leave _client as None
    - Next operation automatically retries

    Usage:
        class MyStore:
            def __init__(self):
                self._client = None

            @never_cache_failure("_client")
            def connect(self):
                self._client = create_connection()  # Only set if succeeds
                self._client.ping()

    Args:
        client_attr: Name of attribute to cache connection (e.g. "_client", "_driver")
        connected_attr: Optional flag attribute (e.g. "_connected")

    Returns:
        Decorator function
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if asyncio.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
                # Reset state before attempting connection
                # This ensures we don't rely on cached success from previous call
                old_client = getattr(self, client_attr, None)
                try:
                    result = await func(self, *args, **kwargs)
                    # Only set connected flag on success
                    if connected_attr:
                        setattr(self, connected_attr, True)
                    logger.debug("%s connected successfully", func.__name__)
                    return result
                except Exception as exc:
                    # On failure, leave client as None so next call retries
                    setattr(self, client_attr, None)
                    if connected_attr:
                        setattr(self, connected_attr, False)
                    logger.warning("%s failed: %s", func.__name__, exc)
                    # Don't raise: let caller decide how to handle

            return async_wrapper  # type: ignore
        else:

            @wraps(func)
            def sync_wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
                try:
                    result = func(self, *args, **kwargs)
                    # Only set connected flag on success
                    if connected_attr:
                        setattr(self, connected_attr, True)
                    logger.debug("%s connected successfully", func.__name__)
                    return result
                except Exception as exc:
                    # On failure, leave client as None so next call retries
                    setattr(self, client_attr, None)
                    if connected_attr:
                        setattr(self, connected_attr, False)
                    logger.warning("%s failed: %s", func.__name__, exc)
                    # Don't raise: let caller decide how to handle

            return sync_wrapper  # type: ignore

    return decorator
