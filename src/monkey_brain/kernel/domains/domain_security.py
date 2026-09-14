"""Domain-agnostic governance/security core, shared across domain modules.

Everything in this module was originally built and proven inside the
grocery domain (kernel/domains/grocery.py, Levels 34-48 of that
benchmark), but none of it actually reasons about groceries: it operates
on generic KG entities, accounts, organizations, and actors. Grocery was
the fixture used to prove these mechanisms real; this module is the
mechanism itself, extracted so a second domain module (or a dozen) can
reuse the identical AUTHZ / policy / governance / delegation / RBAC /
ABAC / separation-of-duties / audit / tenant-isolation / runtime-
integrity core without depending on anything grocery-specific.

Naming note: this is intentionally NOT named governance.py, security.py,
audit.py, or compliance.py — kernel/governance.py (RuntimeCharter /
GovernanceEngine, live in /plan /execute /predict /query), kernel/
security.py (input validation / rate limiting), kernel/audit.py
(immutable audit log), and kernel/compliance.py (GDPR/HIPAA hooks) are
all pre-existing, unrelated production modules with similar-sounding
names. This module lives under kernel/domains/ specifically because its
audience is domain modules (grocery.py and whatever comes after it), not
the platform's top-level infrastructure.

Sections, in the order they were originally built:
  - AUTHZ (payment-source / resource-permission authorization)
  - Delegation (act-on-behalf-of grants, expiry, revocation)
  - Constitutional floor + Policy (household-style declarative rules,
    versioned, governance-gated, with a hard-coded, non-KG-mutable floor
    no policy or exception can ever override)
  - Privacy helper (same-tenant relationship check)
  - Enterprise RBAC + tenant-scoped roles (multi-tenant-safe)
  - ABAC (dynamic attribute-based rules)
  - Audit (tamper-evident, hash-chained event log)
  - Runtime integrity (capability-manifest and plan-signature checks)
  - Separation of duties (request/approve, single-use, tenant-scoped)
"""

from __future__ import annotations

import hashlib
import re
import time

# ── AUTHZ ─────────────────────────────────────────────────────────────

_TARGET_WALLET_RE = re.compile(r"using (\w+)'s wallet", re.IGNORECASE)


def parse_target_payment_actor(question: str) -> str | None:
    """Does the request explicitly ask to pay from a DIFFERENT, named
    actor's wallet? Returns that actor_id, or None for the overwhelmingly
    common "pay for myself" case — the absence of this phrase is not
    itself suspicious, most requests never name a payer at all.
    """
    m = _TARGET_WALLET_RE.search(question)
    return m.group(1).lower() if m else None


def authorize_payment_source(
    authenticated_actor_id: str, target_actor_id: str | None
) -> dict:
    """An authenticated actor may pay from their OWN identity, or a REAL
    shared household/tenant they're actually a member of — never another
    individual's personal identity just because a request happens to name
    them. Checked against the AUTHENTICATED actor_id (sourced from the
    JWT's verified `sub` claim, never a request-supplied field) — asking
    to be someone else must never make you them; that's the entire point
    of AUTHZ existing on top of AUTHN.
    """
    if target_actor_id is None or target_actor_id == authenticated_actor_id:
        return {"authorized": True, "reason": "own account"}
    from src.monkey_brain.kernel.knowledge_graph_neo4j import resolve_household_id

    own_household = resolve_household_id(authenticated_actor_id)
    target_household = resolve_household_id(target_actor_id)
    if own_household is not None and own_household == target_household:
        return {"authorized": True, "reason": "shared household membership"}
    return {
        "authorized": False,
        "reason": f"{authenticated_actor_id} is not authorized to use {target_actor_id}'s personal account",
    }


def authorize_resource_access(actor_permissions, resource) -> dict:
    """A specially-flagged resource (attributes["restricted_permission"]
    naming the real permission required) needs that EXACT permission from
    the actor's own verified JWT claims, not just being logged in at all.
    actor_permissions is None when no real permission enforcement is
    active (auth disabled / dev mode) — treated as permissive; that is
    NOT the same as "verified and grants nothing" (an empty set), which
    correctly still denies.
    """
    required_permission = resource.attributes.get("restricted_permission")
    if not required_permission:
        return {"authorized": True, "reason": "not a restricted resource"}
    if actor_permissions is None:
        return {
            "authorized": True,
            "reason": "no permission enforcement active (dev mode)",
        }
    if required_permission in actor_permissions:
        return {"authorized": True, "reason": f"has {required_permission}"}
    return {
        "authorized": False,
        "reason": f"restricted resource requires {required_permission!r}, which this actor's token does not grant",
    }


# ── Delegation ────────────────────────────────────────────────────────


def _delegation_id(delegator_id: str, delegate_id: str) -> str:
    return f"delegation_{delegator_id}_{delegate_id}"


def grant_delegation(kg, delegator_id: str, delegate_id: str, expires_at: float) -> str:
    """A real, KG-persisted delegation grant — acting "on someone's
    behalf" is only ever real because THIS record exists, is still
    active, and hasn't expired; there is no other path to borrowing
    someone else's authority.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    delegation_id = _delegation_id(delegator_id, delegate_id)
    kg.add_entity(
        delegation_id,
        EntityType.OTHER,
        f"{delegator_id} delegates to {delegate_id}",
        {
            "delegation": True,
            "delegator_id": delegator_id,
            "delegate_id": delegate_id,
            "granted_at": time.time(),
            "expires_at": expires_at,
            "revoked": False,
            "household": True,
        },
    )
    return delegation_id


def revoke_delegation(kg, delegator_id: str, delegate_id: str) -> bool:
    """Revokes a real delegation immediately — a LATER re-check of
    check_delegation must see it as inactive right away (via
    kg.refresh()), since revocation is meant to take effect the moment it
    happens, not only at the end of whatever request happened to already
    be in flight.
    """
    entity = kg.get_entity(_delegation_id(delegator_id, delegate_id))
    if entity is None:
        return False
    kg.update_entity(entity.entity_id, attributes={"revoked": True})
    return True


def check_delegation(delegator_id: str, delegate_id: str, kg=None) -> dict:
    """Is there a REAL, currently-active delegation from delegator_id to
    delegate_id right now? Re-hydrates fresh (kg.refresh()) so a
    revocation that happened moments ago is seen immediately rather than
    from a stale in-memory snapshot.

    A delegation grant is written into the DELEGATOR's own tenant
    partition (grant_delegation is called with the delegator's kg), so it
    must be looked up there too — the delegate is very often in a
    completely different tenant (that's the whole point of delegation:
    the delegate doesn't need to share the delegator's tenant to act for
    them). The caller's own kg is ignored in favor of a KG scoped to
    delegator_id/its real household, so this works regardless of what KG
    the caller happened to have on hand.

    kg: when Neo4j is unavailable, there is no separate per-tenant store
    to fetch the delegator's partition from — the given kg (the caller's
    own graph) is checked directly instead. This is only correct because
    grant_delegation, offline, has nowhere else to write a delegation
    EXCEPT into whatever kg the caller passed it (see this module's
    household_role/org_role fallback for the identical reasoning); a real
    Neo4j deployment still uses the genuinely tenant-scoped lookup above.
    """
    from src.monkey_brain.kernel.knowledge_graph_neo4j import (
        Neo4jBackedKnowledgeGraph,
        resolve_household_id,
        _default_driver,
    )

    if _default_driver() is None:
        if kg is None:
            return {"active": False, "reason": "no delegation exists"}
        entity = kg.get_entity(_delegation_id(delegator_id, delegate_id))
    else:
        delegator_kg = Neo4jBackedKnowledgeGraph(
            person_id=delegator_id, household_id=resolve_household_id(delegator_id)
        )
        delegator_kg.refresh()
        entity = delegator_kg.get_entity(_delegation_id(delegator_id, delegate_id))
    if entity is None:
        return {"active": False, "reason": "no delegation exists"}
    if entity.attributes.get("revoked"):
        return {"active": False, "reason": "delegation was revoked"}
    if entity.attributes.get("expires_at", 0) < time.time():
        return {"active": False, "reason": "delegation has expired"}
    return {"active": True, "reason": "delegation active"}


_DELEGATION_RE = re.compile(r"on behalf of (\w+)", re.IGNORECASE)


def parse_delegator(question: str) -> str | None:
    """Does the request explicitly claim to be acting on behalf of a
    different, named actor? Returns that actor_id, or None for the
    overwhelmingly common case of acting on one's own behalf."""
    m = _DELEGATION_RE.search(question)
    return m.group(1).lower() if m else None


# ── Constitutional floor + Policy ────────────────────────────────────

_CONSTITUTIONAL_PROTECTED_CATEGORIES = frozenset({"controlled_substance"})
"""A genuinely hard floor, not KG-mutable data — no policy entity, no
owner action, no governance channel anywhere can add, remove, or override
membership in this set, because nothing reads or writes it except the two
enforcement points that consult it (a domain's own product-filtering
logic, and grant_category_ban_exception's refusal below). Changing what's
constitutionally protected requires changing this source file itself, not
calling any function available to any actor at runtime — the same
distinction a real constitution draws against ordinary legislation.
"""


def _policy_id(rule_type: str) -> str:
    return f"policy_{rule_type}"


def find_household_policy(kg, rule_type: str):
    """The currently active policy entity for rule_type in this kg's
    tenant scope, or None if the tenant never set one — the overwhelmingly
    common case, and every existing call site must behave EXACTLY as
    before when this returns None."""
    return kg.get_entity(_policy_id(rule_type))


def set_household_policy(kg, rule_type: str, params: dict, created_by: str) -> dict:
    """A real, KG-persisted tenant policy — a declarative rule enforced on
    EVERY request regardless of who's acting, unlike AUTHZ which only
    gates a specific actor/resource pair. Setting a new policy of the same
    rule_type preserves the previous version in "history" instead of
    silently overwriting it — governance changes are provable, not just
    effective.

    Gated by governance: only a real tenant "owner" (household_role) may
    set or change policy — a plain member attempting to do so is denied,
    since letting anyone weaken a tenant's own rules would make the rule
    meaningless.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    from src.monkey_brain.kernel.knowledge_graph_neo4j import household_role

    if household_role(created_by, kg=kg) != "owner":
        return {
            "success": False,
            "error": f"governance denied: {created_by} is not the household owner and cannot set policy",
        }

    policy_id = _policy_id(rule_type)
    existing = kg.get_entity(policy_id)
    history = list(existing.attributes.get("history", [])) if existing else []
    if existing:
        history.append(
            {
                "params": existing.attributes.get("params"),
                "created_by": existing.attributes.get("created_by"),
                "created_at": existing.attributes.get("created_at"),
            }
        )
    kg.add_entity(
        policy_id,
        EntityType.OTHER,
        f"Household policy: {rule_type}",
        {
            "policy": True,
            "rule_type": rule_type,
            "params": params,
            "created_by": created_by,
            "created_at": time.time(),
            "history": history,
            "household": True,
        },
    )
    return {"success": True, "policy_id": policy_id, "version": len(history) + 1}


def grant_policy_exception(kg, rule_type: str, approved_by: str, reason: str) -> dict:
    """A one-time override for an active policy — only the real tenant
    owner may approve one, and it's consumed by the NEXT enforcement
    check it unblocks (see consume_policy_exception), not a standing
    bypass. The policy re-applies to every action after this one.
    """
    from src.monkey_brain.kernel.knowledge_graph_neo4j import household_role

    if household_role(approved_by, kg=kg) != "owner":
        return {
            "success": False,
            "error": f"governance denied: {approved_by} is not the household owner and cannot approve a policy exception",
        }
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    exception_id = f"policy_exception_{rule_type}"
    kg.add_entity(
        exception_id,
        EntityType.OTHER,
        f"Policy exception: {rule_type}",
        {
            "policy_exception": True,
            "rule_type": rule_type,
            "approved_by": approved_by,
            "reason": reason,
            "granted_at": time.time(),
            "consumed": False,
            "household": True,
        },
    )
    return {"success": True, "exception_id": exception_id}


def consume_policy_exception(kg, rule_type: str) -> bool:
    """Consumes an active, unconsumed exception for rule_type if one
    exists — True means this ONE enforcement check should be let through.
    Single-use: calling this again for the same grant returns False, so
    the policy goes back to blocking the next action."""
    entity = kg.get_entity(f"policy_exception_{rule_type}")
    if entity is None or entity.attributes.get("consumed"):
        return False
    kg.update_entity(entity.entity_id, attributes={"consumed": True})
    return True


def grant_category_ban_exception(
    kg, product_id: str, approved_by: str, reason: str
) -> dict:
    """A specific, governance-approved carve-out from an active
    category_ban policy — ONE named resource is let through despite the
    blanket ban, while every other resource in the banned category
    remains blocked. "Specific override beats general policy" is the real
    conflict-resolution rule here, and it's still governance-gated (same
    household_role check as ordinary policy/exception approvals) — a
    specific override is a policy change, not a self-service bypass.

    Checked BEFORE the ordinary governance/ownership gate, and with no
    owner override at all — a constitutionally-protected category can
    never be exempted by ANY authority, including the real tenant owner,
    because this isn't a policy decision the tenant's own governance
    channel has jurisdiction over in the first place.
    """
    product = kg.get_entity(product_id)
    if (
        product is not None
        and product.attributes.get("category") in _CONSTITUTIONAL_PROTECTED_CATEGORIES
    ):
        return {
            "success": False,
            "error": f"constitutional violation: {product.attributes.get('category')!r} cannot be exempted "
            f"from its category ban by any authority, including the household owner",
        }
    from src.monkey_brain.kernel.knowledge_graph_neo4j import household_role

    if household_role(approved_by, kg=kg) != "owner":
        return {
            "success": False,
            "error": f"governance denied: {approved_by} is not the household owner and cannot grant a policy exception",
        }
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    exception_id = f"policy_allow_exception_{product_id}"
    kg.add_entity(
        exception_id,
        EntityType.OTHER,
        f"Category-ban allow exception: {product_id}",
        {
            "policy_allow_exception": True,
            "product_id": product_id,
            "approved_by": approved_by,
            "reason": reason,
            "granted_at": time.time(),
            "household": True,
        },
    )
    return {"success": True, "exception_id": exception_id}


# ── Privacy ───────────────────────────────────────────────────────────


def is_same_household(actor_a: str, actor_b: str) -> bool:
    """Are these two real, distinct actors members of the same real
    tenant? The same relationship that already governs payment-source
    authorization, reused here for what's SHOWN in a response rather than
    what's spent."""
    if actor_a == actor_b:
        return True
    from src.monkey_brain.kernel.knowledge_graph_neo4j import resolve_household_id

    household_a = resolve_household_id(actor_a)
    household_b = resolve_household_id(actor_b)
    return household_a is not None and household_a == household_b


# ── Enterprise RBAC + tenant-scoped roles ────────────────────────────

_ENTERPRISE_ROLE_RANK = {"staff": 0, "procurement_manager": 1, "admin": 2}
_ENTERPRISE_APPROVAL_THRESHOLD = 100.0


def _tenant_scoped_org_key(kg, org_id: str) -> str:
    """set_org_role/org_role are a bare, GLOBAL (person_id, org_id) key
    with no tenant boundary at all — confirmed live: two completely
    unrelated tenants that happen to pick the SAME org_id string (e.g.,
    both calling their org "acme") would silently see and grant each
    other's roles, since nothing distinguishes "acme" under one tenant
    from "acme" under another. household_id is the real tenant boundary
    this whole codebase already uses for storage (purchase requests and
    audit chains already live as entities INSIDE a specific tenant's own
    KG partition) — combining it with org_id here closes the one place
    (org_role's flat global namespace) that wasn't.
    """
    tenant_id = getattr(kg, "_household_id", None) or getattr(kg, "person_id", "")
    return f"{tenant_id}::{org_id}"


def set_tenant_org_role(kg, org_id: str, person_id: str, role: str) -> None:
    """The tenant-safe way to grant an enterprise role — always use this
    (never the bare set_org_role) from real application code."""
    from src.monkey_brain.kernel.knowledge_graph_neo4j import set_org_role

    set_org_role(person_id, _tenant_scoped_org_key(kg, org_id), role, kg=kg)


def tenant_org_role(kg, org_id: str, person_id: str) -> str:
    """The tenant-safe read of an enterprise role — used by
    enterprise_purchase_authorized and approve_purchase_request instead
    of the bare, cross-tenant-collidable org_role."""
    from src.monkey_brain.kernel.knowledge_graph_neo4j import org_role

    return org_role(person_id, _tenant_scoped_org_key(kg, org_id), kg=kg)


def enterprise_purchase_authorized(kg, actor_id: str, account, total: float) -> dict:
    """An action charged to a real enterprise account
    (attributes["account_type"] == "enterprise", attributes["org_id"]
    naming the real organization) above that account's approval threshold
    requires the ACTING user to hold at least "procurement_manager" rank
    in THAT org — role-based, distinct from AUTHZ (identity/tenant-based)
    and from a tenant POLICY (applies regardless of WHO the actor is).
    Below the threshold, any real assigned role (including the "staff"
    floor) is enough. An account that isn't enterprise-typed at all is
    always authorized — this check doesn't apply to it.

    The role check is tenant-scoped (tenant_org_role) — this org's
    "procurement_manager" can never be satisfied by a role granted under
    a different tenant's identically-named org_id.
    """
    if account is None or account.attributes.get("account_type") != "enterprise":
        return {"authorized": True, "reason": "not an enterprise account"}
    org_id = account.attributes.get("org_id")
    threshold = account.attributes.get(
        "approval_threshold", _ENTERPRISE_APPROVAL_THRESHOLD
    )
    role = tenant_org_role(kg, org_id, actor_id)
    if total <= threshold:
        return {
            "authorized": True,
            "reason": f"under the ${threshold:.2f} enterprise approval threshold (real role: {role!r})",
        }
    if (
        _ENTERPRISE_ROLE_RANK.get(role, -1)
        >= _ENTERPRISE_ROLE_RANK["procurement_manager"]
    ):
        return {
            "authorized": True,
            "reason": f"real role {role!r} has procurement authority",
        }
    return {
        "authorized": False,
        "reason": f"order total ${total:.2f} exceeds the ${threshold:.2f} enterprise approval threshold, "
        f"and {actor_id}'s real role ({role!r}) lacks procurement authority",
    }


# ── ABAC ──────────────────────────────────────────────────────────────


def _abac_condition_met(actor_attributes: dict, rule: dict) -> bool:
    attr = rule.get("attribute")
    op = rule.get("op", "eq")
    required = rule.get("value")
    actual = actor_attributes.get(attr)
    if actual is None:
        return False
    if op == "gte":
        return actual >= required
    if op == "eq":
        return actual == required
    return False


def authorize_abac_access(actor_attributes, resource) -> dict:
    """A resource may declare one or more REAL attribute-based rules
    (attributes["abac_rules"], a list of {"attribute", "op", "value"}
    conditions) that the ACTOR's own verified attributes (from their JWT,
    never request-supplied) must ALL satisfy — a dynamic, per-request
    comparison, distinct from a fixed named permission or a role's rank.
    actor_attributes is None when no real attribute enforcement is active
    (dev mode / no JWT) — treated as permissive; an empty dict {} (a
    verified token with no attributes claim at all) is NOT the same thing
    and correctly still fails a real rule.
    """
    rules = resource.attributes.get("abac_rules")
    if not rules:
        return {"authorized": True, "reason": "no ABAC rule on this resource"}
    if actor_attributes is None:
        return {
            "authorized": True,
            "reason": "no attribute enforcement active (dev mode)",
        }
    for rule in rules:
        if not _abac_condition_met(actor_attributes, rule):
            return {
                "authorized": False,
                "reason": f"attribute {rule.get('attribute')!r} does not satisfy "
                f"{rule.get('op')} {rule.get('value')!r} "
                f"(actor has {actor_attributes.get(rule.get('attribute'))!r})",
            }
    return {"authorized": True, "reason": "all real attribute rules satisfied"}


# ── Audit ─────────────────────────────────────────────────────────────


def _audit_hash(
    prev_hash: str,
    action: str,
    actor_id: str,
    outcome: str,
    reason: str,
    timestamp: float,
) -> str:
    payload = f"{prev_hash}|{action}|{actor_id}|{outcome}|{reason}|{timestamp}"
    return hashlib.sha256(payload.encode()).hexdigest()


def record_audit_event(
    kg, org_id: str, action: str, actor_id: str, outcome: str, reason: str
) -> dict:
    """A real, KG-persisted, tamper-evident audit trail for security-
    relevant DECISIONS. Each entry is chained via a real SHA-256 hash of
    the previous entry's own hash plus this entry's fields, so altering,
    deleting, or reordering a past record changes what the NEXT entry's
    hash needed to have been — verify_audit_chain detects exactly that,
    rather than trusting the log was never touched. Scoped per org_id (a
    household is just as valid an org_id as an enterprise one) so
    unrelated orgs' chains never interleave.
    """
    kg.refresh()
    chain = sorted(
        (
            e
            for e in kg.entities
            if e.attributes.get("audit_event") and e.attributes.get("org_id") == org_id
        ),
        key=lambda e: e.attributes.get("sequence", 0),
    )
    prev_hash = chain[-1].attributes.get("hash", "genesis") if chain else "genesis"
    sequence = (chain[-1].attributes.get("sequence", 0) + 1) if chain else 1
    timestamp = time.time()
    this_hash = _audit_hash(prev_hash, action, actor_id, outcome, reason, timestamp)
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    event_id = f"audit_{org_id}_{sequence}"
    kg.add_entity(
        event_id,
        EntityType.OTHER,
        f"Audit: {action}",
        {
            "audit_event": True,
            "org_id": org_id,
            "sequence": sequence,
            "action": action,
            "actor_id": actor_id,
            "outcome": outcome,
            "reason": reason,
            "timestamp": timestamp,
            "prev_hash": prev_hash,
            "hash": this_hash,
            "household": True,
        },
    )
    return {"event_id": event_id, "sequence": sequence, "hash": this_hash}


def verify_audit_chain(kg, org_id: str) -> dict:
    """Walks the real chain in sequence order and recomputes each entry's
    hash from its own recorded fields plus the PREVIOUS entry's real
    (stored) hash — if any past record's fields were altered, or an entry
    was deleted/reordered, the recomputed hash won't match what's stored
    there, and this reports exactly where the chain broke rather than
    silently accepting whatever is currently in the graph.
    """
    kg.refresh()
    chain = sorted(
        (
            e
            for e in kg.entities
            if e.attributes.get("audit_event") and e.attributes.get("org_id") == org_id
        ),
        key=lambda e: e.attributes.get("sequence", 0),
    )
    prev_hash = "genesis"
    for e in chain:
        expected = _audit_hash(
            prev_hash,
            e.attributes.get("action"),
            e.attributes.get("actor_id"),
            e.attributes.get("outcome"),
            e.attributes.get("reason"),
            e.attributes.get("timestamp"),
        )
        if expected != e.attributes.get("hash"):
            return {
                "valid": False,
                "broken_at": e.entity_id,
                "sequence": e.attributes.get("sequence"),
            }
        prev_hash = e.attributes.get("hash")
    return {"valid": True, "length": len(chain)}


# ── Runtime integrity ─────────────────────────────────────────────────


def verify_required_capabilities(bus, required_names) -> dict:
    """Fail CLOSED if the pipeline's own capability bus is missing a
    security-critical capability — a configuration-drift regression
    (someone forgets to register a delegation/authorization capability
    after a refactor) must refuse to process ANY request rather than
    silently running every request through a pipeline with security
    checks quietly absent. Checked against the REAL bus about to execute
    THIS specific request, not a static assumption made once at import
    time. required_names is domain-specific (the exact capability names a
    given domain's pipeline registers) and must always be passed
    explicitly — there is no universal default across domains.
    """
    missing = [name for name in required_names if bus.discover(name) is None]
    if missing:
        return {
            "integrity_ok": False,
            "missing": missing,
            "reason": f"required security capabilities not registered: {missing}",
        }
    return {"integrity_ok": True, "missing": []}


def compute_plan_signature(plan) -> str:
    """A real signature over a Plan's ordered step sequence — computed
    once right after a planner builds it, and re-verified right before
    execution begins (verify_plan_signature). If the step sequence
    differs from what was originally planned (a security-critical step
    silently dropped, steps reordered, an extra step spliced in) between
    build and execution, the recomputed signature won't match, and the
    runtime can refuse to execute rather than blindly trusting whatever
    Plan object it was handed.
    """
    step_sequence = "|".join(step.action for step in plan.steps)
    return hashlib.sha256(step_sequence.encode()).hexdigest()


def verify_plan_signature(plan, signature: str) -> bool:
    return compute_plan_signature(plan) == signature


# ── Separation of duties ──────────────────────────────────────────────


def create_purchase_request(
    kg, org_id: str, requested_by: str, description: str, amount: float
) -> dict:
    """A real, KG-persisted request for an enterprise action above the
    approval threshold — the first half of separation of duties: whoever
    wants to spend the money must ask for it, they cannot also be the one
    who signs off on it (approve_purchase_request enforces that half).
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    request_id = f"purchase_request_{org_id}_{int(time.time() * 1000)}_{requested_by}"
    kg.add_entity(
        request_id,
        EntityType.OTHER,
        f"Purchase request: {description}",
        {
            "purchase_request": True,
            "org_id": org_id,
            "requested_by": requested_by,
            "description": description,
            "amount": amount,
            "requested_at": time.time(),
            "approved_by": None,
            "approved_at": None,
            "consumed": False,
            "household": True,
        },
    )
    return {"success": True, "request_id": request_id}


def approve_purchase_request(kg, request_id: str, approved_by: str) -> dict:
    """Approves a real purchase request. Real separation of duties
    requires the approver to (a) hold at least procurement_manager rank
    in the SAME org, AND (b) be a genuinely DIFFERENT person from whoever
    requested it — self-approval is denied outright regardless of role,
    since a requester who can also be the sole approver has defeated the
    entire point of a second, independent sign-off.

    Every outcome here — self-approval attempt, insufficient rank, or a
    real approval — is recorded in the org's real audit chain, not just
    returned in this dict and forgotten.
    """
    entity = kg.get_entity(request_id)
    if entity is None:
        return {"success": False, "error": f"no such purchase request: {request_id}"}
    org_id = entity.attributes.get("org_id")
    if entity.attributes.get("requested_by") == approved_by:
        reason = f"{approved_by} attempted to approve their own request ({request_id})"
        record_audit_event(
            kg, org_id, "approve_purchase_request", approved_by, "deny", reason
        )
        return {"success": False, "error": f"separation of duties violation: {reason}"}
    role = tenant_org_role(kg, org_id, approved_by)
    if (
        _ENTERPRISE_ROLE_RANK.get(role, -1)
        < _ENTERPRISE_ROLE_RANK["procurement_manager"]
    ):
        reason = f"{approved_by}'s real role ({role!r}) in {org_id} lacks procurement authority to approve {request_id}"
        record_audit_event(
            kg, org_id, "approve_purchase_request", approved_by, "deny", reason
        )
        return {"success": False, "error": reason}
    kg.update_entity(
        request_id, attributes={"approved_by": approved_by, "approved_at": time.time()}
    )
    record_audit_event(
        kg,
        org_id,
        "approve_purchase_request",
        approved_by,
        "allow",
        f"{approved_by} approved {request_id} (requested by {entity.attributes.get('requested_by')})",
    )
    return {"success": True, "request_id": request_id, "approved_by": approved_by}


def find_approved_purchase_request(
    kg, org_id: str, requested_by: str, item_names: list | None = None
):
    """The most recent real, approved-by-someone-else, not-yet-consumed
    purchase request from requested_by in org_id, or None. Re-hydrates
    first (kg.refresh()) so an approval or a prior consumption made
    moments ago in a different KG instance is seen immediately.

    Caught live: matching on (org_id, requested_by) alone let a stale,
    still-unconsumed approval for one item (left behind by an EARLIER
    request that got short-circuited before ever reaching the actual
    charge) silently authorize a COMPLETELY UNRELATED later action that
    happened to cost the same amount. When item_names is given (the real
    action's actual line-item names), a candidate must also keyword-match
    the request's own description — tying the approval to what was
    actually requested, not just a coincidental dollar figure. Left
    permissive (unfiltered) when item_names is empty/None, since not
    every caller has real item names on hand to check against.
    """
    kg.refresh()
    candidates = [
        e
        for e in kg.entities
        if e.attributes.get("purchase_request")
        and e.attributes.get("org_id") == org_id
        and e.attributes.get("requested_by") == requested_by
        and e.attributes.get("approved_by") is not None
        and not e.attributes.get("consumed")
    ]
    if item_names:
        # Lazy import to avoid a circular dependency: grocery.py imports
        # this module at load time, so a top-level import back the other
        # way would deadlock the import machinery. By call time both
        # modules are already fully loaded.
        from src.monkey_brain.kernel.domains.grocery import _matches_request

        candidates = [
            e
            for e in candidates
            if any(
                _matches_request(name, e.attributes.get("description", ""))
                for name in item_names
            )
        ]
    return max(
        candidates, key=lambda e: e.attributes.get("approved_at", 0), default=None
    )


def consume_purchase_request(kg, request_id: str) -> None:
    """Marks a request used — single-use, so an approved request covers
    exactly ONE action, not a standing authorization for the requester to
    keep spending against."""
    kg.update_entity(request_id, attributes={"consumed": True})


def separation_of_duties_satisfied(
    kg, actor_id: str, account, total: float, order: dict | None = None
) -> dict:
    """For an action charged to a real enterprise account above its
    approval threshold, the ACTING user cannot be their own approver — a
    real, separately-APPROVED purchase request from someone else,
    matching this action's amount, must exist. This is checked IN
    ADDITION TO the RBAC rank check, not instead of it — even a
    procurement_manager cannot unilaterally clear their own large action;
    a genuinely separate sign-off is still required. Opt-in via
    attributes["separation_of_duties"] is True on the account (default
    False) — an account that never set this keeps working exactly as RBAC
    rank alone already allows; this is a STRICTER tier an org can
    specifically choose for especially sensitive accounts, not a
    retroactive tightening of every existing one. A non-enterprise
    account, an account that hasn't opted in, or a total at/under the
    threshold, is unaffected.
    """
    if account is None or account.attributes.get("account_type") != "enterprise":
        return {"authorized": True, "reason": "not an enterprise account"}
    if not account.attributes.get("separation_of_duties"):
        return {
            "authorized": True,
            "reason": "this account has not opted into separation of duties",
        }
    org_id = account.attributes.get("org_id")
    threshold = account.attributes.get(
        "approval_threshold", _ENTERPRISE_APPROVAL_THRESHOLD
    )
    if total <= threshold:
        return {
            "authorized": True,
            "reason": "under the enterprise approval threshold — separation of duties doesn't apply",
        }
    item_names = [
        item.get("product", "")
        for item in (order or {}).get("items", [])
        if item.get("product")
    ]
    approved_request = (
        find_approved_purchase_request(kg, org_id, actor_id, item_names)
        if kg is not None
        else None
    )
    if approved_request is None:
        return {
            "authorized": False,
            "reason": f"order total ${total:.2f} exceeds the ${threshold:.2f} threshold and requires a "
            f"separately-approved purchase request (separation of duties) — none found for {actor_id}",
        }
    if abs(approved_request.attributes.get("amount", 0.0) - total) > 0.01:
        return {
            "authorized": False,
            "reason": f"the approved request is for ${approved_request.attributes.get('amount', 0.0):.2f}, "
            f"which doesn't match this order's ${total:.2f} total",
        }
    return {
        "authorized": True,
        "reason": f"approved by {approved_request.attributes['approved_by']} (separation of duties satisfied)",
        "request_id": approved_request.entity_id,
    }
