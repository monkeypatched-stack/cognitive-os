"""Research agents — Literature, Citation, Experiment, Dataset, Hypothesis, Publication."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.domains.research")


class LiteratureAgent(BaseDDDAgent):
    agent_type = "literature"
    description = "Searches and synthesizes academic literature"
    ddd_layer = "entity"
    workload_spec = "source_control"
    readonly = True

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "query": context.get("query", ""),
            "database": context.get("database", "pubmed"),
            "filters": context.get("filters", {}),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {"action": "literature.search", "results_count": 0, "papers": []}

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "literature.search",
            "success": True,
            "results": decision.get("papers", []),
        }


class CitationAgent(BaseDDDAgent):
    agent_type = "citation"
    description = "Manages citations, references, and bibliography formatting"
    ddd_layer = "value_object"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "format"),
            "citations": context.get("citations", []),
            "style": context.get("style", "APA"),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"citation.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"citation.{decision['operation']}",
            "success": True,
            "formatted": [],
        }


class ExperimentAgent(BaseDDDAgent):
    agent_type = "experiment"
    description = "Designs, executes, and records experiments"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "design"),
            "hypothesis": context.get("hypothesis", ""),
            "variables": context.get("variables", {}),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"experiment.{perception['operation']}",
            "design": {},
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"experiment.{decision['operation']}",
            "success": True,
            "experiment_id": f"exp-{decision.get('hypothesis', '')[:8]}",
        }


class DatasetAgent(BaseDDDAgent):
    agent_type = "dataset"
    description = "Manages research datasets, versions, and metadata"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "query"),
            "dataset_id": context.get("dataset_id", ""),
            "query": context.get("query", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"dataset.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"action": f"dataset.{decision['operation']}", "success": True}


class HypothesisAgent(BaseDDDAgent):
    agent_type = "hypothesis"
    description = "Formulates, tests, and evaluates research hypotheses"
    ddd_layer = "value_object"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "observation": context.get("observation", ""),
            "existing_theories": context.get("existing_theories", []),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {"action": "hypothesis.formulate", "hypothesis": "", "testable": True}

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": "hypothesis.formulate",
            "hypothesis": decision.get("hypothesis", ""),
            "testable": decision.get("testable", False),
        }


class PublicationAgent(BaseDDDAgent):
    agent_type = "publication"
    description = "Manages manuscript preparation, submission, and peer review tracking"
    ddd_layer = "aggregate"
    workload_spec = "source_control"

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": context.get("operation", "submit"),
            "manuscript": context.get("manuscript", {}),
            "journal": context.get("journal", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": perception["operation"],
            "action": f"publication.{perception['operation']}",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "action": f"publication.{decision['operation']}",
            "success": True,
            "submission_id": f"sub-{decision.get('journal', '')[:8]}",
        }
