#!/usr/bin/env python3
"""
Interactive Example 1: Simple Adapter

Demonstrates the most basic SDK adapter with interactive input/output.
Run this example to see how a simple adapter works in real-time.
"""

import asyncio
from monkeypatched_sdk import CapabilityAdapter, capability_adapter, AdapterResponse


@capability_adapter(
    capability_id="simple_greeting",
    name="Simple Greeting Adapter",
    description="Demonstrates basic adapter functionality with user interaction",
    version="1.0.0",
)
class SimpleGreetingAdapter(CapabilityAdapter):
    """A simple adapter that greets users and demonstrates basic I/O"""

    async def initialize(self):
        """Initialize the adapter"""
        print("👋 Simple Greeting Adapter initialized!")
        return True

    async def execute(self, context, inputs):
        """Execute the greeting logic"""
        print(f"📥 Received inputs: {inputs}")

        # Get user name from inputs
        name = inputs.get("name", "Guest")

        # Create personalized greeting
        greeting = f"Hello, {name}! Welcome to the Monkeypatched SDK."

        # Return response
        return AdapterResponse.ok({"message": greeting, "timestamp": "2024-06-13T14:45:00Z", "user": name})

    async def health_check(self):
        """Check adapter health"""
        print("⚕️  Health check: All systems operational")
        return {"status": "healthy", "adapters": 1}

    async def shutdown(self):
        """Cleanup resources"""
        print("👋 Simple Greeting Adapter shutting down...")
        return True


async def main():
    """Interactive demonstration of the simple adapter"""
    print("=" * 60)
    print("🚀 Interactive Example 1: Simple Adapter")
    print("=" * 60)

    # Create adapter instance
    adapter = SimpleGreetingAdapter("simple_greeting_001", "simple_greeting")

    # Initialize
    print("\n1️⃣ Initializing adapter...")
    init_result = await adapter.initialize()
    print(f"✅ Initialization result: {init_result}")

    # Get user input
    print("\n2️⃣ Enter your name:")
    name = input("Name: ").strip() or "Guest"

    # Execute with user input
    print(f"\n3️⃣ Executing adapter with name: '{name}'")
    response = await adapter.execute(context=None, inputs={"name": name, "source": "interactive_example"})

    print(f"\n4️⃣ Response received:")
    print(f"   Status: {response.status}")
    print(f"   Message: {response.data['message']}")
    print(f"   User: {response.data['user']}")
    print(f"   Timestamp: {response.data['timestamp']}")

    # Health check
    print(f"\n5️⃣ Performing health check...")
    health = await adapter.health_check()
    print(f"   Health status: {health['status']}")
    print(f"   Adapters running: {health['adapters']}")

    # Shutdown
    print(f"\n6️⃣ Shutting down adapter...")
    shutdown_result = await adapter.shutdown()
    print(f"✅ Shutdown result: {shutdown_result}")

    print("\n" + "=" * 60)
    print("🎉 Example completed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
