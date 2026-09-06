"""CognitiveOS Actor Runtime — the canonical Actor executable entry point
(Actor Artifact model, docs/ACTOR_ARTIFACT.md).

    Actor Artifact / Binary  (this module + the shared monkeybrain/agentos
                               image + a per-actor-id container command)
           ↓
    Actor Runtime            (this module's process lifecycle: config,
                               health, checkpoint, node registration)
           ↓
    Execution Node            (cloud pod, edge host, device, robot —
                               all the SAME binary, a config difference)

THE CENTRAL INVARIANT THIS MODULE EXISTS TO UPHOLD: this process hosts an
EXISTING, already-registered Actor identity — it never creates one. An
actor_id is established once, through the normal canonical registration
path (PlanetaryRuntime.register_actor(), via the cloud API or CLI), and
this runtime's only job is to instantiate that SAME persistent Actor's
cognition somewhere. Restarting this process, moving it to a different
image tag, or running it on a different node never produces a different
Actor — see ACTOR_ARTIFACT_VERSION/ACTOR_RUNTIME_VERSION below, both of
which are pure observability metadata attached to the actor's registry
record, never part of its identity.

Deliberately NOT a new cognitive implementation: every real piece of work
this module does — restoring belief, reconciling lifecycle, ticking
cognition, resolving communication — delegates entirely to
PlanetaryRuntime/ActorLifecycleController/ActorScheduler (all built
earlier this session). This module is infrastructure glue: load config,
construct one PlanetaryRuntime, point it at one actor_id, expose health,
shut down cleanly. It reuses the exact same "one FastAPI app, uvicorn
entrypoint, env-var config" shape src/sync/edge_server.py already
established for the OLD, disconnected EdgeActor prototype — see that
module's own updated docstring for why THIS module supersedes it for any
real (governed, LLM-driven, registry-integrated) deployment.

Configuration (env vars — CLI/K8s-friendly, matching this repo's
established convention; an optional --config file supplies defaults an
env var can still override):

    ACTOR_ID                  — required: must already exist in the
                                 Actor Registry (see bootstrap exception
                                 below).
    ACTOR_ARTIFACT_VERSION    — operator-assigned version of this
                                 deployment, e.g. "1.4". Pure metadata.
    ACTOR_RUNTIME_VERSION     — set automatically to this module's own
                                 ACTOR_RUNTIME_VERSION constant unless
                                 overridden.
    ACTOR_NODE_ID             — this process's node identity (defaults to
                                 COGNITIVEOS_NODE_ID's own default: a
                                 random id, or the pod name via
                                 COGNITIVEOS_NODE_ID in Kubernetes).
    ACTOR_NODE_CLASS          — cloud (default) / edge / device / robot.
    ACTOR_NODE_CAPACITY       — this node's actor-slot capacity (default 1
                                 — the one-actor-per-process model
                                 edge-actor-deployment.yaml already
                                 established).
    ACTOR_NODE_CAPABILITIES   — comma-separated capability tags.
    ACTOR_NODE_REGION         — free-text region label.
    ACTOR_CLAIM_PLACEMENT     — if "true", explicitly claims this actor
                                 for this node (set_actor_desired_node)
                                 on boot rather than only consulting the
                                 Scheduler's existing decision. Default
                                 false — a redeploy of an already-placed
                                 actor should not silently steal it from
                                 wherever it legitimately runs; an
                                 operator doing first-time bring-up or a
                                 deliberate migration sets this
                                 explicitly.
    ACTOR_BOOTSTRAP_IF_MISSING — if "true", registers a brand-new actor
                                 (name=ACTOR_ID) when the registry has no
                                 record for ACTOR_ID at all. Default
                                 false; exists for local/dev convenience,
                                 never silently used in a real deployment.
    ACTOR_TICK_INTERVAL        — auto-tick cadence, seconds (default 300,
                                 matching kernel.py's own default).
    ACTOR_RUNTIME_PORT         — health/status ASGI port (default 8051).

Startup sequence (Section 9): load config -> establish node identity ->
connect to Society infrastructure (construct PlanetaryRuntime) -> verify
(or bootstrap) Actor identity -> restore persistent Actor state + reach a
scheduling decision (PlanetaryRuntime.lifecycle.reconcile, which already
implements exactly this restore-then-activate sequence) -> begin
cognitive execution (auto-tick) -> report readiness. Consequential
execution (real capability calls) never begins before this sequence
completes, because it never begins before the Actor reaches ACTIVE via
the same governed reconcile()/tick_one_actor() path the cloud API uses.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Same sys.path insertions as api/main.py (see that module's own comment
# for why each is needed) -- required here too because POST /execute
# lazily imports src.monkey_brain.api.routes.actors, which imports
# `services.common.opa` at module level; that name only resolves via the
# domains/manufacturing/knowledge insertion below. Without this, every
# uvicorn src.monkey_brain.actor_runtime:app process (the real per-actor
# edge/device/robot Pod entrypoint) 500'd on its first /execute call with
# ModuleNotFoundError: No module named 'services.common.opa' -- confirmed
# live booting a real edge actor. parents[2] is the repo root from
# src/monkey_brain/actor_runtime.py (monkey_brain -> src -> repo root),
# one level shallower than main.py's parents[3] since this module has no
# enclosing api/ package directory.
_pkg_cerebellum = Path(__file__).parents[2] / "packages" / "cerebellum"
if _pkg_cerebellum.exists() and str(_pkg_cerebellum) not in sys.path:
    sys.path.insert(0, str(_pkg_cerebellum))

_pkg_services = Path(__file__).parents[2] / "domains" / "manufacturing" / "knowledge"
if _pkg_services.exists() and str(_pkg_services) not in sys.path:
    sys.path.insert(0, str(_pkg_services))

_pkg_broca = Path(__file__).parents[2] / "packages" / "broca"
if _pkg_broca.exists() and str(_pkg_broca) not in sys.path:
    sys.path.insert(0, str(_pkg_broca))

# Imported at module level (not just inside _build_app) because
# `from __future__ import annotations` above turns every handler's type
# hints into strings that FastAPI resolves via get_type_hints() against
# THIS module's globals -- a Request imported only inside _build_app's
# local scope is invisible to that lookup, so `request: Request` silently
# fails to resolve and FastAPI falls back to treating it as a plain query
# parameter (confirmed live: POST /execute returned 422 "field required:
# query.request" with no body/query param ever supplied for it).
from fastapi import Request

logger = logging.getLogger("agentos.actor_runtime")

# Conformance-run finding (live Docker + Kubernetes test): this module is
# invoked two ways -- `python -m src.monkey_brain.actor_runtime run`
# (main(), below) and `uvicorn src.monkey_brain.actor_runtime:app`
# (the Docker/Kubernetes path — main() never runs at all). Neither path
# configured logging: uvicorn only sets up handlers for its OWN
# "uvicorn"/"uvicorn.access"/"uvicorn.error" loggers, not this module's
# "agentos.*" hierarchy or PlanetaryRuntime's own "agentos.planetary_
# runtime" logger. With no handler anywhere in that hierarchy, Python's
# logging falls back to a "handler of last resort" that only ever prints
# WARNING+ — every INFO-level reconciliation/lifecycle log line (exactly
# the ones needed to diagnose why a background reconcile loop isn't
# progressing) was silently discarded in every container/Pod run during
# this session's live conformance testing. Configured at import time
# (not inside main()) so it applies under uvicorn too, regardless of
# which entrypoint launches this module. Respects LOG_LEVEL (the same
# env var kernel.py's own boot sequence already uses) so deployment
# config doesn't need a second logging knob.
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

ACTOR_RUNTIME_VERSION = "1.0.0"
"""This module's own version — independent of ACTOR_ARTIFACT_VERSION
(an operator's deployment version) and of actor_id (permanent identity).
Bump when this module's own startup/health/shutdown behavior changes."""


# ── Configuration ────────────────────────────────────────────────────────

@dataclass
class ActorRuntimeConfig:
    actor_id: str
    node_id: str = ""
    node_class: str = "cloud"
    node_capacity: int = 1
    node_capabilities: tuple[str, ...] = ()
    node_region: str = ""
    artifact_version: str = ""
    runtime_version: str = ACTOR_RUNTIME_VERSION
    claim_placement: bool = False
    bootstrap_if_missing: bool = False
    tick_interval: float = 300.0
    port: int = 8051

    @staticmethod
    def load(config_path: str | None = None, overrides: dict[str, Any] | None = None) -> "ActorRuntimeConfig":
        """Env vars take precedence over the optional config file, which
        takes precedence over built-in defaults — the same "environment
        wins" 12-factor precedence this codebase already uses everywhere
        else (os.getenv(..., default) throughout kernel.py/integration.py).
        `overrides` (CLI args) take precedence over everything, matching
        `cognitiveos-actor run --actor-id X` overriding ACTOR_ID."""
        file_values: dict[str, Any] = {}
        if config_path:
            file_values = _load_config_file(config_path)

        def _get(env_key: str, file_key: str, default: Any) -> Any:
            if os.getenv(env_key) is not None:
                return os.environ[env_key]
            if file_key in file_values:
                return file_values[file_key]
            return default

        actor_id = (overrides or {}).get("actor_id") or _get("ACTOR_ID", "actor_id", "")
        if not actor_id:
            raise ValueError(
                "ACTOR_ID is required (env var, --actor-id, or config file's actor_id) — "
                "the Actor Runtime hosts an existing Actor identity, it cannot start without one"
            )
        node_capabilities_raw = _get("ACTOR_NODE_CAPABILITIES", "node_capabilities", "")
        if isinstance(node_capabilities_raw, str):
            node_capabilities = tuple(c.strip() for c in node_capabilities_raw.split(",") if c.strip())
        else:
            node_capabilities = tuple(node_capabilities_raw or ())

        return ActorRuntimeConfig(
            actor_id=actor_id,
            node_id=str(_get("ACTOR_NODE_ID", "node_id", "") or ""),
            node_class=str(_get("ACTOR_NODE_CLASS", "node_class", "cloud")).lower(),
            node_capacity=int(_get("ACTOR_NODE_CAPACITY", "node_capacity", 1)),
            node_capabilities=node_capabilities,
            node_region=str(_get("ACTOR_NODE_REGION", "node_region", "") or ""),
            artifact_version=str(_get("ACTOR_ARTIFACT_VERSION", "artifact_version", "") or ""),
            runtime_version=str(_get("ACTOR_RUNTIME_VERSION", "runtime_version", ACTOR_RUNTIME_VERSION) or ACTOR_RUNTIME_VERSION),
            claim_placement=_truthy(_get("ACTOR_CLAIM_PLACEMENT", "claim_placement", False)),
            bootstrap_if_missing=_truthy(_get("ACTOR_BOOTSTRAP_IF_MISSING", "bootstrap_if_missing", False)),
            tick_interval=float(_get("ACTOR_TICK_INTERVAL", "tick_interval", 300.0)),
            port=int(_get("ACTOR_RUNTIME_PORT", "port", 8051)),
        )


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _load_config_file(path: str) -> dict[str, Any]:
    """Best-effort: a missing/unreadable/malformed config file degrades to
    "no file values," never crashes the process — env vars and built-in
    defaults remain fully sufficient without one (Section 7: "do not
    hard-code deployment infrastructure into the binary" cuts both ways —
    a config file must be optional, not required)."""
    try:
        with open(path, "r") as f:
            text = f.read()
    except OSError as exc:
        logger.warning("ActorRuntimeConfig: could not read --config %r (%s) — using env vars/defaults only", path, exc)
        return {}
    try:
        if path.endswith((".yaml", ".yml")):
            import yaml
            return yaml.safe_load(text) or {}
        import json
        return json.loads(text) or {}
    except Exception as exc:
        logger.warning("ActorRuntimeConfig: could not parse --config %r (%s) — using env vars/defaults only", path, exc)
        return {}


# ── Readiness states (Section 21: process-alive != Actor-ready) ─────────

class ReadinessState:
    STARTING = "STARTING"
    """Process alive, PlanetaryRuntime not yet constructed / Redis not
    yet confirmed reachable."""
    NOT_FOUND = "NOT_FOUND"
    """Connected to the Registry, but ACTOR_ID has no record and
    ACTOR_BOOTSTRAP_IF_MISSING is not set — a real configuration error,
    surfaced clearly rather than silently creating a new identity."""
    SCHEDULED_ELSEWHERE = "SCHEDULED_ELSEWHERE"
    """The Scheduler has placed this Actor on a DIFFERENT node — correct,
    expected behavior for a node that isn't the current target (e.g.
    during a migration, or a redeploy of a node the Scheduler didn't
    select). This process correctly does nothing further and waits."""
    UNSCHEDULABLE = "UNSCHEDULABLE"
    """The Scheduler found no healthy node satisfying this Actor's
    placement requirements — see docs/ACTOR_SCHEDULER.md. Retried on
    every reconciliation pass."""
    RESTORING = "RESTORING"
    """Reconciliation is in progress (lease held elsewhere, or this
    process's own reconcile() call has not yet completed)."""
    READY = "READY"
    """This Actor is ACTIVE, resident in this process, and this process
    is its correctly scheduled node. Consequential cognition may proceed."""


# ── Runtime state ─────────────────────────────────────────────────────────

class ActorRuntime:
    """Owns exactly the process-lifecycle concerns (Section 16: Actor
    Binary vs. Actor Runtime Infrastructure) — construction, startup,
    health, shutdown. Never touches cognition, planning, or capability
    dispatch directly; every real operation delegates to
    self.planetary_runtime's own already-governed methods."""

    def __init__(self, config: ActorRuntimeConfig, planetary_runtime_factory: Any = None) -> None:
        self.config = config
        self.planetary_runtime: Any = None
        self._planetary_runtime_factory = planetary_runtime_factory
        """Testability seam only: defaults to the real PlanetaryRuntime
        constructor (start() imports and calls it directly when this is
        None). A test that needs a fake-Redis-backed PlanetaryRuntime
        passes a zero-arg callable returning one already configured —
        production code never sets this."""
        self.state: str = ReadinessState.STARTING
        self.state_reason: str = ""
        self.started_at: float = time.time()
        self.ready_since: float | None = None
        # Actor Cell identity (docs/ACTOR_CELL_ARCHITECTURE.md Section J):
        # a per-actor DelegationCredential layered on top of this process's
        # one SPIFFE SVID, minted lazily on first /execute call and re-
        # minted transparently if it expires or the issuer changes -- see
        # kernel/actor_identity.py. Never persisted: a Cell restart just
        # re-minted this from scratch, which is the desired behavior.
        from src.monkey_brain.kernel.actor_identity import ActorCellIdentityCache
        self.cell_identity = ActorCellIdentityCache(actor_id=config.actor_id)
        # Actor Cell -> ROS Adapter binding (docs/ACTOR_CELL_ARCHITECTURE.md
        # Section I/3): built in start() below, only for a node_class=robot
        # deployment, bound to this Cell's own actor_id. None for every
        # other node class -- no ROS relevance for a cloud/edge/device
        # actor, matching today's "one per Pod" convention.
        self.ros_adapter: Any = None

    async def start(self) -> None:
        # Cloud/Edge Actor Convergence, Section 11/31: an edge/device/robot
        # node defaults to enforcing the offline-safety capability gate
        # (kernel/pipeline/offline_safety.py) unless the operator
        # explicitly overrides it -- must be set BEFORE PlanetaryRuntime()
        # is constructed, since it reads this env var once at
        # _attach_society time. A cloud node's default (gate off) is
        # unchanged.
        if self.config.node_class in ("edge", "device", "robot") and "OFFLINE_SAFETY_GATE_ENABLED" not in os.environ:
            os.environ["OFFLINE_SAFETY_GATE_ENABLED"] = "true"
        if self.config.node_id:
            os.environ["COGNITIVEOS_NODE_ID"] = self.config.node_id
        os.environ["ACTOR_ARTIFACT_VERSION"] = self.config.artifact_version
        os.environ["ACTOR_RUNTIME_VERSION"] = self.config.runtime_version

        if self._planetary_runtime_factory is not None:
            self.planetary_runtime = self._planetary_runtime_factory()
        else:
            from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
            self.planetary_runtime = PlanetaryRuntime()
        pr = self.planetary_runtime

        entry = pr.locate_actor(self.config.actor_id)
        if entry is None:
            if not self.config.bootstrap_if_missing:
                self.state = ReadinessState.NOT_FOUND
                self.state_reason = (
                    f"actor_id {self.config.actor_id!r} has no Actor Registry record — "
                    "the Actor Runtime hosts an existing Actor, it does not create one "
                    "(set ACTOR_BOOTSTRAP_IF_MISSING=true to opt into dev/test bootstrap)"
                )
                logger.error(self.state_reason)
                return
            self._bootstrap_actor()

        from src.monkey_brain.kernel.society.actor_scheduler import NodeClass
        try:
            node_class = NodeClass(self.config.node_class)
        except ValueError:
            logger.warning("Unknown ACTOR_NODE_CLASS %r, defaulting to cloud", self.config.node_class)
            node_class = NodeClass.CLOUD
        pr.register_self_as_node(
            node_class=node_class, capacity=self.config.node_capacity,
            capabilities=self.config.node_capabilities, region=self.config.node_region,
        )

        if node_class == NodeClass.ROBOT:
            # Actor Cell -> ROS Adapter binding (docs/
            # ACTOR_CELL_ARCHITECTURE.md Section I/3): bind a ROS adapter to
            # exactly this Pod's one actor_id. require_real is left at its
            # default (False) so a robot node_class deployment without ROS
            # actually installed still boots, exactly as before this
            # attribute existed. HeartbeatCapability (kernel/domains/
            # robot.py) is the one real, governed capability that reaches
            # this adapter today -- attached to this actor's ActorCell
            # below, once the actor is loaded, so context["ros_adapter"]
            # resolves to it on this actor's own ticks.
            from src.monkey_brain.kernel.edge.ros_integration import build_ros_execution_adapter
            self.ros_adapter = build_ros_execution_adapter(actor_id=self.config.actor_id)

        if self.config.claim_placement:
            # Explicit operator intent: this deployment IS the placement
            # decision for this specific actor_id (Section 19 — the same
            # per-actor-id deployment template model
            # edge-actor-deployment.yaml already established). Does not
            # bypass the Scheduler's own capacity/constraint checks --
            # migrate_actor() still goes through schedule()'s normal
            # accounting when no explicit target is given below; passing
            # THIS node explicitly still reserves capacity for it.
            pr.scheduler.migrate_actor(self.config.actor_id, target_node_id=pr._node_id)

        # Required inbox subscriptions must exist before the Actor is
        # reported READY or its cognition loop is started.
        await pr.connect_nats()
        await pr.wait_for_inbox_subscriptions()

        # Gap Remediation audit fix: this process hosts exactly one actor
        # (self.config.actor_id) -- scope the backstop sweep to it so this
        # pod never fans out reconcile() calls across the whole cluster's
        # actor registry (see start_actor_lifecycle_reconciliation's
        # scope_actor_id docstring).
        pr.start_actor_lifecycle_reconciliation(scope_actor_id=self.config.actor_id)
        await self._reconcile_until_settled()

        if self.ros_adapter is not None:
            # Attach this Pod's ROS adapter to its own actor's ActorCell,
            # now that the actor is loaded -- best-effort, non-fatal, same
            # posture as ActorCell construction itself (kernel/society/
            # runtime.py::register_actor).
            try:
                state = pr._society_runtime.get_actor(self.config.actor_id)
                if state is not None and getattr(state, "cell", None) is not None:
                    state.cell.ros_adapter = self.ros_adapter
            except Exception:
                logger.debug(
                    "ActorRuntime.start: ROS adapter attachment to ActorCell skipped for %s (non-fatal)",
                    self.config.actor_id, exc_info=True,
                )

        if self.state == ReadinessState.READY:
            pr.start_auto_tick(interval_seconds=self.config.tick_interval)

    def _bootstrap_actor(self) -> None:
        from src.monkey_brain.kernel.society.domain import ActorProfile, ActorIdentity, ActorType
        logger.warning(
            "ACTOR_BOOTSTRAP_IF_MISSING=true: registering a NEW actor_id "
            "for %r (dev/test convenience — never use in a real deployment "
            "expecting a pre-existing Actor identity)", self.config.actor_id,
        )
        self.planetary_runtime.register_actor(
            ActorProfile(identity=ActorIdentity(
                actor_id=self.config.actor_id, name=self.config.actor_id, actor_type=ActorType.AI_AGENT,
            )),
        )

    async def _reconcile_until_settled(self, *, max_attempts: int = 3, delay_seconds: float = 1.0) -> None:
        """One reconcile() pass almost always settles immediately (the
        lease is uncontended on a fresh boot); a few retries absorb the
        rare case where another process is mid-transition on this exact
        actor_id right now. Never blocks startup forever -- after
        max_attempts, this process reports its honest current state
        (SCHEDULED_ELSEWHERE/UNSCHEDULABLE/RESTORING) and keeps retrying
        via the background reconciliation loop already started."""
        pr = self.planetary_runtime
        for attempt in range(max_attempts):
            result = pr.lifecycle.reconcile(self.config.actor_id)
            if result.action == "unschedulable":
                self.state, self.state_reason = ReadinessState.UNSCHEDULABLE, result.reason
                return
            if result.action == "scheduled_elsewhere":
                self.state, self.state_reason = ReadinessState.SCHEDULED_ELSEWHERE, result.reason
                return
            if result.action in ("start", "resume", "recover", "none") and result.succeeded:
                observed = pr.observe_actor(self.config.actor_id)
                if observed.resident_here and observed.status in ("active", "initialized"):
                    self.state = ReadinessState.READY
                    self.ready_since = time.time()
                    self.state_reason = ""
                    return
            if result.action == "skipped_lease_held":
                self.state, self.state_reason = ReadinessState.RESTORING, "lease held elsewhere, retrying"
                await asyncio.sleep(delay_seconds)
                continue
            self.state, self.state_reason = ReadinessState.RESTORING, f"last action={result.action!r} succeeded={result.succeeded!r}"
            await asyncio.sleep(delay_seconds)
        logger.warning(
            "Actor %r did not reach READY within %d startup attempts (state=%s, reason=%s) — "
            "the background reconciliation loop will keep retrying",
            self.config.actor_id, max_attempts, self.state, self.state_reason,
        )

    async def shutdown(self) -> None:
        """Graceful shutdown (Section 10): stop accepting new consequential
        work, checkpoint, report the transition, terminate — process
        termination is never treated as Actor deletion. unregister_actor()
        (which would DELETE the Actor's identity) is never called here."""
        pr = self.planetary_runtime
        if pr is None:
            return
        try:
            await pr.stop_auto_tick()
        except Exception as exc:
            logger.warning("stop_auto_tick() failed during shutdown (non-fatal): %s", exc)
        try:
            await pr.stop_actor_lifecycle_reconciliation()
        except Exception as exc:
            logger.warning("stop_actor_lifecycle_reconciliation() failed during shutdown (non-fatal): %s", exc)
        try:
            pr.checkpoint_actor_belief(self.config.actor_id)
        except Exception as exc:
            logger.warning("checkpoint_actor_belief() failed during shutdown: %s", exc)
        try:
            pr.deregister_node(pr._node_id)
        except Exception as exc:
            logger.warning("deregister_node() failed during shutdown (non-fatal): %s", exc)
        self.state = ReadinessState.STARTING
        self.ready_since = None

    def artifact_info(self) -> dict[str, Any]:
        return {
            "actor_id": self.config.actor_id,
            "artifact_version": self.config.artifact_version,
            "runtime_version": self.config.runtime_version,
            "node_id": getattr(self.planetary_runtime, "_node_id", self.config.node_id),
            "node_class": self.config.node_class,
            "started_at": self.started_at,
        }

    def status(self) -> dict[str, Any]:
        pr = self.planetary_runtime
        observed = pr.observe_actor(self.config.actor_id) if pr is not None else None
        live_state, live_reason = self._live_readiness_state(observed)
        # last_reconcile_state: bookkeeping only -- what this process's
        # OWN last active reconcile() attempt (startup, or an explicit
        # retry) concluded. May differ from `state` above, which is
        # recomputed fresh on every call -- see _live_readiness_state's
        # docstring for why both are exposed rather than only the cached
        # one.
        return {
            "state": live_state,
            "reason": live_reason,
            "ready": live_state == ReadinessState.READY,
            "ready_since": self.ready_since,
            "last_reconcile_state": self.state,
            "observed": None if observed is None else {
                "exists": observed.exists, "status": observed.status,
                "node_id": observed.node_id, "resident_here": observed.resident_here,
                "desired_node_id": observed.desired_node_id,
            },
            **self.artifact_info(),
        }

    def _live_readiness_state(self, observed: Any) -> tuple[str, str]:
        """Recomputes readiness from the CURRENT observed snapshot on
        every call, rather than trusting self.state (set once, at this
        process's last active reconcile() attempt — startup, or an
        explicit retry). A stale cached READY would otherwise survive
        this Actor being migrated/suspended/recovered elsewhere by a
        DIFFERENT process's own reconcile loop — confirmed live during
        this session's Docker conformance run: a container correctly
        reached READY at boot, was then migrated away by the host
        process (ACTOR_CLAIM_PLACEMENT contention), and kept reporting
        READY on /status/​/ready indefinitely afterward, because nothing
        ever re-checked. A Kubernetes readinessProbe polling /ready
        repeatedly must not be misled by a snapshot that stopped being
        true after the first check (Section 12: distinguish process
        alive from Actor ready CONTINUOUSLY, not just once at boot).

        Deliberately narrow: only overrides self.state when the fresh
        observation clearly contradicts a cached READY (migrated away,
        no longer resident/active) — it does not attempt to re-run
        _reconcile_until_settled's own richer startup-sequence logic
        (UNSCHEDULABLE detection, bootstrap, etc.), which remains
        self.state's job for the states this method leaves untouched.
        """
        if self.planetary_runtime is None or observed is None or not observed.exists:
            return self.state, self.state_reason
        self_node_id = getattr(self.planetary_runtime, "_node_id", "")
        correctly_placed = not observed.desired_node_id or observed.desired_node_id == self_node_id
        if observed.resident_here and observed.status in ("active", "initialized") and correctly_placed:
            return ReadinessState.READY, ""
        if observed.desired_node_id and observed.desired_node_id != self_node_id:
            return (
                ReadinessState.SCHEDULED_ELSEWHERE,
                f"desired_node_id={observed.desired_node_id!r}, this node={self_node_id!r}",
            )
        if self.state == ReadinessState.READY:
            # Was READY at last check, but the current observation no
            # longer supports that (e.g. suspended/migrated elsewhere
            # for a reason not already covered above) -- never keep
            # reporting a cached READY once it's demonstrably stale.
            return ReadinessState.RESTORING, "no longer resident/active here — awaiting reconciliation"
        return self.state, self.state_reason


# ── ASGI app (uvicorn src.monkey_brain.actor_runtime:app) ────────────────

def _build_app() -> Any:
    from fastapi import FastAPI

    fastapi_app = FastAPI(title="CognitiveOS Actor Runtime", version=ACTOR_RUNTIME_VERSION)
    runtime_holder: dict[str, ActorRuntime] = {}

    @fastapi_app.on_event("startup")
    async def _boot() -> None:
        config = ActorRuntimeConfig.load(os.getenv("ACTOR_CONFIG_FILE"))
        runtime = ActorRuntime(config)
        runtime_holder["runtime"] = runtime
        await runtime.start()

    @fastapi_app.on_event("shutdown")
    async def _stop() -> None:
        runtime = runtime_holder.get("runtime")
        if runtime is not None:
            await runtime.shutdown()

    def _runtime() -> ActorRuntime:
        runtime = runtime_holder.get("runtime")
        if runtime is None:
            raise RuntimeError("Actor Runtime not booted")
        return runtime

    @fastapi_app.get("/live")
    async def live() -> dict:
        # Process-alive only (Section 21) — never gated on Actor readiness.
        return {"status": "alive"}

    @fastapi_app.get("/ready")
    async def ready() -> Any:
        from fastapi import Response
        runtime = _runtime()
        # Conformance-run finding (live Kubernetes readinessProbe test):
        # this MUST gate on body["ready"] (the freshly live-recomputed
        # value status() now returns — see ActorRuntime._live_readiness_
        # state) rather than runtime.state directly. runtime.state is the
        # STALE, cached instance attribute from this process's last
        # active reconcile() attempt; checking it here reintroduced
        # exactly the bug status()'s own fix was meant to close, just one
        # layer up — confirmed live: a Pod whose Actor had been migrated
        # away still returned HTTP 200 from /ready indefinitely,
        # because this check never looked at the recomputed value.
        body = runtime.status()
        if not body["ready"]:
            return Response(content=json.dumps(body), status_code=503, media_type="application/json")
        return body

    @fastapi_app.get("/status")
    async def status() -> dict:
        return _runtime().status()

    @fastapi_app.get("/artifact")
    async def artifact() -> dict:
        return _runtime().artifact_info()

    @fastapi_app.post("/execute")
    async def execute(request: Request) -> Any:
        # Live Deployment Validation finding: the control-plane API's own
        # POST /actors/{id}/execute only ever searched its own process's
        # locally-loaded societies -- correct for the monolithic
        # deployment (deployment.yaml), a real gap for the per-actor-Pod
        # model this module exists for, where a correctly-migrated actor
        # is deliberately NOT resident there anymore. That route now
        # proxies to this endpoint over the network
        # (api/routes/actors.py::_proxy_execute_to_actor_pod) when it
        # finds the actor lives on its own dedicated Pod. This endpoint
        # is the other half of that fix: the actual tick, run against
        # the ONE actor this process ever hosts, using the exact same
        # response-shaping code the control-plane route uses
        # (run_actor_tick) so a caller sees an identical response shape
        # regardless of which path served it.
        from fastapi import HTTPException
        from src.monkey_brain.api.internal_auth import require_internal_service_token
        from src.monkey_brain.api.routes.actors import run_actor_tick
        from src.monkey_brain.kernel.security_boundary import ensure_governed
        from src.monkey_brain.kernel.trusted_auth import (
            bind_trusted_auth, evidence_for_service, evidence_from_spiffe,
            get_trusted_auth, unauthenticated_evidence,
        )
        from src.monkey_brain.kernel.actor_identity import ActorIdentityError

        require_internal_service_token(request)

        runtime = _runtime()
        pr = runtime.planetary_runtime
        if pr is None:
            raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
        actor_id = runtime.config.actor_id
        state = pr._society_runtime.get_actor(actor_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"Actor {actor_id} not resident on this Pod")

        # SPIFFE/SPIRE workload identity layer: this is the real
        # network-crossing agent-to-agent path (a cross-Pod HTTP call
        # proxied from api/routes/actors.py::_proxy_execute_to_actor_pod)
        # -- require_internal_service_token above is a shared-secret
        # header, not per-workload cryptographic identity. Prefer a real
        # verified SPIFFE identity for the responding actor; only when
        # none is available (SPIRE not deployed) does this fall back to
        # the plain service-name evidence already established, and never
        # in production / when explicitly required (Non-negotiable #12:
        # unknown/unauthenticated workload identity must not communicate
        # in production).
        from src.monkey_brain.kernel.workload_identity import get_workload_identity_provider
        from src.monkey_brain.kernel.production_gates import production_mode_enabled
        import os as _os

        identity = await get_workload_identity_provider().get_current_identity()
        if identity is not None:
            bind_trusted_auth(evidence_from_spiffe(identity))
        elif production_mode_enabled() or _os.getenv(
            "COGNITIVEOS_REQUIRE_SPIFFE_AGENT_IDENTITY", "",
        ).strip().lower() in ("true", "1", "yes", "on"):
            bind_trusted_auth(unauthenticated_evidence())
            raise HTTPException(
                status_code=403,
                detail="unauthenticated agent communication refused: no verified workload identity",
            )
        else:
            bind_trusted_auth(evidence_for_service(f"actor-runtime:{actor_id}"))

        # Actor Cell identity (docs/ACTOR_CELL_ARCHITECTURE.md Section J):
        # the SPIFFE/self-asserted evidence above authenticates this
        # PROCESS -- it says nothing about whether this process is actually
        # entitled to answer as `actor_id` specifically. Layer a per-actor
        # DelegationCredential (kernel/actor_identity.py) on top, scoped to
        # exactly this actor_id, issued by this process's own just-bound
        # identity. Fails closed (403) rather than falling back to running
        # ungoverned or under unscoped identity -- a credential minted for
        # a different actor_id can never verify here (kernel/delegation.py's
        # delegate check), which is what makes Actor A unable to
        # authenticate as Actor B.
        issuer = get_trusted_auth().principal_id
        try:
            runtime.cell_identity.ensure(issuer=issuer)
            verified_delegation = runtime.cell_identity.bind_trusted_auth(authenticated_issuer=issuer)
        except ActorIdentityError as exc:
            raise HTTPException(status_code=403, detail=f"Actor Cell identity rejected: {exc}") from exc

        return await ensure_governed(
            "actor.tick",
            actor_id,
            lambda: run_actor_tick(actor_id, pr, state),
            verified_delegation=verified_delegation,
        )

    return fastapi_app


app = _build_app()


# ── CLI entry point (python -m src.monkey_brain.actor_runtime run ...) ──

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cognitiveos-actor")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="Run this process as one Actor's runtime")
    run_parser.add_argument("--actor-id", default=None)
    run_parser.add_argument("--config", default=None, help="Optional YAML/JSON config file")
    run_parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)

    if args.command != "run":
        parser.print_help()
        return 1

    overrides: dict[str, Any] = {}
    if args.actor_id:
        overrides["actor_id"] = args.actor_id
    config = ActorRuntimeConfig.load(args.config, overrides=overrides)
    if args.port:
        config.port = args.port

    runtime = ActorRuntime(config)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stop_event = asyncio.Event()

    def _handle_signal(*_args: Any) -> None:
        loop.call_soon_threadsafe(stop_event.set)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handle_signal)
        except (ValueError, OSError):
            pass  # not the main thread / unsupported platform — best effort

    async def _run() -> None:
        await runtime.start()
        logger.info("Actor Runtime state: %s (%s)", runtime.state, runtime.state_reason)
        await stop_event.wait()
        await runtime.shutdown()

    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()
    return 0 if runtime.state == ReadinessState.READY else 1


if __name__ == "__main__":
    sys.exit(main())
