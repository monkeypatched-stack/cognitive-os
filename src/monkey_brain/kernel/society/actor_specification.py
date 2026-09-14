"""ActorSpecification — the first-class, declarative Actor deployment
specification (Final Architectural Convergence, Phase 5).

    apiVersion: cognitiveos/v1
    kind: Actor
    metadata:
      name: buyer-123
    spec:
      artifact: cognitiveos-actor
      version: "1.4"
      placement:
        node_class: edge
        required_capabilities: [camera]
        preferred_region: us-east
        claim_node: edge-node-4
      resources:
        capacity: 1
      configuration:
        goals: [get_best_grocery_deals]
        objective: cost
        tenant_id: default

CognitiveOS-native, not a Kubernetes compatibility shim: `apiVersion`/
`kind`/`metadata`/`spec` is a FAMILIAR shape (declarative, versioned,
name+spec separation) borrowed because it's a good idea, not because
this file imports or depends on anything Kubernetes. `cogctl` (kernel/
society/../cogctl.py) is the only consumer that needs YAML parsing; this
module itself only needs a plain dict in, since that's exactly what both
`yaml.safe_load()` and `json.loads()` (a REST client posting the same
document) already produce — no schema-validation library, no new
dependency.

What this module does NOT do: it never calls register_actor(), never
touches Redis, never talks to a Scheduler. It is pure data + validation
— the same "pure data, all I/O lives in the caller" split
kernel/society/actor_lifecycle.py already established for
ActorDesiredState/ObservedActorState. The real I/O (apply semantics —
create-or-update, matching `kubectl apply`) lives in
api/routes/actors.py::apply_actor_specification, which is the one place
that actually calls PlanetaryRuntime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_SUPPORTED_API_VERSION = "cognitiveos/v1"
_SUPPORTED_KIND = "Actor"


class ActorSpecificationError(ValueError):
    """A spec document is malformed or fails validation — never raised
    for a merely-unusual-but-legal value (e.g. an empty goals list),
    only for something that cannot be applied at all (missing identity,
    wrong kind/apiVersion, an unrecognized node_class)."""


@dataclass(frozen=True)
class ActorSpecification:
    """One Actor's declarative deployment intent. Frozen/immutable, same
    as every other dataclass this session's control-plane work uses
    (ActorRegistryEntry, ExecutionNode, ActorPlacementRequirements) —
    a spec is applied by producing NEW registry/placement state, never
    mutated in place."""

    api_version: str = _SUPPORTED_API_VERSION
    kind: str = _SUPPORTED_KIND
    name: str = ""
    """metadata.name — human-readable, and the default source of
    actor_id when metadata.actor_id is omitted (the common case: most
    callers only ever have one name for an Actor)."""
    actor_id: str = ""
    """metadata.actor_id — explicit override when the caller already
    knows the real registry actor_id (e.g. re-applying a spec for an
    EXISTING actor whose name alone wouldn't resolve it uniquely).
    "" means "derive from name" -- see resolved_actor_id()."""
    artifact: str = "cognitiveos-actor"
    artifact_version: str = ""
    node_class: str = ""
    """spec.placement.node_class -- a HARD requirement when set (applied
    as ActorPlacementRequirements.required_node_class): this Actor MUST
    run on a node of this class. Defaults to "" (unconstrained, not
    "cloud") deliberately -- a spec that never mentions placement at all
    must not silently impose a hard cloud-only constraint; that would
    make the Scheduler report UNSCHEDULABLE the moment only edge/device
    nodes are registered, for an Actor whose author never expressed any
    placement opinion. Use preferred_node_class for a soft-only hint."""
    required_capabilities: tuple[str, ...] = ()
    preferred_node_class: str = ""
    preferred_region: str = ""
    claim_node: str = ""
    """spec.placement.claim_node — an explicit execution_node_id this
    Actor should be placed on, bypassing the Scheduler's own ranking
    (same semantics as actor_runtime.py's ACTOR_CLAIM_PLACEMENT, exposed
    here as a declarative field instead of a runtime env var). "" means
    "let the Scheduler decide" (docs/ACTOR_SCHEDULER.md's normal
    filter-rank-select path)."""
    capacity_hint: int = 1
    """spec.resources.capacity -- informational sizing hint recorded on
    apply (Section 14: "Actor may require radically different
    resources"); the Scheduler's own capacity accounting lives on
    ExecutionNode/ActorPlacementRequirements.min_available_capacity,
    unchanged by this field -- this is metadata for an operator/cogctl
    describe to see what was requested, not a second enforcement path."""
    goals: tuple[str, ...] = ()
    objective: str = ""
    tenant_id: str = "default"
    """`apply` (api/routes/actors.py::apply_actor_specification) is
    unconditionally create-or-update, the same as `kubectl apply` —
    there is no separate opt-in flag for "create if missing" here,
    because that IS what apply means. This is deliberately distinct
    from actor_runtime.py's OWN, unrelated ACTOR_BOOTSTRAP_IF_MISSING
    env var: that one guards a RUNTIME PROCESS booting with nothing but
    an actor_id (where silently minting a new identity would be
    dangerous — a misconfigured/rogue process could self-register), a
    completely different trust boundary from an authenticated `cogctl
    apply` call through the Control API."""

    def resolved_actor_id(self) -> str:
        return self.actor_id or self.name

    @staticmethod
    def from_dict(doc: dict[str, Any]) -> "ActorSpecification":
        if not isinstance(doc, dict):
            raise ActorSpecificationError("spec document must be a mapping (YAML/JSON object)")
        api_version = doc.get("apiVersion", _SUPPORTED_API_VERSION)
        kind = doc.get("kind", _SUPPORTED_KIND)
        if kind != _SUPPORTED_KIND:
            raise ActorSpecificationError(f"unsupported kind {kind!r} — only {_SUPPORTED_KIND!r} is defined")
        if api_version != _SUPPORTED_API_VERSION:
            raise ActorSpecificationError(
                f"unsupported apiVersion {api_version!r} — only {_SUPPORTED_API_VERSION!r} is defined"
            )
        metadata = doc.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ActorSpecificationError("metadata must be a mapping")
        name = str(metadata.get("name", "") or "")
        actor_id = str(metadata.get("actor_id", "") or "")
        if not name and not actor_id:
            raise ActorSpecificationError("metadata.name or metadata.actor_id is required")

        spec = doc.get("spec") or {}
        if not isinstance(spec, dict):
            raise ActorSpecificationError("spec must be a mapping")
        placement = spec.get("placement") or {}
        resources = spec.get("resources") or {}
        configuration = spec.get("configuration") or {}

        node_class = str(placement.get("node_class", "") or "").lower()
        preferred_node_class = str(placement.get("preferred_node_class", "") or "").lower()
        required_capabilities = tuple(placement.get("required_capabilities", []) or ())

        result = ActorSpecification(
            api_version=api_version,
            kind=kind,
            name=name,
            actor_id=actor_id,
            artifact=str(spec.get("artifact", "cognitiveos-actor") or "cognitiveos-actor"),
            artifact_version=str(spec.get("version", "") or ""),
            node_class=node_class,
            required_capabilities=required_capabilities,
            preferred_node_class=preferred_node_class,
            preferred_region=str(placement.get("preferred_region", "") or ""),
            claim_node=str(placement.get("claim_node", "") or ""),
            capacity_hint=int(resources.get("capacity", 1) or 1),
            goals=tuple(configuration.get("goals", []) or ()),
            objective=str(configuration.get("objective", "") or ""),
            tenant_id=str(configuration.get("tenant_id", "default") or "default"),
        )
        result.validate()
        return result

    def validate(self) -> None:
        """Raises ActorSpecificationError on anything that cannot be
        applied at all. Deliberately does NOT import NodeClass to check
        node_class against it here -- that would make this pure-data
        module depend on actor_scheduler.py's I/O-adjacent module for a
        check the apply route already has to do anyway (constructing
        the real NodeClass enum value to pass to the Scheduler); a typo
        like node_class: "cluod" surfaces there, at apply time, with the
        exact same "unrecognized node_class" class of error, not
        silently here."""
        if not self.resolved_actor_id():
            raise ActorSpecificationError("metadata.name or metadata.actor_id is required")
        if self.capacity_hint < 1:
            raise ActorSpecificationError("spec.resources.capacity must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "apiVersion": self.api_version,
            "kind": self.kind,
            "metadata": {"name": self.name, "actor_id": self.actor_id},
            "spec": {
                "artifact": self.artifact,
                "version": self.artifact_version,
                "placement": {
                    "node_class": self.node_class,
                    "required_capabilities": list(self.required_capabilities),
                    "preferred_node_class": self.preferred_node_class,
                    "preferred_region": self.preferred_region,
                    "claim_node": self.claim_node,
                },
                "resources": {"capacity": self.capacity_hint},
                "configuration": {
                    "goals": list(self.goals),
                    "objective": self.objective,
                    "tenant_id": self.tenant_id,
                },
            },
        }
