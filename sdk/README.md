# Monkeypatched Adapter SDK

A minimal, production-ready framework for connecting external systems to the Monkeypatched platform.

## Overview

The SDK transforms integration complexity:

```
WITHOUT SDK: Understand platform → Duplicate infrastructure → Build adapters
WITH SDK: Implement CapabilityAdapter → Done (everything else is provided)
```

**Core Principle**: Write one class, implement four methods, get everything else for free.

---

## Key Documents

### 1. **[ARCHITECTURE.md](ARCHITECTURE.md)** - System Design & Architecture
- **Purpose**: Understand how the SDK works
- **Contents**:
  - System context and problem statement
  - Three-layer architecture (Platform / SDK / External Systems)
  - Core CapabilityAdapter contract
  - Adapter lifecycle (startup → execution → shutdown)
  - All 7 SDK components explained
  - Design decisions and rationale
  - Portability across languages (Python, Go, Rust, Java, TypeScript)

**Read this first to understand the system holistically.**

### 2. **[DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md)** - Practical Implementation Guide
- **Purpose**: Build adapters quickly
- **Contents**:
  - Quick start (5 minutes to first adapter)
  - 5 complete pattern examples (HTTP, OAuth2, Database, Metrics, Events)
  - Configuration guide (YAML + environment variables)
  - Testing patterns (unit & integration)
  - Docker deployment
  - Logging, error handling, monitoring
  - Troubleshooting guide

**Read this when you're implementing your first adapter.**

### 3. **[SDK_DESIGN.md](../SDK_DESIGN.md)** - Detailed Design Specifications
- **Purpose**: Reference for SDK developers
- **Contents**:
  - Complete component specifications
  - Interface definitions
  - Configuration framework details
  - Authentication strategies
  - Connection pooling patterns
  - Telemetry collection
  - Health monitoring

**Read this if you're implementing the SDK or need detailed specs.**

### 4. **[SDK_API_REFERENCE.md](../SDK_API_REFERENCE.md)** - API Reference & Examples
- **Purpose**: API documentation and architecture diagrams
- **Contents**:
  - Public API surface
  - Dependency rules
  - 4 detailed architecture diagrams
  - 2 complete adapter implementations (CMMS, OPC-UA)
  - Testing patterns
  - SDK size & complexity analysis

**Read this for API details and complete code examples.**

---

## Quick Start (5 Minutes)

### 1. Create Your Adapter

```python
from monkeypatched_sdk import (
    CapabilityAdapter,
    AdapterContext,
    AdapterResponse,
    capability_adapter,
)


@capability_adapter(capability_id="create_work_order")
class MyAdapter(CapabilityAdapter):
    async def initialize(self):
        # Setup: validate config, test connection
        pass

    async def execute(self, context: AdapterContext, inputs: dict) -> AdapterResponse:
        # Do the actual work
        # Return AdapterResponse with result or error
        pass

    async def health_check(self) -> bool:
        # Check if external system is healthy
        pass

    async def shutdown(self):
        # Cleanup: close connections, flush operations
        pass
```

### 2. Create Config

```yaml
# config.yaml
sdk:
  log_level: INFO
  health_check_interval: 30

adapters:
  my_adapter:
    enabled: true
    endpoint: https://api.example.com
    credentials:
      api_key: ${MY_API_KEY}
```

### 3. Run It

```python
from monkeypatched_sdk import LifecycleManager

lifecycle = LifecycleManager()
await lifecycle.startup()

# Your adapters are now running
# Platform can call them via capability registry
```

---

## What the SDK Provides

### ✅ You Write (4 Methods)
- `initialize()` - Setup and validation
- `execute()` - The actual business logic
- `health_check()` - Connectivity testing
- `shutdown()` - Cleanup

### ✅ SDK Provides (Everything Else)
- **Lifecycle Management** - Startup/health/shutdown orchestration
- **Configuration** - YAML + environment variable override
- **Dependency Injection** - Logger, event bus, telemetry, configuration
- **Connection Pooling** - HTTP, MQTT, OPC-UA, Database
- **Authentication** - OAuth2, API Key, JWT, Basic Auth
- **Event Publishing** - Integration with platform event bus
- **Telemetry** - Metrics, distributed tracing, health monitoring
- **Error Handling** - Standard exceptions and error patterns
- **Logging** - Structured, contextual logging

---

## SDK Components at a Glance

| Component | Purpose | Status |
|-----------|---------|--------|
| **LifecycleManager** | Startup/health/shutdown | ✅ |
| **ConfigurationManager** | YAML + env overrides | ✅ |
| **EventBusAdapter** | Pub/sub with platform | ✅ |
| **TelemetryManager** | Metrics & traces | ✅ |
| **AuthHandlers** | OAuth2, API Key, JWT, Basic | ✅ |
| **ConnectionPools** | HTTP, MQTT, OPC-UA, Database | ✅ |
| **HealthMonitor** | Component health tracking | ✅ |

---

## Supported Integrations

### Enterprise Systems
- SAP, Oracle, Dynamics
- DataBricks, Snowflake
- QMS, CMMS, WMS, ERP, MES

### Industrial Systems
- PLC, OPC-UA, MQTT
- SCADA, Historian

### Data Systems
- PostgreSQL, MongoDB, Neo4j
- Elasticsearch, InfluxDB
- REST APIs, GraphQL

### Sensors & IoT
- Temperature, vibration, pressure
- Custom sensors via MQTT/OPC-UA

---

## Why Use the SDK?

### For Platform Teams
✅ Connect to any external system without platform changes
✅ No need to understand external system internals
✅ Clear ownership boundaries with adapter teams
✅ Built-in observability and health monitoring

### For Integration Teams
✅ Simple contract - just 4 methods
✅ No need to understand platform internals
✅ Reusable connection, auth, telemetry helpers
✅ Production-ready error handling and logging

### For Operations
✅ Centralized configuration (YAML)
✅ Health monitoring (ready/degraded/failed)
✅ Full observability (metrics, traces, logs)
✅ Easy to deploy (Docker-ready)
✅ Easy to scale (connection pooling, async)

---

## Design Principles

✅ **Minimal** - 2,500 LOC core (no framework bloat)
✅ **Stable** - Contract-based design
✅ **Portable** - Language-agnostic (Python, Go, Rust, Java, TypeScript)
✅ **Observable** - Built-in telemetry and health monitoring
✅ **Extensible** - Plugin architecture for custom auth, exporters, connections
✅ **Production-Ready** - Comprehensive error handling and logging

---

## Implementation Status

| Language | Status | Notes |
|----------|--------|-------|
| **Python** | 🟢 Ready | Reference implementation complete |
| **Go** | 🟡 Design Complete | Ready for implementation |
| **Rust** | 🟡 Design Complete | Ready for implementation |
| **Java** | 🟡 Design Complete | Ready for implementation |
| **TypeScript** | 🟡 Design Complete | Ready for implementation |

All implementations use the same contract and lifecycle.

---

## Architecture at a Glance

```
┌─────────────────────────────────┐
│   MONKEYPATCHED PLATFORM        │
│ (Planner, World Model, Runtime) │
└────────────┬────────────────────┘
             │ (Capabilities)
             │
┌────────────▼────────────────────┐
│      ADAPTER SDK                │
├─────────────────────────────────┤
│ • LifecycleManager              │
│ • ConfigurationManager          │
│ • EventBusAdapter               │
│ • TelemetryManager              │
│ • AuthHandlers                  │
│ • ConnectionPools               │
└────────────┬────────────────────┘
             │ (Your Adapters)
             │
┌────────────▼────────────────────┐
│  EXTERNAL SYSTEMS               │
│ (CMMS, SAP, PLC, Sensors, etc.) │
└─────────────────────────────────┘
```

---

## File Structure

```
sdk/
├── README.md                      # This file
├── ARCHITECTURE.md                # System design and architecture
├── DEVELOPER_GUIDE.md             # How to build adapters
├── SDK_DESIGN.md                  # Detailed specifications
├── SDK_API_REFERENCE.md           # API reference and diagrams
└── python/
    └── monkeypatched_sdk/         # Python SDK implementation
        ├── __init__.py            # Main exports
        ├── contracts/             # CapabilityAdapter contract
        ├── lifecycle/             # Lifecycle management
        ├── configuration/         # Configuration management
        ├── authentication/        # Auth handlers
        ├── events/                # Event bus integration
        ├── telemetry/             # Metrics and traces
        ├── connections/           # Connection pools
        ├── health/                # Health monitoring
        └── exceptions/            # Error classes
```

---

## Getting Started

### Step 1: Choose Your Integration Pattern
- **HTTP REST API** → Use HTTPConnectionPool + OAuth2Handler
- **Database** → Use DatabaseConnectionPool
- **MQTT/IoT** → Use MQTTConnectionPool
- **OPC-UA Industrial** → Use OPCUAConnectionPool
- **Custom** → Implement your own with ConnectionPool base

### Step 2: Follow the Pattern
1. Create adapter class extending CapabilityAdapter
2. Implement 4 required methods
3. Create config.yaml
4. Run LifecycleManager.startup()

### Step 3: Deploy
1. Docker container (examples provided)
2. Set environment variables for secrets
3. Point platform to your SDK
4. Done!

---

## Common Patterns

### Pattern 1: HTTP API (Most Common)
```python
@capability_adapter(capability_id="create_work_order")
class HTTPAdapter(CapabilityAdapter):
    # Uses HTTPConnectionPool for connection management
    # Uses OAuth2Handler for authentication
    # Emits events and metrics
```

See [DEVELOPER_GUIDE.md#pattern-1-http-based-integration](DEVELOPER_GUIDE.md#pattern-1-http-based-integration)

### Pattern 2: Database Query
```python
@capability_adapter(capability_id="query_data")
class DatabaseAdapter(CapabilityAdapter):
    # Uses DatabaseConnectionPool for connection pooling
    # Handles SQL query execution
```

See [DEVELOPER_GUIDE.md#pattern-3-connection-pooling-database](DEVELOPER_GUIDE.md#pattern-3-connection-pooling-database)

### Pattern 3: OAuth2 Integration
```python
@capability_adapter(capability_id="read_data")
class OAuth2Adapter(CapabilityAdapter):
    # Uses OAuth2Handler for token management
    # Handles token refresh automatically
```

See [DEVELOPER_GUIDE.md#pattern-2-oauth2-authentication](DEVELOPER_GUIDE.md#pattern-2-oauth2-authentication)

### Pattern 4: Event Publishing
```python
@capability_adapter(capability_id="create_order")
class EventAdapter(CapabilityAdapter):
    # Emits platform events on success/failure
    # Enables event-driven workflows
```

See [DEVELOPER_GUIDE.md#pattern-5-event-publishing](DEVELOPER_GUIDE.md#pattern-5-event-publishing)

### Pattern 5: Metrics & Tracing
```python
@capability_adapter(capability_id="process_data")
class MetricsAdapter(CapabilityAdapter):
    # Emits custom metrics
    # Traces execution for debugging
```

See [DEVELOPER_GUIDE.md#pattern-4-metrics-and-tracing](DEVELOPER_GUIDE.md#pattern-4-metrics-and-tracing)

---

## Configuration

### YAML Structure
```yaml
sdk:
  log_level: INFO
  health_check_interval: 30
  telemetry_exporter: prometheus

adapters:
  my_adapter:
    enabled: true
    endpoint: ${ENDPOINT}        # From environment
    credentials:
      api_key: ${API_KEY}
    timeout: 45
    extra:
      custom_setting: value
```

### Environment Overrides
```bash
export ADAPTER_MY_ADAPTER_ENDPOINT=https://new-endpoint.com
export ADAPTER_MY_ADAPTER_TIMEOUT=60
export SDK_LOG_LEVEL=DEBUG
```

See [DEVELOPER_GUIDE.md#configuration-guide](DEVELOPER_GUIDE.md#configuration-guide)

---

## Testing & Deployment

### Unit Testing
```python
# Mock the SDK services, test your adapter logic
# See DEVELOPER_GUIDE.md#testing
```

### Docker Deployment
```dockerfile
FROM python:3.11-slim
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "main.py"]
```

See [DEVELOPER_GUIDE.md#deployment](DEVELOPER_GUIDE.md#deployment)

---

## Support & Documentation

| Need | Go To |
|------|-------|
| Understand system design | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Build your first adapter | [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) |
| Look up an API | [SDK_API_REFERENCE.md](../SDK_API_REFERENCE.md) |
| Deep dive into specs | [SDK_DESIGN.md](../SDK_DESIGN.md) |
| Troubleshoot issues | [DEVELOPER_GUIDE.md#troubleshooting](DEVELOPER_GUIDE.md#troubleshooting) |

---

## Summary

The Monkeypatched Adapter SDK provides:

✅ **Stable Contract** - Simple, well-defined interface
✅ **Full Lifecycle** - Startup to shutdown automation
✅ **Infrastructure** - Logging, config, auth, telemetry, connections
✅ **Observability** - Metrics, traces, health monitoring
✅ **Production-Ready** - Error handling, resilience, scalability

**Result**: Connect any external system with ~50 lines of code.

Start with [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) and pick a pattern. You'll have a working adapter in 5 minutes.

---

**Latest Update**: June 11, 2026  
**Status**: Production Ready  
**Version**: 1.0.0
