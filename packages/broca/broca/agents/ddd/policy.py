"""PolicyAgent — evaluates governance rules and OPA policies, produces compliance decisions."""

from __future__ import annotations
import logging
import re
from typing import Any, Callable
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.ddd.policy")

# Matches canonical cognitive SPIFFE URI:
#   spiffe://{trust_domain}/pipeline/{pid}/step/{step}/agent/{role}
_PIPELINE_SPIFFE_RE = re.compile(r"^spiffe://[^/]+/pipeline/([^/]+)/step/([^/]+)/agent/([^/]+)$")


class PolicyAgent(BaseDDDAgent):
    """Policy agent — owns governance.

    Perceive: reads the proposed action and its context signals.
    Reason:   evaluates registered rules + OPA (if configured) against context.
    Act:      approves or rejects; records violations for audit.
    Learn:    tracks which rules fire most frequently.

    OPA integration is additive — if OPA_URL is not set, the agent falls back
    to pure rule-based evaluation (existing behaviour unchanged).
    """

    agent_type = "policy"
    description = "Policy agent — evaluates governance rules, enforces compliance, approves or rejects proposed actions"
    ddd_layer = "policy"
    workload_spec = "governance_review"

    def __init__(self, rules: list[Callable[[dict], bool]] | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._rules: list[Callable[[dict], bool]] = rules or []
        self._rule_violation_counts: dict[str, int] = {}
        self._violations_log: list[dict] = []

    def add_rule(self, rule: Callable[[dict], bool], name: str = "") -> None:
        rule._policy_name = name or getattr(rule, "__name__", "anonymous")  # type: ignore[attr-defined]
        self._rules.append(rule)

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "layer": self.ddd_layer,
            "proposed_action": context.get("action", ""),
            "actor": context.get("actor", ""),
            "resource": context.get("resource", ""),
            "principal": context.get("principal", {}),
            "opa_policy_path": context.get("opa_policy_path", "agent/mesh/allow"),
            "context": context,
        }

    async def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        """Evaluate rules then consult OPA. Async to support OPA HTTP call."""
        ctx = perception.get("context", {})

        # 1. Local rules (synchronous, always evaluated)
        violations: list[str] = []
        for rule in self._rules:
            try:
                if not rule(ctx):
                    name = getattr(rule, "_policy_name", getattr(rule, "__name__", "anonymous"))
                    violations.append(name)
                    self._rule_violation_counts[name] = self._rule_violation_counts.get(name, 0) + 1
            except Exception as exc:
                violations.append(f"rule_error:{exc}")

        # 2. OPA evaluation (async, no-op if OPA_URL not set)
        opa_result = await self._opa_evaluate(perception)
        opa_violations = opa_result.get("violations", [])
        opa_source = opa_result.get("source", "fallback")

        all_violations = violations + opa_violations

        return {
            "layer": self.ddd_layer,
            "compliant": len(all_violations) == 0,
            "violations": all_violations,
            "rule_violations": violations,
            "opa_violations": opa_violations,
            "opa_source": opa_source,
            "action": perception.get("proposed_action"),
            "obligations": opa_result.get("obligations", []),
            "dynamic_context": opa_result.get("dynamic_context", {}),
            "context": perception.get("context", {}),
        }

    async def _opa_evaluate(self, perception: dict[str, Any]) -> dict[str, Any]:
        """Query OPA for both mesh-level and pipeline guardrail policies.

        If the principal carries a canonical pipeline SPIFFE URI, a second OPA
        evaluation is run against the pipeline/guardrail policy, which verifies:
          - pipeline status == "EXECUTING"
          - current_step matches the step encoded in the SPIFFE URI
          - requested capability is in allowed_capabilities[step_name]
        """
        principal = perception.get("principal", {})
        action = perception.get("proposed_action", "")
        resource = perception.get("resource", "")

        try:
            from services.common.opa import evaluate_full
            from services.common.dynamic_policy import build_dynamic_context

            dynamic_ctx = await build_dynamic_context(principal, action, resource)
            policy_path = perception.get("opa_policy_path", "agent/mesh/allow")

            input_data: dict[str, Any] = {
                "principal": principal,
                "action": action,
                "resource": resource,
                "context": perception.get("context", {}),
                "dynamic": dynamic_ctx.to_dict(),
            }

            # --- Pipeline guardrail (only when sub is a pipeline SPIFFE URI) ---
            guardrail_violations: list[str] = []
            spiffe_sub = principal.get("sub") or principal.get("spiffe_id") or ""
            m = _PIPELINE_SPIFFE_RE.match(spiffe_sub)
            if m:
                pipeline_id, step_name, agent_role = m.group(1), m.group(2), m.group(3)
                pipeline_state: dict[str, Any] = {}
                try:
                    from services.common.pipeline_attestation import get as _attest_get

                    pipeline_state = (await _attest_get(pipeline_id)) or {}
                except Exception:
                    pass

                guardrail_input = {
                    **input_data,
                    "pipeline_id": pipeline_id,
                    "step_name": step_name,
                    "agent_role": agent_role,
                    "requested_capability": resource or action,
                    "pipeline_state": pipeline_state,
                }
                guardrail_result = await evaluate_full(
                    "pipeline/guardrail",
                    guardrail_input,
                    default_allow=False,  # deny-by-default for structural guardrail
                )
                if not guardrail_result.get("allowed", False):
                    guardrail_violations.append(f"pipeline_guardrail:pipeline={pipeline_id}:step={step_name}:denied")

            # --- Mesh-level OPA ---
            result = await evaluate_full(policy_path, input_data, default_allow=True)
            mesh_violations = [] if result["allowed"] else [f"opa:{policy_path}:denied"]

            all_violations = mesh_violations + guardrail_violations
            return {
                "source": result["source"],
                "violations": all_violations,
                "obligations": result.get("obligations", []),
                "dynamic_context": dynamic_ctx.to_dict(),
            }
        except ImportError:
            return {
                "source": "fallback",
                "violations": [],
                "obligations": [],
                "dynamic_context": {},
            }
        except Exception as exc:
            logger.warning("OPA evaluation failed — treating as policy violation: %s", exc)
            return {
                "source": "fallback",
                "violations": ["opa:service_unavailable"],
                "obligations": [],
                "dynamic_context": {},
            }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        obligations: list[dict] = decision.get("obligations", [])

        # Derive local obligations when OPA didn't provide any
        if not obligations:
            try:
                from services.common.policy_obligations import derive_obligations

                principal = decision.get("context", {}).get("principal", {})
                dynamic = decision.get("dynamic_context", {})
                risk_level = dynamic.get("risk_level", "low")
                obs = derive_obligations(principal, decision.get("action", ""), "", risk_level)
                obligations = [o.to_dict() for o in obs]
            except Exception:
                pass

        if decision.get("compliant"):
            return {
                "action": "approved",
                "proposed_action": decision.get("action"),
                "compliant": True,
                "opa_source": decision.get("opa_source"),
                "obligations": obligations,
                "dynamic_context": decision.get("dynamic_context", {}),
            }

        violation_entry = {
            "proposed_action": decision.get("action"),
            "violations": decision.get("violations"),
        }
        self._violations_log.append(violation_entry)

        return {
            "action": "rejected",
            "proposed_action": decision.get("action"),
            "compliant": False,
            "violations": decision.get("violations"),
            "rule_violations": decision.get("rule_violations", []),
            "opa_violations": decision.get("opa_violations", []),
            "obligations": obligations,
            "dynamic_context": decision.get("dynamic_context", {}),
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        if not outcome.get("compliant"):
            logger.debug(
                "[policy] top violations: %s",
                sorted(self._rule_violation_counts.items(), key=lambda x: -x[1])[:3],
            )
