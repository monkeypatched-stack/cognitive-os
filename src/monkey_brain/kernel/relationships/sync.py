"""RelationshipSync — Bridges Affiliation system ↔ SharedWorld relationships.

Provides bidirectional synchronization between:
- Actor Affiliations (actor-owned relationships)
- World Relationships (SharedWorld entity-to-entity relationships)
- RelationshipGraph (unified query layer)

When an affiliation is added/updated/removed on an actor, the corresponding
world relationship is created/updated/removed, and vice versa.
"""

from __future__ import annotations

import logging
from . import RelationshipGraph, Relationship, RelationshipKind
from ..affiliations.affiliation import Affiliation
from ..affiliations.manager import AffiliationManager

logger = logging.getLogger("agentos.relationships.sync")


# Mapping from AffiliationType string to RelationshipKind
_AFFILIATION_TO_RELATIONSHIP: dict[str, RelationshipKind] = {
    # Personal
    "family": RelationshipKind.FAMILY,
    "friendship": RelationshipKind.FRIENDSHIP,
    "marriage": RelationshipKind.MARRIAGE,
    "guardianship": RelationshipKind.GUARDIANSHIP,
    "acquaintance": RelationshipKind.ACQUAINTANCE,
    "best_friend": RelationshipKind.BEST_FRIEND,
    "close_friend": RelationshipKind.CLOSE_FRIEND,
    "rival": RelationshipKind.RIVAL,
    "enemy": RelationshipKind.ENEMY,
    "neighbor": RelationshipKind.NEIGHBOR,
    "confidant": RelationshipKind.CONFIDANT,
    "soulmate": RelationshipKind.SOULMATE,
    # Organizational
    "employment": RelationshipKind.EMPLOYMENT,
    "contractor": RelationshipKind.CONTRACTOR,
    "freelancer": RelationshipKind.FREELANCER,
    "intern": RelationshipKind.INTERN,
    "volunteer": RelationshipKind.VOLUNTEER,
    "superior": RelationshipKind.SUPERIOR,
    "subordinate": RelationshipKind.SUBORDINATE,
    "colleague": RelationshipKind.COLLEAGUE,
    "peer": RelationshipKind.PEER,
    "mentor": RelationshipKind.MENTOR,
    "mentee": RelationshipKind.MENTEE,
    "manager": RelationshipKind.MANAGER,
    "direct_report": RelationshipKind.DIRECT_REPORT,
    "executive": RelationshipKind.EXECUTIVE,
    "board_member": RelationshipKind.BOARD_MEMBER,
    "advisor": RelationshipKind.ADVISOR,
    "consultant": RelationshipKind.CONSULTANT,
    "agent": RelationshipKind.AGENT,
    "broker": RelationshipKind.BROKER,
    # Commercial
    "customer": RelationshipKind.CUSTOMER,
    "supplier": RelationshipKind.SUPPLIER,
    "partner": RelationshipKind.PARTNER,
    "vendor": RelationshipKind.VENDOR,
    "distributor": RelationshipKind.DISTRIBUTOR,
    "retailer": RelationshipKind.RETAILER,
    "wholesaler": RelationshipKind.WHOLESALER,
    "reseller": RelationshipKind.RESELLER,
    "sponsor": RelationshipKind.SPONSOR,
    "affiliate": RelationshipKind.AFFILIATE,
    "referrer": RelationshipKind.REFERRER,
    "buyer": RelationshipKind.BUYER,
    "seller": RelationshipKind.SELLER,
    # Government
    "citizen": RelationshipKind.CITIZEN,
    "resident": RelationshipKind.RESIDENT,
    "taxpayer": RelationshipKind.TAXPAYER,
    "voter": RelationshipKind.VOTER,
    "regulator": RelationshipKind.REGULATOR,
    "regulated": RelationshipKind.REGULATED,
    "public_official": RelationshipKind.PUBLIC_OFFICIAL,
    "civil_servant": RelationshipKind.CIVIL_SERVANT,
    # Education
    "student": RelationshipKind.STUDENT,
    "teacher": RelationshipKind.TEACHER,
    "professor": RelationshipKind.PROFESSOR,
    "instructor": RelationshipKind.INSTRUCTOR,
    "tutor": RelationshipKind.TUTOR,
    "alumni": RelationshipKind.ALUMNI,
    "faculty": RelationshipKind.FACULTY_MEMBER,
    "researcher": RelationshipKind.CO_AUTHOR,
    "collaborator": RelationshipKind.COLLABORATED_WITH,
    # Healthcare
    "patient": RelationshipKind.PATIENT,
    "doctor": RelationshipKind.DOCTOR,
    "nurse": RelationshipKind.NURSE,
    "therapist": RelationshipKind.THERAPIST,
    "caregiver": RelationshipKind.CAREGIVER,
    "insured": RelationshipKind.INSURED,
    "insurer": RelationshipKind.INSURER,
    "donor": RelationshipKind.DONOR,
    "recipient": RelationshipKind.RECIPIENT,
    # Digital
    "ai_agent": RelationshipKind.AI_AGENT,
    "robot": RelationshipKind.ROBOT,
    "device": RelationshipKind.DEVICE,
    "service_account": RelationshipKind.SERVICE_ACCOUNT,
    "bot": RelationshipKind.BOT,
    "chatbot": RelationshipKind.CHATBOT,
    "virtual_assistant": RelationshipKind.VIRTUAL_ASSISTANT,
    # Trust
    "trusted": RelationshipKind.TRUSTED,
    "untrusted": RelationshipKind.UNTRUSTED,
    # Structural (Affiliation Graph vocabulary — kernel/affiliations/types.py)
    "member_of": RelationshipKind.MEMBER_OF,
    "affiliated_with": RelationshipKind.AFFILIATE,
    "belongs_to": RelationshipKind.CONTAINED_IN,
    "employed_by": RelationshipKind.EMPLOYMENT,
    "represents": RelationshipKind.AGENT,
    "manages": RelationshipKind.MANAGER,
    # Knowledge
    "depends_on": RelationshipKind.DEPENDS_ON,
    "caused_by": RelationshipKind.CAUSED_BY,
    "implies": RelationshipKind.IMPLIES,
    "contradicts": RelationshipKind.CONTRADICTS,
    "supports": RelationshipKind.SUPPORTS,
    "opposes": RelationshipKind.OPPOSES,
    "references": RelationshipKind.REFERENCES,
    "derived_from": RelationshipKind.DERIVED_FROM,
    "part_of": RelationshipKind.PART_OF,
    "instance_of": RelationshipKind.INSTANCE_OF,
    "example_of": RelationshipKind.EXAMPLE_OF,
    # Financial
    "owns": RelationshipKind.OWNS,
    "leases": RelationshipKind.LEASES,
    "invests_in": RelationshipKind.INVESTS_IN,
    "lends_to": RelationshipKind.LENDS_TO,
    "pays": RelationshipKind.PAYS,
    "donates_to": RelationshipKind.DONATES_TO,
    "sponsors": RelationshipKind.SPONSORS,
    "funding": RelationshipKind.FUNDING,
    # Legal
    "governs": RelationshipKind.GOVERNS_LEGAL,
    "enforces": RelationshipKind.ENFORCES,
    "regulates": RelationshipKind.REGULATES,
    "protects": RelationshipKind.PROTECTS,
    "authorizes": RelationshipKind.AUTHORIZES_LEGAL,
    "prohibits": RelationshipKind.PROHIBITS,
    "permits": RelationshipKind.PERMITS,
    "requires": RelationshipKind.REQUIRES,
    "certifies": RelationshipKind.CERTIFIES,
    "licenses": RelationshipKind.LICENSES,
}

_RELATIONSHIP_TO_AFFILIATION: dict[RelationshipKind, str] = {v: k for k, v in _AFFILIATION_TO_RELATIONSHIP.items()}


class RelationshipSync:
    """Synchronizes relationships between Affiliation system and RelationshipGraph.

    Responsibilities:
        - Convert Affiliations to Relationships and vice versa
        - Sync changes bidirectionally
        - Resolve conflicts between layers
        - Provide unified query interface
    """

    def __init__(self, graph: RelationshipGraph | None = None):
        self.graph = graph or RelationshipGraph()
        self._sync_enabled = True

    # ── Affiliation → Relationship ──────────────────────────────

    def affiliation_to_relationship(self, affiliation: Affiliation, source_actor_id: str = "") -> Relationship:
        """Convert an Affiliation to a Relationship."""
        kind = _AFFILIATION_TO_RELATIONSHIP.get(
            affiliation.affiliation_type,
            RelationshipKind.RELATED_TO,
        )

        return Relationship(
            source_id=source_actor_id or "self",
            target_id=affiliation.target_id,
            kind=kind,
            strength=affiliation.trust_level,
            confidence=1.0,
            bidirectional=(affiliation.type_info.bidirectional if affiliation.type_info else True),
            attributes={
                "permissions": list(affiliation.permissions),
                "policies": list(affiliation.policies),
            },
            metadata={
                "affiliation_id": affiliation.affiliation_id,
                "affiliation_type": affiliation.affiliation_type,
                "target_name": affiliation.target_name,
                "priority": affiliation.priority,
            },
            source_actor_id=source_actor_id,
        )

    def sync_affiliation(self, affiliation: Affiliation, source_actor_id: str) -> Relationship:
        """Sync an affiliation to the relationship graph."""
        if not self._sync_enabled:
            return None

        # Check if relationship already exists
        existing = self._find_existing(affiliation.target_id, source_actor_id, affiliation.affiliation_type)

        if existing:
            # Update existing
            rel = self.graph.update(
                existing.relationship_id,
                strength=affiliation.trust_level,
                attributes={
                    "permissions": list(affiliation.permissions),
                    "policies": list(affiliation.policies),
                },
                metadata={
                    "affiliation_id": affiliation.affiliation_id,
                    "affiliation_type": affiliation.affiliation_type,
                    "target_name": affiliation.target_name,
                },
            )
        else:
            # Create new
            rel = self.affiliation_to_relationship(affiliation, source_actor_id)
            self.graph.add(
                source_id=rel.source_id,
                target_id=rel.target_id,
                kind=rel.kind,
                strength=rel.strength,
                bidirectional=rel.bidirectional,
                attributes=rel.attributes,
                metadata=rel.metadata,
                source_actor_id=rel.source_actor_id,
            )

        return rel

    def sync_affiliation_manager(self, manager: AffiliationManager, source_actor_id: str) -> int:
        """Sync all affiliations from a manager to the graph."""
        count = 0
        for affiliation in manager.all():
            if self.sync_affiliation(affiliation, source_actor_id):
                count += 1
        return count

    # ── Relationship → Affiliation ──────────────────────────────

    def relationship_to_affiliation(self, rel: Relationship) -> Affiliation:
        """Convert a Relationship to an Affiliation."""
        aff_type = _RELATIONSHIP_TO_AFFILIATION.get(rel.kind, "related_to")

        return Affiliation(
            affiliation_id=f"rel:{rel.relationship_id}",
            affiliation_type=aff_type,
            target_id=rel.target_id,
            target_name=rel.metadata.get("target_name", rel.target_id),
            trust_level=rel.strength,
            permissions=tuple(rel.attributes.get("permissions", [])),
            policies=tuple(rel.attributes.get("policies", [])),
            priority=rel.metadata.get("priority", 0),
        )

    def sync_to_affiliation_manager(self, manager: AffiliationManager, source_actor_id: str) -> int:
        """Sync relationships to an affiliation manager."""
        if not self._sync_enabled:
            return 0

        rels = self.graph.relationships_for(source_actor_id)
        count = 0

        for rel in rels:
            if rel.source_id == source_actor_id:
                aff = self.relationship_to_affiliation(rel)
                existing = manager.get(aff.affiliation_id)
                if existing:
                    manager.update(aff.affiliation_id, trust_level=rel.strength)
                else:
                    manager.add(aff)
                count += 1

        return count

    # ── Conflict Resolution ─────────────────────────────────────

    def resolve_conflict(
        self,
        affiliation: Affiliation,
        relationship: Relationship,
        strategy: str = "strongest",
    ) -> Relationship:
        """Resolve conflicts between affiliation and relationship views.

        Strategies:
            strongest: Keep the one with higher strength/trust
            latest: Keep the one with more recent timestamp
            affiliation: Affiliation wins
            relationship: Relationship wins
        """
        if strategy == "strongest":
            if affiliation.trust_level >= relationship.strength:
                return self.affiliation_to_relationship(affiliation)
            else:
                return relationship
        elif strategy == "latest":
            aff_time = affiliation.valid_from or "0"
            rel_time = str(relationship.updated_at)
            if aff_time >= rel_time:
                return self.affiliation_to_relationship(affiliation)
            else:
                return relationship
        elif strategy == "affiliation":
            return self.affiliation_to_relationship(affiliation)
        elif strategy == "relationship":
            return relationship
        else:
            return relationship

    # ── Helper ──────────────────────────────────────────────────

    def _find_existing(self, target_id: str, source_id: str, aff_type: str) -> Relationship | None:
        """Find existing relationship for an affiliation."""
        kind = _AFFILIATION_TO_RELATIONSHIP.get(aff_type)
        if kind is None:
            return None

        for rel in self.graph.relationships_between(source_id, target_id, kind):
            return rel
        return None

    def enable_sync(self):
        """Enable synchronization."""
        self._sync_enabled = True

    def disable_sync(self):
        """Disable synchronization."""
        self._sync_enabled = False
