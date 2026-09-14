"""BaseComplianceAgent — shared base for all regulatory compliance agents.

Rule evaluation model:
  Each standard defines a RULES list of tuples:
    (trigger_key, check_key, severity, article, description, remediation)

  trigger_key — a key in context["data_signals"] or context["system_attributes"]
                whose truthiness activates this rule.
  check_key   — the key that must be truthy for the rule to pass.
                Use None to mean "trigger alone is a violation" (i.e., the presence
                of the condition IS the problem).

Agents are invoked with context containing:
  data_signals      — {has_pii, cross_border_transfer, audit_trail, ...}
  system_attributes — {audit_log_enabled, encryption_at_rest, mfa_enabled, ...}
  action            — proposed operation (string)
  resource          — target resource (string)
  principal         — auth principal dict

act() always returns:
  {
    "action":          "compliant" | "non_compliant",
    "standard":        str,
    "compliant":       bool,
    "findings":        [{rule, severity, article, description, remediation}],
    "critical_count":  int,
    "high_count":      int,
  }
"""

from __future__ import annotations

import logging
from typing import Any

from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.ddd.compliance")

SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


class BaseComplianceAgent(BaseDDDAgent):
    ddd_layer = "compliance"
    workload_spec = "compliance_review"
    readonly = True  # compliance agents are advisory — they never mutate the codebase
    standard: str = ""
    opa_policy_path: str = ""

    # Subclasses define rules as list of 6-tuples:
    # (trigger_key, check_key, severity, article, description, remediation)
    # trigger_key: key in merged signals whose truth triggers this rule.
    # check_key:   key that must be truthy to pass; None means trigger itself is a violation.
    RULES: list[tuple[str, str | None, str, str, str, str]] = []

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        try:
            from src.monkey_brain.kernel.trusted_auth import (
                get_trusted_auth,
                strip_untrusted_security_signals,
            )

            signals = strip_untrusted_security_signals(
                {
                    **context.get("data_signals", {}),
                    **context.get("system_attributes", {}),
                }
            )
            evidence = get_trusted_auth()
            signals["mfa_enforced"] = evidence.mfa_satisfied()
            principal = evidence.to_opa_auth()
        except Exception:
            signals = {
                k: v
                for k, v in {
                    **context.get("data_signals", {}),
                    **context.get("system_attributes", {}),
                }.items()
                if k not in ("mfa_enforced", "mfa_status", "authenticated", "token_valid")
            }
            signals["mfa_enforced"] = False
            principal = {"authenticated": False, "mfa_status": "unknown"}
        return {
            "layer": self.ddd_layer,
            "standard": self.standard,
            "action": context.get("action", ""),
            "resource": context.get("resource", ""),
            "principal": principal,
            "signals": signals,
            "context": context,
        }

    def _evaluate_rules(self, perception: dict[str, Any]) -> list[dict[str, Any]]:
        signals = perception.get("signals", {})
        findings: list[dict[str, Any]] = []
        for (
            trigger_key,
            check_key,
            severity,
            article,
            description,
            remediation,
        ) in self.RULES:
            if not signals.get(trigger_key):
                continue  # rule not applicable
            if check_key is None or not signals.get(check_key):
                findings.append(
                    {
                        "rule": (trigger_key if check_key is None else f"{trigger_key}:{check_key}_missing"),
                        "severity": severity,
                        "article": article,
                        "description": description,
                        "remediation": remediation,
                    }
                )
        return findings

    async def _opa_compliance_check(self, perception: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.opa_policy_path:
            return []
        try:
            from services.common.opa import evaluate_full

            result = await evaluate_full(
                self.opa_policy_path,
                {
                    "standard": self.standard,
                    "signals": perception.get("signals", {}),
                    "action": perception.get("action"),
                    "resource": perception.get("resource"),
                    "principal": perception.get("principal", {}),
                    "auth": perception.get("principal", {}),
                },
                default_allow=False,
            )
            if not result.get("allowed", True):
                return [
                    {
                        "rule": f"opa:{self.opa_policy_path}",
                        "severity": "HIGH",
                        "article": "OPA",
                        "description": f"OPA policy {self.opa_policy_path!r} denied",
                        "remediation": "Review OPA policy output for specific violations",
                    }
                ]
        except Exception as exc:
            logger.debug("[%s] OPA compliance check skipped: %s", self.agent_type, exc)
        return []

    async def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        findings = self._evaluate_rules(perception)
        findings.extend(await self._opa_compliance_check(perception))
        compliant = not any(f["severity"] in ("CRITICAL", "HIGH") for f in findings)
        return {
            "layer": self.ddd_layer,
            "standard": self.standard,
            "compliant": compliant,
            "findings": findings,
            "action": perception.get("action"),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        findings = decision.get("findings", [])
        compliant = decision.get("compliant", True)
        action = "compliant" if compliant else "non_compliant"
        if not compliant:
            self._violations_log.append(
                {
                    "standard": self.standard,
                    "findings": [f["rule"] for f in findings if f["severity"] in ("CRITICAL", "HIGH")],
                }
            )
        return {
            "action": action,
            "standard": self.standard,
            "compliant": compliant,
            "findings": findings,
            "critical_count": sum(1 for f in findings if f["severity"] == "CRITICAL"),
            "high_count": sum(1 for f in findings if f["severity"] == "HIGH"),
            "medium_count": sum(1 for f in findings if f["severity"] == "MEDIUM"),
            "low_count": sum(1 for f in findings if f["severity"] == "LOW"),
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        if not outcome.get("compliant"):
            for f in outcome.get("findings", []):
                rule = f.get("rule", "unknown")
                self._violations_log_counts[rule] = self._violations_log_counts.get(rule, 0) + 1
            if self._violations_log_counts:
                top = sorted(self._violations_log_counts.items(), key=lambda x: -x[1])[:3]
                logger.debug("[%s] top violations: %s", self.agent_type, top)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._violations_log: list[dict] = []
        self._violations_log_counts: dict[str, int] = {}
