"""Context — global mutable runtime constraints, prioritized and queued (ADR 0021).

The architecture simplifies to three pieces:

    World    W   fixed ground-truth transition tensor — NOT updated at runtime
    Context  C   GLOBAL, mutable runtime constraints — changes are prioritized & queued
    Action   a   LOCAL transition an actor makes

You never update the world at runtime; you give it a context and an action. The context
is the only shared mutable state, and concurrent changes to it are ordered by a priority
queue, so the most urgent constraint (a revoked permission, an exhausted budget) applies
before routine ones. Applying the context to the fixed world yields the constrained
operator an action runs over — the world is untouched.
"""

from __future__ import annotations

import heapq
import json
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from src.monkey_brain.kernel.compile import _obs
from src.monkey_brain.kernel.compile.sparse import SparseMatrix
from src.monkey_brain.kernel.compile.tensor import Feature, SparseTransitionTensor

logger = logging.getLogger("agentos.compile.context")


# Lower number = more urgent (min-heap). Named points; any int is valid.
class Priority:
    URGENT = 0  # a revoked permission, a safety stop
    HIGH = 25
    NORMAL = 100
    LOW = 200


@dataclass(frozen=True)
class Constraints:
    """Immutable snapshot of the current runtime constraints the context holds."""

    allowed_domains: frozenset[str] | None = None  # None = any domain
    forbidden_states: frozenset[str] = frozenset()
    max_cost: float | None = None
    max_latency_ms: float | None = None
    min_confidence: float = 0.0

    def permits(self, world: SparseTransitionTensor, src: str, dst: str) -> bool:
        for s in (src, dst):
            if self.allowed_domains is not None and world.domain_of(s) not in self.allowed_domains:
                return False
            if s in self.forbidden_states:
                return False
        if self.max_cost is not None and world.feature(src, dst, Feature.COST) > self.max_cost:
            return False
        if self.max_latency_ms is not None and world.feature(src, dst, Feature.LATENCY) > self.max_latency_ms:
            return False
        if world.feature(src, dst, Feature.CONFIDENCE) < self.min_confidence:
            return False
        return True


@dataclass(frozen=True)
class ContextChange:
    """A single change to the runtime constraints."""

    op: str  # allow_domains | forbid_state | unforbid_state | max_cost | max_latency | min_confidence
    value: Any = None


@dataclass(order=True)
class _Queued:
    priority: int
    seq: int
    change: ContextChange = field(compare=False)


class Context:
    """Global, mutable runtime context. Constraint changes are prioritized and queued;
    the world is fixed and only masked through the current constraints."""

    def __init__(self, constraints: Constraints | None = None, tenant_id: str = "") -> None:
        self._constraints = constraints or Constraints()
        self._heap: list[_Queued] = []
        self._seq = 0
        self.tenant_id = tenant_id  # first-class: the tenant this context is scoped to

    @classmethod
    def from_token(cls, claims: "dict[str, Any]", constraints: Constraints | None = None) -> "Context":
        """token claim → context. Extract the tenant from a decoded auth token's claims
        (tenant | tenant_id | org | org_id) so tenancy flows from identity into runtime.
        """
        tenant = str(claims.get("tenant") or claims.get("tenant_id") or claims.get("org") or claims.get("org_id") or "")
        return cls(constraints=constraints, tenant_id=tenant)

    @property
    def constraints(self) -> Constraints:
        return self._constraints

    def pending(self) -> int:
        return len(self._heap)

    # ── queue of prioritized changes ─────────────────────────────────────────────

    def submit(self, change: ContextChange, priority: int = Priority.NORMAL) -> None:
        """Enqueue a constraint change. Not applied until apply_pending() drains the
        queue in priority order (ties broken FIFO by submission)."""
        heapq.heappush(self._heap, _Queued(priority, self._seq, change))
        self._seq += 1

    def apply_pending(self) -> list[ContextChange]:
        """Drain the queue in priority order, folding each change into the constraints.
        Returns the changes in the order applied (most urgent first)."""
        applied: list[ContextChange] = []
        while self._heap:
            q = heapq.heappop(self._heap)
            self._constraints = self._reduce(self._constraints, q.change)
            applied.append(q.change)
        if applied:
            logger.info(
                "[context] applied %d constraint change(s) in priority order: %s",
                len(applied),
                [c.op for c in applied],
            )
            _obs.counter("context.changes_applied", increment=len(applied))
        return applied

    @staticmethod
    def _reduce(c: Constraints, change: ContextChange) -> Constraints:
        op, v = change.op, change.value
        if op == "allow_domains":
            return replace(c, allowed_domains=frozenset(v) if v is not None else None)
        if op == "forbid_state":
            return replace(c, forbidden_states=c.forbidden_states | {v})
        if op == "unforbid_state":
            return replace(c, forbidden_states=c.forbidden_states - {v})
        if op == "max_cost":
            return replace(c, max_cost=v)
        if op == "max_latency":
            return replace(c, max_latency_ms=v)
        if op == "min_confidence":
            return replace(c, min_confidence=float(v))
        return c

    # ── constraint injection: fixed world → constrained operator ─────────────────

    def constrain(self, world: SparseTransitionTensor, f: Feature = Feature.PROBABILITY) -> SparseMatrix:
        """Project the fixed world through the current constraints → a constrained
        operator (SparseMatrix), probabilities renormalized over surviving successors.
        The world is never modified."""
        kept: dict[str, dict[str, float]] = {}
        for src, dst in world:
            if self._constraints.permits(world, src, dst):
                kept.setdefault(src, {})[dst] = world.feature(src, dst, f)
        m = SparseMatrix()
        dropped = world.nnz() - sum(len(s) for s in kept.values())
        for src, succ in kept.items():
            total = sum(succ.values()) or 1.0
            for dst, w in succ.items():
                m.set(src, dst, w / total)
        logger.debug(
            "[context] constrained world: kept %d, dropped %d transitions",
            m.nnz(),
            dropped,
        )
        _obs.gauge("context.constrained_transitions", float(m.nnz()))
        return m

    # ── persistence ──────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Persist the current constraints (the applied snapshot; the transient queue
        is not persisted)."""
        c = self._constraints
        return {
            "allowed_domains": (sorted(c.allowed_domains) if c.allowed_domains is not None else None),
            "forbidden_states": sorted(c.forbidden_states),
            "max_cost": c.max_cost,
            "max_latency_ms": c.max_latency_ms,
            "min_confidence": c.min_confidence,
        }

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict()))

    def load(self, path: str | Path) -> None:
        p = Path(path)
        if not p.exists():
            return
        d = json.loads(p.read_text())
        self._constraints = Constraints(
            allowed_domains=(frozenset(d["allowed_domains"]) if d.get("allowed_domains") is not None else None),
            forbidden_states=frozenset(d.get("forbidden_states", [])),
            max_cost=d.get("max_cost"),
            max_latency_ms=d.get("max_latency_ms"),
            min_confidence=d.get("min_confidence", 0.0),
        )
