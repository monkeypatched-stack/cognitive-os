"""Actor Relationships — Relationship metadata for actors.

Provides structured relationship information including:
- Organization/Team memberships
- Roles (admin, member, guest)
- Social connections/contacts

Usage:
    from src.monkey_brain.kernel.relationships_meta import (
        RelationshipMeta, Organization, Team, Role, SocialConnection
    )

    rel = RelationshipMeta(
        organizations=[Organization(name="Acme Corp", role="admin")],
        teams=[Team(name="Engineering", role="member")],
        social_connections=[SocialConnection(platform="linkedin", handle="alice")],
    )
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("agentos.relationships_meta")


# ═══════════════════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════════════════


class RoleType(str, Enum):
    """Role types."""

    OWNER = "owner"
    ADMIN = "admin"
    MODERATOR = "moderator"
    MEMBER = "member"
    GUEST = "guest"
    VIEWER = "viewer"
    CONTRIBUTOR = "contributor"
    MAINTAINER = "maintainer"


class MembershipStatus(str, Enum):
    """Membership status."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    PENDING = "pending"
    SUSPENDED = "suspended"
    INVITED = "invited"


class SocialPlatform(str, Enum):
    """Social platforms."""

    TWITTER = "twitter"
    LINKEDIN = "linkedin"
    GITHUB = "github"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    DISCORD = "discord"
    SLACK = "slack"
    EMAIL = "email"
    PHONE = "phone"
    WEBSITE = "website"
    OTHER = "other"


# ═══════════════════════════════════════════════════════════════════════════════
# Data Classes
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class Role:
    """A role within an organization or team."""

    role_type: RoleType = RoleType.MEMBER
    name: str = ""
    description: str = ""
    permissions: list[str] = field(default_factory=list)
    granted_at: float = field(default_factory=time.time)
    granted_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "role_type": self.role_type.value,
            "name": self.name,
            "description": self.description,
            "permissions": list(self.permissions),
            "granted_at": self.granted_at,
            "granted_by": self.granted_by,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Role:
        return cls(
            role_type=RoleType(data.get("role_type", "member")),
            name=data.get("name", ""),
            description=data.get("description", ""),
            permissions=data.get("permissions", []),
            granted_at=data.get("granted_at", time.time()),
            granted_by=data.get("granted_by", ""),
        )


@dataclass
class Organization:
    """An organization membership."""

    org_id: str = ""
    name: str = ""
    domain: str = ""
    industry: str = ""
    size: str = ""  # startup, small, medium, large, enterprise
    role: Role = field(default_factory=Role)
    status: MembershipStatus = MembershipStatus.ACTIVE
    joined_at: float = field(default_factory=time.time)
    left_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.status == MembershipStatus.ACTIVE and self.left_at is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "name": self.name,
            "domain": self.domain,
            "industry": self.industry,
            "size": self.size,
            "role": self.role.to_dict(),
            "status": self.status.value,
            "joined_at": self.joined_at,
            "left_at": self.left_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Organization:
        return cls(
            org_id=data.get("org_id", ""),
            name=data.get("name", ""),
            domain=data.get("domain", ""),
            industry=data.get("industry", ""),
            size=data.get("size", ""),
            role=Role.from_dict(data.get("role", {})),
            status=MembershipStatus(data.get("status", "active")),
            joined_at=data.get("joined_at", time.time()),
            left_at=data.get("left_at"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class Team:
    """A team membership."""

    team_id: str = ""
    name: str = ""
    description: str = ""
    organization_id: str = ""
    role: Role = field(default_factory=Role)
    status: MembershipStatus = MembershipStatus.ACTIVE
    joined_at: float = field(default_factory=time.time)
    left_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.status == MembershipStatus.ACTIVE and self.left_at is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "name": self.name,
            "description": self.description,
            "organization_id": self.organization_id,
            "role": self.role.to_dict(),
            "status": self.status.value,
            "joined_at": self.joined_at,
            "left_at": self.left_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Team:
        return cls(
            team_id=data.get("team_id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            organization_id=data.get("organization_id", ""),
            role=Role.from_dict(data.get("role", {})),
            status=MembershipStatus(data.get("status", "active")),
            joined_at=data.get("joined_at", time.time()),
            left_at=data.get("left_at"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class SocialConnection:
    """A social connection/contact."""

    platform: SocialPlatform = SocialPlatform.OTHER
    handle: str = ""
    url: str = ""
    display_name: str = ""
    verified: bool = False
    primary: bool = False
    added_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform.value,
            "handle": self.handle,
            "url": self.url,
            "display_name": self.display_name,
            "verified": self.verified,
            "primary": self.primary,
            "added_at": self.added_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SocialConnection:
        return cls(
            platform=SocialPlatform(data.get("platform", "other")),
            handle=data.get("handle", ""),
            url=data.get("url", ""),
            display_name=data.get("display_name", ""),
            verified=data.get("verified", False),
            primary=data.get("primary", False),
            added_at=data.get("added_at", time.time()),
            metadata=data.get("metadata", {}),
        )


@dataclass
class RelationshipMeta:
    """Actor relationship metadata.

    Attributes:
        organizations: List of organization memberships
        teams: List of team memberships
        social_connections: List of social connections
        reports_to: Entity ID of who this actor reports to
        direct_reports: List of entity IDs who report to this actor
        mentors: List of entity IDs who mentor this actor
        mentees: List of entity IDs this actor mentors
        contacts: List of general contact references
        metadata: Additional custom fields
    """

    organizations: list[Organization] = field(default_factory=list)
    teams: list[Team] = field(default_factory=list)
    social_connections: list[SocialConnection] = field(default_factory=list)
    reports_to: str = ""
    direct_reports: list[str] = field(default_factory=list)
    mentors: list[str] = field(default_factory=list)
    mentees: list[str] = field(default_factory=list)
    contacts: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_organization(self, org: Organization) -> None:
        """Add an organization membership."""
        self.organizations.append(org)

    def add_team(self, team: Team) -> None:
        """Add a team membership."""
        self.teams.append(team)

    def add_social_connection(self, connection: SocialConnection) -> None:
        """Add a social connection."""
        self.social_connections.append(connection)

    def remove_organization(self, org_id: str) -> bool:
        """Remove an organization membership."""
        before = len(self.organizations)
        self.organizations = [o for o in self.organizations if o.org_id != org_id]
        return len(self.organizations) < before

    def remove_team(self, team_id: str) -> bool:
        """Remove a team membership."""
        before = len(self.teams)
        self.teams = [t for t in self.teams if t.team_id != team_id]
        return len(self.teams) < before

    def remove_social_connection(self, platform: SocialPlatform, handle: str) -> bool:
        """Remove a social connection."""
        before = len(self.social_connections)
        self.social_connections = [
            s for s in self.social_connections if not (s.platform == platform and s.handle == handle)
        ]
        return len(self.social_connections) < before

    @property
    def active_organizations(self) -> list[Organization]:
        """Get active organization memberships."""
        return [o for o in self.organizations if o.is_active]

    @property
    def active_teams(self) -> list[Team]:
        """Get active team memberships."""
        return [t for t in self.teams if t.is_active]

    @property
    def primary_social(self) -> SocialConnection | None:
        """Get primary social connection."""
        for s in self.social_connections:
            if s.primary:
                return s
        return self.social_connections[0] if self.social_connections else None

    def get_role_in_org(self, org_id: str) -> Role | None:
        """Get role in a specific organization."""
        for org in self.organizations:
            if org.org_id == org_id:
                return org.role
        return None

    def get_role_in_team(self, team_id: str) -> Role | None:
        """Get role in a specific team."""
        for team in self.teams:
            if team.team_id == team_id:
                return team.role
        return None

    def has_role(self, role_type: RoleType) -> bool:
        """Check if actor has a specific role type in any org/team."""
        for org in self.active_organizations:
            if org.role.role_type == role_type:
                return True
        for team in self.active_teams:
            if team.role.role_type == role_type:
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "organizations": [o.to_dict() for o in self.organizations],
            "teams": [t.to_dict() for t in self.teams],
            "social_connections": [s.to_dict() for s in self.social_connections],
            "reports_to": self.reports_to,
            "direct_reports": list(self.direct_reports),
            "mentors": list(self.mentors),
            "mentees": list(self.mentees),
            "contacts": list(self.contacts),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RelationshipMeta:
        """Deserialize from dict."""
        return cls(
            organizations=[Organization.from_dict(o) for o in data.get("organizations", [])],
            teams=[Team.from_dict(t) for t in data.get("teams", [])],
            social_connections=[SocialConnection.from_dict(s) for s in data.get("social_connections", [])],
            reports_to=data.get("reports_to", ""),
            direct_reports=data.get("direct_reports", []),
            mentors=data.get("mentors", []),
            mentees=data.get("mentees", []),
            contacts=data.get("contacts", []),
            metadata=data.get("metadata", {}),
        )

    def __repr__(self) -> str:
        return f"RelationshipMeta(orgs={len(self.organizations)}, teams={len(self.teams)})"
