from __future__ import annotations
from dataclasses import dataclass
from .affiliation import Affiliation


@dataclass(frozen=True)
class FamilyAffiliation(Affiliation):
    """Personal — Family relationship.

    Branches:
        Origin: mother, father, siblings (given by birth)
        Creation: spouse, children (created by choice)
    """

    branch: str = ""  # "origin" | "creation"
    relation: str = ""  # "mother" | "father" | "sibling" | "spouse" | "child"
