"""SOLID Foundation Interfaces - Single Responsibility Principle.

Each interface represents ONE responsibility, enabling:
- Single Responsibility: Classes implement one interface = one reason to change
- Interface Segregation: Clients depend only on what they need
- Liskov Substitution: All implementations are substitutable
- Open/Closed: New implementations don't modify existing code
- Dependency Inversion: Depend on abstractions
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

# ─────────────────────────────────────────────────────────────────────────
# I: Interface Segregation - Focused, Single-Purpose Interfaces
# ─────────────────────────────────────────────────────────────────────────


class BootInterface(ABC):
    """S: Single Responsibility - Component initialization."""

    @abstractmethod
    async def boot(self, app: Any, **kwargs) -> None:
        """Boot the component with kernel dependencies."""
        pass

    @abstractmethod
    async def shutdown(self, app: Any) -> None:
        """Gracefully shutdown the component."""
        pass


class HealthMonitorInterface(ABC):
    """S: Single Responsibility - Health status reporting."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Component name identifier."""
        pass

    @property
    @abstractmethod
    def health(self) -> dict[str, Any]:
        """Get component health status."""
        pass


class PersistenceInterface(ABC):
    """S: Single Responsibility - Data persistence operations."""

    @abstractmethod
    def save(self, key: str, value: Any) -> None:
        """Save data."""
        pass

    @abstractmethod
    def load(self, key: str) -> Any | None:
        """Load data."""
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete data."""
        pass


class RepositoryInterface(ABC):
    """S: Single Responsibility - Aggregate root access."""

    @abstractmethod
    def find_by_id(self, entity_id: str) -> Any | None:
        """Find entity by ID."""
        pass

    @abstractmethod
    def find_all(self) -> list[Any]:
        """Find all entities."""
        pass

    @abstractmethod
    def save(self, entity: Any) -> None:
        """Save entity."""
        pass

    @abstractmethod
    def delete(self, entity_id: str) -> bool:
        """Delete entity."""
        pass


class ExecutorInterface(ABC):
    """S: Single Responsibility - Execute work."""

    @abstractmethod
    async def execute(self, work: Any) -> Any:
        """Execute work and return result."""
        pass


class CompilerInterface(ABC):
    """S: Single Responsibility - Compile abstract syntax."""

    @abstractmethod
    def compile(self, source: Any) -> Any:
        """Compile source into executable form."""
        pass


class EventPublisherInterface(ABC):
    """S: Single Responsibility - Publish domain events."""

    @abstractmethod
    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publish an event."""
        pass


class EventSubscriberInterface(ABC):
    """S: Single Responsibility - Subscribe to domain events."""

    @abstractmethod
    def subscribe(self, event_type: str, handler: Any) -> None:
        """Subscribe to an event type."""
        pass

    @abstractmethod
    def unsubscribe(self, event_type: str, handler: Any) -> None:
        """Unsubscribe from an event type."""
        pass


class ValidatorInterface(ABC):
    """S: Single Responsibility - Validate inputs."""

    @abstractmethod
    def validate(self, value: Any) -> tuple[bool, str]:
        """Validate value. Returns (is_valid, error_message)."""
        pass


class EntityInterface(ABC):
    """S: Single Responsibility - Domain entity base."""

    @property
    @abstractmethod
    def id(self) -> str:
        """Entity identifier."""
        pass

    @property
    @abstractmethod
    def version(self) -> int:
        """Entity version for optimistic locking."""
        pass


class AggregateInterface(EntityInterface):
    """S: Single Responsibility - Aggregate root."""

    @abstractmethod
    def get_events(self) -> list[Any]:
        """Get uncommitted domain events."""
        pass

    @abstractmethod
    def clear_events(self) -> None:
        """Clear uncommitted events after persistence."""
        pass


class ContextInterface(ABC):
    """S: Single Responsibility - Execution context."""

    @property
    @abstractmethod
    def tenant_id(self) -> str:
        """Tenant for this context."""
        pass

    @property
    @abstractmethod
    def request_id(self) -> str:
        """Request identifier."""
        pass

    @property
    @abstractmethod
    def metadata(self) -> dict[str, Any]:
        """Context metadata."""
        pass


# ─────────────────────────────────────────────────────────────────────────
# O: Open/Closed Principle - Abstract Base for Extension
# ─────────────────────────────────────────────────────────────────────────


class ComponentInterface(BootInterface, HealthMonitorInterface):
    """O: Open/Closed - Base for all system components.

    Open for extension: subclasses add functionality
    Closed for modification: interface doesn't change
    """

    pass


class ServiceInterface(ABC):
    """O: Open/Closed - Application service base.

    Orchestrates operations across multiple aggregates.
    """

    @abstractmethod
    async def execute(self, command: Any) -> Any:
        """Execute a command and return result."""
        pass


class SpecificationInterface(ABC):
    """O: Open/Closed - Business rule specification.

    Enables complex queries without coupling to repository.
    """

    @abstractmethod
    def is_satisfied_by(self, entity: Any) -> bool:
        """Check if entity satisfies specification."""
        pass

    @abstractmethod
    def to_sql(self) -> str:
        """Convert specification to SQL WHERE clause."""
        pass


class FactoryInterface(ABC):
    """O: Open/Closed - Object creation abstraction.

    Encapsulates complex creation logic.
    """

    @abstractmethod
    def create(self, **kwargs) -> Any:
        """Create object from parameters."""
        pass


# ─────────────────────────────────────────────────────────────────────────
# L: Liskov Substitution - Proper Behavioral Contracts
# ─────────────────────────────────────────────────────────────────────────


class TransactionalInterface(ABC):
    """L: Liskov - Transactional behavior guarantee.

    All implementations must ensure ACID properties.
    """

    @abstractmethod
    def begin_transaction(self) -> None:
        """Begin transaction."""
        pass

    @abstractmethod
    def commit(self) -> None:
        """Commit transaction."""
        pass

    @abstractmethod
    def rollback(self) -> None:
        """Rollback transaction."""
        pass


class CacheableInterface(ABC):
    """L: Liskov - Caching behavior guarantee.

    All implementations must return identical results.
    """

    @abstractmethod
    def get_from_cache(self, key: str) -> Any | None:
        """Get from cache."""
        pass

    @abstractmethod
    def set_in_cache(self, key: str, value: Any, ttl: int = 3600) -> None:
        """Set in cache with TTL."""
        pass

    @abstractmethod
    def invalidate_cache(self, key: str) -> None:
        """Invalidate cache entry."""
        pass


class ObservableInterface(ABC):
    """L: Liskov - Observable pattern guarantee.

    All implementations guarantee delivery semantics.
    """

    @abstractmethod
    def attach(self, observer: Any) -> None:
        """Attach observer."""
        pass

    @abstractmethod
    def detach(self, observer: Any) -> None:
        """Detach observer."""
        pass

    @abstractmethod
    def notify(self) -> None:
        """Notify all observers."""
        pass


# ─────────────────────────────────────────────────────────────────────────
# D: Dependency Inversion - Dependency Injection Contracts
# ─────────────────────────────────────────────────────────────────────────


class DependencyProviderInterface(ABC):
    """D: Dependency Inversion - Service locator abstraction."""

    @abstractmethod
    def get(self, key: str) -> Any:
        """Get dependency by key."""
        pass

    @abstractmethod
    def register(self, key: str, dependency: Any) -> None:
        """Register dependency."""
        pass


class InjectorInterface(ABC):
    """D: Dependency Inversion - Dependency injection."""

    @abstractmethod
    def inject(self, target: Any) -> Any:
        """Inject dependencies into target."""
        pass


# ─────────────────────────────────────────────────────────────────────────
# Composition: Combine Focused Interfaces for Complete Contract
# ─────────────────────────────────────────────────────────────────────────


class RepositoryComponentInterface(
    RepositoryInterface,
    PersistenceInterface,
    TransactionalInterface,
    HealthMonitorInterface,
):
    """Composition: Repository with persistence, transactions, health."""

    pass


class ServiceComponentInterface(ServiceInterface, ExecutorInterface, EventPublisherInterface, HealthMonitorInterface):
    """Composition: Service with execution, events, health."""

    pass


class RuntimeComponentInterface(
    ComponentInterface,
    ExecutorInterface,
    HealthMonitorInterface,
    TransactionalInterface,
):
    """Composition: Runtime with boot, execution, health, transactions."""

    pass


# ─────────────────────────────────────────────────────────────────────────
# Summary: SOLID Principles Applied
# ─────────────────────────────────────────────────────────────────────────

"""
S - Single Responsibility:
    Each interface has ONE reason to change
    ✅ BootInterface: only for lifecycle
    ✅ HealthMonitorInterface: only for health
    ✅ PersistenceInterface: only for data storage
    ✅ ExecutorInterface: only for work execution

O - Open/Closed:
    ComponentInterface, ServiceInterface, SpecificationInterface
    Open for extension via subclassing
    Closed for modification: interface is stable

L - Liskov Substitution:
    TransactionalInterface, CacheableInterface, ObservableInterface
    All implementations guarantee behavioral contracts
    Substitutable without breaking code

I - Interface Segregation:
    Small, focused interfaces instead of fat interfaces
    ✅ Clients depend only on what they need
    ✅ BootInterface doesn't force health monitoring
    ✅ PersistenceInterface doesn't force validation

D - Dependency Inversion:
    All dependencies flow toward abstractions
    Components depend on interfaces, not concrete classes
    DependencyProviderInterface enables loose coupling
"""
