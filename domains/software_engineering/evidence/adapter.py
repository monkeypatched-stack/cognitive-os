"""Software Engineering Domain Evidence Adapter.

Interprets typed ExecutionOutcome objects (or raw dicts) into software engineering evidence.
This is the ONLY domain-specific piece in the runtime; everything above is domain-agnostic.
"""

from __future__ import annotations

import re
from typing import Any


class DomainEvidence:
    """Software engineering domain evidence interpreter."""

    def interpret(self, outcome: Any, context: dict[str, Any]) -> dict[str, Any]:
        """Interpret an ExecutionOutcome (typed or dict) into software engineering evidence."""
        # Support both typed ExecutionOutcome dataclass and legacy raw dict
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
            observations = []

        evidence = {
            "domain": "software_engineering",
            "goal": goal,
            "question": question,
            "answer_summary": answer[:500] if answer else "",
            "evidence_type": self._classify_evidence(goal, question, answer),
            "artifacts": artifact_list or self._extract_artifacts(answer),
            "quality_flags": self._check_quality(answer, question),
            "reward": reward,
            "agent_type": agent_type,
            "observations": observations,
        }

        return evidence

    def validate(self, evidence: dict[str, Any]) -> bool:
        """Validate that evidence conforms to software engineering schema."""
        required = ["domain", "goal", "question", "evidence_type"]
        return all(evidence.get(k) for k in required)

    def _classify_evidence(self, goal: str, question: str, answer: str) -> str:
        """Classify the type of software engineering evidence."""
        q = question.lower()
        if "code" in q or "generate" in q or "sittingface" in goal:
            return "code_generation"
        if "test" in q or "unit test" in q or "integration test" in q:
            return "test_execution"
        if "deploy" in q or "release" in q or "canary" in q:
            return "deployment"
        if "ci" in q or "pipeline" in q or "build" in q:
            return "ci_cd_pipeline"
        if "review" in q or "pr" in q or "pull request" in q:
            return "code_review"
        if "security" in q or "vulnerability" in q:
            return "security_scan"
        if "static analysis" in q or "lint" in q:
            return "static_analysis"
        return "general_software_engineering"

    def _extract_artifacts(self, answer: str) -> dict[str, Any]:
        """Extract software engineering artifacts from the answer."""
        artifacts = {}

        # Extract file paths
        file_matches = re.findall(r"(?:src|lib|pkg)/[\w/]+\.py", answer)
        if file_matches:
            artifacts["files"] = list(set(file_matches))

        # Extract test results
        test_match = re.findall(r"(\d+)\s*(?:passed|failed|skipped)", answer)
        if test_match:
            artifacts["test_counts"] = test_match

        # Extract PR/issue numbers
        pr_match = re.findall(r"PR[#-]?\d+|issue[#-]?\d+", answer, re.IGNORECASE)
        if pr_match:
            artifacts["references"] = list(set(pr_match))

        return artifacts

    def _check_quality(self, answer: str, question: str) -> list[str]:
        """Check for quality-related flags in the evidence."""
        flags = []
        q = question.lower()

        if "security" in q and (
            "vulnerability" in answer.lower() or "cve" in answer.lower()
        ):
            flags.append("security_vulnerability_found")
        if "test" in q and "fail" in answer.lower():
            flags.append("test_failure")
        if "coverage" in q and answer.lower().find("coverage") > -1:
            flags.append("coverage_report")
        if "breaking change" in answer.lower():
            flags.append("breaking_change_detected")

        return flags
