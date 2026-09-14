"""AuthPolicyAgent — authentication and authorization enforcement agent.

Responsibilities (ZTAA — Zero Trust Agent Architecture):
  - Validate inbound principal (SPIFFE URI, JWT scopes, mTLS cert binding)
  - Enforce RBAC: required scopes against principal's granted scopes
  - Delegate to OPA for complex, context-aware decisions
  - Emit audit events for every allow/deny decision
  - Self-registers into Broca registry on import

Context keys consumed:
  principal        dict    — output of get_current_principal() or get_principal_with_cert_binding()
  required_scopes  list    — scopes that must be present in principal.scopes
  action           str     — operation being requested (e.g. "query", "resolve", "register")
  resource         str     — target resource identifier
  opa_policy_path  str     — OPA policy path (default: "agent/mesh/allow")

Returns in payload:
  allowed          bool
  principal_type   str
  subject          str     — JWT sub (SPIFFE URI for agents)
  granted_scopes   list
  missing_scopes   list
  mtls_verified    bool
  opa_decision     dict
  audit_event_id   str
"""

from __future__ import annotations

import logging
from typing import Any

from broca.agents._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.auth_policy")


class AuthPolicyAgent(BaseETASSAgent):
    """Authentication + authorization enforcement agent.

    Runs the full ZTAA decision stack:
      1. Principal presence check
      2. SPIFFE ID verification (if principal is an agent)
      3. Scope/RBAC enforcement
      4. OPA policy evaluation (if configured)
      5. Audit event emission
    """

    agent_type = "auth_policy"
    description = (
        "Auth policy agent — enforces ZTAA: validates SPIFFE identity, "
        "RBAC scopes, and OPA policy decisions for every inter-agent request"
    )

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict[str, Any]):
        principal: dict[str, Any] = context.get("principal", {})
        required_scopes: list[str] = context.get("required_scopes", [])
        action: str = context.get("action", "unknown")
        resource: str = context.get("resource", "")
        opa_policy_path: str = context.get("opa_policy_path", "agent/mesh/allow")

        decision = await self._evaluate(
            principal=principal,
            required_scopes=required_scopes,
            action=action,
            resource=resource,
            opa_policy_path=opa_policy_path,
            context=context,
        )

        await self._emit_audit(action, resource, principal, decision)

        self._reward(decision["allowed"])
        obs_parts = [
            f"auth_policy: {'allowed' if decision['allowed'] else 'denied'}",
            f"action={action}",
            f"subject={decision.get('subject')}",
            f"opa={decision.get('opa_decision', {}).get('source', 'skip')}",
            f"risk={decision.get('dynamic_context', {}).get('risk_level', 'n/a')}",
            f"obligations={len(decision.get('obligations', []))}",
        ]
        return self._result(
            payload=decision,
            observations=[" ".join(obs_parts)],
        )

    async def _evaluate(
        self,
        principal: dict[str, Any],
        required_scopes: list[str],
        action: str,
        resource: str,
        opa_policy_path: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        principal_type = principal.get("principal_type", "unknown")
        subject = principal.get("sub", "")
        spiffe_id = principal.get("spiffe_id", "")
        granted_scopes: list[str] = principal.get("scopes", [])
        mtls_verified: bool = bool(principal.get("mtls_verified", False))

        # 1. Principal must exist
        if not subject:
            return self._deny(
                "no_principal",
                required_scopes,
                [],
                principal_type,
                subject,
                mtls_verified,
            )

        # 2. SPIFFE ID must match sub for agent principals
        if principal_type == "agent" and spiffe_id and spiffe_id != subject:
            return self._deny(
                "spiffe_sub_mismatch",
                required_scopes,
                granted_scopes,
                principal_type,
                subject,
                mtls_verified,
            )

        # 2b. Pipeline binding: if the token is pipeline-scoped, verify it
        #     matches the pipeline_id in the execution context.
        token_pipeline_id = principal.get("pipeline_id", "")
        ctx_pipeline_id = context.get("pipeline_id", "") or context.get("__pipeline_id__", "")
        if token_pipeline_id and ctx_pipeline_id and token_pipeline_id != ctx_pipeline_id:
            return self._deny(
                f"pipeline_id_mismatch:token={token_pipeline_id} ctx={ctx_pipeline_id}",
                required_scopes,
                granted_scopes,
                principal_type,
                subject,
                mtls_verified,
            )

        # 3. Scope check (agents only — humans use existing RBAC permission system)
        missing_scopes: list[str] = []
        if principal_type == "agent" and required_scopes:
            granted_set = set(granted_scopes)
            missing_scopes = [s for s in required_scopes if s not in granted_set]
            if missing_scopes:
                return self._deny(
                    f"missing_scopes:{','.join(missing_scopes)}",
                    required_scopes,
                    granted_scopes,
                    principal_type,
                    subject,
                    mtls_verified,
                    missing_scopes=missing_scopes,
                )

        # 4. Dynamic context (risk, reliability, audit flags) — best-effort
        dynamic_ctx: dict[str, Any] = {}
        try:
            from services.common.dynamic_policy import build_dynamic_context

            ctx = await build_dynamic_context(principal, action, resource)
            dynamic_ctx = ctx.to_dict()

            # Low-reliability agents restricted to read-only: block write actions
            if ctx.read_only_enforced and ("write" in action.lower() or "delete" in action.lower()):
                return self._deny(
                    "read_only_enforced:low_reliability",
                    required_scopes,
                    granted_scopes,
                    principal_type,
                    subject,
                    mtls_verified,
                    dynamic_context=dynamic_ctx,
                )
        except Exception as exc:
            logger.debug("Dynamic context unavailable (non-fatal): %s", exc)

        # 5. OPA (best-effort, falls through on no OPA_URL)
        opa_decision = await self._opa(opa_policy_path, principal, action, resource, dynamic_ctx)
        if not opa_decision.get("allowed", True):
            return self._deny(
                f"opa_denied:{opa_policy_path}",
                required_scopes,
                granted_scopes,
                principal_type,
                subject,
                mtls_verified,
                opa_decision=opa_decision,
                dynamic_context=dynamic_ctx,
            )

        # 6. Resolve obligations (from OPA result first, then local derivation)
        obligations = opa_decision.get("obligations", [])
        if not obligations:
            try:
                from services.common.policy_obligations import (
                    derive_obligations,
                )

                risk = dynamic_ctx.get("risk_level", "low")
                obs = derive_obligations(principal, action, resource, risk)
                obligations = [o.to_dict() for o in obs]
            except Exception:
                pass

        return {
            "allowed": True,
            "principal_type": principal_type,
            "subject": subject,
            "spiffe_id": spiffe_id,
            "pipeline_id": principal.get("pipeline_id", ""),
            "granted_scopes": granted_scopes,
            "missing_scopes": [],
            "mtls_verified": mtls_verified,
            "opa_decision": opa_decision,
            "dynamic_context": dynamic_ctx,
            "obligations": obligations,
            "reason": "all_checks_passed",
        }

    def _deny(
        self,
        reason: str,
        required_scopes: list[str],
        granted_scopes: list[str],
        principal_type: str,
        subject: str,
        mtls_verified: bool,
        missing_scopes: list[str] | None = None,
        opa_decision: dict | None = None,
        dynamic_context: dict | None = None,
    ) -> dict[str, Any]:
        return {
            "allowed": False,
            "principal_type": principal_type,
            "subject": subject,
            "granted_scopes": granted_scopes,
            "required_scopes": required_scopes,
            "missing_scopes": missing_scopes or [],
            "mtls_verified": mtls_verified,
            "opa_decision": opa_decision or {"source": "skip"},
            "dynamic_context": dynamic_context or {},
            "obligations": [],
            "reason": reason,
        }

    async def _opa(
        self,
        policy_path: str,
        principal: dict[str, Any],
        action: str,
        resource: str,
        dynamic_ctx: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            from services.common.opa import evaluate_full

            result = await evaluate_full(
                policy_path,
                {
                    "principal": {
                        "sub": principal.get("sub"),
                        "principal_type": principal.get("principal_type"),
                        "scopes": principal.get("scopes", []),
                        "role_ids": principal.get("role_ids", []),
                        "spiffe_id": principal.get("spiffe_id"),
                        "mtls_verified": bool(principal.get("mtls_verified", False)),
                    },
                    "action": action,
                    "resource": resource,
                    "dynamic": dynamic_ctx or {},
                },
                default_allow=False,
            )
            return result
        except ImportError:
            return {"source": "skip", "allowed": True, "obligations": []}
        except Exception as exc:
            logger.debug("OPA unreachable (non-fatal): %s", exc)
            return {"source": "fallback", "allowed": True, "obligations": []}

    async def _emit_audit(
        self,
        action: str,
        resource: str,
        principal: dict[str, Any],
        decision: dict[str, Any],
    ) -> None:
        try:
            from services.common.audit_events import emit

            await emit(
                f"policy.{'allow' if decision['allowed'] else 'deny'}",
                "allow" if decision["allowed"] else "deny",
                principal,
                policy_path=decision.get("opa_decision", {}).get("source", ""),
                metadata={
                    "action": action,
                    "resource": resource,
                    "reason": decision.get("reason"),
                    "missing_scopes": decision.get("missing_scopes", []),
                },
            )
        except Exception as exc:
            logger.debug("Audit emit skipped (non-fatal): %s", exc)
