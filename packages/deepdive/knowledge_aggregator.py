"""Knowledge Aggregation — cross-node knowledge synchronization.

Aggregates knowledge and learnings across edge nodes.

Knowledge pack lifecycle:
  write path: KnowledgeEntry (confidence >= 0.6) → knowledge pack YAML → somatic chart update → SittingFace publish
  read path:  knowledge pack YAML → KnowledgeEntry hydration → KnowledgeAggregator

flush_to_charts() is called at the end of each pipeline run.
Only entries that cross the CONFIDENCE_THRESHOLD (0.6) produce chart updates.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

logger = logging.getLogger("deepdive.knowledge_aggregator")

CONFIDENCE_THRESHOLD: float = 0.6

# Knowledge packs land here; each pack is one YAML file named <knowledge_type>.yaml
_PACK_DIR = Path(
    os.environ.get(
        "MONKEYBRAIN_KNOWLEDGE_PACKS_DIR",
        str(Path(__file__).parents[4] / "somatic" / "knowledge_packs"),
    )
)


@dataclass
class KnowledgeEntry:
    """A knowledge entry from an edge node."""

    entry_id: str = field(default_factory=lambda: f"kb-{uuid4().hex[:12]}")
    node_id: str = ""
    knowledge_type: str = ""  # policy | capability | pattern | error | success
    content: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AggregatedKnowledge:
    """Aggregated knowledge across nodes."""

    aggregation_id: str = field(default_factory=lambda: f"agg-{uuid4().hex[:12]}")
    knowledge_type: str = ""
    entries: list[KnowledgeEntry] = field(default_factory=list)
    total_nodes: int = 0
    avg_confidence: float = 0.0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class KnowledgePack:
    """Serializable bundle of high-confidence knowledge entries for one type."""

    pack_id: str = field(default_factory=lambda: f"pack-{uuid4().hex[:12]}")
    knowledge_type: str = ""
    entries: list[dict[str, Any]] = field(default_factory=list)
    avg_confidence: float = 0.0
    entry_count: int = 0
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    threshold_used: float = CONFIDENCE_THRESHOLD


class KnowledgeAggregator:
    """Aggregates knowledge across edge nodes.

    Responsibilities:
    - Collect knowledge from nodes
    - Merge similar knowledge
    - Rank by confidence
    - Flush high-confidence entries to somatic chart knowledge packs (write path)
    - Load knowledge packs back into memory (read path)

    Aggregation never performs cognition.
    Aggregation never controls execution.
    """

    def __init__(
        self,
        elasticsearch_adapter: Any = None,
        pack_dir: str | Path | None = None,
        confidence_threshold: float = CONFIDENCE_THRESHOLD,
    ):
        self._es = elasticsearch_adapter
        self._entries: dict[str, list[KnowledgeEntry]] = {}
        self._pack_dir = Path(pack_dir) if pack_dir else _PACK_DIR
        self._threshold = confidence_threshold

    # ------------------------------------------------------------------
    # Core collection
    # ------------------------------------------------------------------

    def add_entry(self, entry: KnowledgeEntry) -> None:
        """Add a knowledge entry from a node."""
        key = f"{entry.knowledge_type}:{entry.node_id}"
        if key not in self._entries:
            self._entries[key] = []
        self._entries[key].append(entry)
        if len(self._entries[key]) > 100:
            self._entries[key] = self._entries[key][-50:]

    def aggregate(self, knowledge_type: str) -> AggregatedKnowledge:
        """Aggregate knowledge of a specific type."""
        entries = []
        nodes: set[str] = set()
        for key, node_entries in self._entries.items():
            if key.startswith(knowledge_type):
                entries.extend(node_entries)
                for e in node_entries:
                    nodes.add(e.node_id)
        entries.sort(key=lambda e: e.confidence, reverse=True)
        return AggregatedKnowledge(
            knowledge_type=knowledge_type,
            entries=entries[:100],
            total_nodes=len(nodes),
            avg_confidence=(
                sum(e.confidence for e in entries) / len(entries) if entries else 0.0
            ),
        )

    def get_by_node(self, node_id: str) -> list[KnowledgeEntry]:
        entries = []
        for node_entries in self._entries.values():
            entries.extend([e for e in node_entries if e.node_id == node_id])
        return entries

    def get_by_type(self, knowledge_type: str) -> list[KnowledgeEntry]:
        entries = []
        for key, node_entries in self._entries.items():
            if key.startswith(knowledge_type):
                entries.extend(node_entries)
        return entries

    # ------------------------------------------------------------------
    # Write path — flush high-confidence entries to knowledge packs
    # ------------------------------------------------------------------

    def flush_to_charts(self) -> dict[str, Any]:
        """Write path: at end of run, flush entries with confidence >= threshold to packs.

        For each knowledge_type that has at least one entry above the threshold:
          1. Build a KnowledgePack (serialized YAML)
          2. Write to somatic/knowledge_packs/<type>.yaml
          3. Merge relevant fields into the matching somatic chart values.yaml

        Returns a summary dict: {flushed: int, skipped: int, packs: [str]}
        """
        self._pack_dir.mkdir(parents=True, exist_ok=True)

        # Group all above-threshold entries by knowledge_type
        by_type: dict[str, list[KnowledgeEntry]] = {}
        for entries in self._entries.values():
            for e in entries:
                if e.confidence >= self._threshold:
                    by_type.setdefault(e.knowledge_type, []).append(e)

        flushed = 0
        skipped = 0
        written_packs: list[str] = []

        all_types = set(key.split(":")[0] for key in self._entries)
        below_threshold_types = all_types - set(by_type)
        skipped = len(below_threshold_types)

        for ktype, entries in by_type.items():
            entries.sort(key=lambda e: e.confidence, reverse=True)
            avg_conf = sum(e.confidence for e in entries) / len(entries)
            pack = KnowledgePack(
                knowledge_type=ktype,
                entries=[asdict(e) for e in entries],
                avg_confidence=round(avg_conf, 4),
                entry_count=len(entries),
                threshold_used=self._threshold,
            )
            pack_path = self._pack_dir / f"{ktype}.yaml"
            with open(pack_path, "w") as f:
                yaml.dump(asdict(pack), f, default_flow_style=False, sort_keys=False)
            written_packs.append(str(pack_path))
            flushed += 1
            logger.info(
                "[knowledge] flushed pack %s: %d entries avg_confidence=%.3f → %s",
                ktype,
                len(entries),
                avg_conf,
                pack_path,
            )

            # Merge into somatic chart values.yaml if one exists
            self._merge_into_chart(ktype, pack)

        return {
            "flushed": flushed,
            "skipped": skipped,
            "threshold": self._threshold,
            "packs": written_packs,
        }

    def _merge_into_chart(self, knowledge_type: str, pack: KnowledgePack) -> bool:
        """Merge knowledge pack into the matching somatic chart values.yaml."""
        charts_dir = self._pack_dir.parent / "charts"
        chart_dir = charts_dir / knowledge_type
        if not chart_dir.exists():
            return False

        values_path = chart_dir / "values.yaml"
        if not values_path.exists():
            return False

        try:
            with open(values_path) as f:
                values = yaml.safe_load(f) or {}

            knowledge_block = values.setdefault("knowledge", {})
            knowledge_block["last_updated"] = pack.generated_at
            knowledge_block["avg_confidence"] = pack.avg_confidence
            knowledge_block["entry_count"] = pack.entry_count
            knowledge_block["threshold"] = pack.threshold_used
            knowledge_block["pack_id"] = pack.pack_id

            # Merge top-3 content keys from the highest-confidence entry
            if pack.entries:
                top = pack.entries[0].get("content", {})
                for k, v in list(top.items())[:3]:
                    if isinstance(v, (str, int, float, bool)):
                        knowledge_block[k] = v

            with open(values_path, "w") as f:
                yaml.dump(values, f, default_flow_style=False, sort_keys=False)

            logger.info(
                "[knowledge] merged pack into chart %s/values.yaml", knowledge_type
            )
            return True
        except Exception as exc:
            logger.warning(
                "[knowledge] chart merge failed for %s: %s", knowledge_type, exc
            )
            return False

    # ------------------------------------------------------------------
    # Read path — load knowledge packs from disk
    # ------------------------------------------------------------------

    def load_pack(self, knowledge_type: str) -> KnowledgePack | None:
        """Read path: load a knowledge pack YAML and return a KnowledgePack."""
        pack_path = self._pack_dir / f"{knowledge_type}.yaml"
        if not pack_path.exists():
            logger.debug("[knowledge] no pack found for %s", knowledge_type)
            return None
        with open(pack_path) as f:
            data = yaml.safe_load(f) or {}
        pack = KnowledgePack(
            pack_id=data.get("pack_id", ""),
            knowledge_type=data.get("knowledge_type", knowledge_type),
            entries=data.get("entries", []),
            avg_confidence=data.get("avg_confidence", 0.0),
            entry_count=data.get("entry_count", 0),
            generated_at=data.get("generated_at", ""),
            threshold_used=data.get("threshold_used", CONFIDENCE_THRESHOLD),
        )
        logger.info(
            "[knowledge] loaded pack %s: %d entries", knowledge_type, pack.entry_count
        )
        return pack

    def hydrate_from_pack(self, pack: KnowledgePack) -> int:
        """Read path: hydrate KnowledgeEntry objects from a KnowledgePack into this aggregator."""
        loaded = 0
        for raw in pack.entries:
            try:
                entry = KnowledgeEntry(
                    entry_id=raw.get("entry_id", f"kb-{uuid4().hex[:12]}"),
                    node_id=raw.get("node_id", ""),
                    knowledge_type=raw.get("knowledge_type", pack.knowledge_type),
                    content=raw.get("content", {}),
                    confidence=raw.get("confidence", 0.0),
                    timestamp=raw.get("timestamp", ""),
                    metadata=raw.get("metadata", {}),
                )
                self.add_entry(entry)
                loaded += 1
            except Exception as exc:
                logger.warning("[knowledge] skipping malformed entry: %s", exc)
        return loaded

    def load_all_packs(self) -> dict[str, int]:
        """Read path: load all knowledge packs from pack_dir into this aggregator."""
        if not self._pack_dir.exists():
            return {}
        loaded: dict[str, int] = {}
        for pack_path in sorted(self._pack_dir.glob("*.yaml")):
            ktype = pack_path.stem
            pack = self.load_pack(ktype)
            if pack:
                n = self.hydrate_from_pack(pack)
                loaded[ktype] = n
        return loaded

    def list_packs(self) -> list[dict[str, Any]]:
        """List all available knowledge packs without loading entries."""
        if not self._pack_dir.exists():
            return []
        packs = []
        for pack_path in sorted(self._pack_dir.glob("*.yaml")):
            try:
                with open(pack_path) as f:
                    data = yaml.safe_load(f) or {}
                packs.append(
                    {
                        "knowledge_type": data.get("knowledge_type", pack_path.stem),
                        "entry_count": data.get("entry_count", 0),
                        "avg_confidence": data.get("avg_confidence", 0.0),
                        "generated_at": data.get("generated_at", ""),
                        "pack_id": data.get("pack_id", ""),
                    }
                )
            except Exception:
                pass
        return packs

    # ------------------------------------------------------------------
    # End-of-run hook
    # ------------------------------------------------------------------

    def end_of_run(self) -> dict[str, Any]:
        """Call at the end of each pipeline run.

        Flushes all entries >= confidence threshold to knowledge packs
        and merges them into somatic chart values.yaml files.
        Returns the flush summary.
        """
        result = self.flush_to_charts()
        logger.info(
            "[knowledge] end_of_run: flushed=%d skipped=%d packs=%s",
            result["flushed"],
            result["skipped"],
            result["packs"],
        )
        return result

    # ------------------------------------------------------------------
    # Elasticsearch persistence (existing)
    # ------------------------------------------------------------------

    async def persist_to_elasticsearch(self) -> dict[str, Any]:
        """Persist aggregated knowledge to Elasticsearch."""
        if not self._es:
            return {"status": "no_elasticsearch"}
        aggregated = self.aggregate("policy")
        if aggregated.entries:
            from src.monkey_brain.persistence.events import PersistenceEvent, EventType

            event = PersistenceEvent(
                event_type=EventType.ENTITY_CREATED,
                entity_type="knowledge_aggregation",
                entity_id=aggregated.aggregation_id,
                data={
                    "knowledge_type": aggregated.knowledge_type,
                    "total_nodes": aggregated.total_nodes,
                    "avg_confidence": aggregated.avg_confidence,
                    "entry_count": len(aggregated.entries),
                },
            )
            return await self._es.persist(event)
        return {"status": "no_data"}

    def summary(self) -> dict:
        types = set()
        for key in self._entries:
            ktype = key.split(":")[0]
            types.add(ktype)
        return {
            "total_entries": sum(len(v) for v in self._entries.values()),
            "knowledge_types": list(types),
        }
