"""PerturbationQueue — the real Perturbation Queue the "real-time world
changes" spec describes: a World Perturbation is PUBLISHED here by
whatever reported it (an operator via POST /planet/perturbations, an
actor's own ReportWorldPerturbation plan step, a future automated
producer), and the Planetary Runtime DRAINS it in batches during each
Planetary Tick (kernel/society/integration.py::_run_cycle), reconciling
each entry into the Global World Knowledge Graph (kernel/society/world.py
::SharedWorld) the same way this session's OBSERVATION-event
reconciliation pass already does.

Deliberately does not target kernel/knowledge_graph.py::KnowledgeGraph
(the separate commerce/business store ReportWorldPerturbationCapability
mutates synchronously and immediately, unchanged by this module) --
unifying those two world stores is a larger, separate concern. This queue
is additive: it gives SharedWorld (and, downstream, Deja Vu) a real signal
to react to, without changing the existing immediate commerce-KG mutation
contract actors and admins already depend on.

One PerturbationQueue instance per PlanetaryRuntime (constructed in
PlanetaryRuntime.__init__ alongside self._world/self._context_engine/
self._relationships, exposed the same way) -- both producers (the admin
route, ReportWorldPerturbationCapability) and the sole consumer
(PlanetaryRuntime._run_cycle) already have a `pr` reference in hand, so
this needs no module-level singleton the way transaction_event_hub.py's
does (that one exists specifically because its WS route handler and
deep-kernel coordinator have no PlanetaryRuntime reference bridging them).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class WorldPerturbation:
    """One reported change to the world, queued for the next Planetary
    Tick to reconcile into SharedWorld."""

    perturbation_id: str = field(default_factory=lambda: uuid4().hex)
    entity_id: str = ""
    description: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    timestamp: float = field(default_factory=time.time)


class PerturbationQueue:
    """FIFO buffer of WorldPerturbations awaiting the next Planetary Tick.
    publish() is the only write; drain() empties the queue and returns
    everything queued since the last drain -- "continuously consumes
    perturbations in batches," not a per-item ack/retry protocol, matching
    this codebase's other in-process queues (TransactionEventHub)."""

    def __init__(self) -> None:
        self._pending: list[WorldPerturbation] = []

    def publish(self, perturbation: WorldPerturbation) -> None:
        self._pending.append(perturbation)

    def drain(self) -> list[WorldPerturbation]:
        pending, self._pending = self._pending, []
        return pending

    def __len__(self) -> int:
        return len(self._pending)
