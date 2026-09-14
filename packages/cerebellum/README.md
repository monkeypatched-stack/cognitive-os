# MonkeyBrain Cerebellum

Capability framework for the MonkeyBrain Cognitive Operating System.

## Features

- **Capability** base class for execution primitives
- **CapabilityRegistry** for discovery and management
- **Peripheral** abstraction for I/O
- **Lifecycle** management
- **FallbackEngine** for graceful degradation
- **SecureKeystore** for API keys
- **24 capability categories** across AI, databases, enterprise, and more

## Installation

```bash
pip install monkeybrain-cerebellum

# With optional dependencies
pip install monkeybrain-cerebellum[ai]       # AI providers
pip install monkeybrain-cerebellum[databases] # Database drivers
pip install monkeybrain-cerebellum[enterprise] # Elasticsearch
pip install monkeybrain-cerebellum[all]       # Everything
```

## Quick Start

```python
from cerebellum import Capability, CapabilityRegistry


class MyCapability(Capability):
    name = "my-capability"

    async def execute(self, state, inputs):
        return {"result": "done"}


registry = CapabilityRegistry()
registry.register(MyCapability())
```

## License

Proprietary
