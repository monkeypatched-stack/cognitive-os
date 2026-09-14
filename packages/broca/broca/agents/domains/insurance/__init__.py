"""Insurance agents — Claim, Policy, Underwriting."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.domains.insurance")


class ClaimAgent(BaseDDDAgent):
    agent_type = "claim"
    description = "Processes insurance claims from submission to settlement"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "file"),
            "claim": context.get("claim", {}),
            "policy_id": context.get("policy_id", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"claim.{perception['operation']}",
            "claim_id": f"clm-{perception.get('policy_id', '')[:8]}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"claim.{decision['operation']}",
            "success": True,
            "claim_id": decision.get("claim_id", ""),
        }


class InsurancePolicyAgent(BaseDDDAgent):
    agent_type = "insurance_policy"
    description = "Manages insurance policies, renewals, and endorsements"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "query"),
            "policy_id": context.get("policy_id", ""),
            "policy_type": context.get("policy_type", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"insurance_policy.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"insurance_policy.{decision['operation']}", "success": True}


class UnderwritingAgent(BaseDDDAgent):
    agent_type = "underwriting"
    description = "Evaluates risk and determines policy terms and premiums"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "applicant": context.get("applicant", {}),
            "risk_factors": context.get("risk_factors", {}),
            "coverage_type": context.get("coverage_type", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        risk = sum(perception.get("risk_factors", {}).values()) / max(len(perception.get("risk_factors", {})), 1)
        return {
            "action": "underwriting.evaluate",
            "risk_score": risk,
            "approved": risk < 0.7,
            "premium_multiplier": 1.0 + risk,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "underwriting.evaluate",
            "approved": decision.get("approved", False),
            "risk_score": decision.get("risk_score", 0),
            "premium_multiplier": decision.get("premium_multiplier", 1.0),
        }
