"""Pipeline contracts — immutable data types that flow through the Cognitive Runtime.

These contracts define the boundaries between:
    Transport  →  Compilation  →  Runtime  →  Response

They describe STATE — they do not perform work.
No runtime logic. No side effects. No initialization.

Dependency direction:
    contracts  ◀──  runtime
    contracts  ◀──  compiler
    contracts  ◀──  response_builder

The contracts layer must never import runtime implementations.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from src.monkey_brain.kernel.pipeline.protocols import (
    WorldView,
    ActorHandle,
    CapabilityBus,
    KnowledgeStore,
    ContextStreamProtocol,
    ExecutionEngine,
    LearningEngine,
    MetricsCollector,
    BeliefRuntimeProtocol,
    TrustNetworkProtocol,
    AuditServiceProtocol,
    EventBusProtocol,
)

# ── Status Enum ──────────────────────────────────────────────────────────


class PipelineStatus:
    """Pipeline execution status constants."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    PENDING = "pending"


# ── 1. PipelineRequest ──────────────────────────────────────────────────


@dataclass(frozen=True)
class PipelineRequest:
    """Transport-level incoming request.

    Contains ONLY transport information — who is asking, what they asked,
    and metadata about the request itself.

    No intent. No goal. No world. No plans. No runtime state.

    This is the entry point of the pipeline. Every subsequent contract
    is derived from or alongside this one.
    """

    question: str
    """The user's natural language question or request."""

    actor_id: str = "unknown"
    """Identifier of the actor (user/agent) making the request."""

    tenant_id: str = "default"
    """Tenant isolation boundary. Defaults to 'default' for single-tenant."""

    session_id: str = ""
    """Optional session ID for multi-turn conversations."""

    request_id: str = field(default_factory=lambda: uuid4().hex)
    """Unique identifier for this specific request."""

    attachments: tuple[PipelineAttachment, ...] = ()
    """Optional file attachments or structured data accompanying the question."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Arbitrary transport-level metadata (headers, trace context, etc.)."""

    timestamp: float = field(default_factory=time.time)
    """Unix timestamp when the request was received."""

    def __post_init__(self) -> None:
        if not self.question or not self.question.strip():
            raise ValueError("PipelineRequest.question must be a non-empty string")


@dataclass(frozen=True)
class PipelineAttachment:
    """An attachment to a PipelineRequest.

    Represents a file, image, or structured data blob that accompanies
    the question. The pipeline may use these for grounding or context.
    """

    name: str
    """Attachment filename or identifier."""

    content_type: str = "application/octet-stream"
    """MIME type of the attachment."""

    data: bytes = b""
    """Raw attachment bytes."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Attachment-specific metadata (size, checksum, etc.)."""


# ── 2. CompiledRequest ──────────────────────────────────────────────────


@dataclass(frozen=True)
class CompiledRequest:
    """Fully compiled request — the boundary between compilation and execution.

    Produced by the compiler from a PipelineRequest.
    Consumed by the runtime for execution.

    Contains the full compilation output: intent, goal, signed IR,
    execution context, and the original request for traceability.

    Nothing in this object should be mutable during execution.
    """

    request: PipelineRequest
    """The original transport request (for traceability)."""

    intent: Any
    """Classified intent (from intent classification step).
    Type: Intent classifier output — dict with 'intent', 'confidence', 'workload_id'."""

    goal: Any
    """Resolved goal (from goal resolution step).
    Type: Goal object with 'name', 'description', 'constraints', 'confidence'."""

    intent_ir: Any
    """Signed, versioned IntentIR (the 'executable' representation).
    Type: IntentIR from plan.goals.intent_ir — frozen dataclass."""

    goal_ir: Any
    """Goal IR with domain, entities, constraints, relationships.
    Type: GoalIR from layers — entity/constraint enriched goal."""

    execution_context: Any
    """Immutable ExecutionContext scoping one execution attempt.
    Type: ExecutionContext from execute.context — frozen dataclass."""

    execution_graph: Any = None
    """Reference to the execution graph (if compiled).
    Type: ExecutionGraph or None — the planner's output."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Compilation metadata (classification time, confidence scores, etc.)."""


# ── 3. RuntimeContext ───────────────────────────────────────────────────


@dataclass(frozen=True)
class RuntimeContext:
    """References to runtime services — handles, not ownership.

    Contains typed references to long-lived runtime components.
    The pipeline injects this into the runtime; the runtime uses it
    to access services without owning them.

    These are PROTOCOL types, not concrete implementations.
    The contracts layer must never import runtime classes.
    """

    world: WorldView | None = None
    """Reference to the global world tensor / WorldView."""

    actor: ActorHandle | None = None
    """Reference to the actor registry / ActorRuntime."""

    capabilities: CapabilityBus | None = None
    """Reference to the capability bus / agent registry."""

    knowledge: KnowledgeStore | None = None
    """Reference to the knowledge store / semantic memory."""

    context_stream: ContextStreamProtocol | None = None
    """Reference to the Context Stream — the sole mutation path for the world."""

    execution: ExecutionEngine | None = None
    """Reference to the execution coordinator / GoalExecutor."""

    learning: LearningEngine | None = None
    """Reference to the learning runtime / Bellman learner."""

    metrics: MetricsCollector | None = None
    """Reference to the metrics/telemetry service."""

    belief: BeliefRuntimeProtocol | None = None
    """Reference to the belief runtime."""

    trust: TrustNetworkProtocol | None = None
    """Reference to the trust network."""

    audit: AuditServiceProtocol | None = None
    """Reference to the audit service."""

    event_bus: EventBusProtocol | None = None
    """Reference to the event bus."""


# ── 4. PipelineResponse ────────────────────────────────────────────────


@dataclass(frozen=True)
class PipelineResponse:
    """Response returned to the caller.

    Contains only externally visible information.
    No internal state. No runtime references. No mutable artifacts.

    This is the output boundary of the pipeline. The caller receives
    this and nothing else.
    """

    answer: str
    """The textual answer to the user's question."""

    status: str = PipelineStatus.SUCCESS
    """Execution status: success, partial, failed, or pending."""

    execution_id: str = field(default_factory=lambda: uuid4().hex)
    """Unique identifier for this execution (for tracing and replay)."""

    artifacts: tuple[PipelineArtifact, ...] = ()
    """Structured artifacts produced during execution (graphs, plans, etc.)."""

    trace: tuple[PipelineTraceEntry, ...] = ()
    """Execution trace — ordered list of pipeline steps executed."""

    metrics: dict[str, Any] = field(default_factory=dict)
    """Quantitative metrics (latency_ms, tokens_used, nodes_executed, etc.)."""

    errors: tuple[PipelineError, ...] = ()
    """Errors encountered during execution (empty on success)."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Arbitrary response metadata (session_id, classification, etc.)."""

    def __post_init__(self) -> None:
        if self.status not in (
            PipelineStatus.SUCCESS,
            PipelineStatus.PARTIAL,
            PipelineStatus.FAILED,
            PipelineStatus.PENDING,
        ):
            raise ValueError(
                f"PipelineResponse.status must be one of success/partial/failed/pending, got {self.status!r}"
            )


@dataclass(frozen=True)
class PipelineArtifact:
    """A structured artifact produced during pipeline execution.

    Artifacts are typed outputs that callers can inspect without
    parsing the answer string.
    """

    name: str
    """Artifact identifier (e.g. 'execution_graph', 'plan', 'simulation')."""

    artifact_type: str
    """MIME-like type (e.g. 'application/json', 'graph/execution')."""

    data: Any = None
    """Artifact payload — typically a dict or serialized structure."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Artifact-specific metadata (version, checksum, etc.)."""


@dataclass(frozen=True)
class PipelineTraceEntry:
    """A single step in the execution trace.

    Records what happened at each pipeline stage for debugging,
    auditing, and replay.
    """

    step: str
    """Step name (e.g. 'intent_classification', 'planning', 'execution')."""

    status: str = "ok"
    """Step status: ok, skipped, failed."""

    duration_ms: float = 0.0
    """Wall-clock time for this step in milliseconds."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Step-specific metadata (node count, confidence, etc.)."""


@dataclass(frozen=True)
class PipelineError:
    """An error encountered during pipeline execution.

    Captures the error with enough context for debugging without
    leaking internal state to the caller.
    """

    code: str
    """Machine-readable error code (e.g. 'intent_classification_failed')."""

    message: str
    """Human-readable error description."""

    step: str = ""
    """Pipeline step where the error occurred."""

    details: dict[str, Any] = field(default_factory=dict)
    """Additional error context (not for external display)."""
