"""Multitenancy as a first-class dimension (ADR 0021).

token claim → Context → query filter. A tenant sees its private world ∪ shared, never
another tenant's private transitions. Isolation is enforced by construction.
"""

from __future__ import annotations

from src.monkey_brain.kernel.compile import (
    ActorNetwork,
    Context,
    Feature,
    TenantWorld,
)


def test_token_claim_flows_into_context():
    ctx = Context.from_token({"sub": "u1", "tenant": "acme"})
    assert ctx.tenant_id == "acme"
    # accepts common claim names
    assert Context.from_token({"org_id": "globex"}).tenant_id == "globex"
    assert Context.from_token({"sub": "u"}).tenant_id == ""  # no tenant claim → empty


def test_isolation_a_cannot_see_b():
    world = TenantWorld()
    world.observe("acme", "Order", "Ship", domain="logistics")
    world.observe("globex", "Ticket", "Resolve", domain="support")

    acme = world.view(Context(tenant_id="acme"))
    globex = world.view(Context(tenant_id="globex"))

    # each tenant sees only its own transitions
    assert ("Order", "Ship") in set(acme)
    assert ("Order", "Ship") not in set(globex)
    assert ("Ticket", "Resolve") in set(globex)
    assert ("Ticket", "Resolve") not in set(acme)
    # successors respect the boundary
    assert acme.successors("Ticket") == []  # acme can't traverse globex's world


def test_shared_abstractions_visible_to_all():
    world = TenantWorld()
    world.observe("acme", "A", "B", shared=True)  # a shared abstraction
    world.observe("acme", "P", "Q")  # acme-private
    for tenant in ("acme", "globex"):
        view = world.view(Context(tenant_id=tenant))
        assert ("A", "B") in set(view)  # everyone sees shared
    assert ("P", "Q") not in set(world.view(Context(tenant_id="globex")))  # not the private one


def test_private_learning_stays_private():
    world = TenantWorld()
    # both tenants know the same shared transition, but learn different values privately
    world.observe("acme", "X", "Y", shared=True)
    world.observe("acme", "X", "Y", reward=1.0)  # acme reinforces it privately
    a_view = world.view(Context(tenant_id="acme"))
    b_view = world.view(Context(tenant_id="globex"))
    # acme's private reward overrides the shared value for acme; globex sees only shared
    assert a_view.feature("X", "Y", Feature.FREQUENCY) >= b_view.feature("X", "Y", Feature.FREQUENCY)


def test_tenant_scoped_actor_cycle():
    world = TenantWorld()
    world.batch_update(
        "acme",
        [
            {"src": "Hive", "dst": "Inspect", "domain": "beekeeping"},
            {"src": "Inspect", "dst": "Treat", "domain": "beekeeping"},
        ],
    )
    view = world.view(Context.from_token({"tenant": "acme"}))  # token → context → filter
    actor = ActorNetwork(view).add("agent")  # actor runs against the tenant view
    result = actor.cognitive_cycle("Hive", "Treat", view)
    assert result.reached_goal
    # a globex actor against its (empty) view reaches nothing — full isolation
    empty = world.view(Context(tenant_id="globex"))
    assert ActorNetwork(empty).add("g").cognitive_cycle("Hive", "Treat", empty).reached_goal is False


def test_promote_to_shared_federation():
    world = TenantWorld()
    world.observe("acme", "Generic", "Pattern")  # private
    world.promote_to_shared("acme", "Generic", "Pattern")  # publish the abstraction
    # now globex sees the shared abstraction while acme's private weights stay local
    assert ("Generic", "Pattern") in set(world.view(Context(tenant_id="globex")))
