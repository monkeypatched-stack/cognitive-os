from __future__ import annotations
from dataclasses import dataclass
from .affiliation import Affiliation


@dataclass(frozen=True)
class EmploymentAffiliation(Affiliation):
    """Organizational — Employment relationship.

    Covers: employment, contractor, volunteer, board_member, shareholder
    """

    role: str = ""
    start_date: str = ""
    end_date: str = ""
    status: str = "active"  # "active" | "ended" | "sabbatical"
