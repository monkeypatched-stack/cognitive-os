"""Ecommerce agents — Order, Cart, Checkout, Payment, Product, Customer, Pricing, Promotion, Inventory, Warehouse, Shipping, Fulfillment, Returns, Refund, Supplier."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.domains.ecommerce")


class OrderAgent(BaseDDDAgent):
    agent_type = "order"
    description = "Manages order lifecycle from creation to completion"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "create"),
            "order": context.get("order", {}),
            "order_id": context.get("order_id", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"order.{perception['operation']}",
            "order_id": perception.get("order_id", ""),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"order.{decision['operation']}",
            "order_id": decision.get("order_id", ""),
            "success": True,
        }


class CartAgent(BaseDDDAgent):
    agent_type = "cart"
    description = "Manages shopping cart operations and state"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "add"),
            "item": context.get("item", {}),
            "cart_id": context.get("cart_id", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"cart.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"cart.{decision['operation']}", "success": True}


class CheckoutAgent(BaseDDDAgent):
    agent_type = "checkout"
    description = "Manages checkout flow including validation and payment initiation"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "cart_id": context.get("cart_id", ""),
            "payment_method": context.get("payment_method", ""),
            "shipping": context.get("shipping", {}),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "checkout.process",
            "valid": True,
            "cart_id": perception.get("cart_id", ""),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "checkout.process",
            "success": decision.get("valid", False),
            "order_id": f"ord-{decision.get('cart_id', '')[:8]}",
        }


class PaymentAgent(BaseDDDAgent):
    agent_type = "payment"
    description = "Processes payments and manages transaction records"
    ddd_layer = "entity"
    workload_spec = "source_control"
    readonly = False

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "charge"),
            "amount": context.get("amount", 0),
            "currency": context.get("currency", "USD"),
            "method": context.get("method", "card"),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"payment.{perception['operation']}",
            "amount": perception.get("amount", 0),
            "valid": perception.get("amount", 0) > 0,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"payment.{decision['operation']}",
            "success": decision.get("valid", False),
            "transaction_id": f"txn-{decision.get('amount', 0):.0f}",
        }


class ProductAgent(BaseDDDAgent):
    agent_type = "product"
    description = "Manages product catalog, details, and search"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "search"),
            "product_id": context.get("product_id", ""),
            "query": context.get("query", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"product.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"product.{decision['operation']}", "success": True}


class CustomerAgent(BaseDDDAgent):
    agent_type = "customer"
    description = "Manages customer profiles, preferences, and history"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "find"),
            "customer_id": context.get("customer_id", ""),
            "query": context.get("query", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"customer.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"customer.{decision['operation']}", "success": True}


class PricingAgent(BaseDDDAgent):
    agent_type = "pricing"
    description = "Calculates prices, discounts, and dynamic pricing"
    ddd_layer = "value_object"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "product": context.get("product", {}),
            "quantity": context.get("quantity", 1),
            "discount_code": context.get("discount_code", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        base = perception.get("product", {}).get("price", 0) * perception.get("quantity", 1)
        return {"base_price": base, "action": "pricing.calculate", "discount": 0}

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "pricing.calculate",
            "total": decision.get("base_price", 0) - decision.get("discount", 0),
            "base": decision.get("base_price", 0),
        }


class PromotionAgent(BaseDDDAgent):
    agent_type = "promotion"
    description = "Manages promotions, coupons, and marketing campaigns"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "apply"),
            "code": context.get("code", ""),
            "cart_total": context.get("cart_total", 0),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"promotion.{perception['operation']}",
            "valid": bool(perception.get("code")),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"promotion.{decision['operation']}",
            "success": decision.get("valid", False),
        }


class InventoryAgent(BaseDDDAgent):
    agent_type = "inventory"
    description = "Tracks stock levels, reservations, and replenishment"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "check"),
            "product_id": context.get("product_id", ""),
            "quantity": context.get("quantity", 0),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"inventory.{perception['operation']}",
            "in_stock": perception.get("quantity", 0) > 0,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"inventory.{decision['operation']}",
            "success": True,
            "in_stock": decision.get("in_stock", False),
        }


class WarehouseAgent(BaseDDDAgent):
    agent_type = "warehouse"
    description = "Manages warehouse operations, storage, and retrieval"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "store"),
            "item": context.get("item", {}),
            "location": context.get("location", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"warehouse.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"warehouse.{decision['operation']}", "success": True}


class ShippingAgent(BaseDDDAgent):
    agent_type = "shipping"
    description = "Manages shipping routes, carriers, and delivery tracking"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "ship"),
            "order_id": context.get("order_id", ""),
            "destination": context.get("destination", ""),
            "carrier": context.get("carrier", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"shipping.{perception['operation']}",
            "carrier": perception.get("carrier", "standard"),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"shipping.{decision['operation']}",
            "success": True,
            "tracking_id": f"trk-{decision.get('order_id', '')[:8]}",
        }


class FulfillmentAgent(BaseDDDAgent):
    agent_type = "fulfillment"
    description = "Coordinates order fulfillment from warehouse to delivery"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "order_id": context.get("order_id", ""),
            "items": context.get("items", []),
            "priority": context.get("priority", "normal"),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "fulfillment.process",
            "order_id": perception.get("order_id", ""),
            "items_count": len(perception.get("items", [])),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "fulfillment.process",
            "success": True,
            "order_id": decision.get("order_id", ""),
        }


class ReturnsAgent(BaseDDDAgent):
    agent_type = "returns"
    description = "Manages return requests, approvals, and processing"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "request"),
            "order_id": context.get("order_id", ""),
            "reason": context.get("reason", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"returns.{perception['operation']}",
            "eligible": True,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"returns.{decision['operation']}",
            "success": decision.get("eligible", False),
        }


class RefundAgent(BaseDDDAgent):
    agent_type = "refund"
    description = "Processes refunds and credit adjustments"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "process"),
            "order_id": context.get("order_id", ""),
            "amount": context.get("amount", 0),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"refund.{perception['operation']}",
            "amount": perception.get("amount", 0),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"refund.{decision['operation']}",
            "success": True,
            "refund_id": f"ref-{decision.get('order_id', '')[:8]}",
        }


class SupplierAgent(BaseDDDAgent):
    agent_type = "supplier"
    description = "Manages supplier relationships, orders, and communications"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "order"),
            "supplier_id": context.get("supplier_id", ""),
            "items": context.get("items", []),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"supplier.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"supplier.{decision['operation']}", "success": True}
