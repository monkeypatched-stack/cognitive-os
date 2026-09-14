# Monkeypatched Adapter SDK - Architecture

## Executive Summary

The Monkeypatched Adapter SDK is a **minimal, production-ready framework** for connecting external systems to the platform's capabilities. It provides a stable contract-based interface that enables clean separation between the platform and external integrations.

**Core Principle**: Write one class, implement four methods, get everything else for free.

---

## System Context

### The Integration Problem

The Monkeypatched platform needs to connect to diverse external systems:
- **Enterprise**: SAP, Oracle, Dynamics, DataBricks, QMS, CMMS, WMS, ERP, MES
- **Industrial**: PLC, OPC-UA, MQTT, SCADA
- **Data**: Postgres, Mongo, Neo4j, Elasticsearch, REST APIs, GraphQL
- **Sensors**: Temperature, vibration, pressure, custom

Without the SDK, each integration would require:
- Understanding platform internals
- Duplicating connection management code
- Reimplementing auth strategies
- Building observability from scratch

### The SDK Solution

```
External System
     ↓
  Adapter (you write this)
     ↓
  SDK (handles everything else)
     ↓
  Platform Capabilities
```

---

## Architecture Overview

### Three-Layer Design

```
┌─────────────────────────────────────────────┐
│        PLATFORM LAYER (Unchanged)           │
├─────────────────────────────────────────────┤
│ Goal Builder → Planner → World Model       │
│ ↓ Critic → Workflow Memory → Runtime       │
└────────┬──────────────────────────────────┘
         │ (Capabilities)
         │
         ▼
┌─────────────────────────────────────────────┐
│          SDK LAYER (Infrastructure)         │
├─────────────────────────────────────────────┤
│                                             │
│  ┌──────────────────────────────────┐     │
│  │ CapabilityAdapter Contract       │     │
│  │ (You implement this)             │     │
│  └──────────────────────────────────┘     │
│         ↓ (Injected by SDK)                │
│  ┌──────────────────────────────────┐     │
│  │ Lifecycle Manager                │     │
│  │ Configuration Manager            │     │
│  │ Event Bus Adapter                │     │
│  │ Telemetry Manager                │     │
│  │ Auth Handlers                    │     │
│  │ Connection Pools                 │     │
│  └──────────────────────────────────┘     │
│                                             │
└────────┬──────────────────────────────────┘
         │ (Adapters)
         │
         ▼
┌─────────────────────────────────────────────┐
│      EXTERNAL SYSTEMS LAYER                 │
├─────────────────────────────────────────────┤
│ Your Company's Systems                      │
│ (CMMS, SAP, PLC, MQTT Broker, etc.)        │
└─────────────────────────────────────────────┘
```

---

## Core Abstraction: CapabilityAdapter

The entire SDK is built around a single contract:

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from datetime import datetime


class AdapterStatus(Enum):
    """Adapter lifecycle status."""

    INITIALIZED = "initialized"
    READY = "ready"
    EXECUTING = "executing"
    IDLE = "idle"
    DEGRADED = "degraded"
    FAILED = "failed"
    SHUTDOWN = "shutdown"


class AdapterContext:
    """Context passed to adapter.execute()"""

    capability_id: str  # Which capability is being executed
    workflow_id: str  # Which workflow is running
    execution_id: str  # Unique execution instance
    world_state: Dict[str, Any]  # Current world model state
    request_id: str  # For tracing


class AdapterResponse:
    """Standard response from adapter"""

    success: bool  # Did it work?
    result: Optional[Dict]  # The actual result
    error: Optional[str]  # Error message if failed
    metadata: Dict[str, Any]  # Extra info


class CapabilityAdapter(ABC):
    """Base class - all adapters implement this."""

    @abstractmethod
    async def initialize(self) -> None:
        """Called once at SDK startup.

        Use to:
        - Validate configuration
        - Establish connections
        - Verify external system availability
        - Initialize internal state

        Raise AdapterInitializationError if it fails.
        """
        pass

    @abstractmethod
    async def execute(
        self,
        context: AdapterContext,
        inputs: Dict[str, Any],
    ) -> AdapterResponse:
        """Execute the external capability.

        This is where the actual work happens.
        SDK handles logging, tracing, and telemetry wrapping.

        Args:
            context: Workflow execution context
            inputs: Capability inputs

        Returns:
            AdapterResponse with result or error
        """
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if adapter and external system are healthy.

        Called periodically by SDK (every 30 seconds).
        Use to detect connection issues early.

        Returns:
            True if healthy, False otherwise
        """
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        """Called at SDK shutdown.

        Use to:
        - Close connections
        - Flush pending operations
        - Clean up resources
        """
        pass

    # SDK-Provided Utilities (don't override these)

    async def emit_event(self, event_type: str, data: Dict) -> None:
        """Publish event to platform event bus."""

    async def emit_metric(self, metric_name: str, value: float, tags: Dict = None) -> None:
        """Emit telemetry metric."""

    def log(self, level: str, message: str, **kwargs) -> None:
        """Log message."""
```

**That's it.** Implement these four methods, and you have a fully-integrated adapter.

---

## Adapter Lifecycle

### Startup Sequence

```
1. SDK Initialization
   ↓
2. Load configuration.yaml
   ↓
3. Discover @capability_adapter decorated classes
   ↓
4. For each registered adapter:
   a. Create instance
   b. Inject SDK services:
      - logger
      - event_bus
      - telemetry
      - configuration
   c. Call adapter.initialize()
   d. Set status to READY
   e. Emit ADAPTER_READY event
   ↓
5. Start health check loop (every 30 seconds)
   ↓
6. System READY
   Accept adapter.execute() calls from platform
```

### Execution Sequence

```
Platform Workflow Step
     ↓ (requests capability)
SDK adapter.execute(context, inputs)
     ↓
SDK Telemetry Wrapper:
  - Start trace span
  - Log execution start
  - Record start time
     ↓
Your Adapter.execute():
  - Validate inputs
  - Acquire connection from pool
  - Call external system
  - Handle errors
  - Return AdapterResponse
     ↓
SDK Post-Execution:
  - Record execution time
  - Release connection to pool
  - End trace span
  - Emit metrics
  - Log results
     ↓
Return AdapterResponse to Platform
```

### Shutdown Sequence

```
Shutdown Signal
     ↓
1. Stop health check loop
   ↓
2. For each adapter:
   a. Call adapter.shutdown()
   b. Close connections
   c. Flush pending operations
   d. Emit ADAPTER_SHUTDOWN event
   ↓
3. System Stopped
```

---

## SDK Components

### 1. Lifecycle Manager

**Responsibility**: Manage startup, health checks, and shutdown.

```python
class LifecycleManager:
    async def startup(self):
        """Initialize all adapters"""
    
    async def shutdown(self):
        """Shutdown all adapters"""
    
    async def health_check(self):
        """Check adapter health (called every 30s)"""
    
    def get_adapter(self, adapter_id: str) -> CapabilityAdapter:
        """Get adapter by ID"""
    
    def get_health_status(self) -> Dict[str, bool]:
        """Get health status of all adapters"""
```

### 2. Configuration Manager

**Responsibility**: Load and manage adapter configuration.

**Features**:
- YAML configuration files
- Environment variable overrides (e.g., `ADAPTER_CMMS_ENDPOINT`)
- Secret injection
- Runtime reload support
- Per-adapter configuration

```yaml
# config.yaml
sdk:
  log_level: INFO
  health_check_interval: 30

adapters:
  cmms:
    enabled: true
    endpoint: ${CMMS_ENDPOINT}  # From environment
    credentials:
      api_key: ${CMMS_API_KEY}
    timeout: 45
    extra:
      work_order_prefix: WO
```

### 3. Event Bus Adapter

**Responsibility**: Integrate with platform event system.

```python
# In your adapter
await self.emit_event(
    event_type="work_order_created",
    data={
        "work_order_id": "WO-123",
        "equipment_id": "EQ-456",
    },
)
```

The SDK publishes to the platform event bus. Adapters can also subscribe to platform events.

### 4. Telemetry Manager

**Responsibility**: Collect metrics and traces.

**Supported Exporters**:
- Prometheus (push/pull)
- InfluxDB (HTTP write)
- Jaeger (distributed tracing)
- Sentry (error tracking)

```python
# Emit metric
await self.emit_metric(metric_name="work_orders_created", value=1, tags={"equipment": "eq-001"})

# Trace execution
async with await self.telemetry.start_span("cmms_api_call") as span:
    span.set_tag("endpoint", endpoint)
    # Do work
```

### 5. Authentication Helpers

**Responsibility**: Handle auth so you don't have to.

**Supported Strategies**:
- **OAuth2**: Get tokens, handle refresh
- **API Key**: Inject X-API-Key header
- **JWT**: Generate signed tokens
- **Basic Auth**: Base64 encoding
- **Custom**: Implement your own

```python
# OAuth2 example
auth = OAuth2Handler(
    token_endpoint="https://api.example.com/oauth/token",
    client_id="client_id",
    client_secret="client_secret",
)

headers = await auth.authenticate()  # Returns auth headers
```

### 6. Connection Pools

**Responsibility**: Manage pooled connections to external systems.

**Supported Types**:
- **HTTP**: aiohttp sessions
- **MQTT**: Paho MQTT clients
- **OPC-UA**: AsyncUA connections
- **Database**: SQLAlchemy async connections
- **GraphQL**: Custom HTTP-based

```python
# Acquire from pool
conn = await self.connection_pool.acquire()
try:
    # Use connection
    result = await conn.get_work_order(123)
finally:
    # Always release
    await self.connection_pool.release(conn)
```

### 7. Health Monitor

**Responsibility**: Track component health.

**Health Statuses**:
- **HEALTHY**: All checks passing
- **DEGRADED**: Some checks failing
- **UNHEALTHY**: System down

```python
# Platform can query health status
health = sdk.health_monitor.get_system_health()
# Returns: {"status": "healthy", "components": {...}}
```

---

## Dependency Graph

### What SDK Can Depend On

```
✅ Platform Contracts
   - EventBus interface
   - Capability interface
   - Workflow context

✅ Standard Libraries
   - asyncio
   - logging
   - json
   - yaml
   - dataclasses
   - typing

✅ Third-Party Libraries
   - aiohttp (HTTP)
   - paho-mqtt (MQTT)
   - asyncua (OPC-UA)
   - sqlalchemy (Database)
   - others as needed
```

### What SDK CANNOT Depend On

```
❌ Planner
❌ World Model
❌ Critic
❌ Workflow Memory
❌ Workflow Reuse
❌ Execution DAG
❌ Runtime
❌ Goal Builder

These are stable platform components.
SDK doesn't need them (only adapter contracts).
```

---

## Implementation Details

### Adapter Registration

Adapters are discovered via the `@capability_adapter` decorator:

```python
@capability_adapter(
    capability_id="create_work_order",
    version="1.0.0",
    tags=["cmms", "maintenance"],
)
class CMMSAdapter(CapabilityAdapter): ...
```

The decorator:
1. Validates the class implements the contract
2. Attaches metadata
3. Auto-registers in the registry (no manual edits needed)

### Dependency Injection

The SDK injects services into each adapter:

```python
class MyAdapter(CapabilityAdapter):
    def __init__(self, adapter_id: str, capability_id: str):
        super().__init__(adapter_id, capability_id)
        # These are injected by SDK after creation:
        self.logger = None  # Set by LifecycleManager
        self.event_bus = None  # Set by LifecycleManager
        self.telemetry = None  # Set by LifecycleManager
        self.configuration = None  # Set by LifecycleManager
```

### Error Handling

All SDK components raise standard exceptions:

```python
from monkeypatched_sdk import (
    AdapterInitializationError,  # Init failed
    AdapterExecutionError,  # Execute failed
    HealthCheckError,  # Health check failed
    ConfigurationError,  # Config invalid
)
```

---

## Configuration

### YAML Structure

```yaml
sdk:
  log_level: INFO
  health_check_interval: 30
  event_batch_size: 100
  telemetry_exporter: prometheus

adapters:
  cmms:
    enabled: true
    endpoint: https://cmms.example.com/api
    credentials:
      api_key: ${CMMS_API_KEY}
      username: ${CMMS_USER}
    timeout: 45
    retry_count: 3
    extra:
      work_order_prefix: WO
      company_id: "1000"

  sap:
    enabled: true
    endpoint: https://sap.example.com/api
    credentials:
      oauth_endpoint: https://sap.example.com/oauth
      client_id: ${SAP_CLIENT_ID}
      client_secret: ${SAP_CLIENT_SECRET}
    timeout: 60
    extra:
      company_code: "1000"

  opc_ua:
    enabled: true
    endpoint: opc.tcp://plc.example.com:4840
    credentials:
      username: ${OPC_USER}
      password: ${OPC_PASS}
    timeout: 30
```

### Environment Variables

Override any config value with environment variables:

```bash
# Set from shell
export ADAPTER_CMMS_ENDPOINT=https://new-endpoint.com
export ADAPTER_CMMS_TIMEOUT=60
export ADAPTER_SAP_CLIENT_ID=new-client-id
```

---

## Metrics & Observability

### Built-in Metrics

The SDK automatically collects:
- **adapter.execution_time** - How long execute() took
- **adapter.success_count** - Successful executions
- **adapter.error_count** - Failed executions
- **adapter.health_check_duration** - Health check time

### Custom Metrics

Adapters can emit custom metrics:

```python
# In execute()
await self.emit_metric(metric_name="work_orders_created", value=1, tags={"equipment": "eq-001", "type": "maintenance"})
```

### Distributed Tracing

Adapters can trace execution:

```python
async with await self.telemetry.start_span("external_api_call") as span:
    span.set_tag("endpoint", "https://api.example.com")
    span.set_tag("method", "POST")
    # Do work
    # Span automatically ends and sends to Jaeger
```

### Health Monitoring

Health status is automatically tracked:

```python
# Platform can query
status = lifecycle_manager.get_health_status()
# Returns: {"adapter-1": True, "adapter-2": False, ...}
```

---

## Size & Complexity

### Code Statistics

| Component | LOC | Complexity |
|-----------|-----|-----------|
| Contracts | 150 | Low |
| Lifecycle | 400 | Medium |
| Configuration | 250 | Low |
| Authentication | 350 | Medium |
| Events | 200 | Low |
| Telemetry | 400 | High |
| Connections | 500 | High |
| Decorators | 150 | Low |
| Exceptions | 100 | Low |
| **Total** | **2,500** | **Moderate** |

### Principles

✅ **Single Responsibility** - Each module does one thing
✅ **Clear Interfaces** - No hidden magic
✅ **Minimal Dependencies** - Uses only what's needed
✅ **No Framework Bloat** - No routing, ORM, templating, etc.
✅ **Production Ready** - Comprehensive error handling

---

## Platform Integration Points

### Capability Registry

The platform's capability registry:
1. Discovers adapters via `@capability_adapter`
2. Gets adapter metadata
3. Calls `adapter.execute()` for workflow steps
4. Receives `AdapterResponse` with results

### Event Bus

Platform events flow through the SDK:
- Adapters publish events via `emit_event()`
- Adapters subscribe to platform events via `event_bus.subscribe()`
- Events include workflow context and execution metadata

### World State Access

Adapters receive world state in `AdapterContext`:
```python
async def execute(self, context, inputs):
    # Access current world state
    current_state = context.world_state
    # Use to inform decisions
```

---

## Design Decisions

### Why Contract-Based?

Each adapter implements one contract (`CapabilityAdapter`). This:
- Makes adapters **interchangeable**
- Enables **auto-discovery** via decorators
- Allows **testing** without platform
- Supports **multiple languages** (Go, Rust, Java, TypeScript)

### Why Decorator-Based Registration?

```python
@capability_adapter(capability_id="create_work_order")
class CMMSAdapter(CapabilityAdapter): ...
```

This:
- **Eliminates manual registry** - No config files to edit
- **Keeps metadata** with code - Single source of truth
- **Enables validation** - Decorator checks contract
- **Allows versioning** - Multiple versions can coexist

### Why Dependency Injection?

Services are injected, not imported:
```python
# ❌ DON'T: Import from platform
from platform.events import event_bus

# ✅ DO: Receive via injection
# SDK sets self.event_bus during initialization
```

This:
- **Decouples** adapter from platform
- **Enables testing** - Mock injected services
- **Allows substitution** - Different implementations

---

## Portability

The SDK design is **language-agnostic**:

| Language | Async Model | Status |
|----------|-------------|--------|
| Python | asyncio | Reference Implementation |
| Go | goroutines/channels | Ready for Implementation |
| Rust | tokio | Ready for Implementation |
| Java | CompletableFuture | Ready for Implementation |
| TypeScript | async/await | Ready for Implementation |

All use the same:
- **Contract** - CapabilityAdapter interface
- **Lifecycle** - Initialize → Execute → Health Check → Shutdown
- **Configuration** - YAML + environment variables
- **Events** - Publish/subscribe pattern
- **Telemetry** - Metrics and traces

---

## Summary

The SDK provides:

✅ **Stable Contract** - Four methods to implement
✅ **Full Lifecycle Management** - Startup to shutdown
✅ **Flexible Configuration** - YAML + environment overrides
✅ **Multiple Auth Strategies** - OAuth2, API Key, JWT, etc.
✅ **Connection Pooling** - HTTP, MQTT, OPC-UA, Database
✅ **Event Integration** - Pub/sub with platform
✅ **Telemetry** - Metrics, traces, health monitoring
✅ **Production Ready** - Error handling, logging, observability

**The result**: Connect any external system with minimal code, maximum stability, and full observability.
