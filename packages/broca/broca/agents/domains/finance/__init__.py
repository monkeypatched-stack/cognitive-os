"""Finance agents — Payment, Invoice, Accounting, Risk, Fraud."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.domains.finance")


class InvoiceAgent(BaseDDDAgent):
    agent_type = "invoice"
    description = "Generates, sends, and tracks invoices"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "create"),
            "invoice": context.get("invoice", {}),
            "amount": context.get("amount", 0),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"invoice.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"invoice.{decision['operation']}",
            "success": True,
            "invoice_id": f"inv-{decision.get('amount', 0):.0f}",
        }


class AccountingAgent(BaseDDDAgent):
    agent_type = "accounting"
    description = "Manages ledger entries, reconciliations, and financial records"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "record"),
            "entry": context.get("entry", {}),
            "account": context.get("account", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"accounting.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"accounting.{decision['operation']}", "success": True}


class RiskAgent(BaseDDDAgent):
    agent_type = "risk"
    description = "Assesses financial risk and computes risk scores"
    ddd_layer = "value_object"
    workload_spec = "source_control"
    readonly = True

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "entity": context.get("entity", ""),
            "factors": context.get("factors", {}),
            "threshold": context.get("threshold", 0.5),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        risk_score = sum(perception.get("factors", {}).values()) / max(len(perception.get("factors", {})), 1)
        return {
            "action": "risk.assess",
            "risk_score": risk_score,
            "exceeds_threshold": risk_score > perception.get("threshold", 0.5),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "risk.assess",
            "risk_score": decision.get("risk_score", 0),
            "verdict": "high" if decision.get("exceeds_threshold") else "low",
        }


class FraudAgent(BaseDDDAgent):
    agent_type = "fraud"
    description = "Detects fraudulent transactions and patterns"
    ddd_layer = "policy"
    workload_spec = "source_control"
    readonly = True

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "transaction": context.get("transaction", {}),
            "history": context.get("history", []),
            "rules": context.get("rules", []),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "fraud.detect",
            "suspicious": False,
            "confidence": 0.95,
            "flags": [],
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "fraud.detect",
            "suspicious": decision.get("suspicious", False),
            "confidence": decision.get("confidence", 0),
            "flags": decision.get("flags", []),
        }
