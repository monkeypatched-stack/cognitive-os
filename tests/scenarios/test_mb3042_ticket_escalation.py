"""MB-3042 Ticket Escalation — escalate support scenario.

Escalate support.

Built kernel/domains/support.py::escalate_ticket() — advances an open
ticket (MB-3041) to "escalated" and raises its priority to "high".
Only ever from "open" — a ticket that's already escalated can't be
escalated again; re-escalation isn't a state this ticket asked for, so
it's refused rather than silently accepted as a no-op, matching the
guarded single-transition discipline the shipment lifecycle (MB-3019)
already established.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.support import (
    SupportCapability,
    escalate_ticket,
    get_ticket,
    open_ticket,
)
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph


def _open_ticket(kg: KnowledgeGraph) -> str:
    return open_ticket(kg, "alice", "My order never arrived", order_id="ORD-1")["ticket_id"]


def test_mb3042_escalating_an_open_ticket_raises_priority_and_status():
    kg = KnowledgeGraph()
    ticket_id = _open_ticket(kg)

    result = escalate_ticket(kg, ticket_id, escalated_by="agent_bob", reason="no response after 3 days")

    assert result["success"] is True
    assert result["status"] == "escalated"
    assert result["priority"] == "high"
    got = get_ticket(kg, ticket_id)
    assert got["status"] == "escalated"
    assert got["priority"] == "high"


def test_mb3042_cannot_escalate_the_same_ticket_twice():
    kg = KnowledgeGraph()
    ticket_id = _open_ticket(kg)
    escalate_ticket(kg, ticket_id)

    result = escalate_ticket(kg, ticket_id)

    assert result["success"] is False
    assert "must be 'open'" in result["error"]


def test_mb3042_unknown_ticket_is_an_honest_failure():
    kg = KnowledgeGraph()

    result = escalate_ticket(kg, "does-not-exist")

    assert result["success"] is False
    assert "not found" in result["error"]


def test_mb3042_escalate_ticket_via_capability():
    kg = KnowledgeGraph()
    ticket_id = _open_ticket(kg)
    cap = SupportCapability()

    assert cap.can_handle("escalate_ticket")
    result = cap.invoke("escalate_ticket", kg, ticket_id, escalated_by="agent_bob")

    assert result["success"] is True
