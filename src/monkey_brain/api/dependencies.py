"""Shared API dependencies — auth, database clients, etc."""

from __future__ import annotations

import logging
import os
from fastapi import Header, HTTPException, Request

logger = logging.getLogger(__name__)

_AUTH_DISABLED_WARNED = False


class RequestRejected(Exception):
    """Raised by sanitize_and_check_governance on a failed check.

    Carries enough structure (status_code, error_code, detail) for each
    caller to render it in its OWN existing response shape — different
    routes report failures differently (HTTPException for /simulate and
    /compare, JSONResponse for /execute, an SSE fatal frame for
    /execute/stream), and unifying those shapes would be an externally-
    visible API change, not just a refactor. This type lets the shared
    LOGIC be extracted without touching any of those shapes.
    """

    def __init__(self, status_code: int, error_code: str, detail: str):
        self.status_code = status_code
        self.error_code = error_code
        self.detail = detail
        super().__init__(f"{error_code}: {detail}")


async def sanitize_and_check_governance(
    question: str,
    user_id: str,
    action: str,
    extra_context: dict | None = None,
) -> str:
    """Shared input-sanitization + governance-check logic for /simulate,
    /compare, /execute, and /execute/stream (api/routes/predict.py,
    api/routes/execute.py) — previously hand-duplicated across all four
    (Gate 11 TODO audit: "This is not DRY" TODOs on 4 of the 5
    occurrences). Raises RequestRejected on failure; each caller catches
    it and formats its own existing response. Returns the sanitized
    question on success.
    """
    from src.monkey_brain.kernel.security import sanitize_input

    try:
        question = sanitize_input(question)
    except ValueError as e:
        raise RequestRejected(400, "invalid_input", str(e))

    try:
        from src.monkey_brain.kernel.governance import get_governance_engine
        from src.monkey_brain.kernel.trusted_auth import (
            get_trusted_auth,
            strip_untrusted_security_signals,
        )

        gov = get_governance_engine()
        trusted = get_trusted_auth().to_opa_auth()
        context = {"question": question[:200], "trusted_auth": trusted, "auth": trusted}
        if extra_context:
            context.update(strip_untrusted_security_signals(extra_context))
            context["trusted_auth"] = trusted
            context["auth"] = trusted
        gov_result = await gov.evaluate(user_id, action, context)
        if not gov_result.get("allowed"):
            raise RequestRejected(403, "governance_denied", gov_result.get("reason") or "")
    except RequestRejected:
        raise
    except ImportError:
        logger.error("Governance module not available — denying")
        raise RequestRejected(500, "governance_error", "Governance unavailable")
    except Exception as exc:
        logger.error("Governance check failed for action=%r — denying: %s", action, exc)
        raise RequestRejected(500, "governance_error", "Governance check failed")

    return question


def record_request_audit(user_id: str, event_type: str, action: str, details: dict) -> None:
    """Shared audit-record logic — fail-closed for security-critical event types."""
    from src.monkey_brain.kernel.audit import get_audit_log

    get_audit_log().record(
        runtime_id=user_id,
        event_type=event_type,
        action=action,
        actor=user_id,
        details=details,
    )


def auth_required() -> bool:
    """Whether authn/authz is enforced. Secure by default.

    AGENTOS_AUTH_REQUIRED=false is honored only under
    COGNITIVEOS_ALLOW_INSECURE_DEV_MODE. Production and any other
    deployment ignore the disable flag and keep the door locked.
    """
    global _AUTH_DISABLED_WARNED
    explicit_off = os.getenv("AGENTOS_AUTH_REQUIRED", "true").strip().lower() in (
        "false",
        "0",
        "no",
        "off",
    )
    if not explicit_off:
        return True
    from src.monkey_brain.kernel.production_gates import insecure_dev_mode

    if not insecure_dev_mode():
        logger.error(
            "AGENTOS_AUTH_REQUIRED=false ignored without "
            "COGNITIVEOS_ALLOW_INSECURE_DEV_MODE — authentication remains required",
        )
        return True
    if not _AUTH_DISABLED_WARNED:
        _AUTH_DISABLED_WARNED = True
        logger.warning(
            "AGENTOS_AUTH_REQUIRED is disabled — permission checks are bypassed. "
            "Never run this way outside local development.",
        )
    return False


async def get_current_user(
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
    authorization: str | None = Header(default=None),
) -> str:
    """Validate caller identity via Bearer token, then optional insecure-dev X-User-ID.

    X-User-ID never authenticates when auth_required() is true.
    """
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        api_key_valid = os.getenv("AGENTOS_API_KEY", "")
        if api_key_valid and token == api_key_valid:
            from src.monkey_brain.kernel.trusted_auth import (
                bind_trusted_auth,
                evidence_for_service,
            )

            svc_user = os.getenv("AGENTOS_API_USER", "api-service")
            bind_trusted_auth(evidence_for_service(svc_user))
            return svc_user
        try:
            from services.common.agent_auth import get_current_principal
            from src.monkey_brain.kernel.trusted_auth import (
                bind_trusted_auth,
                evidence_from_jwt,
            )

            class _FakeCreds:
                credentials = token
                scheme = "Bearer"

            principal = await get_current_principal(_FakeCreds())
            if principal:
                bind_trusted_auth(evidence_from_jwt(principal))
                return principal.get("user_id", principal.get("sub", "authenticated"))
        except Exception as e:
            logger.debug("JWT validation failed: %s", e)
            if auth_required():
                raise HTTPException(status_code=401, detail="Invalid or expired token")

    if x_user_id:
        if auth_required():
            raise HTTPException(status_code=401, detail="Bearer token required")
        return x_user_id

    if auth_required():
        raise HTTPException(status_code=401, detail="Missing X-User-ID header or Bearer token")

    return "anonymous"


import time as _time  # noqa: E402 -- co-located with the monitoring helper below that needs it

_FAILURE_WINDOW_SECONDS = 60.0
_FAILURE_THRESHOLD = 5
"""Doot audit P1-9 (minimal security monitoring): 5 denied auth attempts
by the same subject within 60s is treated as a suspicious pattern —
repeated authorization failures, repeated cross-actor access attempts,
and repeated policy violations all funnel through _audit_auth_failure,
so this one counter covers all three of the paper's listed patterns
without a separate detector per pattern."""
_recent_failures: dict[str, list[float]] = {}
_MAX_TRACKED_SUBJECTS = 10_000
"""Bounded so an attacker cycling through many fake subject strings can't
grow this dict without limit — oldest-inserted subject is evicted first
when the cap is hit, same trade-off an LRU-less bounded cache makes."""


def _record_failure_and_check_pattern(subject: str) -> bool:
    """Best-effort, in-memory only (no new datastore) — a process
    restart resets it, which is fine for "is this bursting right now,"
    not meant as a durable forensic record (that's audit_events.emit's
    job, called for every single denial regardless of pattern)."""
    if not subject:
        return False
    now = _time.time()
    if subject not in _recent_failures and len(_recent_failures) >= _MAX_TRACKED_SUBJECTS:
        oldest_subject = next(iter(_recent_failures), None)
        if oldest_subject is not None:
            _recent_failures.pop(oldest_subject, None)
    timestamps = [t for t in _recent_failures.get(subject, []) if now - t < _FAILURE_WINDOW_SECONDS]
    timestamps.append(now)
    _recent_failures[subject] = timestamps
    return len(timestamps) >= _FAILURE_THRESHOLD


async def _audit_auth_failure(permission: str, outcome: str, reason: str, subject: str = "") -> None:
    """Level 34 (GS-3402): a failed auth attempt against /prompt (or any
    require_permission-gated route) previously left no queryable trace at
    all — the audit framework already exists (services/common/audit_events
    -> src/introspection/audit.record, used by the separate agent-token
    path) but was never called from THIS function, the one every human-JWT
    route actually depends on. Best-effort: emit() never raises, so a
    logging/audit outage must never itself turn into an unrelated 500 on
    top of the real 401/403.

    Doot audit P1-9 fix: this is also the ONE chokepoint every denial
    reason this module produces already flows through (permission_denied,
    not_self_and_no_act_on_behalf_authority, token_revoked, bearer_required,
    missing_authentication, not_a_negotiation_counterparty, ...) — a
    minimal repeated-failure signal here covers "repeated authorization
    failures," "repeated cross-actor access attempts," and "repeated
    policy violations" all at once, without a bespoke detector per
    pattern.
    """
    pattern_detected = False
    try:
        from services.common import audit_events

        await audit_events.emit(
            "auth.denied",
            outcome,
            {"sub": subject, "principal_type": "human"} if subject else None,
            metadata={"reason": reason, "permission": permission},
        )
        pattern_detected = _record_failure_and_check_pattern(subject)
        if pattern_detected:
            logger.warning(
                "security.suspicious_pattern: subject=%r hit %d+ denied auth attempts (reason=%r) within %.0fs",
                subject,
                _FAILURE_THRESHOLD,
                reason,
                _FAILURE_WINDOW_SECONDS,
            )
            await audit_events.emit(
                "security.suspicious_pattern",
                "detected",
                {"sub": subject, "principal_type": "human"} if subject else None,
                metadata={
                    "pattern": "repeated_auth_denial",
                    "reason": reason,
                    "permission": permission,
                    "threshold": _FAILURE_THRESHOLD,
                    "window_seconds": _FAILURE_WINDOW_SECONDS,
                },
            )
    except Exception:
        logger.debug("auth-failure audit emit failed (non-fatal)", exc_info=True)

    # Real, queryable persistence (Doot audit follow-up: the pattern
    # detector above was fire-and-forget in-memory only — this is the
    # durable record GET /security/violations reads). Every denial is
    # stored, not just the ones that crossed the burst threshold, so an
    # isolated denial is still visible in the console, just not flagged
    # as a pattern.
    try:
        from src.monkey_brain.kernel.pipeline.violation_store import record_violation

        record_violation(
            subject=subject,
            permission=permission,
            reason=reason,
            outcome=outcome,
            pattern_detected=pattern_detected,
        )
    except Exception:
        logger.debug("violation persistence failed (non-fatal)", exc_info=True)


def _effective_delegated_permissions(request: Request, actor_id: str) -> frozenset[str]:
    """Doot audit P1-4 fix: DelegationRegistry.effective_delegated_permissions()
    (kernel/society/delegation.py) had zero callers at any real
    authorization chokepoint — a real, scoped, expiring/revocable
    delegation could be granted and would simply never be consulted.
    This is the one place both require_permission and
    require_self_or_permission now check it, so there's still exactly
    ONE authorization model, not a second one bolted on beside it.

    Best-effort and additive only: only ever WIDENS what the JWT's own
    `permissions` claim already grants, only reached when the base
    permission check has already failed, and never raises — any lookup
    failure here must fall through to the pre-existing deny, not become
    an unrelated 500. DelegationRegistry instances are society-scoped
    (kernel/society/integration.py), so this aggregates across every
    society the actor actually belongs to, the same _societies_for
    traversal kernel/society/transaction.py already uses for the same
    kind of actor-scoped lookup.
    """
    try:
        pr = getattr(request.app.state, "planetary_runtime", None)
        if pr is None:
            return frozenset()
        permissions: set[str] = set()
        for sr in pr._societies_for(actor_id):
            registry = getattr(sr, "delegation_registry", None)
            if registry is not None:
                permissions.update(registry.effective_delegated_permissions(actor_id))
        return frozenset(permissions)
    except Exception:
        logger.debug(
            "_effective_delegated_permissions(%s): suppressed exception",
            actor_id,
            exc_info=True,
        )
        return frozenset()


def require_permission(permission: str):
    """Gate an endpoint by permission name.

    Mirrors services/common/auth.py::require_permission exactly.
    Enforcement is ON by default (see auth_required): the Bearer JWT must carry
    the permission. Only when AGENTOS_AUTH_REQUIRED is explicitly set false does
    any caller with a valid identity pass, and X-User-ID alone authenticate.
    """

    async def _check(
        request: Request,
        x_user_id: str | None = Header(default=None, alias="X-User-ID"),
        authorization: str | None = Header(default=None),
    ) -> str:
        auth_on = auth_required()

        if authorization and authorization.startswith("Bearer "):
            token = authorization[7:]

            api_key = os.getenv("AGENTOS_API_KEY", "")
            if api_key and token == api_key:
                from src.monkey_brain.kernel.trusted_auth import (
                    bind_trusted_auth,
                    evidence_for_service,
                )

                svc_user = os.getenv("AGENTOS_API_USER", "api-service")
                bind_trusted_auth(evidence_for_service(svc_user))
                return svc_user

            try:
                from services.auth.helpers.tokens import decode_access_token

                payload = decode_access_token(token)
                from src.monkey_brain.kernel.production_gates import insecure_dev_mode

                try:
                    from services.auth.helpers.revocation import is_jti_revoked

                    revoked = await is_jti_revoked(payload.get("jti"))
                except Exception as exc:
                    if not insecure_dev_mode():
                        await _audit_auth_failure(permission, "deny", "revocation_unavailable")
                        raise HTTPException(status_code=503, detail="Revocation check unavailable") from exc
                    logger.warning("revocation check skipped (insecure-dev): %s", exc)
                    revoked = False
                if revoked:
                    await _audit_auth_failure(
                        permission,
                        "deny",
                        "token_revoked",
                        subject=payload.get("sub", ""),
                    )
                    raise HTTPException(status_code=401, detail="Token has been revoked")
                granted = {
                    p if isinstance(p, str) else p.get("permission_id", "") for p in payload.get("permissions", [])
                }
                # Level 35: the route handler needs the REAL verified
                # permission set (not just the single required one this
                # dependency checks) to make its own authorization
                # decisions — e.g. "does this actor's token carry
                # perm-emergency-food-access". request.state is additive:
                # every other caller of require_permission still gets
                # exactly the same `user` string return value it always
                # did; nothing about the existing 100+ call sites changes.
                request.state.jwt_permissions = granted
                # Level 41 (GS-4100/4101/4102): the same additive
                # threading as jwt_permissions above, for real per-actor
                # attributes (clearance_level, department, ...) ABAC needs
                # to evaluate a dynamic rule — never request-supplied,
                # only ever what the verified token itself carries.
                request.state.jwt_attributes = payload.get("attributes") or {}
                user = payload.get("sub", "authenticated")
                from src.monkey_brain.kernel.trusted_auth import (
                    bind_trusted_auth,
                    evidence_from_jwt,
                    mfa_allows_operation,
                )

                evidence = evidence_from_jwt(payload)
                bind_trusted_auth(evidence)
                request.state.trusted_auth = evidence
                if not mfa_allows_operation(evidence):
                    await _audit_auth_failure(permission, "deny", "mfa_required", subject=user)
                    raise HTTPException(status_code=403, detail="MFA evidence required")
                if permission in granted or not auth_on:
                    return user
                if permission in _effective_delegated_permissions(request, user):
                    return user
                await _audit_auth_failure(permission, "deny", "permission_denied", subject=user)
                raise HTTPException(status_code=403, detail=f"Permission '{permission}' required")
            except HTTPException:
                raise
            except Exception:
                if auth_on:
                    await _audit_auth_failure(permission, "deny", "invalid_or_expired_token")
                    raise HTTPException(status_code=401, detail="Invalid or expired token")

        # X-User-ID: accepted in dev mode only — no permission check possible without JWT
        if x_user_id:
            if auth_on:
                await _audit_auth_failure(permission, "deny", "bearer_required", subject=x_user_id)
                raise HTTPException(status_code=401, detail="Bearer token required")
            return x_user_id

        if auth_on:
            await _audit_auth_failure(permission, "deny", "missing_authentication")
            raise HTTPException(status_code=401, detail="Missing authentication")

        return "anonymous"

    return _check


ACT_ON_BEHALF_PERMISSION = "perm-act-on-behalf-of-actor"
"""Doot audit BYPASS-02 fix: the exact, distinct permission a caller must
hold to act FOR a different actor_id than themselves. Deliberately
separate from perm-manage-actors — being authorized to administer actor
records is not the same authority as being authorized to impersonate one
in a real commerce transaction. Required invariant: perm-manage-actors
alone must never silently grant act-on-behalf authority."""


async def authorize_acting_for(request: Request, user_id: str, target_actor_id: str) -> None:
    """Call from inside a route body, AFTER a require_permission()
    dependency has already resolved `user_id` and the route's own
    Pydantic body (carrying `target_actor_id`, e.g. body.actor_id) is
    available. This can't be a Depends() the way require_self_or_permission
    is, because that dependency reads its target out of request.path_params
    — a URL path segment — and orders.py's actor_id (like every route this
    protects) is a body field, not a path parameter.

    Raises 403 unless the caller IS target_actor_id, or explicitly holds
    ACT_ON_BEHALF_PERMISSION. Dev mode (AGENTOS_AUTH_REQUIRED unset/false)
    is left exactly as permissive as every other auth dependency in this
    module already is in that mode — this only enforces once auth_required()
    is true, same gating auth_on already uses everywhere else here.
    """
    if not auth_required():
        return
    if user_id == target_actor_id:
        return
    granted = getattr(request.state, "jwt_permissions", set()) or set()
    if ACT_ON_BEHALF_PERMISSION in granted:
        return
    await _audit_auth_failure(
        ACT_ON_BEHALF_PERMISSION,
        "deny",
        "not_self_and_no_act_on_behalf_authority",
        subject=user_id,
    )
    raise HTTPException(
        status_code=403,
        detail=f"caller {user_id!r} may not act for actor_id {target_actor_id!r} without {ACT_ON_BEHALF_PERMISSION!r}",
    )


def require_self_or_permission(permission: str, id_param: str = "actor_id"):
    """Gate an endpoint by "the caller IS the entity named by `id_param`
    in the path" OR holds `permission` — for routes that are inherently
    self-service (an actor reading/managing their OWN account, profile,
    sessions, knowledge graph) but should also be reachable by an
    operator/admin token. `id_param` names the route's own path
    parameter to compare against (e.g. "actor_id", "person_id") — FastAPI
    binds it from the URL the same way it binds the route function's own
    matching parameter, so this dependency must declare it under the
    exact same name the route uses.

    Production Hardening audit finding: api/routes/actor_profile.py's
    get_account/update_account/list_sessions, and separately every
    route in api/routes/knowledge_graph.py (get/add entities, get/add
    relationships, create_snapshot), had NO auth dependency at all — any
    caller, unauthenticated, could read another actor's account state or
    knowledge graph, or WRITE to it (update_account can set/replace a
    real login password, PBKDF2-hashed via kernel/login_info.py;
    add_entity/add_relationship mutate another actor's real episodic
    knowledge graph). A prior "Gate 7 Security" pass fixed the FAKE auth
    in actor_profile.py's login/logout/otp but never added a guard to
    the three real credential/session routes in that same file, and
    knowledge_graph.py apparently was never covered by that pass at
    all. require_permission() alone would be the wrong fix here: it has
    no notion of "the caller IS this actor," so a real actor's own token
    (which login() mints with permissions=[], see that route's own
    comment) could never pass it for their own account/KG — this
    mirrors require_permission()'s exact resolution logic but checks
    self first, unconditionally allowed regardless of granted
    permissions, before falling back to the permission check for a
    caller who is not that actor (e.g. an operator/admin token)."""

    async def _check(
        request: Request,
        x_user_id: str | None = Header(default=None, alias="X-User-ID"),
        authorization: str | None = Header(default=None),
    ) -> str:
        target_id = request.path_params.get(id_param, "")
        auth_on = auth_required()

        if authorization and authorization.startswith("Bearer "):
            token = authorization[7:]

            api_key = os.getenv("AGENTOS_API_KEY", "")
            if api_key and token == api_key:
                from src.monkey_brain.kernel.trusted_auth import (
                    bind_trusted_auth,
                    evidence_for_service,
                )

                svc_user = os.getenv("AGENTOS_API_USER", "api-service")
                bind_trusted_auth(evidence_for_service(svc_user))
                return svc_user

            try:
                from services.auth.helpers.tokens import decode_access_token

                payload = decode_access_token(token)
                from src.monkey_brain.kernel.production_gates import insecure_dev_mode

                try:
                    from services.auth.helpers.revocation import is_jti_revoked

                    revoked = await is_jti_revoked(payload.get("jti"))
                except Exception as exc:
                    if not insecure_dev_mode():
                        await _audit_auth_failure(permission, "deny", "revocation_unavailable")
                        raise HTTPException(status_code=503, detail="Revocation check unavailable") from exc
                    logger.warning("revocation check skipped (insecure-dev): %s", exc)
                    revoked = False
                if revoked:
                    await _audit_auth_failure(
                        permission,
                        "deny",
                        "token_revoked",
                        subject=payload.get("sub", ""),
                    )
                    raise HTTPException(status_code=401, detail="Token has been revoked")
                granted = {
                    p if isinstance(p, str) else p.get("permission_id", "") for p in payload.get("permissions", [])
                }
                request.state.jwt_permissions = granted
                request.state.jwt_attributes = payload.get("attributes") or {}
                user = payload.get("sub", "authenticated")
                if user == target_id or permission in granted or not auth_on:
                    return user
                if permission in _effective_delegated_permissions(request, user):
                    return user
                await _audit_auth_failure(permission, "deny", "permission_denied", subject=user)
                raise HTTPException(status_code=403, detail=f"Permission '{permission}' required")
            except HTTPException:
                raise
            except Exception:
                if auth_on:
                    await _audit_auth_failure(permission, "deny", "invalid_or_expired_token")
                    raise HTTPException(status_code=401, detail="Invalid or expired token")

        if x_user_id:
            # CRITICAL, live-exploitable bug found while testing login
            # under AGENTOS_AUTH_REQUIRED=true: `x_user_id == target_id`
            # let ANY unauthenticated caller — no token, no password, no
            # cryptographic proof of anything — take over another
            # actor's account by simply sending X-User-ID: <their id>.
            # Confirmed live: PUT /actors/{id}/account with only that
            # header (no Authorization at all) successfully overwrote a
            # real actor's real password. X-User-ID is a self-reported,
            # unverified claim; it must be trusted no more than
            # require_permission() already trusts it above — dev mode
            # (auth_on is False) only. A verified Bearer JWT is the only
            # thing that may ever establish "the caller IS this actor."
            if not auth_on:
                return x_user_id
            await _audit_auth_failure(permission, "deny", "bearer_required", subject=x_user_id)
            raise HTTPException(status_code=401, detail="Bearer token required")

        if auth_on:
            await _audit_auth_failure(permission, "deny", "missing_authentication")
            raise HTTPException(status_code=401, detail="Missing authentication")

        return "anonymous"

    return _check
