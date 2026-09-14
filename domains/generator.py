"""DDD Domain Generator — turns business requirements into domain packages.

Pipeline:
    Business Requirements
        │
        ▼
    DDD Domain Generator    ← this module
        │
        ▼
    SOMA Specification      ← declarative charts
        │
        ▼
    ETASS Specification     ← goal, policies, evidence, success criteria

The Domain Generator takes business requirements and produces:
1. A Domain Package (bounded context, aggregates, entities, value objects)
2. SOMA charts (declarative specifications)
3. An ETASS specification (goal, policies, constitutions, evidence schemas)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("domains.generator")


# ---------------------------------------------------------------------------
# ETASS Specification
# ---------------------------------------------------------------------------


@dataclass
class ETASSSpecification:
    """ETASS specification generated from business requirements."""

    goal: str = ""
    domain: str = ""
    bounded_context: str = ""
    reasoning: str = "chain_of_thought"
    constraints: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    policies: list[str] = field(default_factory=list)
    constitutions: list[str] = field(default_factory=list)

    def to_yaml(self) -> str:
        """Serialize to YAML."""
        return yaml.dump(
            {
                "goal": self.goal,
                "domain": self.domain,
                "bounded_context": self.bounded_context,
                "reasoning": self.reasoning,
                "constraints": self.constraints,
                "evidence": self.evidence,
                "success_criteria": self.success_criteria,
                "policies": self.policies,
                "constitutions": self.constitutions,
            },
            default_flow_style=False,
        )


# ---------------------------------------------------------------------------
# SOMA Chart
# ---------------------------------------------------------------------------


@dataclass
class SOMAChart:
    """SOMA chart — declarative specification for a domain capability."""

    name: str
    version: str = "0.1.0"
    description: str = ""
    aggregates: list[dict[str, Any]] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    value_objects: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    policies: list[dict[str, Any]] = field(default_factory=list)

    def to_yaml(self) -> str:
        """Serialize to YAML."""
        return yaml.dump(
            {
                "name": self.name,
                "version": self.version,
                "description": self.description,
                "aggregates": self.aggregates,
                "entities": self.entities,
                "value_objects": self.value_objects,
                "events": self.events,
                "policies": self.policies,
            },
            default_flow_style=False,
        )


# ---------------------------------------------------------------------------
# DDD Domain Generator
# ---------------------------------------------------------------------------


class DDDDomainGenerator:
    """Generates domain packages from business requirements.

    Pipeline:
        Business Requirements → DDD Domain Generator → SOMA Specification → ETASS Specification
    """

    def __init__(self, output_dir: Path | None = None):
        self.output_dir = output_dir or Path("domains")

    def generate(self, requirements: dict[str, Any]) -> dict[str, Any]:
        """Generate a domain package from business requirements.

        Args:
            requirements: Business requirements dict with keys:
                - name: domain name
                - description: domain description
                - capabilities: list of business capabilities
                - entities: list of entity types
                - events: list of domain events
                - policies: list of governance policies
                - success_criteria: list of success criteria

        Returns:
            dict with 'soma_chart', 'etass_spec', 'domain_package_path'
        """
        name = requirements.get("name", "unknown")
        description = requirements.get("description", "")
        capabilities = requirements.get("capabilities", [])
        entities = requirements.get("entities", [])
        events = requirements.get("events", [])
        policies = requirements.get("policies", [])
        success_criteria = requirements.get("success_criteria", [])

        # 1. Generate SOMA chart
        soma_chart = self._generate_soma_chart(
            name, description, capabilities, entities, events, policies
        )

        # 2. Generate ETASS specification
        etass_spec = self._generate_etass_spec(
            name, description, capabilities, policies, success_criteria
        )

        # 3. Generate domain package structure
        domain_path = self._generate_domain_package(
            name, soma_chart, etass_spec, entities, events, policies
        )

        return {
            "soma_chart": soma_chart,
            "etass_spec": etass_spec,
            "domain_package_path": str(domain_path),
            "name": name,
        }

    def _generate_soma_chart(
        self,
        name: str,
        description: str,
        capabilities: list,
        entities: list,
        events: list,
        policies: list,
    ) -> SOMAChart:
        """Generate a SOMA chart from requirements."""
        chart = SOMAChart(
            name=name,
            description=description,
        )

        # Generate aggregates from capabilities
        for cap in capabilities:
            chart.aggregates.append(
                {
                    "name": cap.get("name", "unknown"),
                    "description": cap.get("description", ""),
                    "entities": cap.get("entities", []),
                }
            )

        # Generate entities
        for ent in entities:
            chart.entities.append(
                {
                    "name": ent.get("name", "unknown"),
                    "attributes": ent.get("attributes", []),
                    "lifecycle": ent.get(
                        "lifecycle", ["created", "active", "archived"]
                    ),
                }
            )

        # Generate events
        for evt in events:
            chart.events.append(
                {
                    "name": evt.get("name", "unknown"),
                    "trigger": evt.get("trigger", ""),
                    "payload": evt.get("payload", []),
                }
            )

        # Generate policies
        for pol in policies:
            chart.policies.append(
                {
                    "name": pol.get("name", "unknown"),
                    "rules": pol.get("rules", []),
                }
            )

        return chart

    def _generate_etass_spec(
        self,
        name: str,
        description: str,
        capabilities: list,
        policies: list,
        success_criteria: list,
    ) -> ETASSSpecification:
        """Generate an ETASS specification from requirements."""
        # Derive goal from capabilities
        goal = f"Implement {name} domain with {len(capabilities)} capabilities"

        # Derive constraints from policies
        constraints = []
        for pol in policies:
            constraints.append(pol.get("name", "unknown"))

        # Derive evidence schemas from capabilities
        evidence = []
        for cap in capabilities:
            evidence.append(f"{cap.get('name', 'unknown')}_evidence")

        return ETASSSpecification(
            goal=goal,
            domain=name,
            bounded_context=name,
            reasoning="chain_of_thought",
            constraints=constraints,
            evidence=evidence,
            success_criteria=success_criteria,
            policies=[p.get("name", "unknown") for p in policies],
            constitutions=[p.get("name", "unknown") for p in policies],
        )

    def _generate_domain_package(
        self,
        name: str,
        soma_chart: SOMAChart,
        etass_spec: ETASSSpecification,
        entities: list,
        events: list,
        policies: list,
    ) -> Path:
        """Generate domain package directory structure."""
        domain_path = self.output_dir / name
        domain_path.mkdir(parents=True, exist_ok=True)

        # Write manifest
        manifest = {
            "name": name,
            "version": "0.1.0",
            "description": soma_chart.description,
        }
        with open(domain_path / "manifest.yaml", "w") as f:
            yaml.dump(manifest, f, default_flow_style=False)

        # Write SOMA chart
        soma_path = domain_path / "soma"
        soma_path.mkdir(exist_ok=True)
        with open(soma_path / "chart.yaml", "w") as f:
            f.write(soma_chart.to_yaml())

        # Write ETASS spec
        etass_path = domain_path / "etass_spec.yaml"
        with open(etass_path, "w") as f:
            f.write(etass_spec.to_yaml())

        # Create evidence adapter stub
        evidence_path = domain_path / "evidence"
        evidence_path.mkdir(exist_ok=True)
        with open(evidence_path / "adapter.py", "w") as f:
            f.write(
                f'"""Domain Evidence Adapter for {name}."""\n\n\nclass DomainEvidence:\n    def interpret(self, outcome, context):\n        return {{"domain": "{name}", "outcome": outcome}}\n\n    def validate(self, evidence):\n        return True\n'
            )

        # Create constitutions stub
        const_path = domain_path / "constitutions"
        const_path.mkdir(exist_ok=True)
        with open(const_path / "default.yaml", "w") as f:
            yaml.dump({"name": f"{name}_constitution", "rules": []}, f)

        # Create empty directories
        for subdir in [
            "bounded_context",
            "aggregates",
            "entities",
            "value_objects",
            "events",
            "repositories",
            "services",
            "broca",
            "cerebellum",
            "workloads",
        ]:
            (domain_path / subdir).mkdir(exist_ok=True)
            (domain_path / subdir / "__init__.py").touch()

        logger.info("Generated domain package: %s at %s", name, domain_path)
        return domain_path
