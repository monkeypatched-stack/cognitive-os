"""MB-3059 Complete Customer Journey — full business workflow scenario.

Register -> Browse -> Search -> View Product -> Add To Cart -> Checkout
-> Payment -> Inventory -> Pick -> Pack -> Ship -> Deliver -> Review ->
Return -> Refund.

Verify the entire business workflow executes successfully.

This is a genuine end-to-end integration test, not a "verify a single
gap" ticket like MB-3046-3058: it chains together every capability
built across this session's whole MB-30xx arc, using ONE customer, ONE
product, and ONE order throughout, proving each stage's real OUTPUT
correctly feeds the next stage's real INPUT with no impedance mismatch
— not each function tested in isolation (already covered by its own
dedicated MB-30xx test file), but the whole pipeline composing.

  Register       PlanetaryRuntime.register_actor()            (MB-3000)
  Browse         open_products()                               (MB-3002)
  Search         CommerceCapabilityBus "search_products"        (MB-3002)
  View Product   get_product_detail()                           (MB-3005)
  Add To Cart    add_to_cart()                                  (MB-3007)
  Checkout       OrderCreationCapability                        (MB-3010)
  Payment        PaymentConfirmationCapability/PaymentCapability(MB-3012)
  Inventory      confirm_reservation() (real stock decrement)   (MB-3015)
  Pick           assign_picker()                                (MB-3017)
  Pack           pack_order()                                   (MB-3018)
  Ship           create_shipment()                              (MB-3019)
  Deliver        mark_shipment_in_transit/_delivered,
                 confirm_receipt()                       (MB-3019/21/22)
  Review         leave_review()                                 (MB-3023)
  Return         return_order()/approve_return()          (MB-3025/26)
  Refund         (approve_return()'s own refund — verified here)(MB-3027)

Register uses PlanetaryRuntime (the real actor/society/geography
system); everything from Browse onward uses the real domain-layer
capabilities (commerce.py/grocery.py/logistics.py) against a plain
KnowledgeGraph, connected by the SAME actor_id the registration
produced — the two layers are genuinely separate in this codebase (see
MB-3051/3052's own findings), so this deliberately doesn't fabricate an
integration between them that doesn't exist; it chains what's real on
each side.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.commerce import (
    CommerceCapability,
    CommerceCapabilityBus,
    add_to_cart,
    get_product_detail,
    leave_review,
    list_product,
    onboard_merchant,
)
from src.monkey_brain.kernel.domains.grocery import (
    OrderCreationCapability,
    PaymentCapability,
    PaymentConfirmationCapability,
    approve_return,
    open_products,
    return_order,
)
from src.monkey_brain.kernel.domains.logistics import (
    assign_picker,
    confirm_receipt,
    create_shipment,
    mark_shipment_delivered,
    mark_shipment_in_transit,
    pack_order,
)
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph
from src.monkey_brain.kernel.society.domain import (
    ActorIdentity,
    ActorProfile,
    ActorType,
)
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

MERCHANT_ID = "merchant_bob"
STARTING_BALANCE = 100.0


def test_mb3059_complete_customer_journey_executes_successfully():
    # ── Register ──────────────────────────────────────────────────────
    marketplace = PlanetaryRuntime()
    alice_state = marketplace.register_actor(
        ActorProfile(identity=ActorIdentity(name="Alice", actor_type=ActorType.HUMAN)),
    )
    actor_id = alice_state.actor_id
    assert marketplace._home_society_runtime(actor_id) is not None

    kg = KnowledgeGraph()
    store_id = onboard_merchant(
        kg,
        MERCHANT_ID,
        "Bob's Store",
        delivery_fee=4.99,
        address="200 W 23rd St, New York, NY",
        has_autonomous_cart=True,
        robot_pick_minutes=4.0,
    )["store_id"]
    product_id = list_product(kg, store_id, MERCHANT_ID, "Oat Milk", price=4.5, quantity=10)["product_id"]
    kg.add_entity(
        "addr_home",
        EntityType.ADDRESS,
        "Home",
        {
            "street": "123 Main St",
            "city": "New York",
            "state": "NY",
            "zip_code": "10001",
            "is_primary": True,
        },
    )
    wallet_id = "wallet_alice"
    kg.add_entity(
        wallet_id,
        EntityType.ACCOUNT,
        "Alice Wallet",
        {
            "account_type": "debit",
            "balance": STARTING_BALANCE,
            "owner": actor_id,
        },
    )
    kg.add_entity(
        "proc_stripe",
        EntityType.ORGANIZATION,
        "Stripe",
        {"type": "payment_processor", "priority": 0},
    )

    # ── Browse ────────────────────────────────────────────────────────
    catalog = open_products(kg)
    assert any(p.entity_id == product_id for p in catalog)

    # ── Search ────────────────────────────────────────────────────────
    bus = CommerceCapabilityBus([CommerceCapability()])
    search_results = bus.invoke("search_products", kg, query="oat milk")
    assert any(p.entity_id == product_id for p in search_results)

    # ── View Product ──────────────────────────────────────────────────
    detail = get_product_detail(kg, product_id)
    assert detail.name == "Oat Milk"
    assert detail.inventory == 10

    # ── Add To Cart ───────────────────────────────────────────────────
    cart = add_to_cart(kg, actor_id, product_id, quantity=2)
    assert cart.item_count == 2

    # ── Checkout ──────────────────────────────────────────────────────
    cart_items = [
        {
            "id": line.product_id,
            "name": line.name,
            "price": line.price,
            "qty": line.quantity,
            "store_id": store_id,
            "store_name": "Bob's Store",
        }
        for line in cart.lines
    ]
    order = OrderCreationCapability().handle(
        {
            "context": {
                "knowledge_graph": kg,
                "selected_product": cart_items,
                "actor_id": actor_id,
                "question": "deliver my order",
            }
        }
    )
    assert order["success"] is True, order
    order_id = order["order_id"]
    assert order["backordered"] == []

    # ── Payment ───────────────────────────────────────────────────────
    payment_context = {
        "knowledge_graph": kg,
        "total": order["total"],
        "order": order,
        "actor_id": actor_id,
        "selected_product": cart_items,
    }
    confirmation = PaymentConfirmationCapability().handle({"context": payment_context})
    assert confirmation["success"] is True, confirmation
    payment = PaymentCapability().handle({"context": payment_context})
    assert payment["success"] is True, payment
    assert payment["status"] == "completed"
    assert payment["amount"] == order["total"]

    # ── Inventory ─────────────────────────────────────────────────────
    # The reservation OrderCreation only HELD is now a real, permanent
    # decrement — the exact no-oversell guarantee MB-3015 verified.
    assert kg.get_entity(product_id).attributes["quantity"] == 8

    # ── Pick ──────────────────────────────────────────────────────────
    picker = assign_picker(kg, store_id)
    assert picker["success"] is True, picker
    assert picker["picker_type"] == "autonomous_cart"

    # ── Pack ──────────────────────────────────────────────────────────
    packed = pack_order(cart_items)
    assert packed["success"] is True
    assert packed["package_count"] == 1

    # ── Ship ──────────────────────────────────────────────────────────
    shipment = create_shipment(kg, order_id, packed["packages"], rider_id=picker.get("picker_id"))
    assert shipment["success"] is True
    shipment_id = shipment["shipment_id"]

    # ── Deliver ───────────────────────────────────────────────────────
    mark_shipment_in_transit(kg, shipment_id)
    delivered = mark_shipment_delivered(kg, shipment_id)
    assert delivered["success"] is True
    assert delivered["order_delivered"] is True
    assert kg.get_entity(order_id).attributes["status"] == "delivered"

    receipt = confirm_receipt(kg, order_id, actor_id=actor_id)
    assert receipt["success"] is True
    assert kg.get_entity(order_id).attributes["status"] == "completed"

    # ── Review ────────────────────────────────────────────────────────
    review = leave_review(kg, product_id, actor_id, 5, "Great oat milk!")
    assert review["success"] is True, review
    assert review["review_count"] == 1

    # ── Return ────────────────────────────────────────────────────────
    return_request = return_order(kg, order_id, actor_id=actor_id, reason="changed my mind")
    assert return_request["success"] is True, return_request
    assert return_request["status"] == "return_requested"

    approved = approve_return(kg, order_id, approved_by=MERCHANT_ID)
    assert approved["success"] is True, approved
    assert kg.get_entity(order_id).attributes["status"] == "returned"

    # ── Refund ────────────────────────────────────────────────────────
    # The customer's wallet is back to exactly its starting balance, and
    # the product is restocked to its original quantity — the whole
    # journey nets out to a clean, fully-reversed no-op on both money
    # and inventory.
    assert kg.get_entity(wallet_id).attributes["balance"] == STARTING_BALANCE
    assert kg.get_entity(order_id).attributes["total_refunded"] == order["total"]
    assert kg.get_entity(product_id).attributes["quantity"] == 10
