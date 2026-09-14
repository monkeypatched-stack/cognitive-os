"""Shared, actor_id-based reachability lookup — who a given actor can
legitimately reach, via either a shared active SocietyMembershipRegistry
membership or a personal/organizational AffiliationGraph edge (family,
employment, customer/supplier, etc.). The exact same reachability
resolve_communication (AskActorCapability's own eligibility check) honors.

Two real callers share this single source of truth rather than each
re-deriving their own version:
  - context_engine.py::_retrieve_reachable_colleagues — surfaces this list
    (name + actor_id) to the LLM planner's prompt, so it can write a real,
    unambiguous actor_id for AskActor/BroadcastToAffiliation instead of
    reconstructing an exact display-name string.
  - grocery.py::AskActorCapability — falls back to this same list to
    resolve a short/partial name (e.g. "Raj") the model or a caller wrote
    instead of the exact actor_id or full name, scoped to who the asker
    can ACTUALLY reach (not a global, ambiguous name search).
"""

from __future__ import annotations

from typing import Any


def reachable_colleagues(pr: Any, actor_id: str) -> tuple[dict[str, Any], ...]:
    if pr is None:
        return ()
    seen: dict[str, dict[str, Any]] = {}

    registry = getattr(pr, "membership_registry", None)
    if registry is not None:
        for m in registry.memberships_for_actor(actor_id):
            if not m.is_active():
                continue
            society_runtime = pr.get_society_runtime(m.society_id)
            if society_runtime is None:
                continue
            for state in society_runtime.active_actors():
                other_id = state.actor_id
                if other_id == actor_id or other_id in seen:
                    continue
                seen[other_id] = {
                    "actor_id": other_id,
                    "name": state.profile.identity.name,
                    "society_name": society_runtime.society.name,
                }

    # Personal/organizational AffiliationGraph edges are a SEPARATE
    # reachability path from shared society membership above —
    # resolve_communication honors both. Checks EVERY society this actor
    # is registered in, not just the first match: each SocietyRuntime
    # holds its own separate ActorState/actor_runtime instance for the
    # same actor_id, and affiliations.all() can come back empty on
    # whichever copy happens to be checked first (e.g. the generic
    # bootstrap "Default Society" instance) even though the actor's real
    # affiliations were populated on a DIFFERENT society's copy.
    for sr in pr.all_societies():
        state = sr.get_actor(actor_id)
        if state is None:
            continue
        affiliations = state.actor_runtime.affiliations if state.actor_runtime is not None else None
        if affiliations is None:
            continue
        for a in affiliations.all():
            other_id = a.target_id
            if not other_id or other_id == actor_id or other_id in seen:
                continue
            target_is_live_actor = any(other_sr.get_actor(other_id) is not None for other_sr in pr.all_societies())
            if not target_is_live_actor:
                continue  # target_id doesn't resolve to a real, live actor
            seen[other_id] = {
                "actor_id": other_id,
                "name": a.target_name,
                "society_name": a.category or "affiliation",
            }
    return tuple(seen.values())
