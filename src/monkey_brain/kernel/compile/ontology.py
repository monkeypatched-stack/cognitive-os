"""Ontology — typed entity and relationship hierarchy for the Global World.

Defines the structure of the world:
- Entity types and their attributes
- Relationship types and their attributes
- Type inheritance
- Domain constraints
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agentos.ontology")


@dataclass
class TypeDef:
    """Definition of an entity or relationship type."""

    name: str
    parent: str | None
    category: str
    attributes: dict[str, str] = field(default_factory=dict)
    constraints: list[dict] = field(default_factory=list)
    domain: str = "default"


class Ontology:
    """Type hierarchy for entities and relationships.

    Read-only after initialization — never modified by actor cognition.
    """

    def __init__(self) -> None:
        self._entity_types: dict[str, TypeDef] = {}
        self._relation_types: dict[str, TypeDef] = {}
        self._init_defaults()

    def _init_defaults(self) -> None:
        """Initialize default type hierarchy."""
        # Entity types
        self.add_entity_type("entity", None, "generic")
        self.add_entity_type("physical_entity", "entity", "physical")
        self.add_entity_type(
            "person",
            "physical_entity",
            "physical",
            attributes={"name": "str", "age": "int", "role": "str"},
        )
        self.add_entity_type(
            "vehicle",
            "physical_entity",
            "physical",
            attributes={"capacity_kg": "float", "fuel_type": "str"},
        )
        self.add_entity_type("car", "vehicle", "physical", attributes={"plate": "str"})
        self.add_entity_type("truck", "vehicle", "physical", attributes={"axles": "int"})
        self.add_entity_type("robot", "physical_entity", "physical", attributes={"robot_type": "str"})
        self.add_entity_type(
            "machine",
            "physical_entity",
            "physical",
            attributes={"capacity": "float", "status": "str"},
        )
        self.add_entity_type(
            "product",
            "physical_entity",
            "physical",
            attributes={"category": "str", "unit": "str", "brand": "str"},
        )
        self.add_entity_type("building", "physical_entity", "physical", attributes={"address": "str"})
        self.add_entity_type(
            "road",
            "physical_entity",
            "physical",
            attributes={"road_type": "str", "blocked": "bool"},
        )
        self.add_entity_type(
            "sensor",
            "physical_entity",
            "physical",
            attributes={"sensor_type": "str", "reading": "float"},
        )
        self.add_entity_type(
            "organization",
            "entity",
            "organization",
            attributes={"name": "str", "legal_form": "str"},
        )
        self.add_entity_type(
            "enterprise",
            "organization",
            "organization",
            attributes={"revenue": "float", "employees": "int"},
        )
        self.add_entity_type(
            "government",
            "organization",
            "organization",
            attributes={"jurisdiction": "str"},
        )
        self.add_entity_type(
            "community",
            "organization",
            "organization",
            attributes={"population": "int"},
        )
        self.add_entity_type(
            "location",
            "entity",
            "location",
            attributes={"lat": "float", "lon": "float"},
        )
        self.add_entity_type("city", "location", "location")
        self.add_entity_type("factory", "location", "location")
        self.add_entity_type("warehouse", "location", "location")
        self.add_entity_type("store", "location", "location")
        self.add_entity_type("digital_entity", "entity", "digital", attributes={"version": "str"})
        self.add_entity_type("ai_agent", "digital_entity", "digital", attributes={"model": "str"})

        # Relation types
        self.add_relation_type("related_to", None, "general")
        self.add_relation_type("located_in", "related_to", "location")
        self.add_relation_type("located_on", "related_to", "location")
        self.add_relation_type("connected_to", "related_to", "location")
        self.add_relation_type("adjacent_to", "related_to", "location")
        self.add_relation_type("owns", "related_to", "ownership")
        self.add_relation_type("manages", "related_to", "ownership")
        self.add_relation_type("controls", "related_to", "ownership")
        self.add_relation_type("works_for", "related_to", "employment")
        self.add_relation_type("reports_to", "related_to", "employment")
        self.add_relation_type("member_of", "related_to", "employment")
        self.add_relation_type("drives", "related_to", "transportation")
        self.add_relation_type("rides", "related_to", "transportation")
        self.add_relation_type("operates", "related_to", "transportation")
        self.add_relation_type("travels_to", "related_to", "transportation")
        self.add_relation_type("produces", "related_to", "manufacturing")
        self.add_relation_type("consumes", "related_to", "manufacturing")
        self.add_relation_type("supplies", "related_to", "manufacturing")
        self.add_relation_type("delivers", "related_to", "manufacturing")
        self.add_relation_type("regulates", "related_to", "government")
        self.add_relation_type("licenses", "related_to", "government")
        self.add_relation_type("taxes", "related_to", "government")
        self.add_relation_type("inspects", "related_to", "government")
        self.add_relation_type("uses", "related_to", "general")
        self.add_relation_type("depends_on", "related_to", "general")
        self.add_relation_type("communicates_with", "related_to", "general")
        self.add_relation_type("observes", "related_to", "general")
        self.add_relation_type("transitions_to", "related_to", "procedural")

    def add_entity_type(self, name: str, parent: str | None, category: str, **kwargs: Any) -> TypeDef:
        td = TypeDef(
            name=name,
            parent=parent,
            category=category,
            attributes=kwargs.get("attributes", {}),
            constraints=kwargs.get("constraints", []),
            domain=kwargs.get("domain", "default"),
        )
        self._entity_types[name] = td
        return td

    def add_relation_type(self, name: str, parent: str | None, category: str, **kwargs: Any) -> TypeDef:
        td = TypeDef(
            name=name,
            parent=parent,
            category=category,
            attributes=kwargs.get("attributes", {}),
            constraints=kwargs.get("constraints", []),
            domain=kwargs.get("domain", "default"),
        )
        self._relation_types[name] = td
        return td

    def get_entity_type(self, name: str) -> TypeDef | None:
        return self._entity_types.get(name)

    def get_relation_type(self, name: str) -> TypeDef | None:
        return self._relation_types.get(name)

    def is_subtype(self, child: str, parent: str) -> bool:
        current = child
        while current:
            if current == parent:
                return True
            td = self._entity_types.get(current) or self._relation_types.get(current)
            current = td.parent if td else None
        return False

    def attributes_of(self, type_name: str) -> dict[str, str]:
        attrs: dict[str, str] = {}
        current = type_name
        while current:
            td = self._entity_types.get(current) or self._relation_types.get(current)
            if td:
                attrs.update(td.attributes)
                current = td.parent
            else:
                break
        return attrs

    def entity_types_by_category(self, category: str) -> list[TypeDef]:
        return [td for td in self._entity_types.values() if td.category == category]

    def relation_types_by_category(self, category: str) -> list[TypeDef]:
        return [td for td in self._relation_types.values() if td.category == category]

    def export(self) -> dict:
        return {
            "entity_types": {
                k: {
                    "parent": v.parent,
                    "category": v.category,
                    "attributes": v.attributes,
                    "constraints": v.constraints,
                }
                for k, v in self._entity_types.items()
            },
            "relation_types": {
                k: {
                    "parent": v.parent,
                    "category": v.category,
                    "attributes": v.attributes,
                    "constraints": v.constraints,
                }
                for k, v in self._relation_types.items()
            },
        }

    def validate_entity(self, entity_type: str, attributes: dict[str, Any]) -> list[str]:
        """Validate entity attributes against ontology constraints.

        Returns list of validation errors (empty if valid).
        """
        errors = []
        td = self._entity_types.get(entity_type)
        if td is None:
            errors.append(f"Unknown entity type: {entity_type}")
            return errors

        # Check required attributes
        for attr_name, attr_type in td.attributes.items():
            if attr_name not in attributes:
                errors.append(f"Missing required attribute: {attr_name} (type: {attr_type})")
            elif not self._check_type(attributes[attr_name], attr_type):
                errors.append(
                    f"Attribute {attr_name} has wrong type: expected {attr_type}, got {type(attributes[attr_name]).__name__}"
                )

        # Check constraints
        for constraint in td.constraints:
            if not self._check_constraint(attributes, constraint):
                errors.append(f"Constraint violated: {constraint}")

        return errors

    def validate_relationship(self, relation_type: str, source_type: str, target_type: str) -> list[str]:
        """Validate that a relationship is allowed between two entity types.

        Returns list of validation errors (empty if valid).
        """
        errors = []
        if relation_type not in self._relation_types:
            errors.append(f"Unknown relation type: {relation_type}")
        if source_type not in self._entity_types:
            errors.append(f"Unknown source entity type: {source_type}")
        if target_type not in self._entity_types:
            errors.append(f"Unknown target entity type: {target_type}")
        return errors

    # ── Entity/Relationship type removal ──────────────────────────────────────

    def remove_entity_type(self, name: str) -> bool:
        """Remove an entity type. Returns True if removed."""
        if name in self._entity_types:
            # Check if any types inherit from this one
            children = [k for k, v in self._entity_types.items() if v.parent == name]
            if children:
                logger.warning("[ontology] cannot remove %s: has children %s", name, children)
                return False
            del self._entity_types[name]
            return True
        return False

    def remove_relation_type(self, name: str) -> bool:
        """Remove a relationship type. Returns True if removed."""
        if name in self._relation_types:
            children = [k for k, v in self._relation_types.items() if v.parent == name]
            if children:
                logger.warning("[ontology] cannot remove %s: has children %s", name, children)
                return False
            del self._relation_types[name]
            return True
        return False

    def deprecate_entity_type(self, name: str, replacement: str | None = None) -> None:
        """Mark an entity type as deprecated."""
        td = self._entity_types.get(name)
        if td:
            td.constraints.append({"type": "deprecated", "replacement": replacement})

    def is_deprecated(self, name: str) -> bool:
        """Check if a type is deprecated."""
        td = self._entity_types.get(name) or self._relation_types.get(name)
        if td:
            return any(c.get("type") == "deprecated" for c in td.constraints)
        return False

    @staticmethod
    def _check_type(value: Any, expected_type: str) -> bool:
        """Check if a value matches the expected type."""
        type_map = {
            "str": str,
            "int": int,
            "float": (int, float),
            "bool": bool,
            "list": list,
            "dict": dict,
        }
        expected = type_map.get(expected_type)
        if expected is None:
            return True  # Unknown type, skip validation
        return isinstance(value, expected)

    @staticmethod
    def _check_constraint(attributes: dict, constraint: dict) -> bool:
        """Check if attributes satisfy a constraint."""
        return True  # Default: pass

    def summary(self) -> dict:
        return {
            "entity_types": len(self._entity_types),
            "relation_types": len(self._relation_types),
            "entity_categories": list(set(td.category for td in self._entity_types.values())),
            "relation_categories": list(set(td.category for td in self._relation_types.values())),
        }
