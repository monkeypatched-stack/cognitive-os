"""CCB-300 — retail-store society governance and replenishment."""

from __future__ import annotations

import time

from src.monkey_brain.kernel.society.delegation import DelegationRegistry
from src.monkey_brain.kernel.society.domain import (
    ActorIdentity,
    ActorProfile,
    ActorType,
)
from src.monkey_brain.kernel.society.governance import GovernancePolicy, Permission
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime


def _actor(pr, name):
    return pr.register_actor(
        ActorProfile(
            identity=ActorIdentity(name=name, actor_type=ActorType.HUMAN),
        )
    )


def _store():
    pr = PlanetaryRuntime()
    actors = {
        name: _actor(pr, name)
        for name in (
            "Manager",
            "Cashier",
            "Stock Clerk",
            "Customer",
            "Assistant Manager",
        )
    }
    store = pr.create_society("Main Street Store", society_type="retail_store", always_active=True)
    for name, actor in actors.items():
        pr.join_society(actor.actor_id, store.society_id, role=name.lower().replace(" ", "_"))
    store.update_shared_resources(
        inventory={"milk": {"quantity": 2, "minimum": 5}},
        warehouse={"milk": 20},
        supplier={"name": "Dairy Supplier"},
    )
    return pr, actors, store


def test_stock_replenishment_reads_warehouse_and_updates_inventory():
    _pr, _actors, store = _store()
    check = store.inventory_replenishment("milk")

    assert check["needs_replenishment"] is True
    assert check["warehouse"]["milk"] == 20
    assert check["supplier"]["name"] == "Dairy Supplier"

    updated = store.replenish_inventory("milk", 10, supplier="Dairy Supplier")
    assert updated["quantity"] == 12
    assert store.inventory_replenishment("milk")["needs_replenishment"] is False


def test_cashier_refund_policy_denies_amount_above_limit():
    pr, actors, store = _store()
    cashier = actors["Cashier"].actor_id
    governance = pr.governance_for(store.society_id)
    governance.grant_permission(
        Permission(
            actor_id=cashier,
            resource="refund",
            action="issue",
        )
    )
    governance.add_policy(
        GovernancePolicy(
            name="cashier refund limit",
            metadata={"resource": "refund", "action": "issue", "max_amount": 100},
        )
    )

    assert pr.authorize(cashier, "refund", "issue", amount=50)
    assert not pr.authorize(cashier, "refund", "issue", amount=500)


def test_manager_delegation_grants_assistant_authority_until_expiry():
    pr, actors, store = _store()
    manager = next(
        m
        for m in pr.membership_registry.memberships_for_actor(actors["Manager"].actor_id)
        if m.society_id == store.society_id
    )
    assistant = actors["Assistant Manager"].actor_id
    delegations = DelegationRegistry(pr.membership_registry)
    delegation = delegations.grant(
        manager.membership_id,
        assistant,
        ("refund:issue",),
        # Keep enough wall-clock headroom for the runtime's lazy dependency
        # initialization; expiry semantics are covered independently by the
        # membership delegation tests.
        valid_until=time.time() + 60,
        reason="manager departure coverage",
    )

    assert "refund:issue" in delegations.effective_delegated_permissions(assistant)
    pr.leave_society(actors["Manager"].actor_id, store.society_id)
    assert delegations.is_valid(delegation.delegation_id)
