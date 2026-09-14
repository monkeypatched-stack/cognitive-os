# MonkeyBrain SDK

Python SDK for the MonkeyBrain Cognitive Operating System.

## Installation

```bash
pip install monkeypatched-sdk
```

## Quick Start

```python
from monkeypatched_sdk import MonkeyBrainClient

client = MonkeyBrainClient(base_url="http://localhost:8032")
result = await client.query("Show me batch records")
print(result)
```

## API Reference

### MonkeyBrainClient

```python
client = MonkeyBrainClient(base_url="http://localhost:8032", api_key="your-api-key")

# Query
result = await client.query("question")

# Health check
health = await client.health()

# Get state
state = await client.get_state()
```
