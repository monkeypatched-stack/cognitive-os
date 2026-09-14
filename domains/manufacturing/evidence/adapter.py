"""Manufacturing Domain Evidence Adapter.

Interprets generic Monkey Brain ExecutionOutcomes into manufacturing-specific evidence:
- Work order execution evidence
- Batch production evidence
- SOP compliance evidence
- Calibration evidence
- Change control evidence
"""

from __future__ import annotations

import re
from typing import Any


class DomainEvidence:
    """Manufacturing domain evidence interpreter."""

    def interpret(self, outcome: Any, context: dict[str, Any]) -> dict[str, Any]:
        """Interpret an ExecutionOutcome (typed or dict) into manufacturing evidence."""
        if hasattr(outcome, "goal"):
            goal = outcome.goal
            question = context.get("question", "")
            reward = outcome.reward
            agent_type = getattr(outcome, "agent_type", "")
            observations = (
                outcome.result.observations if hasattr(outcome, "result") else []
            )
            artifact_list = [
                {"kind": a.kind, "name": a.name, "uri": a.uri}
                for a in (
                    outcome.result.artifacts if hasattr(outcome, "result") else []
                )
            ]
            answer = " ".join(observations)
        else:
            goal = outcome.get("goal", "")
            question = context.get("question", "")
            reward = outcome.get("reward", 0.5)
            agent_type = outcome.get("agent_type", "")
            answer = outcome.get("answer", "")
            artifact_list = outcome.get("artifacts", [])

        evidence = {
            "domain": "manufacturing",
            "goal": goal,
            "question": question,
            "answer_summary": answer[:500] if answer else "",
            "evidence_type": self._classify_evidence(goal, question, answer),
            "entities": self._extract_entities(answer),
            "compliance_flags": self._check_compliance(answer, question),
            "reward": reward,
            "agent_type": agent_type,
            "artifacts": artifact_list,
        }

        return evidence

    def validate(self, evidence: dict[str, Any]) -> bool:
        """Validate that evidence conforms to manufacturing schema."""
        required = ["domain", "goal", "question", "evidence_type"]
        return all(evidence.get(k) for k in required)

    def _classify_evidence(self, goal: str, question: str, answer: str) -> str:
        """Classify the type of manufacturing evidence."""
        q = question.lower()
        if "work order" in q or "work_order" in goal:
            return "work_order_execution"
        if "batch" in q or "batch_record" in goal:
            return "batch_production"
        if "sop" in q or "sop_compliance" in goal:
            return "sop_compliance"
        if "calibration" in q:
            return "calibration_evidence"
        if "change control" in q or "change_control" in goal:
            return "change_control"
        if "maintenance" in q:
            return "maintenance_evidence"
        if "oee" in q or "production_kpi" in goal:
            return "production_kpi"
        return "general_manufacturing"

    def _extract_entities(self, answer: str) -> dict[str, Any]:
        """Extract manufacturing entities from the answer."""
        entities = {}
        wo_match = re.findall(r"WO-\w+", answer)
        if wo_match:
            entities["work_order_ids"] = wo_match
        batch_match = re.findall(r"BATCH-\w+", answer)
        if batch_match:
            entities["batch_ids"] = batch_match
        sop_match = re.findall(r"SOP-\w+", answer)
        if sop_match:
            entities["sop_ids"] = sop_match
        cc_match = re.findall(r"CC-\w+", answer)
        if cc_match:
            entities["change_control_ids"] = cc_match
        return entities

    def _check_compliance(self, answer: str, question: str) -> list[str]:
        """Check for compliance-related flags in the evidence."""
        flags = []
        q = question.lower()
        if "overdue" in q and "maintenance" in q:
            flags.append("overdue_maintenance_check")
        if "calibration" in q and ("due" in q or "overdue" in q):
            flags.append("calibration_status_check")
        if "deviation" in q or "out of spec" in q:
            flags.append("deviation_investigation")
        if "root cause" in q:
            flags.append("rca_required")
        if "capa" in q.lower():
            flags.append("capa_tracking")
        return flags
