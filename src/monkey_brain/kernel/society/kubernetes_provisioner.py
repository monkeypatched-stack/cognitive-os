"""Kubernetes Provisioner — closes the Scheduler → Kubernetes gap identified
by the Gap Remediation audit: nothing in this codebase ever called the
Kubernetes API, so a Scheduler placement decision for an Actor with no
running process anywhere had no path to actually cause a Pod to exist —
an operator had to notice and run `kubectl apply` manually.

    Lifecycle Controller
           |
   actor is UNSCHEDULABLE because
   no healthy node exists at all
           |
           v
   KubernetesProvisioner.provision(actor_id, requirements)
           |
   renders deploy/k8s/actor-deployment.yaml (the EXISTING, already-
   canonical per-actor template — this does not invent a second one)
   for this actor_id
           |
   kubectl apply -f -   (idempotent by construction: kubectl apply on an
                          already-existing, unchanged Deployment is a
                          no-op)
           |
           v
   a real Pod eventually boots src.monkey_brain.actor_runtime:app,
   which reconciles itself through the ordinary Lifecycle Controller
   path — this module's ONLY job is closing the "nothing would ever
   create that Pod" gap, not participating in reconciliation itself.

Deliberately NOT a Kubernetes operator (no CRD, no watch loop, no
informer, no new long-running process) and deliberately NOT a new
dependency (no `kubernetes` Python client — shells out to `kubectl`,
the same tool every deployment script in this repo already assumes is
available and configured). This is the smallest change that closes the
actual gap: one more action the ALREADY-EXISTING reconcile() call can
take when it discovers UNSCHEDULABLE specifically means "nothing is
running to claim this Actor at all," not "no node satisfies its
requirements" (those two UNSCHEDULABLE causes are handled differently —
see should_provision()).

Opt-in, off by default (KUBERNETES_PROVISIONING_ENABLED=true) — a local
dev/Docker Compose/CI deployment that has no kubectl/kubeconfig
configured must see ZERO behavior change. When disabled or when kubectl
itself fails for any reason, this degrades to exactly today's behavior:
UNSCHEDULABLE is reported and the actor waits for an operator, never a
crash, never a fabricated placement.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Any

logger = logging.getLogger("agentos.society.kubernetes_provisioner")

_ACTOR_DEPLOYMENT_TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))),
    "deploy", "k8s", "actor-deployment.yaml",
)
"""Resolves to <repo_root>/deploy/k8s/actor-deployment.yaml from this
file's own location (src/monkey_brain/kernel/society/) — reused as-is,
never duplicated. ACTOR_DEPLOYMENT_TEMPLATE_PATH env var overrides this
for a packaged/non-source-tree deployment where the template lives
somewhere else."""


def provisioning_enabled() -> bool:
    return os.getenv("KUBERNETES_PROVISIONING_ENABLED", "false").lower() not in ("false", "0", "no")


_PROVISIONABLE_REASONS = frozenset({
    "no healthy nodes registered",
    # Actors default to edge: this repo's actor-deployment.yaml is a
    # ONE-ACTOR-PER-POD template (ACTOR_NODE_CAPACITY=1, one ACTOR_ID
    # substituted per apply) — every already-provisioned actor Pod
    # therefore ALWAYS reports zero available capacity for placing any
    # OTHER actor, by design, not as a symptom of real exhaustion needing
    # an operator's scaling decision. So "no healthy node has available
    # capacity" (ActorScheduler._summarize_no_candidate_reason's default
    # message, no other requirement set) means exactly the same thing
    # "no healthy nodes registered" does for a totally empty registry:
    # this specific actor has no dedicated Pod of its own yet. A generic
    # capability/node_class mismatch still produces a DIFFERENT, more
    # specific reason string ("no healthy node satisfies: ...") and is
    # deliberately still excluded below — provisioning a generic Pod
    # cannot satisfy a requirement it was never templated to meet.
    "no healthy node has available capacity",
})


def should_provision(unschedulable_reason: str) -> bool:
    """Distinguishes the UNSCHEDULABLE causes provisioning can actually
    fix (nothing exists to host THIS actor yet, whether because zero
    nodes are registered at all or because every registered node is
    either non-actor-hosting or already dedicated to a different actor)
    from every other UNSCHEDULABLE cause (a real node exists but rejects
    THIS actor's specific requirements — missing capability, wrong
    node_class). Provisioning a generic Pod would not satisfy a
    capability/node_class requirement it doesn't meet — see
    ActorScheduler._summarize_no_candidate_reason for the exact reason
    strings this matches against."""
    return unschedulable_reason in _PROVISIONABLE_REASONS


class KubernetesProvisioner:
    """Facilitates Actor Pod provisioning — same composition pattern as
    ActorScheduler/ActorLifecycleController (holds a back-reference to
    the owning PlanetaryRuntime for the actor_id/artifact_version it
    needs; owns no state of its own, no I/O beyond kubectl and reading
    the one template file)."""

    def __init__(self, planetary: Any) -> None:
        self._planetary = planetary

    def provision(self, actor_id: str, *, node_class: str = "cloud",
                  namespace: str = "monkeybrain") -> bool:
        """Renders and applies the canonical per-actor template for
        actor_id. Returns True only on a real, successful `kubectl
        apply`. Never raises — every failure mode (kubectl missing, no
        cluster configured, template unreadable, apply rejected) is
        caught and logged; the caller (Lifecycle Controller) treats a
        False return exactly like provisioning was never attempted —
        UNSCHEDULABLE remains UNSCHEDULABLE, reconciliation retries on
        its normal cadence, nothing is fabricated."""
        if shutil.which("kubectl") is None:
            logger.debug("KubernetesProvisioner: kubectl not on PATH, skipping (actor_id=%s)", actor_id)
            return False
        template_path = os.getenv("ACTOR_DEPLOYMENT_TEMPLATE_PATH", _ACTOR_DEPLOYMENT_TEMPLATE_PATH)
        try:
            with open(template_path, "r") as f:
                template = f.read()
        except OSError as exc:
            logger.warning("KubernetesProvisioner: could not read template %r: %s", template_path, exc)
            return False

        artifact_version = getattr(self._planetary, "_artifact_version", "") or "latest"
        rendered = (
            template
            .replace("${ACTOR_ID}", actor_id)
            .replace("${ACTOR_NODE_CLASS}", node_class)
            .replace("${ACTOR_ARTIFACT_VERSION}", artifact_version)
        )
        try:
            result = subprocess.run(
                ["kubectl", "apply", "-n", namespace, "-f", "-"],
                input=rendered, capture_output=True, text=True, timeout=30,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning("KubernetesProvisioner: kubectl apply failed to run for %s: %s", actor_id, exc)
            return False
        if result.returncode != 0:
            logger.warning(
                "KubernetesProvisioner: kubectl apply rejected for %s (exit %d): %s",
                actor_id, result.returncode, result.stderr.strip(),
            )
            return False
        logger.info("KubernetesProvisioner: provisioned Pod for actor_id=%s (%s)", actor_id, result.stdout.strip())
        return True
