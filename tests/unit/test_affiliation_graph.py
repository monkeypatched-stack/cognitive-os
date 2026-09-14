"""Tests for kernel/affiliations/graph.py::AffiliationGraph -- the single
authoritative communication-eligibility implementation.

Built against lightweight fakes rather than a booted PlanetaryRuntime (no
Redis/Neo4j needed) -- same "pure logic, independently testable" precedent
as kernel/pipeline/planning/plan_hysteresis.py. Each fake implements only
the exact surface AffiliationGraph actually calls: `_societies_for`,
`.get_actor`, `.governance.authorize`, `.delegation_registry`,
`.membership_registry`.

Written, not executed via pytest, per this project's standing preference
(see feedback_no_test_runs) -- for the user/CI to run.
"""

from __future__ import annotations

from src.monkey_brain.kernel.affiliations.affiliation import Affiliation
from src.monkey_brain.kernel.affiliations.graph import AffiliationGraph
from src.monkey_brain.kernel.affiliations.manager import AffiliationManager

# ── Fakes ──────────────────────────────────────────────────────────────────


class _FakeActorRuntime:
    def __init__(self, affiliations: AffiliationManager) -> None:
        self.affiliations = affiliations


class _FakeActorState:
    def __init__(self, actor_id: str, affiliations: AffiliationManager, *, is_active: bool = True) -> None:
        self.actor_id = actor_id
        self.actor_runtime = _FakeActorRuntime(affiliations)
        self.is_active = is_active


class _FakeSociety:
    def __init__(self, society_id: str) -> None:
        self.society_id = society_id


class _FakeGovernance:
    def __init__(self, allow_pairs: frozenset[tuple[str, str, str]] = frozenset()) -> None:
        self._allow = allow_pairs

    def authorize(self, actor_id: str, resource: str, action: str) -> bool:
        return (actor_id, resource, action) in self._allow


class _FakeSocietyRuntime:
    def __init__(
        self,
        society_id: str,
        actors: dict[str, _FakeActorState],
        *,
        allow_pairs: frozenset[tuple[str, str, str]] = frozenset(),
    ) -> None:
        self.society = _FakeSociety(society_id)
        self._actors = actors
        self.governance = _FakeGovernance(allow_pairs)

    def get_actor(self, actor_id: str):
        return self._actors.get(actor_id)


class _FakeDelegation:
    def __init__(self, delegation_id: str, membership_id: str, delegate_actor_id: str) -> None:
        self.delegation_id = delegation_id
        self.membership_id = membership_id
        self.delegate_actor_id = delegate_actor_id


class _FakeDelegationRegistry:
    def __init__(self, delegations: tuple[_FakeDelegation, ...] = ()) -> None:
        self._delegations = {d.delegation_id: d for d in delegations}

    def is_valid(self, delegation_id: str) -> bool:
        return delegation_id in self._delegations


class _FakeMembership:
    def __init__(self, actor_id: str) -> None:
        self.actor_id = actor_id


class _FakeMembershipRegistry:
    def __init__(self, memberships: dict[str, _FakeMembership]) -> None:
        self._memberships = memberships

    def get_membership(self, membership_id: str):
        return self._memberships.get(membership_id)


class _FakePlanetaryRuntime:
    def __init__(
        self,
        societies: tuple[_FakeSocietyRuntime, ...],
        *,
        delegation_registry=None,
        membership_registry=None,
    ) -> None:
        self._society_list = societies
        self.delegation_registry = delegation_registry
        self.membership_registry = membership_registry

    def _societies_for(self, actor_id: str):
        return tuple(sr for sr in self._society_list if sr.get_actor(actor_id) is not None)


def _actor(actor_id: str, *affiliations: Affiliation) -> _FakeActorState:
    manager = AffiliationManager()
    for affiliation in affiliations:
        manager.add(affiliation)
    return _FakeActorState(actor_id, manager)


def _aff(affiliation_type: str, target_id: str, target_name: str, trust: float = 0.5) -> Affiliation:
    return Affiliation(
        affiliation_id=f"aff:{target_id}:{affiliation_type}",
        affiliation_type=affiliation_type,
        target_id=target_id,
        target_name=target_name,
        trust_level=trust,
    )


# ── 1/2: direct + reverse affiliation (the diagnosed bug's fix) ────────────


def test_customer_store_one_sided_allows_both_directions():
    """The exact bug scenario: only the customer's side has the affiliation
    record (store's manager holds nothing pointing back). Uses an
    unregistered type id ("shopper") to isolate rules 1/2 from rule 3
    (bidirectional-by-type)."""
    priya = _actor("priya", _aff("shopper", "store", "Whole Foods Market", trust=0.7))
    store = _actor("store")
    society = _FakeSocietyRuntime("planet", {"priya": priya, "store": store})
    graph = AffiliationGraph(_FakePlanetaryRuntime((society,)))

    forward = graph.can_communicate("priya", "store")
    assert forward.allowed
    assert forward.reason == "direct affiliation permits communication"

    reverse = graph.can_communicate("store", "priya")
    assert reverse.allowed
    assert reverse.reason == "reverse affiliation permits communication"


def test_roommate_mirrored_both_directions_hits_direct_rule():
    priya = _actor("priya", _aff("roommate", "alice", "Alice Nguyen", trust=0.8))
    alice = _actor("alice", _aff("roommate", "priya", "Priya Sharma", trust=0.8))
    society = _FakeSocietyRuntime("planet", {"priya": priya, "alice": alice})
    graph = AffiliationGraph(_FakePlanetaryRuntime((society,)))

    decision = graph.can_communicate("priya", "alice")
    assert decision.allowed
    assert decision.reason == "direct affiliation permits communication"


# ── 3: bidirectional-by-type, one-sided record ──────────────────────────────


def test_friendship_one_sided_allows_reverse_via_bidirectional_type():
    """FRIENDSHIP is a real, registered bidirectional=True type
    (kernel/affiliations/types.py). Only A's side has the record -- B
    reaching A should still succeed.

    Fires via rule 2 (_reverse_affiliation), not rule 3
    (_bidirectional_affiliation), and that's correct, not a regression:
    "the diagnosed bug's fix" (see test_customer_store_one_sided_allows_
    both_directions above) intentionally widened rule 2 to permit the
    reverse direction for ANY one-sided record of a negotiable type,
    regardless of the type's own bidirectional declaration -- a strict
    superset of what rule 3 checks (rule 3 uses the identical
    _negotiable() helper, just gated by type_info.bidirectional). Rule 3
    remains correct, defensive fallback coverage for a hypothetical
    non-negotiable-yet-bidirectional type (none currently registered --
    member_of, the only _NON_NEGOTIABLE_TYPES entry, is itself
    bidirectional=False) -- not reachable by this scenario, or by any
    type in the registry today."""
    a = _actor("a", _aff("friendship", "b", "B", trust=0.6))
    b = _actor("b")
    society = _FakeSocietyRuntime("planet", {"a": a, "b": b})
    graph = AffiliationGraph(_FakePlanetaryRuntime((society,)))

    decision = graph.can_communicate("b", "a")
    assert decision.allowed
    assert decision.reason == "reverse affiliation permits communication"


# ── 4: shared organization (pre-existing rule, regression coverage) ────────


def test_shared_organization_third_party_target():
    a = _actor("a", _aff("employment", "acme", "Acme Corp"))
    b = _actor("b", _aff("employment", "acme", "Acme Corp"))
    society = _FakeSocietyRuntime("planet", {"a": a, "b": b})
    graph = AffiliationGraph(_FakePlanetaryRuntime((society,)))

    decision = graph.can_communicate("a", "b")
    assert decision.allowed
    assert decision.reason == "shared organizational affiliation permits communication"


# ── 5: shared society, no affiliations at all ───────────────────────────────


def test_shared_society_with_no_affiliations():
    a = _actor("a")
    b = _actor("b")
    society = _FakeSocietyRuntime("planet", {"a": a, "b": b})
    graph = AffiliationGraph(_FakePlanetaryRuntime((society,)))

    decision = graph.can_communicate("a", "b")
    assert decision.allowed
    assert decision.reason == "shared society membership permits communication"
    assert decision.society_id == "planet"


# ── employee <-> manager (asymmetric, real SUPERIOR/SUBORDINATE types) ─────


def test_employee_manager_asymmetric_relationship():
    """Only the employee's side records the relationship (SUPERIOR,
    bidirectional=False) -- both directions must still resolve, proving
    direction is respected (different `reason` per direction) rather than
    merely "presence of any affiliation" gating communication."""
    employee = _actor("employee", _aff("superior", "manager", "Manager", trust=0.6))
    manager = _actor("manager")
    society = _FakeSocietyRuntime("planet", {"employee": employee, "manager": manager})
    graph = AffiliationGraph(_FakePlanetaryRuntime((society,)))

    employee_to_manager = graph.can_communicate("employee", "manager")
    assert employee_to_manager.allowed
    assert employee_to_manager.reason == "direct affiliation permits communication"

    manager_to_employee = graph.can_communicate("manager", "employee")
    assert manager_to_employee.allowed
    assert manager_to_employee.reason == "reverse affiliation permits communication"


# ── No relationship at all ───────────────────────────────────────────────────


def test_no_relationship_and_no_shared_society_denies():
    a = _actor("a")
    b = _actor("b")
    society_a = _FakeSocietyRuntime("society-a", {"a": a})
    society_b = _FakeSocietyRuntime("society-b", {"b": b})
    graph = AffiliationGraph(_FakePlanetaryRuntime((society_a, society_b)))

    decision = graph.can_communicate("a", "b")
    assert not decision.allowed
    assert decision.reason == "no eligible communication pattern"


# ── 6: enterprise (PART_OF/BELONGS_TO chain) ────────────────────────────────


def test_enterprise_shared_parent_org_isolated():
    """Different societies (so rule 5 can't fire), both actors PART_OF the
    same parent org.

    Fires via rule 4 (_shared_organization), not rule 6
    (_enterprise_policy), and that's correct, not a regression: "part_of"
    is a negotiable type (not in _NON_NEGOTIABLE_TYPES), so rule 4's
    _target_set intersection (any negotiable type's shared targets) is a
    strict superset of rule 6's _ORG_HIERARCHY_TYPES-filtered
    intersection -- rule 4 always finds "enterprise-x" first. Rule 6
    remains correct, defensive fallback coverage for a hypothetical
    non-negotiable org-hierarchy type -- not reachable by this scenario,
    since both registered _ORG_HIERARCHY_TYPES entries ("part_of",
    "belongs_to") are negotiable today."""
    a = _actor("a", _aff("part_of", "enterprise-x", "Enterprise X"))
    b = _actor("b", _aff("part_of", "enterprise-x", "Enterprise X"))
    society_a = _FakeSocietyRuntime("society-a", {"a": a})
    society_b = _FakeSocietyRuntime("society-b", {"b": b})
    graph = AffiliationGraph(_FakePlanetaryRuntime((society_a, society_b)))

    decision = graph.can_communicate("a", "b")
    assert decision.allowed
    assert decision.reason == "shared organizational affiliation permits communication"


# ── 7: authorization policy (unit-tested directly -- see note below) ───────


def test_authorization_policy_rule_directly():
    """Rule 5 (shared society) is an unconditional allow for ANY
    same-society pair, so it always fires before rule 7 can be reached
    through can_communicate() when sender/recipient share a society --
    exactly the flat, first-match-wins precedence the architecture calls
    for (policies are additive GRANTS here, not overrides of an earlier
    allow). Unit-testing _authorization_policy directly still gives real
    coverage of the governance.authorize() integration itself."""
    a = _actor("a")
    b = _actor("b")
    society = _FakeSocietyRuntime(
        "planet",
        {"a": a, "b": b},
        allow_pairs=frozenset({("a", "actor:b", "communicate")}),
    )
    graph = AffiliationGraph(_FakePlanetaryRuntime((society,)))

    result = graph._authorization_policy("a", "b")
    assert result is not None
    reason, extra = result
    assert reason == "governance policy authorizes communication"
    assert extra["society_id"] == "planet"

    denied = graph._authorization_policy("b", "a")
    assert denied is None  # no matching grant for this direction


# ── 8: delegated authority ──────────────────────────────────────────────────


def test_delegated_authority_isolated():
    """Different societies (rules 4/5/6 can't fire), sender holds a valid
    delegation for a membership owned by recipient."""
    a = _actor("a")
    b = _actor("b")
    society_a = _FakeSocietyRuntime("society-a", {"a": a})
    society_b = _FakeSocietyRuntime("society-b", {"b": b})
    membership_registry = _FakeMembershipRegistry({"mem-b": _FakeMembership("b")})
    delegation_registry = _FakeDelegationRegistry((_FakeDelegation("del-1", "mem-b", "a"),))
    graph = AffiliationGraph(
        _FakePlanetaryRuntime(
            (society_a, society_b),
            delegation_registry=delegation_registry,
            membership_registry=membership_registry,
        )
    )

    decision = graph.can_communicate("a", "b")
    assert decision.allowed
    assert decision.reason == "delegated authority permits communication"


# ── 9: inherited organizational relationship (one intermediate hop) ────────


def test_inherited_relationship_via_manager_isolated():
    """A -MANAGES-> intermediate, intermediate has a direct affiliation to
    B -- A inherits the ability to reach B through the managed
    relationship. Different societies so rules 4/5/6 don't fire first."""
    a = _actor("a", _aff("manages", "intermediate", "Intermediate"))
    intermediate = _actor("intermediate", _aff("shopper", "b", "B"))
    b = _actor("b")
    society_a = _FakeSocietyRuntime("society-a", {"a": a, "intermediate": intermediate})
    society_b = _FakeSocietyRuntime("society-b", {"b": b})
    graph = AffiliationGraph(_FakePlanetaryRuntime((society_a, society_b)))

    decision = graph.can_communicate("a", "b")
    assert decision.allowed
    assert decision.reason == "inherited organizational relationship permits communication"


# ── Trust ranking happens only after eligibility ────────────────────────────


def test_trust_plays_no_role_in_eligibility():
    """A low-trust direct affiliation is still eligible -- can_communicate
    never reads trust_level. Trust only affects ORDERING of the eligible
    set, in TransactionCoordinator._eligible_affiliates (kernel/society/
    transaction.py, unchanged by this refactor -- that sort call is
    untouched regression coverage, not re-tested here)."""
    a = _actor("a", _aff("shopper", "b", "B", trust=0.01))
    b = _actor("b")
    society = _FakeSocietyRuntime("planet", {"a": a, "b": b})
    graph = AffiliationGraph(_FakePlanetaryRuntime((society,)))

    decision = graph.can_communicate("a", "b")
    assert decision.allowed
    assert decision.reason == "direct affiliation permits communication"
