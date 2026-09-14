"""Execution-graph compiler — semantic source of truth → sparse transition operator.

See docs/adr/0021-execution-graph-compiler.md. The compiler is a pure, deterministic
pass OUTSIDE the learning loop: it consumes the learned semantic graph (topology +
coordination strengths) and emits a row-stochastic sparse operator M = D⁻¹W that
execution propagates through. Nothing here writes back into learning.

Four-Layer Cognitive Architecture:
  Layer 1: World Inference — SparseTransitionTensor, WorldLearner
  Layer 2: Belief Formation — Actor.belief, TenantView
  Layer 3: Decision — PolicyStore, ActionOperator, ActionLegality
  Layer 4: Learning — WorldLearner, PolicyLearner, ComparatorRuntime
"""

from __future__ import annotations

from src.monkey_brain.kernel.compile.types import (
    AgentKey,
    CompiledOperator,
    DanglingPolicy,
    DomainOperatorSet,
    NodeId,
    SemanticGraphSnapshot,
    VerificationReport,
)
from src.monkey_brain.kernel.compile.compiler import GraphCompiler
from src.monkey_brain.kernel.compile.tensor import Feature, SparseTransitionTensor

# NOTE: registry.py (DomainOperatorRegistry) is SUPERSEDED by SparseTransitionTensor —
# the tensor is the collection of domain slices, done with global state indexing. The
# module is retained but no longer part of the public API. Import it directly if needed.
from src.monkey_brain.kernel.compile.actor import ActorModel, EffectMatrix
from src.monkey_brain.kernel.compile.cognitive_actor import (
    CognitiveActor,
    CycleResult,
    Delta,
)
from src.monkey_brain.kernel.compile.society import Actor, ActorNetwork
from src.monkey_brain.kernel.compile.sparse import SparseMatrix, epistemic_loss
from src.monkey_brain.kernel.compile.context import (
    Constraints,
    Context,
    ContextChange,
    Priority,
)
from src.monkey_brain.kernel.compile.tenancy import TenantWorld, TenantView, SHARED
from src.monkey_brain.kernel.compile.world_model_runtime import WorldModelRuntime
from src.monkey_brain.kernel.compile.actor_runtime import (
    ActorRuntime,
    AuthorizationView,
    AuthorizedAgentView,
    AuthorizedCapabilityView,
)
from src.monkey_brain.kernel.compile.lifecycle import (
    merge_with_conflicts,
    sign_checkpoint,
    verify_checkpoint,
)
from src.monkey_brain.kernel.compile.trust import (
    Perm,
    Relationship,
    TrustEdge,
    TrustNetwork,
)
from src.monkey_brain.kernel.compile.exchange import (
    BeliefProposal,
    ExchangeResult,
    KnowledgeExchange,
    MergeQueue,
    is_shareable,
)

# Layer 3: Decision — Action operators and legality
from src.monkey_brain.kernel.compile.action_operator import (
    ActionOperator,
    ActionLegality,
)

# Phase 3: Unified Scheduler Interface
from src.monkey_brain.kernel.compile.scheduler_interface import (
    SchedulerInterface,
    ScheduleRequest,
    ScheduleResult,
)
from src.monkey_brain.kernel.compile.scheduler_adapters import (
    DistributedSchedulerAdapter,
    ReasoningSchedulerAdapter,
    GraphSchedulerAdapter,
    ProcessSchedulerAdapter,
)
from src.monkey_brain.kernel.compile.scheduler_registry import (
    SchedulerRegistry,
    get_scheduler_registry,
)
from src.monkey_brain.kernel.compile.society_runtime import CompileSocietyRuntime

# Dependency Inversion: Runtime Abstractions
from src.monkey_brain.kernel.compile.runtime_interface import (
    RuntimeInterface,
    CognitiveRuntimeInterface,
    SocietyRuntimeInterface,
    RequestContext,
    RuntimeCoordinator,
)

# SOLID Foundation Interfaces
from src.monkey_brain.kernel.compile.solid_interfaces import (
    BootInterface,
    HealthMonitorInterface,
    PersistenceInterface,
    RepositoryInterface,
    ExecutorInterface,
    CompilerInterface,
    EventPublisherInterface,
    EventSubscriberInterface,
    ValidatorInterface,
    EntityInterface,
    AggregateInterface,
    ContextInterface,
    ComponentInterface,
    ServiceInterface,
    SpecificationInterface,
    FactoryInterface,
    TransactionalInterface,
    CacheableInterface,
    ObservableInterface,
    DependencyProviderInterface,
    InjectorInterface,
    RepositoryComponentInterface,
    ServiceComponentInterface,
    RuntimeComponentInterface,
)

# Phase 5: Production Hardening
from src.monkey_brain.kernel.compile.error_recovery import (
    CircuitBreaker,
    CircuitBreakerConfig,
    RetryConfig,
    RetryableFunction,
    ErrorRecoveryRegistry,
    get_error_recovery_registry,
)
from src.monkey_brain.kernel.compile.metrics import (
    MetricsCollector,
    LatencyStats,
    HealthChecker,
    get_health_checker,
)
from src.monkey_brain.kernel.compile.degradation import (
    DegradationManager,
    CapabilityState,
    CapabilityInfo,
    get_degradation_manager,
)
from src.monkey_brain.kernel.compile.deployment_readiness import (
    DeploymentReadiness,
    CheckState,
    ReadinessCheck,
    get_deployment_readiness,
)

__all__ = [
    # Layer 1: World Inference
    "SparseTransitionTensor",
    "Feature",
    "GraphCompiler",
    "WorldModelRuntime",
    # Layer 2: Belief Formation
    "CognitiveActor",
    "Actor",
    "ActorRuntime",
    "AuthorizationView",
    "AuthorizedAgentView",
    "AuthorizedCapabilityView",
    "ActorNetwork",
    "TenantWorld",
    "TenantView",
    "SHARED",
    # Layer 3: Decision
    "ActorModel",
    "EffectMatrix",
    "ActionOperator",
    "ActionLegality",
    # Layer 4: Learning (losses computed by ComparatorRuntime)
    "CycleResult",
    "Delta",
    "epistemic_loss",
    # Types
    "AgentKey",
    "NodeId",
    "SemanticGraphSnapshot",
    "CompiledOperator",
    "DomainOperatorSet",
    "VerificationReport",
    "DanglingPolicy",
    "SparseMatrix",
    # Context
    "Context",
    "Constraints",
    "ContextChange",
    "Priority",
    # Lifecycle
    "merge_with_conflicts",
    "sign_checkpoint",
    "verify_checkpoint",
    # Trust
    "TrustNetwork",
    "TrustEdge",
    "Relationship",
    "Perm",
    # Exchange
    "KnowledgeExchange",
    "BeliefProposal",
    "ExchangeResult",
    "MergeQueue",
    "is_shareable",
    # Dependency Inversion: Runtime Abstractions
    "RuntimeInterface",
    "CognitiveRuntimeInterface",
    "SocietyRuntimeInterface",
    "RequestContext",
    "RuntimeCoordinator",
    # SOLID Foundation Interfaces
    "BootInterface",
    "HealthMonitorInterface",
    "PersistenceInterface",
    "RepositoryInterface",
    "ExecutorInterface",
    "CompilerInterface",
    "EventPublisherInterface",
    "EventSubscriberInterface",
    "ValidatorInterface",
    "EntityInterface",
    "AggregateInterface",
    "ContextInterface",
    "ComponentInterface",
    "ServiceInterface",
    "SpecificationInterface",
    "FactoryInterface",
    "TransactionalInterface",
    "CacheableInterface",
    "ObservableInterface",
    "DependencyProviderInterface",
    "InjectorInterface",
    "RepositoryComponentInterface",
    "ServiceComponentInterface",
    "RuntimeComponentInterface",
    # Phase 3: Unified Scheduler Interface
    "SchedulerInterface",
    "ScheduleRequest",
    "ScheduleResult",
    "DistributedSchedulerAdapter",
    "ReasoningSchedulerAdapter",
    "GraphSchedulerAdapter",
    "ProcessSchedulerAdapter",
    "SchedulerRegistry",
    "get_scheduler_registry",
    # Society Runtime (legacy, actor-id-keyed — see society_runtime.py docstring)
    "CompileSocietyRuntime",
    # Phase 5: Production Hardening
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "RetryConfig",
    "RetryableFunction",
    "ErrorRecoveryRegistry",
    "get_error_recovery_registry",
    "MetricsCollector",
    "LatencyStats",
    "HealthChecker",
    "get_health_checker",
    "DegradationManager",
    "CapabilityState",
    "CapabilityInfo",
    "get_degradation_manager",
    "DeploymentReadiness",
    "CheckState",
    "ReadinessCheck",
    "get_deployment_readiness",
]
