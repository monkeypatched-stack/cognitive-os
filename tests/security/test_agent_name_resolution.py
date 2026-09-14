"""Agent name resolution must never silently bind the WRONG agent (E-4).

The PascalCase → snake_case step ended with:

    snake.rstrip('_agent')

`str.rstrip` strips trailing CHARACTERS from the set {_, a, g, e, n, t} — it does not strip
the suffix. So the resolver looked up:

    StorageAgent  -> 'stor'
    MessageAgent  -> 'mess'
    DataAgent     -> 'd'
    StateAgent    -> 's'

and then fed that into a substring match ("first registered type containing the key"). With
311 agents registered, 'd' matched 65 of them, and DataAgent resolved to `nanda` — a
confidently wrong agent, executed with no error raised.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.runtime.agent_resolver import (
    _fuzzy_agent_type,
    _snake_case,
    _strip_agent_suffix,
)

# a slice of the real registry
REGISTERED = [
    "nanda",
    "data_routing",
    "cognitive_data_routing",
    "storage",
    "store",
    "state",
    "message",
    "security",
    "planner",
    "policy",
    "static_analysis",
    "calibration_due",
]


@pytest.mark.parametrize(
    "name,expected",
    [
        ("StorageAgent", "storage"),  # was 'stor'
        ("MessageAgent", "message"),  # was 'mess'
        ("DataAgent", "data"),  # was 'd'  → matched 65 agents
        ("StateAgent", "state"),  # was 's'
        ("NotificationAgent", "notification"),  # was 'notificatio'
        ("EventAgent", "event"),  # was 'ev'
        ("SecurityAgent", "security"),  # was already correct
        ("LineResolverAgent", "line_resolver"),
    ],
)
def test_the_agent_suffix_is_stripped_as_a_suffix_not_a_character_set(name, expected):
    assert _strip_agent_suffix(_snake_case(name)) == expected


def test_snake_case_handles_punctuation_and_acronyms():
    assert _snake_case("Line-Resolver Agent") == "line_resolver_agent"
    assert _snake_case("NANDAAgent") == "nandaagent"  # no lower→upper boundary to split on
    assert _snake_case("") == ""


def test_the_agent_named_agent_does_not_strip_to_nothing():
    assert _strip_agent_suffix(_snake_case("Agent")) == "agent"


# ------------------------------------------------------------------ fuzzy matching


def test_data_no_longer_binds_nanda():
    """The exact regression: core 'd' substring-matched 'nanda' and bound it."""
    assert _fuzzy_agent_type("d", REGISTERED) is None


@pytest.mark.parametrize("core", ["d", "s", "ev", "abc"])
def test_a_short_key_is_never_fuzzy_matched(core):
    assert _fuzzy_agent_type(core, REGISTERED) is None


def test_an_unambiguous_token_match_still_resolves():
    assert _fuzzy_agent_type("static_analysis", REGISTERED) == "static_analysis"
    assert _fuzzy_agent_type("calibration_due", REGISTERED) == "calibration_due"


def test_token_order_does_not_matter():
    assert _fuzzy_agent_type("due_calibration", REGISTERED) == "calibration_due"


def test_ambiguity_is_refused_not_guessed():
    """'data' is covered by data_routing, cognitive_data_routing... Picking one at random is
    how the wrong agent gets executed — refuse instead."""
    assert _fuzzy_agent_type("data", REGISTERED) is None


def test_an_exact_token_match_beats_the_ambiguity_check():
    """'data_routing' IS a registered type; the fact that `cognitive_data_routing` also
    contains those tokens must not make the precise answer ambiguous."""
    assert _fuzzy_agent_type("data_routing", REGISTERED) == "data_routing"


def test_a_more_specific_request_is_not_absorbed_by_a_generic_agent():
    """`line` must not answer for `LineResolverAgent` — it cannot do the resolving part.
    Widening ('storage' -> 'cognitive_storage') is safe; narrowing is not."""
    registered = REGISTERED + ["line", "cognitive_storage"]
    assert _fuzzy_agent_type("line_resolver", registered) is None
    # widening in the safe direction: the registered agent covers every token asked for
    assert _fuzzy_agent_type("calibration", ["cognitive_calibration"]) == "cognitive_calibration"


def test_a_bare_substring_is_not_a_match():
    """'stor' is a substring of 'storage' and 'store' but a token of neither."""
    assert _fuzzy_agent_type("stor", REGISTERED) is None


def test_no_match_is_none_not_a_crash():
    assert _fuzzy_agent_type("calibration_due", []) is None
    assert _fuzzy_agent_type("", REGISTERED) is None


# ------------------------------------------------------------------ against the real registry


def test_real_registry_resolves_common_names_to_themselves():
    from broca.registry import get_registry, register_etass_agents

    register_etass_agents()
    types = get_registry().agent_types()
    assert len(types) > 100, "the registry should not be empty"

    for name in ("PlannerAgent", "PolicyAgent", "GovernanceAgent"):
        core = _strip_agent_suffix(_snake_case(name))
        assert core in types, f"{name} must resolve to its own registered type {core!r}"


def test_security_agent_is_refused_rather_than_bound_to_one_of_four():
    """The registry has security_scan, security_audit, security_identity and
    cognitive_security. The old substring match bound the FIRST one. A scan is not an audit.
    """
    from broca.registry import get_registry, register_etass_agents

    register_etass_agents()
    types = get_registry().agent_types()
    assert "security" not in types
    assert _fuzzy_agent_type("security", types, "SecurityAgent") is None


def test_no_registered_agent_is_reachable_from_a_one_character_key():
    from broca.registry import get_registry, register_etass_agents

    register_etass_agents()
    types = get_registry().agent_types()
    for ch in "abcdefghijklmnopqrstuvwxyz":
        assert _fuzzy_agent_type(ch, types) is None
