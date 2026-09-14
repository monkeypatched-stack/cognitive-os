"""Edge Provisioner — the edge-device counterpart to KubernetesProvisioner
(kernel/society/kubernetes_provisioner.py), closing the Scheduler -> Edge
Device gap the same way that module closed the Scheduler -> Kubernetes
gap: a Scheduler placement decision pointing at an EDGE-class node had no
path to actually cause an Actor process to exist on that device.

    Lifecycle Controller
           |
   actor's Scheduler decision names a registered node whose
   node_class is EDGE/DEVICE/ROBOT, but nothing is resident there yet
           |
           v
   EdgeProvisioner.provision(actor_id, device_id, node_class)
           |
   POST http://{device_id}:{EDGE_AGENT_PORT}/actors/{actor_id}/start
   to that device's own Edge Agent (src/monkey_brain/edge_agent.py) --
   push-based, mirroring `kubectl apply`'s own push model exactly
           |
           v
   the Edge Agent spawns actor_runtime.py as a local subprocess --
   THE SAME ASGI export/env-var contract Kubernetes' own actor-
   deployment.yaml uses, so it self-registers, self-claims, and
   reconciles through the ordinary Lifecycle Controller path exactly
   like a Kubernetes Pod. This module's ONLY job is closing the
   "nothing would ever start that process" gap, not participating in
   reconciliation itself -- identical division of responsibility to
   KubernetesProvisioner's own docstring.

Deliberately NOT pretending an edge device is Kubernetes: no container
image is built or pulled, no Pod spec is rendered -- this shells out to
one small, purpose-built HTTP API instead, matching "the edge deployment
mechanism should be appropriate for a normal Linux edge device."

Deliberately push-based, not pull/polling: simpler, directly testable,
and mirrors KubernetesProvisioner's own push model. Documented limitation
(see EDGE_DEPLOYMENT_REPORT.md): a device behind NAT/a firewall the
control plane cannot reach inbound would need a pull-based variant (the
Edge Agent polling the Registry for its own desired state instead of
waiting to be called) -- not implemented this pass, since every actor_
runtime.py subprocess this Agent spawns already reaches OUT to Redis/
Mongo/NATS on its own (the same outbound-only connectivity model
Kubernetes Pods already rely on), so only this narrow "please start now"
signal is push-based, not the Actor's own ordinary operation.

Opt-in, off by default (EDGE_PROVISIONING_ENABLED=true) -- a deployment
with no edge devices configured must see ZERO behavior change. When
disabled, or when the target device's Edge Agent is unreachable for any
reason, this degrades to exactly today's behavior: the actor stays
unscheduled/unresident, reconciliation retries on its normal cadence,
nothing is fabricated.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("agentos.society.edge_provisioner")

DEFAULT_EDGE_AGENT_PORT = 8061
"""Matches edge_agent.py's own DEFAULT_EDGE_AGENT_PORT constant -- kept as
a literal here (not a cross-module import) so this control-plane-side
module never needs edge_agent.py's own FastAPI/subprocess dependencies
importable at all, the same "no shared runtime import" boundary
KubernetesProvisioner keeps from actor_runtime.py."""


def provisioning_enabled() -> bool:
    return os.getenv("EDGE_PROVISIONING_ENABLED", "false").lower() not in (
        "false",
        "0",
        "no",
    )


class EdgeProvisioner:
    """Facilitates Actor process provisioning on edge devices -- same
    composition pattern as KubernetesProvisioner/ActorScheduler/
    ActorLifecycleController (holds a back-reference to the owning
    PlanetaryRuntime; owns no state of its own beyond a reusable HTTP
    client)."""

    def __init__(self, planetary: Any) -> None:
        self._planetary = planetary

    def _agent_url(self, device_id: str, path: str) -> str:
        port = os.getenv("EDGE_AGENT_PORT", str(DEFAULT_EDGE_AGENT_PORT))
        return f"http://{device_id}:{port}{path}"

    def provision(
        self,
        actor_id: str,
        *,
        device_id: str,
        node_class: str = "edge",
        timeout: float = 15.0,
    ) -> bool:
        """Asks device_id's own Edge Agent to start actor_id. Returns
        True only on a real, successful response. Never raises -- every
        failure mode (Agent unreachable, device offline, HTTP error) is
        caught and logged; the caller (Lifecycle Controller) treats a
        False return exactly like provisioning was never attempted."""
        import httpx

        artifact_version = getattr(self._planetary, "_artifact_version", "") or ""
        try:
            resp = httpx.post(
                self._agent_url(device_id, f"/actors/{actor_id}/start"),
                json={
                    "node_class": node_class,
                    "artifact_version": artifact_version,
                    "claim_placement": True,
                },
                timeout=timeout,
            )
        except httpx.RequestError as exc:
            logger.warning(
                "EdgeProvisioner: could not reach Edge Agent on %r for actor_id=%s: %s",
                device_id,
                actor_id,
                exc,
            )
            return False
        if resp.status_code >= 400:
            logger.warning(
                "EdgeProvisioner: Edge Agent on %r rejected start for actor_id=%s (%d): %s",
                device_id,
                actor_id,
                resp.status_code,
                resp.text,
            )
            return False
        logger.info(
            "EdgeProvisioner: provisioned actor_id=%s on device_id=%s",
            actor_id,
            device_id,
        )
        return True

    def stop(self, actor_id: str, *, device_id: str, timeout: float = 20.0) -> bool:
        import httpx

        try:
            resp = httpx.post(self._agent_url(device_id, f"/actors/{actor_id}/stop"), timeout=timeout)
        except httpx.RequestError as exc:
            logger.warning(
                "EdgeProvisioner: could not reach Edge Agent on %r to stop actor_id=%s: %s",
                device_id,
                actor_id,
                exc,
            )
            return False
        return resp.status_code < 400

    def status(self, actor_id: str, *, device_id: str, timeout: float = 10.0) -> dict[str, Any] | None:
        import httpx

        try:
            resp = httpx.get(
                self._agent_url(device_id, f"/actors/{actor_id}/status"),
                timeout=timeout,
            )
        except httpx.RequestError as exc:
            logger.debug(
                "EdgeProvisioner: status check failed for actor_id=%s on device_id=%s: %s",
                actor_id,
                device_id,
                exc,
            )
            return None
        if resp.status_code >= 400:
            return None
        return resp.json()
