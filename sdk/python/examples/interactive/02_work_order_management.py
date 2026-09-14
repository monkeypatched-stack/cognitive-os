#!/usr/bin/env python3
"""
Interactive Example 2: Work Order Management

Demonstrates a more complex adapter with database simulation,
input validation, and multi-step workflow.
"""

import asyncio
import json
from datetime import datetime
from monkeypatched_sdk import CapabilityAdapter, capability_adapter, AdapterResponse


# Simulated database
class WorkOrderDatabase:
    """Simulates a work order database"""

    def __init__(self):
        self.work_orders = {}
        self.next_id = 1

    async def create(self, work_order_data):
        """Create a new work order"""
        work_order_id = f"WO-{self.next_id:06d}"
        self.next_id += 1

        work_order = {
            "id": work_order_id,
            "status": "created",
            "data": work_order_data,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }

        self.work_orders[work_order_id] = work_order
        return work_order

    async def get(self, work_order_id):
        """Get a work order by ID"""
        return self.work_orders.get(work_order_id)

    async def update_status(self, work_order_id, new_status):
        """Update work order status"""
        if work_order_id not in self.work_orders:
            return None

        self.work_orders[work_order_id]["status"] = new_status
        self.work_orders[work_order_id]["updated_at"] = datetime.now().isoformat()
        return self.work_orders[work_order_id]

    async def list_all(self):
        """List all work orders"""
        return list(self.work_orders.values())

    async def ping(self):
        """Health check"""
        return True


@capability_adapter(
    capability_id="work_order_management",
    name="Work Order Management Adapter",
    description="Manages work orders with full CRUD operations",
    version="2.0.0",
)
class WorkOrderManagementAdapter(CapabilityAdapter):
    """Adapter for managing work orders"""

    def __init__(self, adapter_id, capability_id):
        super().__init__(adapter_id, capability_id)
        self.db = WorkOrderDatabase()

    async def initialize(self):
        """Initialize the adapter"""
        print("📋 Work Order Management Adapter initialized!")
        print("   Database: Connected")
        print("   Status: Ready to process work orders")
        return True

    async def execute(self, context, inputs):
        """Execute work order operations"""
        action = inputs.get("action", "list")
        print(f"📥 Processing action: {action}")

        try:
            if action == "create":
                return await self._create_work_order(inputs)
            elif action == "get":
                return await self._get_work_order(inputs)
            elif action == "update":
                return await self._update_work_order(inputs)
            elif action == "list":
                return await self._list_work_orders()
            else:
                return AdapterResponse.fail(f"Unknown action: {action}", "INVALID_ACTION")
        except Exception as e:
            return AdapterResponse.fail(f"Error processing request: {str(e)}", "PROCESSING_ERROR")

    async def _create_work_order(self, inputs):
        """Create a new work order"""
        work_order_data = inputs.get("work_order_data", {})

        # Validate required fields
        required_fields = ["batch_id", "product_id", "quantity"]
        for field in required_fields:
            if field not in work_order_data:
                return AdapterResponse.fail(f"Missing required field: {field}", "VALIDATION_ERROR")

        # Create work order
        work_order = await self.db.create(work_order_data)

        print(f"✅ Created work order: {work_order['id']}")

        return AdapterResponse.ok({"work_order": work_order, "message": "Work order created successfully"})

    async def _get_work_order(self, inputs):
        """Get a work order by ID"""
        work_order_id = inputs.get("work_order_id")

        if not work_order_id:
            return AdapterResponse.fail("work_order_id is required", "VALIDATION_ERROR")

        work_order = await self.db.get(work_order_id)

        if not work_order:
            return AdapterResponse.fail(f"Work order {work_order_id} not found", "NOT_FOUND")

        print(f"📄 Retrieved work order: {work_order_id}")

        return AdapterResponse.ok({"work_order": work_order})

    async def _update_work_order(self, inputs):
        """Update work order status"""
        work_order_id = inputs.get("work_order_id")
        new_status = inputs.get("status")

        if not work_order_id or not new_status:
            return AdapterResponse.fail("work_order_id and status are required", "VALIDATION_ERROR")

        # Validate status
        valid_statuses = [
            "created",
            "assigned",
            "in_progress",
            "completed",
            "cancelled",
        ]
        if new_status not in valid_statuses:
            return AdapterResponse.fail(
                f"Invalid status: {new_status}. Valid: {', '.join(valid_statuses)}",
                "VALIDATION_ERROR",
            )

        # Update status
        updated_work_order = await self.db.update_status(work_order_id, new_status)

        if not updated_work_order:
            return AdapterResponse.fail(f"Work order {work_order_id} not found", "NOT_FOUND")

        print(f"🔄 Updated work order {work_order_id} to {new_status}")

        return AdapterResponse.ok(
            {
                "work_order": updated_work_order,
                "message": f"Status updated to {new_status}",
            }
        )

    async def _list_work_orders(self):
        """List all work orders"""
        work_orders = await self.db.list_all()

        print(f"📋 Found {len(work_orders)} work orders")

        return AdapterResponse.ok({"work_orders": work_orders, "count": len(work_orders)})

    async def health_check(self):
        """Check adapter health"""
        db_health = await self.db.ping()
        work_order_count = len(await self.db.list_all())

        return {
            "status": "healthy",
            "database": db_health,
            "work_orders": work_order_count,
            "adapter": self.adapter_id,
        }

    async def shutdown(self):
        """Cleanup resources"""
        print("📋 Work Order Management Adapter shutting down...")
        print("   Database: Disconnected")
        return True


async def show_menu():
    """Display interactive menu"""
    print("\n" + "=" * 60)
    print("📋 Work Order Management Menu")
    print("=" * 60)
    print("1. Create new work order")
    print("2. Get work order details")
    print("3. Update work order status")
    print("4. List all work orders")
    print("5. Exit")
    print("-" * 60)


async def get_user_choice():
    """Get user menu choice"""
    while True:
        try:
            choice = input("Enter your choice (1-5): ").strip()
            if choice in ["1", "2", "3", "4", "5"]:
                return choice
            print("❌ Invalid choice. Please enter 1-5.")
        except Exception:
            print("❌ Invalid input. Please try again.")


async def get_work_order_data():
    """Get work order data from user"""
    print("\n📝 Enter work order details:")

    batch_id = input("Batch ID (e.g., BATCH-2024-001): ").strip() or "BATCH-2024-001"
    product_id = input("Product ID (e.g., PROD-ASPRN-500): ").strip() or "PROD-ASPRN-500"

    while True:
        try:
            quantity = input("Quantity: ").strip()
            quantity = int(quantity) if quantity else 1000
            if quantity > 0:
                break
            print("❌ Quantity must be positive.")
        except ValueError:
            print("❌ Please enter a valid number.")

    priority = input("Priority (high/medium/low): ").strip().lower() or "medium"
    if priority not in ["high", "medium", "low"]:
        priority = "medium"

    return {
        "batch_id": batch_id,
        "product_id": product_id,
        "quantity": quantity,
        "priority": priority,
        "notes": input("Notes (optional): ").strip(),
    }


async def main():
    """Interactive work order management demonstration"""
    print("=" * 60)
    print("🚀 Interactive Example 2: Work Order Management")
    print("=" * 60)

    # Create adapter instance
    adapter = WorkOrderManagementAdapter("work_order_mgmt_001", "work_order_management")

    # Initialize
    print("\n🔄 Initializing adapter...")
    await adapter.initialize()

    # Main loop
    while True:
        await show_menu()
        choice = await get_user_choice()

        if choice == "1":  # Create work order
            print("\n📝 Creating new work order...")
            work_order_data = await get_work_order_data()

            response = await adapter.execute(
                context=None,
                inputs={"action": "create", "work_order_data": work_order_data},
            )

            if response.status == "ok":
                wo = response.data["work_order"]
                print(f"\n✅ Work order created!")
                print(f"   ID: {wo['id']}")
                print(f"   Batch: {wo['data']['batch_id']}")
                print(f"   Product: {wo['data']['product_id']}")
                print(f"   Quantity: {wo['data']['quantity']}")
                print(f"   Status: {wo['status']}")
            else:
                print(f"\n❌ Error: {response.error}")

        elif choice == "2":  # Get work order
            work_order_id = input("\n🔍 Enter work order ID: ").strip()
            if not work_order_id:
                print("❌ Work order ID is required.")
                continue

            response = await adapter.execute(context=None, inputs={"action": "get", "work_order_id": work_order_id})

            if response.status == "ok":
                wo = response.data["work_order"]
                print(f"\n📄 Work Order Details:")
                print(f"   ID: {wo['id']}")
                print(f"   Status: {wo['status']}")
                print(f"   Created: {wo['created_at']}")
                print(f"   Updated: {wo['updated_at']}")
                print(f"   Batch: {wo['data']['batch_id']}")
                print(f"   Product: {wo['data']['product_id']}")
                print(f"   Quantity: {wo['data']['quantity']}")
                print(f"   Priority: {wo['data']['priority']}")
                if wo["data"].get("notes"):
                    print(f"   Notes: {wo['data']['notes']}")
            else:
                print(f"\n❌ Error: {response.error}")

        elif choice == "3":  # Update work order
            work_order_id = input("\n🔄 Enter work order ID to update: ").strip()
            if not work_order_id:
                print("❌ Work order ID is required.")
                continue

            print("Available statuses: created, assigned, in_progress, completed, cancelled")
            new_status = input("Enter new status: ").strip().lower()

            response = await adapter.execute(
                context=None,
                inputs={
                    "action": "update",
                    "work_order_id": work_order_id,
                    "status": new_status,
                },
            )

            if response.status == "ok":
                wo = response.data["work_order"]
                print(f"\n✅ Work order updated!")
                print(f"   ID: {wo['id']}")
                print(f"   New Status: {wo['status']}")
                print(f"   Message: {response.data['message']}")
            else:
                print(f"\n❌ Error: {response.error}")

        elif choice == "4":  # List work orders
            response = await adapter.execute(context=None, inputs={"action": "list"})

            if response.status == "ok":
                work_orders = response.data["work_orders"]
                count = response.data["count"]

                print(f"\n📋 Found {count} work orders:")
                if work_orders:
                    for i, wo in enumerate(work_orders, 1):
                        print(f"\n{i}. {wo['id']}")
                        print(f"   Status: {wo['status']}")
                        print(f"   Batch: {wo['data']['batch_id']}")
                        print(f"   Product: {wo['data']['product_id']}")
                        print(f"   Quantity: {wo['data']['quantity']}")
                else:
                    print("   No work orders found.")
            else:
                print(f"\n❌ Error: {response.error}")

        elif choice == "5":  # Exit
            print("\n👋 Exiting work order management...")

            # Health check before shutdown
            health = await adapter.health_check()
            print(f"\n📊 Final Health Check:")
            print(f"   Status: {health['status']}")
            print(f"   Work orders managed: {health['work_orders']}")

            # Shutdown
            await adapter.shutdown()
            print("\n✅ Adapter shutdown complete.")

            print("\n" + "=" * 60)
            print("🎉 Interactive Example 2 completed!")
            print("=" * 60)
            break


if __name__ == "__main__":
    asyncio.run(main())
