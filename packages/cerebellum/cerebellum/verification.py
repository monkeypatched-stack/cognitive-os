"""verification.py — VerificationPipeline: Book 6 skeleton.

Four concrete verifiers cover the four EPA loss terms:

    EpistemicVerifier   — L_B: confidence in [0,1], total uncertainty ≤ 1
    CapabilityVerifier  — L_A: preconditions satisfied before invocation
    SimulationVerifier  — L_S: SimulationLoss below threshold
    MeshVerifier        — L_M: no capability gaps in active mesh

Each implements IVerifier.  VerificationPipeline runs them in sequence
and aggregates into a list[VerificationResult].
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Result & Interface
# ---------------------------------------------------------------------------


@dataclass
class VerificationResult:
    passed: bool
    violations: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    score: float = 1.0  # [0, 1] — 1.0 = fully passing

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "violations": self.violations,
            "evidence": self.evidence,
            "score": round(self.score, 4),
        }


class IVerifier(ABC):
    """Verifier interface — one concern, one class."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def verify(self, artifact: Any, context: dict[str, Any]) -> VerificationResult: ...


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class VerificationPipeline:
    """Run a sequence of verifiers against an artifact.

    Usage:
        pipeline = VerificationPipeline([EpistemicVerifier(), CapabilityVerifier()])
        results = await pipeline.run(epa_state, context={"world_state": ws})
    """

    def __init__(self, verifiers: list[IVerifier], concurrency: int = 4) -> None:
        self._verifiers = verifiers
        self._concurrency = concurrency

    async def run(
        self,
        artifact: Any,
        context: dict[str, Any] | None = None,
    ) -> list[VerificationResult]:
        context = context or {}
        sem = asyncio.Semaphore(self._concurrency)

        async def _one(v: IVerifier) -> VerificationResult:
            async with sem:
                try:
                    return await v.verify(artifact, context)
                except Exception as exc:
                    return VerificationResult(
                        passed=False,
                        violations=[f"{v.name}: exception — {exc}"],
                        score=0.0,
                    )

        return list(await asyncio.gather(*[_one(v) for v in self._verifiers]))

    def passed(self, results: list[VerificationResult]) -> bool:
        return all(r.passed for r in results)

    def aggregate_score(self, results: list[VerificationResult]) -> float:
        if not results:
            return 1.0
        return round(sum(r.score for r in results) / len(results), 4)


# ---------------------------------------------------------------------------
# Concrete Verifiers
# ---------------------------------------------------------------------------


class EpistemicVerifier(IVerifier):
    """L_B verifier — confidence and uncertainty invariants.

    Passes when:
      - B.confidence ∈ [0, 1]
      - B.uncertainty.total ≤ 1
      - B.loss() ≤ threshold (default 0.80)
    """

    def __init__(self, belief_loss_threshold: float = 0.80) -> None:
        self._threshold = belief_loss_threshold

    @property
    def name(self) -> str:
        return "epistemic"

    async def verify(self, artifact: Any, context: dict[str, Any]) -> VerificationResult:
        violations: list[str] = []
        evidence: dict[str, Any] = {}

        belief = None
        # Accept EpistemicPredictiveState or plain dict
        if hasattr(artifact, "B"):
            belief = artifact.B
        elif isinstance(artifact, dict):
            belief = artifact.get("B")

        if belief is None:
            return VerificationResult(passed=True, evidence={"note": "no belief state to verify"}, score=1.0)

        conf = getattr(belief, "confidence", None)
        if conf is not None:
            evidence["confidence"] = conf
            if not (0.0 <= conf <= 1.0):
                violations.append(f"confidence {conf:.4f} outside [0, 1]")

        unc = getattr(belief, "uncertainty", None)
        if unc is not None:
            total = getattr(unc, "total", None)
            if total is not None:
                evidence["uncertainty_total"] = total
                if total > 1.0:
                    violations.append(f"uncertainty.total {total:.4f} > 1.0")

        loss = None
        if hasattr(belief, "loss"):
            try:
                loss = belief.loss()
                evidence["belief_loss"] = loss
                if loss > self._threshold:
                    violations.append(f"belief loss {loss:.4f} > threshold {self._threshold}")
            except Exception:
                pass

        score = 1.0 - (len(violations) / max(3, len(violations) + 1))
        return VerificationResult(
            passed=not violations,
            violations=violations,
            evidence=evidence,
            score=round(score, 4),
        )


class CapabilityVerifier(IVerifier):
    """L_A verifier — preconditions satisfied before invocation.

    Checks each capability's preconditions against world_state in context.
    """

    @property
    def name(self) -> str:
        return "capability"

    async def verify(self, artifact: Any, context: dict[str, Any]) -> VerificationResult:
        violations: list[str] = []
        evidence: dict[str, Any] = {}
        ws = context.get("world_state", {})

        # artifact can be a CapabilityDescriptor or a list of them
        descriptors: list[Any] = []
        if isinstance(artifact, list):
            descriptors = artifact
        elif hasattr(artifact, "preconditions"):
            descriptors = [artifact]
        else:
            return VerificationResult(passed=True, evidence={"note": "no capability descriptor"}, score=1.0)

        total, failed = 0, 0
        for desc in descriptors:
            for pred in getattr(desc, "preconditions", []):
                total += 1
                if not self._eval(pred, ws):
                    violations.append(f"{desc.name}: precondition '{pred}' not satisfied")
                    failed += 1

        evidence = {"total_preconditions": total, "failed": failed}
        score = 1.0 - (failed / max(total, 1))
        return VerificationResult(
            passed=not violations,
            violations=violations,
            evidence=evidence,
            score=round(score, 4),
        )

    def _eval(self, predicate: str, ws: dict) -> bool:
        for op in ("==", "!=", ">=", "<=", ">", "<"):
            if op in predicate:
                lhs, rhs = predicate.split(op, 1)
                lv = self._resolve(lhs.strip(), ws)
                rv = self._coerce(rhs.strip())
                try:
                    if op == "==":
                        return lv == rv
                    if op == "!=":
                        return lv != rv
                    if op == ">":
                        return float(lv) > float(rv)  # type: ignore[arg-type]
                    if op == ">=":
                        return float(lv) >= float(rv)  # type: ignore[arg-type]
                    if op == "<":
                        return float(lv) < float(rv)  # type: ignore[arg-type]
                    if op == "<=":
                        return float(lv) <= float(rv)  # type: ignore[arg-type]
                except Exception:
                    return True
        return True

    @staticmethod
    def _resolve(path: str, state: dict) -> Any:
        cur: Any = state
        for p in path.split("."):
            cur = cur.get(p) if isinstance(cur, dict) else None
        return cur

    @staticmethod
    def _coerce(v: str) -> Any:
        if v.lower() in ("true", "yes"):
            return True
        if v.lower() in ("false", "no"):
            return False
        try:
            return int(v)
        except ValueError:
            pass
        try:
            return float(v)
        except ValueError:
            pass
        return v.strip("'\"")


class SimulationVerifier(IVerifier):
    """L_S verifier — SimulationLoss below threshold.

    Accepts an artifact with a `.total` attribute or `to_epa_dict()` method,
    or a plain dict with key "L_E" or "total".
    """

    def __init__(self, loss_threshold: float = 0.60) -> None:
        self._threshold = loss_threshold

    @property
    def name(self) -> str:
        return "simulation"

    async def verify(self, artifact: Any, context: dict[str, Any]) -> VerificationResult:
        violations: list[str] = []
        evidence: dict[str, Any] = {}

        loss_val = None
        if hasattr(artifact, "to_epa_dict"):
            epa = artifact.to_epa_dict()
            loss_val = epa.get("L_E")
            evidence["epa_loss"] = epa
        elif hasattr(artifact, "total"):
            loss_val = artifact.total
            evidence["total"] = loss_val
        elif isinstance(artifact, dict):
            loss_val = artifact.get("L_E") or artifact.get("total")
            evidence["raw"] = artifact

        if loss_val is None:
            return VerificationResult(
                passed=True,
                evidence={"note": "no simulation loss to verify"},
                score=1.0,
            )

        evidence["loss_value"] = loss_val
        evidence["threshold"] = self._threshold
        if loss_val > self._threshold:
            violations.append(f"simulation loss {loss_val:.4f} > threshold {self._threshold}")

        score = max(0.0, 1.0 - loss_val)
        return VerificationResult(
            passed=not violations,
            violations=violations,
            evidence=evidence,
            score=round(score, 4),
        )


class MeshVerifier(IVerifier):
    """L_M verifier — no capability gaps in active mesh.

    Checks that the mesh's required_capabilities are all available in affordances.
    """

    @property
    def name(self) -> str:
        return "mesh"

    async def verify(self, artifact: Any, context: dict[str, Any]) -> VerificationResult:
        violations: list[str] = []
        evidence: dict[str, Any] = {}

        # Get affordances from EPA state or context
        affordances: set[str] = set()
        if hasattr(artifact, "A"):
            affordances = set(artifact.A)
        elif isinstance(artifact, dict):
            affordances = set(artifact.get("A", []))

        # Get required capabilities from mesh or context
        required: set[str] = set()
        mesh = {}
        if hasattr(artifact, "M"):
            mesh = artifact.M or {}
        elif isinstance(artifact, dict):
            mesh = artifact.get("M", {})

        required = set(mesh.get("required_capabilities", []))
        active_caps = set(mesh.get("active_capabilities", []))
        all_available = affordances | active_caps

        evidence["affordances"] = sorted(affordances)
        evidence["required"] = sorted(required)

        gaps = required - all_available
        if gaps:
            violations.append(f"capability gaps in mesh: {sorted(gaps)}")

        score = 1.0 - (len(gaps) / max(len(required), 1))
        return VerificationResult(
            passed=not violations,
            violations=violations,
            evidence=evidence,
            score=round(score, 4),
        )
