"""Domain-Agnostic Monkey Brain Core.

This module provides the foundation for Monkey Brain's domain-agnostic architecture:

ETASS (Enterprise Architecture as a Software Service)
    │
    ▼
Monkey Brain (Domain-Agnostic Runtime)
    │
    ▼
Domain-Specific Workloads (SE, Manufacturing, Robotics, etc.)
    │
    ▼
Domain Evidence Adapters
    │
    ▼
Domain-Specific Evidence

Key Components:
- DomainGoal: Domain-agnostic goal representation
- ExecutionOutcome: Domain-agnostic execution result
- BaseAgent: Domain-agnostic agent interface
- BaseCapability: Domain-agnostic capability interface
- DomainRegistry: Registry for domain components
- CognitiveProcessGenerator: Creates execution workflows
- MonkeyBrainRuntime: Core domain-agnostic runtime
- DomainEvidence: Base class for domain-specific evidence
- BaseEvidenceAdapter: Interface for evidence adapters
- EvidenceAdapterRegistry: Registry for evidence adapters
- EvidencePublisher: Publishes domain-specific evidence
"""

from __future__ import annotations

from .core import (
    DomainGoal,
    ExecutionOutcome,
    BaseAgent,
    BaseCapability,
    DomainRegistry,
    CognitiveProcessGenerator,
    MonkeyBrainRuntime,
)

from .evidence import (
    DomainEvidence,
    BaseEvidenceAdapter,
    EvidenceAdapterRegistry,
    EvidencePublisher,
    SoftwareEngineeringEvidence,
    SoftwareEngineeringEvidenceAdapter,
    ManufacturingEvidence,
    ManufacturingEvidenceAdapter,
    RoboticsEvidence,
    RoboticsEvidenceAdapter,
)

__all__ = [
    # Core components
    "DomainGoal",
    "ExecutionOutcome",
    "BaseAgent",
    "BaseCapability",
    "DomainRegistry",
    "CognitiveProcessGenerator",
    "MonkeyBrainRuntime",
    # Evidence components
    "DomainEvidence",
    "BaseEvidenceAdapter",
    "EvidenceAdapterRegistry",
    "EvidencePublisher",
    # Domain-specific evidence
    "SoftwareEngineeringEvidence",
    "SoftwareEngineeringEvidenceAdapter",
    "ManufacturingEvidence",
    "ManufacturingEvidenceAdapter",
    "RoboticsEvidence",
    "RoboticsEvidenceAdapter",
]

# Architecture Diagram
ARCHITECTURE_DIAGRAM = """
ETASS
Enterprise Architecture as a Software Service
────────────────────────────────────────────────────────

• Governance
• Policies  
• Constitutions
• SOMA Specifications
• Architecture Evolution

                    │
                    ▼

               Monkey Brain
      Dynamic Process Generation Runtime
────────────────────────────────────────────────────────

Goal
↓
Dynamic Workflow Generation
↓
Agent Discovery
↓
Capability Discovery
↓
Execution
↓
ExecutionOutcome
↓
Event Publication

At this point Monkey Brain's job is finished.

Then the domain interprets the outcome:

ExecutionOutcome
        │
        ▼
Domain Evidence Adapter
        │
        ├── Software Engineering Evidence
        ├── Manufacturing Evidence
        ├── Robotics Evidence
        ├── Healthcare Evidence
        ├── Finance Evidence
        └── Research Evidence

This keeps Monkey Brain completely domain-agnostic.
"""

# Domain Architecture
DOMAIN_ARCHITECTURE = """
Monkey Brain
├── Manufacturing Workload
│     Batch Investigation
│     Production KPI
│     Work Orders
│     Quality Control
│
├── Robotics Workload
│     Navigation
│     Planning
│     Perception
│     Safety Validation
│
├── Software Engineering Workload
│     Self Healing
│     Stabilization
│     Refactoring
│     Modernization
│     Chaos Testing
│     SRE
│
├── Research Workload
│
├── Finance Workload
│
└── Healthcare Workload
"""

# Governance Flow
GOVERNANCE_FLOW = """
ETASS Governance Flow:

1. Domain Execution → Domain Evidence
2. Evidence Aggregation → Pattern Detection
3. Pattern Analysis → Architecture Review
4. Architecture Review → Specification Updates
5. Specification Updates → Improved Domain Execution

Key Principle:
- Monkey Brain doesn't know it's running software engineering
- It only knows it's executing a cognitive workload
- ETASS governs all domains with the same mechanism
- Only the specifications change per domain
"""

# Example Usage
EXAMPLE_USAGE = """
# 1. Create Monkey Brain runtime
from monkey_brain.common import MonkeyBrainRuntime, DomainGoal

runtime = MonkeyBrainRuntime()

# 2. Register domain components
# runtime.register_domain_components(agents, capabilities)

# 3. Execute a goal
goal = DomainGoal(
    domain="software_engineering",
    objective="Refactor legacy code module",
    success_criteria="All tests pass and code quality improved"
)

outcome = await runtime.execute_goal(goal)

# 4. Convert to domain evidence
from monkey_brain.common import EvidencePublisher, EvidenceAdapterRegistry

adapter_registry = EvidenceAdapterRegistry()
adapter_registry.register_adapter(SoftwareEngineeringEvidenceAdapter())
publisher = EvidencePublisher(adapter_registry)

evidence = await publisher.publish_outcome(outcome)

# 5. Evidence is now domain-specific and ready for analysis
print(f"Domain: {evidence.domain}")
print(f"Status: {evidence.status}")
print(f"Metrics: {evidence.metrics}")
"""

# Domain Comparison
DOMAIN_COMPARISON = """
For Software Engineering, ETASS governs:
- Architecture
- Policies
- Coding standards
- Testing constitutions
- Deployment policies

For Manufacturing, ETASS governs:
- SOPs
- GMP regulations
- Quality policies
- Safety constitutions

For Robotics, ETASS governs:
- Safety constraints
- Motion policies
- Simulation requirements
- World models

The governance mechanism is identical; only the specifications change.
"""
