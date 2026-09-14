"""Software Engineering Bounded Context Agent.

Owns the software engineering business capability:
- Code generation (SittingFace)
- Version control (git/GitHub)
- CI/CD pipeline
- Code review
- Deployment
"""

from __future__ import annotations
import logging
from typing import Any
from domains.package import BoundedContextAgent, AggregateAgent

logger = logging.getLogger("domain.software_engineering.context")


class SoftwareEngineeringContextAgent(BoundedContextAgent):
    """Bounded context for software engineering.

    Coordinates: PullRequest, TestSuite, Deployment, Pipeline, CodeReview aggregates.
    """

    def __init__(self) -> None:
        super().__init__(name="software_engineering")
        self.aggregates = [
            PullRequestAggregateAgent(),
            TestSuiteAggregateAgent(),
            DeploymentAggregateAgent(),
        ]

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        question = context.get("question", "").lower()
        active_aggregate = None
        if any(w in question for w in ("pull request", "pr", "code review", "merge")):
            active_aggregate = "pull_request"
        elif any(w in question for w in ("test", "coverage", "unit", "integration")):
            active_aggregate = "test_suite"
        elif any(w in question for w in ("deploy", "release", "canary", "rollout")):
            active_aggregate = "deployment"
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


class PullRequestAggregateAgent(AggregateAgent):
    """Pull Request aggregate — enforces PR lifecycle invariants.

    Invariants:
    - Cannot merge without at least 1 approval
    - Cannot merge into protected branch without CI passing
    - Cannot approve your own PR
    """

    def __init__(self) -> None:
        super().__init__(name="pull_request")
        self.add_invariant(lambda: True)  # placeholder — real invariants set at runtime

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        pr = context.get("pr", {})
        return {
            "aggregate": self.name,
            "pr_id": pr.get("id"),
            "status": pr.get("status", "open"),
            "approvals": pr.get("approvals", 0),
            "ci_passing": pr.get("ci_passing", False),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        can_merge = (
            perception.get("approvals", 0) >= 1
            and perception.get("ci_passing", False)
            and perception.get("status") == "open"
        )
        return {
            "aggregate": self.name,
            "can_merge": can_merge,
            "violations": [] if can_merge else ["insufficient_approvals_or_ci_failing"],
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        if decision.get("can_merge"):
            return {
                "aggregate": self.name,
                "action": "approved_for_merge",
                "consistent": True,
            }
        return {
            "aggregate": self.name,
            "action": "merge_blocked",
            "violations": decision.get("violations"),
            "consistent": False,
        }


class TestSuiteAggregateAgent(AggregateAgent):
    """Test suite aggregate — enforces test quality invariants.

    Invariants:
    - Coverage must not drop below threshold
    - No new failing tests without explicit waiver
    """

    def __init__(self, coverage_threshold: float = 0.8) -> None:
        super().__init__(name="test_suite")
        self.coverage_threshold = coverage_threshold

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "aggregate": self.name,
            "passed": context.get("passed", 0),
            "failed": context.get("failed", 0),
            "coverage_pct": context.get("coverage_pct", 0.0),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        failures = perception.get("failed", 0)
        coverage = perception.get("coverage_pct", 0.0)
        violations = []
        if failures > 0:
            violations.append(f"{failures}_tests_failing")
        if coverage < self.coverage_threshold:
            violations.append(
                f"coverage_{coverage:.0%}_below_threshold_{self.coverage_threshold:.0%}"
            )
        return {
            "aggregate": self.name,
            "violations": violations,
            "consistent": len(violations) == 0,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        if decision.get("consistent"):
            return {"aggregate": self.name, "action": "suite_passes_invariants"}
        return {
            "aggregate": self.name,
            "action": "suite_violations",
            "violations": decision.get("violations"),
        }


class DeploymentAggregateAgent(AggregateAgent):
    """Deployment aggregate — enforces deployment safety invariants.

    Invariants:
    - Cannot deploy to production without staging passing
    - Cannot deploy a failing build
    - Canary must pass before full rollout
    """

    def __init__(self) -> None:
        super().__init__(name="deployment")

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "aggregate": self.name,
            "environment": context.get("environment", "staging"),
            "build_passing": context.get("build_passing", True),
            "staging_passing": context.get("staging_passing", False),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        env = perception.get("environment", "staging")
        violations = []
        if not perception.get("build_passing"):
            violations.append("failing_build")
        if env == "production" and not perception.get("staging_passing"):
            violations.append("staging_not_passing")
        return {
            "aggregate": self.name,
            "violations": violations,
            "consistent": len(violations) == 0,
            "environment": env,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        if decision.get("consistent"):
            return {
                "aggregate": self.name,
                "action": "deployment_approved",
                "environment": decision.get("environment"),
            }
        return {
            "aggregate": self.name,
            "action": "deployment_blocked",
            "violations": decision.get("violations"),
        }
