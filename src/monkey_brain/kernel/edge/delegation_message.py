"""Delegation extraction at the live agent-to-agent message boundary
(Sections 4/6 of the edge gap-closure pass).

This is the ONE place a signed delegation chain riding on an inbound NATS
message (kernel/domains/grocery.py::subscribe_actor_inbox's `_on_message`)
is turned into a TRUSTED `context["verified_delegation"]` value --
action_executor.py already reads that key (see its own long comment at
the "Portable Delegation integration point") but, until this module,
nothing ever populated it from a live message. Nothing else needs to
touch delegation for this reason.

Non-negotiables, all satisfied by delegating to the EXISTING primitives
rather than re-implementing any of them:

  * The chain is read from a STRUCTURED `delegation_chain` field, never
    from `parameters` or any other agent-authored free-form content.
  * Verification is exactly kernel/delegation.py::verify_delegation_chain
    -- signature, attenuation, expiry, audience, revocation, depth are
    ALL already enforced there; this module adds no second verifier.
  * `authenticated_delegate` is the RESPONDING actor's own bound identity
    (already established via SPIFFE/trusted_auth before this runs, per
    subscribe_actor_inbox's own comment) -- never a value read out of the
    message itself. This is the delegate/identity binding check.
  * A malformed or failed-verification chain is a hard rejection when a
    chain was supplied -- never silently dropped and never a scope that
    quietly proceeds ungoverned instead.
  * No delegation chain present at all is NOT an error -- most
    delegated_task messages carry none today and rely on whatever
    authority the responding actor already has; this module only acts
    when the field is actually present.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DelegationExtractionResult:
    present: bool
    """True iff the message actually carried a delegation_chain field."""
    verified: bool
    """True iff present AND verify_delegation_chain authorized it."""
    verified_delegation: dict[str, Any] | None
    """The trusted OPA-shaped context (to_opa_delegation_context output) --
    only ever non-None when verified is True."""
    chain: tuple[Any, ...] = ()
    """The raw, parsed DelegationCredential tuple -- only ever non-empty
    when verified is True. Handed to LocalGovernanceEvaluator.evaluate()
    (kernel/edge/local_governance.py), which independently re-verifies it
    itself; this is never trusted a second time on that say-so alone."""
    denial_reason: str = ""


def extract_and_verify_delegation(
    payload: dict[str, Any], *, authenticated_delegate: str, now: float | None = None,
) -> DelegationExtractionResult:
    """`payload` is the already-JSON-decoded inbound message body.
    Expects, when present, `payload["delegation_chain"]` to be a list of
    dicts each shaped like DelegationCredential.to_dict()'s output, root
    first (chain[0].parent_delegation_id == "")."""
    raw_chain = payload.get("delegation_chain")
    if not raw_chain:
        return DelegationExtractionResult(present=False, verified=False, verified_delegation=None)

    from src.monkey_brain.kernel.delegation import (
        DelegationCredential,
        DelegationError,
        get_delegation_store,
        to_opa_delegation_context,
        verify_delegation_chain,
    )

    if not isinstance(raw_chain, list):
        return DelegationExtractionResult(
            present=True, verified=False, verified_delegation=None,
            denial_reason="delegation_chain must be a list",
        )

    try:
        chain = tuple(DelegationCredential.from_dict(hop) for hop in raw_chain)
    except (DelegationError, TypeError, ValueError, AttributeError) as exc:
        return DelegationExtractionResult(
            present=True, verified=False, verified_delegation=None,
            denial_reason=f"malformed delegation_chain: {exc}",
        )

    result = verify_delegation_chain(
        chain=chain, authenticated_delegate=authenticated_delegate,
        is_revoked=get_delegation_store().is_revoked, now=now,
    )
    if not result.authorized:
        return DelegationExtractionResult(
            present=True, verified=False, verified_delegation=None,
            denial_reason=result.failure_reason or "delegation chain verification failed",
        )

    return DelegationExtractionResult(
        present=True, verified=True,
        verified_delegation=to_opa_delegation_context(chain), chain=chain,
    )
