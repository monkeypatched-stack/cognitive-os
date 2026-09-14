"""Sales agents — Lead, Opportunity, CRMSearch, Quote, Proposal, SalesForecast, Territory, Commission."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.domains.sales")


class LeadAgent(BaseDDDAgent):
    agent_type = "lead"
    description = "Manages lead capture, qualification, and scoring"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "capture"),
            "lead": context.get("lead", {}),
            "source": context.get("source", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"lead.{perception['operation']}",
            "qualified": True,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"lead.{decision['operation']}",
            "success": True,
            "qualified": decision.get("qualified", False),
        }


class OpportunityAgent(BaseDDDAgent):
    agent_type = "opportunity"
    description = "Manages sales opportunities through the pipeline"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "create"),
            "opportunity": context.get("opportunity", {}),
            "lead_id": context.get("lead_id", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"opportunity.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"opportunity.{decision['operation']}", "success": True}


class CRMSearchAgent(BaseDDDAgent):
    agent_type = "crm_search"
    description = "Searches CRM records, contacts, and interaction history"
    ddd_layer = "entity"
    workload_spec = "source_control"
    readonly = True

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "query": context.get("query", ""),
            "record_type": context.get("record_type", "contact"),
            "filters": context.get("filters", {}),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {"action": "crm_search.find", "results_count": 0}

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": "crm_search.find", "success": True, "results": []}


class QuoteAgent(BaseDDDAgent):
    agent_type = "quote"
    description = "Generates price quotes and proposals for opportunities"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "generate"),
            "opportunity_id": context.get("opportunity_id", ""),
            "items": context.get("items", []),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        total = sum(i.get("price", 0) * i.get("quantity", 1) for i in perception.get("items", []))
        return {
            "operation": perception["operation"],
            "action": f"quote.{perception['operation']}",
            "total": total,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"quote.{decision['operation']}",
            "success": True,
            "total": decision.get("total", 0),
            "quote_id": f"q-{decision.get('total', 0):.0f}",
        }


class ProposalAgent(BaseDDDAgent):
    agent_type = "proposal"
    description = "Generates and manages sales proposals"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "generate"),
            "opportunity_id": context.get("opportunity_id", ""),
            "template": context.get("template", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"proposal.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"proposal.{decision['operation']}", "success": True}


class SalesForecastAgent(BaseDDDAgent):
    agent_type = "sales_forecast"
    description = "Predicts revenue and sales pipeline outcomes"
    ddd_layer = "value_object"
    workload_spec = "source_control"
    readonly = True

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "pipeline": context.get("pipeline", []),
            "historical": context.get("historical", []),
            "period": context.get("period", "quarter"),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        pipeline_value = sum(o.get("amount", 0) for o in perception.get("pipeline", []))
        return {
            "action": "sales_forecast.predict",
            "forecast_value": pipeline_value,
            "confidence": 0.75,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "sales_forecast.predict",
            "forecast_value": decision.get("forecast_value", 0),
            "confidence": decision.get("confidence", 0),
        }


class TerritoryAgent(BaseDDDAgent):
    agent_type = "territory"
    description = "Manages sales territories, assignments, and quotas"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "assign"),
            "territory": context.get("territory", ""),
            "rep_id": context.get("rep_id", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"territory.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"territory.{decision['operation']}", "success": True}


class CommissionAgent(BaseDDDAgent):
    agent_type = "commission"
    description = "Calculates sales commissions and incentives"
    ddd_layer = "value_object"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "rep_id": context.get("rep_id", ""),
            "deals": context.get("deals", []),
            "rate": context.get("rate", 0.1),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        total = sum(d.get("amount", 0) for d in perception.get("deals", []))
        commission = total * perception.get("rate", 0.1)
        return {
            "action": "commission.calculate",
            "total_sales": total,
            "commission": commission,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "commission.calculate",
            "total_sales": decision.get("total_sales", 0),
            "commission": decision.get("commission", 0),
        }
