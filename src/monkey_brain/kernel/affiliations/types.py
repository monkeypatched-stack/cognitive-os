"""AffiliationType — semantic concept with metadata for each relationship type.

Instead of fixed enum values, each affiliation type is a rich object describing:
    - category (personal, organizational, etc.)
    - cardinality (how many-to-many)
    - bidirectionality (does the relationship exist in both directions?)
    - default permissions (what this relationship grants)
    - trust model (how trust propagates)
    - lifecycle (creation, maintenance, dissolution rules)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


class Cardinality(str, Enum):
    ONE_TO_ONE = "one_to_one"  # marriage, guardianship
    ONE_TO_MANY = "one_to_many"  # employer→employees
    MANY_TO_ONE = "many_to_one"  # employee→employer
    MANY_TO_MANY = "many_to_many"  # friends, community members


@dataclass(frozen=True)
class TrustModel:
    """How trust behaves for this relationship type."""

    initial_trust: float = 0.5
    growth_rate: float = 0.05
    decay_rate: float = -0.08
    decay_on_breach: float = -0.15
    asymmetric: bool = True  # decay faster than growth


@dataclass(frozen=True)
class LifecycleRules:
    """How this relationship is created, maintained, and dissolved."""

    requires_mutual_consent: bool = False
    auto_expire: bool = False
    duration_limit_days: int | None = None  # None = indefinite
    dissolution_requires_action: bool = True


@dataclass(frozen=True)
class AffiliationType:
    """Semantic concept for a relationship type.

    Each type carries rich metadata that the runtime uses for:
    - relationship semantics (cardinality, bidirectionality)
    - trust propagation (growth/decay rates)
    - lifecycle management (creation, expiration, dissolution)
    - permission defaults (what this relationship grants)
    - governance rules (who can create/modify/dissolve)
    """

    id: str
    category: str
    cardinality: Cardinality = Cardinality.MANY_TO_ONE
    bidirectional: bool = True
    default_permissions: tuple[str, ...] = ()
    trust_model: TrustModel = field(default_factory=TrustModel)
    lifecycle: LifecycleRules = field(default_factory=LifecycleRules)
    description: str = ""


# ════════════════════════════════════════════════════════════════
# Personal
# ════════════════════════════════════════════════════════════════

FAMILY = AffiliationType(
    id="family",
    category="personal",
    cardinality=Cardinality.ONE_TO_MANY,
    bidirectional=True,
    default_permissions=("emotional_support", "financial_dependents", "caregiving"),
    trust_model=TrustModel(initial_trust=0.9, growth_rate=0.02, decay_rate=-0.10, decay_on_breach=-0.20),
    lifecycle=LifecycleRules(requires_mutual_consent=False),
    description="Origin (parents, siblings) or Creation (children) family bonds",
)

FRIENDSHIP = AffiliationType(
    id="friendship",
    category="personal",
    cardinality=Cardinality.MANY_TO_MANY,
    bidirectional=True,
    default_permissions=("social", "emotional_support", "recommendation"),
    trust_model=TrustModel(initial_trust=0.6, growth_rate=0.05, decay_rate=-0.08),
    lifecycle=LifecycleRules(requires_mutual_consent=True),
    description="Social bond between individuals",
)

ROOMMATE = AffiliationType(
    id="roommate",
    category="personal",
    cardinality=Cardinality.MANY_TO_MANY,
    bidirectional=True,
    default_permissions=("shared_space", "shared_pantry", "social"),
    trust_model=TrustModel(initial_trust=0.55, growth_rate=0.04, decay_rate=-0.08),
    lifecycle=LifecycleRules(requires_mutual_consent=False),
    description="Shares a household with, without a family or romantic bond",
)

MARRIAGE = AffiliationType(
    id="marriage",
    category="personal",
    cardinality=Cardinality.ONE_TO_ONE,
    bidirectional=True,
    default_permissions=("financial", "legal", "medical", "emotional_support"),
    trust_model=TrustModel(initial_trust=1.0, growth_rate=0.02, decay_rate=-0.15, decay_on_breach=-0.30),
    lifecycle=LifecycleRules(requires_mutual_consent=True, dissolution_requires_action=True),
    description="Legal and emotional partnership",
)

GUARDIANSHIP = AffiliationType(
    id="guardianship",
    category="personal",
    cardinality=Cardinality.ONE_TO_ONE,
    bidirectional=False,
    default_permissions=("caregiving", "legal", "financial_dependents", "education"),
    trust_model=TrustModel(initial_trust=0.95, growth_rate=0.02, decay_rate=-0.10, decay_on_breach=-0.25),
    lifecycle=LifecycleRules(requires_mutual_consent=False, duration_limit_days=6570),
    description="Legal responsibility for a minor or incapacitated person",
)

# Specific, directional parent/child/sibling roles — FAMILY above stays as
# the generic fallback for when the specific role isn't known; these are
# for when it is, so a family edge can read "Arjun SON_OF Priya" instead
# of a symmetric, roleless "FAMILY_OF" in both directions. Each is a single
# directed record — the reverse role (e.g. Priya's MOTHER_OF Arjun) is a
# distinct, not automatically mirrored, statement.
SON_OF = AffiliationType(
    id="son_of",
    category="personal",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=False,
    default_permissions=("emotional_support",),
    trust_model=TrustModel(initial_trust=0.9, growth_rate=0.02, decay_rate=-0.10),
    lifecycle=LifecycleRules(requires_mutual_consent=False),
    description="Source is the son of target",
)

DAUGHTER_OF = AffiliationType(
    id="daughter_of",
    category="personal",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=False,
    default_permissions=("emotional_support",),
    trust_model=TrustModel(initial_trust=0.9, growth_rate=0.02, decay_rate=-0.10),
    lifecycle=LifecycleRules(requires_mutual_consent=False),
    description="Source is the daughter of target",
)

SIBLING_OF = AffiliationType(
    id="sibling_of",
    category="personal",
    cardinality=Cardinality.MANY_TO_MANY,
    bidirectional=True,
    default_permissions=("emotional_support",),
    trust_model=TrustModel(initial_trust=0.85, growth_rate=0.02, decay_rate=-0.10),
    lifecycle=LifecycleRules(requires_mutual_consent=False),
    description="Source and target share a parent — recorded once, per the "
    "world's unidirectional-edge convention, even though the "
    "relationship itself is inherently mutual",
)

# ════════════════════════════════════════════════════════════════
# Organizational
# ════════════════════════════════════════════════════════════════

EMPLOYMENT = AffiliationType(
    id="employment",
    category="organizational",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=True,
    default_permissions=(
        "career_planning",
        "calendar",
        "work_scheduling",
        "compensation",
    ),
    trust_model=TrustModel(initial_trust=0.7, growth_rate=0.03, decay_rate=-0.08, decay_on_breach=-0.15),
    lifecycle=LifecycleRules(duration_limit_days=None),
    description="Formal employment relationship",
)

CONTRACTOR = AffiliationType(
    id="contractor",
    category="organizational",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=True,
    default_permissions=("project_access", "compensation", "work_scheduling"),
    trust_model=TrustModel(initial_trust=0.5, growth_rate=0.05, decay_rate=-0.10),
    lifecycle=LifecycleRules(auto_expire=True, duration_limit_days=365),
    description="Contract-based work relationship",
)

VOLUNTEER = AffiliationType(
    id="volunteer",
    category="organizational",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=True,
    default_permissions=("project_access", "community"),
    trust_model=TrustModel(initial_trust=0.6, growth_rate=0.05, decay_rate=-0.05),
    lifecycle=LifecycleRules(requires_mutual_consent=True),
    description="Voluntary service to an organization",
)

BOARD_MEMBER = AffiliationType(
    id="board_member",
    category="organizational",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=True,
    default_permissions=("governance", "strategy", "financial_oversight"),
    trust_model=TrustModel(initial_trust=0.8, growth_rate=0.03, decay_rate=-0.10, decay_on_breach=-0.20),
    lifecycle=LifecycleRules(duration_limit_days=1825),
    description="Board of directors membership",
)

SHAREHOLDER = AffiliationType(
    id="shareholder",
    category="organizational",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=False,
    default_permissions=("financial_oversight", "voting"),
    trust_model=TrustModel(initial_trust=0.6, growth_rate=0.02, decay_rate=-0.05),
    description="Ownership stake in an organization",
)

# ════════════════════════════════════════════════════════════════
# Commercial
# ════════════════════════════════════════════════════════════════

CUSTOMER = AffiliationType(
    id="customer",
    category="commercial",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=True,
    default_permissions=("purchasing", "support"),
    trust_model=TrustModel(initial_trust=0.5, growth_rate=0.05, decay_rate=-0.10),
    description="Purchaser of goods or services",
)

SUPPLIER = AffiliationType(
    id="supplier",
    category="commercial",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=True,
    default_permissions=("procurement", "quality", "delivery"),
    trust_model=TrustModel(initial_trust=0.6, growth_rate=0.03, decay_rate=-0.12, decay_on_breach=-0.18),
    description="Provider of goods or services",
)

PARTNER = AffiliationType(
    id="partner",
    category="commercial",
    cardinality=Cardinality.MANY_TO_MANY,
    bidirectional=True,
    default_permissions=("strategic", "joint_venture", "cross_promotion"),
    trust_model=TrustModel(initial_trust=0.7, growth_rate=0.05, decay_rate=-0.10),
    description="Strategic business partnership",
)

VENDOR = AffiliationType(
    id="vendor",
    category="commercial",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=True,
    default_permissions=("procurement", "delivery"),
    trust_model=TrustModel(initial_trust=0.5, growth_rate=0.03, decay_rate=-0.10),
    description="Regular supplier of specific goods or services",
)

FRANCHISE = AffiliationType(
    id="franchise",
    category="commercial",
    cardinality=Cardinality.ONE_TO_MANY,
    bidirectional=True,
    default_permissions=("brand_use", "operational_guidelines", "training"),
    trust_model=TrustModel(initial_trust=0.7, growth_rate=0.03, decay_rate=-0.08),
    lifecycle=LifecycleRules(duration_limit_days=1825),
    description="Franchise relationship between franchisor and franchisee",
)

# ════════════════════════════════════════════════════════════════
# Government
# ════════════════════════════════════════════════════════════════

CITIZEN = AffiliationType(
    id="citizen",
    category="government",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=True,
    default_permissions=("legal", "protection", "voting"),
    trust_model=TrustModel(initial_trust=0.5, growth_rate=0.02, decay_rate=-0.05),
    description="Legal membership in a nation-state",
)

RESIDENT = AffiliationType(
    id="resident",
    category="government",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=True,
    default_permissions=("local_services", "legal"),
    trust_model=TrustModel(initial_trust=0.5, growth_rate=0.02, decay_rate=-0.03),
    description="Residence in a jurisdiction",
)

TAXPAYER = AffiliationType(
    id="taxpayer",
    category="government",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=True,
    default_permissions=("fiscal", "representation"),
    trust_model=TrustModel(initial_trust=0.4, growth_rate=0.02, decay_rate=-0.05),
    description="Tax obligation to a government entity",
)

VOTER = AffiliationType(
    id="voter",
    category="government",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=True,
    default_permissions=("electoral", "participation"),
    trust_model=TrustModel(initial_trust=0.5, growth_rate=0.03, decay_rate=-0.05),
    description="Voting rights in a jurisdiction",
)

PUBLIC_OFFICIAL = AffiliationType(
    id="public_official",
    category="government",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=True,
    default_permissions=("governance", "policy", "public_service"),
    trust_model=TrustModel(initial_trust=0.5, growth_rate=0.03, decay_rate=-0.10, decay_on_breach=-0.20),
    description="Elected or appointed government official",
)

# ════════════════════════════════════════════════════════════════
# Education
# ════════════════════════════════════════════════════════════════

STUDENT = AffiliationType(
    id="student",
    category="education",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=True,
    default_permissions=("learning", "facilities", "library"),
    trust_model=TrustModel(initial_trust=0.5, growth_rate=0.05, decay_rate=-0.05),
    lifecycle=LifecycleRules(duration_limit_days=1825),
    description="Enrolled learner at an institution",
)

TEACHER = AffiliationType(
    id="teacher",
    category="education",
    cardinality=Cardinality.ONE_TO_MANY,
    bidirectional=True,
    default_permissions=("teaching", "mentoring", "grading"),
    trust_model=TrustModel(initial_trust=0.7, growth_rate=0.03, decay_rate=-0.08),
    description="Educator at an institution",
)

ALUMNI = AffiliationType(
    id="alumni",
    category="education",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=True,
    default_permissions=("networking", "facilities"),
    trust_model=TrustModel(initial_trust=0.6, growth_rate=0.02, decay_rate=-0.03),
    description="Graduate of an institution",
)

RESEARCHER = AffiliationType(
    id="researcher",
    category="education",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=True,
    default_permissions=("research", "publications", "funding"),
    trust_model=TrustModel(initial_trust=0.7, growth_rate=0.03, decay_rate=-0.05),
    description="Research affiliate at an institution",
)

# ════════════════════════════════════════════════════════════════
# Healthcare
# ════════════════════════════════════════════════════════════════

PATIENT = AffiliationType(
    id="patient",
    category="healthcare",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=True,
    default_permissions=("medical", "emergency"),
    trust_model=TrustModel(initial_trust=0.7, growth_rate=0.02, decay_rate=-0.15, decay_on_breach=-0.25),
    description="Person receiving healthcare services",
)

DOCTOR = AffiliationType(
    id="doctor",
    category="healthcare",
    cardinality=Cardinality.ONE_TO_MANY,
    bidirectional=True,
    default_permissions=("medical", "prescriptions", "diagnosis"),
    trust_model=TrustModel(initial_trust=0.8, growth_rate=0.02, decay_rate=-0.10, decay_on_breach=-0.20),
    description="Healthcare provider",
)

CAREGIVER = AffiliationType(
    id="caregiver",
    category="healthcare",
    cardinality=Cardinality.ONE_TO_ONE,
    bidirectional=False,
    default_permissions=("caregiving", "medical_decisions", "emergency"),
    trust_model=TrustModel(initial_trust=0.9, growth_rate=0.03, decay_rate=-0.10, decay_on_breach=-0.20),
    description="Person providing care to another",
)

INSURED = AffiliationType(
    id="insured",
    category="healthcare",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=True,
    default_permissions=("coverage", "claims"),
    trust_model=TrustModel(initial_trust=0.5, growth_rate=0.02, decay_rate=-0.08),
    description="Person covered by health insurance",
)

# ════════════════════════════════════════════════════════════════
# Digital
# ════════════════════════════════════════════════════════════════

AI_AGENT = AffiliationType(
    id="ai_agent",
    category="digital",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=True,
    default_permissions=("task_execution", "data_access", "messaging"),
    trust_model=TrustModel(initial_trust=0.5, growth_rate=0.05, decay_rate=-0.10),
    description="Autonomous AI agent",
)

ROBOT = AffiliationType(
    id="robot",
    category="digital",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=True,
    default_permissions=("physical_tasks", "sensor_data"),
    trust_model=TrustModel(initial_trust=0.5, growth_rate=0.03, decay_rate=-0.08),
    description="Physical robotic agent",
)

DEVICE = AffiliationType(
    id="device",
    category="digital",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=False,
    default_permissions=("telemetry", "control"),
    trust_model=TrustModel(initial_trust=0.4, growth_rate=0.02, decay_rate=-0.05),
    description="IoT or connected device",
)

SERVICE_ACCOUNT = AffiliationType(
    id="service_account",
    category="digital",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=False,
    default_permissions=("api_access", "data_read", "data_write"),
    trust_model=TrustModel(initial_trust=0.3, growth_rate=0.05, decay_rate=-0.15),
    description="Programmatic service account",
)

# ════════════════════════════════════════════════════════════════
# Coordination — intra-society actor-to-actor topology
#
# Covers what the legacy Society-owned ActorRelationship/RelationshipType
# modeled (peer/superior/subordinate/collaborator/regulator/dependent/
# trusted/untrusted). customer/supplier already exist above and are reused
# as-is rather than duplicated here.
# ════════════════════════════════════════════════════════════════

PEER = AffiliationType(
    id="peer",
    category="coordination",
    cardinality=Cardinality.MANY_TO_MANY,
    bidirectional=True,
    default_permissions=("coordinate",),
    trust_model=TrustModel(initial_trust=0.5, growth_rate=0.05, decay_rate=-0.08),
    description="Equal-standing actor-to-actor coordination relationship",
)

SUPERIOR = AffiliationType(
    id="superior",
    category="coordination",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=False,
    default_permissions=("direct", "escalate"),
    trust_model=TrustModel(initial_trust=0.5, growth_rate=0.03, decay_rate=-0.10),
    description="Source holds authority over the target",
)

SUBORDINATE = AffiliationType(
    id="subordinate",
    category="coordination",
    cardinality=Cardinality.ONE_TO_MANY,
    bidirectional=False,
    default_permissions=("report",),
    trust_model=TrustModel(initial_trust=0.5, growth_rate=0.03, decay_rate=-0.10),
    description="Source is under the target's authority",
)

COLLABORATOR = AffiliationType(
    id="collaborator",
    category="coordination",
    cardinality=Cardinality.MANY_TO_MANY,
    bidirectional=True,
    default_permissions=("coordinate", "share_context"),
    trust_model=TrustModel(initial_trust=0.6, growth_rate=0.05, decay_rate=-0.08),
    description="Joint participants working toward a shared goal",
)

REGULATOR = AffiliationType(
    id="regulator",
    category="coordination",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=False,
    default_permissions=("audit", "enforce"),
    trust_model=TrustModel(initial_trust=0.5, growth_rate=0.02, decay_rate=-0.12, decay_on_breach=-0.20),
    description="Target exercises regulatory oversight over the source",
)

DEPENDENT = AffiliationType(
    id="dependent",
    category="coordination",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=False,
    default_permissions=(),
    trust_model=TrustModel(initial_trust=0.5, growth_rate=0.03, decay_rate=-0.10),
    description="Source relies on the target for a resource or capability",
)

TRUSTED = AffiliationType(
    id="trusted",
    category="coordination",
    cardinality=Cardinality.MANY_TO_MANY,
    bidirectional=False,
    default_permissions=("coordinate",),
    trust_model=TrustModel(initial_trust=0.8, growth_rate=0.03, decay_rate=-0.05),
    description="Explicit high-trust designation from source to target",
)

UNTRUSTED = AffiliationType(
    id="untrusted",
    category="coordination",
    cardinality=Cardinality.MANY_TO_MANY,
    bidirectional=False,
    default_permissions=(),
    trust_model=TrustModel(initial_trust=0.2, growth_rate=0.01, decay_rate=-0.15),
    description="Explicit low-trust designation from source to target",
)

# ════════════════════════════════════════════════════════════════
# Structural — Affiliation Graph relationship vocabulary
#
# The generic structural relationships used to answer "which societies
# does an actor belong to / which enterprises is it affiliated with /
# which actors may legally communicate" — the graph traversal that
# determines communication topology (see AffiliationManager). These are
# deliberately generic where the categories above already model a more
# specific real-world relationship (e.g. EMPLOYMENT above already covers
# the employer-side employment relationship in more detail than
# EMPLOYED_BY needs to).
# ════════════════════════════════════════════════════════════════

MEMBER_OF = AffiliationType(
    id="member_of",
    category="structural",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=False,
    default_permissions=("participate", "receive_broadcasts"),
    trust_model=TrustModel(initial_trust=0.5, growth_rate=0.03, decay_rate=-0.08),
    description="Source is a member of the target society/organization "
    "(mirrors kernel/society/membership.py::Membership as an "
    "affiliation edge — see relationship_bridge.py)",
)

AFFILIATED_WITH = AffiliationType(
    id="affiliated_with",
    category="structural",
    cardinality=Cardinality.MANY_TO_MANY,
    bidirectional=True,
    default_permissions=("associate",),
    trust_model=TrustModel(initial_trust=0.4, growth_rate=0.04, decay_rate=-0.08),
    description="Generic, loosely-coupled affiliation not covered by a more specific relationship type",
)

BELONGS_TO = AffiliationType(
    id="belongs_to",
    category="structural",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=False,
    default_permissions=("participate",),
    trust_model=TrustModel(initial_trust=0.5, growth_rate=0.03, decay_rate=-0.08),
    description="Source is structurally owned/contained by the target (e.g. a team belonging to a department)",
)

EMPLOYED_BY = AffiliationType(
    id="employed_by",
    category="structural",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=False,
    default_permissions=("work_scheduling", "compensation"),
    trust_model=TrustModel(initial_trust=0.7, growth_rate=0.03, decay_rate=-0.08),
    description="Employee-side direction of the employment relationship "
    "(see EMPLOYMENT above for the fuller employer-side model)",
)

PART_OF = AffiliationType(
    id="part_of",
    category="structural",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=False,
    default_permissions=(),
    trust_model=TrustModel(initial_trust=0.5, growth_rate=0.02, decay_rate=-0.08),
    description="Source is a structural subdivision of the target (e.g. a society that is part of an enterprise)",
)

REPRESENTS = AffiliationType(
    id="represents",
    category="structural",
    cardinality=Cardinality.MANY_TO_ONE,
    bidirectional=False,
    default_permissions=("act_on_behalf_of", "negotiate"),
    trust_model=TrustModel(initial_trust=0.7, growth_rate=0.03, decay_rate=-0.12, decay_on_breach=-0.25),
    description="Source acts as delegated representative/agent for the target in negotiations and coordination",
)

MANAGES = AffiliationType(
    id="manages",
    category="structural",
    cardinality=Cardinality.ONE_TO_MANY,
    bidirectional=False,
    default_permissions=("direct", "evaluate", "escalate"),
    trust_model=TrustModel(initial_trust=0.5, growth_rate=0.03, decay_rate=-0.10),
    description="Source holds direct managerial authority over the target "
    "(the structural counterpart to SUPERIOR above)",
)


# ════════════════════════════════════════════════════════════════
# Registry — all types indexed by ID
# ════════════════════════════════════════════════════════════════

ALL_TYPES: dict[str, AffiliationType] = {
    t.id: t
    for t in [
        FAMILY,
        FRIENDSHIP,
        ROOMMATE,
        MARRIAGE,
        GUARDIANSHIP,
        SON_OF,
        DAUGHTER_OF,
        SIBLING_OF,
        EMPLOYMENT,
        CONTRACTOR,
        VOLUNTEER,
        BOARD_MEMBER,
        SHAREHOLDER,
        CUSTOMER,
        SUPPLIER,
        PARTNER,
        VENDOR,
        FRANCHISE,
        CITIZEN,
        RESIDENT,
        TAXPAYER,
        VOTER,
        PUBLIC_OFFICIAL,
        STUDENT,
        TEACHER,
        ALUMNI,
        RESEARCHER,
        PATIENT,
        DOCTOR,
        CAREGIVER,
        INSURED,
        AI_AGENT,
        ROBOT,
        DEVICE,
        SERVICE_ACCOUNT,
        PEER,
        SUPERIOR,
        SUBORDINATE,
        COLLABORATOR,
        REGULATOR,
        DEPENDENT,
        TRUSTED,
        UNTRUSTED,
        MEMBER_OF,
        AFFILIATED_WITH,
        BELONGS_TO,
        EMPLOYED_BY,
        PART_OF,
        REPRESENTS,
        MANAGES,
    ]
}

CATEGORIES: dict[str, list[str]] = {}
for _t in ALL_TYPES.values():
    CATEGORIES.setdefault(_t.category, []).append(_t.id)


def get_type(type_id: str) -> AffiliationType | None:
    """Look up an affiliation type by ID."""
    return ALL_TYPES.get(type_id)


def types_in_category(category: str) -> list[str]:
    """Get all type IDs for a category."""
    return CATEGORIES.get(category, [])
