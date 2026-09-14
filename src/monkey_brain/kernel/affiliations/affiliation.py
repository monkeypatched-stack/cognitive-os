from __future__ import annotations
from dataclasses import dataclass, field
from .types import AffiliationType, ALL_TYPES


@dataclass(frozen=True)
class Affiliation:
    """Base affiliation — every relationship is a trust relationship.

    Each affiliation references an AffiliationType (by id) which carries
    the semantic metadata: category, cardinality, trust model, lifecycle.
    """

    affiliation_id: str
    affiliation_type: str
    target_id: str
    target_name: str
    trust_level: float = 0.5
    permissions: tuple[str, ...] = ()
    policies: tuple[str, ...] = ()
    priority: int = 0
    valid_from: str = ""
    valid_until: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def type_info(self) -> AffiliationType | None:
        """The semantic type definition for this affiliation."""
        return ALL_TYPES.get(self.affiliation_type)

    @property
    def category(self) -> str | None:
        """The category of this affiliation (personal, organizational, etc.)."""
        t = self.type_info
        return t.category if t else None

    @property
    def cardinality(self) -> str | None:
        """The cardinality of this affiliation."""
        t = self.type_info
        return t.cardinality.value if t else None

    @property
    def is_bidirectional(self) -> bool:
        """Whether this relationship exists in both directions."""
        t = self.type_info
        return t.bidirectional if t else True

    @property
    def default_permissions(self) -> tuple[str, ...]:
        """Default permissions granted by this affiliation type."""
        t = self.type_info
        return t.default_permissions if t else ()
