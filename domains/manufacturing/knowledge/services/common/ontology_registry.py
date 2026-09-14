from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - requirements include PyYAML in normal installs
    yaml = None


REPO_ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY_REGISTRY_PATH = REPO_ROOT / "config" / "ontology" / "graph_ontology.v1.yaml"
INTEGRATION_ADAPTERS_DIR = REPO_ROOT / "config" / "integration_adapters"
FACTORY_MANIFEST_TEMPLATE_PATH = (
    REPO_ROOT / "deployments" / "factory_manifests" / "template.customer.yaml"
)


def _load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError(
            "PyYAML is required to load declarative ontology and integration manifests."
        )
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML object.")
    return data


@lru_cache(maxsize=1)
def load_ontology_registry() -> dict[str, Any]:
    return _load_yaml(ONTOLOGY_REGISTRY_PATH)


@lru_cache(maxsize=1)
def ontology_collection_map() -> dict[str, dict[str, Any]]:
    registry = load_ontology_registry()
    mapped: dict[str, dict[str, Any]] = {}
    for entity_name, entity in (registry.get("entity_types") or {}).items():
        for collection in entity.get("collections") or []:
            mapped[str(collection)] = {"entity_name": entity_name, **entity}
    return mapped


def ontology_entity_for_collection(collection: str) -> dict[str, Any] | None:
    return ontology_collection_map().get(collection)


def load_integration_adapter(adapter_id: str) -> dict[str, Any]:
    path = INTEGRATION_ADAPTERS_DIR / f"{adapter_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Integration adapter manifest not found: {adapter_id}")
    return _load_yaml(path)


def list_integration_adapters() -> list[dict[str, Any]]:
    adapters = []
    for path in sorted(INTEGRATION_ADAPTERS_DIR.glob("*.yaml")):
        data = _load_yaml(path)
        adapters.append(
            {
                "adapter_id": data.get("adapter_id"),
                "version": data.get("version"),
                "vendor": data.get("vendor"),
                "systems": data.get("systems") or [],
                "mapping_count": len(data.get("mappings") or []),
                "path": str(path.relative_to(REPO_ROOT)),
            }
        )
    return adapters


def load_factory_manifest_template() -> dict[str, Any]:
    return _load_yaml(FACTORY_MANIFEST_TEMPLATE_PATH)


def validate_declarative_architecture() -> list[str]:
    errors: list[str] = []
    registry = load_ontology_registry()
    entity_types = registry.get("entity_types") or {}
    if not registry.get("version"):
        errors.append("Ontology registry is missing version.")
    if not entity_types:
        errors.append("Ontology registry must define entity_types.")
    collections = set()
    for entity_name, entity in entity_types.items():
        for key in ("collections", "label", "id_fields", "properties"):
            if not entity.get(key):
                errors.append(f"Entity {entity_name} is missing {key}.")
        for collection in entity.get("collections") or []:
            if collection in collections:
                errors.append(
                    f"Collection {collection} is mapped to more than one entity type."
                )
            collections.add(collection)

    entity_names = set(entity_types)
    for adapter in list_integration_adapters():
        manifest = load_integration_adapter(str(adapter["adapter_id"]))
        for mapping in manifest.get("mappings") or []:
            target_entity = mapping.get("target_entity")
            if target_entity not in entity_names:
                errors.append(
                    f"Adapter {adapter['adapter_id']} maps to unknown ontology entity {target_entity}."
                )
            if not mapping.get("target_collection"):
                errors.append(
                    f"Adapter {adapter['adapter_id']} mapping {mapping.get('source_object')} missing target_collection."
                )
            if not mapping.get("id"):
                errors.append(
                    f"Adapter {adapter['adapter_id']} mapping {mapping.get('source_object')} missing id mapping."
                )

    factory_manifest = load_factory_manifest_template()
    adapter_ids = {adapter["adapter_id"] for adapter in list_integration_adapters()}
    for binding in factory_manifest.get("adapter_bindings") or []:
        if binding.get("adapter_id") not in adapter_ids:
            errors.append(
                f"Factory manifest references unknown adapter {binding.get('adapter_id')}."
            )
    return errors
