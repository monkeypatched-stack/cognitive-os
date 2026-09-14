"""Relationships System — Unified, graph-based relationship management.

Provides a single abstraction over all relationship types:
- Actor-to-Actor (via Affiliations)
- Entity-to-Entity (via SharedWorld)
- Cross-layer queries
- Bidirectional traversal
- Transitive inference
- Relationship history and audit trail
- Conflict resolution

Architecture:
    RelationshipGraph — stores all relationships as a directed graph
    RelationshipStore — persistence and query interface
    RelationshipSync — bridges Affiliation ↔ WorldRelationship
    RelationshipHistory — audit trail for all changes

Usage:
    from src.monkey_brain.kernel.relationships import RelationshipGraph

    graph = RelationshipGraph()
    graph.add("alice", "bob", "friendship", strength=0.8)
    graph.add("bob", "charlie", "colleague", strength=0.6)

    # Find all relationships for alice
    graph.relationships_for("alice")

    # Find path between alice and charlie
    graph.find_path("alice", "charlie")

    # Get transitive relationships
    graph.transitive("alice", "colleague")
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

logger = logging.getLogger("agentos.relationships")


# ═══════════════════════════════════════════════════════════════════════════════
# Core Types
# ═══════════════════════════════════════════════════════════════════════════════


class RelationshipKind(str, Enum):
    """Types of relationships between entities.

    Comprehensive taxonomy covering:
    - Personal relationships
    - Family relationships
    - Organizational relationships
    - Commercial relationships
    - Government relationships
    - Education relationships
    - Healthcare relationships
    - Digital/AI relationships
    - Knowledge relationships
    - Spatial relationships
    - Temporal relationships
    - Causal relationships
    - Financial relationships
    - Legal relationships
    - Social relationships
    - Technical relationships
    - Creative relationships
    - Scientific relationships
    """

    # ═══════════════════════════════════════════════════════════════════════════
    # Personal Relationships
    # ═══════════════════════════════════════════════════════════════════════════
    FRIENDSHIP = "friendship"
    ACQUAINTANCE = "acquaintance"
    BEST_FRIEND = "best_friend"
    CLOSE_FRIEND = "close_friend"
    CASUAL_FRIEND = "casual_friend"
    RIVAL = "rival"
    ENEMY = "enemy"
    NEIGHBOR = "neighbor"
    ROOMMATE = "roommate"
    CONFIDANT = "confidant"
    SOULMATE = "soulmate"

    # ═══════════════════════════════════════════════════════════════════════════
    # Family Relationships
    # ═══════════════════════════════════════════════════════════════════════════
    FAMILY = "family"
    PARENT = "parent"
    CHILD = "child"
    SIBLING = "sibling"
    SPOUSE = "spouse"
    MARRIAGE = "marriage"
    DIVORCE = "divorce"
    ENGAGEMENT = "engagement"
    DATING = "dating"
    GUARDIANSHIP = "guardianship"
    WARD = "ward"
    ADOPTION = "adoption"
    FOSTER = "foster"
    GRANDPARENT = "grandparent"
    GRANDCHILD = "grandchild"
    AUNT_UNCLE = "aunt_uncle"
    NEPHEW_NIECE = "nephew_niece"
    COUSIN = "cousin"
    IN_LAW = "in_law"
    STEP_PARENT = "step_parent"
    STEP_CHILD = "step_child"
    STEP_SIBLING = "step_sibling"
    GODPARENT = "godparent"
    GODCHILD = "godchild"

    # ═══════════════════════════════════════════════════════════════════════════
    # Organizational Relationships
    # ═══════════════════════════════════════════════════════════════════════════
    EMPLOYMENT = "employment"
    CONTRACTOR = "contractor"
    FREELANCER = "freelancer"
    INTERN = "intern"
    VOLUNTEER = "volunteer"
    SUPERIOR = "superior"
    SUBORDINATE = "subordinate"
    COLLEAGUE = "colleague"
    PEER = "peer"
    MENTOR = "mentor"
    MENTEE = "mentee"
    MANAGER = "manager"
    DIRECT_REPORT = "direct_report"
    EXECUTIVE = "executive"
    BOARD_MEMBER = "board_member"
    ADVISOR = "advisor"
    CONSULTANT = "consultant"
    AGENT = "agent"
    BROKER = "broker"
    FRANCHISE = "franchise"
    FRANCHISOR = "franchisor"
    FRANCHISEE = "franchisee"

    # ═══════════════════════════════════════════════════════════════════════════
    # Commercial Relationships
    # ═══════════════════════════════════════════════════════════════════════════
    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    VENDOR = "vendor"
    PARTNER = "partner"
    DISTRIBUTOR = "distributor"
    RETAILER = "retailer"
    WHOLESALER = "wholesaler"
    RESELLER = "reseller"
    SPONSOR = "sponsor"
    SPONSORED = "sponsored"
    AFFILIATE = "affiliate"
    REFERRER = "referrer"
    REFERRED = "referred"
    BUYER = "buyer"
    SELLER = "seller"
    LESSOR = "lessor"
    LESSEE = "lessee"

    # ═══════════════════════════════════════════════════════════════════════════
    # Government Relationships
    # ═══════════════════════════════════════════════════════════════════════════
    CITIZEN = "citizen"
    RESIDENT = "resident"
    TAXPAYER = "taxpayer"
    VOTER = "voter"
    REGULATOR = "regulator"
    REGULATED = "regulated"
    PUBLIC_OFFICIAL = "public_official"
    ELECTED_OFFICIAL = "elected_official"
    APPOINTED_OFFICIAL = "appointed_official"
    CIVIL_SERVANT = "civil_servant"
    DIPLOMAT = "diplomat"
    AMBASSADOR = "ambassador"
    CONSUL = "consul"
    JURISDICTION = "jurisdiction"
    GOVERNED_BY = "governed_by"
    LICENSED_BY_GOV = "licensed_by_gov"
    PERMITTED_BY_GOV = "permitted_by_gov"

    # ═══════════════════════════════════════════════════════════════════════════
    # Education Relationships
    # ═══════════════════════════════════════════════════════════════════════════
    STUDENT = "student"
    TEACHER = "teacher"
    PROFESSOR = "professor"
    INSTRUCTOR = "instructor"
    TUTOR = "tutor"
    DEPARTMENT_HEAD = "department_head"
    DEAN = "dean"
    PROVOST = "provost"
    CHANCELLOR = "chancellor"
    ALUMNI = "alumni"
    FACULTY_MEMBER = "faculty_member"
    LAB_ASSISTANT = "lab_assistant"
    TEACHING_ASSISTANT = "teaching_assistant"
    CLASSMATE = "classmate"
    SCHOOLMATE = "schoolmate"
    THESIS_ADVISOR = "thesis_advisor"
    POSTDOC = "postdoc"
    CO_AUTHOR = "co_author"

    # ═══════════════════════════════════════════════════════════════════════════
    # Healthcare Relationships
    # ═══════════════════════════════════════════════════════════════════════════
    PATIENT = "patient"
    DOCTOR = "doctor"
    NURSE = "nurse"
    SPECIALIST = "specialist"
    SURGEON = "surgeon"
    THERAPIST = "therapist"
    PSYCHOLOGIST = "psychologist"
    PSYCHIATRIST = "psychiatrist"
    PHARMACIST = "pharmacist"
    CAREGIVER = "caregiver"
    HOME_HEALTH_AIDE = "home_health_aide"
    PHYSICAL_THERAPIST = "physical_therapist"
    OCCUPATIONAL_THERAPIST = "occupational_therapist"
    SPEECH_THERAPIST = "speech_therapist"
    DENTIST = "dentist"
    OPTOMETRIST = "optometrist"
    CHIROPRACTOR = "chiropractor"
    ACUPUNCTURIST = "acupuncturist"
    INSURED = "insured"
    INSURER = "insurer"
    HEALTH_PLAN = "health_plan"
    HOSPITAL = "hospital"
    CLINIC = "clinic"
    PHARMACY = "pharmacy"
    LABORATORY = "laboratory"
    EMERGENCY_CONTACT = "emergency_contact"
    MEDICAL_PROXY = "medical_proxy"
    DONOR = "donor"
    RECIPIENT = "recipient"

    # ═══════════════════════════════════════════════════════════════════════════
    # Digital/AI Relationships
    # ═══════════════════════════════════════════════════════════════════════════
    AI_AGENT = "ai_agent"
    ROBOT = "robot"
    DEVICE = "device"
    SERVICE_ACCOUNT = "service_account"
    API_ENDPOINT = "api_endpoint"
    WEBHOOK = "webhook"
    BOT = "bot"
    CHATBOT = "chatbot"
    VIRTUAL_ASSISTANT = "virtual_assistant"
    DIGITAL_TWIN = "digital_twin"
    EMBEDDED_SYSTEM = "embedded_system"
    IoT_DEVICE = "iot_device"
    SENSOR = "sensor"
    ACTUATOR = "actuator"
    GATEWAY = "gateway"
    EDGE_NODE = "edge_node"
    CLOUD_INSTANCE = "cloud_instance"
    CONTAINER = "container"
    MICROSERVICE = "microservice"
    DATABASE = "database"
    CACHE = "cache"
    MESSAGE_QUEUE = "message_queue"

    # ═══════════════════════════════════════════════════════════════════════════
    # Knowledge Relationships
    # ═══════════════════════════════════════════════════════════════════════════
    RELATED_TO = "related_to"
    DEPENDS_ON = "depends_on"
    DEPENDENCY_OF = "dependency_of"
    CAUSED_BY = "caused_by"
    CAUSES = "causes"
    IMPLIES = "implies"
    CONTRADICTS = "contradicts"
    SUPPORTS = "supports"
    OPPOSES = "opposes"
    REFERENCES = "references"
    REFERENCED_BY = "referenced_by"
    DERIVED_FROM = "derived_from"
    DERIVES = "derives"
    GENERALIZES = "generalizes"
    SPECIALIZES = "specializes"
    CONTAINS = "contains"
    CONTAINED_IN = "contained_in"
    PART_OF = "part_of"
    HAS_PART = "has_part"
    MEMBER_OF = "member_of"
    HAS_MEMBER = "has_member"
    INSTANCE_OF = "instance_of"
    HAS_INSTANCE = "has_instance"
    EXAMPLE_OF = "example_of"
    HAS_EXAMPLE = "has_example"
    DEFINES = "defines"
    DEFINED_BY = "defined_by"
    EXPLAINS = "explains"
    EXPLAINED_BY = "explained_by"
    PRECEDES = "precedes"
    FOLLOWS = "follows"
    OVERLAPS = "overlaps"
    DISJOINT = "disjoint"
    EQUIVALENT_TO = "equivalent_to"
    SIMILAR_TO = "similar_to"
    OPPOSITE_OF = "opposite_of"
    SYNONYM_OF = "synonym_of"
    ANTONYM_OF = "antonym_of"
    HYPERNYM_OF = "hypernym_of"
    HYPONYM_OF = "hyponym_of"
    MERONYM_OF = "meronym_of"
    HOLONYM_OF = "holonym_of"

    # ═══════════════════════════════════════════════════════════════════════════
    # Spatial Relationships
    # ═══════════════════════════════════════════════════════════════════════════
    LOCATED_IN = "located_in"
    LOCATED_AT = "located_at"
    LIVES_IN = "lives_in"
    """Actor LIVES_IN Space — physical-geography refactor (kernel/geography/):
    an actor's home Space, semantic/associative, NOT physical containment
    (that's kernel/geography/registry.py::GeographicRegistry.host_society/
    add_child instead)."""
    WORKS_IN = "works_in"
    """Actor WORKS_IN Space — same refactor, an actor's workplace Space."""
    HOSTED_BY = "hosted_by"
    """Society HOSTED_BY GeographicEntity — same refactor. The queryable
    mirror of GeographicRegistry.host_society()/entity_for_society(); the
    registry stays the source of truth (single-host enforced there), this
    is the semantic edge for relationship-graph traversal/queries."""
    NEAR = "near"
    FAR_FROM = "far_from"
    ADJACENT_TO = "adjacent_to"
    ABOVE = "above"
    BELOW = "below"
    LEFT_OF = "left_of"
    RIGHT_OF = "right_of"
    IN_FRONT_OF = "in_front_of"
    BEHIND = "behind"
    INSIDE = "inside"
    OUTSIDE = "outside"
    SURROUNDS = "surrounds"
    CONNECTS_TO = "connects_to"
    LINKED_TO = "linked_to"
    ATTACHED_TO = "attached_to"
    MOUNTED_ON = "mounted_on"
    EMBEDDED_IN = "embedded_in"
    FLOATS_ON = "floats_on"
    ORBITS = "orbits"
    ORBITED_BY = "orbited_by"
    ORIGINATED_FROM = "originated_from"

    # ═══════════════════════════════════════════════════════════════════════════
    # Temporal Relationships
    # ═══════════════════════════════════════════════════════════════════════════
    BEFORE = "before"
    AFTER = "after"
    DURING = "during"
    SIMULTANEOUS_WITH = "simultaneous_with"
    OVERLAPS_WITH = "overlaps_with"
    STARTED_BY = "started_by"
    ENDED_BY = "ended_by"
    TRIGGERED_BY = "triggered_by"
    PRECEDED_BY = "preceded_by"
    SUCCEEDED_BY = "succeeded_by"
    CONCURRENT_WITH = "concurrent_with"
    PERIODIC = "periodic"
    RECURRING = "recurring"
    ONE_TIME = "one_time"
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"
    POSTPONED = "postponed"
    RESCHEDULED = "rescheduled"
    COMPLETED = "completed"
    IN_PROGRESS = "in_progress"
    PENDING = "pending"
    DEFERRED = "deferred"

    # ═══════════════════════════════════════════════════════════════════════════
    # Causal Relationships
    # ═══════════════════════════════════════════════════════════════════════════
    CAUSES_EFFECT = "causes_effect"
    CAUSED_BY_EFFECT = "caused_by_effect"
    PREVENTS = "prevents"
    PREVENTED_BY = "prevented_by"
    ENABLES = "enables"
    ENABLED_BY = "enabled_by"
    INHIBITS_EFFECT = "inhibits_effect"
    INHIBITED_BY_EFFECT = "inhibited_by_effect"
    PROMOTES = "promotes"
    PROMOTED_BY = "promoted_by"
    AMPLIFIES = "amplifies"
    DAMPENS = "dampens"
    TRIGGERS = "triggers"
    TRIGGERED_BY_EFFECT = "triggered_by_effect"
    INITIATES = "initiates"
    INITIATED_BY = "initiated_by"
    TERMINATES_EFFECT = "terminates_effect"
    TERMINATED_BY_EFFECT = "terminated_by_effect"
    MODIFIES = "modifies"
    MODIFIED_BY = "modified_by"

    # ═══════════════════════════════════════════════════════════════════════════
    # Financial Relationships
    # ═══════════════════════════════════════════════════════════════════════════
    OWNS = "owns"
    OWNED_BY = "owned_by"
    LEASES = "leases"
    LEASED_BY = "leased_by"
    RENTS = "rents"
    RENTED_BY = "rented_by"
    INVESTS_IN = "invests_in"
    INVESTED_BY = "invested_by"
    LENDS_TO = "lends_to"
    BORROWS_FROM = "borrows_from"
    PAYS = "pays"
    PAID_BY = "paid_by"
    OWE_TO = "owe_to"
    OWED_BY = "owed_by"
    DONATES_TO = "donates_to"
    DONATED_BY = "donated_by"
    SPONSORS = "sponsors"
    SPONSORED_BY = "sponsored_by"
    SUBSIDIZES = "subsidizes"
    SUBSIDIZED_BY = "subsidized_by"
    FUNDING = "funding"
    FUNDED_BY = "funded_by"
    GRANT = "grant"
    GRANTED_BY = "granted_by"
    SCHOLARSHIP = "scholarship"
    SCHOLARSHIPPED_BY = "scholarshipped_by"
    DIVIDEND = "dividend"
    EQUITY = "equity"
    DEBT = "debt"
    LOAN = "loan"
    MORTGAGE = "mortgage"
    INSURANCE = "insurance"
    COVERAGE = "coverage"
    PREMIUM = "premium"
    DEDUCTIBLE = "deductible"
    CLAIM = "claim"
    BENEFICIARY = "beneficiary"

    # ═══════════════════════════════════════════════════════════════════════════
    # Legal Relationships
    # ═══════════════════════════════════════════════════════════════════════════
    GOVERNS_LEGAL = "governs_legal"
    GOVERNED_BY_LEGAL = "governed_by_legal"
    ENFORCES = "enforces"
    ENFORCED_BY = "enforced_by"
    REGULATES = "regulates"
    REGULATED_BY = "regulated_by"
    PROTECTS = "protects"
    PROTECTED_BY = "protected_by"
    AUTHORIZES_LEGAL = "authorizes_legal"
    AUTHORIZED_BY_LEGAL = "authorized_by_legal"
    PROHIBITS = "prohibits"
    PROHIBITED_BY = "prohibited_by"
    PERMITS = "permits"
    PERMITTED_BY_LEGAL = "permitted_by_legal"
    REQUIRES = "requires"
    REQUIRED_BY = "required_by"
    RECOMMENDS = "recommends"
    RECOMMENDED_BY = "recommended_by"
    CERTIFIES = "certifies"
    CERTIFIED_BY = "certified_by"
    ACCREDITS = "accredits"
    ACCREDITED_BY = "accredited_by"
    LICENSES = "licenses"
    LICENSED_BY = "licensed_by"
    PATENTS = "patents"
    PATENTED_BY = "patented_by"
    COPYRIGHTS = "copyrights"
    COPYRIGHTED_BY = "copyrighted_by"
    TRADEMARKS = "trademarks"
    TRADEMARKED_BY = "trademarked_by"
    ARBITRATES = "arbitrates"
    ARBITRATED_BY = "arbitrated_by"
    MEDIATES = "mediates"
    MEDIATED_BY = "mediated_by"
    LITIGATES = "litigates"
    LITIGATED_BY = "litigated_by"
    PROSECUTES = "prosecutes"
    PROSECUTED_BY = "prosecuted_by"
    DEFENDS = "defends"
    DEFENDED_BY = "defended_by"
    TESTIFIES = "testifies"
    TESTIFIED_BY = "testified_by"
    WARRANTS = "warrants"
    WARRANTED_BY = "warranted_by"
    SUBPOENAS = "subpoenas"
    SUBPOENAED_BY = "subpoenaed_by"
    OATHS = "oaths"
    OATHED_BY = "oathed_by"

    # ═══════════════════════════════════════════════════════════════════════════
    # Social Relationships
    # ═══════════════════════════════════════════════════════════════════════════
    FOLLOWER = "follower"
    FOLLOWING = "following"
    SUBSCRIBER = "subscriber"
    SUBSCRIBED_TO = "subscribed_to"
    MEMBER = "member"
    MEMBERSHIP = "membership"
    CONTRIBUTOR = "contributor"
    CONTRIBUTED_TO = "contributed_to"
    COLLABORATED_WITH = "collaborated_with"
    SUPPORTER = "supporter"
    SUPPORTED_BY = "supported_by"
    OPPONENT = "opponent"
    OPPONENT_OF = "opponent_of"
    ALLY = "ally"
    ALLIED_WITH = "allied_with"
    COMPETITOR = "competitor"
    COMPETED_WITH = "competed_with"
    COMPLEMENT = "complement"
    COMPLEMENTED_BY = "complemented_by"
    SUBSTITUTED_BY = "substituted_by"
    REPLACES = "replaces"
    REPLACED_BY = "replaced_by"
    INFLUENCES = "influences"
    INFLUENCED_BY = "influenced_by"
    PERSUADES = "persuades"
    PERSUADED_BY = "persuaded_by"
    COERCES = "coerces"
    COERCED_BY = "coerced_by"
    MANIPULATES = "manipulates"
    MANIPULATED_BY = "manipulated_by"
    DECEIVES = "deceives"
    DECEIVED_BY = "deceived_by"
    TRUSTS = "trusts"
    TRUSTED_BY = "trusted_by"
    DISTRUSTS = "distrusts"
    DISTRUSTED_BY = "distrusted_by"
    FEARS = "fears"
    FEARED_BY = "feared_by"
    ADMIRES = "admires"
    ADMIRED_BY = "admired_by"
    RESPECTS = "respects"
    RESPECTED_BY = "respected_by"
    LOVES = "loves"
    LOVED_BY = "loved_by"
    HATES = "hates"
    HATED_BY = "hated_by"
    ENVIES = "envies"
    ENVIED_BY = "envied_by"
    RESENTS = "resents"
    RESENTED_BY = "resented_by"
    PITIES = "pities"
    PITIED_BY = "pitied_by"
    CONGRATULATES = "congratulates"
    CONGRATULATED_BY = "congratulated_by"
    MOURNS = "mourns"
    MOURNED_BY = "mourned_by"
    CELEBRATES = "celebrates"
    CELEBRATED_BY = "celebrated_by"

    # ═══════════════════════════════════════════════════════════════════════════
    # Technical Relationships
    # ═══════════════════════════════════════════════════════════════════════════
    IMPLEMENTS = "implements"
    IMPLEMENTED_BY = "implemented_by"
    EXTENDS = "extends"
    EXTENDED_BY = "extended_by"
    INHERITS_FROM = "inherits_from"
    INHERITED_BY = "inherited_by"
    OVERRIDES = "overrides"
    OVERRIDDEN_BY = "overridden_by"
    CALLS = "calls"
    CALLED_BY = "called_by"
    INVOKES = "invokes"
    INVOKED_BY = "invoked_by"
    WRAPS = "wraps"
    WRAPPED_BY = "wrapped_by"
    PROXIES = "proxies"
    PROXIED_BY = "proxied_by"
    CACHES = "caches"
    CACHED_BY = "cached_by"
    INDEXES = "indexes"
    INDEXED_BY = "indexed_by"
    SERIALIZES = "serializes"
    SERIALIZED_BY = "serialized_by"
    DESERIALIZES = "deserializes"
    DESERIALIZED_BY = "deserialized_by"
    VALIDATES = "validates"
    VALIDATED_BY = "validated_by"
    TRANSFORMS_TECH = "transforms_tech"
    TRANSFORMED_BY_TECH = "transformed_by_tech"
    ENCODES = "encodes"
    ENCODED_BY = "encoded_by"
    DECODES = "decodes"
    DECODED_BY = "decoded_by"
    COMPRESSES = "compresses"
    COMPRESSED_BY = "compressed_by"
    DECOMPRESSES = "decompresses"
    DECOMPRESSED_BY = "decompressed_by"
    ENCRYPTS = "encrypts"
    ENCRYPTED_BY = "encrypted_by"
    DECRYPTS = "decrypts"
    DECRYPTED_BY = "decrypted_by"
    HASHES = "hashes"
    HASHED_BY = "hashed_by"
    SIGNATURE = "signature"
    SIGNED_BY = "signed_by"
    VERIFIES = "verifies"
    VERIFIED_BY = "verified_by"
    AUTHENTICATES = "authenticates"
    AUTHENTICATED_BY = "authenticated_by"
    AUTHORIZES = "authorizes"
    AUTHORIZED_BY = "authorized_by"
    RATE_LIMITS = "rate_limits"
    RATE_LIMITED_BY = "rate_limited_by"
    LOAD_BALANCES = "load_balances"
    LOAD_BALANCED_BY = "load_balanced_by"
    FAILOVER_TO = "failover_to"
    FAILOVER_FROM = "failover_from"
    MIGRATES_TO = "migrates_to"
    MIGRATED_FROM = "migrated_from"
    REPLICATES_TO = "replicates_to"
    REPLICATED_FROM = "replicated_from"
    SYNCS_TO = "syncs_to"
    SYNCS_FROM = "syncs_from"
    BACKS_UP = "backs_up"
    BACKED_UP_BY = "backed_up_by"
    RESTORES_FROM = "restores_from"
    RESTORED_BY = "restored_by"

    # ═══════════════════════════════════════════════════════════════════════════
    # Creative Relationships
    # ═══════════════════════════════════════════════════════════════════════════
    CREATOR = "creator"
    CREATED_BY = "created_by"
    ARTIST = "artist"
    ARTIST_OF = "artist_of"
    AUTHOR = "author"
    AUTHOR_OF = "author_of"
    COMPOSER = "composer"
    COMPOSED_BY = "composed_by"
    DIRECTOR = "director"
    DIRECTED_BY = "directed_by"
    PRODUCER = "producer"
    PRODUCED_BY = "produced_by"
    PERFORMER = "performer"
    PERFORMED_BY = "performed_by"
    CURATOR = "curator"
    CURATED_BY = "curated_by"
    COLLECTOR = "collector"
    COLLECTED_BY = "collected_by"
    PATRON = "patron"
    PATRON_OF = "patron_of"
    MUSE = "muse"
    MUSE_OF = "muse_of"
    INSPIRATION = "inspiration"
    INSPIRED_BY = "inspired_by"
    ADAPTATION = "adaptation"
    ADAPTED_FROM = "adapted_from"
    ADAPTED_TO = "adapted_to"
    REMAKE = "remake"
    REMAKE_OF = "remake_of"
    SEQUEL = "sequel"
    SEQUEL_OF = "sequel_of"
    PREQUEL = "prequel"
    PREQUEL_OF = "prequel_of"
    SPINOFF = "spinoff"
    SPINOFF_OF = "spinoff_of"
    TRIBUTE = "tribute"
    TRIBUTE_TO = "tribute_to"
    PARODY = "parody"
    PARODY_OF = "parody_of"
    SATIRE = "satire"
    SATIRE_OF = "satire_of"
    HOMAGE = "homage"
    HOMAGE_TO = "homage_to"
    EASTER_EGG = "easter_egg"
    REFERENCED_IN = "referenced_in"
    SAMPLED_IN = "sampled_in"
    SAMPLED_FROM = "sampled_from"
    COVERED_IN = "covered_in"
    COVERED_FROM = "covered_from"
    REMIXED_IN = "remixed_in"
    REMIXED_FROM = "remixed_from"
    TRANSLATED_TO = "translated_to"
    TRANSLATED_FROM = "translated_from"
    LOCALIZED_TO = "localized_to"
    LOCALIZED_FROM = "localized_from"

    # ═══════════════════════════════════════════════════════════════════════════
    # Scientific Relationships
    # ═══════════════════════════════════════════════════════════════════════════
    HYPOTHESIS = "hypothesis"
    HYPOTHESIZED_BY = "hypothesized_by"
    THEORY = "theory"
    THEORIZED_BY = "theorized_by"
    LAW = "law"
    LAWS_OF = "laws_of"
    PRINCIPLE = "principle"
    PRINCIPLE_OF = "principle_of"
    AXIOM = "axiom"
    AXIOM_OF = "axiom_of"
    THEOREM = "theorem"
    THEOREM_OF = "theorem_of"
    LEMMA = "lemma"
    LEMMA_OF = "lemma_of"
    COROLLARY = "corollary"
    COROLLARY_OF = "corollary_of"
    POSTULATE = "postulate"
    POSTULATED_BY = "postulated_by"
    CONJECTURE = "conjecture"
    CONJECTURED_BY = "conjectured_by"
    PARADIGM = "paradigm"
    PARADIGM_OF = "paradigm_of"
    METHODOLOGY = "methodology"
    METHODOLOGY_OF = "methodology_of"
    PROTOCOL = "protocol"
    PROTOCOL_FOR = "protocol_for"
    STANDARD = "standard"
    STANDARD_FOR = "standard_for"
    SPECIFICATION = "specification"
    SPECIFICATION_FOR = "specification_for"
    SCHEMA = "schema"
    SCHEMA_FOR = "schema_for"
    MODEL = "model"
    MODEL_OF = "model_of"
    SIMULATION = "simulation"
    SIMULATION_OF = "simulation_of"
    EMPIRICAL = "empirical"
    EMPIRICALLY_VALIDATED_BY = "empirically_validated_by"
    PEER_REVIEWED = "peer_reviewed"
    PEER_REVIEWED_BY = "peer_reviewed_by"
    CITATION = "citation"
    CITED_BY = "cited_by"
    BIBLIOGRAPHY = "bibliography"
    BIBLIOGRAPHY_OF = "bibliography_of"

    # ═══════════════════════════════════════════════════════════════════════════
    # Generic/Utility
    # ═══════════════════════════════════════════════════════════════════════════
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"
    BLOCKED = "blocked"
    ALLOWED = "allowed"
    WHITELISTED = "whitelisted"
    BLACKLISTED = "blacklisted"
    FLAGGED = "flagged"
    ARCHIVED = "archived"
    SOFT_DELETED = "soft_deleted"
    HIDDEN = "hidden"
    VISIBLE = "visible"
    PUBLIC = "public"
    PRIVATE = "private"
    INTERNAL = "internal"
    EXTERNAL = "external"
    SHARED = "shared"
    UNSHARED = "unshared"
    LOCKED = "locked"
    UNLOCKED = "unlocked"
    FROZEN = "frozen"
    THAWED = "thawed"
    SUSPENDED = "suspended"
    RESUMED = "resumed"
    PAUSED = "paused"
    CONTINUED = "continued"
    STARTED = "started"
    STOPPED = "stopped"
    INITIALIZED = "initialized"
    TERMINATED = "terminated"
    CREATED = "created"
    UPDATED = "updated"
    MODIFIED = "modified"
    DELETED = "deleted"
    RESTORED = "restored"
    BACKED_UP = "backed_up"
    MIGRATED = "migrated"
    REPLICATED = "replicated"
    SYNCHRONIZED = "synchronized"
    ASYNCHRONIZED = "asynchronized"
    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"
    VALID = "valid"
    INVALID = "invalid"
    CORRUPT = "corrupt"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DEGRADED = "degraded"
    OVERLOADED = "overloaded"
    UNDERLOADED = "underloaded"
    IDLE = "idle"
    BUSY = "busy"
    ACTIVE = "active"
    INACTIVE = "inactive"
    ENABLED = "enabled"
    DISABLED = "disabled"
    ON = "on"
    OFF = "off"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ONLINE = "online"
    OFFLINE = "offline"
    SYNCHRONOUS = "synchronous"
    ASYNCHRONOUS = "asynchronous"
    REAL_TIME = "real_time"
    NEAR_REAL_TIME = "near_real_time"
    BATCH = "batch"
    STREAMING = "streaming"
    EVENT_DRIVEN = "event_driven"
    POLLING = "polling"
    PUSH = "push"
    PULL = "pull"
    PUB_SUB = "pub_sub"
    REQUEST_RESPONSE = "request_response"
    FIRE_AND_FORGET = "fire_and_forget"
    ONE_WAY = "one_way"
    TWO_WAY = "two_way"
    MULTI_CAST = "multi_cast"
    BROAD_CAST = "broad_cast"
    UNI_CAST = "uni_cast"


class RelationshipDirection(str, Enum):
    """Direction of a relationship."""

    OUTGOING = "outgoing"
    INCOMING = "incoming"
    BIDIRECTIONAL = "bidirectional"


@dataclass
class Relationship:
    """A directed relationship between two entities."""

    relationship_id: str = field(default_factory=lambda: uuid4().hex)
    source_id: str = ""
    target_id: str = ""
    kind: RelationshipKind = RelationshipKind.RELATED_TO
    strength: float = 0.5  # 0.0 to 1.0
    confidence: float = 1.0
    bidirectional: bool = False
    attributes: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    source_actor_id: str = ""  # Who created this relationship
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float | None = None

    def __hash__(self) -> int:
        return hash(self.relationship_id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Relationship):
            return NotImplemented
        return self.relationship_id == other.relationship_id

    @property
    def is_active(self) -> bool:
        """Check if relationship is still valid."""
        if self.expires_at is None:
            return True
        return time.time() < self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "relationship_id": self.relationship_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "kind": self.kind.value,
            "strength": self.strength,
            "confidence": self.confidence,
            "bidirectional": self.bidirectional,
            "attributes": dict(self.attributes),
            "metadata": dict(self.metadata),
            "source_actor_id": self.source_actor_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Relationship:
        return cls(
            relationship_id=d.get("relationship_id", uuid4().hex),
            source_id=d.get("source_id", ""),
            target_id=d.get("target_id", ""),
            kind=RelationshipKind(d.get("kind", "related_to")),
            strength=d.get("strength", 0.5),
            confidence=d.get("confidence", 1.0),
            bidirectional=d.get("bidirectional", False),
            attributes=d.get("attributes", {}),
            metadata=d.get("metadata", {}),
            source_actor_id=d.get("source_actor_id", ""),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
            expires_at=d.get("expires_at"),
        )


@dataclass
class RelationshipPath:
    """A path through the relationship graph."""

    source_id: str
    target_id: str
    relationships: list[Relationship]
    total_strength: float = 0.0
    hop_count: int = 0

    @property
    def exists(self) -> bool:
        return len(self.relationships) > 0


@dataclass
class RelationshipHistoryEntry:
    """An audit trail entry for a relationship change."""

    entry_id: str = field(default_factory=lambda: uuid4().hex)
    relationship_id: str = ""
    action: str = ""  # "create", "update", "delete"
    old_value: dict[str, Any] | None = None
    new_value: dict[str, Any] | None = None
    actor_id: str = ""
    timestamp: float = field(default_factory=time.time)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "relationship_id": self.relationship_id,
            "action": self.action,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "actor_id": self.actor_id,
            "timestamp": self.timestamp,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RelationshipHistoryEntry":
        return cls(
            entry_id=d.get("entry_id", uuid4().hex),
            relationship_id=d.get("relationship_id", ""),
            action=d.get("action", ""),
            old_value=d.get("old_value"),
            new_value=d.get("new_value"),
            actor_id=d.get("actor_id", ""),
            timestamp=d.get("timestamp", 0.0),
            reason=d.get("reason", ""),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# RelationshipGraph — Core Graph Engine
# ═══════════════════════════════════════════════════════════════════════════════


class RelationshipGraph:
    """Graph-based relationship storage with traversal and inference.

    Responsibilities:
        Storage: Add, update, remove relationships
        Query: Find relationships by source, target, kind, or attribute
        Traversal: BFS/DFS path finding between entities
        Inference: Transitive relationships, reverse lookups
        History: Audit trail for all changes
    """

    def __init__(self, max_history: int = 100000):
        self._relationships: dict[str, Relationship] = {}
        self._adjacency: dict[str, list[str]] = {}  # source_id -> [rel_id]
        self._reverse_adjacency: dict[str, list[str]] = {}  # target_id -> [rel_id]
        self._history: list[RelationshipHistoryEntry] = []
        self._max_history = max_history
        """Temporal Presence & Actor Timeline Model refactor: this was
        previously unbounded (a long-running graph grew _history forever)
        and unpersisted (to_dict()/from_dict() never round-tripped it) —
        same _max_history-style cap as SocietyContextStream, now also
        round-tripped by to_dict()/from_dict() below so it survives a
        save/reload cycle."""

    # ── Storage ─────────────────────────────────────────────────

    def add(
        self,
        source_id: str,
        target_id: str,
        kind: RelationshipKind | str = RelationshipKind.RELATED_TO,
        strength: float = 0.5,
        confidence: float = 1.0,
        bidirectional: bool = False,
        attributes: dict | None = None,
        metadata: dict | None = None,
        source_actor_id: str = "",
        **kwargs,
    ) -> Relationship:
        """Add a relationship to the graph."""
        if isinstance(kind, str):
            kind = RelationshipKind(kind)

        rel = Relationship(
            source_id=source_id,
            target_id=target_id,
            kind=kind,
            strength=max(0.0, min(1.0, strength)),
            confidence=max(0.0, min(1.0, confidence)),
            bidirectional=bidirectional,
            attributes=attributes or {},
            metadata=metadata or {},
            source_actor_id=source_actor_id,
            **kwargs,
        )

        self._relationships[rel.relationship_id] = rel
        self._adjacency.setdefault(source_id, []).append(rel.relationship_id)
        self._reverse_adjacency.setdefault(target_id, []).append(rel.relationship_id)

        # Add inverse for bidirectional
        if bidirectional:
            self._adjacency.setdefault(target_id, []).append(rel.relationship_id)
            self._reverse_adjacency.setdefault(source_id, []).append(rel.relationship_id)

        # Record history
        self._record_history(
            rel.relationship_id,
            "create",
            None,
            rel.to_dict(),
            source_actor_id,
            kwargs.get("reason", ""),
        )

        logger.debug(
            "Added relationship: %s -[%s]-> %s (strength=%.2f)",
            source_id,
            kind.value,
            target_id,
            strength,
        )
        return rel

    def update(self, relationship_id: str, **kwargs) -> Relationship | None:
        """Update a relationship."""
        old = self._relationships.get(relationship_id)
        if old is None:
            return None

        old_dict = old.to_dict()

        # Update fields
        for key, value in kwargs.items():
            if hasattr(old, key):
                object.__setattr__(old, key, value)

        # Update timestamps
        object.__setattr__(old, "updated_at", time.time())

        # Handle kind change
        if "kind" in kwargs and isinstance(kwargs["kind"], str):
            object.__setattr__(old, "kind", RelationshipKind(kwargs["kind"]))

        # Handle strength clamping
        if "strength" in kwargs:
            object.__setattr__(old, "strength", max(0.0, min(1.0, kwargs["strength"])))

        # Record history
        self._record_history(
            relationship_id,
            "update",
            old_dict,
            old.to_dict(),
            kwargs.get("source_actor_id", ""),
            kwargs.get("reason", ""),
        )

        return old

    def remove(self, relationship_id: str, source_actor_id: str = "", reason: str = "") -> bool:
        """Remove a relationship."""
        rel = self._relationships.pop(relationship_id, None)
        if rel is None:
            return False

        # Remove from adjacency lists
        source_list = self._adjacency.get(rel.source_id, [])
        if relationship_id in source_list:
            source_list.remove(relationship_id)

        target_list = self._reverse_adjacency.get(rel.target_id, [])
        if relationship_id in target_list:
            target_list.remove(relationship_id)

        if rel.bidirectional:
            source_list2 = self._adjacency.get(rel.target_id, [])
            if relationship_id in source_list2:
                source_list2.remove(relationship_id)
            target_list2 = self._reverse_adjacency.get(rel.source_id, [])
            if relationship_id in target_list2:
                target_list2.remove(relationship_id)

        # Record history
        self._record_history(relationship_id, "delete", rel.to_dict(), None, source_actor_id, reason)

        return True

    def get(self, relationship_id: str) -> Relationship | None:
        """Get a relationship by ID."""
        return self._relationships.get(relationship_id)

    def count(self) -> int:
        """Total number of relationships."""
        return len(self._relationships)

    def all_relationships(self) -> list[Relationship]:
        """Get all relationships. DIP: Public API for external access."""
        return list(self._relationships.values())

    # ── Query ───────────────────────────────────────────────────

    def relationships_for(
        self,
        entity_id: str,
        direction: RelationshipDirection = RelationshipDirection.BIDIRECTIONAL,
        kind: RelationshipKind | None = None,
        min_strength: float = 0.0,
    ) -> list[Relationship]:
        """Get all relationships involving an entity."""
        results = set()

        if direction in (
            RelationshipDirection.OUTGOING,
            RelationshipDirection.BIDIRECTIONAL,
        ):
            for rel_id in self._adjacency.get(entity_id, []):
                rel = self._relationships.get(rel_id)
                if rel and rel.source_id == entity_id:
                    results.add(rel)

        if direction in (
            RelationshipDirection.INCOMING,
            RelationshipDirection.BIDIRECTIONAL,
        ):
            for rel_id in self._reverse_adjacency.get(entity_id, []):
                rel = self._relationships.get(rel_id)
                if rel and rel.target_id == entity_id:
                    results.add(rel)

        # Filter by kind
        if kind is not None:
            results = {r for r in results if r.kind == kind}

        # Filter by strength
        results = {r for r in results if r.strength >= min_strength}

        return sorted(results, key=lambda r: r.strength, reverse=True)

    def relationships_between(
        self, source_id: str, target_id: str, kind: RelationshipKind | None = None
    ) -> list[Relationship]:
        """Get all relationships between two entities."""
        results = []
        for rel in self._relationships.values():
            if rel.source_id == source_id and rel.target_id == target_id:
                if kind is None or rel.kind == kind:
                    results.append(rel)
            elif rel.bidirectional and rel.source_id == target_id and rel.target_id == source_id:
                if kind is None or rel.kind == kind:
                    results.append(rel)
        return results

    def by_kind(self, kind: RelationshipKind) -> list[Relationship]:
        """Get all relationships of a specific kind."""
        return [r for r in self._relationships.values() if r.kind == kind]

    def by_attribute(self, key: str, value: Any) -> list[Relationship]:
        """Get all relationships with a specific attribute value."""
        return [r for r in self._relationships.values() if r.attributes.get(key) == value]

    def strongest_relationship(self, entity_id: str, kind: RelationshipKind | None = None) -> Relationship | None:
        """Get the strongest relationship for an entity."""
        rels = self.relationships_for(entity_id, kind=kind)
        return rels[0] if rels else None

    def entities_connected_to(self, entity_id: str, min_strength: float = 0.0) -> list[str]:
        """Get all entities connected to this entity."""
        connected = set()
        for rel in self.relationships_for(entity_id, min_strength=min_strength):
            if rel.source_id == entity_id:
                connected.add(rel.target_id)
            else:
                connected.add(rel.source_id)
        return sorted(connected)

    # ── Traversal ───────────────────────────────────────────────

    def find_path(
        self,
        source_id: str,
        target_id: str,
        max_hops: int = 10,
        kind: RelationshipKind | None = None,
    ) -> RelationshipPath:
        """Find shortest path between two entities using BFS."""
        if source_id == target_id:
            return RelationshipPath(source_id, target_id, [], 0.0, 0)

        from collections import deque

        queue = deque([(source_id, [])])
        visited = {source_id}

        while queue:
            current, path = queue.popleft()

            if len(path) >= max_hops:
                continue

            for rel in self.relationships_for(current, kind=kind):
                next_entity = rel.target_id if rel.source_id == current else rel.source_id

                if next_entity == target_id:
                    final_path = path + [rel]
                    total_strength = min(r.strength for r in final_path) if final_path else 0.0
                    return RelationshipPath(
                        source_id=source_id,
                        target_id=target_id,
                        relationships=final_path,
                        total_strength=total_strength,
                        hop_count=len(final_path),
                    )

                if next_entity not in visited:
                    visited.add(next_entity)
                    queue.append((next_entity, path + [rel]))

        return RelationshipPath(source_id, target_id, [], 0.0, 0)

    def all_paths(self, source_id: str, target_id: str, max_hops: int = 5) -> list[RelationshipPath]:
        """Find all paths between two entities (up to max_hops)."""
        paths = []
        self._dfs_paths(source_id, target_id, [], set(), paths, max_hops)
        return paths

    def _dfs_paths(
        self,
        current: str,
        target: str,
        path: list[Relationship],
        visited: set,
        results: list[RelationshipPath],
        max_hops: int,
    ):
        """DFS helper for finding all paths."""
        if len(path) >= max_hops:
            return

        if current == target and path:
            total_strength = min(r.strength for r in path)
            results.append(
                RelationshipPath(
                    source_id=path[0].source_id,
                    target_id=target,
                    relationships=list(path),
                    total_strength=total_strength,
                    hop_count=len(path),
                )
            )
            return

        for rel in self.relationships_for(current):
            next_entity = rel.target_id if rel.source_id == current else rel.source_id
            if next_entity not in visited:
                visited.add(next_entity)
                self._dfs_paths(next_entity, target, path + [rel], visited, results, max_hops)
                visited.discard(next_entity)

    # ── Inference ───────────────────────────────────────────────

    def transitive(self, source_id: str, kind: RelationshipKind, max_hops: int = 5) -> list[Relationship]:
        """Find transitive relationships of a given kind.

        Example: If A -colleague-> B and B -colleague-> C,
        then A has transitive colleague relationship with C.
        """
        results = []
        visited = set()

        def _traverse(current: str, depth: int):
            if depth >= max_hops:
                return
            visited.add(current)

            for rel in self.relationships_for(current, kind=kind):
                next_entity = rel.target_id if rel.source_id == current else rel.source_id
                if next_entity not in visited and next_entity != source_id:
                    results.append(rel)
                    _traverse(next_entity, depth + 1)

        _traverse(source_id, 0)
        return results

    def reverse_relationships(self, entity_id: str, kind: RelationshipKind | None = None) -> list[Relationship]:
        """Get all incoming relationships (where entity is the target)."""
        return self.relationships_for(entity_id, direction=RelationshipDirection.INCOMING, kind=kind)

    # ── History ─────────────────────────────────────────────────

    def _record_history(
        self,
        relationship_id: str,
        action: str,
        old_value: dict | None,
        new_value: dict | None,
        actor_id: str,
        reason: str,
    ) -> None:
        """Record a history entry."""
        entry = RelationshipHistoryEntry(
            relationship_id=relationship_id,
            action=action,
            old_value=old_value,
            new_value=new_value,
            actor_id=actor_id,
            reason=reason,
        )
        self._history.append(entry)
        if len(self._history) > self._max_history:
            del self._history[: len(self._history) - self._max_history]

    def history(
        self,
        relationship_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
    ) -> list[RelationshipHistoryEntry]:
        """Get relationship history."""
        results = self._history

        if relationship_id:
            results = [e for e in results if e.relationship_id == relationship_id]

        if action:
            results = [e for e in results if e.action == action]

        return sorted(results, key=lambda e: e.timestamp, reverse=True)[:limit]

    # ── Serialization ───────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "relationships": [r.to_dict() for r in self._relationships.values()],
            "history_count": len(self._history),
            "history": [e.to_dict() for e in self._history],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RelationshipGraph:
        """Deserialize from dict."""
        graph = cls()
        for rel_data in data.get("relationships", []):
            rel = Relationship.from_dict(rel_data)
            graph._relationships[rel.relationship_id] = rel
            graph._adjacency.setdefault(rel.source_id, []).append(rel.relationship_id)
            graph._reverse_adjacency.setdefault(rel.target_id, []).append(rel.relationship_id)
            if rel.bidirectional:
                graph._adjacency.setdefault(rel.target_id, []).append(rel.relationship_id)
                graph._reverse_adjacency.setdefault(rel.source_id, []).append(rel.relationship_id)
        graph._history = [RelationshipHistoryEntry.from_dict(e) for e in data.get("history", [])]
        return graph

    def stats(self) -> dict[str, Any]:
        """Get graph statistics."""
        kind_counts = {}
        for rel in self._relationships.values():
            kind_counts[rel.kind.value] = kind_counts.get(rel.kind.value, 0) + 1

        return {
            "total_relationships": self.count(),
            "unique_entities": len(
                set(
                    [r.source_id for r in self._relationships.values()]
                    + [r.target_id for r in self._relationships.values()]
                )
            ),
            "by_kind": kind_counts,
            "bidirectional_count": sum(1 for r in self._relationships.values() if r.bidirectional),
            "history_entries": len(self._history),
        }
