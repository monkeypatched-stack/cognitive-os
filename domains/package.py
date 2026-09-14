"""Domain Package — DDD as Executable Cognitive Abstractions.

Every DDD construct becomes an active cognitive agent with
Perceive → Reason → Act → Learn capabilities.

DDD Hierarchy as Recursive Cognition:

    Domain Agent
        ↓
    Bounded Context Agent
        ↓
    Aggregate Agent
        ↓
    Entity Agent
        ↓
    Value Object Agent
        ↓
    Policy Agent
        ↓
    Repository Agent
        ↓
    Factory Agent
        ↓
    Event Agent

Each layer reasons at a different abstraction level:
- Domain Agent      → reasons about business strategy
- Context Agent     → reasons about bounded problems
- Aggregate Agent   → reasons about consistency
- Entity Agent      → reasons about lifecycle
- Value Object Agent → reasons about transformation
- Policy Agent      → reasons about governance
- Repository Agent  → reasons about persistence
- Factory Agent     → reasons about creation
- Event Agent       → reasons about temporal causality

Every DDD concept is an executable cognitive abstraction.
Every level has: Perceive, Reason, Act, Learn.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger("domains")


# ---------------------------------------------------------------------------
# Cognitive Core — every DDD construct implements this
# ---------------------------------------------------------------------------


class CognitiveAgent(ABC):
    """Base class for all DDD cognitive agents.

    Every DDD construct is an executable cognitive abstraction
    with Perceive → Reason → Act → Learn capabilities.
    """

    @abstractmethod
    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        """Perceive the environment and extract relevant signals."""
        ...

    @abstractmethod
    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        """Reason about the perception and produce a decision."""
        ...

    @abstractmethod
    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        """Act on the decision and produce an outcome."""
        ...

    @abstractmethod
    def learn(self, outcome: dict[str, Any]) -> None:
        """Learn from the outcome to improve future reasoning."""
        ...

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """Full cognitive cycle: Perceive → Reason → Act → Learn."""
        perception = self.perceive(context)
        decision = self.reason(perception)
        outcome = self.act(decision)
        self.learn(outcome)
        return outcome


# ---------------------------------------------------------------------------
# DDD Constructs as Cognitive Agents
# ---------------------------------------------------------------------------


class DomainAgent(CognitiveAgent):
    """Domain Agent — reasons about business strategy.

    Responsibilities:
    - Enforce domain invariants
    - Coordinate bounded contexts
    - Produce domain events
    - Maintain ubiquitous language
    """

    def __init__(self, name: str):
        self.name = name
        self.contexts: list[BoundedContextAgent] = []
        self.policies: list[PolicyAgent] = []
        self._memory: list[dict] = []

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"domain": self.name, "signals": context.get("signals", [])}

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {"strategy": "domain_coordination", "domain": self.name}

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "domain": self.name,
            "action": "domain_coordination",
            "result": "executed",
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        self._memory.append(outcome)


class BoundedContextAgent(CognitiveAgent):
    """Bounded Context Agent — reasons about bounded problems.

    Responsibilities:
    - Enforce context boundaries
    - Coordinate aggregates
    - Produce context events
    - Maintain ubiquitous language within context
    """

    def __init__(self, name: str):
        self.name = name
        self.aggregates: list[AggregateAgent] = []
        self.services: list[DomainServiceAgent] = []
        self._memory: list[dict] = []

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"context": self.name, "signals": context.get("signals", [])}

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {"strategy": "context_coordination", "context": self.name}

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "context": self.name,
            "action": "context_coordination",
            "result": "executed",
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        self._memory.append(outcome)


class AggregateAgent(CognitiveAgent):
    """Aggregate Agent — reasons about consistency.

    Responsibilities:
    - Enforce invariants
    - Aggregate entity state
    - Coordinate child entities
    - Produce domain events
    - Maintain transactional consistency
    """

    def __init__(self, name: str):
        self.name = name
        self.entities: list[EntityAgent] = []
        self._events: list[dict] = []
        self._invariants: list[callable] = []

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"aggregate": self.name, "entity_count": len(self.entities)}

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        violations = [inv() for inv in self._invariants if not inv()]
        return {
            "aggregate": self.name,
            "violations": violations,
            "consistent": len(violations) == 0,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        if decision.get("consistent"):
            return {
                "aggregate": self.name,
                "action": "state_committed",
                "result": "consistent",
            }
        return {
            "aggregate": self.name,
            "action": "invariant_violated",
            "result": "rejected",
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        self._events.append(outcome)

    def add_invariant(self, invariant: callable) -> None:
        self._invariants.append(invariant)


class EntityAgent(CognitiveAgent):
    """Entity Agent — reasons about lifecycle.

    Responsibilities:
    - Manage entity lifecycle
    - Execute entity behavior
    - Maintain identity
    - Validate state transitions
    """

    def __init__(self, entity_id: str, entity_type: str):
        self.id = entity_id
        self.entity_type = entity_type
        self.state: str = "created"
        self._transitions: dict[str, list[str]] = {}
        self._memory: list[dict] = []

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"entity_id": self.id, "type": self.entity_type, "state": self.state}

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        allowed = self._transitions.get(self.state, [])
        return {
            "entity_id": self.id,
            "current_state": self.state,
            "allowed_transitions": allowed,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        target = decision.get("target_state")
        if target and target in self._transitions.get(self.state, []):
            self.state = target
            return {
                "entity_id": self.id,
                "action": "transitioned",
                "from": decision["current_state"],
                "to": target,
            }
        return {
            "entity_id": self.id,
            "action": "transition_rejected",
            "reason": "invalid_transition",
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        self._memory.append(outcome)

    def add_transition(self, from_state: str, to_state: str) -> None:
        self._transitions.setdefault(from_state, []).append(to_state)


class ValueObjectAgent(CognitiveAgent):
    """Value Object Agent — reasons about transformation.

    Responsibilities:
    - Transform values
    - Normalize input
    - Validate constraints
    - Compare values
    - Compose complex values
    """

    def __init__(self, name: str, validators: list[callable] | None = None):
        self.name = name
        self.validators = validators or []
        self._transformations: list[callable] = []

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"value_object": self.name, "raw_value": context.get("value")}

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        raw = perception.get("raw_value")
        errors = [v(raw) for v in self.validators if not v(raw)]
        return {
            "value_object": self.name,
            "raw_value": raw,
            "valid": len(errors) == 0,
            "errors": errors,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        if decision.get("valid"):
            value = decision.get("raw_value")
            for transform in self._transformations:
                value = transform(value)
            return {"value_object": self.name, "action": "transformed", "value": value}
        return {
            "value_object": self.name,
            "action": "validation_failed",
            "errors": decision.get("errors"),
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        pass

    def add_transformation(self, transform: callable) -> None:
        self._transformations.append(transform)


class PolicyAgent(CognitiveAgent):
    """Policy Agent — reasons about governance.

    Responsibilities:
    - Enforce governance rules
    - Evaluate compliance
    - Produce policy decisions
    - Track policy violations
    """

    def __init__(self, name: str, rules: list[callable] | None = None):
        self.name = name
        self.rules = rules or []
        self._violations: list[dict] = []

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"policy": self.name, "context": context}

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        violations = [
            rule(perception["context"])
            for rule in self.rules
            if not rule(perception["context"])
        ]
        return {
            "policy": self.name,
            "compliant": len(violations) == 0,
            "violations": violations,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        if decision.get("compliant"):
            return {"policy": self.name, "action": "approved", "result": "compliant"}
        self._violations.extend(decision.get("violations", []))
        return {
            "policy": self.name,
            "action": "rejected",
            "violations": decision.get("violations"),
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        pass


class RepositoryAgent(CognitiveAgent):
    """Repository Agent — reasons about persistence.

    Responsibilities:
    - Manage data access
    - Optimize queries
    - Handle caching
    - Ensure data consistency
    """

    def __init__(self, name: str, store: Any = None):
        self.name = name
        self.store = store
        self._cache: dict[str, Any] = {}
        self._query_count: int = 0

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "repository": self.name,
            "query": context.get("query"),
            "cached": context.get("query") in self._cache,
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        if perception.get("cached"):
            return {
                "repository": self.name,
                "action": "cache_hit",
                "query": perception["query"],
            }
        return {
            "repository": self.name,
            "action": "database_query",
            "query": perception["query"],
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        self._query_count += 1
        return {
            "repository": self.name,
            "action": decision["action"],
            "query_count": self._query_count,
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        pass


class FactoryAgent(CognitiveAgent):
    """Factory Agent — reasons about creation.

    Responsibilities:
    - Create domain objects
    - Validate creation parameters
    - Apply default values
    - Emit creation events
    """

    def __init__(self, name: str, creators: dict[str, callable] | None = None):
        self.name = name
        self.creators = creators or {}
        self._created: list[dict] = []

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "factory": self.name,
            "object_type": context.get("type"),
            "params": context.get("params", {}),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        obj_type = perception.get("object_type")
        creator = self.creators.get(obj_type)
        return {
            "factory": self.name,
            "can_create": creator is not None,
            "type": obj_type,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        if decision.get("can_create"):
            creator = self.creators[decision["type"]]
            obj = creator()
            self._created.append(
                {"type": decision["type"], "id": getattr(obj, "id", None)}
            )
            return {"factory": self.name, "action": "created", "type": decision["type"]}
        return {
            "factory": self.name,
            "action": "creation_failed",
            "reason": "unknown_type",
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        pass


class EventAgent(CognitiveAgent):
    """Event Agent — reasons about temporal causality.

    Responsibilities:
    - Capture domain events
    - Correlate events
    - Detect event patterns
    - Produce event-driven actions
    """

    def __init__(self, name: str):
        self.name = name
        self._events: list[dict] = []
        self._patterns: list[callable] = []

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        event = context.get("event")
        if event:
            self._events.append(event)
        return {
            "event_agent": self.name,
            "event_count": len(self._events),
            "latest_event": event,
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        patterns = [p(self._events) for p in self._patterns if p(self._events)]
        return {
            "event_agent": self.name,
            "patterns_detected": patterns,
            "event_count": len(self._events),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_agent": self.name,
            "action": "event_processed",
            "patterns": decision.get("patterns_detected"),
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        pass

    def add_pattern(self, pattern: callable) -> None:
        self._patterns.append(pattern)


class DomainServiceAgent(CognitiveAgent):
    """Domain Service Agent — reasons about operations spanning multiple entities.

    Responsibilities:
    - Orchestrate cross-aggregate operations
    - Coordinate domain services
    - Produce service-level outcomes
    """

    def __init__(self, name: str):
        self.name = name
        self._memory: list[dict] = []

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"service": self.name, "operation": context.get("operation")}

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return {"service": self.name, "action": "execute_operation"}

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        return {"service": self.name, "action": "executed", "result": "success"}

    def learn(self, outcome: dict[str, Any]) -> None:
        self._memory.append(outcome)


# ---------------------------------------------------------------------------
# Evidence Adapter Protocol
# ---------------------------------------------------------------------------


class DomainEvidenceAdapter(Protocol):
    """Interprets generic ExecutionOutcome into domain-specific evidence."""

    def interpret(
        self, outcome: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]: ...
    def validate(self, evidence: dict[str, Any]) -> bool: ...


# ---------------------------------------------------------------------------
# Bounded Context
# ---------------------------------------------------------------------------


@dataclass
class BoundedContext:
    """A bounded context defines a domain boundary."""

    name: str
    aggregates: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    repositories: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Domain Package
# ---------------------------------------------------------------------------


@dataclass
class DomainPackage:
    """An installable cognitive application following DDD.

    Every DDD construct is an executable cognitive abstraction.
    """

    name: str
    version: str = "0.1.0"
    description: str = ""

    # DDD components
    bounded_context: BoundedContext | None = None
    aggregates: list[dict[str, Any]] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    value_objects: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    repositories: list[dict[str, Any]] = field(default_factory=list)
    services: list[dict[str, Any]] = field(default_factory=list)

    # Runtime components
    evidence_adapter: DomainEvidenceAdapter | None = None
    policies: list[dict[str, Any]] = field(default_factory=list)
    capabilities: list[dict[str, Any]] = field(default_factory=list)

    def load(self, base_path: Path) -> None:
        """Load the domain package from disk following DDD structure."""
        manifest = base_path / "manifest.yaml"
        if manifest.exists():
            import yaml

            with open(manifest) as f:
                data = yaml.safe_load(f) or {}
            self.version = data.get("version", self.version)
            self.description = data.get("description", self.description)

        bc_path = base_path / "bounded_context"
        if bc_path.exists():
            self.bounded_context = BoundedContext(name=self.name)

        agg_path = base_path / "aggregates"
        if agg_path.exists():
            for f in agg_path.glob("*.py"):
                if f.name.startswith("_"):
                    continue
                self.aggregates.append({"file": str(f), "name": f.stem})

        ent_path = base_path / "entities"
        if ent_path.exists():
            for f in ent_path.glob("*.py"):
                if f.name.startswith("_"):
                    continue
                self.entities.append({"file": str(f), "name": f.stem})

        vo_path = base_path / "value_objects"
        if vo_path.exists():
            for f in vo_path.glob("*.py"):
                if f.name.startswith("_"):
                    continue
                self.value_objects.append({"file": str(f), "name": f.stem})

        evt_path = base_path / "events"
        if evt_path.exists():
            for f in evt_path.glob("*.py"):
                if f.name.startswith("_"):
                    continue
                self.events.append({"file": str(f), "name": f.stem})

        repo_path = base_path / "repositories"
        if repo_path.exists():
            for f in repo_path.glob("*.py"):
                if f.name.startswith("_"):
                    continue
                self.repositories.append({"file": str(f), "name": f.stem})

        svc_path = base_path / "services"
        if svc_path.exists():
            for f in svc_path.glob("*.py"):
                if f.name.startswith("_"):
                    continue
                self.services.append({"file": str(f), "name": f.stem})

        adapter_file = base_path / "evidence" / "adapter.py"
        if adapter_file.exists():
            spec = importlib.util.spec_from_file_location(
                f"domains.{self.name}.evidence.adapter", adapter_file
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "DomainEvidence"):
                self.evidence_adapter = mod.DomainEvidence()

        const_path = base_path / "constitutions"
        if const_path.exists():
            for f in const_path.glob("*.yaml"):
                import yaml

                with open(f) as fh:
                    policy = yaml.safe_load(fh) or {}
                self.policies.append(policy)

        logger.info(
            "Domain package %s v%s loaded: %d aggregates, %d entities, %d services, %d policies",
            self.name,
            self.version,
            len(self.aggregates),
            len(self.entities),
            len(self.services),
            len(self.policies),
        )

    def interpret_outcome(
        self, outcome: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        """Interpret a generic ExecutionOutcome into domain-specific evidence."""
        if self.evidence_adapter:
            return self.evidence_adapter.interpret(outcome, context)
        return {
            "domain": self.name,
            "raw_outcome": outcome,
            "interpretation": "no adapter",
        }


# ---------------------------------------------------------------------------
# Domain Registry
# ---------------------------------------------------------------------------


class DomainRegistry:
    """Registry of loaded domain packages."""

    _instance = None

    def __init__(self):
        self._domains: dict[str, DomainPackage] = {}

    @classmethod
    def instance(cls) -> DomainRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register(self, domain: DomainPackage) -> None:
        self._domains[domain.name] = domain

    def get(self, name: str) -> DomainPackage | None:
        return self._domains.get(name)

    def list_all(self) -> list[str]:
        return list(self._domains.keys())

    def load_from_directory(self, domains_dir: Path) -> None:
        """Auto-discover and load all domain packages from a directory."""
        if not domains_dir.exists():
            return
        for entry in domains_dir.iterdir():
            if entry.is_dir() and not entry.name.startswith("_"):
                manifest = entry / "manifest.yaml"
                init = entry / "__init__.py"
                if manifest.exists() or init.exists():
                    pkg = DomainPackage(name=entry.name)
                    try:
                        pkg.load(entry)
                        self.register(pkg)
                    except Exception as e:
                        logger.warning("Failed to load domain %s: %s", entry.name, e)
