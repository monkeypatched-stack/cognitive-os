"""parse_budget() regression: a negotiation floor/target dollar amount in
the SAME request text must not be misread as a stated total-order
spending cap.

Confirmed live this session, once NegotiatePrice's real deal-parameters
started actually reaching this same request text (the wiring this fix
enables — see OrderCreationCapability's own comment on
context["negotiated_prices"]): "negotiate ground coffee down from its
listed price, floor $8.00, then buy it" rejected a real, affordable
$12.98 order with "exceeds the $8.00 budget stated in the request" —
parse_budget matched ANY dollar amount anywhere in the text,
unconditionally. Now gated on real budget language.
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.grocery import parse_budget


def test_a_negotiation_floor_is_not_mistaken_for_a_stated_budget():
    q = "Negotiate the price of ground coffee down from its listed price, floor $8.00, then buy it."
    assert parse_budget(q) is None


def test_a_negotiation_target_is_not_mistaken_for_a_stated_budget():
    q = "Open at $8.50 for the coffee negotiation, then buy it."
    assert parse_budget(q) is None


def test_real_budget_phrasings_still_parse_correctly():
    assert parse_budget("Buy milk and pizza under a $20 budget.") == 20.0
    assert parse_budget("Buy milk, pizza, and eggs. Do not spend more than $5.") == 5.0
    assert parse_budget("I have exactly $20 to spend on groceries.") == 20.0


def test_no_dollar_amount_at_all_is_none():
    assert parse_budget("Buy milk and pizza.") is None
