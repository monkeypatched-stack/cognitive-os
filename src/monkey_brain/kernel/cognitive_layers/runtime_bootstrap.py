"""Runtime Bootstrap - Single Responsibility.

Responsibility: Boot, shutdown, dependency injection, subsystem initialization,
lifecycle management, health checks.
Depends on: bootstrap module, all subsystem components
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("agentos.cognitive.layers.runtime_bootstrap")


class RuntimeBootstrap:
    """Handles runtime lifecycle: boot, shutdown, dependency wiring.

    Responsible for:
    - Attaching kernel-injected dependencies
    - Wiring subscribers
    - Initializing domain registry
    - Running health checks
    - Graceful shutdown
    """

    def __init__(self) -> None:
        self._semantic_graph: Any = None
        self._semantic_graph_connect_task: Any = None

    async def boot(
        self,
        rt: Any,
        app: Any,
        *,
        lemon: Any = None,
        persistence: Any = None,
        event_bus: Any = None,
        semantic_memory: Any = None,
        graph_manager: Any = None,
    ) -> None:
        """Attach Kernel-injected dependencies to the runtime."""
        from src.monkey_brain.api import bootstrap as boot_mod

        rt._event_bus = event_bus
        rt.lemon = lemon
        rt.semantic_memory = semantic_memory
        rt.graph_manager = graph_manager
        rt.persistence = persistence or getattr(app.state, "_persistence", None)
        rt.wolverine = getattr(app.state, "_wolverine", None)

        rt.pcp = getattr(app.state, "_pcp", None)
        rt.policy = getattr(app.state, "_policy", None)
        rt.observer = getattr(app.state, "_observer", None)
        rt.learning = getattr(app.state, "_learning", None)
        rt.execution_graph = None
        rt.graph_store = getattr(app.state, "_graph_store", None)

        await boot_mod.wire_subscribers(rt.wolverine, rt.policy, rt.observer, rt.learning, rt.lemon)
        rt.domain_registry = await boot_mod.init_domain_registry(app)
        await boot_mod.run_health_checks(app, rt.persistence, rt.lemon, rt.wolverine, rt.policy)

        # Initialize SemanticGraph
        try:
            from src.monkey_brain.kernel.compile.semantic_graph import SemanticGraph

            self._semantic_graph = SemanticGraph()
            try:
                loop = asyncio.get_running_loop()
                # Fire-and-forget so a slow Neo4j handshake doesn't add to
                # boot latency — but the task reference is kept (previously
                # discarded) so shutdown() can await it before closing the
                # driver. Without this, every boot leaked its own Neo4j
                # driver: shutdown() never called semantic_graph.close() at
                # all (not passed to bootstrap.shutdown() below), and even
                # with that fixed, closing immediately could race a connect
                # that hadn't finished yet, leaving THAT driver orphaned
                # instead. Confirmed as the root cause of ~150+ pytest
                # failures ("PlanetaryRuntime not available") once enough
                # sequential test-module boots had each leaked one driver —
                # Neo4j's connection pool exhausted partway through the full
                # suite; the same test passed cleanly in isolation.
                self._semantic_graph_connect_task = loop.create_task(self._semantic_graph.connect())
            except RuntimeError:
                logger.debug("boot: suppressed exception", exc_info=True)
            logger.info("[bootstrap] SemanticGraph initialized")
        except Exception as exc:
            logger.warning("[bootstrap] SemanticGraph init failed: %s", exc)

        rt.lemon.info("CognitiveRuntime boot complete", component="bootstrap")
        logger.info("CognitiveRuntime boot complete")
        await rt._publish("cognitive_runtime.booted", {})

    async def shutdown(self, rt: Any, app: Any) -> None:
        """Tear down every subsystem in reverse dependency order."""
        # Let a still-in-flight connect() finish (bounded wait) before
        # closing — closing while _driver is still unset would silently
        # no-op (SemanticGraph.close()'s own guard) and orphan the driver
        # the task creates moments later, since nothing awaits it after this.
        if self._semantic_graph_connect_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._semantic_graph_connect_task), timeout=5.0)
            except Exception as exc:
                logger.warning(
                    "[bootstrap] SemanticGraph connect did not finish before shutdown: %s",
                    exc,
                )

        from src.monkey_brain.api import bootstrap as boot_mod

        await boot_mod.shutdown(app, rt.persistence, rt.lemon, rt.pcp, rt.graph_store, self._semantic_graph)

    @property
    def semantic_graph(self) -> Any:
        return self._semantic_graph
