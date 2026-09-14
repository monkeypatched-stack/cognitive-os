"""SocietyActivationEngine (Society as Organizational Context refactor).

An actor may be MEMBER_OF several societies at once (see membership.py).
For any given goal, only some of those societies are relevant — this engine
discovers an actor's memberships, decides which societies activate for a
goal, and aggregates their governance policies into one deterministically
ordered bundle. ReasoningRuntime invokes this once per tick, between the
"believe" (goal is now known) and "plan" stages (kernel/cognitive_os/
reasoning_runtime.py::ReasoningRuntime.reason()).

Relevance is a deterministic tag/keyword match against the goal text — this
codebase has no NLU/LLM goal-understanding engine, so a keyword scorer is
the honest, testable substitute. Real-world "why did Grocery Store activate
for 'buy groceries'" reasoning would need a semantic matcher; the interface
here (`activate_for_goal`) is designed so that implementation can be swapped
later without touching any caller.

Policy precedence: GovernancePolicy.priority (kernel/society/governance.py)
already exists but had zero consumers before this refactor — this is its
first real use. Activated societies' policies are merged by priority
descending, deduped by name (highest-priority society's version wins),
ties broken by activation order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from src.monkey_brain.kernel.society.governance import GovernancePolicy

SocietyLookup = Callable[[str], Any | None]
MembershipLookup = Callable[[str], tuple[str, ...]]


@dataclass(frozen=True)
class ActivatedSociety:
    society_id: str
    reason: str
    """"always_active" or "tag_match:<tag>" — why this society activated."""


@dataclass(frozen=True)
class PolicyBundle:
    activated_society_ids: tuple[str, ...]
    policies: tuple[GovernancePolicy, ...]
    """Deduped by name, ordered highest-priority first."""


@dataclass(frozen=True)
class SocietyActivationResult:
    actor_id: str
    goal: str
    activated: tuple[ActivatedSociety, ...] = ()
    policy_bundle: PolicyBundle = field(default_factory=lambda: PolicyBundle((), ()))

    def activated_society_ids(self) -> tuple[str, ...]:
        return tuple(a.society_id for a in self.activated)


def _matches(goal_text: str, society: Any) -> str | None:
    """Return the matched tag (or None) — society.always_active is checked
    separately by the caller."""
    goal_lower = goal_text.lower()
    if not goal_lower:
        return None
    for tag in getattr(society, "activation_tags", ()):
        if tag and tag.lower() in goal_lower:
            return tag
    name = getattr(society, "name", "")
    if name and name.lower() in goal_lower:
        return name
    return None


class SocietyActivationEngine:
    """Stateless service: given an actor_id + goal, discovers memberships,
    selects relevant societies, and merges their policies. Owned once by
    PlanetaryRuntime and shared by every SocietyRuntime it manages (and,
    through them, every actor's ReasoningRuntime) — see kernel/society/
    integration.py's __init__ and _attach_society()."""

    def __init__(self, membership_lookup: MembershipLookup, society_lookup: SocietyLookup) -> None:
        self._membership_lookup = membership_lookup
        self._society_lookup = society_lookup

    def activate_for_goal(self, actor_id: str, goal: str) -> SocietyActivationResult:
        activated: list[ActivatedSociety] = []
        for society_id in self._membership_lookup(actor_id):
            society_runtime = self._society_lookup(society_id)
            if society_runtime is None:
                continue
            society = society_runtime.society
            if society.always_active:
                activated.append(ActivatedSociety(society_id, "always_active"))
                continue
            tag = _matches(goal, society)
            if tag is not None:
                activated.append(ActivatedSociety(society_id, f"tag_match:{tag}"))

        policy_bundle = self._merge_policies(activated)
        return SocietyActivationResult(
            actor_id=actor_id,
            goal=goal,
            activated=tuple(activated),
            policy_bundle=policy_bundle,
        )

    def _merge_policies(self, activated: list[ActivatedSociety]) -> PolicyBundle:
        all_policies: list[GovernancePolicy] = []
        for a in activated:
            society_runtime = self._society_lookup(a.society_id)
            if society_runtime is None:
                continue
            all_policies.extend(society_runtime.governance.policies())

        # Highest priority first; stable sort preserves activation order as
        # the tiebreak for equal priorities.
        all_policies.sort(key=lambda p: p.priority, reverse=True)

        seen_names: set[str] = set()
        deduped: list[GovernancePolicy] = []
        for policy in all_policies:
            key = policy.name or policy.policy_id
            if key in seen_names:
                continue
            seen_names.add(key)
            deduped.append(policy)

        return PolicyBundle(
            activated_society_ids=tuple(a.society_id for a in activated),
            policies=tuple(deduped),
        )

    def check_permission(self, actor_id: str, resource: str, action: str, goal: str = "") -> bool:
        """Aggregate permission check across activated societies: walk them
        in precedence order (priority of their highest-priority policy,
        descending — approximated here by re-checking activation order,
        since activation itself has no priority; a future refinement could
        rank societies by their own policies' max priority), returning the
        first explicit entry found. Deny (matching today's single-society
        default-deny semantics) if no activated society has an opinion."""
        result = self.activate_for_goal(actor_id, goal)
        for activated_society in result.activated:
            society_runtime = self._society_lookup(activated_society.society_id)
            if society_runtime is None:
                continue
            governance = society_runtime.governance
            if governance.has_permission_entry(actor_id, resource, action):
                return governance.check_permission(actor_id, resource, action)
        return False
