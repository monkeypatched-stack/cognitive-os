"""MB-3041 Customer Support — customer opens ticket scenario.

Customer opens ticket.

No support-ticket concept existed anywhere in the codebase. Built a new
kernel/domains/support.py module — open_ticket() persists a real
support ticket (status="open", priority="normal"), referencing
whatever order_id/shipment_id the customer names (a plain string,
never validated against commerce/logistics — the same cross-reference
convention every other domain module already uses, keeping support.py
free of any dependency on either).
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.grocery import build_default_capability_bus
from src.monkey_brain.kernel.domains.support import (
    SupportCapability,
    get_ticket,
    open_ticket,
)
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph


def test_mb3041_customer_opens_a_ticket():
    kg = KnowledgeGraph()

    result = open_ticket(
        kg,
        "alice",
        "My order never arrived",
        description="Been a week",
        order_id="ORD-1",
        category="shipping",
    )

    assert result["success"] is True
    assert result["status"] == "open"

    got = get_ticket(kg, result["ticket_id"])
    assert got["status"] == "open"
    assert got["priority"] == "normal"
    assert got["order_id"] == "ORD-1"
    assert got["actor_id"] == "alice"


def test_mb3041_ticket_without_a_subject_is_refused():
    kg = KnowledgeGraph()

    result = open_ticket(kg, "alice", "")

    assert result["success"] is False


def test_mb3041_unknown_ticket_is_an_honest_failure():
    kg = KnowledgeGraph()

    result = get_ticket(kg, "does-not-exist")

    assert result["success"] is False
    assert "not found" in result["error"]


def test_mb3041_support_capability_is_on_the_default_bus():
    bus = build_default_capability_bus()

    assert bus.discover("support") is not None
    assert bus.discover_operation("open_ticket") is not None


def test_mb3041_open_ticket_via_capability():
    kg = KnowledgeGraph()
    cap = SupportCapability()

    assert cap.can_handle("open_ticket")
    result = cap.invoke("open_ticket", kg, "alice", "Where is my refund?")

    assert result["success"] is True
