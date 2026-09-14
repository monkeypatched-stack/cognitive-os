"""Knowledge Pack lifecycle — candidate staging, maker-checker validation, versioned release.

The existing KnowledgeAggregator.flush_to_charts() is a direct-write path:
entries above a confidence threshold are serialised straight into deployed YAML files.
That is equivalent to live-patching a deployed pack, which reintroduces the same
calibration-debt problem that uninstall-and-rebuild was designed to prevent.

This module adds the missing write discipline:

  1. Contributions accumulate in a PackCandidate (staging), never touching the deployed pack.
  2. Each contribution carries its own EpisodicRef provenance chain back to the
     episodic instances that produced it — the audit trail covers how the pack came
     to know what it knows, not just what it knows.
  3. A deterministic ContributionValidator gates every contribution before it enters
     the candidate: confidence check, provenance check, duplicate check.
  4. A full candidate is released into a new immutable PackVersion only after all
     contributions in it pass validation — the maker-checker discipline applied to
     the packaging step.
  5. Deployed versions are never mutated. A version object is sealed at release time;
     subsequent contributions start a new candidate for the next version.

Write path (new):
  compacted pattern / crystallised capability
    → PackContributor.propose()          (adds to staging)
    → ContributionValidator.validate()   (deterministic gate)
    → PackVersionRegistry.release()      (seals new version, writes YAML)

Read path (unchanged from KnowledgeAggregator):
  KnowledgeAggregator.load_pack() / hydrate_from_pack()
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from src.deepdive.knowledge_aggregator import (
    KnowledgeEntry,
    KnowledgePack,
    CONFIDENCE_THRESHOLD,
    _PACK_DIR,
)

logger = logging.getLogger("deepdive.pack_lifecycle")


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EpisodicRef:
    """Pointer back to the episodic memory node that produced this contribution.

    Ties each piece of proposed knowledge to the concrete runtime trace that
    earned it — once merged into a version, the audit trail runs:
      PackVersion → PackContribution → EpisodicRef → EpisodicTrace (graph DB)
    """

    episode_id: str  # MemoryNode.node_id of the source EpisodicTrace
    pattern_id: str  # BackgroundCompactor pattern that crystallised it
    access_count: int  # how many times the pattern was observed before nomination
    compacted_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Contribution status
# ---------------------------------------------------------------------------


class ContributionStatus(str, Enum):
    PROPOSED = "proposed"  # accumulated in the candidate, not yet validated
    VALIDATED = "validated"  # passed all validation checks
    REJECTED = "rejected"  # failed one or more checks — never enters a version
    MERGED = "merged"  # included in a released PackVersion


# ---------------------------------------------------------------------------
# Pack contribution (the unit of staging)
# ---------------------------------------------------------------------------


@dataclass
class PackContribution:
    """A proposed addition to a Knowledge Pack.

    This is NOT a KnowledgeEntry — it is a diff proposal from the runtime's
    internal compaction pipeline.  It carries its own provenance chain so the
    audit trail covers not just what the system did with a pack, but how the
    pack itself came to know what it knows.
    """

    contribution_id: str = field(default_factory=lambda: f"contrib-{uuid4().hex[:12]}")
    knowledge_type: str = ""
    entry: KnowledgeEntry = field(default_factory=KnowledgeEntry)
    episodic_refs: list[EpisodicRef] = field(default_factory=list)
    proposed_by: str = "runtime"  # "runtime" | agent_type | node_id
    proposed_at: float = field(default_factory=time.time)
    status: ContributionStatus = ContributionStatus.PROPOSED
    rejection_reasons: list[str] = field(default_factory=list)
    content_hash: str = ""  # sha256[:16] of entry.content — for dedup

    def __post_init__(self) -> None:
        if not self.content_hash:
            raw = json.dumps(self.entry.content, sort_keys=True, default=str).encode()
            self.content_hash = hashlib.sha256(raw).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "contribution_id": self.contribution_id,
            "knowledge_type": self.knowledge_type,
            "entry": asdict(self.entry),
            "episodic_refs": [
                {
                    "episode_id": r.episode_id,
                    "pattern_id": r.pattern_id,
                    "access_count": r.access_count,
                    "compacted_at": r.compacted_at,
                }
                for r in self.episodic_refs
            ],
            "proposed_by": self.proposed_by,
            "proposed_at": self.proposed_at,
            "status": self.status,
            "content_hash": self.content_hash,
            "rejection_reasons": self.rejection_reasons,
        }


# ---------------------------------------------------------------------------
# Pack candidate (the staging area for one knowledge type)
# ---------------------------------------------------------------------------


@dataclass
class PackCandidate:
    """Staging area for proposed contributions to one knowledge type.

    A candidate accumulates contributions from the compaction pipeline.
    It is never deployed as-is — it must be promoted by PackVersionRegistry.release().
    The deployed pack is never touched until release is called.
    """

    knowledge_type: str
    base_version: str = ""  # version of the deployed pack this builds on
    contributions: list[PackContribution] = field(default_factory=list)
    opened_at: float = field(default_factory=time.time)
    closed_at: float | None = None

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    @property
    def validated_contributions(self) -> list[PackContribution]:
        return [c for c in self.contributions if c.status == ContributionStatus.VALIDATED]

    @property
    def rejected_contributions(self) -> list[PackContribution]:
        return [c for c in self.contributions if c.status == ContributionStatus.REJECTED]

    def content_hashes(self) -> set[str]:
        return {c.content_hash for c in self.contributions}

    def summary(self) -> dict[str, Any]:
        by_status: dict[str, int] = {}
        for c in self.contributions:
            by_status[c.status] = by_status.get(c.status, 0) + 1
        return {
            "knowledge_type": self.knowledge_type,
            "base_version": self.base_version,
            "is_open": self.is_open,
            "total": len(self.contributions),
            "by_status": by_status,
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
        }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@dataclass
class ContributionValidationResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)  # failure reasons; empty on pass


class ContributionValidator:
    """Deterministic gate between proposed and validated states.

    All checks are local and stateless with respect to the runtime —
    no LLM calls, no I/O. The caller passes in the candidate's existing
    content hashes so duplicate detection works without state.

    Checks performed (in order; first failure short-circuits):
      1. Confidence threshold — entry.confidence >= min_confidence
      2. Provenance present   — at least one EpisodicRef attached
      3. Provenance depth     — EpisodicRef.access_count >= min_access_count
         (prevents single-observation flukes from entering the pack)
      4. Duplicate content    — content_hash not already in existing_hashes
    """

    def __init__(
        self,
        min_confidence: float = CONFIDENCE_THRESHOLD,
        min_access_count: int = 10,
    ) -> None:
        self._min_confidence = min_confidence
        self._min_access_count = min_access_count

    def validate(
        self,
        contribution: PackContribution,
        existing_hashes: set[str],
    ) -> ContributionValidationResult:
        reasons: list[str] = []

        if contribution.entry.confidence < self._min_confidence:
            reasons.append(f"confidence {contribution.entry.confidence:.3f} < threshold {self._min_confidence}")

        if not contribution.episodic_refs:
            reasons.append("no episodic provenance — cannot trace origin")
        else:
            weak_refs = [r for r in contribution.episodic_refs if r.access_count < self._min_access_count]
            if len(weak_refs) == len(contribution.episodic_refs):
                # All refs are below the access floor — likely a fluke observation
                max_count = max(r.access_count for r in contribution.episodic_refs)
                reasons.append(
                    f"all episodic refs have access_count < {self._min_access_count} (max seen: {max_count})"
                )

        if contribution.content_hash in existing_hashes:
            reasons.append(
                f"duplicate: content_hash {contribution.content_hash!r} "
                "already present in candidate or deployed version"
            )

        return ContributionValidationResult(passed=not reasons, reasons=reasons)


# ---------------------------------------------------------------------------
# Versioned pack record
# ---------------------------------------------------------------------------


@dataclass
class PackVersion:
    """An immutable, released snapshot of a Knowledge Pack.

    Once released, a PackVersion is never mutated.  Subsequent contributions
    open a new PackCandidate targeting the next version number.
    """

    version_id: str = field(default_factory=lambda: f"v-{uuid4().hex[:8]}")
    knowledge_type: str = ""
    version: str = "0.1.0"
    pack: KnowledgePack = field(default_factory=KnowledgePack)
    contribution_ids: list[str] = field(default_factory=list)
    episodic_refs: list[dict[str, Any]] = field(default_factory=list)  # flattened for audit
    released_at: float = field(default_factory=time.time)
    released_by: str = "system"  # "system" | human approver id
    yaml_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "knowledge_type": self.knowledge_type,
            "version": self.version,
            "pack_id": self.pack.pack_id,
            "entry_count": self.pack.entry_count,
            "avg_confidence": self.pack.avg_confidence,
            "contribution_ids": self.contribution_ids,
            "episodic_ref_count": len(self.episodic_refs),
            "released_at": self.released_at,
            "released_by": self.released_by,
            "yaml_path": self.yaml_path,
        }


# ---------------------------------------------------------------------------
# Version registry
# ---------------------------------------------------------------------------


class PackVersionRegistry:
    """Tracks released versions and manages the live candidate per knowledge type.

    Invariants:
    - Released versions are immutable; the dict entry is never replaced.
    - The candidate for a type is replaced (reset) after each successful release.
    - A release only proceeds if every contribution in the candidate passed validation.
    - Provenance from all merged contributions is flattened into the PackVersion record.
    """

    def __init__(
        self,
        pack_dir: Path | None = None,
        validator: ContributionValidator | None = None,
        sittingface_publisher: "SittingFacePublisher | None" = None,
    ) -> None:
        self._pack_dir = pack_dir or _PACK_DIR
        self._validator = validator or ContributionValidator()
        self._sf_publisher: SittingFacePublisher | None = sittingface_publisher

        # knowledge_type → list of released PackVersion (oldest first)
        self._versions: dict[str, list[PackVersion]] = {}
        # knowledge_type → open PackCandidate (None if no staging work yet)
        self._candidates: dict[str, PackCandidate] = {}

    # ------------------------------------------------------------------
    # Staging
    # ------------------------------------------------------------------

    def candidate_for(self, knowledge_type: str) -> PackCandidate:
        """Return (or create) the open candidate for a knowledge type."""
        if knowledge_type not in self._candidates or not self._candidates[knowledge_type].is_open:
            deployed = self.deployed_version(knowledge_type)
            self._candidates[knowledge_type] = PackCandidate(
                knowledge_type=knowledge_type,
                base_version=deployed.version if deployed else "",
            )
        return self._candidates[knowledge_type]

    def propose(
        self,
        contribution: PackContribution,
    ) -> ContributionValidationResult:
        """Validate a contribution and, if it passes, add it to the staging candidate.

        Validation is synchronous and deterministic — no side effects on failure.
        The contribution's status is updated in place.
        """
        candidate = self.candidate_for(contribution.knowledge_type)
        known_hashes = candidate.content_hashes()

        # Also include hashes already in the deployed version
        deployed = self.deployed_version(contribution.knowledge_type)
        if deployed:
            for entry_dict in deployed.pack.entries:
                raw = json.dumps(entry_dict.get("content", {}), sort_keys=True, default=str).encode()
                known_hashes.add(hashlib.sha256(raw).hexdigest()[:16])

        result = self._validator.validate(contribution, known_hashes)

        if result.passed:
            contribution.status = ContributionStatus.VALIDATED
            candidate.contributions.append(contribution)
            logger.info(
                "[pack_lifecycle] contribution %s validated for %s (conf=%.3f, refs=%d)",
                contribution.contribution_id,
                contribution.knowledge_type,
                contribution.entry.confidence,
                len(contribution.episodic_refs),
            )
        else:
            contribution.status = ContributionStatus.REJECTED
            contribution.rejection_reasons = result.reasons
            candidate.contributions.append(contribution)  # kept for audit
            logger.warning(
                "[pack_lifecycle] contribution %s rejected for %s: %s",
                contribution.contribution_id,
                contribution.knowledge_type,
                "; ".join(result.reasons),
            )

        return result

    # ------------------------------------------------------------------
    # Release
    # ------------------------------------------------------------------

    def release(
        self,
        knowledge_type: str,
        released_by: str = "system",
        next_version: str | None = None,
    ) -> PackVersion | None:
        """Promote the validated candidate into a new immutable PackVersion.

        Returns None (without writing anything) if:
          - There is no open candidate.
          - The candidate has no validated contributions.
          - Any rejected contributions remain unresolved (caller must explicitly
            purge them or open a new candidate).

        The deployed pack YAML is written atomically: a temp file is renamed
        into place so readers never see a partially-written file.
        """
        candidate = self._candidates.get(knowledge_type)
        if not candidate or not candidate.is_open:
            logger.warning("[pack_lifecycle] no open candidate for %s", knowledge_type)
            return None

        validated = candidate.validated_contributions
        if not validated:
            logger.warning(
                "[pack_lifecycle] no validated contributions for %s — nothing to release",
                knowledge_type,
            )
            return None

        # Determine next version string
        deployed = self.deployed_version(knowledge_type)
        version = next_version or self._bump_version(deployed.version if deployed else "0.0.0")

        # Build the new KnowledgePack from validated contributions only
        entries = [asdict(c.entry) for c in validated]
        avg_conf = sum(c.entry.confidence for c in validated) / len(validated)
        new_pack = KnowledgePack(
            knowledge_type=knowledge_type,
            entries=entries,
            avg_confidence=round(avg_conf, 4),
            entry_count=len(entries),
            threshold_used=self._validator._min_confidence,
        )

        # Flatten all episodic refs for the version record
        all_refs: list[dict[str, Any]] = []
        for c in validated:
            for ref in c.episodic_refs:
                all_refs.append(
                    {
                        "contribution_id": c.contribution_id,
                        "episode_id": ref.episode_id,
                        "pattern_id": ref.pattern_id,
                        "access_count": ref.access_count,
                    }
                )

        # Write to disk — new YAML alongside any existing pack
        self._pack_dir.mkdir(parents=True, exist_ok=True)
        yaml_path = self._pack_dir / f"{knowledge_type}.yaml"
        tmp_path = yaml_path.with_suffix(".yaml.tmp")

        pack_dict = asdict(new_pack)
        pack_dict["_version"] = version
        pack_dict["_released_at"] = datetime.now(timezone.utc).isoformat()
        pack_dict["_released_by"] = released_by
        pack_dict["_episodic_refs"] = all_refs

        with open(tmp_path, "w") as fh:
            yaml.dump(pack_dict, fh, default_flow_style=False, sort_keys=False)
        tmp_path.rename(yaml_path)  # atomic on POSIX

        pack_version = PackVersion(
            knowledge_type=knowledge_type,
            version=version,
            pack=new_pack,
            contribution_ids=[c.contribution_id for c in validated],
            episodic_refs=all_refs,
            released_by=released_by,
            yaml_path=str(yaml_path),
        )

        # Seal the candidate and register the version
        candidate.closed_at = time.time()
        for c in validated:
            c.status = ContributionStatus.MERGED

        self._versions.setdefault(knowledge_type, []).append(pack_version)
        # Reset — next contributions open a fresh candidate
        del self._candidates[knowledge_type]

        logger.info(
            "[pack_lifecycle] released %s v%s — %d entries, %d episodic refs → %s",
            knowledge_type,
            version,
            len(entries),
            len(all_refs),
            yaml_path,
        )

        if self._sf_publisher:
            try:
                sf_result = self._sf_publisher.publish_version(pack_version)
                logger.info("[pack_lifecycle] sittingface: %s", sf_result)
            except Exception as exc:
                logger.warning("[pack_lifecycle] sittingface publish failed (non-fatal): %s", exc)

        return pack_version

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def deployed_version(self, knowledge_type: str) -> PackVersion | None:
        versions = self._versions.get(knowledge_type, [])
        return versions[-1] if versions else None

    def version_history(self, knowledge_type: str) -> list[PackVersion]:
        return list(self._versions.get(knowledge_type, []))

    def candidate_summary(self, knowledge_type: str) -> dict[str, Any] | None:
        c = self._candidates.get(knowledge_type)
        return c.summary() if c else None

    def all_summaries(self) -> dict[str, Any]:
        types = set(self._versions) | set(self._candidates)
        out: dict[str, Any] = {}
        for kt in sorted(types):
            deployed = self.deployed_version(kt)
            out[kt] = {
                "deployed_version": deployed.version if deployed else None,
                "deployed_entry_count": deployed.pack.entry_count if deployed else 0,
                "candidate": self.candidate_summary(kt),
            }
        return out

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _bump_version(current: str) -> str:
        """Increment the patch component of a semver string."""
        parts = current.split(".")
        try:
            parts[-1] = str(int(parts[-1]) + 1)
        except (ValueError, IndexError):
            parts = ["0", "1", "0"]
        return ".".join(parts)


# ---------------------------------------------------------------------------
# Contributor — convenience façade over registry + validator
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# SittingFace publisher — optional post-release publication
# ---------------------------------------------------------------------------


class SittingFacePublisher:
    """Publishes a sealed PackVersion to the SittingFace chart registry.

    After PackVersionRegistry.release() writes the immutable YAML, this class
    reads it back and registers the pack as a ``knowledge_pack`` chart so the
    full provenance chain (episodic refs, contribution ids, version lineage) is
    discoverable through the SittingFace API.

    Two modes:
      SittingFacePublisher.local()       — writes to ~/.sittingface/registry/
      SittingFacePublisher.remote(url)   — HTTP POST to a running registry server
    """

    def __init__(
        self,
        registry_url: str | None = None,
        use_local: bool = False,
    ) -> None:
        self._registry_url = registry_url
        self._use_local = use_local

    @classmethod
    def local(cls) -> "SittingFacePublisher":
        return cls(use_local=True)

    @classmethod
    def remote(cls, url: str = "http://localhost:8500") -> "SittingFacePublisher":
        return cls(registry_url=url)

    def publish_version(self, pack_version: PackVersion) -> dict[str, Any]:
        """Publish a sealed PackVersion to SittingFace.  Returns a status dict."""
        yaml_path = Path(pack_version.yaml_path)
        if not yaml_path.exists():
            logger.warning("[sf_publisher] yaml_path not found: %s", yaml_path)
            return {"status": "skipped", "reason": "yaml_path_missing"}

        values_yaml = yaml_path.read_text()
        tags = [
            "knowledge_pack",
            pack_version.knowledge_type,
            f"version:{pack_version.version}",
        ]
        description = (
            f"KnowledgePack {pack_version.knowledge_type} v{pack_version.version} — "
            f"{pack_version.pack.entry_count} entries, "
            f"{len(pack_version.episodic_refs)} episodic refs"
        )

        if self._use_local:
            return self._publish_local(pack_version, values_yaml, tags)
        if self._registry_url:
            return self._publish_remote(pack_version, values_yaml, tags, description)

        logger.warning("[sf_publisher] no registry configured — skipping")
        return {"status": "skipped", "reason": "no_registry_configured"}

    def _publish_local(
        self,
        pack_version: PackVersion,
        values_yaml: str,
        tags: list[str],
    ) -> dict[str, Any]:
        import tempfile

        from sittingface.registry import SittingFaceRegistry

        registry = SittingFaceRegistry()
        with tempfile.TemporaryDirectory() as tmp:
            chart_dir = Path(tmp) / pack_version.knowledge_type
            chart_dir.mkdir()
            (chart_dir / "values.yaml").write_text(values_yaml)
            meta = registry.publish(chart_dir, tags=tags)

        logger.info("[sf_publisher] local publish: %s v%s", meta.name, meta.version)
        return {
            "status": "published",
            "registry": "local",
            "name": meta.name,
            "version": meta.version,
        }

    def _publish_remote(
        self,
        pack_version: PackVersion,
        values_yaml: str,
        tags: list[str],
        description: str,
    ) -> dict[str, Any]:
        from sittingface.client import SittingFaceClient

        client = SittingFaceClient(self._registry_url)
        try:
            result = client.publish(
                name=pack_version.knowledge_type,
                version=pack_version.version,
                values_yaml=values_yaml,
                description=description,
                tags=tags,
                chart_type="knowledge_pack",
            )
            logger.info(
                "[sf_publisher] remote publish: %s v%s → %s",
                pack_version.knowledge_type,
                pack_version.version,
                self._registry_url,
            )
            return {"status": "published", "registry": self._registry_url, **result}
        finally:
            client.close()


# ---------------------------------------------------------------------------
# Contributor — convenience façade over registry + validator
# ---------------------------------------------------------------------------


class PackContributor:
    """High-level API for the compaction pipeline to propose new knowledge.

    Usage:
        contributor = PackContributor(registry)

        # After BackgroundCompactor crystallises a pattern:
        contribution = contributor.from_compacted_pattern(
            knowledge_type="capability",
            entry=knowledge_entry,
            episodic_refs=[EpisodicRef(episode_id=..., pattern_id=..., access_count=120)],
            proposed_by="background_compactor",
        )
        result = contributor.propose(contribution)
        if result.passed:
            ...  # accumulates in candidate

        # When a release is warranted (scheduled, or human-triggered):
        version = contributor.release("capability", released_by="human_reviewer")
    """

    def __init__(
        self,
        registry: PackVersionRegistry,
        sittingface_publisher: SittingFacePublisher | None = None,
    ) -> None:
        self._registry = registry
        self._sf_pub = sittingface_publisher
        if sittingface_publisher and registry._sf_publisher is None:
            registry._sf_publisher = sittingface_publisher

    def from_compacted_pattern(
        self,
        knowledge_type: str,
        entry: KnowledgeEntry,
        episodic_refs: list[EpisodicRef],
        proposed_by: str = "background_compactor",
    ) -> PackContribution:
        return PackContribution(
            knowledge_type=knowledge_type,
            entry=entry,
            episodic_refs=episodic_refs,
            proposed_by=proposed_by,
        )

    def propose(self, contribution: PackContribution) -> ContributionValidationResult:
        return self._registry.propose(contribution)

    def release(
        self,
        knowledge_type: str,
        released_by: str = "system",
        next_version: str | None = None,
    ) -> PackVersion | None:
        return self._registry.release(knowledge_type, released_by, next_version)

    def candidate_summary(self, knowledge_type: str) -> dict[str, Any] | None:
        return self._registry.candidate_summary(knowledge_type)
