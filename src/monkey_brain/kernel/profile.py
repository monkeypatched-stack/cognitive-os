"""Actor Profile — Basic metadata for actor identity.

Provides a structured profile for actors with basic metadata:
- Name, Username, Profile Photo
- Date of Birth, Preferred Language, Time Zone
- Bio, Location, Website
- Created/Updated timestamps

Usage:
    from src.monkey_brain.kernel.profile import Profile

    profile = Profile(
        name="Alice Smith",
        username="alice",
        email="alice@example.com",
        date_of_birth="1990-01-15",
        preferred_language="en",
        timezone="America/New_York",
    )
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agentos.profile")


@dataclass
class Profile:
    """Basic actor profile metadata.

    Attributes:
        name: Full name of the actor
        username: Unique username/handle
        email: Email address
        profile_photo: URL or path to profile photo
        date_of_birth: ISO format date (YYYY-MM-DD)
        preferred_language: ISO 639-1 language code (e.g., "en", "es", "fr")
        timezone: IANA timezone (e.g., "America/New_York", "Europe/London")
        bio: Short biography/description
        location: City/Region/Country
        website: Personal or professional website URL
        phone: Phone number
        organization: Company or organization name
        job_title: Current job title
        created_at: Account creation timestamp
        updated_at: Last profile update timestamp
        metadata: Additional custom fields
    """

    name: str = ""
    username: str = ""
    email: str = ""
    profile_photo: str = ""
    date_of_birth: str = ""  # ISO format: YYYY-MM-DD
    preferred_language: str = "en"  # ISO 639-1
    timezone: str = "UTC"
    bio: str = ""
    location: str = ""
    website: str = ""
    phone: str = ""
    organization: str = ""
    job_title: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def update(self, **kwargs: Any) -> None:
        """Update profile fields."""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "name": self.name,
            "username": self.username,
            "email": self.email,
            "profile_photo": self.profile_photo,
            "date_of_birth": self.date_of_birth,
            "preferred_language": self.preferred_language,
            "timezone": self.timezone,
            "bio": self.bio,
            "location": self.location,
            "website": self.website,
            "phone": self.phone,
            "organization": self.organization,
            "job_title": self.job_title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Profile:
        """Deserialize from dict."""
        return cls(
            name=data.get("name", ""),
            username=data.get("username", ""),
            email=data.get("email", ""),
            profile_photo=data.get("profile_photo", ""),
            date_of_birth=data.get("date_of_birth", ""),
            preferred_language=data.get("preferred_language", "en"),
            timezone=data.get("timezone", "UTC"),
            bio=data.get("bio", ""),
            location=data.get("location", ""),
            website=data.get("website", ""),
            phone=data.get("phone", ""),
            organization=data.get("organization", ""),
            job_title=data.get("job_title", ""),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            metadata=data.get("metadata", {}),
        )

    @property
    def display_name(self) -> str:
        """Get display name (name or username or entity_id)."""
        return self.name or self.username or "Anonymous"

    @property
    def age(self) -> int | None:
        """Calculate age from date of birth."""
        if not self.date_of_birth:
            return None
        try:
            from datetime import datetime, date

            birth = datetime.strptime(self.date_of_birth, "%Y-%m-%d").date()
            today = date.today()
            return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
        except (ValueError, TypeError):
            return None

    @property
    def is_complete(self) -> bool:
        """Check if profile has required fields."""
        return bool(self.name and self.username)

    def __repr__(self) -> str:
        return f"Profile(name={self.name!r}, username={self.username!r})"
