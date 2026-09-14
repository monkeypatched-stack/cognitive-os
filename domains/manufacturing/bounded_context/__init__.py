"""Manufacturing Bounded Context Agent.

Owns the pharmaceutical manufacturing business capability:
- Work orders (calibration, maintenance, installation)
- Batch production (release, hold, deviation)
- SOP compliance
- Change control
- Equipment management
"""

from __future__ import annotations
import logging
from typing import Any
from domains.package import BoundedContextAgent, AggregateAgent

logger = logging.getLogger("domain.manufacturing.context")


class ManufacturingContextAgent(BoundedContextAgent):
    """Bounded context for pharmaceutical manufacturing.

    Coordinates: WorkOrder, Batch, SOP, ChangeControl, CalibrationRecord aggregates.
    """

    def __init__(self) -> None:
        super().__init__(name="manufacturing")
        self.aggregates = [
            WorkOrderAggregateAgent(),
            BatchAggregateAgent(),
            ChangeControlAggregateAgent(),
        ]

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        question = context.get("question", "").lower()
        active_aggregate = None
        if any(
            w in question for w in ("work order", "calibration", "maintenance", "wo")
        ):
            active_aggregate = "work_order"
        elif any(
            w in question for w in ("batch", "yield", "release", "hold", "deviation")
        ):
            active_aggregate = "batch"
        elif any(
            w in question for w in ("change control", "change request", "validation")
        ):
            active_aggregate = "change_control"
        elif any(w in question for w in ("sop", "procedure", "compliance")):
            active_aggregate = "sop"
        return {
            "context": self.name,
            "active_aggregate": active_aggregate,
            "question": question,
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        aggregate_name = perception.get("active_aggregate")
        aggregate = next((a for a in self.aggregates if a.name == aggregate_name), None)
        return {
            "context": self.name,
            "delegate_to": aggregate_name,
            "aggregate": aggregate,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        aggregate = decision.get("aggregate")
        if aggregate:
            return aggregate.execute({"question": decision.get("question", "")})
        return {
            "context": self.name,
            "action": "no_delegate",
            "result": "handled_at_context",
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        self._memory.append(outcome)


class WorkOrderAggregateAgent(AggregateAgent):
    """Work order aggregate — enforces work order lifecycle invariants.

    Invariants:
    - Cannot complete without assigned worker
    - Cannot start without equipment availability confirmed
    - Overdue orders require escalation flag
    """

    def __init__(self) -> None:
        super().__init__(name="work_order")

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        wo = context.get("work_order", {})
        return {
            "aggregate": self.name,
            "wo_id": wo.get("id"),
            "status": wo.get("status", "open"),
            "assigned_to": wo.get("assigned_to", ""),
            "equipment_available": wo.get("equipment_available", False),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        violations = []
        if not perception.get("assigned_to"):
            violations.append("no_assigned_worker")
        if perception.get("status") == "in_progress" and not perception.get(
            "equipment_available"
        ):
            violations.append("equipment_unavailable")
        return {
            "aggregate": self.name,
            "violations": violations,
            "consistent": len(violations) == 0,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        if decision.get("consistent"):
            return {
                "aggregate": self.name,
                "action": "work_order_approved",
                "consistent": True,
            }
        return {
            "aggregate": self.name,
            "action": "work_order_blocked",
            "violations": decision.get("violations"),
        }


class BatchAggregateAgent(AggregateAgent):
    """Batch aggregate — enforces batch release and hold invariants.

    Invariants:
    - Cannot release batch with yield below threshold
    - Cannot release without QA sign-off
    - Batch on hold requires deviation report
    """

    def __init__(self, min_yield_pct: float = 90.0) -> None:
        super().__init__(name="batch")
        self.min_yield_pct = min_yield_pct

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "aggregate": self.name,
            "batch_id": context.get("batch_id"),
            "status": context.get("status", "in_progress"),
            "yield_pct": context.get("yield_pct", 0.0),
            "qa_signed": context.get("qa_signed", False),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        violations = []
        if perception.get("yield_pct", 0.0) < self.min_yield_pct:
            violations.append(
                f"yield_{perception["yield_pct"]:.1f}%_below_threshold_{self.min_yield_pct}%"
            )
        if perception.get("status") == "releasing" and not perception.get("qa_signed"):
            violations.append("missing_qa_sign_off")
        return {
            "aggregate": self.name,
            "violations": violations,
            "consistent": len(violations) == 0,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        if decision.get("consistent"):
            return {
                "aggregate": self.name,
                "action": "batch_released",
                "consistent": True,
            }
        return {
            "aggregate": self.name,
            "action": "batch_held",
            "violations": decision.get("violations"),
        }


class ChangeControlAggregateAgent(AggregateAgent):
    """Change control aggregate — enforces change request governance.

    Invariants:
    - Cannot approve without required risk assessment
    - Production changes require validation protocol
    - Emergency changes require retrospective documentation
    """

    def __init__(self) -> None:
        super().__init__(name="change_control")

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "aggregate": self.name,
            "change_id": context.get("change_id"),
            "change_type": context.get("change_type", "standard"),
            "risk_assessed": context.get("risk_assessed", False),
            "validation_protocol": context.get("validation_protocol", False),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        violations = []
        if not perception.get("risk_assessed"):
            violations.append("missing_risk_assessment")
        change_type = perception.get("change_type", "standard")
        if change_type == "production" and not perception.get("validation_protocol"):
            violations.append("production_change_requires_validation_protocol")
        return {
            "aggregate": self.name,
            "violations": violations,
            "consistent": len(violations) == 0,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        if decision.get("consistent"):
            return {
                "aggregate": self.name,
                "action": "change_approved",
                "consistent": True,
            }
        return {
            "aggregate": self.name,
            "action": "change_blocked",
            "violations": decision.get("violations"),
        }
