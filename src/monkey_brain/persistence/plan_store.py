"""PlanStore — durable persistence for planned execution graphs.

Saves plans to:
1. Local filesystem: {plans_dir}/{run_id}/graph.json, intent_ir.json
2. Neo4j: via GraphStore.sync_graph()

Unlike RunStore (process-local, ephemeral), PlanStore survives restarts.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("agentos.plan_store")

_DEFAULT_PLANS_DIR = "/tmp/monkeybrain-plans"

# run_id becomes a path segment ({plans_dir}/{run_id}/graph.json). It is
# client-supplied on /execute (payload.run_id), so it must never be able to
# escape the plans directory. Server-minted run_ids are uuid4().hex; accept
# only that shape and reject anything with separators or "..".
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _validate_run_id(run_id: str) -> str:
    """Return run_id if it is a safe single path segment, else raise.

    Blocks path traversal ("../", absolute paths, separators) before the
    value is ever joined onto the plans directory.
    """
    if not isinstance(run_id, str) or not _SAFE_RUN_ID.match(run_id):
        raise ValueError(f"unsafe run_id for filesystem use: {run_id!r}")
    return run_id


class PlanStore:
    """Durable plan persistence — local files + Neo4j."""

    _instance: "PlanStore | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "PlanStore":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._plans_dir = Path(os.environ.get("PLANS_DIR", _DEFAULT_PLANS_DIR))
                    cls._instance = instance
        return cls._instance

    @property
    def plans_dir(self) -> Path:
        return self._plans_dir

    def save_local(
        self,
        run_id: str,
        graph: dict[str, Any],
        intent_ir: dict[str, Any] | None = None,
    ) -> Path:
        """Save plan graph and intent IR to local filesystem.

        Returns the plan directory path.
        """
        run_id = _validate_run_id(run_id)
        plan_dir = self._plans_dir / run_id
        plan_dir.mkdir(parents=True, exist_ok=True)

        graph_path = plan_dir / "graph.json"
        graph_path.write_text(json.dumps(graph, indent=2, default=str))

        if intent_ir is not None:
            ir_path = plan_dir / "intent_ir.json"
            ir_path.write_text(json.dumps(intent_ir, indent=2, default=str))

        logger.info("Plan saved locally: %s (%d nodes)", plan_dir, len(graph.get("nodes", [])))
        return plan_dir

    def load_local(self, run_id: str) -> dict[str, Any] | None:
        """Load a plan graph from local filesystem.

        A malformed/unsafe run_id is treated as a miss (returns None), not an
        error — callers already handle "no plan for this run_id", and a
        traversal attempt must never reach the filesystem.
        """
        try:
            run_id = _validate_run_id(run_id)
        except ValueError:
            logger.warning("load_local rejected unsafe run_id: %r", run_id)
            return None
        graph_path = self._plans_dir / run_id / "graph.json"
        if not graph_path.exists():
            return None
        return json.loads(graph_path.read_text())

    def list_local(self) -> list[str]:
        """List all locally stored run_ids."""
        if not self._plans_dir.exists():
            return []
        return [d.name for d in self._plans_dir.iterdir() if d.is_dir() and (d / "graph.json").exists()]

    async def save_neo4j(self, graph: dict[str, Any], graph_store: Any = None) -> bool:
        """Save plan graph to Neo4j via GraphStore (append mode).

        Returns True if saved, False if graph_store not connected.
        """
        if graph_store is None:
            logger.debug("No graph_store provided — skipping Neo4j sync")
            return False

        if not graph_store.is_connected():
            logger.debug("GraphStore not connected — skipping Neo4j sync")
            return False

        try:
            await graph_store.sync_graph(graph, append=True)
            logger.info("Plan synced to Neo4j: %d nodes", len(graph.get("nodes", [])))
            return True
        except Exception as exc:
            logger.warning("Neo4j sync failed: %s", exc)
            return False

    async def save(
        self,
        run_id: str,
        graph: dict[str, Any],
        intent_ir: dict[str, Any] | None = None,
        graph_store: Any = None,
    ) -> dict[str, Any]:
        """Save plan to all durable stores (local + Neo4j).

        Returns a summary of what was saved.
        """
        result = {
            "run_id": run_id,
            "local": False,
            "neo4j": False,
            "plan_dir": None,
        }

        # Local filesystem
        try:
            plan_dir = self.save_local(run_id, graph, intent_ir)
            result["local"] = True
            result["plan_dir"] = str(plan_dir)
        except Exception as exc:
            logger.error("Local save failed for run=%s: %s", run_id, exc)

        # Neo4j deliberately NOT written at plan time. Per-run plan-step
        # instance nodes (one fresh set per run_id, forever) are exactly the
        # growth the three-layer model exists to avoid: Neo4j holds only
        # canonical, name-keyed nodes (Agent/ExecutionGraph/Capability/...),
        # merged at execute-completion by GraphStore.record_execution_graph
        # — joining on common names, one node per identity regardless of how
        # many runs touch it. The full per-run graph lives here (graph.json)
        # and in Mongo; recovery reads it back by run_id (load_local).
        result["neo4j"] = False

        logger.info(
            "Plan persistence: run=%s local=%s neo4j=%s",
            run_id,
            result["local"],
            result["neo4j"],
        )
        return result


def get_plan_store() -> PlanStore:
    return PlanStore()
