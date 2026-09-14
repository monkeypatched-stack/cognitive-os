"""Support domain — customer support ticket lifecycle, shared across verticals.

Genuinely new (no grocery.py extraction, unlike commerce/logistics/
finance/supply_chain): nothing here reasons about groceries, orders, or
shipments specifically. A ticket references whatever order_id/
shipment_id an actor names, as a plain string — the same convention
every other domain module already uses for cross-references (buyer_id,
reviewer_id, approved_by, ...) — this module never validates those
references against commerce/logistics/grocery, so it has no dependency
on any of them.

Sections:
  - Ticket lifecycle (open -> escalated)
"""

from __future__ import annotations

import time

from src.monkey_brain.kernel.domains.commerce import DomainCapability


class SupportCapability(DomainCapability):
    """Discoverable customer-support competency."""

    name = "support"

    def __init__(self):
        super().__init__(
            {
                "open_ticket": open_ticket,
                "get_ticket": get_ticket,
                "escalate_ticket": escalate_ticket,
            }
        )


# ── Ticket lifecycle ────────────────────────────────────────────────


def open_ticket(
    kg,
    actor_id: str,
    subject: str,
    description: str = "",
    order_id: str | None = None,
    shipment_id: str | None = None,
    category: str = "general",
    now: float | None = None,
) -> dict:
    """MB-3041 Customer Support: a customer opens a real, persisted
    support ticket — status "open", priority "normal" by default.
    order_id/shipment_id are optional, plain string references, never
    validated here — the same "just a reference" convention used
    throughout (this keeps support.py free of any commerce/logistics
    dependency, even though most tickets in practice point back at one).
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    import uuid

    if not subject:
        return {"success": False, "error": "a ticket needs a subject"}

    now = now if now is not None else time.time()
    ticket_id = f"ticket_{uuid.uuid4().hex}"
    kg.add_entity(
        ticket_id,
        EntityType.OTHER,
        subject,
        {
            "ticket": True,
            "actor_id": actor_id,
            "subject": subject,
            "description": description,
            "order_id": order_id,
            "shipment_id": shipment_id,
            "category": category,
            "status": "open",
            "priority": "normal",
            "created_at": now,
            "history": [{"status": "open", "at": now}],
        },
    )
    return {"success": True, "ticket_id": ticket_id, "status": "open"}


def get_ticket(kg, ticket_id: str) -> dict:
    """Look up a ticket's current status and details."""
    ticket = kg.get_entity(ticket_id) if kg is not None else None
    if ticket is None or not ticket.attributes.get("ticket"):
        return {"success": False, "error": f"ticket {ticket_id!r} not found"}
    fields = (
        "actor_id",
        "subject",
        "description",
        "order_id",
        "shipment_id",
        "category",
        "status",
        "priority",
        "history",
    )
    return {
        "success": True,
        "ticket_id": ticket_id,
        **{f: ticket.attributes.get(f) for f in fields},
    }


def escalate_ticket(
    kg,
    ticket_id: str,
    escalated_by: str | None = None,
    reason: str = "",
    now: float | None = None,
) -> dict:
    """MB-3042 Ticket Escalation: advances an open ticket to
    "escalated" and raises its priority to "high". Only ever from
    "open" — a ticket that's already escalated (or, in the future,
    closed) can't be escalated again; re-escalation isn't a real state
    this ticket asked for, so it's refused rather than silently
    accepted as a no-op.
    """
    now = now if now is not None else time.time()
    ticket = kg.get_entity(ticket_id) if kg is not None else None
    if ticket is None or not ticket.attributes.get("ticket"):
        return {"success": False, "error": f"ticket {ticket_id!r} not found"}

    current = ticket.attributes.get("status")
    if current != "open":
        return {
            "success": False,
            "error": f"ticket {ticket_id!r} is {current!r}, cannot escalate (must be 'open' first)",
        }

    history = list(ticket.attributes.get("history", []))
    history.append({"status": "escalated", "at": now, "reason": reason})
    kg.update_entity(
        ticket_id,
        attributes={
            "status": "escalated",
            "priority": "high",
            "escalated_by": escalated_by,
            "escalated_at": now,
            "escalation_reason": reason,
            "history": history,
        },
    )
    return {
        "success": True,
        "ticket_id": ticket_id,
        "status": "escalated",
        "priority": "high",
    }


__all__ = ["SupportCapability", "open_ticket", "get_ticket", "escalate_ticket"]
