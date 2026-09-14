"""DomainOperatorRegistry — the collection of per-domain sparse operators (ADR 0021).

DEPRECATED / SUPERSEDED: SparseTransitionTensor (tensor.py) is the world model and the
proper collection of domain slices, with global state indexing. This registry (separate
per-domain CompiledOperator objects) was an earlier, weaker shape. Retained for reference;
not part of the package's public API. Prefer SparseTransitionTensor.slice()/to_operator().


The persistent library of domain representations. Each domain has one current compiled
operator (its latest block); registering an execution's DomainOperatorSet updates the
domains it touched. This is the substrate for recursive domain composition and the agent
mesh: domains are independent, retrievable, composable blocks, plus an accumulated
inter-domain mesh operator.

Latest-wins per domain (structure = latest execution, mirroring
GraphStore.record_execution_graph), with a version counter so staleness/change is visible.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from src.monkey_brain.kernel.compile.types import CompiledOperator, DomainOperatorSet

logger = logging.getLogger("agentos.compile.registry")


@dataclass
class DomainEntry:
    domain: str
    operator: CompiledOperator
    revision: int
    version: int  # bumps each time this domain is re-registered
    source_graph_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "operator": self.operator.to_dict(),
            "revision": self.revision,
            "version": self.version,
            "source_graph_id": self.source_graph_id,
        }

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "DomainEntry":
        return DomainEntry(
            domain=d["domain"],
            operator=CompiledOperator.from_dict(d["operator"]),
            revision=int(d.get("revision", 0)),
            version=int(d.get("version", 1)),
            source_graph_id=d.get("source_graph_id", ""),
        )


class DomainOperatorRegistry:
    """A collection of compiled domain operators, keyed by domain name."""

    def __init__(self) -> None:
        self._domains: dict[str, DomainEntry] = {}
        self._mesh: CompiledOperator | None = None
        self._lock = threading.Lock()

    # ── register / update ────────────────────────────────────────────────────────

    def register_set(self, op_set: DomainOperatorSet, *, revision: int = 0) -> list[str]:
        """Fold an execution's per-domain operators into the collection.

        Updates every domain the execution touched (latest-wins) and replaces the
        inter-domain mesh. Returns the list of domain names that were updated.
        """
        updated: list[str] = []
        with self._lock:
            for domain, operator in op_set.domains.items():
                updated.append(self._put(domain, operator, revision))
            if op_set.mesh is not None:
                self._mesh = op_set.mesh
        return updated

    def register_domain(self, domain: str, operator: CompiledOperator, *, revision: int = 0) -> str:
        with self._lock:
            return self._put(domain, operator, revision)

    def _put(self, domain: str, operator: CompiledOperator, revision: int) -> str:
        prev = self._domains.get(domain)
        version = (prev.version + 1) if prev else 1
        self._domains[domain] = DomainEntry(
            domain=domain,
            operator=operator,
            revision=revision,
            version=version,
            source_graph_id=operator.source_graph_id,
        )
        logger.debug("registry: domain %r updated to v%d (%d nodes)", domain, version, operator.n)
        return domain

    # ── query ────────────────────────────────────────────────────────────────────

    def get(self, domain: str) -> CompiledOperator | None:
        entry = self._domains.get(domain)
        return entry.operator if entry else None

    def entry(self, domain: str) -> DomainEntry | None:
        return self._domains.get(domain)

    def version(self, domain: str) -> int:
        entry = self._domains.get(domain)
        return entry.version if entry else 0

    def mesh(self) -> CompiledOperator | None:
        return self._mesh

    def domains(self) -> list[str]:
        return sorted(self._domains)

    def summary(self) -> dict[str, Any]:
        return {
            "domains": len(self._domains),
            "total_nodes": sum(e.operator.n for e in self._domains.values()),
            "total_nnz": sum(e.operator.nnz for e in self._domains.values()),
            "has_mesh": self._mesh is not None,
            "mesh_domains": self._mesh.n if self._mesh else 0,
            "by_domain": {d: {"nodes": e.operator.n, "version": e.version} for d, e in sorted(self._domains.items())},
        }

    def __contains__(self, domain: str) -> bool:
        return domain in self._domains

    def __len__(self) -> int:
        return len(self._domains)

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._domains))

    # ── persistence ──────────────────────────────────────────────────────────────

    def save(self, path: Path | str) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "domains": {d: e.to_dict() for d, e in self._domains.items()},
            "mesh": self._mesh.to_dict() if self._mesh else None,
        }
        path.write_text(json.dumps(payload, indent=2))

    def load(self, path: Path | str) -> None:
        path = Path(path)
        if not path.exists():
            return
        payload = json.loads(path.read_text())
        with self._lock:
            self._domains = {d: DomainEntry.from_dict(e) for d, e in payload.get("domains", {}).items()}
            self._mesh = CompiledOperator.from_dict(payload["mesh"]) if payload.get("mesh") else None
