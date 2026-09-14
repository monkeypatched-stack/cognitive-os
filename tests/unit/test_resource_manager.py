"""Tests for ResourceManager — health states, retry, config validation, graceful degradation."""

import asyncio
import os
import sys

_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_repo, os.path.join(_repo, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from unittest.mock import AsyncMock, MagicMock
from src.monkey_brain.kernel.resource_manager import (
    ResourceManager,
    ResourceState,
    ErrorCategory,
    ResourceHealth,
    ResourceConfig,
    ConfigValidator,
    BackoffRetryPolicy,
    ConfigIssue,
)


class FakeResource:
    def __init__(
        self,
        name: str,
        init_state: ResourceState = ResourceState.READY,
        init_reason: str = "",
        required: bool = True,
    ):
        self._name = name
        self._init_state = init_state
        self._init_reason = init_reason
        self._config = ResourceConfig(name=name, required=required)
        self._health_calls = 0

    @property
    def name(self):
        return self._name

    @property
    def config(self):
        return self._config

    async def initialize(self):
        return ResourceHealth(name=self._name, state=self._init_state, reason=self._init_reason)

    async def health(self):
        self._health_calls += 1
        return ResourceHealth(name=self._name, state=self._init_state)

    async def shutdown(self):
        pass


class FailingResource:
    def __init__(self, name: str, exc: Exception, required: bool = True):
        self._name = name
        self._exc = exc
        self._config = ResourceConfig(name=name, required=required)
        self.init_calls = 0

    @property
    def name(self):
        return self._name

    @property
    def config(self):
        return self._config

    async def initialize(self):
        self.init_calls += 1
        raise self._exc

    async def health(self):
        return ResourceHealth(name=self._name, state=ResourceState.UNAVAILABLE)

    async def shutdown(self):
        pass


class SlowResource:
    def __init__(self, name: str, delay: float = 0.1):
        self._name = name
        self._delay = delay
        self._config = ResourceConfig(name=name, required=True, timeout_sec=0.05)

    @property
    def name(self):
        return self._name

    @property
    def config(self):
        return self._config

    async def initialize(self):
        await asyncio.sleep(self._delay)
        return ResourceHealth(name=self._name, state=ResourceState.READY)

    async def health(self):
        return ResourceHealth(name=self._name, state=ResourceState.READY)

    async def shutdown(self):
        pass


def test_register_resource():
    rm = ResourceManager()
    r = FakeResource("mongo")
    rm.register(r)
    assert "mongo" in rm._resources
    assert rm.get_health("mongo").state == ResourceState.STARTING


def test_initialize_ready():
    async def scenario():
        rm = ResourceManager()
        r = FakeResource("mongo", ResourceState.READY)
        rm.register(r)
        health = await rm.initialize_all()
        assert health["mongo"].state == ResourceState.READY
        assert rm.is_ready("mongo")
        assert rm.all_ready()

    asyncio.run(scenario())


def test_initialize_degraded():
    async def scenario():
        rm = ResourceManager()
        r = FakeResource("influx", ResourceState.DEGRADED, "read-only mode")
        rm.register(r)
        health = await rm.initialize_all()
        assert health["influx"].state == ResourceState.DEGRADED
        assert rm.all_ready()

    asyncio.run(scenario())


def test_optional_resource_unavailable_does_not_block():
    async def scenario():
        rm = ResourceManager()
        r = FakeResource("mem0", ResourceState.UNAVAILABLE, "credentials missing", required=False)
        rm.register(r)
        health = await rm.initialize_all()
        assert health["mem0"].state == ResourceState.UNAVAILABLE
        assert rm.all_ready()

    asyncio.run(scenario())


def test_required_resource_failed_raises():
    async def scenario():
        rm = ResourceManager()
        r = FakeResource("mongo", ResourceState.FAILED, "connection refused", required=True)
        rm.register(r, ResourceConfig(name="mongo", required=True, max_retries=0))  # no 121s backoff
        try:
            await rm.initialize_all()
            assert False, "Should have raised"
        except RuntimeError as e:
            assert "mongo" in str(e)

    asyncio.run(scenario())


def test_required_resource_exception_raises():
    async def scenario():
        rm = ResourceManager()
        r = FailingResource("mongo", Exception("connection refused"), required=True)
        rm.register(r, ResourceConfig(name="mongo", required=True, max_retries=0))
        try:
            await rm.initialize_all()
            assert False, "Should have raised"
        except RuntimeError as e:
            assert "mongo" in str(e)

    asyncio.run(scenario())


def test_optional_resource_exception_degrades():
    async def scenario():
        rm = ResourceManager()
        r = FailingResource("neo4j", Exception("auth failed"), required=False)
        rm.register(r, ResourceConfig(name="neo4j", required=False, max_retries=0))
        health = await rm.initialize_all()
        assert health["neo4j"].state == ResourceState.FAILED
        assert rm.all_ready()

    asyncio.run(scenario())


def test_disabled_resource():
    async def scenario():
        rm = ResourceManager()
        r = FakeResource("nats")
        rm.register(r, ResourceConfig(name="nats", enabled=False))
        health = await rm.initialize_all()
        assert health["nats"].state == ResourceState.DISABLED
        assert rm.all_ready()

    asyncio.run(scenario())


def test_retry_backoff():
    """BackoffRetryPolicy.delay() follows the sprint spec's exact example sequence
    (1s, 5s, 30s, 60s, 300s) rather than a multiplier formula — updated
    after resource_manager.py's RetryPolicy was changed to match it."""
    policy = BackoffRetryPolicy(max_retries=5)
    assert policy.delay(0) == 1.0
    assert policy.delay(1) == 5.0
    assert policy.delay(2) == 30.0
    assert policy.delay(3) == 60.0
    assert policy.delay(4) == 300.0
    assert policy.should_retry(0)
    assert policy.should_retry(4)
    assert not policy.should_retry(5)


def test_retry_backoff_clamps_at_custom_maximum():
    policy = BackoffRetryPolicy(maximum=10.0)
    assert policy.delay(2) == 10.0  # step would be 30.0, clamped to maximum
    assert policy.delay(4) == 10.0  # step would be 300.0, clamped to maximum


def test_optional_resource_single_attempt_at_boot():
    """initialize_all() must not retry optional resources inline — an
    unreachable optional resource used to be able to add multi-minute
    blocking sleeps to boot (up to 1+5+30+60+300s) before this fix capped
    the boot-time pass at a single attempt via max_retries_override=0."""

    async def scenario():
        rm = ResourceManager()
        r = FailingResource("ollama", Exception("connection refused"), required=False)
        # Real retry budget on the resource's own config...
        rm.register(r, ResourceConfig(name="ollama", required=False, max_retries=5))
        health = await rm.initialize_all()
        # ...but initialize_all() must still only try once for an optional
        # resource, regardless of its configured max_retries.
        assert health["ollama"].state == ResourceState.FAILED
        assert r.init_calls == 1

    asyncio.run(scenario())


def test_config_validation():
    configs = [
        ResourceConfig(name="mongo", required=True, config_keys=["TEST_MONGO_KEY"]),
        ResourceConfig(name="redis", required=False, config_keys=["TEST_REDIS_KEY"]),
    ]
    issues = ConfigValidator.validate(configs)
    assert any(i.key == "TEST_MONGO_KEY" for i in issues)
    assert not any(i.key == "TEST_REDIS_KEY" for i in issues)


def test_error_classification():
    rm = ResourceManager()
    assert rm._classify_error(Exception("connection refused")) == ErrorCategory.NETWORK
    assert rm._classify_error(Exception("401 unauthorized")) == ErrorCategory.AUTHENTICATION
    assert rm._classify_error(Exception("config key missing")) == ErrorCategory.CONFIGURATION
    assert rm._classify_error(ImportError("no module named x")) == ErrorCategory.DEPENDENCY_MISSING
    assert rm._classify_error(Exception("something broke")) == ErrorCategory.INTERNAL


def test_health_summary():
    async def scenario():
        rm = ResourceManager()
        rm.register(FirebaseResource("mongo", ResourceState.READY))
        rm.register(FirebaseResource("redis", ResourceState.DEGRADED))
        await rm.initialize_all()
        summary = rm.summary()
        assert len(summary) == 2
        assert any(s["state"] == "ready" for s in summary)
        assert any(s["state"] == "degraded" for s in summary)

    asyncio.run(scenario())


class FirebaseResource:
    def __init__(self, name, state):
        self._name = name
        self._state = state
        self._config = ResourceConfig(name=name)

    @property
    def name(self):
        return self._name

    @property
    def config(self):
        return self._config

    async def initialize(self):
        return ResourceHealth(name=self._name, state=self._state)

    async def health(self):
        return ResourceHealth(name=self._name, state=self._state)

    async def shutdown(self):
        pass


def test_shutdown_all():
    async def scenario():
        rm = ResourceManager()
        r = FirebaseResource("mongo", ResourceState.READY)
        rm.register(r)
        await rm.initialize_all()
        await rm.shutdown_all()
        assert rm.get_health("mongo").state == ResourceState.DISABLED

    asyncio.run(scenario())


def test_multiple_resources_mixed():
    async def scenario():
        rm = ResourceManager()
        # mongo/redis are required primaries; neo4j/influx/mem0 are optional and
        # degrade gracefully. This must be explicit: with the default config a
        # resource is required, and initialize_all() gives a required resource that
        # comes back UNAVAILABLE its FULL retry budget inline — 1+5+30+60+300s of
        # real backoff sleeps (~396s) before it gives up, then FAILS and raises. So
        # a default-config "influx" here would both hang the test for ~6.5 min AND
        # never satisfy the `influx == "unavailable"` assertion below. Optional
        # resources return their reported state immediately (no boot-blocking retry).
        rm.register(FirebaseResource("mongo", ResourceState.READY))
        rm.register(FirebaseResource("redis", ResourceState.READY))
        rm.register(
            FirebaseResource("neo4j", ResourceState.DEGRADED),
            ResourceConfig(name="neo4j", required=False),
        )
        rm.register(
            FirebaseResource("influx", ResourceState.UNAVAILABLE),
            ResourceConfig(name="influx", required=False),
        )
        rm.register(
            FirebaseResource("mem0", ResourceState.DISABLED),
            ResourceConfig(name="mem0", required=False),
        )

        await rm.initialize_all()
        assert rm.is_ready("mongo")
        assert rm.is_ready("redis")
        assert not rm.is_ready("neo4j")
        assert not rm.is_ready("influx")

        summary = rm.summary()
        states = {s["name"]: s["state"] for s in summary}
        assert states["mongo"] == "ready"
        assert states["neo4j"] == "degraded"
        assert states["influx"] == "unavailable"
        assert states["mem0"] == "disabled"

    asyncio.run(scenario())


def test_classify_auth_error():
    rm = ResourceManager()
    assert rm._classify_error(Exception("authentication failed")) == ErrorCategory.AUTHENTICATION
    assert rm._classify_error(Exception("401 Unauthorized")) == ErrorCategory.AUTHENTICATION
    assert rm._classify_error(Exception("invalid credentials")) == ErrorCategory.AUTHENTICATION


def test_classify_network_error():
    rm = ResourceManager()
    assert rm._classify_error(Exception("connection refused")) == ErrorCategory.NETWORK
    assert rm._classify_error(Exception("timeout")) == ErrorCategory.NETWORK
    assert rm._classify_error(Exception("dns resolution failed")) == ErrorCategory.NETWORK


if __name__ == "__main__":
    tests = [
        test_register_resource,
        test_initialize_ready,
        test_initialize_degraded,
        test_optional_resource_unavailable_does_not_block,
        test_required_resource_failed_raises,
        test_required_resource_exception_raises,
        test_optional_resource_exception_degrades,
        test_disabled_resource,
        test_retry_backoff,
        test_retry_backoff_clamps_at_custom_maximum,
        test_optional_resource_single_attempt_at_boot,
        test_config_validation,
        test_error_classification,
        test_health_summary,
        test_shutdown_all,
        test_multiple_resources_mixed,
        test_classify_auth_error,
        test_classify_network_error,
    ]
    ok = 0
    failures = []
    for fn in tests:
        try:
            fn()
            ok += 1
            print(f"  PASS: {fn.__name__}")
        except Exception as e:
            failures.append(f"{fn.__name__}: {e!r}")
            print(f"  FAIL: {fn.__name__}: {e!r}")
    print(f"\nResourceManager: {ok}/{len(tests)} passed")
    if failures:
        for f in failures:
            print(f"  {f}")
