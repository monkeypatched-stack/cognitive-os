"""CognitiveOS Edge Agent — the missing deployment substrate for running a
persistent Actor as a local process on a normal Linux edge device, instead
of a Kubernetes Pod.

    Actor Artifact / Binary  (unchanged — same monkeybrain/agentos image or
                               plain venv, same src.monkey_brain.actor_
                               runtime:app ASGI export Kubernetes already
                               uses)
           |
    Actor Runtime            (unchanged — actor_runtime.py; this module
                               never re-implements ANY of its cognition,
                               registration, lifecycle, or Society/World
                               logic)
           |
    Execution Node            (an edge device instead of a Pod — this
                               module's only real job: make "start/stop
                               that same binary as a local subprocess"
                               possible, remotely, the way Kubernetes
                               already makes "start/stop that same image
                               as a container" possible)

THIS MODULE IS PURE INFRASTRUCTURE GLUE, deliberately: it owns process
supervision (start/stop/restart-on-crash) for one or more actor_runtime.py
subprocesses on this device, and nothing else. It NEVER touches Actor
cognition, belief, goals, authority, or decisions — those live entirely
inside the actor_runtime.py subprocess it supervises, exactly as they do
on Kubernetes. This is the literal implementation of Section 5's own
distinction: the Edge Agent owns process lifecycle/installation/runtime
supervision/device-level concerns; the Actor owns cognition/beliefs/goals/
decisions/authority/state.

Device identity vs. Actor identity (Section 14): this process has its own
stable device_id (EDGE_DEVICE_ID env var, defaulting to the hostname) —
purely a label for "which physical machine," reported via /health and
logged alongside every action, NEVER used as an actor_id and NEVER
written into any actor's persisted identity. Each actor_runtime.py
subprocess this Agent spawns gets its own distinct COGNITIVEOS_NODE_ID
(f"{device_id}-{actor_id[:12]}") — the same "one process = one node
identity" granularity Kubernetes already uses (one Pod = one node_id),
just simulated on shared edge hardware instead of one-container-per-node,
so multiple actors on the same device never race over a single shared
node_id's capacity/heartbeat bookkeeping in the Scheduler's node registry.

Control-plane side: kernel/society/edge_provisioner.py::EdgeProvisioner
is this module's counterpart — it calls this Agent's small HTTP API
(start/stop/status) exactly the way KubernetesProvisioner shells out to
`kubectl apply`, using the SAME opt-in, never-raises, degrade-to-
"provisioning skipped" contract. See that module's own docstring for the
full push-based provisioning story and its documented limitation (this
pass does not implement a pull/polling fallback for NAT'd/firewalled
devices the control plane cannot reach — see EDGE_DEPLOYMENT_REPORT.md).

Run with:
    EDGE_DEVICE_ID=edge-007 python -m uvicorn src.monkey_brain.edge_agent:app \\
        --host 0.0.0.0 --port 8061
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agentos.edge_agent")

EDGE_AGENT_VERSION = "1.0.0"
DEFAULT_EDGE_AGENT_PORT = 8061
"""The well-known port convention EdgeProvisioner (control-plane side)
targets by default — deliberately a plain convention rather than a new
field on the shared, already-heavily-tested ExecutionNode dataclass
(kernel/society/actor_scheduler.py), matching this task's own "reuse
existing interfaces, introduce the smallest abstraction needed" guidance:
adding a reachable-URL field to ExecutionNode would ripple through every
existing Scheduler/Lifecycle Controller call site for zero benefit to the
Kubernetes path. An operator whose edge fleet needs a non-default port
sets EDGE_AGENT_PORT consistently on both sides (this Agent's own bind
port and EDGE_AGENT_PORT read by EdgeProvisioner)."""


def _device_id() -> str:
    return os.getenv("EDGE_DEVICE_ID", "").strip() or socket.gethostname()


@dataclass
class _ManagedActor:
    actor_id: str
    node_class: str
    port: int
    process: "subprocess.Popen[bytes]"
    started_at: float = field(default_factory=time.time)
    restart_count: int = 0
    stopped: bool = False
    """Set True by an explicit /stop call — distinguishes an intentional
    stop (no auto-restart) from an unexpected crash (auto-restart, see
    _supervise_loop) without needing to inspect the subprocess's own exit
    code, which actor_runtime.py's own shutdown path doesn't distinguish
    either way."""


class EdgeAgent:
    """Owns zero or more actor_runtime.py subprocesses on this device.
    One instance per Edge Agent process — analogous in spirit to
    KubernetesProvisioner, but here the "provisioner" and the thing being
    provisioned onto share a single OS, so this class does the actual
    process management directly instead of shelling out to a separate
    orchestrator."""

    def __init__(self) -> None:
        self.device_id = _device_id()
        self.started_at = time.time()
        self._actors: dict[str, _ManagedActor] = {}
        self._next_port = int(os.getenv("EDGE_ACTOR_PORT_BASE", "8100"))
        self._supervisor_task: "asyncio.Task[None] | None" = None
        self._node_task: "asyncio.Task[None] | None" = None

    # ── device-level self-registration (Section 14/9) ───────────────────

    async def register_device_heartbeat_loop(self) -> None:
        """Self-registers this device (NOT any individual actor) as a
        Scheduler-visible ExecutionNode with node_class=EDGE, reusing
        PlanetaryRuntime.register_node()/heartbeat_node() unchanged —
        the exact same registry mechanism a Kubernetes actor Pod already
        uses at its own boot, just called from this supervisor process
        instead of from inside an actor_runtime.py process. This is
        purely for fleet visibility (`GET /scheduler/nodes` shows this
        device is alive) -- Actor placement itself is still decided
        per-actor-subprocess (see class docstring: each subprocess gets
        its own distinct node_id), so this device-level registration's
        own node_id is deliberately never targeted by actor placement
        directly.

        Live Deployment Validation finding #1: calls register_node()
        directly rather than the higher-level register_self_as_node()
        convenience wrapper, because that wrapper hard-codes
        current_actor_count = sum(len(sr.all_actors()) for every
        society this PlanetaryRuntime instance can see -- correct for
        an actor_runtime.py process (single-actor-scoped, so that sum
        really is "how many actors THIS process hosts"), but WRONG here:
        this device-heartbeat PlanetaryRuntime is deliberately unscoped
        (no ACTOR_ID) so it can reach the full registry, meaning that
        same sum silently became "every actor in the entire fleet" --
        confirmed live, this made a nearly-idle device with 1 real
        actor report current_actor_count=11 (the fleet total) against
        capacity=10, making the Scheduler correctly treat it as full
        and reject placement.

        Live Deployment Validation finding #2, more fundamental:
        registering this device with a real, nonzero capacity makes it
        a SECOND valid scheduling candidate alongside every individual
        actor subprocess's own per-actor node registration (see class
        docstring -- each spawned actor gets its own node_id, the same
        1:1 granularity Kubernetes uses). Confirmed live: an actor that
        had already explicitly self-claimed its OWN per-actor node
        (via ACTOR_CLAIM_PLACEMENT, actor_runtime.py's own start())
        got its desired_node_id silently overwritten back to THIS
        device's own node_id on the very next ordinary reconcile,
        because schedule()'s generic constraint-matching has no reason
        to prefer one valid EDGE-class candidate over another and
        picked this one instead -- a real placement flip-flop between
        two registrations that both legitimately claimed to satisfy
        "required_node_class=edge" for the SAME actor. capacity=0 below
        is the fix: this registration stays fully visible for fleet/
        health monitoring (`GET /scheduler/nodes` still shows the
        device is alive) but can never itself satisfy an actor's
        min_available_capacity requirement, so it can never be chosen
        as a placement target -- only each actor's own explicit
        self-claim can be."""
        from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
        from src.monkey_brain.kernel.society.domain import Society
        from src.monkey_brain.kernel.society.actor_scheduler import (
            ExecutionNode,
            NodeClass,
        )

        node_id = f"{self.device_id}-edge-agent"
        os.environ.setdefault("COGNITIVEOS_NODE_ID", node_id)
        try:
            pr = PlanetaryRuntime(Society(name=f"edge-agent-{self.device_id}"))
        except Exception as exc:
            logger.warning(
                "EdgeAgent could not reach control-plane persistence (%s) — device heartbeat disabled, actor subprocess management still works",
                exc,
            )
            return
        interval = float(os.getenv("EDGE_AGENT_HEARTBEAT_INTERVAL", "30"))
        while True:
            try:
                pr.register_node(
                    ExecutionNode(
                        node_id=node_id,
                        node_class=NodeClass.EDGE,
                        capacity=0,
                        current_actor_count=0,
                    )
                )
            except Exception as exc:
                logger.debug("EdgeAgent device heartbeat failed (non-fatal): %s", exc)
            await asyncio.sleep(interval)

    # ── actor subprocess management ──────────────────────────────────────

    def start_actor(
        self,
        actor_id: str,
        *,
        node_class: str = "edge",
        artifact_version: str = "",
        claim_placement: bool = True,
    ) -> dict[str, Any]:
        """Spawns actor_runtime.py as a local subprocess for actor_id —
        the EXACT same ASGI export (src.monkey_brain.actor_runtime:app)
        and env-var configuration contract Kubernetes' own actor-
        deployment.yaml uses, so this subprocess runs 100% identical
        cognition/registration/lifecycle/Society/World code, zero
        duplication. Idempotent: a second start_actor() call for an
        already-running actor_id is a no-op returning its existing
        status, matching kubectl apply's own idempotent-by-construction
        contract KubernetesProvisioner relies on."""
        existing = self._actors.get(actor_id)
        if existing is not None and existing.process.poll() is None:
            return self._describe(existing)

        port = self._next_port
        self._next_port += 1
        env = dict(os.environ)
        env.update(
            {
                "ACTOR_ID": actor_id,
                "ACTOR_NODE_CLASS": node_class,
                "ACTOR_NODE_ID": f"{self.device_id}-{actor_id[:12]}",
                "COGNITIVEOS_NODE_ID": f"{self.device_id}-{actor_id[:12]}",
                "ACTOR_CLAIM_PLACEMENT": "true" if claim_placement else "false",
                "ACTOR_RUNTIME_PORT": str(port),
            }
        )
        if artifact_version:
            env["ACTOR_ARTIFACT_VERSION"] = artifact_version

        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "src.monkey_brain.actor_runtime:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        managed = _ManagedActor(actor_id=actor_id, node_class=node_class, port=port, process=process)
        self._actors[actor_id] = managed
        logger.info(
            "EdgeAgent(%s): started actor_id=%s pid=%d port=%d",
            self.device_id,
            actor_id,
            process.pid,
            port,
        )
        return self._describe(managed)

    def stop_actor(self, actor_id: str, *, timeout: float = 15.0) -> bool:
        """Graceful stop: SIGTERM (actor_runtime.py's own shutdown()
        handler checkpoints belief and deregisters this subprocess's
        node before exiting — same graceful-shutdown contract a
        Kubernetes Pod's own SIGTERM handling relies on), SIGKILL only
        if it doesn't exit in time. Marks `stopped=True` so the
        supervisor loop does not treat this as a crash to auto-restart."""
        managed = self._actors.get(actor_id)
        if managed is None:
            return False
        managed.stopped = True
        if managed.process.poll() is None:
            managed.process.send_signal(signal.SIGTERM)
            try:
                managed.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                managed.process.kill()
                managed.process.wait(timeout=5.0)
        logger.info("EdgeAgent(%s): stopped actor_id=%s", self.device_id, actor_id)
        return True

    def remove_actor(self, actor_id: str) -> bool:
        """Stop (if running) and forget this actor_id entirely — does
        NOT unregister the Actor from the durable Registry (that would
        DELETE the Actor's identity, exactly the invariant actor_
        runtime.py's own shutdown() docstring already establishes this
        module must never do); it only stops managing the local
        subprocess."""
        stopped = self.stop_actor(actor_id)
        self._actors.pop(actor_id, None)
        return stopped

    def status(self, actor_id: str) -> dict[str, Any] | None:
        managed = self._actors.get(actor_id)
        if managed is None:
            return None
        return self._describe(managed)

    def list_actors(self) -> list[dict[str, Any]]:
        return [self._describe(m) for m in self._actors.values()]

    def _describe(self, managed: _ManagedActor) -> dict[str, Any]:
        alive = managed.process.poll() is None
        return {
            "actor_id": managed.actor_id,
            "device_id": self.device_id,
            "node_class": managed.node_class,
            "port": managed.port,
            "pid": managed.process.pid,
            "running": alive,
            "stopped": managed.stopped,
            "restart_count": managed.restart_count,
            "started_at": managed.started_at,
        }

    # ── local crash recovery (Section 13) ───────────────────────────────

    async def supervise_loop(self) -> None:
        """The Edge Agent's own equivalent of Kubernetes' kubelet restart
        policy: if a managed subprocess exits unexpectedly (not via an
        explicit stop_actor() call), restart it with the SAME actor_id/
        node_class/artifact_version. Same actor_id -> same durable
        Registry identity -> actor_runtime.py's own boot sequence
        restores belief from ActorStateStore and reconciles back to
        RUNNING exactly as it would after a Kubernetes Pod restart -- no
        separate edge recovery model, this just re-invokes the same
        start_actor() path."""
        interval = float(os.getenv("EDGE_AGENT_SUPERVISE_INTERVAL", "5"))
        while True:
            for actor_id, managed in list(self._actors.items()):
                if managed.stopped:
                    continue
                if managed.process.poll() is not None:
                    logger.warning(
                        "EdgeAgent(%s): actor_id=%s (pid=%d) exited unexpectedly (code=%s) — restarting",
                        self.device_id,
                        actor_id,
                        managed.process.pid,
                        managed.process.returncode,
                    )
                    restart_count = managed.restart_count + 1
                    self.start_actor(actor_id, node_class=managed.node_class)
                    self._actors[actor_id].restart_count = restart_count
            await asyncio.sleep(interval)


# ── ASGI app (uvicorn src.monkey_brain.edge_agent:app) ────────────────────


def _build_app() -> Any:
    from fastapi import FastAPI, HTTPException

    fastapi_app = FastAPI(title="CognitiveOS Edge Agent", version=EDGE_AGENT_VERSION)
    agent_holder: dict[str, EdgeAgent] = {}

    @fastapi_app.on_event("startup")
    async def _boot() -> None:
        agent = EdgeAgent()
        agent_holder["agent"] = agent
        agent._supervisor_task = asyncio.create_task(agent.supervise_loop())
        agent._node_task = asyncio.create_task(agent.register_device_heartbeat_loop())
        logger.info("EdgeAgent started: device_id=%s", agent.device_id)

    @fastapi_app.on_event("shutdown")
    async def _stop() -> None:
        agent = agent_holder.get("agent")
        if agent is None:
            return
        for task in (agent._supervisor_task, agent._node_task):
            if task is not None:
                task.cancel()
        for actor_id in list(agent._actors):
            agent.stop_actor(actor_id)

    def _agent() -> EdgeAgent:
        agent = agent_holder.get("agent")
        if agent is None:
            raise RuntimeError("Edge Agent not started")
        return agent

    @fastapi_app.get("/health")
    async def health() -> dict:
        agent = _agent()
        return {
            "status": "alive",
            "device_id": agent.device_id,
            "version": EDGE_AGENT_VERSION,
            "started_at": agent.started_at,
            "managed_actors": len(agent._actors),
        }

    @fastapi_app.get("/actors")
    async def list_actors() -> list[dict]:
        return _agent().list_actors()

    @fastapi_app.post("/actors/{actor_id}/start")
    async def start_actor(actor_id: str, body: dict | None = None) -> dict:
        body = body or {}
        return _agent().start_actor(
            actor_id,
            node_class=body.get("node_class", "edge"),
            artifact_version=body.get("artifact_version", ""),
            claim_placement=body.get("claim_placement", True),
        )

    @fastapi_app.post("/actors/{actor_id}/stop")
    async def stop_actor(actor_id: str) -> dict:
        ok = _agent().stop_actor(actor_id)
        if not ok:
            raise HTTPException(
                status_code=404,
                detail=f"actor_id {actor_id!r} not managed on this device",
            )
        return {"actor_id": actor_id, "stopped": True}

    @fastapi_app.post("/actors/{actor_id}/restart")
    async def restart_actor(actor_id: str) -> dict:
        agent = _agent()
        managed = agent._actors.get(actor_id)
        if managed is None:
            raise HTTPException(
                status_code=404,
                detail=f"actor_id {actor_id!r} not managed on this device",
            )
        node_class = managed.node_class
        agent.stop_actor(actor_id)
        return agent.start_actor(actor_id, node_class=node_class)

    @fastapi_app.delete("/actors/{actor_id}")
    async def remove_actor(actor_id: str) -> dict:
        ok = _agent().remove_actor(actor_id)
        return {"actor_id": actor_id, "removed": ok}

    @fastapi_app.get("/actors/{actor_id}/status")
    async def actor_status(actor_id: str) -> dict:
        status = _agent().status(actor_id)
        if status is None:
            raise HTTPException(
                status_code=404,
                detail=f"actor_id {actor_id!r} not managed on this device",
            )
        return status

    return fastapi_app


app = _build_app()
