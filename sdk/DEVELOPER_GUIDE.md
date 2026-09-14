# Monkeypatched Adapter SDK - Developer Guide

## Quick Start

### 1. Install the SDK

```bash
pip install monkeypatched-sdk
```

### 2. Create Your First Adapter

```python
from monkeypatched_sdk import (
    CapabilityAdapter,
    AdapterContext,
    AdapterResponse,
    capability_adapter,
)


@capability_adapter(
    capability_id="create_work_order",
    version="1.0.0",
    tags=["cmms", "maintenance"],
)
class CMMSAdapter(CapabilityAdapter):
    """Adapter for creating work orders in CMMS system."""

    async def initialize(self):
        """Called once at startup."""
        self.log("info", "Initializing CMMS adapter")
        # Validate configuration
        self.endpoint = self.configuration.get("endpoint")
        # Test connection

    async def execute(self, context: AdapterContext, inputs: dict) -> AdapterResponse:
        """Execute the capability."""
        try:
            # Validate inputs
            equipment_id = inputs.get("equipment_id")
            description = inputs.get("description")

            # Call external system
            result = await self._create_work_order(equipment_id, description)

            # Emit event
            await self.emit_event(
                "work_order_created",
                {"work_order_id": result["id"]},
            )

            # Emit metric
            await self.emit_metric("work_orders_created", 1)

            return AdapterResponse(
                success=True,
                result={"work_order_id": result["id"]},
            )
        except Exception as e:
            return AdapterResponse(
                success=False,
                error=str(e),
            )

    async def health_check(self) -> bool:
        """Check if CMMS is reachable."""
        try:
            # Simple connectivity test
            return await self._test_connection()
        except:
            return False

    async def shutdown(self):
        """Clean up on shutdown."""
        self.log("info", "Shutting down CMMS adapter")

    async def _create_work_order(self, equipment_id: str, description: str):
        """Internal: Call CMMS API."""
        # Implementation here
        pass

    async def _test_connection(self) -> bool:
        """Internal: Test CMMS connectivity."""
        # Implementation here
        pass
```

### 3. Create Configuration

```yaml
# config.yaml
sdk:
  log_level: INFO
  health_check_interval: 30
  telemetry_exporter: prometheus

adapters:
  cmms:
    enabled: true
    endpoint: https://cmms.example.com/api
    credentials:
      api_key: ${CMMS_API_KEY}
    timeout: 45
```

### 4. Run the SDK

```python
from monkeypatched_sdk import LifecycleManager

# Initialize SDK
lifecycle = LifecycleManager()
await lifecycle.startup()

# System is ready, platform can call adapters now

# On shutdown
await lifecycle.shutdown()
```

---

## Core Concepts

### CapabilityAdapter Contract

Every adapter implements four methods:

| Method | Called When | Responsibility |
|--------|-----------|-----------------|
| `initialize()` | SDK starts | Setup, validation, connection test |
| `execute()` | Platform requests capability | Do the actual work |
| `health_check()` | Every 30 seconds | Check if system is reachable |
| `shutdown()` | SDK stops | Cleanup, close connections |

### Dependency Injection

The SDK injects these services into your adapter:

```python
class MyAdapter(CapabilityAdapter):
    def __init__(self, adapter_id: str, capability_id: str):
        super().__init__(adapter_id, capability_id)

        # These are available after init:
        self.logger  # Logging
        self.event_bus  # Publish events
        self.telemetry  # Metrics & traces
        self.configuration  # Config access
        self.connection_pool  # Connection management
```

### AdapterContext

Passed to `execute()`:

```python
async def execute(self, context: AdapterContext, inputs: dict):
    context.capability_id  # "create_work_order"
    context.workflow_id  # Workflow being executed
    context.execution_id  # Unique execution instance
    context.world_state  # Current world model state
    context.request_id  # For tracing
```

### AdapterResponse

Return from `execute()`:

```python
return AdapterResponse(
    success=True,  # Did it work?
    result={  # The actual result
        "work_order_id": "WO-123",
    },
    error=None,  # Error message if failed
    metadata={  # Extra information
        "execution_time_ms": 150,
    },
)
```

---

## Common Patterns

### Pattern 1: HTTP-Based Integration

```python
from monkeypatched_sdk import HTTPConnectionPool


@capability_adapter(capability_id="create_work_order")
class HTTPAdapter(CapabilityAdapter):
    async def initialize(self):
        self.pool = HTTPConnectionPool(
            base_url=self.configuration.get("endpoint"),
            pool_size=5,
        )
        await self.pool.initialize()

    async def execute(self, context, inputs) -> AdapterResponse:
        conn = await self.pool.acquire()
        try:
            response = await conn.post(
                "/api/work-orders",
                json=inputs,
                timeout=30,
            )
            data = await response.json()
            return AdapterResponse(success=True, result=data)
        finally:
            await self.pool.release(conn)

    async def health_check(self) -> bool:
        conn = await self.pool.acquire()
        try:
            response = await conn.get("/health", timeout=5)
            return response.status == 200
        finally:
            await self.pool.release(conn)

    async def shutdown(self):
        await self.pool.shutdown()
```

### Pattern 2: OAuth2 Authentication

```python
from monkeypatched_sdk import OAuth2Handler


@capability_adapter(capability_id="read_data")
class OAuth2Adapter(CapabilityAdapter):
    async def initialize(self):
        self.auth = OAuth2Handler(
            token_endpoint=self.configuration.get("oauth_endpoint"),
            client_id=self.configuration.get("client_id"),
            client_secret=self.configuration.get("client_secret"),
        )

    async def execute(self, context, inputs) -> AdapterResponse:
        # Get auth headers (handles token refresh)
        headers = await self.auth.authenticate()

        # Use headers in your API call
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.example.com/data",
                headers=headers,
            ) as resp:
                data = await resp.json()
                return AdapterResponse(success=True, result=data)

    async def health_check(self) -> bool:
        return await self.auth.is_valid()

    async def shutdown(self):
        pass
```

### Pattern 3: Connection Pooling (Database)

```python
from monkeypatched_sdk import DatabaseConnectionPool


@capability_adapter(capability_id="query_data")
class DatabaseAdapter(CapabilityAdapter):
    async def initialize(self):
        self.pool = DatabaseConnectionPool(
            connection_string=self.configuration.get("connection_string"),
            pool_size=10,
        )
        await self.pool.initialize()

    async def execute(self, context, inputs) -> AdapterResponse:
        conn = await self.pool.acquire()
        try:
            result = await conn.execute(
                "SELECT * FROM table WHERE id = ?",
                [inputs["id"]],
            )
            return AdapterResponse(
                success=True,
                result={"rows": result},
            )
        finally:
            await self.pool.release(conn)

    async def health_check(self) -> bool:
        conn = await self.pool.acquire()
        try:
            await conn.execute("SELECT 1")
            return True
        except:
            return False
        finally:
            await self.pool.release(conn)

    async def shutdown(self):
        await self.pool.shutdown()
```

### Pattern 4: Metrics and Tracing

```python
@capability_adapter(capability_id="process_data")
class MetricsAdapter(CapabilityAdapter):
    async def execute(self, context, inputs) -> AdapterResponse:
        # Create trace span
        async with await self.telemetry.start_span("process_data") as span:
            span.set_tag("input_size", len(inputs))

            try:
                # Do work
                result = await self._process(inputs)

                # Emit custom metric
                await self.emit_metric(
                    "items_processed",
                    len(result),
                    tags={"type": inputs.get("type", "unknown")},
                )

                return AdapterResponse(success=True, result=result)

            except Exception as e:
                # Error metric
                await self.emit_metric("process_errors", 1)
                span.set_tag("error", True)
                span.set_tag("error_message", str(e))
                return AdapterResponse(success=False, error=str(e))

    async def _process(self, inputs):
        # Implementation
        pass
```

### Pattern 5: Event Publishing

```python
@capability_adapter(capability_id="create_order")
class EventAdapter(CapabilityAdapter):
    async def execute(self, context, inputs) -> AdapterResponse:
        try:
            # Create the order
            order = await self._create_order(inputs)

            # Emit event to platform
            await self.emit_event(
                event_type="order_created",
                data={
                    "order_id": order["id"],
                    "customer_id": order["customer_id"],
                    "amount": order["amount"],
                    "workflow_id": context.workflow_id,
                },
            )

            return AdapterResponse(success=True, result=order)

        except Exception as e:
            # Emit error event
            await self.emit_event(
                event_type="order_creation_failed",
                data={
                    "error": str(e),
                    "inputs": inputs,
                },
            )
            return AdapterResponse(success=False, error=str(e))

    async def _create_order(self, inputs):
        # Implementation
        pass
```

---

## Configuration Guide

### Basic Configuration

```yaml
sdk:
  log_level: INFO                    # DEBUG, INFO, WARNING, ERROR
  health_check_interval: 30          # Seconds between health checks
  event_batch_size: 100              # Events batched before sending
  telemetry_exporter: prometheus     # prometheus, influx, jaeger

adapters:
  my_adapter:
    enabled: true                    # Enable/disable at runtime
    endpoint: https://api.example.com
    credentials:
      api_key: ${MY_API_KEY}         # Environment variable
      username: ${MY_USERNAME}
    timeout: 45                      # Request timeout in seconds
    retry_count: 3                   # Number of retries
    extra:                           # Custom config
      work_order_prefix: WO
      company_id: "1000"
```

### Environment Variables

Override any config:

```bash
# Adapter config
export ADAPTER_CMMS_ENDPOINT=https://new-endpoint.com
export ADAPTER_CMMS_TIMEOUT=60
export ADAPTER_CMMS_RETRY_COUNT=5

# SDK config
export SDK_LOG_LEVEL=DEBUG
export SDK_HEALTH_CHECK_INTERVAL=60
```

### Accessing Configuration

```python
class MyAdapter(CapabilityAdapter):
    async def initialize(self):
        # Get from configuration manager
        endpoint = self.configuration.get("endpoint")
        timeout = self.configuration.get("timeout", 30)
        api_key = self.configuration.get("api_key")
```

---

## Logging

### Using the Logger

```python
# Simple logging
self.log("info", "Starting work")
self.log("warning", "Connection slow")
self.log("error", "Failed to connect", exception=e)

# With extra context
self.log("info", "Created work order", work_order_id="WO-123", status="created")

# Log levels: debug, info, warning, error, critical
```

### Log Output

The SDK logs automatically:
- Adapter initialization
- Execution start/end
- Health check results
- Errors and exceptions

---

## Error Handling

### Standard Exceptions

```python
from monkeypatched_sdk import (
    AdapterInitializationError,  # Init failed
    AdapterExecutionError,  # Execute failed
    HealthCheckError,  # Health check failed
    ConfigurationError,  # Config invalid
)


async def initialize(self):
    try:
        # Setup
        pass
    except Exception as e:
        raise AdapterInitializationError(f"Failed to connect: {e}")
```

### Return Errors via AdapterResponse

```python
async def execute(self, context, inputs) -> AdapterResponse:
    try:
        # Do work
        pass
    except ValueError as e:
        return AdapterResponse(
            success=False,
            error=f"Invalid input: {e}",
        )
    except ConnectionError as e:
        return AdapterResponse(
            success=False,
            error=f"Connection failed: {e}",
        )
```

---

## Testing

### Unit Test Pattern

```python
import pytest
from monkeypatched_sdk import AdapterContext, AdapterResponse


class MockEventBus:
    async def publish(self, *args, **kwargs):
        self.last_event = (args, kwargs)


class MockTelemetry:
    async def emit_metric(self, *args, **kwargs):
        self.last_metric = (args, kwargs)

    async def start_span(self, name):
        class MockSpan:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def set_tag(self, k, v):
                pass

        return MockSpan()


@pytest.mark.asyncio
async def test_adapter_creation():
    adapter = MyAdapter("test_id", "test_capability")

    # Inject mocks
    adapter.event_bus = MockEventBus()
    adapter.telemetry = MockTelemetry()
    adapter.configuration = {"endpoint": "http://test"}

    # Mock logger
    adapter.log = lambda *a, **kw: None

    # Test initialization
    await adapter.initialize()

    # Test execution
    context = AdapterContext(
        capability_id="test",
        workflow_id="wf-123",
        execution_id="exec-456",
        world_state={},
        request_id="req-789",
    )

    response = await adapter.execute(context, {"key": "value"})

    assert isinstance(response, AdapterResponse)
    assert response.success is True
```

### Integration Test Pattern

```python
@pytest.mark.asyncio
async def test_full_lifecycle():
    from monkeypatched_sdk import LifecycleManager
    
    lifecycle = LifecycleManager()
    
    # Startup
    await lifecycle.startup()
    
    # Get adapter
    adapter = lifecycle.get_adapter("my_adapter")
    assert adapter is not None
    
    # Check health
    health = lifecycle.get_health_status()
    assert health["my_adapter"] is True
    
    # Shutdown
    await lifecycle.shutdown()
```

---

## Deployment

### Docker Example

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
```

### main.py

```python
import asyncio
import logging
from monkeypatched_sdk import LifecycleManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    lifecycle = LifecycleManager()

    try:
        logger.info("Starting SDK...")
        await lifecycle.startup()

        logger.info("SDK ready for requests")

        # Keep running
        while True:
            await asyncio.sleep(60)

    except KeyboardInterrupt:
        logger.info("Received shutdown signal")

    finally:
        await lifecycle.shutdown()
        logger.info("SDK stopped")


if __name__ == "__main__":
    asyncio.run(main())
```

### Environment Setup

```bash
# Copy config
cp config.yaml /etc/monkeypatched/sdk/config.yaml

# Set environment variables
export CMMS_ENDPOINT=https://cmms.company.com
export CMMS_API_KEY=secret_key
export SAP_CLIENT_ID=sap_client
export SAP_CLIENT_SECRET=sap_secret

# Run container
docker run -e CMMS_ENDPOINT -e CMMS_API_KEY -e SAP_CLIENT_ID -e SAP_CLIENT_SECRET my-adapter:latest
```

---

## Monitoring & Debugging

### Health Check Endpoint

```python
from fastapi import FastAPI

app = FastAPI()

lifecycle = LifecycleManager()


@app.get("/health")
async def health():
    return lifecycle.get_health_status()


@app.get("/metrics")
async def metrics():
    # Return Prometheus metrics
    pass
```

### Debug Logging

```bash
# Enable debug logging
export SDK_LOG_LEVEL=DEBUG

# Or in config.yaml
sdk:
  log_level: DEBUG
```

### Metrics Export

```yaml
# config.yaml
sdk:
  telemetry_exporter: prometheus
  prometheus_port: 9090
```

Then access metrics at `http://localhost:9090/metrics`

---

## Best Practices

### 1. Always Implement health_check()

```python
async def health_check(self) -> bool:
    try:
        # Quick connectivity test
        result = await self._test_connection()
        return result
    except:
        return False
```

### 2. Use Connection Pooling

```python
# ❌ DON'T: Create new connection each time
async with aiohttp.ClientSession() as session:
    async with session.get(...) as resp:
        pass

# ✅ DO: Use connection pool
conn = await self.connection_pool.acquire()
try:
    result = await conn.get(...)
finally:
    await self.connection_pool.release(conn)
```

### 3. Emit Events for Important Operations

```python
# After successful operation
await self.emit_event(
    "order_created",
    {"order_id": order_id, "timestamp": datetime.now().isoformat()},
)
```

### 4. Use Metrics for Performance

```python
# Track execution time
await self.emit_metric("api_call_duration_ms", duration)

# Track volume
await self.emit_metric("items_processed", count)

# Track errors
await self.emit_metric("errors", 1)
```

### 5. Validate Inputs Early

```python
async def execute(self, context, inputs) -> AdapterResponse:
    # Validate immediately
    if not inputs.get("id"):
        return AdapterResponse(
            success=False,
            error="Missing required field: id",
        )
```

### 6. Clean Up Resources

```python
async def shutdown(self):
    # Close connections
    if hasattr(self, "connection_pool"):
        await self.connection_pool.shutdown()

    # Flush pending operations
    # Close files
    # Release locks
```

---

## Troubleshooting

### Adapter Not Discovered

**Problem**: Adapter decorator not working

**Solution**:
1. Check `@capability_adapter` is applied
2. Check adapter class extends `CapabilityAdapter`
3. Check all four methods are implemented
4. Check no syntax errors

```python
# ✅ Correct
@capability_adapter(capability_id="my_capability")
class MyAdapter(CapabilityAdapter):
    async def initialize(self): ...
    async def execute(self, context, inputs): ...
    async def health_check(self): ...
    async def shutdown(self): ...
```

### Configuration Not Loaded

**Problem**: `configuration.get()` returns None

**Solution**:
1. Check `config.yaml` exists
2. Check adapter name matches config
3. Check YAML syntax is valid
4. Check environment variables are set

```bash
# Debug
export SDK_LOG_LEVEL=DEBUG
# Check logs for config loading
```

### Health Check Failing

**Problem**: Adapter shows as DEGRADED

**Solution**:
1. Check external system is reachable
2. Check network connectivity
3. Check credentials are valid
4. Check firewall rules

```python
async def health_check(self) -> bool:
    try:
        # Add logging for debugging
        self.log("debug", "Starting health check")
        result = await self._test_connection()
        self.log("debug", f"Health check result: {result}")
        return result
    except Exception as e:
        self.log("error", f"Health check failed: {e}")
        return False
```

---

## Next Steps

1. **Create your first adapter** - Pick one of the patterns above
2. **Configure it** - Create `config.yaml` with your settings
3. **Test it** - Write unit tests using the test pattern
4. **Deploy it** - Use Docker for production deployment
5. **Monitor it** - Check health and metrics endpoints

For more details, see:
- **ARCHITECTURE.md** - System architecture and design
- **SDK_DESIGN.md** - Detailed design specifications
- **SDK_API_REFERENCE.md** - Complete API documentation

---

## Support

If you hit issues:
1. Check troubleshooting section above
2. Enable debug logging: `SDK_LOG_LEVEL=DEBUG`
3. Check adapter logs
4. Review health check endpoint
5. Check metrics and traces

The SDK is designed to be simple and stable. If you need help, the error messages are detailed and actionable.

Happy integrating! 🚀
