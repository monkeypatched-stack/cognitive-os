"""Actor-Centric Affiliation Architecture.

TrustEngine is the single trust abstraction used by both
SocietyRuntime (actor-to-actor) and AffiliationManager (affiliation trust).

Each affiliation type is a semantic concept with metadata:
    - category (personal, organizational, etc.)
    - cardinality (how many-to-many)
    - bidirectionality
    - default permissions
    - trust model (growth/decay rates)
    - lifecycle rules (creation, expiration, dissolution)
"""

from .trust import TrustEngine
from .types import (
    Cardinality,
    TrustModel,
    LifecycleRules,
    AffiliationType,
    ALL_TYPES,
    CATEGORIES,
    get_type,
    types_in_category,
)
from .affiliation import Affiliation
from .family import FamilyAffiliation
from .employment import EmploymentAffiliation
from .education import EducationAffiliation
from .manager import AffiliationManager

__all__ = [
    "TrustEngine",
    "Cardinality",
    "TrustModel",
    "LifecycleRules",
    "AffiliationType",
    "ALL_TYPES",
    "CATEGORIES",
    "get_type",
    "types_in_category",
    "Affiliation",
    "AffiliationManager",
    "FamilyAffiliation",
    "EmploymentAffiliation",
    "EducationAffiliation",
]
