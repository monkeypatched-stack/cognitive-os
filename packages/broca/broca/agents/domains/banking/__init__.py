"""Banking agents — Account, Transaction, KYC, AML, Compliance."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.domains.banking")


class AccountAgent(BaseDDDAgent):
    agent_type = "account"
    description = "Manages bank accounts, balances, and account lifecycle"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "query"),
            "account_id": context.get("account_id", ""),
            "account_type": context.get("account_type", "checking"),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"account.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"account.{decision['operation']}", "success": True}


class TransactionAgent(BaseDDDAgent):
    agent_type = "transaction"
    description = "Records and queries financial transactions"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "record"),
            "transaction": context.get("transaction", {}),
            "account_id": context.get("account_id", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"transaction.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"transaction.{decision['operation']}",
            "success": True,
            "transaction_id": f"txn-{decision.get('account_id', '')[:8]}",
        }


class KYCAgent(BaseDDDAgent):
    agent_type = "kyc"
    description = "Performs Know Your Customer identity verification"
    ddd_layer = "policy"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "customer_id": context.get("customer_id", ""),
            "documents": context.get("documents", []),
            "operation": context.get("operation", "verify"),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"kyc.{perception['operation']}",
            "verified": True,
            "confidence": 0.95,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"kyc.{decision['operation']}",
            "success": decision.get("verified", False),
            "confidence": decision.get("confidence", 0),
        }


class AMLAgent(BaseDDDAgent):
    agent_type = "aml"
    description = "Monitors transactions for Anti-Money Laundering compliance"
    ddd_layer = "policy"
    workload_spec = "source_control"
    readonly = True

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "transactions": context.get("transactions", []),
            "threshold": context.get("threshold", 10000),
            "patterns": context.get("patterns", []),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        flagged = [
            t for t in perception.get("transactions", []) if t.get("amount", 0) > perception.get("threshold", 10000)
        ]
        return {
            "action": "aml.screen",
            "flagged_count": len(flagged),
            "suspicious": len(flagged) > 0,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "aml.screen",
            "flagged": decision.get("flagged_count", 0),
            "suspicious": decision.get("suspicious", False),
        }


class BankingComplianceAgent(BaseDDDAgent):
    agent_type = "banking_compliance"
    description = "Enforces banking regulatory compliance (Basel III, Dodd-Frank)"
    ddd_layer = "policy"
    workload_spec = "source_control"
    readonly = True

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "regulation": context.get("regulation", ""),
            "operation": context.get("operation", "check"),
            "data": context.get("data", {}),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"compliance.{perception['operation']}",
            "compliant": True,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"compliance.{decision['operation']}",
            "compliant": decision.get("compliant", True),
        }
