"""World Update Coordinator - Single Responsibility.

Responsibility: Context Stream publication, world mutation, graph updates.
Depends on: context stream, gpu world, engine, semantic graph
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("agentos.cognitive.layers.world_coordinator")


class WorldCoordinator:
    """Context Stream publication and world mutation coordination.

    Responsibility: All world mutations MUST go through this component.
    Direct world.observe() calls are forbidden.
    """

    def __init__(self) -> None:
        self._context_stream: Any = None
        self._gpu_world: Any = None
        self._engine: Any = None
        self._semantic_graph: Any = None
        self._tenant_id: str = "default"

    def set_context_stream(self, stream: Any) -> None:
        self._context_stream = stream

    def set_gpu_world(self, gpu_world: Any) -> None:
        self._gpu_world = gpu_world

    def set_engine(self, engine: Any) -> None:
        self._engine = engine

    def set_semantic_graph(self, graph: Any) -> None:
        self._semantic_graph = graph

    def set_tenant_id(self, tenant_id: str) -> None:
        self._tenant_id = tenant_id

    @property
    def semantic_graph(self) -> Any:
        return self._semantic_graph

    def publish_world_update(
        self,
        src: str,
        dst: str,
        *,
        domain: str = "default",
        weight: float = 1.0,
        source: str = "cognitive_runtime",
        metadata: dict | None = None,
    ) -> None:
        """Publish a world mutation through the Context Stream.

        INVARIANT: All world mutations MUST go through this method.
        """
        if self._context_stream is not None and hasattr(self._context_stream, "publish"):
            from src.monkey_brain.kernel.compile.context_stream import (
                ContextEvent,
                EventType,
            )

            event = ContextEvent(
                timestamp=time.time(),
                entity=src,
                attribute=dst,
                previous_value=None,
                current_value=weight,
                source=source,
                confidence=1.0,
                event_type=EventType.TRANSITION_ADD,
                metadata={"domain": domain, **(metadata or {})},
                tenant_id=self._tenant_id,
            )
            self._context_stream.publish(event)
        elif self._gpu_world is not None and hasattr(self._gpu_world, "observe"):
            self._gpu_world.observe(src, dst, domain=domain, weight=weight)
        elif self._engine is not None and hasattr(self._engine, "world"):
            self._engine.world.observe(src, dst, domain=domain, weight=weight)

    def subscribe_context_stream(self) -> None:
        """Subscribe to context stream for world updates."""
        if self._context_stream is None:
            return

        def on_world_update(event: Any, version: Any) -> None:
            if event is None:
                return
            try:
                if self._semantic_graph is not None:
                    self._semantic_graph.on_context_event(event)
                if self._gpu_world is not None:
                    self._gpu_world.apply_context_update(event)
                elif self._engine is not None:
                    if hasattr(event, "transitions"):
                        self._engine.propose_batch(event.transitions)
            except Exception as e:
                logger.error("[context_stream] Event processing failed: %s", e)

        try:
            self._context_stream.subscribe(on_world_update)
        except Exception as e:
            logger.error("[context_stream] Subscription failed: %s", e)

    def populate_belief_from_graph(self, belief: Any, world_view: Any) -> int:
        """Populate belief from SemanticGraph entity relationships."""
        observations = 0
        if self._semantic_graph is not None and world_view is not None:
            graph_transitions = self._semantic_graph.populate_tensor(world_view)
            observations += graph_transitions
        return observations
