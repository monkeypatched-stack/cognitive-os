"""MB-3046 Payment Service Outage — retry workflow scenario.

Retry workflow.

Like MB-3010/3012/3013/3015/3028/3032/3033, no new code was needed:
finance.py::process_payment_with_fallback() (Level 22, GS-2200) already
tries every payment processor in priority order, retrying a
transiently-down one before falling back to the next, and is already
wired into the real checkout path — PaymentCapability.handle() calls it
directly and refuses to charge the wallet at all when every processor
is unavailable (grocery.py:5177-5178). This file verifies the outage
scenario end to end: a down primary processor falls back to a working
backup and the charge still succeeds; every processor down produces an
honest failure with a full attempt trace and never touches the wallet.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.finance import process_payment_with_fallback
from src.monkey_brain.kernel.domains.grocery import PaymentCapability
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph


def test_mb3046_down_primary_processor_falls_back_to_a_working_backup():
    kg = KnowledgeGraph()
    kg.add_entity(
        "proc_primary",
        EntityType.ORGANIZATION,
        "Primary Gateway",
        {"type": "payment_processor", "priority": 0, "status": "down"},
    )
    kg.add_entity(
        "proc_backup",
        EntityType.ORGANIZATION,
        "Backup Gateway",
        {"type": "payment_processor", "priority": 1, "status": "operational"},
    )

    result = process_payment_with_fallback(kg, 50.0)

    assert result["success"] is True
    assert result["processor"] == "Backup Gateway"
    assert any(a["result"] == "failed (processor down)" for a in result["attempts"])


def test_mb3046_every_processor_down_is_an_honest_failure_with_full_trace():
    kg = KnowledgeGraph()
    kg.add_entity(
        "proc_a",
        EntityType.ORGANIZATION,
        "Gateway A",
        {"type": "payment_processor", "priority": 0, "status": "down"},
    )
    kg.add_entity(
        "proc_b",
        EntityType.ORGANIZATION,
        "Gateway B",
        {"type": "payment_processor", "priority": 1, "status": "down"},
    )

    result = process_payment_with_fallback(kg, 50.0)

    assert result["success"] is False
    assert result["reason"] == "every payment processor unavailable"
    assert len(result["attempts"]) == 4


def test_mb3046_outage_blocks_the_real_checkout_charge():
    kg = KnowledgeGraph()
    kg.add_entity(
        "wallet_1",
        EntityType.ACCOUNT,
        "Alice Wallet",
        {"account_type": "debit", "balance": 100.0},
    )
    kg.add_entity(
        "proc_a",
        EntityType.ORGANIZATION,
        "Gateway A",
        {"type": "payment_processor", "priority": 0, "status": "down"},
    )
    cap = PaymentCapability()

    outcome = cap.handle(
        {
            "context": {
                "knowledge_graph": kg,
                "actor_id": "alice",
                "total": 20.0,
                "chosen_payment_source": "wallet_1",
            }
        }
    )

    assert outcome["success"] is False
    assert kg.get_entity("wallet_1").attributes["balance"] == 100.0


def test_mb3046_checkout_succeeds_via_fallback_despite_a_down_processor():
    kg = KnowledgeGraph()
    kg.add_entity(
        "wallet_1",
        EntityType.ACCOUNT,
        "Alice Wallet",
        {"account_type": "debit", "balance": 100.0},
    )
    kg.add_entity(
        "proc_a",
        EntityType.ORGANIZATION,
        "Gateway A",
        {"type": "payment_processor", "priority": 0, "status": "down"},
    )
    kg.add_entity(
        "proc_b",
        EntityType.ORGANIZATION,
        "Gateway B",
        {"type": "payment_processor", "priority": 1, "status": "operational"},
    )
    cap = PaymentCapability()

    outcome = cap.handle(
        {
            "context": {
                "knowledge_graph": kg,
                "actor_id": "alice",
                "total": 20.0,
                "chosen_payment_source": "wallet_1",
            }
        }
    )

    assert outcome["success"] is True
    assert outcome["processor"] == "Gateway B"
    assert kg.get_entity("wallet_1").attributes["balance"] == 80.0
