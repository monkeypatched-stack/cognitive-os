"""Actor Location — Location metadata for actors.

Provides structured location information including:
- Country, City, Time zone
- Approximate coordinates (if enabled)
- Address, Postal code
- Location history

Usage:
    from src.monkey_brain.kernel.location import Location

    location = Location(
        country="United States",
        city="New York",
        timezone="America/New_York",
        latitude=40.7128,
        longitude=-74.0060,
    )
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agentos.location")


@dataclass
class Coordinates:
    """Geographic coordinates."""

    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    accuracy: float = 0.0  # meters

    @property
    def is_valid(self) -> bool:
        """Check if coordinates are valid."""
        return -90 <= self.latitude <= 90 and -180 <= self.longitude <= 180

    def to_dict(self) -> dict[str, Any]:
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "altitude": self.altitude,
            "accuracy": self.accuracy,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Coordinates:
        return cls(
            latitude=data.get("latitude", 0.0),
            longitude=data.get("longitude", 0.0),
            altitude=data.get("altitude", 0.0),
            accuracy=data.get("accuracy", 0.0),
        )


@dataclass
class Location:
    """Actor location metadata.

    Attributes:
        country: Country name
        country_code: ISO 3166-1 alpha-2 country code
        state: State/Province/Region
        city: City name
        district: District/Borough
        address: Street address
        postal_code: Postal/ZIP code
        timezone: IANA timezone
        coordinates: Geographic coordinates (if enabled)
        location_enabled: Whether approximate location is enabled
        last_updated: Last location update timestamp
        metadata: Additional custom fields
    """

    country: str = ""
    country_code: str = ""
    state: str = ""
    city: str = ""
    district: str = ""
    address: str = ""
    postal_code: str = ""
    timezone: str = "UTC"
    coordinates: Coordinates = field(default_factory=Coordinates)
    location_enabled: bool = False
    last_updated: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def update(self, **kwargs: Any) -> None:
        """Update location fields."""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self.last_updated = time.time()

    @property
    def display_location(self) -> str:
        """Get display string for location."""
        parts = []
        if self.city:
            parts.append(self.city)
        if self.state:
            parts.append(self.state)
        if self.country:
            parts.append(self.country)
        return ", ".join(parts) if parts else "Unknown"

    @property
    def has_coordinates(self) -> bool:
        """Check if coordinates are available and valid."""
        return self.location_enabled and self.coordinates.is_valid

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "country": self.country,
            "country_code": self.country_code,
            "state": self.state,
            "city": self.city,
            "district": self.district,
            "address": self.address,
            "postal_code": self.postal_code,
            "timezone": self.timezone,
            "coordinates": self.coordinates.to_dict(),
            "location_enabled": self.location_enabled,
            "last_updated": self.last_updated,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Location:
        """Deserialize from dict."""
        return cls(
            country=data.get("country", ""),
            country_code=data.get("country_code", ""),
            state=data.get("state", ""),
            city=data.get("city", ""),
            district=data.get("district", ""),
            address=data.get("address", ""),
            postal_code=data.get("postal_code", ""),
            timezone=data.get("timezone", "UTC"),
            coordinates=Coordinates.from_dict(data.get("coordinates", {})),
            location_enabled=data.get("location_enabled", False),
            last_updated=data.get("last_updated", time.time()),
            metadata=data.get("metadata", {}),
        )

    def __repr__(self) -> str:
        return f"Location(city={self.city!r}, country={self.country!r})"
